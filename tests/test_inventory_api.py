"""Tests de la API de inventario (CRUD) sin tocar config/devices.yaml.

Se redirige DEVICES_FILE a un archivo temporal y se usan tokens
generados directamente (sin pasar por /login) para no agotar el rate limiter.
"""

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.main import app
from app.services import inventory_svc

ADMIN = {"Authorization": "Bearer " + create_access_token({"sub": "superadmin", "role": "admin"})}
OPERATOR = {"Authorization": "Bearer " + create_access_token({"sub": "operator", "role": "operator"})}


@pytest.fixture()
def device_file(tmp_path, monkeypatch):
    f = tmp_path / "devices.yaml"
    f.write_text("devices: []\n")
    monkeypatch.setattr(inventory_svc, "DEVICES_FILE", f)
    return f


@pytest.fixture()
def client():
    # Sin contexto: no se ejecuta el lifespan (evita sembrar devices reales / syslog)
    return TestClient(app)


@pytest.fixture()
def legacy_device():
    return {
        "id": "legacy-telnet-01",
        "hostname": "10.10.10.10",
        "port": 23,
        "driver": "procurve",
        "protocol": "telnet",
        "type": "switch",
        "group": "Access",
        "username": "manager",
        "password": "secret123",
        "enable_password": "",
        "netmiko_device_type": "hp_procurve_telnet",
    }


def test_list_devices_vacio(device_file, client):
    r = client.get("/api/devices", headers=ADMIN)
    assert r.status_code == 200
    assert r.json() == []


def test_create_device_telnet(device_file, client, legacy_device):
    r = client.post("/api/devices", json=legacy_device, headers=ADMIN)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"] == "legacy-telnet-01"
    assert body["protocol"] == "telnet"
    assert body["netmiko_device_type"] == "hp_procurve_telnet"
    # Nunca exponer credenciales en la respuesta
    assert "password" not in body
    assert "credentials" not in body


def test_create_y_listar_y_get(device_file, client, legacy_device):
    client.post("/api/devices", json=legacy_device, headers=ADMIN)
    listed = client.get("/api/devices", headers=OPERATOR).json()
    assert any(d["id"] == "legacy-telnet-01" for d in listed)
    one = client.get("/api/devices/legacy-telnet-01", headers=OPERATOR)
    assert one.status_code == 200
    assert one.json()["driver"] == "procurve"


def test_update_device(device_file, client, legacy_device):
    client.post("/api/devices", json=legacy_device, headers=ADMIN)
    r = client.put("/api/devices/legacy-telnet-01",
                   json={"netmiko_device_type": "cisco_ios_telnet", "enable_password": "enable123"},
                   headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["netmiko_device_type"] == "cisco_ios_telnet"


def test_delete_device(device_file, client, legacy_device):
    client.post("/api/devices", json=legacy_device, headers=ADMIN)
    r = client.delete("/api/devices/legacy-telnet-01", headers=ADMIN)
    assert r.status_code == 204
    listed = client.get("/api/devices", headers=ADMIN).json()
    assert listed == []


def test_password_cifrado_en_disco(device_file, client, legacy_device):
    client.post("/api/devices", json=legacy_device, headers=ADMIN)
    raw = yaml.safe_load(device_file.read_text())
    dev = raw["devices"][0]
    pw = dev["credentials"]["password"]
    assert pw.startswith("gAAAAA")  # Fernet
