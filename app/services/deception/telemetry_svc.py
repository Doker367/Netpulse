"""NetPulse — Deception Telemetry.

Punto único por el que pasan todos los eventos de la red señuelo antes
de persistirse y observarse:

1. ``store_svc``  — SQLite append-only (fuente de verdad del dashboard).
2. Prometheus     — ``netpulse_deception_events_total``.
3. Loki           — vía el mismo mecanismo que el colector de syslog.

El envío a Loki es best-effort y no bloquea el event loop (se ejecuta en
un thread aparte con timeout corto).
"""

import asyncio
import logging
from typing import Optional

from app.models.deception import DeceptionEventIn, DeceptionEventType
from app.services import metrics_svc
from app.services.deception import store_svc

logger = logging.getLogger(__name__)

# Mensajes de consola/log por tipo (para Loki)
_SEVERITY = {
    DeceptionEventType.LOGIN_SUCCESS: "warning",
    DeceptionEventType.COMMAND: "warning",
    DeceptionEventType.FILE_DOWNLOAD: "error",
}


def _coerce_type(value) -> DeceptionEventType:
    if isinstance(value, DeceptionEventType):
        return value
    try:
        return DeceptionEventType(str(value))
    except ValueError:
        return DeceptionEventType.OTHER


def _push_loki(device_id: str, src_ip: str, proto: str, etype: str, message: str) -> None:
    """Reenvía el evento a Loki (bloqueante; se llama desde un thread)."""
    try:
        from app.services.collectors import syslog_svc

        stream = {
            "container": "deception",
            "device": device_id,
            "proto": proto or "",
            "event": etype,
            "severity": _SEVERITY.get(_coerce_type(etype), "info"),
        }
        syslog_svc.push_to_loki(stream, f"[{src_ip}] {message}")
    except Exception as exc:  # noqa: BLE001
        logger.debug("[deception] push a Loki falló: %s", exc)


async def emit(
    device_id: str,
    src_ip: str,
    proto: str,
    event_type,
    detail: Optional[dict] = None,
    username: Optional[str] = None,
    success: bool = False,
    src_port: Optional[int] = None,
    ts: Optional[str] = None,
) -> dict:
    """Registra un evento de deception en store + Prometheus + Loki.

    Returns:
        El evento almacenado (dict) como lo devuelve ``store_svc``.
    """
    record = _store(
        device_id, src_ip, proto, event_type, detail, username,
        success, src_port, ts,
    )
    etype = record["type"]
    message = f"{etype} {username or ''} {detail or ''}".strip()
    try:
        await asyncio.to_thread(_push_loki, device_id, src_ip, proto, etype, message)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[deception] telemetría Loki diferida falló: %s", exc)
    return record


def emit_sync(
    device_id: str,
    src_ip: str,
    proto: str,
    event_type,
    detail: Optional[dict] = None,
    username: Optional[str] = None,
    success: bool = False,
    src_port: Optional[int] = None,
    ts: Optional[str] = None,
) -> dict:
    """Versión síncrona de ``emit`` (para hilos, ej. listener SSH).

    El push a Loki se hace de forma bloqueante aquí; es aceptable porque
    el llamante ya corre en un thread dedicado.
    """
    record = _store(
        device_id, src_ip, proto, event_type, detail, username,
        success, src_port, ts,
    )
    etype = record["type"]
    message = f"{etype} {username or ''} {detail or ''}".strip()
    _push_loki(device_id, src_ip, proto, etype, message)
    return record


def _store(
    device_id: str,
    src_ip: str,
    proto: str,
    event_type,
    detail: Optional[dict],
    username: Optional[str],
    success: bool,
    src_port: Optional[int],
    ts: Optional[str],
) -> dict:
    """Persiste el evento y actualiza las métricas de Prometheus."""
    etype = _coerce_type(event_type)
    event = DeceptionEventIn(
        device_id=device_id,
        src_ip=src_ip,
        src_port=src_port,
        proto=proto or "tcp",
        type=etype,
        detail=detail or {},
        username=username,
        success=success,
        ts=ts,
    )
    record = store_svc.log_event(event)
    try:
        metrics_svc.netpulse_deception_events_total.labels(
            device_id=device_id, proto=proto or "", event_type=etype.value
        ).inc()
    except Exception:  # noqa: BLE001
        pass

    # Alertas (asíncronas, no bloquean al listener)
    try:
        from app.services.deception import alert_svc
        alert_svc.submit(record)
    except Exception:  # noqa: BLE001
        pass

    return record
