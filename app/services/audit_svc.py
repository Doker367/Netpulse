"""NetPulse — Immutable Audit Logging Service.

Desde Fase 2 el log de auditoría se guarda en SQLite (WAL) en vez de un
JSONL append-only: consultas filtradas/paginadas en O(log n), sin tener
que leer todo el archivo por request. Los eventos se insertan (nunca se
actualizan/borran) y cada escritura es síncrona pero muy ligera; en el
middleware se delegan a un executor para no bloquear el event loop.

Compatible con la API anterior (log_event / get_events / get_audit_stats).
"""

import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.core.settings import BASE_DIR
from app.services.metrics_svc import netpulse_audit_events_total

logger = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────

AUDIT_DIR = BASE_DIR / "audit"
AUDIT_DB = AUDIT_DIR / "audit.db"

# Lock para SQLite (un solo writer a la vez en el proceso)
_write_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    action TEXT NOT NULL,
    device_id TEXT,
    username TEXT,
    details TEXT DEFAULT '',
    ip_address TEXT DEFAULT '',
    success INTEGER,
    session_id TEXT,
    path TEXT DEFAULT '',
    method TEXT DEFAULT '',
    response_status INTEGER
);
"""


def _ensure_db() -> None:
    """Crea el directorio/BD si no existen y aplica el esquema."""
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(AUDIT_DB)) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute(_SCHEMA)


def _connect() -> sqlite3.Connection:
    """Conexión corta y thread-safe (check_same_thread=False por si migra a threads)."""
    return sqlite3.connect(str(AUDIT_DB), check_same_thread=False, timeout=10)


# ── Public API ────────────────────────────────────────────────

def log_event(
    action: str,
    device_id: Optional[str] = None,
    username: str = "anonymous",
    details: Optional[str] = None,
    ip_address: Optional[str] = None,
    success: bool = True,
    session_id: Optional[str] = None,
    path: Optional[str] = None,
    method: Optional[str] = None,
    response_status: Optional[int] = None,
) -> dict:
    """Log an immutable audit event (append-only).

    Returns:
        The logged event dict.
    """
    ts = datetime.now(timezone.utc).isoformat()
    event = {
        "timestamp": ts,
        "action": action,
        "device_id": device_id,
        "username": username,
        "details": details or "",
        "ip_address": ip_address or "",
        "success": bool(success),
        "session_id": session_id or str(uuid.uuid4()),
        "path": path or "",
        "method": method or "",
        "response_status": response_status,
    }

    try:
        _ensure_db()
        with _write_lock, sqlite3.connect(str(AUDIT_DB), timeout=10) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                """INSERT INTO audit
                   (ts, action, device_id, username, details, ip_address,
                    success, session_id, path, method, response_status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    ts, action, device_id, username, event["details"],
                    event["ip_address"], 1 if success else 0,
                    event["session_id"], path or "", method or "",
                    response_status,
                ),
            )
    except Exception as e:
        logger.error("[audit] fallo al escribir evento (%s): %s", action, e)
        return event

    netpulse_audit_events_total.inc()
    return event


def get_events(
    limit: int = 100,
    offset: int = 0,
    device_id: Optional[str] = None,
    username: Optional[str] = None,
    action: Optional[str] = None,
) -> list[dict]:
    """Lista eventos de auditoría (más recientes primero) con filtros y paginación.

    Returns:
        List of matching event dicts (most recent first).
    """
    if not AUDIT_DB.exists():
        return []

    where = []
    params: list = []
    if device_id:
        where.append("device_id = ?")
        params.append(device_id)
    if username:
        where.append("username = ?")
        params.append(username)
    if action:
        where.append("action = ?")
        params.append(action)

    sql = "SELECT * FROM audit"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params += [limit, offset]

    rows = []
    try:
        with _write_lock:
            conn = _connect()
            try:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(sql, params).fetchall()
            finally:
                conn.close()
    except Exception as e:
        logger.error("[audit] error leyendo eventos: %s", e)
        return []

    return [_row_to_event(r) for r in rows]


def get_audit_stats() -> dict:
    """Estadísticas básicas del log de auditoría.

    Returns:
        Dict with total_events, file_size_bytes, and file path.
    """
    if not AUDIT_DB.exists():
        return {
            "total_events": 0,
            "file_size_bytes": 0,
            "file_path": str(AUDIT_DB),
        }

    total = 0
    try:
        with _write_lock:
            conn = _connect()
            try:
                total = conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
            finally:
                conn.close()
    except Exception as e:
        logger.error("[audit] stats error: %s", e)

    return {
        "total_events": total,
        "file_size_bytes": AUDIT_DB.stat().st_size if AUDIT_DB.exists() else 0,
        "file_path": str(AUDIT_DB),
    }


def retain(days: int = 90) -> int:
    """Borra eventos más antiguos que N días. Retorna cuántos eliminó."""
    if days <= 0:
        return 0
    try:
        _ensure_db()
        cutoff = datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() - days * 86400,
            tz=timezone.utc).isoformat()
        with _write_lock, sqlite3.connect(str(AUDIT_DB), timeout=10) as conn:
            cur = conn.execute(
                "DELETE FROM audit WHERE ts < ?",
                (cutoff,),
            )
            return cur.rowcount
    except Exception as e:
        logger.error("[audit] retain error: %s", e)
        return 0


# ── Helpers ──────────────────────────────────────────────────

def _row_to_event(row) -> dict:
    """Convierte una fila SQLite al dict de evento de la API anterior."""
    return {
        "timestamp": row["ts"],
        "action": row["action"],
        "device_id": row["device_id"],
        "username": row["username"],
        "details": row["details"] or "",
        "ip_address": row["ip_address"] or "",
        "success": bool(row["success"]),
        "session_id": row["session_id"] or "",
        "path": row["path"] or "",
        "method": row["method"] or "",
        "response_status": row["response_status"],
    }


# ── Migración desde el JSONL legacy (una vez, opcional) ─────

_LEGACY_JSONL = AUDIT_DIR / "audit.jsonl"


def migrate_legacy_jsonl() -> int:
    """Importa el audit.jsonl antiguo a SQLite (idempotente por marca)."""
    if not _LEGACY_JSONL.exists():
        return 0
    marker = AUDIT_DIR / ".jsonl_migrated"
    if marker.exists():
        return 0
    count = 0
    try:
        with open(_LEGACY_JSONL, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                try:
                    log_event(
                        action=ev.get("action", "unknown"),
                        device_id=ev.get("device_id"),
                        username=ev.get("username", "anonymous"),
                        details=ev.get("details"),
                        ip_address=ev.get("ip_address"),
                        success=bool(ev.get("success", True)),
                        session_id=ev.get("session_id"),
                        path=ev.get("path"),
                        method=ev.get("method"),
                        response_status=ev.get("response_status"),
                    )
                    count += 1
                except Exception:
                    continue
        marker.write_text("ok")
        logger.info("[audit] migrados %d eventos legacy JSONL → SQLite", count)
    except Exception as e:
        logger.error("[audit] migración JSONL falló: %s", e)
    return count
