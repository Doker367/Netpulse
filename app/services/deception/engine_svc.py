"""NetPulse — Motor nativo de la red señuelo.

Supervisa el ciclo de vida de los listeners nativos: recorre la
topología declarada, instancia un listener por servicio de cada
dispositivo habilitado y los levanta en su IP/puerto. Expone
``start``/``stop``/``status`` para la API y el lifespan de la app.

En fases posteriores este supervisor podrá delegar el data plane al
engine en Go sin cambiar el contrato (``telemetry_svc``/``ingest``).
"""

import asyncio
import logging
from typing import Any, Optional

from app.core.settings import DECEPTION_BIND_HOST, DECEPTION_ENABLED
from app.services.deception import topology_svc
from app.services.deception.listeners import build_listener

logger = logging.getLogger(__name__)

_lock = asyncio.Lock()
_entries: list[dict[str, Any]] = []
_running = False
_last_error: Optional[str] = None


def is_running() -> bool:
    """True si el motor tiene listeners activos."""
    return _running


async def start() -> dict:
    """Levanta los listeners de todos los dispositivos habilitados.

    Returns:
        Reporte con ``started``, ``failed`` y ``skipped``.
    """
    global _running, _last_error

    if not DECEPTION_ENABLED:
        return {"status": "disabled", "started": 0, "failed": 0, "skipped": 0}

    async with _lock:
        if _running:
            return {"status": "already_running", "started": 0, "failed": 0, "skipped": 0}

        started, failed, skipped = [], [], []
        for device in topology_svc.list_devices():
            if not device.get("enabled", True):
                skipped.append(device["id"])
                continue
            host = DECEPTION_BIND_HOST or device["ip"]
            for service in device.get("services", []):
                listener = build_listener(
                    device_id=device["id"],
                    role=device["role"],
                    host=host,
                    port=int(service["port"]),
                    proto=service.get("proto", ""),
                    banner=service.get("banner"),
                )
                if listener is None:
                    skipped.append(f"{device['id']}:{service.get('proto')}")
                    continue
                # Vendor de la persona → respuestas/banners coherentes.
                listener.vendor = (device.get("persona") or {}).get("vendor")
                try:
                    await listener.start()
                    _entries.append({
                        "device_id": device["id"],
                        "role": device["role"],
                        "proto": listener.proto,
                        "host": host,
                        "port": listener.port,
                        "listener": listener,
                    })
                    started.append(f"{device['id']}:{listener.proto}/{listener.port}")
                except Exception as exc:  # noqa: BLE001
                    _last_error = str(exc)
                    failed.append(f"{device['id']}:{service.get('proto')} ({exc})")
                    logger.warning("[deception] no se pudo levantar %s:%s/%s — %s",
                                   device["id"], host, service.get("port"), exc)

        _running = bool(_entries)
        _update_metrics()
        logger.info("[deception] motor iniciado: %d listeners, %d fallos",
                    len(_entries), len(failed))
        return {
            "status": "started" if _running else "failed",
            "started": len(started),
            "failed": len(failed),
            "skipped": len(skipped),
            "detail": {"started": started, "failed": failed, "skipped": skipped},
        }


async def stop() -> dict:
    """Detiene todos los listeners activos."""
    global _running

    async with _lock:
        count = len(_entries)
        for entry in _entries:
            try:
                await entry["listener"].stop()
            except Exception as exc:  # noqa: BLE001
                logger.debug("[deception] error deteniendo %s: %s", entry["device_id"], exc)
        _entries.clear()
        _running = False
        _update_metrics()
        logger.info("[deception] motor detenido (%d listeners)", count)
        return {"status": "stopped", "stopped": count}


def status() -> dict:
    """Estado actual del motor y sus listeners."""
    return {
        "enabled": DECEPTION_ENABLED,
        "running": _running,
        "listeners": [
            {"device_id": e["device_id"], "role": e["role"], "proto": e["proto"],
             "host": e["host"], "port": e["port"]}
            for e in _entries
        ],
        "count": len(_entries),
        "last_error": _last_error,
    }


def _update_metrics() -> None:
    try:
        from app.services import metrics_svc

        metrics_svc.netpulse_deception_devices_configured.set(
            topology_svc.summary()["devices"]
        )
        metrics_svc.netpulse_deception_devices_enabled.set(
            len({e["device_id"] for e in _entries})
        )
    except Exception:  # noqa: BLE001
        pass
