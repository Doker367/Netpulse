"""NetPulse — Device Inventory Router.

CRUD endpoints para gestionar el inventario de dispositivos.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.middleware.auth import JWTBearer
from app.middleware.rbac import get_current_user, requires_role, requires_scope
from app.models.schemas import DeviceCreate, DeviceResponse, DeviceUpdate
from app.services import device_health as health_svc
from app.services import inventory_svc

router = APIRouter(prefix="/api/devices", tags=["Devices"])


# ── Device Health Check (TCP/ICMP, sin NAPALM) ───────────────
# ⚠ MUST be defined BEFORE /{device_id} to avoid route collision.
# La lógica vive en app/services/device_health.py: TTL por equipo, sondeo
# incremental acotado y refresco de fondo.


@router.get("/health")
async def device_health(
    device_id: Optional[str] = Query(None, description="Filter by device ID"),
    simple: bool = Query(False, description="Return simple boolean format {id: bool}"),
):
    """Quick health check: which devices are online?

    Sonda TCP (o ICMP en equipos SNMP) con TTL por equipo. Respuesta:

    ``{device_id: {"online": bool | null, "latency_ms": float | null,
    "rx": str, "tx": str, "checked_at": ISO-8601 | null, "stale": bool}, ...}``

    - ``online=true``: sondeo dentro del TTL y respondió.
    - ``online=false``: dos fallos consecutivos dentro del TTL.
    - ``online=null``: sin medición fresca (``stale=true``) o no medible
      (DNS, sin permiso ICMP). **Nunca se devuelve verde con datos viejos.**

    Con ``simple=true`` devuelve ``{device_id: bool}``; un equipo desconocido
    se representa como ``false`` (conservador, nunca un falso verde).

    ``rx``/``tx`` son informativos (docker stats) y no afectan al estado.
    This endpoint is **public** (no auth required).
    """
    devices = inventory_svc.list_devices()

    if device_id and isinstance(device_id, str):
        devices = [d for d in devices if d["id"] == device_id]
        if not devices:
            raise HTTPException(404, f"Dispositivo '{device_id}' no encontrado")

    return await health_svc.collect(devices, simple=bool(simple))


# ── CRUD Endpoints ───────────────────────────────────────────


@router.get(
    "",
    response_model=list[DeviceResponse],
    dependencies=[Depends(JWTBearer()), Depends(requires_role("viewer"))],
)
def get_all():
    """Lista todos los dispositivos del inventario."""
    return inventory_svc.list_devices()


@router.get(
    "/{device_id}",
    response_model=DeviceResponse,
    dependencies=[Depends(JWTBearer()), Depends(requires_role("viewer"))],
)
def get_one(device_id: str):
    """Obtiene un dispositivo por ID."""
    d = inventory_svc.get_device(device_id)
    if not d:
        raise HTTPException(404, f"Dispositivo '{device_id}' no encontrado")
    return d


@router.post(
    "",
    response_model=DeviceResponse,
    status_code=201,
    dependencies=[Depends(JWTBearer()), Depends(requires_role("admin"))],
)
def create(device: DeviceCreate, request: Request):
    """Agrega un dispositivo al inventario."""
    requires_scope(group=device.group, request=request)
    try:
        dump = device.model_dump()
        # Convert enums to plain strings for YAML serialization
        if hasattr(dump.get("driver"), "value"):
            dump["driver"] = dump["driver"].value
        if hasattr(dump.get("type"), "value"):
            dump["type"] = dump["type"].value
        return inventory_svc.add_device(dump)
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.put(
    "/{device_id}",
    response_model=DeviceResponse,
    dependencies=[Depends(JWTBearer()), Depends(requires_role("admin"))],
)
def update(device_id: str, updates: DeviceUpdate, request: Request):
    """Actualiza un dispositivo existente; mover de grupo exige ambos scopes."""
    requires_scope(device_id=device_id, request=request)
    if updates.group is not None:
        requires_scope(group=updates.group, request=request)
    upd = updates.model_dump(exclude_none=True)
    if hasattr(upd.get("driver"), "value"):
        upd["driver"] = upd["driver"].value
    if hasattr(upd.get("type"), "value"):
        upd["type"] = upd["type"].value
    result = inventory_svc.update_device(device_id, upd)
    if not result:
        raise HTTPException(404, f"Dispositivo '{device_id}' no encontrado")
    return result


@router.delete(
    "/{device_id}",
    status_code=204,
    dependencies=[Depends(JWTBearer()), Depends(requires_role("admin"))],
)
def delete(device_id: str, request: Request):
    """Elimina un dispositivo del inventario."""
    requires_scope(device_id=device_id, request=request)
    if not inventory_svc.delete_device(device_id):
        raise HTTPException(404, f"Dispositivo '{device_id}' no encontrado")


@router.post(
    "/lab/init",
    response_model=list[DeviceResponse],
    dependencies=[Depends(JWTBearer()), Depends(requires_role("admin"))],
)
def init_lab(request: Request):
    """Inicializa el inventario con los 5 dispositivos del laboratorio."""
    # Este servicio crea equipos en varios grupos: no se puede ejecutar a medias.
    if get_current_user(request).get("scope_group") is not None or get_current_user(request).get("scope_sites") is not None:
        raise HTTPException(403, "Inicialización global del laboratorio requiere acceso sin scope")
    return inventory_svc.add_lab_devices()
