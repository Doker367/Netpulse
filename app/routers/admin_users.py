"""NetPulse — Router de Administración de Usuarios (CRUD).

Endpoints (todos requieren rol admin):
- GET    /api/admin/users          — Listar todos los usuarios (sin contraseñas)
- POST   /api/admin/users          — Crear un nuevo usuario
- GET    /api/admin/users/{username} — Obtener info de un usuario
- PUT    /api/admin/users/{username} — Actualizar un usuario
- DELETE /api/admin/users/{username} — Eliminar un usuario
"""

import yaml
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.security import hash_password
from app.models.schemas import UserCreate, UserUpdate, UserResponse, UserRole
from app.middleware.auth import JWTBearer
from app.middleware.rbac import requires_role, get_current_user

router = APIRouter(
    prefix="/api/admin",
    tags=["Admin — Usuarios"],
    dependencies=[Depends(JWTBearer()), Depends(requires_role("admin"))],
)

# ── Ruta al archivo de usuarios ───────────────────────────────

USERS_FILE = Path(__file__).resolve().parent.parent.parent / "config" / "users.yaml"


# ── Utilidades ────────────────────────────────────────────────


def _load_users_raw() -> list[dict]:
    """Carga la lista de usuarios desde config/users.yaml."""
    if not USERS_FILE.exists():
        return []
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("users", [])


def _save_users(users: list[dict]) -> None:
    """Guarda la lista de usuarios en config/users.yaml."""
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        yaml.dump(
            {"users": users},
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )


def _user_to_response(user: dict) -> UserResponse:
    """Convierte un dict de usuario a UserResponse (sin contraseña)."""
    return UserResponse(
        username=user["username"],
        role=UserRole(user.get("role", "viewer")),
        name=user.get("name", user["username"]),
    )


def _find_user(users: list[dict], username: str) -> int | None:
    """Busca un usuario por username, devuelve su índice o None."""
    for i, u in enumerate(users):
        if u.get("username") == username:
            return i
    return None


def _invalidate_auth_cache():
    """Invalida el caché de usuarios en auth.py para que recargue del archivo."""
    import app.routers.auth as auth_mod

    auth_mod._users_cache.clear()


# ── Endpoints ─────────────────────────────────────────────────


@router.get("/users", response_model=list[UserResponse])
def list_users():
    """Lista todos los usuarios registrados (sin contraseñas)."""
    users = _load_users_raw()
    return [_user_to_response(u) for u in users]


@router.post("/users", response_model=UserResponse, status_code=201)
def create_user(body: UserCreate):
    """Crea un nuevo usuario en el sistema."""
    users = _load_users_raw()

    # Verificar que no exista
    if _find_user(users, body.username) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"El usuario '{body.username}' ya existe",
        )

    # Crear nuevo usuario
    new_user = {
        "username": body.username,
        "role": body.role.value,
        "name": body.name,
        "password_hash": hash_password(body.password),
        "plain_password": None,
    }
    users.append(new_user)
    _save_users(users)
    _invalidate_auth_cache()
    return _user_to_response(new_user)


@router.get("/users/{username}", response_model=UserResponse)
def get_user(username: str):
    """Obtiene información de un usuario específico."""
    users = _load_users_raw()
    idx = _find_user(users, username)
    if idx is None:
        raise HTTPException(
            status_code=404,
            detail=f"Usuario '{username}' no encontrado",
        )
    return _user_to_response(users[idx])


@router.put("/users/{username}", response_model=UserResponse)
def update_user(username: str, body: UserUpdate):
    """Actualiza los datos de un usuario existente."""
    users = _load_users_raw()
    idx = _find_user(users, username)
    if idx is None:
        raise HTTPException(
            status_code=404,
            detail=f"Usuario '{username}' no encontrado",
        )

    user = users[idx]

    # Actualizar solo los campos proporcionados
    if body.password is not None:
        user["password_hash"] = hash_password(body.password)
        user["plain_password"] = None
    if body.role is not None:
        user["role"] = body.role.value
    if body.name is not None:
        user["name"] = body.name

    users[idx] = user
    _save_users(users)
    _invalidate_auth_cache()
    return _user_to_response(user)


@router.delete("/users/{username}", status_code=204)
def delete_user(username: str, request: Request):
    """Elimina un usuario del sistema.

    No permite eliminarse a sí mismo.
    """
    current_user = get_current_user(request)
    current_username = current_user.get("sub", "")

    if current_username == username:
        raise HTTPException(
            status_code=400,
            detail="No puedes eliminar tu propio usuario",
        )

    users = _load_users_raw()
    idx = _find_user(users, username)
    if idx is None:
        raise HTTPException(
            status_code=404,
            detail=f"Usuario '{username}' no encontrado",
        )

    # Eliminar usuario
    users.pop(idx)
    _save_users(users)
    _invalidate_auth_cache()
    return None
