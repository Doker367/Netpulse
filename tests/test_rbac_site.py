"""RBAC por grupo real de inventario: compatibilidad y rechazo por dispositivo."""

import pytest
import yaml
from fastapi.testclient import TestClient

from app.core.security import create_access_token, hash_password, verify_token
from app.main import app
from app.services import inventory_svc


def headers(role="admin", **claims):
    return {"Authorization": "Bearer " + create_access_token({"sub": "tester", "role": role, **claims})}


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    import app.routers.admin_users as admin_users
    import app.routers.auth as auth

    devices = tmp_path / "devices.yaml"
    devices.write_text(yaml.safe_dump({"devices": [
        {"id": "core-1", "hostname": "127.0.0.1", "driver": "ios", "group": "Core"},
        {"id": "dmz-1", "hostname": "127.0.0.1", "driver": "ios", "group": "DMZ"},
        {"id": "none-1", "hostname": "127.0.0.1", "driver": "ios"},
    ]}))
    monkeypatch.setattr(inventory_svc, "DEVICES_FILE", devices)
    inventory_svc.invalidate_cache()
    users = tmp_path / "users.yaml"
    users.write_text(yaml.safe_dump({"users": [{"username": "admin", "role": "admin",
                                                "password_hash": hash_password("secure-pass")}] }))
    monkeypatch.setattr(auth, "USERS_FILE", users)
    monkeypatch.setattr(admin_users, "USERS_FILE", users)
    auth._users_cache.clear()
    yield TestClient(app)
    auth._users_cache.clear()
    inventory_svc.invalidate_cache()


def test_legacy_token_and_read_unrestricted(isolated, monkeypatch):
    from app.routers import command
    from app.services.napalm_svc import NapalmResult
    called = []

    def run(device_id, commands):
        called.append(device_id)
        result = NapalmResult(device_id)
        result.success = True
        result.data = [{"command": commands[0], "output": "ok"}]
        return result

    monkeypatch.setattr(command.napalm_svc, "run_commands", run)
    monkeypatch.setattr(command.audit_svc, "log_event", lambda **kw: None)
    token = create_access_token({"sub": "tester", "role": "operator"})
    assert "scope_group" not in verify_token(token) and "scope_sites" not in verify_token(token)
    legacy = {"Authorization": f"Bearer {token}"}
    assert isolated.post("/api/command/core-1", json={"commands": ["show version"]}, headers=legacy).status_code == 200
    assert called == ["core-1"]
    assert len(isolated.get("/api/devices", headers=headers(role="viewer", scope_group="dmz")).json()) == 3


def test_command_scope_and_bulk_rejections(isolated, monkeypatch):
    from app.routers import command
    from app.services.napalm_svc import NapalmResult
    called = []

    def run(device_id, commands):
        called.append(device_id)
        result = NapalmResult(device_id)
        result.success = True
        result.data = []
        return result

    monkeypatch.setattr(command.napalm_svc, "run_commands", run)
    monkeypatch.setattr(command.audit_svc, "log_event", lambda **kw: None)
    h = headers(role="operator", scope_group="dmz")
    denied = isolated.post("/api/command/core-1", json={"commands": ["show version"]}, headers=h)
    assert denied.status_code == 403
    assert denied.json()["detail"] == "device fuera de tu scope: Core"
    assert called == []
    assert isolated.post("/api/command/dmz-1", json={"commands": ["show version"]}, headers=h).status_code == 200
    mixed = isolated.post("/api/command/bulk", json={"device_ids": ["core-1", "dmz-1", "none-1"],
                                                     "commands": ["show version"]}, headers=h)
    assert mixed.status_code == 200, mixed.text
    assert mixed.json()["success_count"] == 1 and mixed.json()["failed_count"] == 2
    assert mixed.json()["errors"] == [
        {"device_id": "core-1", "error": "device fuera de tu scope: Core"},
        {"device_id": "none-1", "error": "device fuera de tu scope: sin grupo"},
    ]
    assert called == ["dmz-1", "dmz-1"]


def test_config_and_device_mutations(isolated, monkeypatch):
    from app.routers import config
    calls = []
    from app.services.napalm_svc import NapalmResult

    def save(device_id):
        calls.append(device_id)
        result = NapalmResult(device_id)
        result.success = True
        result.data = []
        return result

    monkeypatch.setattr(config.napalm_svc, "save_config", save)
    h = headers(scope_group="dmz")
    assert isolated.post("/api/config/core-1/save", headers=h).json()["detail"] == "device fuera de tu scope: Core"
    assert isolated.post("/api/config/core-1/deploy", json={"candidate_config": "x"}, headers=h).status_code == 403
    assert calls == []
    assert isolated.post("/api/config/dmz-1/save", headers=h).status_code == 200
    assert calls == ["dmz-1"]
    payload = {"id": "new", "hostname": "127.0.0.1", "driver": "ios", "group": "Core"}
    assert isolated.post("/api/devices", json=payload, headers=h).status_code == 403
    payload["group"] = "dmz"
    assert isolated.post("/api/devices", json=payload, headers=h).status_code == 201
    assert isolated.put("/api/devices/core-1", json={"hostname": "other"}, headers=h).status_code == 403
    assert isolated.delete("/api/devices/core-1", headers=h).status_code == 403
    assert isolated.put("/api/devices/new", json={"group": "Core"}, headers=h).status_code == 403
    assert isolated.put("/api/devices/new", json={"hostname": "127.0.0.2"}, headers=h).status_code == 200
    assert isolated.get("/api/devices/new", headers=h).json()["group"] == "dmz"
    assert isolated.delete("/api/devices/new", headers=h).status_code == 204


def test_admin_assigns_scope_login_and_viewer_denied(isolated):
    user = {"username": "field", "name": "Field", "password": "secure-pass", "role": "operator",
            "scope_group": "dmz", "scope_sites": ["Edge"]}
    denied = isolated.post("/api/admin/users", json=user, headers=headers(role="viewer"))
    assert denied.status_code == 403
    created = isolated.post("/api/admin/users", json=user, headers=headers())
    assert created.status_code == 201, created.text
    assert created.json()["scope_group"] == "dmz"
    assert "password" not in created.json()
    token = isolated.post("/api/auth/login", json={"username": "field", "password": "secure-pass"})
    assert token.status_code == 200, token.text
    claims = verify_token(token.json()["access_token"])
    assert claims["scope_group"] == "dmz" and claims["scope_sites"] == ["Edge"]
    assert isolated.put("/api/admin/users/field", json={"scope_sites": []}, headers=headers(role="viewer")).status_code == 403
    updated = isolated.put("/api/admin/users/field", json={"scope_group": None, "scope_sites": []}, headers=headers())
    assert updated.status_code == 200 and updated.json()["scope_sites"] == []
    assert verify_token(isolated.post("/api/auth/login", json={"username": "field", "password": "secure-pass"}).json()["access_token"])["scope_sites"] == []


def test_bulk_deploy_and_backup_skip_unauthorized(isolated, monkeypatch):
    from app.routers import bulk
    calls = []
    monkeypatch.setattr(bulk, "_deploy_single", lambda did, cfg, dry: calls.append(did) or
                        bulk.BulkDeployDeviceResult(device_id=did, success=True, committed=True))
    monkeypatch.setattr(bulk.nornir_svc, "bulk_backup", lambda ids: calls.extend(ids) or [])
    h = headers(scope_sites=["DMZ"])
    deployment = isolated.post("/api/bulk/deploy", json={"device_ids": ["core-1", "dmz-1"],
                                                       "candidate_config": "x"}, headers=h)
    assert deployment.status_code == 200, deployment.text
    assert deployment.json()["total"] == 2 and deployment.json()["failed"] == 1
    assert next(r for r in deployment.json()["results"] if r["device_id"] == "core-1")["error"] == "device fuera de tu scope: Core"
    backup = isolated.post("/api/bulk/backup", json=["core-1", "dmz-1"], headers=h)
    assert backup.status_code == 200, backup.text
    assert backup.json()["total"] == 2 and backup.json()["failed"] == 1
    assert backup.json()["errors"][0]["device_id"] == "core-1"
    all_backup = isolated.post("/api/bulk/backup", headers=h)
    assert all_backup.status_code == 200 and all_backup.json()["total"] == 3
    assert all_backup.json()["failed"] == 2
    assert calls == ["dmz-1", "dmz-1", "dmz-1"]
