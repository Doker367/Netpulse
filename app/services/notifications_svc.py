"""NetPulse — Notifications / Webhooks Service.

Gestión de webhooks registrados y envío de notificaciones
a URLs externas vía POST JSON.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from app.core.settings import CONFIG_DIR

logger = logging.getLogger(__name__)

WEBHOOKS_FILE = CONFIG_DIR / "webhooks.json"


# ── Persistencia ─────────────────────────────────────────────


def _ensure_file():
    """Crea config/webhooks.json si no existe."""
    WEBHOOKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not WEBHOOKS_FILE.exists():
        WEBHOOKS_FILE.write_text(json.dumps({"webhooks": []}, indent=2), encoding="utf-8")


def _read_webhooks() -> list[dict]:
    """Lee todos los webhooks registrados desde disco."""
    _ensure_file()
    try:
        data = json.loads(WEBHOOKS_FILE.read_text(encoding="utf-8"))
        return data.get("webhooks", [])
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def _write_webhooks(webhooks: list[dict]):
    """Persiste la lista de webhooks en disco."""
    _ensure_file()
    WEBHOOKS_FILE.write_text(
        json.dumps({"webhooks": webhooks}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ── CRUD Webhooks ────────────────────────────────────────────


def register_webhook(url: str, events: list[str]) -> dict:
    """Registra una nueva URL de webhook.

    Args:
        url: URL completa del webhook (https://...).
        events: Lista de eventos a los que suscribirse
                (device_down, device_up, backup_complete, compliance_violation).

    Returns:
        Dict con los datos del webhook registrado.
    """
    webhooks = _read_webhooks()

    # Validar que no exista la misma URL
    for wh in webhooks:
        if wh["url"] == url:
            raise ValueError(f"Webhook URL ya registrada: {url}")

    webhook = {
        "id": str(uuid.uuid4()),
        "url": url,
        "events": events,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "active": True,
    }
    webhooks.append(webhook)
    _write_webhooks(webhooks)
    logger.info("Webhook registrado: %s (eventos: %s)", url, events)
    return webhook


def list_webhooks() -> list[dict]:
    """Lista todos los webhooks registrados."""
    return _read_webhooks()


def delete_webhook(webhook_id: str) -> bool:
    """Elimina un webhook por su ID.

    Returns:
        True si se eliminó, False si no se encontró.
    """
    webhooks = _read_webhooks()
    filtered = [wh for wh in webhooks if wh["id"] != webhook_id]
    if len(filtered) == len(webhooks):
        return False
    _write_webhooks(filtered)
    logger.info("Webhook eliminado: %s", webhook_id)
    return True


# ── Envío de Notificaciones ──────────────────────────────────


def _get_subscribed_webhooks(event_type: str) -> list[dict]:
    """Obtiene los webhooks suscritos a un tipo de evento específico."""
    return [
        wh
        for wh in _read_webhooks()
        if wh.get("active", True) and event_type in wh.get("events", [])
    ]


def send_webhook(url: str, event_type: str, data: dict) -> bool:
    """Envía una notificación a una URL de webhook vía POST JSON.

    Args:
        url: URL del webhook.
        event_type: Tipo de evento.
        data: Payload con datos del evento.

    Returns:
        True si se envió correctamente, False en caso contrario.
    """
    payload = {
        "event": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
        logger.info("Webhook enviado a %s (evento: %s) — status=%d", url, event_type, response.status_code)
        return True
    except httpx.HTTPError as e:
        logger.warning("Error enviando webhook a %s: %s", url, e)
        return False
    except Exception as e:
        logger.error("Error inesperado enviando webhook a %s: %s", url, e)
        return False


def notify_event(event_type: str, data: dict):
    """Envía notificaciones a todos los webhooks suscritos a un evento.

    Args:
        event_type: Tipo de evento (device_down, device_up, etc.).
        data: Payload del evento.
    """
    webhooks = _get_subscribed_webhooks(event_type)
    for wh in webhooks:
        send_webhook(wh["url"], event_type, data)


# ── Eventos específicos ──────────────────────────────────────


def notify_device_down(device_id: str, error: Optional[str] = None):
    """Notifica que un dispositivo está caído."""
    data = {
        "device_id": device_id,
        "status": "down",
        "error": error,
    }
    logger.warning("Dispositivo DOWN: %s — %s", device_id, error or "Sin respuesta")
    notify_event("device_down", data)


def notify_device_up(device_id: str):
    """Notifica que un dispositivo se recuperó."""
    data = {
        "device_id": device_id,
        "status": "up",
    }
    logger.info("Dispositivo UP: %s", device_id)
    notify_event("device_up", data)


def notify_backup_complete(device_id: str, file_path: Optional[str] = None, size: Optional[int] = None):
    """Notifica que el backup de un dispositivo se completó."""
    data = {
        "device_id": device_id,
        "operation": "backup",
        "file": file_path,
        "size_bytes": size,
    }
    logger.info("Backup completado: %s — %s (%d bytes)", device_id, file_path or "?", size or 0)
    notify_event("backup_complete", data)


def notify_compliance_violation(device_id: str, violations: list[dict]):
    """Notifica que un dispositivo tiene violaciones de compliance."""
    data = {
        "device_id": device_id,
        "operation": "compliance_check",
        "compliant": False,
        "violations_count": len(violations),
        "violations": violations[:10],  # primeras 10 para no saturar
    }
    logger.warning("Violación de compliance: %s (%d violaciones)", device_id, len(violations))
    notify_event("compliance_violation", data)
