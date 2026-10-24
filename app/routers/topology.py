"""NetPulse — Topology Router.

Vista de topología de red basada en LLDP + tabla de interfaces.
"""

import logging
import os

from fastapi import APIRouter, Depends, HTTPException

from app.middleware.auth import JWTBearer
from app.middleware.rbac import requires_role
from app.services import geoip_svc, inventory_svc, napalm_svc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/topology", tags=["Topology"])


from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout

# Presupuesto máximo para recolectar LLDP (equipos caídos no deben colgar la vista).
LLDP_DEADLINE = float(os.getenv("NETPULSE_TOPOLOGY_TIMEOUT", "12"))

# Drivers que soportan LLDP vía NAPALM.
_LLDP_DRIVERS = {"ios", "eos", "junos", "nxos", "iosxr", "ros"}


def _lldp_capable(device: dict) -> bool:
    """Solo intentamos LLDP en equipos que lo soportan y no son solo-SNMP."""
    driver = str(device.get("driver", "")).lower()
    proto = str(device.get("protocol", "")).lower()
    if driver == "snmp" or (proto == "snmp" and not device.get("cli_transport")):
        return False
    return driver in _LLDP_DRIVERS


@router.get(
    "/map",
    dependencies=[Depends(JWTBearer()), Depends(requires_role("viewer"))],
)
def topology_map():
    """Construye el mapa de topología desde LLDP (concurrente y acotado).

    Solo consulta LLDP en equipos que lo soportan y con un presupuesto de
    tiempo global, para que un equipo caído no congele la vista.
    """
    devices = inventory_svc.list_devices()
    nodes = []
    for d in devices:
        dev_ip = str(d.get("hostname", "") or "")
        nodes.append({
            "id": d["id"],
            "driver": d.get("driver", "?"),
            "type": d.get("type", "router"),
            "group": d.get("group", ""),
            "ip": dev_ip,
            "geo": geoip_svc.lookup(dev_ip) if dev_ip else None,
        })

    def _fetch_lldp(dev_id):
        try:
            return dev_id, napalm_svc.get_lldp_neighbors(dev_id)
        except Exception:  # noqa: BLE001
            return dev_id, None

    candidates = [d for d in devices if _lldp_capable(d)]
    lldp_results: dict = {}
    if candidates:
        executor = ThreadPoolExecutor(max_workers=min(len(candidates), 10))
        try:
            futures = {executor.submit(_fetch_lldp, d["id"]): d["id"] for d in candidates}
            try:
                for future in as_completed(futures, timeout=LLDP_DEADLINE):
                    dev_id, res = future.result()
                    if res and res.success:
                        lldp_results[dev_id] = res.data or {}
            except FuturesTimeout:
                logger.info("[topology] LLDP sin respuesta en %ss; devuelvo nodos igual", LLDP_DEADLINE)
        finally:
            # No esperamos a los threads en curso (equipos caídos): la vista no debe colgarse.
            executor.shutdown(wait=False, cancel_futures=True)

    links = []
    seen_links = set()
    for d in devices:
        dev_id = d["id"]
        neighbors = lldp_results.get(dev_id, {})
        for local_port, nbr_list in neighbors.items():
            for nbr in nbr_list or []:
                remote = nbr.get("hostname", "")
                if not remote:
                    continue
                key = tuple(sorted([dev_id, remote]))
                if key in seen_links:
                    continue
                seen_links.add(key)
                links.append({
                    "source": dev_id,
                    "target": remote,
                    "local_port": local_port,
                    "remote_port": nbr.get("port", ""),
                })

    return {"nodes": nodes, "links": links}


@router.get(
    "/interfaces/{device_id}",
    dependencies=[Depends(JWTBearer()), Depends(requires_role("viewer"))],
)
def device_interfaces_topology(device_id: str):
    """Lista interfaces del dispositivo con estado, para el mapa."""
    result = napalm_svc.get_interfaces(device_id)
    if not result.success:
        raise HTTPException(status_code=502, detail=result.error)
    data = result.data or {}
    ifaces = []
    for name, info in data.items():
        ifaces.append({
            "name": name,
            "is_up": info.get("is_up", False),
            "is_enabled": info.get("is_enabled", False),
            "speed": info.get("speed"),
            "description": info.get("description", ""),
            "mac_address": info.get("mac_address", ""),
        })
    return {"device_id": device_id, "interfaces": ifaces}
