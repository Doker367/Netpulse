"""NetPulse — Notifications / Webhooks Router.

Endpoints para registrar, listar y eliminar webhooks.
Los webhooks reciben notificaciones POST JSON cuando ocurren
eventos como device_down, device_up, backup_complete, etc.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.middleware.auth import JWTBearer
from app.middleware.rbac import requires_role
from app.models.schemas import WebhookRegisterRequest, WebhookResponse
from app.services import notifications_svc

router = APIRouter(
    prefix="/api/notifications",
    tags=["Notifications"],
    dependencies=[Depends(JWTBearer()), Depends(requires_role("admin"))],
)


@router.post("/webhooks", response_model=WebhookResponse)
def register_webhook(body: WebhookRegisterRequest):
    """Registra una nueva URL de webhook para recibir notificaciones.

    Eventos disponibles:
    - device_down: Dispositivo no responde
    - device_up: Dispositivo se recuperó
    - backup_complete: Backup de configuración finalizado
    - compliance_violation: Violación de compliance detectada

    El webhook recibirá un POST JSON con:
    ```json
    {
        "event": "device_down",
        "timestamp": "2025-06-11T18:00:00Z",
        "data": {
            "device_id": "cisco-core-01",
            "status": "down",
            "error": "..."
        }
    }
    ```
    """
    try:
        webhook = notifications_svc.register_webhook(body.url, body.events)
        return WebhookResponse(
            id=webhook["id"],
            url=webhook["url"],
            events=webhook["events"],
            created_at=webhook["created_at"],
            active=webhook["active"],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/webhooks", response_model=list[WebhookResponse])
def list_webhooks():
    """Lista todos los webhooks registrados."""
    webhooks = notifications_svc.list_webhooks()
    return [
        WebhookResponse(
            id=wh["id"],
            url=wh["url"],
            events=wh["events"],
            created_at=wh["created_at"],
            active=wh["active"],
        )
        for wh in webhooks
    ]


@router.delete("/webhooks/{webhook_id}")
def delete_webhook(webhook_id: str):
    """Elimina un webhook registrado por su ID."""
    deleted = notifications_svc.delete_webhook(webhook_id)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"Webhook con ID '{webhook_id}' no encontrado",
        )
    return {"deleted": True, "webhook_id": webhook_id}
