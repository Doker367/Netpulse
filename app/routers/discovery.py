"""NetPulse — Auto-discovery Router.

Expone el escaneo de vecinos (LLDP/CDP), la lista de candidatos propuestos
y la importación al inventario.

Contrato para la UI: ``docs/handoffs/discovery.md``.
"""

from __future__ import annotations

import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.middleware.auth import JWTBearer
from app.middleware.rbac import get_current_user, requires_role, scope_error
from app.models.schemas import DeviceDriver, DeviceType
from app.services import discovery_svc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/discovery", tags=["Discovery"])


class ScanRequest(BaseModel):
    """Orígenes y protocolo del escaneo."""

    source_ids: Optional[list[str]] = Field(
        default=None,
        description="IDs del inventario a escanear. Si se omite, se escanean todos.",
        examples=[["cisco-core-01", "juniper-edge-01"]],
    )
    metodo: Literal["lldp", "cdp"] = Field(
        default="lldp",
        description="Protocolo de descubrimiento de vecinos.",
    )


class ImportRequest(BaseModel):
    """Candidatos a registrar y datos opcionales para todos ellos."""

    candidates: list[str] = Field(
        ...,
        min_length=1,
        description="IPs de gestión de candidatos del último escaneo.",
        examples=[["10.0.0.2", "10.0.0.3"]],
    )
    username: str = Field(default="", description="Usuario SSH para los equipos importados.")
    password: str = Field(default="", description="Contraseña SSH para los equipos importados.")
    group: Optional[str] = Field(default=None, description="Grupo destino (opcional).")
    tipo: Optional[DeviceType] = Field(
        default=None,
        description="Tipo a asignar a los importados. Si se omite: 'router'.",
    )
    driver: Optional[DeviceDriver] = Field(
        default=None,
        description="Driver manual para todos; sustituye la plataforma sugerida.",
    )

    @field_validator("candidates")
    @classmethod
    def _clean_candidates(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            ip = str(item or "").strip()
            if ip and ip not in cleaned:
                cleaned.append(ip)
        if not cleaned:
            raise ValueError("Debes indicar al menos una IP de candidato.")
        return cleaned


@router.post(
    "/scan",
    dependencies=[Depends(JWTBearer()), Depends(requires_role("viewer"))],
)
def scan(request: ScanRequest):
    """Escanea orígenes y propone vecinos deduplicados por IP de gestión.

    Los fallos de un equipo se devuelven dentro de ``origenes`` con su
    ``estado`` y ``motivo``; no convierten la respuesta en un error global.
    """
    try:
        return discovery_svc.scan(request.source_ids, request.metodo)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — solo fallos globales (inventario)
        logger.exception("[discovery] escaneo falló")
        raise HTTPException(
            503,
            detail={
                "error": "No se pudo completar el escaneo.",
                "motivo": str(exc),
            },
        ) from exc


@router.get(
    "/candidates",
    dependencies=[Depends(JWTBearer()), Depends(requires_role("viewer"))],
)
def candidates():
    """Devuelve el último escaneo guardado en memoria, con su timestamp.

    Si todavía no hay escaneo, responde 200 con ``timestamp: null`` y
    ``candidatos: []`` para que la UI muestre «sin datos» sin tratarlo como
    un error.
    """
    last = discovery_svc.get_last_scan()
    if last is not None:
        return last
    return {
        "timestamp": None,
        "metodo": None,
        "origenes": [],
        "candidatos": [],
        "resumen": {
            "origenes": 0,
            "origenes_ok": 0,
            "origenes_con_error": 0,
            "vecinos": 0,
            "candidatos": 0,
            "omitidos": 0,
        },
        "mensaje": "Sin escaneo registrado. Ejecuta POST /api/discovery/scan.",
    }


@router.post(
    "/import",
    dependencies=[Depends(JWTBearer()), Depends(requires_role("operator"))],
)
def import_candidates(request: ImportRequest, http_request: Request):
    """Registra candidatos del último escaneo mediante ``inventory_svc.add_device``.

    Es la misma función de registro que usa ``POST /api/devices``. El
    resultado separa ``creados``, ``ya_existian``, ``fallidos`` (con motivo) y
    ``rechazados`` (fuera de scope, con el ``device_id`` que se habría creado).

    El grupo destino es único para todo el lote (``body.group``); si se omite,
    el equipo se crearía sin grupo y un token restringido no puede operarlo.
    Los rechazados se informan por dispositivo; solo se responde 403 cuando
    **todos** los candidatos quedan fuera de scope (sin altas ni existentes).
    """
    user = get_current_user(http_request)
    result = discovery_svc.import_candidates(
        request.candidates,
        username=request.username,
        password=request.password,
        group=request.group,
        tipo=request.tipo,
        driver=request.driver,
        authorize=lambda group: scope_error(user, group),
    )
    rejected = result.get("rechazados") or []
    if (
        rejected
        and not result["creados"]
        and not result["ya_existian"]
        and not result["fallidos"]
    ):
        raise HTTPException(403, rejected[0]["error"])
    return result
