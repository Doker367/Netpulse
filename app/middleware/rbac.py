"""NetPulse — RBAC (Role-Based Access Control) dependencies.

Provee:
- get_current_user: dependencia que obtiene el payload del usuario autenticado.
- requires_role: factory que genera una dependencia para exigir rol mínimo.
"""

from fastapi import HTTPException, Request

from app.models.schemas import UserRole

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
