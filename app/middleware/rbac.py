"""NetPulse — RBAC (Role-Based Access Control) dependencies.

Provee:
- get_current_user: dependencia que obtiene el payload del usuario autenticado.
- requires_role: factory que genera una dependencia para exigir rol mínimo.
- requires_scope: valida grupo real del dispositivo para mutaciones.
"""

from fastapi import HTTPException, Request

from app.models.schemas import UserRole
from app.services import inventory_svc

# Jerarquía de roles: admin > operator > viewer
_ROLE_HIERARCHY = {
    UserRole.ADMIN: 3,
    UserRole.OPERATOR: 2,
    UserRole.VIEWER: 1,
}


def get_current_user(request: Request) -> dict:
    """Obtiene el payload del usuario autenticado desde request.state.

    Debe usarse después de JWTBearer, que almacena el payload en request.state.user.
    """
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="No autenticado — token requerido",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def requires_role(min_role: str | UserRole):
    """Factory: crea una dependencia que exige un rol mínimo.

    Args:
        min_role: Rol mínimo requerido ("viewer", "operator", "admin").

    Returns:
        Función dependencia (callable) que valida el rol del usuario.

    Uso:
        @router.post("/devices", dependencies=[Depends(requires_role("admin"))])
        def create_device(...):
            ...
    """

    if isinstance(min_role, str):
        min_role = UserRole(min_role)

    min_level = _ROLE_HIERARCHY.get(min_role, 1)

    async def role_checker(request: Request) -> dict:
        """Verifica que el usuario tenga el rol mínimo requerido."""
        user = get_current_user(request)
        user_role_raw = user.get("role", "viewer")
        try:
            user_role = UserRole(user_role_raw)
        except ValueError:
            raise HTTPException(
                status_code=403,
                detail=f"Rol desconocido: {user_role_raw}",
            )

        user_level = _ROLE_HIERARCHY.get(user_role, 0)
        if user_level < min_level:
            raise HTTPException(
                status_code=403,
                detail=f"Acceso denegado — se requiere rol '{min_role.value}' o superior",
            )

        return user

    return role_checker


def scope_error(user: dict, group: str | None) -> str | None:
    """Devuelve el rechazo literal o None; un device sin grupo no concede acceso.

    Los sites son los mismos grupos del inventario, comparados sin distinguir
    mayúsculas; [] explícito significa ningún grupo autorizado.
    """
    scoped = user.get("scope_group") is not None or user.get("scope_sites") is not None
    if not scoped:
        return None
    sites = user.get("scope_sites")
    names = [user.get("scope_group")]
    if isinstance(sites, list):
        names.extend(sites)
    allowed = {name.casefold() for name in names if isinstance(name, str) and name.strip()}
    if isinstance(group, str) and group.strip() and group.casefold() in allowed:
        return None
    return f"device fuera de tu scope: {group if group else 'sin grupo'}"


def requires_scope(*, group: str | None = None, device_id: str | None = None,
                   request: Request | None = None) -> None:
    """Valida un grupo explícito o resuelve el device_id contra inventario.

    Se invoca justo antes de la operación, tras JWTBearer/requires_role; no
    permite usar un group enviado por cliente para suplantar el grupo real.
    """
    if request is None:
        raise ValueError("requires_scope necesita request")
    user = get_current_user(request)
    if device_id is not None:
        device = inventory_svc.get_device(device_id)
        if device is None:
            # Para tokens legados se conserva el manejo histórico del endpoint.
            if user.get("scope_group") is None and user.get("scope_sites") is None:
                return
            raise HTTPException(404, f"Dispositivo '{device_id}' no encontrado")
        group = device.get("group")
    error = scope_error(user, group)
    if error:
        raise HTTPException(403, error)


def partition_device_scope(request: Request, device_ids: list[str]) -> tuple[list[str], list[dict]]:
    """Separa autorizados y errores por device sin cancelar todo el batch."""
    allowed, rejected = [], []
    for device_id in device_ids:
        try:
            requires_scope(device_id=device_id, request=request)
        except HTTPException as exc:
            rejected.append({"device_id": device_id, "error": exc.detail})
        else:
            allowed.append(device_id)
    return allowed, rejected
