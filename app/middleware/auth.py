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

        # Soporte de API keys (np_...) para automatización
        if token.startswith("np_"):
            from app.services.security import api_keys_svc
            key_info = api_keys_svc.validate_api_key(token)
            if key_info is None:
                raise HTTPException(
                    status_code=401,
                    detail="API key inválida o revocada",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            if key_info.get("kind", "user") == "collector":
                raise HTTPException(
                    status_code=403,
                    detail="Las claves de colector solo pueden usar /api/remote/ingest y /api/remote/whoami",
                )
            request.state.user = {
                "sub": f"apikey:{key_info['name']}",
                "role": key_info.get("role", "viewer"),
                "api_key_id": key_info.get("id"),
            }
            return credentials

        payload = verify_token(token)

        if payload is None:
            raise HTTPException(
                status_code=401,
                detail="Token inválido o expirado",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Revocación stateless por versión: el JWT lleva ``tv`` y debe coincidir
        # con ``token_version`` del usuario en config/users.yaml. Un token
        # emitido antes de una revocación deja de valer sin blacklist.
        _reject_revoked_session(payload)

        # Guardar payload en request.state para uso posterior
        request.state.user = payload
        return credentials


def _reject_revoked_session(payload: dict) -> None:
    """Lanza 401 si el token pertenece a una sesión revocada.

    - Token con ``tv``: se compara con la versión actual del usuario. Si el
      usuario ya no existe en la configuración, también se rechaza (un token
      nuevo nunca debe sobrevivir al borrado de su usuario).
    - Token legado sin ``tv``: se interpreta como versión 0. Si el usuario
      existe con versión > 0, la revocación también lo invalida; si el usuario
      no existe, se mantiene el comportamiento anterior (válido hasta ``exp``)
      para no romper tokens emitidos antes de esta tarjeta.

    Las API keys ``np_`` se resuelven antes de llegar aquí y no son JWT.
    """
    sub = payload.get("sub")
    if not isinstance(sub, str) or sub.startswith("apikey:"):
        return

    # Import perezoso: app.routers.auth importa este módulo al cargarse.
    from app.routers.auth import _get_users, user_token_version

    current = _get_users().get(sub)
    if current is None:
        if "tv" in payload:
            _raise_revoked()
        return

    if payload.get("tv", 0) != user_token_version(current):
        _raise_revoked()


def _raise_revoked() -> None:
    raise HTTPException(
        status_code=401,
        detail="Sesión revocada; vuelve a entrar",
        headers={"WWW-Authenticate": "Bearer"},
    )
