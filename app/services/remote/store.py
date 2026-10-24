"""SQLite del almacén central de colectores remotos.

DB separada de ``audit.db`` y del histórico nativo. Cada request abre una
conexión corta; las escrituras se serializan con un lock de proceso, igual que
``audit_svc``. En fase 1 se asume un solo proceso uvicorn.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

from app.core import settings
from app.remote_collector.timeutil import parse_iso, utcnow

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
_WRITE_LOCK = threading.Lock()
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY: set[str] = set()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS remote_collector (
    site_id              TEXT NOT NULL,
    collector_id         TEXT NOT NULL,
    collector_version    TEXT,
    probe_interval_s     INTEGER,
    buffer_epoch         TEXT,
    buffer_epoch_changed_at TEXT,
    first_seen_at        TEXT NOT NULL,
    last_seen_at         TEXT NOT NULL,
    last_sent_at         TEXT,
    clock_skew_s         REAL,
    time_quality         TEXT NOT NULL DEFAULT 'unknown'
                         CHECK (time_quality IN ('ok','skewed','unknown')),
    buffer_pending       INTEGER,
    buffer_oldest_ts     TEXT,
    dropped_total        INTEGER,
    dropped_total_max    INTEGER,
    gap_since            TEXT,
    paused_reason        TEXT,
    last_batch_accepted  INTEGER,
    last_batch_duplicates INTEGER,
    last_batch_rejected  INTEGER,
    last_sync_error      TEXT,
    last_sync_error_at   TEXT,
    sync_failures        INTEGER,
    auth_state           TEXT CHECK (auth_state IN ('ok','error','unknown') OR auth_state IS NULL),
    last_remote_addr     TEXT,
    PRIMARY KEY (site_id, collector_id)
);

CREATE TABLE IF NOT EXISTS remote_sample (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id       TEXT NOT NULL,
    collector_id  TEXT NOT NULL,
    device_id     TEXT NOT NULL,
    metric        TEXT NOT NULL
                  CHECK (metric IN ('reachable','latency_ms','packet_loss_pct')),
    ts            TEXT NOT NULL,
    received_at   TEXT NOT NULL,
    sync_batch_id TEXT,
    value_num     REAL,
    value_bool    INTEGER CHECK (value_bool IN (0,1) OR value_bool IS NULL),
    ok            INTEGER NOT NULL CHECK (ok IN (0,1)),
    probe_kind    TEXT NOT NULL CHECK (probe_kind IN ('icmp','tcp')),
    error         TEXT,
    UNIQUE (site_id, device_id, ts, metric)
);

CREATE INDEX IF NOT EXISTS idx_remote_sample_device_ts
    ON remote_sample (site_id, device_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_remote_sample_ts
    ON remote_sample (ts);

CREATE TABLE IF NOT EXISTS remote_device_state (
    site_id          TEXT NOT NULL,
    device_id        TEXT NOT NULL,
    collector_id     TEXT NOT NULL,
    last_ts          TEXT NOT NULL,
    last_received_at TEXT NOT NULL,
    reachable        INTEGER CHECK (reachable IN (0,1) OR reachable IS NULL),
    latency_ms       REAL,
    packet_loss_pct  REAL,
    probe_kind       TEXT,
    ok               INTEGER NOT NULL CHECK (ok IN (0,1)),
    error            TEXT,
    inventory_match  INTEGER CHECK (inventory_match IN (0,1) OR inventory_match IS NULL),
    inventory_error  TEXT,
    PRIMARY KEY (site_id, device_id)
);

CREATE TABLE IF NOT EXISTS remote_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class RemoteStoreUnavailable(RuntimeError):
    """La DB existe pero no se puede leer/escribir con garantías."""


def db_path() -> Path:
    raw = os.getenv("NETPULSE_REMOTE_DB")
    if raw:
        return Path(raw).expanduser()
    return Path(settings.BASE_DIR) / "runtime" / "remote_collector.db"


def db_exists() -> bool:
    return db_path().is_file()


def retention_days() -> int:
    try:
        return max(1, int(os.getenv("NETPULSE_REMOTE_RETENTION_DAYS", "30")))
    except ValueError:
        return 30


def prune_interval_s() -> int:
    try:
        return max(60, int(os.getenv("NETPULSE_REMOTE_PRUNE_INTERVAL_S", "3600")))
    except ValueError:
        return 3600


def _ensure_schema(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RemoteStoreUnavailable(
            f"esquema remoto más nuevo ({version}) que el soportado ({SCHEMA_VERSION})"
        )
    conn.executescript(_SCHEMA)
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    conn.execute(
        "INSERT INTO remote_meta (key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


def _connect() -> sqlite3.Connection:
    path = db_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        key = str(path)
        if key not in _SCHEMA_READY:
            with _SCHEMA_LOCK:
                if key not in _SCHEMA_READY:
                    _ensure_schema(conn)
                    _SCHEMA_READY.add(key)
        return conn
    except (sqlite3.DatabaseError, OSError) as exc:
        try:
            conn.close()  # type: ignore[name-defined]
        except Exception:
            pass
        raise RemoteStoreUnavailable(f"DB remota ilegible: {exc}") from exc


@contextmanager
def read_connection() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    with _WRITE_LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            raise RemoteStoreUnavailable(f"DB remota no disponible: {exc}") from exc
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM remote_meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row is not None else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO remote_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def should_prune(conn: sqlite3.Connection, now: datetime | None = None) -> bool:
    last = get_meta(conn, "last_prune_at")
    if not last:
        return True
    try:
        return ((now or utcnow()) - parse_iso(last)).total_seconds() >= prune_interval_s()
    except (ValueError, TypeError):
        return True


def prune_samples(conn: sqlite3.Connection, now: datetime | None = None) -> int:
    """Poda ``remote_sample`` por antigüedad en lotes de 10 000."""
    current = now or utcnow()
    cutoff = (current - timedelta(days=retention_days())).isoformat(timespec="microseconds")
    total = 0
    while True:
        cur = conn.execute(
            "DELETE FROM remote_sample WHERE id IN ("
            "SELECT id FROM remote_sample WHERE ts < ? LIMIT 10000)",
            (cutoff,),
        )
        deleted = max(0, cur.rowcount)
        total += deleted
        if deleted < 10000:
            break
    set_meta(conn, "last_prune_at", current.isoformat(timespec="microseconds"))
    if total:
        logger.info("[remote] podadas %s muestras anteriores a %s", total, cutoff)
    return total


def db_size_bytes() -> int:
    total = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            total += Path(str(db_path()) + suffix).stat().st_size
        except OSError:
            pass
    return total
