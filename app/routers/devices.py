"""NetPulse — Device Inventory Router.

CRUD endpoints para gestionar el inventario de dispositivos.
"""

import asyncio
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.middleware.auth import JWTBearer
from app.middleware.rbac import requires_role
from app.models.schemas import DeviceCreate, DeviceResponse, DeviceUpdate
from app.services import inventory_svc

router = APIRouter(prefix="/api/devices", tags=["Devices"])

# ── Caché/histéresis del health ──────────────────────────────
# Evita parpadeos: un equipo solo pasa a OFFLINE tras N fallos
# consecutivos y los resultados se reutilizan durante TTL segundos.
_HEALTH_TTL = 8.0
_HEALTH_FAILS_TO_OFFLINE = 2
_health_cache: dict[str, tuple[float, bool]] = {}
_health_fails: dict[str, int] = {}


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

    Aplica caché corta + histéresis para que el estado no parpadee.

    This endpoint is **public** (no auth required).
    """
    devices = inventory_svc.list_devices()

    if device_id and isinstance(device_id, str):
        devices = [d for d in devices if d["id"] == device_id]
        if not devices:
            raise HTTPException(404, f"Dispositivo '{device_id}' no encontrado")

    now = time.monotonic()
    result: dict[str, bool] = {}
    to_probe = []
    for d in devices:
        did = d["id"]
        cached = _health_cache.get(did)
        if cached and now - cached[0] < _HEALTH_TTL:
            result[did] = cached[1]
        else:
            to_probe.append(d)

    if to_probe:
        probes = await asyncio.gather(
            *(_probe_device(d, timeout=2.5) for d in to_probe),
            return_exceptions=True,
        )
        for d, res in zip(to_probe, probes):
            did = d["id"]
            if res is True:
                _health_fails[did] = 0
                online = True
            else:
                _health_fails[did] = _health_fails.get(did, 0) + 1
                if _health_fails[did] >= _HEALTH_FAILS_TO_OFFLINE:
                    online = False
                else:
                    # Primer fallo: mantener el último estado conocido
                    online = _health_cache.get(did, (0.0, False))[1]
            _health_cache[did] = (now, online)
            result[did] = online

    return result


async def _probe_device(dev: dict, timeout: float = 2.5) -> bool:
    """Sondea la conectividad del dispositivo según su protocolo."""
    import sys
    host = dev.get("hostname", "")
    port = int(dev.get("port", 22))
    protocol = (dev.get("protocol") or "").lower()
    driver = (dev.get("driver") or "").lower()

    if protocol == "snmp" or driver == "snmp" or port == 161:
        # 1. ICMP Ping probe (2 paquetes: tolera 1 perdido y evita falsos OFFLINE)
        ping_args = ["ping", "-c", "2", "-t", "2", host] if sys.platform == "darwin" else ["ping", "-c", "2", "-W", "2", host]
        try:
            proc = await asyncio.create_subprocess_exec(
                *ping_args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            code = await asyncio.wait_for(proc.wait(), timeout=timeout)
            if code == 0:
                return True
        except Exception:
            pass

        # Para el health (rápido) el ICMP es suficiente. No consultamos SNMP
        # aquí: si el equipo no responde a ping, se considera offline y así
        # evitamos esperas de varios segundos por los timeouts SNMP.
        return False

    return await _tcp_check(host, port, timeout=timeout)


async def _tcp_check(host: str, port: int, timeout: float = 2.0) -> bool:
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
