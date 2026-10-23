"""Contrato de retención con HTTP y disco/Prometheus aislados por mocks."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.main import app
from app.services import audit_svc, retention_svc

CLIENT = TestClient(app)


def auth(role):
    return {"Authorization": "Bearer " + create_access_token({"sub": role, "role": role})}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    db = tmp_path / "audit.db"
    monkeypatch.setattr(audit_svc, "AUDIT_DB", db)
    monkeypatch.setattr(audit_svc, "AUDIT_DIR", tmp_path)
    monkeypatch.setattr(retention_svc.settings, "BACKUP_DIR", tmp_path / "backups")
    # Middleware general asíncrono no debe introducir eventos ni escribir fuera de tmp_path.
    monkeypatch.setattr("app.middleware.audit._log_async", lambda **kwargs: None)
    monkeypatch.setattr("app.middleware.audit._safe_log", lambda **kwargs: None)
    yield


def seed_audit(days):
    audit_svc._ensure_db()
    with sqlite3.connect(audit_svc.AUDIT_DB) as conn:
        for i, age in enumerate(days):
            ts = (datetime.now(timezone.utc) - timedelta(days=age)).isoformat()
            conn.execute("INSERT INTO audit (ts, action, username) VALUES (?, ?, ?)",
                         (ts, f"event_{i}", "tester"))


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.data).encode()


def test_inventory_all_measured(monkeypatch, tmp_path):
    monkeypatch.setattr(retention_svc.urllib.request, "urlopen", lambda *a, **k: FakeResponse({
        "status": "success", "data": {"storage.tsdb.retention.time": "15d",
                                       "storage.tsdb.retention.size": "10GB"},
    }))
    seed_audit([2, 8])
    folder = tmp_path / "backups"
    folder.mkdir()
    (folder / "node.cfg").write_bytes(b"12345")
    response = CLIENT.get("/api/retention/inventory", headers=auth("viewer"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["prometheus"] == {"retention_time": "15d", "retention_size": "10GB",
                                   "configurado": True, "estado": "con datos"}
    assert body["audit"]["registros"] == 2
    assert body["audit"]["mas_viejo"] is not None
    assert body["backups"]["archivos"] == 1
    assert body["backups"]["uso_bytes"] == 5
    assert body["backups"]["mas_viejo"] is not None
    assert body["backups"]["ruta"] == str(folder)
    listed = CLIENT.get("/api/retention/backups", headers=auth("viewer"))
    assert listed.status_code == 200
    assert listed.json()["backups"] == [{"nombre": "node.cfg", "tamano_bytes": 5,
                                         "fecha": body["backups"]["mas_viejo"]}]


def test_inventory_missing_services_are_explicit(monkeypatch):
    def offline(*args, **kwargs):
        raise ConnectionError("conexión rechazada")

    monkeypatch.setattr(retention_svc.urllib.request, "urlopen", offline)
    response = CLIENT.get("/api/retention/inventory", headers=auth("viewer"))
    assert response.status_code == 200
    body = response.json()
    assert body["prometheus"] == {"retention_time": None, "retention_size": None,
                                   "configurado": False,
                                   "estado": "no disponible: conexión rechazada"}
    assert body["audit"] == {"registros": None, "mas_viejo": None, "estado": "sin medir"}
    assert body["backups"] == {"archivos": 0, "uso_bytes": 0, "mas_viejo": None,
                               "ruta": None, "estado": "backups no configurados"}
    listed = CLIENT.get("/api/retention/backups", headers=auth("viewer"))
    assert listed.status_code == 200
    assert listed.json()["estado"] == "backups no configurados"
    assert listed.json()["backups"] == []


def test_prometheus_invalid_status_not_defaults(monkeypatch):
    monkeypatch.setattr(retention_svc.urllib.request, "urlopen", lambda *a, **k: FakeResponse({
        "status": "error", "error": "flags no accesibles",
    }))
    prometheus = CLIENT.get("/api/retention/inventory", headers=auth("viewer")).json()["prometheus"]
    assert prometheus == {"retention_time": None, "retention_size": None,
                          "configurado": False, "estado": "no disponible: flags no accesibles"}


def test_dry_run_does_not_delete(monkeypatch):
    seed_audit([120, 100, 1])
    before = audit_svc.get_audit_stats()["total_events"]
    response = CLIENT.post("/api/retention/audit-prune", json={"dias": 90, "dry_run": True},
                           headers=auth("admin"))
    assert response.status_code == 200, response.text
    assert response.json()["borraria"] == 2
    assert [item["action"] for item in response.json()["muestras"]] == ["event_0", "event_1"]
    assert audit_svc.get_audit_stats()["total_events"] == before


def test_prune_deletes_only_old_and_logs_action():
    seed_audit([120, 100, 1])
    response = CLIENT.post("/api/retention/audit-prune", json={"dias": 90, "dry_run": False},
                           headers=auth("admin"))
    assert response.status_code == 200, response.text
    assert response.json() == {"borrados": 2, "kept": 2}
    events = audit_svc.get_events()
    assert {event["action"] for event in events} == {"event_2", "audit_prune"}
    assert next(event for event in events if event["action"] == "audit_prune")["username"] == "admin"


@pytest.mark.parametrize("body", [
    {"dias": 0, "dry_run": True}, {"dias": -1, "dry_run": False},
    {"dias": "3", "dry_run": True}, {"dias": 3.2, "dry_run": False},
    {"dias": True, "dry_run": True},
])
def test_invalid_days_422(body):
    assert CLIENT.post("/api/retention/audit-prune", json=body,
                       headers=auth("admin")).status_code == 422


def test_missing_dry_run_422():
    response = CLIENT.post("/api/retention/audit-prune", json={"dias": 90}, headers=auth("admin"))
    assert response.status_code == 422
    assert "se requiere dry_run: obtén primero la lista de lo que se borraría" in str(response.json()["detail"])


def test_days_outside_date_range_422():
    seed_audit([1])
    response = CLIENT.post("/api/retention/audit-prune", json={"dias": 999999999, "dry_run": False},
                           headers=auth("admin"))
    assert response.status_code == 422
    assert response.json() == {"detail": "dias fuera de rango"}
    assert audit_svc.get_audit_stats()["total_events"] == 1


def test_rbac():
    assert CLIENT.post("/api/retention/audit-prune", json={"dias": 90, "dry_run": False},
                       headers=auth("viewer")).status_code == 403
    assert CLIENT.post("/api/retention/audit-prune", json={"dias": 90, "dry_run": True}).status_code == 401
    assert CLIENT.get("/api/retention/inventory").status_code == 401
    assert CLIENT.get("/api/retention/backups").status_code == 401


def test_invalid_backups_path_is_503(tmp_path):
    folder = tmp_path / "backups"
    folder.write_text("no es carpeta")
    response = CLIENT.get("/api/retention/backups", headers=auth("viewer"))
    assert response.status_code == 503
    assert response.json() == {"detail": "Backups no disponibles: la ruta no es un directorio local"}


def test_audit_unavailable_prevents_prune():
    response = CLIENT.post("/api/retention/audit-prune", json={"dias": 90, "dry_run": False},
                           headers=auth("admin"))
    assert response.status_code == 503
    assert response.json() == {"detail": "Auditoría sin medir: base de datos no disponible"}
