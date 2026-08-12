"""NetPulse — Alertmanager Webhook y Alertas Activas.

Recibe alertas de Alertmanager (Prometheus) y las reenvía a los
webhooks registrados en notifications_svc. También expone las
alertas activas actuales para el dashboard.
"""

import json
import logging
import urllib.request

from fastapi import APIRouter, Depends, Request

from app.middleware.auth import JWTBearer
from app.services import notifications_svc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/alerts", tags=["Alerts"])


@router.get("/active", dependencies=[Depends(JWTBearer())])
def active_alerts():
    """Lista las alertas activas desde Prometheus /api/v1/alerts."""
    try:
        with urllib.request.urlopen("http://localhost:9090/api/v1/alerts", timeout=8) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        logger.warning("[alerts] Prometheus no disponible: %s", e)
        return {"status": "error", "detail": f"Prometheus: {e}", "alerts": []}

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
        })

    firing = [a for a in alerts if a["state"] == "firing"]
    return {
        "status": "success",
        "total": len(alerts),
        "firing": len(firing),
        "alerts": alerts,
    }


@router.post("/webhook", dependencies=[Depends(JWTBearer())])
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

    # Cargar ventanas de mantenimiento una sola vez
    from app.services.ops import maintenance_svc
    active_windows = maintenance_svc.active_windows()
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
            notifications_svc.notify_event(
                event_type=event,
                data={"title": title, "message": message, "labels": labels},
            )
            delivered += 1
        except Exception as e:
            logger.error("[alerts] Fallo al notificar: %s", e)

    logger.info("[alerts] Recibidas %d, entregadas %d, suprimidas %d (mantenimiento)",
                len(alerts), delivered, suppressed)
    return {"status": "ok", "received": len(alerts), "delivered": delivered, "suppressed": suppressed}
