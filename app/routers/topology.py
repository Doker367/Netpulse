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


@router.get(
    "/map",
    dependencies=[Depends(JWTBearer()), Depends(requires_role("viewer"))],
)
def topology_map():
    """Construye el mapa de topología desde LLDP de todos los dispositivos.

    Returns:
        {
          "nodes": [{"id", "driver", "type", "group"}],
          "links": [{"source", "target", "local_port", "remote_port"}]
        }
    """
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

        result = napalm_svc.get_lldp_neighbors(d["id"])
        if not result.success:
            continue

        neighbors = result.data or {}
        # NAPALM format: {interface: [{'hostname':..., 'port':...}, ...]}
        for local_port, nbr_list in neighbors.items():
            for nbr in nbr_list or []:
                remote = nbr.get("hostname", "")
                if not remote:
                    continue
                key = tuple(sorted([d["id"], remote]))
                if key in seen_links:
                    continue
                seen_links.add(key)
                links.append({
                    "source": d["id"],
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
