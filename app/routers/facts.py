"""NetPulse — Facts & Info Router.

Endpoints para obtener facts, interfaces, BGP, y ping.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.middleware.auth import JWTBearer
from app.middleware.rbac import requires_role
from app.models.schemas import (
    FactsResponse,
    InterfacesResponse,
    InterfaceInfo,
    BgpResponse,
    BgpPeer,
    PingResult,
)
from app.services import napalm_svc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/facts", tags=["Facts"])


@router.get(
    "/{device_id}",
    response_model=FactsResponse,
    dependencies=[Depends(JWTBearer()), Depends(requires_role("viewer"))],
)
def get_facts(device_id: str):
    """Obtiene facts del dispositivo (hostname, OS, modelo, uptime...)."""
    result = napalm_svc.get_facts(device_id)
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

    f = result.data or {}
    return FactsResponse(
        device_id=device_id,
        hostname=f.get("hostname", "Unknown"),
        os_version=f.get("os_version", "Unknown"),
        model=f.get("model", "Unknown"),
        vendor=f.get("vendor", "Unknown"),
        serial_number=f.get("serial_number", "Unknown"),
        uptime=f.get("uptime", 0),
        interface_count=len(f.get("interface_list", [])),
    )


@router.get(
    "/{device_id}/interfaces",
    response_model=InterfacesResponse,
    dependencies=[Depends(JWTBearer()), Depends(requires_role("viewer"))],
)
def get_interfaces(device_id: str):
    """Lista todas las interfaces del dispositivo."""
    result = napalm_svc.get_interfaces(device_id)
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

    ifaces_data = result.data or {}
    ifaces = [
        InterfaceInfo(
            name=name,
            is_up=iface.get("is_up", False),
            is_enabled=iface.get("is_enabled", False),
            description=iface.get("description", ""),
            mac_address=iface.get("mac_address", "N/A"),
            speed=iface.get("speed"),
            mtu=iface.get("mtu"),
        )
        for name, iface in ifaces_data.items()
    ]

    up = sum(1 for i in ifaces if i.is_up)
    return InterfacesResponse(
        device_id=device_id,
        interfaces=ifaces,
        total=len(ifaces),
        up_count=up,
        down_count=len(ifaces) - up,
    )


@router.get(
    "/{device_id}/bgp",
    response_model=BgpResponse,
    dependencies=[Depends(JWTBearer()), Depends(requires_role("viewer"))],
)
def get_bgp(device_id: str):
    """Obtiene vecinos BGP del dispositivo."""
    result = napalm_svc.get_bgp_neighbors(device_id)

    # Fallback: get_bgp_neighbors() no funciona en FRRouting
    if not result.success and "afi" in str(result.error).lower():
        logger.info("[%s] get_bgp_neighbors falló en FRR (%s), usando get_bgp_config",
                    device_id, result.error)
        result = napalm_svc.get_bgp_config(device_id)

    if not result.success:
        # Si ambos fallaron, devolver respuesta vacía en vez de 502
        logger.warning("[%s] BGP no disponible: %s", device_id, result.error)
        return BgpResponse(
            device_id=device_id,
            local_as=None,
            peers=[],
            peer_count=0,
        )

    bgp = result.data or {}

    # Estructura de get_bgp_config(): {"_": {"local_as": N, "neighbors": {...}}}
    # Estructura de get_bgp_neighbors(): {"global": {"peers": {...}}}
    peers = []
    local_as = None

    if "global" in bgp:
        # Formato get_bgp_neighbors
        for vrf_data in bgp.values():
            if not isinstance(vrf_data, dict):
                continue
            peers_data = vrf_data.get("peers", {})
            for ip, peer in peers_data.items():
                if local_as is None:
                    local_as = peer.get("local_as")
                peers.append(BgpPeer(
                    peer_ip=ip,
                    remote_as=peer.get("remote_as", 0),
                    state="Established" if peer.get("is_up", False) else "Idle",
                    description=peer.get("description", ""),
                ))
    else:
        # Formato get_bgp_config: {"group_name": {"local_as": N, "neighbors": {...}}}
        for group_data in bgp.values():
            if not isinstance(group_data, dict):
                continue
            if local_as is None:
                local_as = group_data.get("local_as")
            neighbors = group_data.get("neighbors", {})
            for ip, neighbor in neighbors.items():
                if local_as is None:
                    local_as = neighbor.get("local_as")
                peers.append(BgpPeer(
                    peer_ip=ip,
                    remote_as=neighbor.get("remote_as", 0),
                    state="Configured",
                    description=neighbor.get("description", "").strip('"'),
                ))

    return BgpResponse(
        device_id=device_id,
        local_as=local_as,
        peers=peers,
        peer_count=len(peers),
    )


@router.post(
    "/{device_id}/ping",
    response_model=PingResult,
    dependencies=[Depends(JWTBearer()), Depends(requires_role("viewer"))],
)
def ping_target(device_id: str, target: str = "8.8.8.8"):
    """Hace ping desde el dispositivo."""
    result = napalm_svc.ping(device_id, target)
    
    # Fallback 1: si NAPALM ping falla, intentar via CLI
    if not result.success:
        logger.info("[%s] NAPALM ping falló, intentando via CLI...", device_id)
        cmd_result = napalm_svc.run_commands(device_id, [f"ping {target} count 2"])
        if cmd_result.success and cmd_result.data:
            cmd_out = cmd_result.data[0].get("output", "") if isinstance(cmd_result.data, list) else str(cmd_result.data)
            has_success = ("bytes from" in cmd_out.lower() or "!" in cmd_out or "received" in cmd_out or "packets transmitted" in cmd_out)
            if has_success:
                # Parse results
                rx = 0
                for line in cmd_out.split("\n"):
                    if "packets transmitted" in line:
                        parts = line.split()
                        rx = int(parts[3]) if len(parts) > 3 else 0
                    elif "bytes from" in line:
                        rx += 1
                return PingResult(
                    device_id=device_id, target=target, success=rx > 0,
                    probes_sent=2, probes_received=rx,
                    rtt_min=0.0, rtt_avg=0.0, rtt_max=0.0,
                )
    
    # Fallback 2: bash directo via SSH (para FRR que no captura ping via vtysh)
    if not result.success:
        logger.info("[%s] CLI ping falló, intentando bash directo...", device_id)
        bash_ping = napalm_svc.bash_command(device_id, f"ping -c 2 -W 2 {target}")
        if bash_ping.success and bash_ping.data:
            bash_out = str(bash_ping.data)
            has_success = "bytes from" in bash_out.lower() or "packets transmitted" in bash_out
            rx = 0; tx = 0
            for line in bash_out.split("\n"):
                if "packets transmitted" in line:
                    parts = line.split()
                    if len(parts) >= 4:
                        tx = int(parts[0])
                        rx = int(parts[3])
                elif "bytes from" in line:
                    rx += 1
            if tx == 0: tx = 2
            return PingResult(
                device_id=device_id, target=target, success=rx > 0,
                probes_sent=tx, probes_received=rx,
                rtt_min=0.0, rtt_avg=0.0, rtt_max=0.0,
            )
    
    # Si todo falló, devolver error graceful
    logger.warning("[%s] Ping no disponible: %s", device_id, result.error)
    return PingResult(
        device_id=device_id, target=target, success=False,
        probes_sent=0, probes_received=0,
        rtt_min=0.0, rtt_avg=0.0, rtt_max=0.0,
    )

    p = result.data or {}
    # NAPALM ping() returns {'success': {...}} — unwrap first
    if "success" in p:
        p = p["success"]

    probes = p.get("probes_sent", 0)
    loss_pct = p.get("packet_loss", 0)
    probes_received = probes - int(probes * loss_pct / 100) if probes else 0

    return PingResult(
        device_id=device_id,
        target=target,
        success=probes_received > 0,
        probes_sent=probes,
        probes_received=probes_received,
        rtt_min=p.get("rtt_min", 0.0),
        rtt_avg=p.get("rtt_avg", 0.0),
        rtt_max=p.get("rtt_max", 0.0),
    )


@router.get(
    "/{device_id}/resources",
    dependencies=[Depends(JWTBearer()), Depends(requires_role("viewer"))],
)
def device_resources(device_id: str):
    """Obtiene métricas de recursos del dispositivo (CPU, memoria, disco)."""
    result = napalm_svc.get_resources(device_id)
    if not result.success:
        raise HTTPException(status_code=502, detail=result.error)
    return result.data
