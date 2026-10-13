"""Tests de la red señuelo (Deception): topología, CRUD API y telemetría.

Se redirige el YAML y la base SQLite a rutas temporales y se usan tokens
generados directamente (sin pasar por /login) para no agotar el rate
limiter, siguiendo el patrón de tests/test_inventory_api.py.
"""

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.main import app
from app.models.deception import DeceptionEventIn, DeceptionEventType
from app.routers import deception as deception_router
from app.services.deception import store_svc, topology_svc

ADMIN = {"Authorization": "Bearer " + create_access_token({"sub": "superadmin", "role": "admin"})}
VIEWER = {"Authorization": "Bearer " + create_access_token({"sub": "viewer", "role": "viewer"})}

SAMPLE_NETWORK = {
    "segments": [
        {"id": "vlan-oficina", "vlan_id": 10, "cidr": "10.10.0.0/24", "gateway": "10.10.0.1"},
    ],
    "devices": [
        {
            "id": "pc-01",
            "role": "pc",
            "segment": "vlan-oficina",
            "ip": "10.10.0.21",
            "persona": {"vendor": "dell", "os": "windows", "hostname": "PC-01"},
            "services": [{"port": 22, "proto": "ssh", "engine": "native"}],
        }
    ],
}


@pytest.fixture()
def network_file(tmp_path, monkeypatch):
    f = tmp_path / "network.yaml"
    f.write_text(yaml.safe_dump(SAMPLE_NETWORK))
    monkeypatch.setattr(topology_svc, "NETWORK_FILE", f)
    topology_svc.invalidate_cache()
    return f


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    db = tmp_path / "deception.db"
    monkeypatch.setattr(store_svc, "DB_PATH", db)
    return db


@pytest.fixture()
def client():
    # Sin contexto: no se ejecuta el lifespan (evita arrancar collectors)
    return TestClient(app)


# ── Topología (unit) ─────────────────────────────────────────


def test_cargar_red_y_resumen(network_file):
    summary = topology_svc.summary()
    assert summary["segments"] == 1
    assert summary["devices"] == 1
    assert summary["devices_enabled"] == 1
    assert summary["by_role"] == {"pc": 1}


def test_add_device_segmento_inexistente_falla(network_file):
    with pytest.raises(ValueError, match="no existe"):
        topology_svc.add_device(
            {"id": "x", "role": "server", "segment": "no-existe", "ip": "10.0.0.1"}
        )


def test_crud_device_unit(network_file):
    topology_svc.add_device(
        {"id": "srv-01", "role": "server", "segment": "vlan-oficina", "ip": "10.10.0.30"}
    )
    assert topology_svc.get_device("srv-01")["role"] == "server"
    topology_svc.update_device("srv-01", {"ip": "10.10.0.31"})
    assert topology_svc.get_device("srv-01")["ip"] == "10.10.0.31"
    assert topology_svc.delete_device("srv-01") is True
    assert topology_svc.get_device("srv-01") is None


# ── Telemetría (unit) ────────────────────────────────────────


def test_store_evento_sesion_y_stats(db_path):
    store_svc.log_event(DeceptionEventIn(
        device_id="pc-01", src_ip="203.0.113.9", proto="ssh",
        type=DeceptionEventType.LOGIN_ATTEMPT, username="admin",
    ))
    store_svc.log_event(DeceptionEventIn(
        device_id="pc-01", src_ip="203.0.113.9", proto="ssh",
        type=DeceptionEventType.COMMAND, detail={"cmd": "uname -a"},
    ))

    events = store_svc.get_events()
    assert len(events) == 2
    assert events[0]["type"] == "command"

    sessions = store_svc.get_sessions()
    assert len(sessions) == 1
    assert sessions[0]["event_count"] == 2
    assert sessions[0]["command_count"] == 1

    stats = store_svc.get_stats()
    assert stats["events_total"] == 2
    assert stats["attackers_unique"] == 1
    assert stats["by_type"]["login_attempt"] == 1


def test_store_filtros(db_path):
    store_svc.log_event(DeceptionEventIn(
        device_id="pc-01", src_ip="1.1.1.1", type=DeceptionEventType.CONNECT,
    ))
    store_svc.log_event(DeceptionEventIn(
        device_id="srv-01", src_ip="2.2.2.2", type=DeceptionEventType.PORT_SCAN,
    ))
    assert len(store_svc.get_events(device_id="srv-01")) == 1
    assert len(store_svc.get_events(src_ip="1.1.1.1")) == 1
    assert len(store_svc.get_events(event_type="port_scan")) == 1


# ── API ──────────────────────────────────────────────────────


def test_api_network_y_status(network_file, client):
    r = client.get("/api/deception/network", headers=VIEWER)
    assert r.status_code == 200, r.text
    assert len(r.json()["devices"]) == 1

    s = client.get("/api/deception/status", headers=VIEWER)
    assert s.status_code == 200
    assert s.json()["devices"] == 1


def test_api_crud_device(network_file, client):
    payload = {
        "id": "cam-01", "role": "camera", "segment": "vlan-oficina",
        "ip": "10.10.0.40", "services": [{"port": 554, "proto": "rtsp"}],
    }
    r = client.post("/api/deception/devices", json=payload, headers=ADMIN)
    assert r.status_code == 201, r.text

    r = client.get("/api/deception/devices/cam-01", headers=VIEWER)
    assert r.status_code == 200

    r = client.delete("/api/deception/devices/cam-01", headers=ADMIN)
    assert r.status_code == 204


def test_api_viewer_no_puede_crear(network_file, client):
    r = client.post(
        "/api/deception/devices",
        json={"id": "x", "role": "pc", "segment": "vlan-oficina", "ip": "10.10.0.50"},
        headers=VIEWER,
    )
    assert r.status_code == 403


def test_api_ingest_con_token(db_path, monkeypatch, client):
    monkeypatch.setattr(deception_router, "DECEPTION_INGEST_TOKEN", "s3cret")
    body = {
        "device_id": "pc-01", "src_ip": "203.0.113.9", "proto": "ssh",
        "type": "login_success", "username": "root", "success": True,
    }
    r = client.post("/api/deception/ingest", json=body,
                    headers={"X-Deception-Token": "s3cret"})
    assert r.status_code == 202, r.text

    events = client.get("/api/deception/events", headers=VIEWER).json()
    assert events[0]["device_id"] == "pc-01"
    assert events[0]["success"] is True


def test_api_ingest_token_incorrecto(db_path, monkeypatch, client):
    monkeypatch.setattr(deception_router, "DECEPTION_INGEST_TOKEN", "s3cret")
    r = client.post(
        "/api/deception/ingest",
        json={"device_id": "pc-01", "src_ip": "1.2.3.4"},
        headers={"X-Deception-Token": "malo"},
    )
    assert r.status_code in (401, 403)


def test_api_timeline_y_csv(db_path, client):
    store_svc.log_event(DeceptionEventIn(
        device_id="pc-01", src_ip="203.0.113.9", proto="ssh",
        type=DeceptionEventType.LOGIN_ATTEMPT, username="admin",
    ))
    tl = client.get("/api/deception/timeline?minutes=30", headers=VIEWER)
    assert tl.status_code == 200
    assert isinstance(tl.json(), list)

    csv = client.get("/api/deception/events.csv", headers=VIEWER)
    assert csv.status_code == 200
    assert "device_id" in csv.text
    assert "pc-01" in csv.text


def test_api_simulate(network_file, db_path, client):
    r = client.post("/api/deception/simulate?count=3", headers=ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["events"] >= 4
    events = client.get("/api/deception/events?limit=50", headers=VIEWER).json()
    assert len(events) >= 4
