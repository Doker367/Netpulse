"""Tests del filtro de falsos positivos de deception con IA (Laya/stub)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import settings
from app.models.deception import DeceptionEventIn, DeceptionEventType
from app.services import audit_svc, notifications_svc
from app.services.ai import decision_svc
from app.services.ai.backends import StubBackend
from app.services.deception import alert_svc, fp_svc, store_svc


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
def _ai_on(monkeypatch):
    monkeypatch.setattr(settings, "AI_ENABLED", True)
    monkeypatch.setattr(settings, "AI_BACKEND", "stub")
    decision_svc.set_backend(StubBackend())
    decision_svc._cache.clear()
    monkeypatch.setattr(fp_svc, "DECEPTION_AI_FP_ENABLED", True)
    monkeypatch.setattr(fp_svc, "DECEPTION_AI_FP_SUPPRESS", True)
    monkeypatch.setattr(fp_svc, "DECEPTION_AI_FP_MIN_CONFIDENCE", 0.90)
    monkeypatch.setattr(alert_svc, "DECEPTION_ALERTS_ENABLED", True)
    monkeypatch.setattr(alert_svc, "DECEPTION_ALERT_COOLDOWN", 0)
    alert_svc._last_fired.clear()
    yield
    decision_svc.set_backend(None)
    decision_svc._cache.clear()
    alert_svc._last_fired.clear()


def _record(event_type, **kw):
    return store_svc.log_event(DeceptionEventIn(
        device_id=kw.pop("device_id", "srv-01"),
        src_ip=kw.pop("src_ip", "203.0.113.9"),
        proto=kw.pop("proto", "ssh"),
        type=event_type,
        **kw,
    ))


def test_evento_benigno_se_suprime(db_path, audit_tmp, captured):
    record = _record(
        DeceptionEventType.LOGIN_SUCCESS,
        username="svc",
        detail={"command": "nmap inventory health check monitor"},
    )
    fired = alert_svc.evaluate(record)
    assert fired == []
    assert captured == []  # no se notifica
    suppressed = alert_svc.recent_suppressed()
    assert any(a["action"] == "deception_alert_suppressed" for a in suppressed)


def test_intrusion_real_dispara_con_bloque_ai(db_path, audit_tmp, captured):
    record = _record(
        DeceptionEventType.LOGIN_SUCCESS,
        username="root",
        detail={"command": "hydra brute force root /etc/passwd reverse shell"},
    )
    fired = alert_svc.evaluate(record)
    assert len(fired) == 1
    assert "ai" in fired[0]
    assert fired[0]["ai"]["verdict"] == "malicious"
    assert captured and captured[0][0] == "deception_alert"


def test_baja_confianza_no_suprime(db_path, audit_tmp, captured, monkeypatch):
    # Umbral imposible → siempre "unknown" → fail-open (alerta normal).
    monkeypatch.setattr(fp_svc, "DECEPTION_AI_FP_MIN_CONFIDENCE", 0.999)
    record = _record(
        DeceptionEventType.LOGIN_SUCCESS,
        username="svc",
        detail={"command": "nmap inventory health check monitor"},
    )
    fired = alert_svc.evaluate(record)
    assert len(fired) == 1


def test_ia_deshabilitada_flujo_igual(db_path, audit_tmp, captured, monkeypatch):
    monkeypatch.setattr(settings, "AI_ENABLED", False)
    record = _record(DeceptionEventType.LOGIN_SUCCESS, username="root")
    fired = alert_svc.evaluate(record)
    assert len(fired) == 1
    assert "ai" not in fired[0]
