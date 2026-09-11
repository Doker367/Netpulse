"""NetPulse — Topology Router.

Vista de topología de red basada en LLDP + tabla de interfaces.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.middleware.auth import JWTBearer
from app.middleware.rbac import requires_role
from app.services import inventory_svc, napalm_svc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/topology", tags=["Topology"])


from concurrent.futures import ThreadPoolExecutor, as_completed

@router.get(
    "/map",
    dependencies=[Depends(JWTBearer()), Depends(requires_role("viewer"))],
)
def topology_map():
    """Construye el mapa de topología desde LLDP de todos los dispositivos de forma concurrente."""
    devices = inventory_svc.list_devices()
    nodes = []
    links = []
    seen_links = set()

    for d in devices:
        nodes.append({
            "id": d["id"],
            "driver": d.get("driver", "?"),
            "type": d.get("type", "router"),
            "group": d.get("group", ""),
        })

    def _fetch_lldp(dev_id):
        try:
            res = napalm_svc.get_lldp_neighbors(dev_id)
            return dev_id, res
        except Exception:
            return dev_id, None

    lldp_results = {}
    if devices:
        with ThreadPoolExecutor(max_workers=min(len(devices), 10)) as executor:
            futures = {executor.submit(_fetch_lldp, d["id"]): d["id"] for d in devices}
            for future in as_completed(futures):
                dev_id, res = future.result()
                if res and res.success:
                    lldp_results[dev_id] = res.data or {}

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
