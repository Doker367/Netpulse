"""Inventario de retención y purga explícita del audit trail de NetPulse."""

import json
import logging
import sqlite3
import urllib.request
from datetime import datetime, timedelta, timezone

from app.core import settings
from app.routers import monitoring
from app.services import audit_svc

logger = logging.getLogger(__name__)


class RetentionUnavailable(Exception):
    """No se pudo medir o modificar un recurso sin arriesgar datos engañosos."""


def prometheus_inventory() -> dict:
    """Consulta flags reales de Prometheus; no confunde defaults con configuración."""
    result = {"retention_time": None, "retention_size": None,
              "configurado": False, "estado": "sin medir"}
    try:
        with urllib.request.urlopen(f"{monitoring.PROMETHEUS_URL}/api/v1/status/flags", timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("respuesta de status inválida")
        if payload.get("status") != "success" or not isinstance(payload.get("data"), dict):
            raise ValueError(payload.get("error") or "respuesta de status inválida")
        flags = payload["data"]
        for name, key in (("retention_time", "storage.tsdb.retention.time"),
                          ("retention_size", "storage.tsdb.retention.size")):
            value = flags.get(key)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"flag {key} inválido")
            result[name] = value or None
        result["configurado"] = bool(result["retention_time"] or result["retention_size"])
        result["estado"] = "con datos" if result["configurado"] else "sin medir"
    except (OSError, ValueError, UnicodeError, TypeError, KeyError) as exc:
        logger.warning("[retention] Prometheus no disponible: %s", exc)
        result["retention_time"] = None
        result["retention_size"] = None
        result["estado"] = f"no disponible: {exc}"
    return result


def _audit_connection():
    """Reutiliza la conexión y el lock del servicio de auditoría."""
    conn = audit_svc._connect()
    conn.row_factory = sqlite3.Row
    return conn


def audit_inventory() -> dict:
    if not audit_svc.AUDIT_DB.is_file():
        return {"registros": None, "mas_viejo": None, "estado": "sin medir"}
    try:
        with audit_svc._write_lock:
            conn = _audit_connection()
            try:
                row = conn.execute("SELECT COUNT(*) AS total, MIN(ts) AS oldest FROM audit").fetchone()
            finally:
                conn.close()
    except (OSError, sqlite3.Error) as exc:
        logger.warning("[retention] auditoría no disponible: %s", exc)
        return {"registros": None, "mas_viejo": None, "estado": f"no disponible: {exc}"}
    return {"registros": row["total"], "mas_viejo": row["oldest"],
            "estado": "con datos" if row["total"] else "sin medir"}


def backup_inventory() -> dict:
    """Cuenta archivos regulares directos; no sigue symlinks ni crea directorios."""
    folder = settings.BACKUP_DIR
    empty = {"archivos": 0, "uso_bytes": 0, "mas_viejo": None,
             "ruta": None, "estado": "backups no configurados"}
    try:
        if not folder.exists():
            return {**empty, "backups": []}
        if not folder.is_dir() or folder.is_symlink():
            raise RetentionUnavailable("Backups no disponibles: la ruta no es un directorio local")
        files = []
        for path in folder.iterdir():
            if not path.is_symlink() and path.is_file():
                stat = path.stat()
                files.append({"nombre": path.name, "tamano_bytes": stat.st_size,
                              "fecha": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()})
        if not files:
            return {**empty, "backups": []}
        files.sort(key=lambda item: (item["fecha"], item["nombre"]))
        return {"archivos": len(files), "uso_bytes": sum(f["tamano_bytes"] for f in files),
                "mas_viejo": files[0]["fecha"], "ruta": str(folder),
                "estado": "con datos", "backups": files}
    except OSError as exc:
        logger.warning("[retention] backups no disponibles: %s", exc)
        raise RetentionUnavailable(f"Backups no disponibles: {exc}") from exc


def inventory() -> dict:
    backups = backup_inventory()
    return {"prometheus": prometheus_inventory(), "audit": audit_inventory(),
            "backups": {key: value for key, value in backups.items() if key != "backups"}}


def list_backups() -> dict:
    return backup_inventory()


def prune_audit(dias: int, dry_run: bool, username: str) -> dict:
    """El lock cubre lectura/borrado; solo confirma tras poder registrar la acción."""
    if not audit_svc.AUDIT_DB.is_file():
        raise RetentionUnavailable("Auditoría sin medir: base de datos no disponible")
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()
    except OverflowError as exc:
        raise ValueError("dias fuera de rango") from exc
    try:
        with audit_svc._write_lock:
            conn = _audit_connection()
            try:
                if dry_run:
                    count = conn.execute("SELECT COUNT(*) FROM audit WHERE ts < ?", (cutoff,)).fetchone()[0]
                    rows = conn.execute(
                        "SELECT * FROM audit WHERE ts < ? ORDER BY ts ASC, id ASC LIMIT 5", (cutoff,)
                    ).fetchall()
                    return {"borraria": count, "muestras": [audit_svc._row_to_event(r) for r in rows]}
                # Un registro de la propia acción entra en la misma transacción: si falla,
                # SQLite deshace tanto el DELETE como el INSERT.
                with conn:
                    deleted = conn.execute("DELETE FROM audit WHERE ts < ?", (cutoff,)).rowcount
                    kept = conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
                    conn.execute(
                        "INSERT INTO audit (ts, action, username, details, success, path, method, response_status) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (datetime.now(timezone.utc).isoformat(), "audit_prune", username,
                         f"Purga de auditoría: dias={dias}, borrados={deleted}", 1,
                         "/api/retention/audit-prune", "POST", 200),
                    )
                    kept += 1  # incluye el evento de purga, persistido en la misma transacción
                return {"borrados": deleted, "kept": kept}
            finally:
                conn.close()
    except (OSError, sqlite3.Error) as exc:
        logger.warning("[retention] purga de auditoría falló: %s", exc)
        raise RetentionUnavailable(f"Auditoría no disponible: {exc}") from exc
