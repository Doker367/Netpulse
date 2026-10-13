"""Tests de la Fase 2 (NOC): diff de backups, offsite, FortiOS y WebSSH."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import settings
from app.core.security import create_access_token
from app.main import app
from app.services import backup_offsite_svc, fortios_svc
from app.routers import config as config_router


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def _admin():
    return {"Authorization": f"Bearer {create_access_token({'sub': 'admin', 'role': 'admin'})}"}


# ── 2.2 Diff de backups ───────────────────────────────────────

def test_diff_backups(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config_router, "BACKUP_DIR", tmp_path)
    (tmp_path / "dev1_a.cfg").write_text("hostname r1\ninterface Gi0\n ip address 1.1.1.1\n", encoding="utf-8")
    (tmp_path / "dev1_b.cfg").write_text("hostname r1\ninterface Gi0\n ip address 2.2.2.2\nvlan 10\n", encoding="utf-8")

    r = client.get(
        "/api/config/dev1/backups/diff",
        params={"a": "dev1_a.cfg", "b": "dev1_b.cfg"},
        headers=_admin(),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["stats"]["added"] >= 1
    assert data["stats"]["removed"] >= 1
    types = {row["type"] for row in data["rows"]}
    assert "change" in types or ("add" in types and "del" in types)


def test_diff_backups_archivo_inexistente(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config_router, "BACKUP_DIR", tmp_path)
    r = client.get("/api/config/dev1/backups/diff",
                   params={"a": "no.cfg", "b": "tampoco.cfg"}, headers=_admin())
    assert r.status_code == 404


# ── 2.3 Offsite ───────────────────────────────────────────────

def test_offsite_deshabilitado(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OFFSITE_ENABLED", False)
    monkeypatch.setattr(backup_offsite_svc, "BACKUP_DIR", tmp_path)
    res = backup_offsite_svc.export_all()
    assert res["uploaded"] == 0
    assert "deshabilitado" in res.get("skipped", "")


def test_offsite_status_endpoint(client, monkeypatch):
    monkeypatch.setattr(settings, "OFFSITE_ENABLED", True)
    monkeypatch.setattr(settings, "OFFSITE_BACKEND", "sftp")
    monkeypatch.setattr(settings, "OFFSITE_SFTP_HOST", "backup.example.com")
    r = client.get("/api/config/offsite/status", headers=_admin())
    assert r.status_code == 200
    body = r.json()
    assert body["backend"] == "sftp"
    assert body["host"] == "backup.example.com"


# ── 2.4 FortiOS (parsing con datos simulados) ─────────────────

def _device():
    return {"id": "fw01", "hostname": "10.0.0.1", "port": 443,
            "credentials": {"api_token": "tok"}}


def test_fortios_status_parse(monkeypatch):
    monkeypatch.setattr(fortios_svc, "_get_json", lambda d, p, params=None: {
        "results": {"model_name": "FortiGate 60F", "hostname": "fw01",
                    "version": "v7.4.1", "serial": "FG60F0000000001", "uptime": 12345}
    })
    out = fortios_svc.status(_device())
    assert out["model"] == "FortiGate 60F"
    assert out["version"] == "v7.4.1"


def test_fortios_policies_parse(monkeypatch):
    monkeypatch.setattr(fortios_svc, "_get_json", lambda d, p, params=None: {
        "results": [{
            "policyid": 1, "name": "allow-web",
            "srcintf": [{"name": "wan1"}], "dstintf": [{"name": "internal"}],
            "srcaddr": [{"name": "all"}], "dstaddr": [{"name": "web servers"}],
            "service": [{"name": "HTTPS"}], "action": "accept", "status": "enable",
        }]
    })
    pols = fortios_svc.policies(_device())
    assert pols[0]["policyid"] == 1
    assert pols[0]["action"] == "accept"
    assert pols[0]["srcintf"] == ["wan1"]


def test_fortios_interfaces_parse(monkeypatch):
    monkeypatch.setattr(fortios_svc, "_get_json", lambda d, p, params=None: {
        "results": [{"name": "port1", "ip": {"ip": "192.168.1.1", "mask": "255.255.255.0"},
                     "status": "up", "type": "physical"}]
    })
    ifs = fortios_svc.interfaces(_device())
    assert ifs[0]["name"] == "port1"
    assert ifs[0]["ip"] == "192.168.1.1/255.255.255.0"


# ── 2.1 WebSSH: rechazo sin token válido ──────────────────────

def test_ws_terminal_token_invalido(client):
    from starlette.testclient import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/terminal/x?token=no-valido") as ws:
            ws.receive_text()
