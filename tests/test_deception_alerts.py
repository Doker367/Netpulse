"""Tests del motor de alertas de deception (Fase H)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.deception import DeceptionEventIn, DeceptionEventType
from app.services import audit_svc, notifications_svc
from app.services.deception import alert_svc, store_svc


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    monkeypatch.setattr(store_svc, "DB_PATH", tmp_path / "deception.db")


@pytest.fixture()
def audit_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_svc, "AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(audit_svc, "AUDIT_DB", tmp_path / "audit" / "audit.db")


@pytest.fixture()
def captured(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(
        notifications_svc, "notify_event",
        lambda event_type, data: calls.append((event_type, data)),
    )
    return calls


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    monkeypatch.setattr(alert_svc, "DECEPTION_ALERTS_ENABLED", True)
    monkeypatch.setattr(alert_svc, "DECEPTION_ALERT_COOLDOWN", 60)
    monkeypatch.setattr(alert_svc, "DECEPTION_BRUTE_FORCE_THRESHOLD", 3)
    monkeypatch.setattr(alert_svc, "DECEPTION_BRUTE_FORCE_WINDOW", 60)
    alert_svc._last_fired.clear()
    yield
    alert_svc._last_fired.clear()


def _record(event_type, **kw):
    return store_svc.log_event(DeceptionEventIn(
        device_id=kw.pop("device_id", "srv-01"),
        src_ip=kw.pop("src_ip", "203.0.113.9"),
        proto=kw.pop("proto", "ssh"),
        type=event_type,
        **kw,
    ))


def test_login_success_dispara_alerta(db_path, audit_tmp, captured):
    record = _record(DeceptionEventType.LOGIN_SUCCESS, username="root", success=True)
    fired = alert_svc.evaluate(record)
    assert len(fired) == 1
    assert fired[0]["rule"] == "login_success"
    assert fired[0]["severity"] == "high"
    assert captured and captured[0][0] == "deception_alert"
    # Queda en la auditoría
    alerts = alert_svc.recent()
    assert any(a["action"] == "deception_alert" for a in alerts)


def test_file_download_es_critical(db_path, audit_tmp, captured):
    fired = alert_svc.evaluate(_record(
        DeceptionEventType.FILE_DOWNLOAD, detail={"url": "http://x/malware.bin"}
    ))
    assert fired and fired[0]["severity"] == "critical"


def test_comando_no_alerta_por_defecto(db_path, audit_tmp, captured):
    assert alert_svc.evaluate(_record(
        DeceptionEventType.COMMAND, detail={"command": "whoami"}
    )) == []


def test_cooldown_evita_duplicados(db_path, audit_tmp, captured):
    rec = _record(DeceptionEventType.LOGIN_SUCCESS, username="root")
    assert len(alert_svc.evaluate(rec)) == 1
    assert alert_svc.evaluate(rec) == []


def test_fuerza_bruta(db_path, audit_tmp, captured):
    for _ in range(3):
        _record(DeceptionEventType.LOGIN_ATTEMPT, username="admin")
    # El cuarto intento supera el umbral (3 en la ventana)
    fired = alert_svc.evaluate(_record(DeceptionEventType.LOGIN_ATTEMPT, username="admin"))
    assert fired and fired[0]["rule"] == "brute_force"
