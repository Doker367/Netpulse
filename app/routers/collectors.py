"""NetPulse — Collectors Router.

Estado del collector syslog y polling SNMP manual.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.middleware.auth import JWTBearer
from app.middleware.rbac import requires_role
from app.services.collectors import syslog_svc, snmp_svc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/collectors", tags=["Collectors"])

AUTH = [Depends(JWTBearer()), Depends(requires_role("viewer"))]
ADMIN = [Depends(JWTBearer()), Depends(requires_role("admin"))]


@router.get("/syslog/status", dependencies=AUTH)
def syslog_status():
    """Estado del collector syslog (corriendo/parado)."""
    return {
        "running": syslog_svc.is_running(),
        "host": syslog_svc.DEFAULT_BIND_HOST,
        "port": syslog_svc.DEFAULT_BIND_PORT,
    }


@router.post("/syslog/start", dependencies=ADMIN)
def syslog_start():
    """Arranca el collector syslog (si no está corriendo)."""
    if syslog_svc.is_running():
        return {"status": "already_running"}
    ok = syslog_svc.start_syslog_collector()
    return {"status": "started" if ok else "failed"}


@router.post("/syslog/stop", dependencies=ADMIN)
def syslog_stop():
    """Detiene el collector syslog."""
    syslog_svc.stop_syslog_collector()
    return {"status": "stopped"}


@router.post("/snmp/poll", dependencies=ADMIN)
def snmp_poll():
    """Ejecuta un polling SNMP manual sobre todos los dispositivos."""
    return snmp_svc.poll_all_devices()


@router.get("/snmp/poll/{device_id}", dependencies=AUTH)
def snmp_poll_device(device_id: str):
    """Polling SNMP de un dispositivo específico."""
    from app.services import inventory_svc
    d = inventory_svc.get_device(device_id)
    if not d:
        raise HTTPException(404, "Dispositivo no encontrado")
    return snmp_svc.poll_device_snmp(d.get("hostname", ""))
