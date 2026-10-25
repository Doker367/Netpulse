"""Revocación de sesiones por `token_version` (logout global stateless).

Cada login/refresh emite un JWT con el claim ``tv``. El middleware lo compara
con ``token_version`` de ``config/users.yaml``: al incrementarlo, todos los
tokens anteriores de ese usuario dejan de valer sin blacklist en memoria y sin
cambiar el formato del token para el resto de consumidores.

Los logins usan una IP propia por test para no compartir el cupo de 5/min del
rate limiter entre pruebas.
"""

import yaml
import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token, hash_password, verify_token
from app.main import app
from app.services import audit_svc

TEST_PASSWORD = "revoke-test-password"
_PASSWORD_HASH = hash_password(TEST_PASSWORD)

_USERS = {
    "users": [
        {
            "username": "boss",
            "role": "admin",
            "name": "Boss",
            "password_hash": _PASSWORD_HASH,
            "token_version": 0,
        },
        {
            # Sin token_version: compatibilidad con usuarios ya existentes.
            "username": "alice",
            "role": "operator",
            "name": "Alice",
            "password_hash": _PASSWORD_HASH,
        },
        {
            "username": "bob",
            "role": "operator",
            "name": "Bob",
            "password_hash": _PASSWORD_HASH,
            "token_version": 0,
        },
        {
            "username": "seer",
            "role": "viewer",
            "name": "Seer",
            "password_hash": _PASSWORD_HASH,
            "token_version": 0,
        },
    ]
}


def _write_users(path, users):
    path.write_text(yaml.safe_dump(users, allow_unicode=True), encoding="utf-8")


def _read_users(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))["users"]


@pytest.fixture(scope="module", autouse=True)
def users_file(tmp_path_factory):
    """Apunta auth/admin_users a un archivo temporal y restaura el original."""
    import app.routers.admin_users as admin_users
    import app.routers.auth as auth

    path = tmp_path_factory.mktemp("config") / "users.yaml"
    _write_users(path, _USERS)

    original_file = auth.USERS_FILE
    original_cache = dict(auth._users_cache)
    original_admin_file = admin_users.USERS_FILE
    auth.USERS_FILE = path
    admin_users.USERS_FILE = path
    auth._users_cache.clear()
    yield path
    auth.USERS_FILE = original_file
    admin_users.USERS_FILE = original_admin_file
    auth._users_cache.clear()
    auth._users_cache.update(original_cache)


@pytest.fixture(autouse=True)
def reset_users(users_file):
    """Cada test parte de la configuración base (sin usuarios temporales)."""
    import app.routers.auth as auth

    _write_users(users_file, _USERS)
    auth._users_cache.clear()
    yield
    auth._users_cache.clear()


@pytest.fixture(scope="module")
def client():
    # Sin contexto: no se ejecuta el lifespan (evita sembrar devices/syslog).
    return TestClient(app)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _login(client, username: str, ip: str) -> str:
    r = client.post(
        "/api/auth/login",
        json={"username": username, "password": TEST_PASSWORD},
        headers={"X-Forwarded-For": ip},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _revoke(client, admin_token: str, username: str):
    return client.post(
        f"/api/admin/users/{username}/revoke-sessions",
        headers=_bearer(admin_token),
    )


REVOKED_DETAIL = "Sesión revocada; vuelve a entrar"


def test_login_revocar_invalida_token(client):
    bob = _login(client, "bob", "203.0.113.11")
    boss = _login(client, "boss", "203.0.113.11")

    assert client.get("/api/auth/me", headers=_bearer(bob)).status_code == 200

    r = _revoke(client, boss, "bob")
    assert r.status_code == 200, r.text
    assert r.json() == {"username": "bob", "token_version": 1}

    me = client.get("/api/auth/me", headers=_bearer(bob))
    assert me.status_code == 401
    assert me.json()["detail"] == REVOKED_DETAIL
    assert me.headers.get("www-authenticate") == "Bearer"

    # Ruta protegida normal (no solo /me) con el mismo token revocado.
    devices = client.get("/api/devices", headers=_bearer(bob))
    assert devices.status_code == 401
    assert devices.json()["detail"] == REVOKED_DETAIL

    # La revocación de bob no afecta a la sesión del admin.
    assert client.get("/api/auth/me", headers=_bearer(boss)).status_code == 200

    # Un login nuevo emite un token con la versión nueva y sí funciona.
    bob2 = _login(client, "bob", "203.0.113.11")
    assert verify_token(bob2)["tv"] == 1
    assert client.get("/api/auth/me", headers=_bearer(bob2)).status_code == 200


def test_refresh_con_token_revocado_no_emite_otro(client):
    bob = _login(client, "bob", "203.0.113.12")
    boss = _login(client, "boss", "203.0.113.12")
    assert _revoke(client, boss, "bob").status_code == 200

    r = client.post("/api/auth/refresh", headers=_bearer(bob))
    assert r.status_code == 401
    assert r.json()["detail"] == REVOKED_DETAIL
    assert "access_token" not in r.json()

    # Un token viejo no puede resucitar por refresh; el login nuevo sí vale.
    bob2 = _login(client, "bob", "203.0.113.12")
    refreshed = client.post("/api/auth/refresh", headers=_bearer(bob2))
    assert refreshed.status_code == 200, refreshed.text
    assert verify_token(refreshed.json()["access_token"])["tv"] == 1


def test_revocar_a_no_afecta_a_b(client):
    alice = _login(client, "alice", "203.0.113.13")
    bob = _login(client, "bob", "203.0.113.13")
    boss = _login(client, "boss", "203.0.113.13")

    assert _revoke(client, boss, "alice").json()["token_version"] == 1
    assert client.get("/api/auth/me", headers=_bearer(alice)).status_code == 401

    assert client.get("/api/auth/me", headers=_bearer(bob)).status_code == 200
    assert client.post("/api/auth/refresh", headers=_bearer(bob)).status_code == 200


def test_usuario_sin_token_version_sigue_funcionando(client, users_file):
    alice_raw = next(u for u in _read_users(users_file) if u["username"] == "alice")
    assert "token_version" not in alice_raw

    token = _login(client, "alice", "203.0.113.14")
    assert verify_token(token)["tv"] == 0
    assert client.get("/api/auth/me", headers=_bearer(token)).status_code == 200
    assert client.post("/api/auth/refresh", headers=_bearer(token)).status_code == 200

    # Un token legado (sin claim tv) se interpreta como versión 0.
    legacy = create_access_token({"sub": "alice", "role": "operator"})
    assert client.get("/api/auth/me", headers=_bearer(legacy)).status_code == 200

    # Pero la revocación también invalida los tokens legados de ese usuario.
    boss = _login(client, "boss", "203.0.113.14")
    assert _revoke(client, boss, "alice").status_code == 200
    assert client.get("/api/auth/me", headers=_bearer(legacy)).status_code == 401


def test_revocacion_auditada(client, monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(audit_svc, "log_event", lambda **kwargs: calls.append(kwargs))

    boss = create_access_token({"sub": "boss", "role": "admin", "tv": 0})
    r = _revoke(client, boss, "bob")
    assert r.status_code == 200, r.text

    events = [c for c in calls if c.get("action") == "user_revoke_sessions"]
    assert len(events) == 1
    event = events[0]
    assert event["username"] == "boss"
    assert "target=bob" in event["details"]
    assert "token_version=0->1" in event["details"]
    assert event["path"] == "/api/admin/users/bob/revoke-sessions"
    assert event["method"] == "POST"


def test_revocar_no_pierde_token_version_al_editar(client, users_file):
    boss = _login(client, "boss", "203.0.113.15")
    created = client.post(
        "/api/admin/users",
        json={
            "username": "tempedit",
            "password": TEST_PASSWORD,
            "role": "viewer",
            "name": "Temp Edit",
        },
        headers=_bearer(boss),
    )
    assert created.status_code == 201, created.text
    raw = next(u for u in _read_users(users_file) if u["username"] == "tempedit")
    assert raw["token_version"] == 0

    old = _login(client, "tempedit", "203.0.113.15")
    assert _revoke(client, boss, "tempedit").json()["token_version"] == 1

    # PUT no debe borrar ni reiniciar el contador de versión.
    updated = client.put(
        "/api/admin/users/tempedit",
        json={"name": "Editado"},
        headers=_bearer(boss),
    )
    assert updated.status_code == 200, updated.text
    raw = next(u for u in _read_users(users_file) if u["username"] == "tempedit")
    assert raw["token_version"] == 1

    assert client.get("/api/auth/me", headers=_bearer(old)).status_code == 401
    new = _login(client, "tempedit", "203.0.113.15")
    assert verify_token(new)["tv"] == 1
    assert client.get("/api/auth/me", headers=_bearer(new)).status_code == 200


def test_usuario_eliminado_sus_tokens_fallan(client):
    boss = _login(client, "boss", "203.0.113.16")
    created = client.post(
        "/api/admin/users",
        json={
            "username": "tempdel",
            "password": TEST_PASSWORD,
            "role": "viewer",
            "name": "Temp Del",
        },
        headers=_bearer(boss),
    )
    assert created.status_code == 201, created.text

    token = _login(client, "tempdel", "203.0.113.16")
    assert client.get("/api/auth/me", headers=_bearer(token)).status_code == 200

    deleted = client.delete("/api/admin/users/tempdel", headers=_bearer(boss))
    assert deleted.status_code == 204

    me = client.get("/api/auth/me", headers=_bearer(token))
    assert me.status_code == 401
    assert me.json()["detail"] == REVOKED_DETAIL
    assert client.get("/api/devices", headers=_bearer(token)).status_code == 401
    assert client.post("/api/auth/refresh", headers=_bearer(token)).status_code == 401


def test_revocar_inexistente_404_y_rol_insuficiente_403(client):
    boss = create_access_token({"sub": "boss", "role": "admin", "tv": 0})
    missing = _revoke(client, boss, "nadie")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Usuario 'nadie' no encontrado"

    viewer = create_access_token({"sub": "seer", "role": "viewer", "tv": 0})
    denied = _revoke(client, viewer, "bob")
    assert denied.status_code == 403
