"""NetPulse — JWT Bearer authentication middleware.

Provee JWTBearer, una dependencia FastAPI que extrae y valida
el token JWT del header Authorization en cada request.
"""

from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import verify_token


class JWTBearer(HTTPBearer):
    """Dependencia FastAPI que valida tokens JWT Bearer.

    Uso:
        @router.get("/protegido", dependencies=[Depends(JWTBearer())])
        def endpoint_protegido(...):
            ...
    """

    def __init__(self, auto_error: bool = True):
        super().__init__(auto_error=auto_error)

    async def __call__(self, request: Request) -> HTTPAuthorizationCredentials:
        """Extrae y valida el token, devuelve credenciales o lanza 401."""
        credentials: HTTPAuthorizationCredentials = await super().__call__(request)

        if not credentials:
            raise HTTPException(
                status_code=401,
                detail="Token de autorización requerido",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if credentials.scheme.lower() != "bearer":
            raise HTTPException(
                status_code=403,
                detail="Esquema de autenticación inválido (se requiere Bearer)",
            )

        token = credentials.credentials
        payload = verify_token(token)

        if payload is None:
            raise HTTPException(
                status_code=401,
                detail="Token inválido o expirado",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Guardar payload en request.state para uso posterior
        request.state.user = payload
        return credentials
