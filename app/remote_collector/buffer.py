"""Buffer local SQLite del colector remoto.

Una fila por métrica; ``pending`` → ``synced``/``dead``. La poda está acotada
por filas, antigüedad y disco. Cuando se descarta algo se incrementa
``dropped_total`` y se conserva ``gap_since``: el centro puede declarar el
hueco, pero el colector nunca inventa continuidad.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

from app.remote_collector.config import BufferConfig, DeviceSpec
from app.remote_collector.timeutil import iso_now, utcnow
from app.services.collectors.probe_svc import ProbeResult

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS probe_batch (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT NOT NULL,
    device_count  INTEGER NOT NULL,
    error_count   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sample (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id        INTEGER NOT NULL,
    ts              TEXT NOT NULL,
    device_id       TEXT NOT NULL,
    metric          TEXT NOT NULL
                    CHECK (metric IN ('reachable','latency_ms','packet_loss_pct')),
    value_num       REAL,
    value_bool      INTEGER CHECK (value_bool IN (0,1) OR value_bool IS NULL),
    ok              INTEGER NOT NULL CHECK (ok IN (0,1)),
    probe_kind      TEXT NOT NULL CHECK (probe_kind IN ('icmp','tcp')),
    error           TEXT,
    sync_state      TEXT NOT NULL DEFAULT 'pending'
                    CHECK (sync_state IN ('pending','synced','dead')),
    sync_attempts   INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    synced_at       TEXT,
    dead_reason     TEXT,
    UNIQUE (device_id, ts, metric)
);

CREATE INDEX IF NOT EXISTS idx_sample_pending ON sample (sync_state, id);
CREATE INDEX IF NOT EXISTS idx_sample_ts ON sample (ts);

CREATE TABLE IF NOT EXISTS collector_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class BufferError(RuntimeError):
    """El buffer no se puede abrir o escribir."""


def _truncate(text: str | None, limit: int = 200) -> str | None:
    if text is None:
        return None
    text = " ".join(str(text).split())
    return text[:limit]


class Buffer:
    """Buffer SQLite thread-safe (un writer por proceso)."""

    def __init__(self, config: BufferConfig):
        self.config = config
        self.path = Path(config.path)
        self._lock = threading.RLock()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = self._connect()
            try:
                self._ensure_schema(conn)
            finally:
                conn.close()
        except (OSError, sqlite3.Error) as exc:
            raise BufferError(f"no se pudo abrir el buffer {self.path}: {exc}") from exc

    # ── conexión / esquema ───────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(_SCHEMA)
        row = conn.execute(
            "SELECT value FROM collector_state WHERE key='schema_version'"
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO collector_state (key, value) VALUES ('schema_version','1')"
            )
        conn.execute(
            "INSERT OR IGNORE INTO collector_state (key, value) VALUES ('buffer_epoch', ?)",
            (str(uuid.uuid4()),),
        )
        conn.execute(
            "INSERT OR IGNORE INTO collector_state (key, value) VALUES ('created_at', ?)",
            (iso_now(),),
        )
        conn.execute(
            "INSERT OR IGNORE INTO collector_state (key, value) VALUES ('dropped_total','0')"
        )
        conn.commit()

    # ── estado clave/valor ───────────────────────────────────

    def get_state(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT value FROM collector_state WHERE key=?", (key,)
                ).fetchone()
                return row["value"] if row is not None else default
            finally:
                conn.close()

    def set_state(self, key: str, value: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO collector_state (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value),
                )
                conn.commit()
            finally:
                conn.close()

    def buffer_epoch(self) -> str:
        return self.get_state("buffer_epoch", "") or ""

    # ── escritura de un ciclo ────────────────────────────────

    def insert_probe(
        self,
        ts: str,
        results: Sequence[tuple[DeviceSpec, ProbeResult]],
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> int:
        """Guarda un ciclo completo en una transacción.

        ``ts`` es la hora de medición del ciclo; todas las filas del ciclo la
        comparten. Devuelve el número de filas insertadas (las duplicadas por
        la clave única se ignoran).
        """
        started = started_at or ts
        finished = finished_at or iso_now()
        error_count = sum(
            1 for _, result in results
            if not result.ok or result.reachable is False
        )
        inserted = 0
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                cur = conn.execute(
                    "INSERT INTO probe_batch (started_at, finished_at, device_count, error_count) "
                    "VALUES (?, ?, ?, ?)",
                    (started, finished, len(results), error_count),
                )
                batch_id = int(cur.lastrowid)
                rows: list[tuple[Any, ...]] = []
                for device, result in results:
                    value_bool = None
                    if result.ok and result.reachable is not None:
                        value_bool = 1 if result.reachable else 0
                    rows.append((
                        batch_id, ts, device.id, "reachable", None, value_bool,
                        1 if result.ok else 0, result.probe_kind,
                        _truncate(result.error),
                    ))
                    if result.ok and result.reachable and result.latency_ms is not None:
                        rows.append((
                            batch_id, ts, device.id, "latency_ms",
                            float(result.latency_ms), None, 1, result.probe_kind, None,
                        ))
                    if (
                        result.ok
                        and result.probe_kind == "icmp"
                        and result.packet_loss_pct is not None
                    ):
                        rows.append((
                            batch_id, ts, device.id, "packet_loss_pct",
                            float(result.packet_loss_pct), None, 1,
                            result.probe_kind, None,
                        ))
                for row in rows:
                    cur = conn.execute(
                        "INSERT INTO sample "
                        "(batch_id, ts, device_id, metric, value_num, value_bool, ok, "
                        "probe_kind, error) VALUES (?,?,?,?,?,?,?,?,?) "
                        "ON CONFLICT (device_id, ts, metric) DO NOTHING",
                        row,
                    )
                    inserted += max(0, cur.rowcount)
                conn.commit()
                return inserted
            except sqlite3.Error:
                conn.rollback()
                raise
            finally:
                conn.close()

    # ── lectura / marcado para sync ──────────────────────────

    def peek_pending(self, limit: int) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT id, ts, device_id, metric, value_num, value_bool, ok, "
                    "probe_kind, error FROM sample WHERE sync_state='pending' "
                    "ORDER BY id ASC LIMIT ?",
                    (int(limit),),
                ).fetchall()
                return [dict(row) for row in rows]
            finally:
                conn.close()

    def mark_synced(self, ids: Iterable[int]) -> int:
        ids = list(ids)
        if not ids:
            return 0
        now = iso_now()
        with self._lock:
            conn = self._connect()
            try:
                updated = 0
                for chunk in _chunks(ids, 900):
                    placeholders = ",".join("?" for _ in chunk)
                    cur = conn.execute(
                        f"UPDATE sample SET sync_state='synced', synced_at=?, "
                        f"sync_attempts=sync_attempts+1, last_attempt_at=? "
                        f"WHERE id IN ({placeholders}) AND sync_state='pending'",
                        (now, now, *chunk),
                    )
                    updated += max(0, cur.rowcount)
                conn.commit()
                return updated
            finally:
                conn.close()

    def mark_attempt(self, ids: Iterable[int]) -> int:
        """Registra un intento fallido sin cambiar el estado de sync."""
        ids = list(ids)
        if not ids:
            return 0
        now = iso_now()
        with self._lock:
            conn = self._connect()
            try:
                updated = 0
                for chunk in _chunks(ids, 900):
                    placeholders = ",".join("?" for _ in chunk)
                    cur = conn.execute(
                        f"UPDATE sample SET sync_attempts=sync_attempts+1, "
                        f"last_attempt_at=? WHERE id IN ({placeholders})",
                        (now, *chunk),
                    )
                    updated += max(0, cur.rowcount)
                conn.commit()
                return updated
            finally:
                conn.close()

    def mark_dead(self, items: Iterable[tuple[str, str, str]], reason: str) -> int:
        """Marca ``dead`` por triplete ``(device_id, ts, metric)``."""
        items = list(items)
        if not items:
            return 0
        now = iso_now()
        reason = _truncate(reason) or "rechazado por el servidor"
        with self._lock:
            conn = self._connect()
            try:
                updated = 0
                for device_id, ts, metric in items:
                    cur = conn.execute(
                        "UPDATE sample SET sync_state='dead', dead_reason=?, "
                        "sync_attempts=sync_attempts+1, last_attempt_at=? "
                        "WHERE device_id=? AND ts=? AND metric=? AND sync_state='pending'",
                        (reason, now, device_id, ts, metric),
                    )
                    updated += max(0, cur.rowcount)
                conn.commit()
                return updated
            finally:
                conn.close()

    # ── poda / disco ─────────────────────────────────────────

    def counts(self) -> dict[str, int]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT sync_state, COUNT(*) AS n FROM sample GROUP BY sync_state"
                ).fetchall()
                result = {"pending": 0, "synced": 0, "dead": 0, "total": 0}
                for row in rows:
                    result[row["sync_state"]] = int(row["n"])
                    result["total"] += int(row["n"])
                return result
            finally:
                conn.close()

    def summary(self) -> dict[str, Any]:
        counts = self.counts()
        with self._lock:
            conn = self._connect()
            try:
                oldest = conn.execute(
                    "SELECT MIN(ts) AS ts FROM sample WHERE sync_state='pending'"
                ).fetchone()["ts"]
            finally:
                conn.close()
        return {
            "buffer_epoch": self.buffer_epoch(),
            "pending": counts["pending"],
            "dead": counts["dead"],
            "oldest_pending_ts": oldest,
            "dropped_total": int(self.get_state("dropped_total", "0") or 0),
            "gap_since": self.get_state("gap_since") or None,
            "paused_reason": self.paused_reason(),
            "free_mb": self.free_mb(),
        }

    def free_mb(self) -> float | None:
        try:
            usage = shutil.disk_usage(str(self.path.parent))
        except OSError:
            return None
        return round(usage.free / (1024 * 1024), 1)

    def paused_reason(self) -> str | None:
        value = self.get_state("paused_reason", "") or ""
        return value or None

    def check_disk(self) -> str | None:
        """Pausa por disco bajo y reanuda con histéresis de 50 MB."""
        free = self.free_mb()
        if free is None:
            return self.paused_reason()
        current = self.paused_reason()
        if free < self.config.min_free_mb:
            if current != "disk_low":
                self.set_state("paused_reason", "disk_low")
                logger.warning(
                    "[buffer] sonda pausada: free_mb=%s < min_free_mb=%s",
                    free, self.config.min_free_mb,
                )
            return "disk_low"
        if current == "disk_low" and free >= self.config.min_free_mb + 50:
            self.set_state("paused_reason", "")
            logger.info("[buffer] sonda reanudada: free_mb=%s", free)
            return None
        return current

    def prune(self, now: datetime | None = None) -> dict[str, int]:
        """Aplica la política de cotas y devuelve contadores de la pasada."""
        current = now or utcnow()
        synced_cutoff = (
            current - timedelta(hours=self.config.synced_retention_hours)
        ).isoformat(timespec="microseconds")
        age_cutoff = (
            current - timedelta(hours=self.config.max_age_hours)
        ).isoformat(timespec="microseconds")
        stats = {"deleted_synced": 0, "dropped_unsynced": 0, "dropped_by_rows": 0}
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                cur = conn.execute(
                    "DELETE FROM sample WHERE sync_state='synced' AND ts < ?",
                    (synced_cutoff,),
                )
                stats["deleted_synced"] = max(0, cur.rowcount)
                conn.execute(
                    "DELETE FROM probe_batch WHERE finished_at < ?", (age_cutoff,)
                )
                # Cota de antigüedad de lo no sincronizado (aunque quepan filas).
                old_ts = conn.execute(
                    "SELECT MIN(ts) AS ts FROM sample "
                    "WHERE sync_state IN ('pending','dead') AND ts < ?",
                    (age_cutoff,),
                ).fetchone()["ts"]
                cur = conn.execute(
                    "DELETE FROM sample WHERE sync_state IN ('pending','dead') AND ts < ?",
                    (age_cutoff,),
                )
                dropped_age = max(0, cur.rowcount)
                stats["dropped_unsynced"] = dropped_age
                if dropped_age:
                    self._bump_dropped(conn, dropped_age, old_ts)

                total = conn.execute("SELECT COUNT(*) FROM sample").fetchone()[0]
                if total > self.config.max_rows:
                    overflow = total - self.config.max_rows
                    cur = conn.execute(
                        "DELETE FROM sample WHERE id IN ("
                        "SELECT id FROM sample WHERE sync_state='synced' "
                        "ORDER BY id ASC LIMIT ?)",
                        (overflow,),
                    )
                    stats["deleted_synced"] += max(0, cur.rowcount)
                    total = conn.execute("SELECT COUNT(*) FROM sample").fetchone()[0]

                if total > self.config.max_rows:
                    overflow = total - self.config.max_rows
                    victims = conn.execute(
                        "SELECT id, ts FROM sample "
                        "WHERE sync_state IN ('pending','dead') "
                        "ORDER BY id ASC LIMIT ?",
                        (overflow,),
                    ).fetchall()
                    if victims:
                        ids = [row["id"] for row in victims]
                        placeholders = ",".join("?" for _ in ids)
                        conn.execute(
                            f"DELETE FROM sample WHERE id IN ({placeholders})", ids
                        )
                        stats["dropped_by_rows"] = len(ids)
                        self._bump_dropped(
                            conn, len(ids), min(row["ts"] for row in victims)
                        )

                conn.execute(
                    "INSERT INTO collector_state (key, value) VALUES ('last_prune_at', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (iso_now(),),
                )
                conn.commit()
            except sqlite3.Error:
                conn.rollback()
                raise
            finally:
                conn.close()
        try:
            with self._lock:
                conn = self._connect()
                try:
                    conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                finally:
                    conn.close()
        except sqlite3.Error:
            pass
        if stats["dropped_unsynced"] or stats["dropped_by_rows"]:
            logger.warning(
                "[buffer] descartadas: edad=%s filas=%s",
                stats["dropped_unsynced"], stats["dropped_by_rows"],
            )
        return stats

    def _bump_dropped(
        self, conn: sqlite3.Connection, count: int, oldest_ts: str | None
    ) -> None:
        if count <= 0:
            return
        row = conn.execute(
            "SELECT value FROM collector_state WHERE key='dropped_total'"
        ).fetchone()
        current = int(row["value"]) if row is not None else 0
        conn.execute(
            "INSERT INTO collector_state (key, value) VALUES ('dropped_total', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(current + count),),
        )
        if oldest_ts:
            row = conn.execute(
                "SELECT value FROM collector_state WHERE key='gap_since'"
            ).fetchone()
            existing = row["value"] if row is not None else None
            gap = min(existing, oldest_ts) if existing else oldest_ts
            conn.execute(
                "INSERT INTO collector_state (key, value) VALUES ('gap_since', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (gap,),
            )
        conn.execute(
            "INSERT INTO collector_state (key, value) VALUES ('dropped_last_at', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (iso_now(),),
        )

    def sync_state(self) -> dict[str, Any]:
        failures = int(self.get_state("consecutive_failures", "0") or 0)
        return {
            "last_success_at": self.get_state("last_success_at") or None,
            "last_error": self.get_state("last_error") or None,
            "last_error_at": self.get_state("last_error_at") or None,
            "consecutive_failures": failures,
            "auth_state": self.get_state("auth_state", "unknown") or "unknown",
        }

    def record_sync_success(self, when: str | None = None) -> None:
        now = when or iso_now()
        with self._lock:
            for key, value in (
                ("last_success_at", now),
                ("consecutive_failures", "0"),
                ("auth_state", "ok"),
            ):
                self.set_state(key, value)

    def record_sync_failure(self, error: str, auth_error: bool = False) -> None:
        failures = int(self.get_state("consecutive_failures", "0") or 0) + 1
        now = iso_now()
        with self._lock:
            self.set_state("consecutive_failures", str(failures))
            self.set_state("last_error", _truncate(error) or "error de sync")
            self.set_state("last_error_at", now)
            if auth_error:
                self.set_state("auth_state", "error")

    def db_size_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(self.path) + suffix)
            try:
                total += path.stat().st_size
            except OSError:
                pass
        return total


def _chunks(values: list[int], size: int):
    for index in range(0, len(values), size):
        yield values[index:index + size]
