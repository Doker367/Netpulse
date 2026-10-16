"""NetPulse — Router de Firewalls (Fortinet FortiOS).

Consulta estado, interfaces, políticas, sesiones (NAT) y configuración de
un FortiGate vía su REST API (ver ``fortios_svc``). Requiere que el
dispositivo del inventario apunte al FortiGate (driver ``fortios``) con su
token de API en las credenciales.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse

from app.middleware.auth import JWTBearer
from app.middleware.rbac import requires_role
from app.services import fortios_svc, napalm_svc

router = APIRouter(
    prefix="/api/firewall",
    tags=["Firewall"],
    dependencies=[Depends(JWTBearer()), Depends(requires_role("viewer"))],
)


def _device(device_id: str) -> dict:
    device = napalm_svc._find_device(device_id)
    if not device:
        raise HTTPException(404, f"Dispositivo '{device_id}' no encontrado")
    return device


def _run(fn, device: dict, *args, **kwargs):
    try:
        return fn(device, *args, **kwargs)
    except fortios_svc.FortiOSError as exc:
        raise HTTPException(502, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, detail=f"FortiOS: {exc}")


@router.get("/{device_id}/status")
def firewall_status(device_id: str):
    """Estado del FortiGate (modelo, versión, uptime)."""
    return _run(fortios_svc.status, _device(device_id))


@router.get("/{device_id}/interfaces")
def firewall_interfaces(device_id: str):
    """Interfaces del FortiGate."""
    return _run(fortios_svc.interfaces, _device(device_id))


@router.get("/{device_id}/policies")
def firewall_policies(device_id: str):
    """Políticas de firewall (zones, origen/destino, servicio, acción)."""
    return _run(fortios_svc.policies, _device(device_id))


@router.get("/{device_id}/sessions")
def firewall_sessions(device_id: str, limit: int = Query(200, ge=1, le=2000)):
    """Sesiones activas (incluye NAT) del FortiGate."""
    return _run(fortios_svc.sessions, _device(device_id), limit=limit)


@router.get("/{device_id}/config", response_class=PlainTextResponse)
def firewall_config(device_id: str):
    """Backup de la configuración global en texto plano."""
    return _run(fortios_svc.running_config, _device(device_id))
