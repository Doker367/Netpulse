"""NetPulse — Alertmanager Admin Router (silencios y reglas).

Administración de alertas sobre el Alertmanager desplegado:

- ``GET    /api/alerts/admin/silences``       → silencios activos
- ``POST   /api/alerts/admin/silences``       → crear silencio
- ``DELETE /api/alerts/admin/silences/{id}``  → cancelar silencio
- ``GET    /api/alerts/admin/rules``          → reglas de alerta (solo lectura)

Es ADITIVO a ``app/routers/alerts.py`` (webhook + alertas activas): el prefijo
``/admin`` evita el choque de rutas y no se toca ninguno de sus endpoints.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.middleware.auth import JWTBearer
from app.middleware.rbac import get_current_user, requires_role
from app.services import alerts_admin_svc, audit_svc
from app.services.alerts_admin_svc import (
    DURATION_SECONDS,
    UpstreamNotFound,
    UpstreamUnavailable,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/alerts/admin", tags=["Alerts Admin"])

AUTH_VIEWER = [Depends(JWTBearer()), Depends(requires_role("viewer"))]
AUTH_OPERATOR = [Depends(JWTBearer()), Depends(requires_role("operator"))]


class SilenceMatcher(BaseModel):
    """Un matcher de silencio (se manda a Alertmanager tal cual)."""

    name: str = Field(..., description="Etiqueta a silenciar (ej. 'device_id', 'group')")
    value: str = Field(..., description="Valor de la etiqueta")
    isRegex: bool = Field(default=False, description="Si el valor es una regex")


class SilenceCreate(BaseModel):
    """Petición de creación de silencio.

    Acepta dos formas excluyentes de indicar el alcance:

    - ``matchers`` explícitos, o
    - el simplificado ``device_id`` / ``group`` (se traducen a matchers).
    """

    matchers: Optional[list[SilenceMatcher]] = Field(
        default=None,
        description="Matchers explícitos (alternativa a device_id/group)",
    )
    device_id: Optional[str] = Field(
        default=None,
        description="Silencia un dispositivo concreto (equivale a matcher name='device_id')",
    )
    group: Optional[str] = Field(
        default=None,
        description="Silencia un grupo (equivale a matcher name='group')",
    )
    duration: str = Field(
        ...,
        description="Duración del silencio: '2h', '8h', '24h' o '7d'",
    )
    comment: Optional[str] = Field(
        default=None,
        description="Motivo del silencio (recomendado)",
    )


def _build_matchers(body: SilenceCreate) -> list[dict]:
    """Convierte la petición en la lista de matchers que se envía a Alertmanager."""
    if body.matchers:
        return [
            {"name": m.name, "value": m.value, "isRegex": m.isRegex}
            for m in body.matchers
        ]
    matchers: list[dict] = []
    if body.device_id:
        matchers.append({"name": "device_id", "value": body.device_id, "isRegex": False})
    if body.group:
        matchers.append({"name": "group", "value": body.group, "isRegex": False})
    return matchers


@router.get("/silences", dependencies=AUTH_VIEWER)
def list_silences():
    """Lista los silencios ACTIVOS en este momento."""
    try:
        silences = alerts_admin_svc.list_active_silences()
    except UpstreamUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Alertmanager no disponible: {exc.reason}",
        ) from exc
    return {"status": "success", "total": len(silences), "silences": silences}


@router.post("/silences", status_code=201, dependencies=AUTH_OPERATOR)
def create_silence(body: SilenceCreate, request: Request):
    """Crea un silencio en Alertmanager (startsAt=ahora, endsAt=ahora+duración)."""
    matchers = _build_matchers(body)
    if not matchers:
        raise HTTPException(
            status_code=422,
            detail="Al menos un matcher es requerido (usa matchers, device_id o group)",
        )
    if body.duration not in DURATION_SECONDS:
        allowed = ", ".join(DURATION_SECONDS)
        raise HTTPException(
            status_code=422,
            detail=f"Duración inválida: usa una de {allowed}",
        )

    user = get_current_user(request)
    username = user.get("sub", "anonymous")

    try:
        result = alerts_admin_svc.create_silence(
            matchers=matchers,
            duration=body.duration,
            comment=body.comment,
            created_by=username,
        )
    except UpstreamUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Alertmanager no disponible: {exc.reason}",
        ) from exc

    audit_svc.log_event(
        action="silence_create",
        username=username,
        details=(
            f"Silencio {result['id']} ({body.duration}) "
            f"matchers={matchers} comment={body.comment or ''}"
        ),
        ip_address=request.client.host if request.client else None,
        path="/api/alerts/admin/silences",
        method="POST",
    )
    return {"status": "created", **result}


@router.delete("/silences/{silence_id}", dependencies=AUTH_OPERATOR)
def delete_silence(silence_id: str, request: Request):
    """Cancela (expira) un silencio vía la API v2 de Alertmanager."""
    user = get_current_user(request)
    username = user.get("sub", "anonymous")

    try:
        alerts_admin_svc.delete_silence(silence_id)
    except UpstreamNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Silencio no encontrado: {exc.reason}",
        ) from exc
    except UpstreamUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Alertmanager no disponible: {exc.reason}",
        ) from exc

    audit_svc.log_event(
        action="silence_delete",
        username=username,
        details=f"Silencio {silence_id} cancelado",
        ip_address=request.client.host if request.client else None,
        path=f"/api/alerts/admin/silences/{silence_id}",
        method="DELETE",
    )
    return {"status": "deleted", "id": silence_id}


@router.get("/rules", dependencies=AUTH_VIEWER)
def list_rules():
    """Reglas de alerta actuales (solo lectura, desde Prometheus)."""
    try:
        rules = alerts_admin_svc.list_rules()
    except UpstreamUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Prometheus no disponible: {exc.reason}",
        ) from exc
    return {"status": "success", "total": len(rules), "rules": rules}
