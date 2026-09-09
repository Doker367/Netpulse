"""Tests de Fase 2: caché de inventario, auditoría SQLite y límite de concurrencia."""

import sys
import threading
import time
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import audit_svc, inventory_svc, napalm_svc
from app.services import concurrency


# ── Caché de inventario ───────────────────────────────────────────────────

@pytest.fixture()
def dev_file(tmp_path, monkeypatch):
    f = tmp_path / "devices.yaml"
    f.write_text("devices: []\n")
    monkeypatch.setattr(inventory_svc, "DEVICES_FILE", f)
    inventory_svc.invalidate_cache()
    return f


def _mk(dev_id):
    return {
        "id": dev_id,
        "hostname": "10.0.0.1",
        "port": 23,
        "driver": "procurve",
        "protocol": "telnet",
        "username": "manager",
        "password": "x",
    }


def test_cache_refleja_escrituras(dev_file):
    inventory_svc.add_device(_mk("a"))
    inventory_svc.add_device(_mk("b"))
    ids = [d["id"] for d in inventory_svc.list_devices()]
    assert ids == ["a", "b"]


def test_cache_lee_cambios_externos(dev_file):
    inventory_svc.add_device(_mk("a"))
    # edición externa del YAML
    dev_file.write_text(yaml.safe_dump({"devices": [{
        "id": "ext", "hostname": "10.0.0.2", "port": 22,
        "driver": "ios", "protocol": "ssh",
        "credentials": {"username": "u", "password": ""},
    }]}))
    ids = [d["id"] for d in inventory_svc.list_devices()]
    assert ids == ["ext"]


def test_get_raw_devices_es_copia(dev_file):
    inventory_svc.add_device(_mk("a"))
    raw = inventory_svc.get_raw_devices()
    raw[0]["credentials"]["password"] = "touched"
    raw2 = inventory_svc.get_raw_devices()
    assert raw2[0]["credentials"]["password"] != "touched"


def test_napalm_carga_desde_cache(dev_file, monkeypatch):
    inventory_svc.add_device({**_mk("nap-dev"), "password": "secreto"})
    devices = napalm_svc._load_devices()
    # encriptado en disco, descifrado efímero en memoria
    raw = inventory_svc.get_raw_devices()
    assert raw[0]["credentials"]["password"].startswith("gAAAAA")
    dev = next(d for d in devices if d["id"] == "nap-dev")
    assert dev["credentials"]["password"] == "secreto"


# ── Auditoría SQLite ──────────────────────────────────────────────────────

@pytest.fixture()
def audit_db(tmp_path, monkeypatch):
    db = tmp_path / "audit.db"
    monkeypatch.setattr(audit_svc, "AUDIT_DB", db)
    monkeypatch.setattr(audit_svc, "AUDIT_DIR", tmp_path)
    return db


def test_audit_log_y_listado(audit_db):
    audit_svc.log_event(action="device_create", device_id="sw-1", username="admin", response_status=201)
    audit_svc.log_event(action="device_delete", device_id="sw-1", username="admin", response_status=204)
    events = audit_svc.get_events()
    assert len(events) == 2
    # más reciente primero
    assert events[0]["action"] == "device_delete"
    assert events[0]["success"] is True


def test_audit_filtros(audit_db):
    audit_svc.log_event(action="device_create", device_id="sw-1", username="admin")
    audit_svc.log_event(action="command_execute", device_id="sw-2", username="operator")
    only_operator = audit_svc.get_events(username="operator")
    assert len(only_operator) == 1
    assert only_operator[0]["username"] == "operator"
    only_sw1 = audit_svc.get_events(device_id="sw-1")
    assert len(only_sw1) == 1
    only_cmd = audit_svc.get_events(action="command_execute")
    assert len(only_cmd) == 1


def test_audit_paginacion(audit_db):
    for i in range(5):
        audit_svc.log_event(action="op", username="u")
    page = audit_svc.get_events(limit=2, offset=1)
    assert len(page) == 2


def test_audit_stats(audit_db):
    audit_svc.log_event(action="op", username="u")
    stats = audit_svc.get_audit_stats()
    assert stats["total_events"] == 1
    assert stats["file_path"].endswith("audit.db")


def test_audit_retain(audit_db):
    audit_svc.log_event(action="recent", username="u")
    # insertar un evento con timestamp viejo directamente
    import sqlite3
    audit_svc._ensure_db()
    with sqlite3.connect(str(audit_db)) as conn:
        conn.execute(
            "INSERT INTO audit (ts, action, username, success) VALUES (?,?,?,1)",
            ("2000-01-01T00:00:00+00:00", "ancient", "u"),
        )
    removed = audit_svc.retain(days=7)
    assert removed >= 1
    remaining = audit_svc.get_events()
    assert all(ev["action"] != "ancient" for ev in remaining)
    assert any(ev["action"] == "recent" for ev in remaining)


# ── Semáforo de concurrencia ──────────────────────────────────────────────

def test_limited_limita_concurrencia(monkeypatch):
    monkeypatch.setattr(concurrency, "_semaphore", threading.BoundedSemaphore(2))
    active = 0
    max_active = 0
    lock = threading.Lock()

    @concurrency.limited
    def op():
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1

    threads = [threading.Thread(target=op) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert max_active <= 2
