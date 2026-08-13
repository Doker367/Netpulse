"""NetPulse — Device Inventory Router.

CRUD endpoints para gestionar el inventario de dispositivos.
"""

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.middleware.auth import JWTBearer
from app.middleware.rbac import requires_role
from app.models.schemas import DeviceCreate, DeviceResponse, DeviceUpdate
from app.services import inventory_svc

router = APIRouter(prefix="/api/devices", tags=["Devices"])


# ── Device Health Check (fast TCP probe, no NAPALM) ──────────
# ⚠ MUST be defined BEFORE /{device_id} to avoid route collision.


@router.get("/health")
async def device_health(
    device_id: Optional[str] = Query(None, description="Filter by device ID"),
):
    """Quick health check: which devices are online?

    Uses a fast TCP connect (2 s timeout) instead of full NAPALM operations.
    Returns ``{device_id: online_bool, ...}`` for all devices,
    or a single device if ``?device_id=`` is provided.

    This endpoint is **public** (no auth required).
    """
    devices = inventory_svc.list_devices()

    if device_id:
        devices = [d for d in devices if d["id"] == device_id]
        if not devices:
            raise HTTPException(404, f"Dispositivo '{device_id}' no encontrado")

    # Run all TCP checks concurrently
    results = {}
    tasks = {
        d["id"]: _tcp_check(d["hostname"], d["port"], timeout=4.0)
        for d in devices
    }

    for dev_id, coro in tasks.items():
        results[dev_id] = await coro

    return results


async def _tcp_check(host: str, port: int, timeout: float = 4.0) -> bool:
    """Fast TCP-connect probe. Returns True if port is reachable."""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (asyncio.TimeoutError, OSError, ConnectionRefusedError):
        return False


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
def create(device: DeviceCreate):
    """Agrega un dispositivo al inventario."""
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
def update(device_id: str, updates: DeviceUpdate):
    """Actualiza un dispositivo existente."""
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
def delete(device_id: str):
    """Elimina un dispositivo del inventario."""
    if not inventory_svc.delete_device(device_id):
        raise HTTPException(404, f"Dispositivo '{device_id}' no encontrado")


@router.post(
    "/lab/init",
    response_model=list[DeviceResponse],
    dependencies=[Depends(JWTBearer()), Depends(requires_role("admin"))],
)
def init_lab():
    """Inicializa el inventario con los 5 dispositivos del laboratorio."""
    return inventory_svc.add_lab_devices()
