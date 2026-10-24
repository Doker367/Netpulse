"""Contrato de POST /api/auth/refresh y regresión de login/me.

El refresh es rotación stateless: exige un JWT aún válido y recalculate los
claims desde config/users.yaml. El login real se prueba con una IP
X-Forwarded-For propia para no consumir el cupo de 5/min del rate limiter que
comparten los demás tests de auth.
"""

from datetime import timedelta

import pytest
import yaml
from fastapi.testclient import TestClient

from app.core.security import create_access_token, hash_password, verify_token
from app.main import app

TEST_PASSWORD = "refresh-test-password"

# IP propia para el único login de este módulo (ver client_key en limiter.py).
LOGIN_IP = "198.51.100.77"

_USERS = {
    "users": [
        {
            "username": "scoped",
            "role": "admin",
            "scope_group": "Core",
            "scope_sites": ["DMZ", "Lab"],
            "password_hash": hash_password(TEST_PASSWORD),
        },
        {
            "username": "plain",
            "role": "operator",
            "password_hash": hash_password(TEST_PASSWORD),
        },
    ]
}


def _write_users(path, users):
    path.write_text(yaml.safe_dump(users, allow_unicode=True), encoding="utf-8")


@pytest.fixture(scope="module", autouse=True)
def users_file(tmp_path_factory):
    """Apunta auth.USERS_FILE a un archivo temporal y restaura el original."""
    import app.routers.auth as auth

    path = tmp_path_factory.mktemp("config") / "users.yaml"
    _write_users(path, _USERS)

    original_file = auth.USERS_FILE
    original_cache = dict(auth._users_cache)
    auth.USERS_FILE = path
    auth._users_cache.clear()
    yield path
    auth.USERS_FILE = original_file
    auth._users_cache.clear()
    auth._users_cache.update(original_cache)


@pytest.fixture(scope="module")
def client():
    # Sin contexto: no se ejecuta el lifespan (evita sembrar devices/syslog).
    return TestClient(app)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_refresh_conserva_sub_role_y_scopes(client):
    old = create_access_token(
        {
            "sub": "scoped",
            "role": "admin",
            "scope_group": "Core",
            "scope_sites": ["DMZ", "Lab"],
        },
        expires_delta=timedelta(minutes=1),
    )
    old_payload = verify_token(old)

    r = client.post("/api/auth/refresh", headers=_bearer(old))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"] != old

    payload = verify_token(body["access_token"])
    assert payload["sub"] == "scoped"
    assert payload["role"] == "admin"
    assert payload["scope_group"] == "Core"
    assert payload["scope_sites"] == ["DMZ", "Lab"]
    # El token renovado recibe la expiración completa, no la heredada.
    assert payload["exp"] > old_payload["exp"]


def test_refresh_sin_scopes_no_los_inventa(client):
    token = create_access_token({"sub": "plain", "role": "operator"})
    r = client.post("/api/auth/refresh", headers=_bearer(token))
    assert r.status_code == 200, r.text

    payload = verify_token(r.json()["access_token"])
    assert payload["sub"] == "plain"
    assert payload["role"] == "operator"
    assert "scope_group" not in payload
    assert "scope_sites" not in payload


def test_refresh_relee_rol_y_scope_actuales(client, users_file):
    """Si el scope/rol cambió en config, el refresh no prorroga los viejos."""
    import app.routers.auth as auth

    old = create_access_token(
        {
            "sub": "scoped",
            "role": "admin",
            "scope_group": "Core",
            "scope_sites": ["DMZ", "Lab"],
        }
    )

    changed = {
        "users": [
            {
                "username": "scoped",
                "role": "viewer",
                "scope_group": "DMZ",
                "password_hash": hash_password(TEST_PASSWORD),
            }
        ]
    }
    try:
        _write_users(users_file, changed)
        auth._users_cache.clear()

        r = client.post("/api/auth/refresh", headers=_bearer(old))
    finally:
        # Restaura la config compartida por el resto del módulo.
        _write_users(users_file, _USERS)
        auth._users_cache.clear()

    assert r.status_code == 200, r.text
    payload = verify_token(r.json()["access_token"])
    assert payload["role"] == "viewer"
    assert payload["scope_group"] == "DMZ"
    assert "scope_sites" not in payload


def test_refresh_usuario_eliminado(client):
    old = create_access_token({"sub": "efimero", "role": "admin"})
    # El usuario "efimero" nunca existió en config: no se puede renovar.
    r = client.post("/api/auth/refresh", headers=_bearer(old))
    assert r.status_code == 401
    assert r.json()["detail"] == "Usuario no encontrado para renovar la sesión"


def test_refresh_sin_token(client):
    r = client.post("/api/auth/refresh")
    assert r.status_code == 401
    assert r.json()["detail"] == "Token de autorización requerido"
    assert r.headers.get("www-authenticate") == "Bearer"


def test_refresh_token_invalido(client):
    r = client.post("/api/auth/refresh", headers=_bearer("esto-no-es-un-jwt"))
    assert r.status_code == 401
    assert r.json()["detail"] == "Token inválido o expirado"
    assert r.headers.get("www-authenticate") == "Bearer"


def test_refresh_token_expirado(client):
    expired = create_access_token(
        {"sub": "scoped", "role": "admin"}, expires_delta=timedelta(minutes=-1)
    )
    r = client.post("/api/auth/refresh", headers=_bearer(expired))
    assert r.status_code == 401
    assert r.json()["detail"] == "Token inválido o expirado"


def test_refresh_firma_manipulada(client):
    token = create_access_token({"sub": "scoped", "role": "admin"})
    header, payload, _ = token.split(".")
    forged = f"{header}.{payload}.firma-invalida"
    r = client.post("/api/auth/refresh", headers=_bearer(forged))
    assert r.status_code == 401
    assert r.json()["detail"] == "Token inválido o expirado"


def test_login_y_me_siguen_intactos(client):
    """Regresión: el contrato del login y /me no cambia con el refresh."""
    r = client.post(
        "/api/auth/login",
        json={"username": "scoped", "password": TEST_PASSWORD},
        headers={"X-Forwarded-For": LOGIN_IP},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "bearer"
    token = body["access_token"]

    payload = verify_token(token)
    assert payload["sub"] == "scoped"
    assert payload["role"] == "admin"
    assert payload["scope_group"] == "Core"
    assert payload["scope_sites"] == ["DMZ", "Lab"]

    me = client.get("/api/auth/me", headers=_bearer(token))
    assert me.status_code == 200
    assert me.json() == {"username": "scoped", "role": "admin"}

    # El token del login también se puede renovar.
    refreshed = client.post("/api/auth/refresh", headers=_bearer(token))
    assert refreshed.status_code == 200, refreshed.text
    assert verify_token(refreshed.json()["access_token"])["sub"] == "scoped"
