"""API keys de colector: emisión, binding, revocación y regresión de usuarios."""

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.main import app
from app.services.security import api_keys_svc

CLIENT = TestClient(app)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("NETPULSE_REMOTE_DB", str(tmp_path / "remote.db"))
    monkeypatch.setattr(api_keys_svc, "API_KEYS_FILE", tmp_path / "api_keys.json")
    monkeypatch.setattr("app.middleware.audit._log_async", lambda **kwargs: None)
    monkeypatch.setattr("app.middleware.audit._safe_log", lambda **kwargs: None)
    monkeypatch.setattr("app.routers.security_ext.audit_svc.log_event", lambda **kwargs: None)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def admin():
    return create_access_token({"sub": "admin", "role": "admin"})


def viewer():
    return create_access_token({"sub": "viewer", "role": "viewer"})


def create_collector(payload=None):
    body = payload or {
        "name": "collector-lima-01", "kind": "collector",
        "site_id": "site-lima", "collector_id": "collector-lima-01",
    }
    return CLIENT.post("/api/security/api-keys", headers=auth(admin()), json=body)


def test_create_collector_key():
    response = create_collector()
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["key"].startswith("np_")
    assert data["kind"] == "collector"
    assert data["role"] is None
    assert data["site_id"] == "site-lima"
    assert data["collector_id"] == "collector-lima-01"
    listed = CLIENT.get("/api/security/api-keys", headers=auth(admin())).json()
    assert "key" not in listed[0]
    assert listed[0]["kind"] == "collector"


def test_collector_key_requires_site_and_collector():
    response = create_collector({"name": "x", "kind": "collector"})
    assert response.status_code == 422
    response = create_collector({
        "name": "x", "kind": "collector", "site_id": "s", "collector_id": "c",
        "role": "viewer",
    })
    assert response.status_code == 422


def test_create_collector_key_requires_admin():
    response = CLIENT.post(
        "/api/security/api-keys",
        headers=auth(viewer()),
        json={"name": "x", "kind": "collector", "site_id": "s", "collector_id": "c"},
    )
    assert response.status_code == 403


def test_collector_whoami_and_read_denied():
    key = create_collector().json()["key"]
    who = CLIENT.get("/api/remote/whoami", headers=auth(key))
    assert who.status_code == 200
    assert who.json()["site_id"] == "site-lima"
    assert who.json()["collector_id"] == "collector-lima-01"
    assert CLIENT.get("/api/remote/sites", headers=auth(key)).status_code == 403
    assert CLIENT.get("/api/devices", headers=auth(key)).status_code == 403


def test_collector_key_revoked():
    created = create_collector().json()
    key = created["key"]
    assert CLIENT.delete(
        f"/api/security/api-keys/{created['id']}", headers=auth(admin())
    ).status_code == 204
    assert CLIENT.get("/api/remote/whoami", headers=auth(key)).status_code == 401


def test_user_key_contract_still_works():
    response = CLIENT.post(
        "/api/security/api-keys",
        headers=auth(admin()),
        json={"name": "ci", "role": "viewer"},
    )
    assert response.status_code == 201
    key = response.json()["key"]
    assert response.json()["kind"] == "user"
    assert CLIENT.get("/api/remote/sites", headers=auth(key)).status_code == 200
    assert CLIENT.get("/api/devices", headers=auth(key)).status_code == 200


def test_unknown_kind_rejected():
    response = create_collector({"name": "x", "kind": "otro"})
    assert response.status_code == 422
