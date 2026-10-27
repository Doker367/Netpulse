"""NetPulse — Alertmanager Webhook y Alertas Activas.

Recibe alertas de Alertmanager (Prometheus) y las reenvía a los
webhooks registrados en notifications_svc. También expone las
alertas activas actuales para el dashboard.

**I/O fuera del event loop** (tarjeta 13C): el webhook es ``async`` y antes
leía las ventanas de mantenimiento (JSON en disco) y notificaba (webhooks
HTTP, Telegram, Slack, SMTP) en línea. Ambas cosas se ejecutan ahora con
``run_blocking`` (``asyncio.to_thread`` + tope de tiempo): un SMTP lento ya no
congela el loop, y un fallo/timeout de notificación no cambia la respuesta.
"""

import hmac
import json
import logging
import urllib.request
from typing import Optional

from fastapi import APIRouter, Depends, Header, Request

from app.core.settings import ALERTMANAGER_TOKEN
from app.middleware.auth import JWTBearer
from app.middleware.rbac import requires_role
from app.services import notifications_svc
from app.utils.async_io import BlockingCallTimeout, env_timeout, run_blocking

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/alerts", tags=["Alerts"])

#: Tope para leer las ventanas de mantenimiento (JSON local, milisegundos).
_WINDOWS_TIMEOUT_ENV = "NETPULSE_ALERT_WINDOWS_TIMEOUT_S"
_WINDOWS_TIMEOUT_S = 5.0

#: Tope para UNA notificación (varios webhooks + canales externos, cada uno
#: con su propio timeout de red). Si vence, se registra y se sigue: la
#: respuesta al webhook no depende de la entrega.
_NOTIFY_TIMEOUT_ENV = "NETPULSE_ALERT_NOTIFY_TIMEOUT_S"
_NOTIFY_TIMEOUT_S = 60.0


async def alertmanager_auth(
    request: Request,
    x_alertmanager_token: Optional[str] = Header(default=None),
) -> dict:
    """Autentica el webhook de Alertmanager.

    Alertmanager no envía JWT de usuario: se acepta un token de servicio en la
    cabecera ``X-Alertmanager-Token`` (o ``Authorization: Bearer <token>``).
    Si no hay token configurado, se exige JWT de admin (comportamiento previo).
    """
    supplied = x_alertmanager_token or ""
    if not supplied:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
    if ALERTMANAGER_TOKEN and supplied and hmac.compare_digest(supplied, ALERTMANAGER_TOKEN):
        return {"sub": "alertmanager", "role": "service"}
    # Sin token o no coincide → exigir JWT admin.
    await JWTBearer()(request)
    await requires_role("admin")(request)
    return {"sub": "admin", "role": "admin"}


def _watchdog_alerts() -> list[dict]:
    """Alertas de site del watchdog interno (nunca deben romper la respuesta)."""
    try:
        from app.services.remote import watchdog_svc

        return watchdog_svc.active_alerts()
    except Exception as e:  # noqa: BLE001
        logger.warning("[alerts] watchdog de sites no disponible: %s", e)
        return []


@router.get("/active", dependencies=[Depends(JWTBearer())])
def active_alerts():
    """Lista las alertas activas: Prometheus + watchdog de sites remotos.

    Las de Prometheus se leen de ``/api/v1/alerts``. Las de site las produce
    ``watchdog_svc`` y se incluyen con ``source="watchdog"``. Si Prometheus no
    responde pero hay alertas de site, se devuelve ``status="success"`` con
    ``prometheus_available=false``: el operador ve lo que sí se sabe, sin
    ocultar una alerta crítica porque el stack de métricas esté caído.
    """
    internal = _watchdog_alerts()
    try:
        with urllib.request.urlopen("http://localhost:9090/api/v1/alerts", timeout=8) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        logger.warning("[alerts] Prometheus no disponible: %s", e)
        if internal:
            return {
                "status": "success",
                "total": len(internal),
                "firing": len(internal),
                "alerts": internal,
                "prometheus_available": False,
                "prometheus_error": f"Prometheus: {e}",
                "sources": ["watchdog"],
            }
        return {
            "status": "error",
            "detail": f"Prometheus: {e}",
            "alerts": [],
            "prometheus_available": False,
        }

    alerts = []
    for a in data.get("data", {}).get("alerts", []):
        labels = a.get("labels", {})
        annotations = a.get("annotations", {})
        alerts.append({
            "name": labels.get("alertname", "?"),
            "state": a.get("state", "?"),
            "severity": labels.get("severity", "warning"),
            "device_id": labels.get("device_id", ""),
            "summary": annotations.get("summary", ""),
            "description": annotations.get("description", ""),
            "active_at": a.get("activeAt", ""),
            "source": "prometheus",
        })

    alerts.extend(internal)
    firing = [a for a in alerts if a["state"] == "firing"]
    sources = ["prometheus"] + (["watchdog"] if internal else [])
    return {
        "status": "success",
        "total": len(alerts),
        "firing": len(firing),
        "alerts": alerts,
        "prometheus_available": True,
        "sources": sources,
    }


@router.post("/webhook", dependencies=[Depends(alertmanager_auth)])
async def alertmanager_webhook(request: Request):
    """Recibe un payload de Alertmanager y lo distribuye.

    Formato: https://prometheus.io/docs/alerting/latest/webhooks/
    """
    try:
        payload = await request.json()
    except Exception:
        return {"status": "error", "detail": "JSON inválido"}

    alerts = payload.get("alerts", [])
    delivered = 0
    suppressed = 0

    # Cargar ventanas de mantenimiento una sola vez (fuera del loop). Si el
    # disco falla o tarda, se degrada a "sin ventanas": la alerta se entrega
    # (mejor duplicar que silenciar) y se registra el motivo.
    from app.services.ops import maintenance_svc
    try:
        active_windows = await run_blocking(
            maintenance_svc.active_windows,
            timeout=env_timeout(_WINDOWS_TIMEOUT_ENV, _WINDOWS_TIMEOUT_S),
            label="leer ventanas de mantenimiento",
        )
    except BlockingCallTimeout as exc:
        logger.warning("[alerts] ventanas de mantenimiento no leídas a tiempo: %s", exc)
        active_windows = []
    except Exception as e:  # noqa: BLE001 — nunca romper el webhook por esto
        logger.warning("[alerts] ventanas de mantenimiento no disponibles: %s", e)
        active_windows = []

    suppressed_ids = {w["device_id"] for w in active_windows}

    for alert in alerts:
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})
        status = alert.get("status", "firing")

        device_id = labels.get("device_id", "")

        # Suprimir si el dispositivo está en mantenimiento
        if device_id and device_id in suppressed_ids:
            suppressed += 1
            logger.info("[alerts] Suprimida alerta de %s (ventana de mantenimiento)", device_id)
            continue

        title = annotations.get("summary") or labels.get("alertname", "Alerta NetPulse")
        message = annotations.get("description") or title
        severity = labels.get("severity", "warning")
        event = f"alert_{severity}_{status}"

        try:
            await run_blocking(
                notifications_svc.notify_event,
                event,
                {"title": title, "message": message, "labels": labels},
                timeout=env_timeout(_NOTIFY_TIMEOUT_ENV, _NOTIFY_TIMEOUT_S),
                label=f"notificar {event}",
            )
            delivered += 1
        except BlockingCallTimeout as exc:
            logger.error("[alerts] Notificación %s no terminó a tiempo: %s", event, exc)
        except Exception as e:  # noqa: BLE001 — un canal caído no rompe el webhook
            logger.error("[alerts] Fallo al notificar: %s", e)

    logger.info("[alerts] Recibidas %d, entregadas %d, suprimidas %d (mantenimiento)",
                len(alerts), delivered, suppressed)
    return {"status": "ok", "received": len(alerts), "delivered": delivered, "suppressed": suppressed}
