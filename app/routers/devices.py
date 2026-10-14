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
_health_cache: dict[str, tuple[float, bool, float]] = {}
_health_fails: dict[str, int] = {}


# ── Device Health Check (fast TCP probe, no NAPALM) ──────────
# ⚠ MUST be defined BEFORE /{device_id} to avoid route collision.


_traffic_cache: dict[str, tuple[str, str]] = {}
_traffic_cache_time: float = 0.0


def _get_device_traffic() -> dict[str, tuple[str, str]]:
    """Devuelve un mapa {nombre: (rx_str, tx_str)} desde Docker stats con caché de 5s."""
    global _traffic_cache, _traffic_cache_time
    now = time.monotonic()
    if now - _traffic_cache_time < 5.0 and _traffic_cache:
        return _traffic_cache

    traffic_map: dict[str, tuple[str, str]] = {}
    import subprocess
    try:
        out = subprocess.check_output(
            ["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.NetIO}}"],
            stderr=subprocess.DEVNULL,
            timeout=2.0,
        ).decode()
        for line in out.strip().splitlines():
            parts = line.split("\t")
            if len(parts) == 2 and " / " in parts[1]:
                cname = parts[0].strip()
                rx, tx = parts[1].split(" / ")
                traffic_map[cname] = (rx.strip(), tx.strip())
                if cname.startswith("netpulse-"):
                    short = cname[len("netpulse-"):]
                    traffic_map[short] = (rx.strip(), tx.strip())
        _traffic_cache = traffic_map
        _traffic_cache_time = now
    except Exception:
        pass
    return _traffic_cache


def _lookup_traffic(dev_id: str) -> tuple[str, str]:
    """Busca el tráfico rx/tx de un dispositivo online."""
    tmap = _get_device_traffic()
    prefix = dev_id.split("-")[0]
    candidates = [dev_id, f"netpulse-{dev_id}", prefix, f"netpulse-{prefix}", dev_id.replace("_", "-")]
    for c in candidates:
        if c in tmap:
            return tmap[c]
    return ("0.0 Mb/s", "0.0 Mb/s")


@router.get("/health")
async def device_health(
    device_id: Optional[str] = Query(None, description="Filter by device ID"),
    simple: bool = Query(False, description="Return simple boolean format {id: bool}"),
):
    """Quick health check: which devices are online?

    Uses a fast TCP connect (2 s timeout) or ICMP ping.
    Returns ``{device_id: {"online": bool, "latency_ms": float | null, "rx": str, "tx": str}, ...}``
    o ``{device_id: bool, ...}`` si ``simple=true``.

    Aplica caché corta + histéresis para que el estado no parpadee.
    This endpoint is **public** (no auth required).
    """
    devices = inventory_svc.list_devices()

    if device_id and isinstance(device_id, str):
        devices = [d for d in devices if d["id"] == device_id]
        if not devices:
            raise HTTPException(404, f"Dispositivo '{device_id}' no encontrado")

    now = time.monotonic()
    result: dict = {}
    to_probe = []
    is_simple = (simple is True)
    for d in devices:
        did = d["id"]
        cached = _health_cache.get(did)
        if cached and now - cached[0] < _HEALTH_TTL:
            online, lat, rx, tx = cached[1], cached[2], cached[3], cached[4]
            result[did] = online if is_simple else {
                "online": online,
                "latency_ms": lat if online else None,
                "rx": rx,
                "tx": tx,
            }
        else:
            to_probe.append(d)

    if to_probe:
        probes = await asyncio.gather(
            *(_probe_device(d, timeout=2.5) for d in to_probe),
            return_exceptions=True,
        )
        for d, res in zip(to_probe, probes):
            did = d["id"]
            if isinstance(res, tuple):
                ok, lat = res
            elif res is True:
                ok, lat = True, 1.0
            else:
                ok, lat = False, 0.0

            if ok:
                _health_fails[did] = 0
                online = True
                rx, tx = _lookup_traffic(did)
            else:
                _health_fails[did] = _health_fails.get(did, 0) + 1
                if _health_fails[did] >= _HEALTH_FAILS_TO_OFFLINE:
                    online = False
                    lat = 0.0
                    rx, tx = "—", "—"
                else:
                    # Primer fallo: mantener el último estado conocido
                    prev = _health_cache.get(did, (0.0, False, 0.0, "—", "—"))
                    online = prev[1]
                    lat = prev[2]
                    rx = prev[3] if len(prev) > 3 else "—"
                    tx = prev[4] if len(prev) > 4 else "—"

            _health_cache[did] = (now, online, lat, rx, tx)
            result[did] = online if is_simple else {
                "online": online,
                "latency_ms": lat if online else None,
                "rx": rx,
                "tx": tx,
            }

    return result


async def _probe_device(dev: dict, timeout: float = 2.5) -> tuple[bool, float]:
    """Sondea la conectividad del dispositivo según su protocolo y mide latencia real."""
    import re
    import sys
    host = dev.get("hostname", "")
    port = int(dev.get("port", 22))
    protocol = (dev.get("protocol") or "").lower()
    driver = (dev.get("driver") or "").lower()

    if protocol == "snmp" or driver == "snmp" or port == 161:
        # ICMP Ping probe (1 paquete rápido para medir RTT)
        ping_args = ["ping", "-c", "1", "-t", "2", host] if sys.platform == "darwin" else ["ping", "-c", "1", "-W", "2", host]
        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *ping_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            if proc.returncode == 0:
                out = stdout.decode("utf-8", errors="ignore")
                m = re.search(r"time[=<]([\d.]+)\s*ms", out)
                latency = float(m.group(1)) if m else round((time.monotonic() - start) * 1000, 1)
                return True, max(0.1, latency)
        except Exception:
            pass
        return False, 0.0

    return await _tcp_check(host, port, timeout=timeout)


async def _tcp_check(host: str, port: int, timeout: float = 2.0) -> tuple[bool, float]:
    """Fast TCP-connect probe. Returns (True, latency_ms) if port is reachable."""
    start = time.monotonic()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
        latency = round((time.monotonic() - start) * 1000, 1)
        writer.close()
        await writer.wait_closed()
        return True, max(0.1, latency)
    except (asyncio.TimeoutError, OSError, ConnectionRefusedError):
        return False, 0.0


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
