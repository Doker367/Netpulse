"""RBAC por grupo en el scheduler: backups con scope y reporte global."""

import pytest
import yaml
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.main import app
from app.services import inventory_svc, scheduler_svc


def headers(role="operator", **claims):
    token = create_access_token({"sub": "tester", "role": role, **claims})
    return {"Authorization": f"Bearer {token}"}


def _job(job_id: str, device_id: str, job_type: str = "backup") -> dict:
    return {
        "job_id": job_id,
        "device_id": device_id,
        "job_type": job_type,
        "delay_minutes": 5 if job_type == "backup" else None,
        "interval_minutes": 30 if job_type == "recurring" else None,
        "created_at": "2026-01-01T00:00:00Z",
        "status": "scheduled",
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    devices = tmp_path / "devices.yaml"
    devices.write_text(yaml.safe_dump({"devices": [
        {"id": "core-1", "hostname": "127.0.0.1", "driver": "ios", "group": "Core"},
        {"id": "dmz-1", "hostname": "127.0.0.1", "driver": "ios", "group": "DMZ"},
        {"id": "none-1", "hostname": "127.0.0.1", "driver": "ios"},
    ]}))
    monkeypatch.setattr(inventory_svc, "DEVICES_FILE", devices)
    inventory_svc.invalidate_cache()
    yield TestClient(app)
    inventory_svc.invalidate_cache()


def test_backup_fuera_de_scope_no_programa(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        scheduler_svc, "schedule_backup",
        lambda device_id, delay: calls.append((device_id, delay)) or "job-1",
    )
    denied = client.post(
        "/api/scheduler/backup",
        json={"device_id": "core-1", "delay_minutes": 5},
        headers=headers(scope_group="dmz"),
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "device fuera de tu scope: Core"
    assert calls == []


def test_backup_autorizado_programa(client, monkeypatch):
    monkeypatch.setattr(scheduler_svc, "schedule_backup", lambda device_id, delay: "job-1")
    monkeypatch.setattr(scheduler_svc, "list_jobs", lambda: [_job("job-1", "dmz-1")])
    response = client.post(
        "/api/scheduler/backup",
        json={"device_id": "dmz-1", "delay_minutes": 5},
        headers=headers(scope_sites=["DMZ"]),
    )
    assert response.status_code == 201, response.text
    assert response.json()["device_id"] == "dmz-1"


def test_recurrente_aplica_scope(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        scheduler_svc, "schedule_recurring",
        lambda device_id, interval: calls.append(device_id) or "job-r",
    )
    assert client.post(
        "/api/scheduler/backup/recurring",
        json={"device_id": "core-1", "interval_minutes": 30},
        headers=headers(scope_group="dmz"),
    ).status_code == 403
    monkeypatch.setattr(
        scheduler_svc, "list_jobs", lambda: [_job("job-r", "dmz-1", "recurring")]
    )
    allowed = client.post(
        "/api/scheduler/backup/recurring",
        json={"device_id": "dmz-1", "interval_minutes": 30},
        headers=headers(scope_group="dmz"),
    )
    assert allowed.status_code == 201, allowed.text
    assert calls == ["dmz-1"]


def test_cancelar_job_valida_su_dispositivo(client, monkeypatch):
    cancelled = []

    def fake_cancel(job_id: str) -> bool:
        cancelled.append(job_id)
        return True

    monkeypatch.setattr(scheduler_svc, "cancel_job", fake_cancel)
    monkeypatch.setattr(scheduler_svc, "list_jobs", lambda: [
        _job("job-core", "core-1"),
        _job("job-dmz", "dmz-1"),
        _job("job-report", "all", "report"),
        _job("job-deleted", "gone-1"),
    ])

    h = headers(scope_group="dmz")
    denied = client.delete("/api/scheduler/jobs/job-core", headers=h)
    assert denied.status_code == 403
    assert denied.json()["detail"] == "device fuera de tu scope: Core"
    assert cancelled == []

    assert client.delete("/api/scheduler/jobs/job-dmz", headers=h).status_code == 204
    # El reporte es global por diseño: no tiene dispositivo que validar.
    assert client.delete("/api/scheduler/jobs/job-report", headers=h).status_code == 204
    assert cancelled == ["job-dmz", "job-report"]

    # Un job cuyo dispositivo ya no existe no se puede atribuir a un grupo:
    # el token restringido recibe 404 (mismo criterio que command/config).
    missing_device = client.delete("/api/scheduler/jobs/job-deleted", headers=h)
    assert missing_device.status_code == 404
    assert missing_device.json()["detail"] == "Dispositivo 'gone-1' no encontrado"

    # Token legado sin claims conserva el comportamiento anterior.
    legacy = client.delete("/api/scheduler/jobs/job-core", headers=headers())
    assert legacy.status_code == 204


def test_cancelar_job_inexistente_404(client, monkeypatch):
    monkeypatch.setattr(scheduler_svc, "list_jobs", lambda: [])
    monkeypatch.setattr(scheduler_svc, "cancel_job", lambda job_id: False)
    response = client.delete("/api/scheduler/jobs/nope", headers=headers(scope_group="dmz"))
    assert response.status_code == 404
    assert response.json()["detail"] == "Trabajo 'nope' no encontrado"


def test_reporte_global_no_se_restringe(client, monkeypatch):
    monkeypatch.setattr(scheduler_svc, "schedule_report", lambda delay, interval: "job-rep")
    monkeypatch.setattr(
        scheduler_svc, "list_jobs", lambda: [_job("job-rep", "all", "report")]
    )
    response = client.post(
        "/api/scheduler/report",
        json={"delay_minutes": 1},
        headers=headers(scope_group="dmz"),
    )
    assert response.status_code == 201, response.text
    assert response.json()["device_id"] == "all"


def test_listar_jobs_sigue_siendo_lectura_global(client, monkeypatch):
    monkeypatch.setattr(scheduler_svc, "list_jobs", lambda: [_job("job-core", "core-1")])
    response = client.get("/api/scheduler/jobs", headers=headers(scope_group="dmz"))
    assert response.status_code == 200
    assert response.json()[0]["device_id"] == "core-1"
