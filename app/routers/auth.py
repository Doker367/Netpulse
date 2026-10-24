"""NetPulse — Router de Autenticación.

Endpoints:
- POST /api/auth/login   — validar credenciales, devolver JWT
- POST /api/auth/refresh — renovar un JWT aún válido (rotación stateless)
- GET  /api/auth/me      — info del usuario autenticado
"""

import yaml
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.limiter import limiter

from app.core.security import (
    create_access_token,
    hash_password,
    verify_password,
)
from app.models.schemas import LoginRequest, TokenResponse, UserInfo, UserRole
from app.middleware.auth import JWTBearer
from app.middleware.rbac import get_current_user
from app.services.metrics_svc import netpulse_auth_total

router = APIRouter(prefix="/api/auth", tags=["Auth"])

# ── Carga de usuarios ─────────────────────────────────────────

USERS_FILE = Path(__file__).resolve().parent.parent.parent / "config" / "users.yaml"
_users_cache: dict[str, dict] = {}


def _load_users() -> dict[str, dict]:
    """Carga usuarios desde config/users.yaml y hashea contraseñas."""
    users: dict[str, dict] = {}
    if not USERS_FILE.exists():
        return users

    with open(USERS_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    for u in data.get("users", []):
        username = u.get("username", "")
        if not username:
            continue
        # Si no hay hash almacenado, generarlo desde plain_password
        pwd_hash = u.get("password_hash") or hash_password(u.get("plain_password", ""))
        users[username] = {
            "username": username,
            "role": u.get("role", "viewer"),
            "scope_group": u.get("scope_group"),
            "scope_sites": u.get("scope_sites"),
            "password_hash": pwd_hash,
        }

    return users


def _get_users() -> dict[str, dict]:
    """Devuelve el caché de usuarios, cargándolo si es necesario."""
    global _users_cache
    if not _users_cache:
        _users_cache = _load_users()
    return _users_cache


def _token_claims(user: dict) -> dict:
    """Claims del JWT para un usuario de config/users.yaml.

    Login y refresh comparten esta función para que el contrato del token sea
    idéntico: sub, role y, solo si están definidos, scope_group/scope_sites.
    """
    claims = {"sub": user["username"], "role": user["role"]}
    # Sin asignación, el token no incluye el claim (acceso no restringido).
    for field in ("scope_group", "scope_sites"):
        if user.get(field) is not None:
            claims[field] = user[field]
    return claims


# ── Endpoints ─────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")  # Anti brute-force: máx 5 intentos/min por IP
def login(request: Request, body: LoginRequest):
    """Inicia sesión con usuario y contraseña.

    Devuelve un JWT con sub, role y, si existen, scope_group/scope_sites.
    Límite: 5 intentos por minuto por IP (anti fuerza bruta).
    """
    users = _get_users()
    user = users.get(body.username)

    if not user or not verify_password(body.password, user["password_hash"]):
        netpulse_auth_total.labels(status="failure").inc()
        raise HTTPException(
            status_code=401,
            detail="Credenciales inválidas",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Generar token JWT
    token = create_access_token(data=_token_claims(user))

    netpulse_auth_total.labels(status="success").inc()
    return TokenResponse(access_token=token)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    dependencies=[Depends(JWTBearer(auto_error=False))],
)
def refresh_token(user: dict = Depends(get_current_user)):
    """Renueva la sesión con un JWT de usuario todavía válido.

    Emite un token nuevo con la misma expiración completa, recalculando los
    claims desde config/users.yaml: si el rol o el scope cambiaron desde que se
    emitió el token original, el token renovado refleja el valor actual. Un
    usuario eliminado de la configuración no puede renovar.

    No hay estado en servidor: el token anterior sigue siendo válido hasta su
    propio `exp` (ver docs/handoffs/auth-session.md).
    """
    username = user.get("sub")

    # JWTBearer también acepta API keys np_ (sin exp ni usuario de config):
    # no deben poder convertirse en un JWT de sesión.
    if not isinstance(username, str) or username.startswith("apikey:"):
        raise HTTPException(
            status_code=401,
            detail="La renovación de sesión solo acepta tokens JWT de usuario",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Re-leer el usuario actual: el refresh es el punto donde se re-evalúan
    # rol y scope, en lugar de prorrogar privilegios ya retirados.
    current = _get_users().get(username)
    if current is None:
        raise HTTPException(
            status_code=401,
            detail="Usuario no encontrado para renovar la sesión",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return TokenResponse(access_token=create_access_token(data=_token_claims(current)))


@router.get("/me", response_model=UserInfo, dependencies=[Depends(JWTBearer())])
def get_me(user: dict = Depends(get_current_user)):
    """Devuelve la información del usuario autenticado.

    Requiere token JWT válido en el header Authorization.
    """
    return UserInfo(
        username=user["sub"],
        role=UserRole(user["role"]),
    )
