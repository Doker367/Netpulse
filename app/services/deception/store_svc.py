"""NetPulse — Deception Telemetry Store.

Almacén append-only (SQLite WAL) de los eventos que generan los
atacantes sobre la red señuelo. Sigue el mismo patrón que
``app/services/audit_svc.py``: escrituras síncronas y ligeras, lecturas
filtradas/paginadas, y agregaciones para el dashboard.

Los eventos llegan ya normalizados (``DeceptionEventIn``) desde
cualquier motor: listeners nativos, engine en Go o honeypots reales
(Cowrie/OpenCanary) mediante la capa de integración.
"""

import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.core.settings import DECEPTION_DB
from app.models.deception import DeceptionEventIn

logger = logging.getLogger(__name__)

# Ruta del archivo SQLite (monkeypatch-able en tests)
DB_PATH = DECEPTION_DB

# Un solo writer a la vez en el proceso
_write_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS deception_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    device_id TEXT NOT NULL,
    src_ip TEXT NOT NULL,
    src_port INTEGER,
    proto TEXT DEFAULT '',
    event_type TEXT NOT NULL,
    username TEXT,
    success INTEGER,
    detail TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_deception_events_ts ON deception_events(ts);
CREATE INDEX IF NOT EXISTS idx_deception_events_dev ON deception_events(device_id);
CREATE INDEX IF NOT EXISTS idx_deception_events_src ON deception_events(src_ip);
"""


def _ensure_db() -> None:
    """Crea el directorio/BD si no existen y aplica el esquema."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.executescript(_SCHEMA)


def _connect() -> sqlite3.Connection:
    """Conexión corta (thread-safe por check_same_thread=False)."""
    return sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10)


# ── Escritura ────────────────────────────────────────────────


def log_event(event: DeceptionEventIn) -> dict:
    """Inserta un evento de deception normalizado (append-only).

    Returns:
        El evento tal como quedó representado (dict).
    """
    ts = event.ts or datetime.now(timezone.utc).isoformat()
    detail_json = json.dumps(event.detail or {}, ensure_ascii=False)
    etype = event.type.value if hasattr(event.type, "value") else str(event.type)

    record = {
        "timestamp": ts,
        "device_id": event.device_id,
        "src_ip": event.src_ip,
        "src_port": event.src_port,
        "proto": event.proto,
        "type": etype,
        "username": event.username,
        "success": bool(event.success),
        "detail": event.detail or {},
    }

    try:
        _ensure_db()
        with _write_lock, sqlite3.connect(str(DB_PATH), timeout=10) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                """INSERT INTO deception_events
                   (ts, device_id, src_ip, src_port, proto, event_type,
                    username, success, detail)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    ts, event.device_id, event.src_ip, event.src_port,
                    event.proto, etype, event.username,
                    1 if event.success else 0, detail_json,
                ),
            )
            record["id"] = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    except Exception as exc:  # noqa: BLE001
        logger.error("[deception] fallo al escribir evento (%s): %s", etype, exc)
        record.setdefault("id", 0)

    return record


# ── Lectura ──────────────────────────────────────────────────


def _build_filters(
    device_id: Optional[str],
    src_ip: Optional[str],
    event_type: Optional[str],
) -> tuple[str, list]:
    where: list[str] = []
    params: list = []
    if device_id:
        where.append("device_id = ?")
        params.append(device_id)
    if src_ip:
        where.append("src_ip = ?")
        params.append(src_ip)
    if event_type:
        where.append("event_type = ?")
        params.append(event_type)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    return clause, params


def get_events(
    limit: int = 100,
    offset: int = 0,
    device_id: Optional[str] = None,
    src_ip: Optional[str] = None,
    event_type: Optional[str] = None,
) -> list[dict]:
    """Lista eventos (más recientes primero) con filtros y paginación."""
    if not DB_PATH.exists():
        return []

    clause, params = _build_filters(device_id, src_ip, event_type)
    sql = f"SELECT * FROM deception_events{clause} ORDER BY id DESC LIMIT ? OFFSET ?"
    params += [limit, offset]

    try:
        with _write_lock:
            conn = _connect()
            try:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(sql, params).fetchall()
            finally:
                conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.error("[deception] error leyendo eventos: %s", exc)
        return []

    return [_row_to_event(r) for r in rows]


def get_sessions(limit: int = 100) -> list[dict]:
    """Agrega eventos por (dispositivo, atacante) para la vista de sesiones."""
    if not DB_PATH.exists():
        return []

    sql = """
        SELECT device_id, src_ip,
               MAX(proto) AS proto,
               MIN(ts) AS started_at,
               MAX(ts) AS last_seen,
               COUNT(*) AS event_count,
               SUM(CASE WHEN event_type = 'command' THEN 1 ELSE 0 END) AS command_count,
               MAX(success) AS success
        FROM deception_events
        GROUP BY device_id, src_ip
        ORDER BY last_seen DESC
        LIMIT ?
    """
    try:
        with _write_lock:
            conn = _connect()
            try:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(sql, [limit]).fetchall()
            finally:
                conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.error("[deception] error leyendo sesiones: %s", exc)
        return []

    return [
        {
            "device_id": r["device_id"],
            "src_ip": r["src_ip"],
            "proto": r["proto"] or "",
            "started_at": r["started_at"],
            "last_seen": r["last_seen"],
            "event_count": r["event_count"],
            "command_count": r["command_count"] or 0,
            "success": bool(r["success"]),
        }
        for r in rows
    ]


def get_stats() -> dict:
    """Resumen agregado para el dashboard."""
    if not DB_PATH.exists():
        return {
            "events_total": 0,
            "attackers_unique": 0,
            "by_type": {},
            "top_devices": [],
            "top_attackers": [],
        }

    stats: dict = {
        "events_total": 0,
        "attackers_unique": 0,
        "by_type": {},
        "top_devices": [],
        "top_attackers": [],
    }
    try:
        with _write_lock:
            conn = _connect()
            try:
                conn.row_factory = sqlite3.Row
                stats["events_total"] = conn.execute(
                    "SELECT COUNT(*) FROM deception_events"
                ).fetchone()[0]
                stats["attackers_unique"] = conn.execute(
                    "SELECT COUNT(DISTINCT src_ip) FROM deception_events"
                ).fetchone()[0]
                stats["by_type"] = {
                    r["event_type"]: r["n"]
                    for r in conn.execute(
                        "SELECT event_type, COUNT(*) AS n FROM deception_events "
                        "GROUP BY event_type ORDER BY n DESC"
                    ).fetchall()
                }
                stats["top_devices"] = [
                    {"device_id": r["device_id"], "count": r["n"]}
                    for r in conn.execute(
                        "SELECT device_id, COUNT(*) AS n FROM deception_events "
                        "GROUP BY device_id ORDER BY n DESC LIMIT 10"
                    ).fetchall()
                ]
                stats["top_attackers"] = [
                    {"src_ip": r["src_ip"], "count": r["n"]}
                    for r in conn.execute(
                        "SELECT src_ip, COUNT(*) AS n FROM deception_events "
                        "GROUP BY src_ip ORDER BY n DESC LIMIT 10"
                    ).fetchall()
                ]
            finally:
                conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.error("[deception] stats error: %s", exc)

    return stats


def get_timeline(minutes: int = 30) -> list[dict]:
    """Serie de eventos por minuto (para el gráfico de actividad).

    Args:
        minutes: tamaño de la ventana en minutos.

    Returns:
        Lista ordenada de ``{"bucket": ISO-minuto, "count": n}``.
    """
    if not DB_PATH.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    buckets: dict[str, int] = {}
    try:
        with _write_lock:
            conn = _connect()
            try:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT ts FROM deception_events WHERE ts >= ? ORDER BY ts",
                    (cutoff.isoformat(),),
                ).fetchall()
            finally:
                conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.error("[deception] timeline error: %s", exc)
        return []

    for row in rows:
        ts = _parse_iso(row["ts"])
        if ts is None:
            continue
        key = ts.replace(second=0, microsecond=0).isoformat()
        buckets[key] = buckets.get(key, 0) + 1
    return [{"bucket": k, "count": buckets[k]} for k in sorted(buckets)]


def events_to_csv(limit: int = 1000) -> str:
    """Exporta los eventos de deception a CSV."""
    import csv
    import io

    buffer = io.StringIO()
    headers = ["timestamp", "device_id", "src_ip", "src_port", "proto",
               "type", "username", "success", "detail"]
    writer = csv.DictWriter(buffer, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    for event in get_events(limit=limit):
        row = dict(event)
        row["detail"] = json.dumps(row.get("detail") or {}, ensure_ascii=False)
        writer.writerow({h: row.get(h, "") for h in headers})
    return buffer.getvalue()


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def retain(days: int = 90) -> int:
    """Borra eventos más antiguos que N días. Retorna cuántos eliminó."""
    if days <= 0:
        return 0
    try:
        _ensure_db()
        cutoff = datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() - days * 86400, tz=timezone.utc
        ).isoformat()
        with _write_lock, sqlite3.connect(str(DB_PATH), timeout=10) as conn:
            cur = conn.execute("DELETE FROM deception_events WHERE ts < ?", (cutoff,))
            return cur.rowcount
    except Exception as exc:  # noqa: BLE001
        logger.error("[deception] retain error: %s", exc)
        return 0


# ── Helpers ──────────────────────────────────────────────────


def _row_to_event(row: sqlite3.Row) -> dict:
    """Convierte una fila SQLite al dict de evento de la API."""
    try:
        detail = json.loads(row["detail"] or "{}")
    except json.JSONDecodeError:
        detail = {}
    return {
        "id": row["id"],
        "timestamp": row["ts"],
        "device_id": row["device_id"],
        "src_ip": row["src_ip"],
        "src_port": row["src_port"],
        "proto": row["proto"] or "",
        "type": row["event_type"],
        "username": row["username"],
        "success": bool(row["success"]),
        "detail": detail,
    }
