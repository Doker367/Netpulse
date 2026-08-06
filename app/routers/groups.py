"""NetPulse — Device Groups Router.

Gestiona grupos de dispositivos (Core, Access, Edge, Firewall, DMZ).
"""

from fastapi import APIRouter, Depends, HTTPException

from app.middleware.auth import JWTBearer
from app.middleware.rbac import requires_role
from app.models.schemas import GroupCreate, GroupResponse, DeviceResponse, DeviceUpdate
from app.services import inventory_svc

router = APIRouter(prefix="/api/groups", tags=["Groups"])


@router.get(
    "",
    response_model=list[GroupResponse],
    dependencies=[Depends(JWTBearer()), Depends(requires_role("viewer"))],
)
def list_groups():
    """Lista todos los grupos con conteo de dispositivos."""
    return inventory_svc.list_groups()


@router.post(
    "",
    response_model=GroupResponse,
    status_code=201,
    dependencies=[Depends(JWTBearer()), Depends(requires_role("admin"))],
)
def create_group(group: GroupCreate):
    """Crea un nuevo grupo."""
    try:
        return inventory_svc.create_group(group.name, group.description)
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.get(
    "/{group_name}/devices",
    response_model=list[DeviceResponse],
    dependencies=[Depends(JWTBearer()), Depends(requires_role("viewer"))],
)
def get_group_devices(group_name: str):
    """Lista dispositivos que pertenecen a un grupo."""
    devices = inventory_svc.get_group_devices(group_name)
    if not devices:
        raise HTTPException(404, f"Grupo '{group_name}' no encontrado o vacío")
    return devices
