"""NetPulse — Command Execution Router.

Endpoints para ejecutar comandos CLI en dispositivos de red.
Incluye filtros de seguridad y auditoría de todos los comandos.

POST /api/command/{device_id}  — Ejecutar comandos en un dispositivo
POST /api/command/bulk          — Ejecutar comandos en múltiples dispositivos
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from app.middleware.auth import JWTBearer
from app.middleware.rbac import get_current_user, requires_role
from app.models.schemas import (
    CommandRequest,
    CommandResponse,
    CommandOutput,
    BulkCommandRequest,
    BulkCommandResponse,
)
from app.services import napalm_svc, audit_svc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/command", tags=["Command Execution"])


def _build_response(device_id: str, result: napalm_svc.NapalmResult) -> CommandResponse:
    """Construye una CommandResponse a partir de un NapalmResult."""
    results_data = result.data or []
    if not isinstance(results_data, list):
        results_data = []

    outputs = [
        CommandOutput(
            command=r.get("command", "unknown"),
            output=r.get("output", ""),
            error=r.get("error"),
        )
        for r in results_data
    ]

    return CommandResponse(
        device_id=device_id,
        results=outputs,
        success=result.success,
        timestamp=result.timestamp,
    )


@router.post(
    "/{device_id}",
    response_model=CommandResponse,
    dependencies=[Depends(JWTBearer()), Depends(requires_role("operator"))],
)
def execute_commands(device_id: str, body: CommandRequest, request: Request):
    """Ejecuta comandos CLI en un dispositivo específico.

    Solo se permiten comandos de lectura/diagnóstico
    (show, display, get, ping, traceroute, etc.).

    Requiere rol: operator+
    """
    user = get_current_user(request)
    username = user.get("sub", "anonymous")

    # Log audit entry for every command execution
    audit_svc.log_event(
        action="command_execute",
        device_id=device_id,
        username=username,
        details=f"Comandos: {', '.join(body.commands[:5])}",
        ip_address=request.client.host if request.client else None,
        path=f"/api/command/{device_id}",
        method="POST",
    )

    result = napalm_svc.run_commands(device_id, body.commands)

    if not result.success:
        raise HTTPException(
            status_code=502,
            detail={
                "error": result.error,
                "error_raw": result.error_raw,
                "error_type": result.error_type or "unknown",
                "device_id": device_id,
            },
        )

    return _build_response(device_id, result)


@router.post(
    "/bulk",
    response_model=BulkCommandResponse,
    dependencies=[Depends(JWTBearer()), Depends(requires_role("operator"))],
)
def execute_commands_bulk(body: BulkCommandRequest, request: Request):
    """Ejecuta comandos CLI en múltiples dispositivos simultáneamente.

    Los mismos comandos se ejecutan en todos los dispositivos especificados.
    Cada dispositivo se procesa de forma independiente.

    Requiere rol: operator+
    """
    user = get_current_user(request)
    username = user.get("sub", "anonymous")

    # Log audit entry for bulk execution
    audit_svc.log_event(
        action="command_bulk",
        username=username,
        details=f"Dispositivos: {', '.join(body.device_ids[:10])} | "
                f"Comandos: {', '.join(body.commands[:5])}",
        ip_address=request.client.host if request.client else None,
        path="/api/command/bulk",
        method="POST",
    )

    results: list[CommandResponse] = []
    errors: list[dict] = []

    for device_id in body.device_ids:
        result = napalm_svc.run_commands(device_id, body.commands)

        if result.success:
            results.append(_build_response(device_id, result))
        else:
            errors.append({
                "device_id": device_id,
                "error": result.error,
                "error_raw": result.error_raw,
                "error_type": result.error_type or "unknown",
            })

    return BulkCommandResponse(
        total=len(body.device_ids),
        success_count=len(results),
        failed_count=len(errors),
        results=results,
        errors=errors,
    )
