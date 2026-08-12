"""NetPulse — Ventanas de Mantenimiento.

Persistencia JSON en config/maintenance_windows.json.
Todas las fechas se manejan en ISO-8601 con timezone UTC.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.core.settings import CONFIG_DIR

logger = logging.getLogger(__name__)

WINDOWS_FILE = CONFIG_DIR / "maintenance_windows.json"


def _load() -> list[dict]:
    """Carga las ventanas desde el archivo JSON (lista vacía si no existe)."""
    if not WINDOWS_FILE.exists():
        return []
    try:
        with open(WINDOWS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as e:
        logger.error("No se pudo leer %s: %s", WINDOWS_FILE, e)
        return []


def _save(windows: list[dict]) -> None:
    """Persiste la lista de ventanas en el archivo JSON."""
    WINDOWS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(WINDOWS_FILE, "w", encoding="utf-8") as f:
        json.dump(windows, f, indent=2, ensure_ascii=False)


def _parse_iso(value: str) -> datetime:
    """Parsea un string ISO-8601; asume UTC si no trae timezone."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _resolve_now(now_iso: Optional[str]) -> datetime:
    """Devuelve el datetime UTC de 'now_iso' o el momento actual."""
    if now_iso is None:
        return datetime.now(timezone.utc)
    return _parse_iso(now_iso)


def list_windows() -> list:
    """Devuelve todas las ventanas de mantenimiento ordenadas por start."""
    return sorted(_load(), key=lambda w: w.get("start", ""))


def create_window(device_id: str, start_iso: str, end_iso: str, reason: str = "") -> dict:
    """Crea una ventana de mantenimiento.

    Valida que start sea anterior a end y persiste la ventana
    con id (uuid4) y created_at (UTC).
    """
    start = _parse_iso(start_iso)
    end = _parse_iso(end_iso)
    if start >= end:
        raise ValueError("start_iso debe ser anterior a end_iso")

    window = {
        "id": str(uuid.uuid4()),
        "device_id": device_id,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "reason": reason,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    windows = _load()
    windows.append(window)
    _save(windows)
    logger.info("Ventana de mantenimiento creada: %s (%s → %s)",
                window["id"], window["start"], window["end"])
    return window


def delete_window(window_id: str) -> bool:
    """Elimina una ventana por su id. Devuelve True si existía."""
    windows = _load()
    remaining = [w for w in windows if w.get("id") != window_id]
    if len(remaining) == len(windows):
        return False
    _save(remaining)
    logger.info("Ventana de mantenimiento eliminada: %s", window_id)
    return True


def is_in_maintenance(device_id: str, now_iso: Optional[str] = None) -> bool:
    """True si existe una ventana activa (start <= now <= end) para el dispositivo."""
    now = _resolve_now(now_iso)
    for w in _load():
        if w.get("device_id") != device_id:
            continue
        try:
            start = _parse_iso(w["start"])
            end = _parse_iso(w["end"])
        except (KeyError, ValueError):
            continue
        if start <= now <= end:
            return True
    return False


def active_windows(now_iso: Optional[str] = None) -> list:
    """Devuelve las ventanas activas en 'now' (start <= now <= end)."""
    now = _resolve_now(now_iso)
    active = []
    for w in _load():
        try:
            start = _parse_iso(w["start"])
            end = _parse_iso(w["end"])
        except (KeyError, ValueError):
            continue
        if start <= now <= end:
            active.append(w)
    return sorted(active, key=lambda w: w.get("start", ""))
