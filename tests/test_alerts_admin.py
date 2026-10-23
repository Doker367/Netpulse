"""Tests del router de administración de alertas (silencios + reglas).

No se toca la red real: se monkeypatchea ``alerts_admin_svc._request``
(la única puerta de salida HTTP del servicio) para simular Alertmanager
y Prometheus.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.main import app
from app.services import alerts_admin_svc, audit_svc

VIEWER = {"Authorization": "Bearer " + create_access_token({"sub": "viewer", "role": "viewer"})}
OPERATOR = {"Authorization": "Bearer " + create_access_token({"sub": "operator", "role": "operator"})}


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _no_audit(monkeypatch):
    """Evita escribir el audit trail real durante los tests."""
    monkeypatch.setattr(audit_svc, "log_event", lambda **kwargs: None)


# ── GET /silences ─────────────────────────────────────────────


def test_list_silences_feliz(monkeypatch):
    _no_audit(monkeypatch)
    now = datetime.now(timezone.utc)

    def fake_request(method, path, payload=None, base=None):
        assert method == "GET"
        assert path == "/api/v2/silences"
        return [
            {
                "id": "sil-active",
                "matchers": [{"name": "device_id", "value": "cisco-core-01", "isRegex": False}],
                "startsAt": _ts(now - timedelta(minutes=5)),
                "endsAt": _ts(now + timedelta(hours=2)),
                "createdBy": "admin",
                "comment": "mantenimiento",
                "status": {"state": "active"},
            },
            {
                "id": "sil-expired",
                "matchers": [{"name": "group", "value": "Core", "isRegex": False}],
                "startsAt": _ts(now - timedelta(hours=3)),
                "endsAt": _ts(now - timedelta(hours=1)),
                "createdBy": "admin",
                "comment": "viejo",
                "status": {"state": "expired"},
            },
        ]

    monkeypatch.setattr(alerts_admin_svc, "_request", fake_request)

    client = TestClient(app)
    r = client.get("/api/alerts/admin/silences", headers=VIEWER)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success"
    assert body["total"] == 1  # solo el activo, el expirado se filtra
    sil = body["silences"][0]
    assert sil["id"] == "sil-active"
    assert sil["createdBy"] == "admin"
    assert sil["comment"] == "mantenimiento"
    assert sil["matchers"] == [{"name": "device_id", "value": "cisco-core-01", "isRegex": False}]
    assert sil["expires_in"] > 0


# ── POST /silences ────────────────────────────────────────────


def test_create_silence_feliz(monkeypatch):
    _no_audit(monkeypatch)
    captured = {}

    def fake_request(method, path, payload=None, base=None):
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"silenceID": "sil-123"}

    monkeypatch.setattr(alerts_admin_svc, "_request", fake_request)

    client = TestClient(app)
    r = client.post(
        "/api/alerts/admin/silences",
        headers=OPERATOR,
        json={"device_id": "cisco-core-01", "duration": "2h", "comment": "mantenimiento"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "created"
    assert body["id"] == "sil-123"
    assert body["createdBy"] == "operator"  # sub del JWT

    payload = captured["payload"]
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v2/silences"
    assert payload["createdBy"] == "operator"
    assert payload["matchers"] == [{"name": "device_id", "value": "cisco-core-01", "isRegex": False}]
    assert payload["startsAt"] < payload["endsAt"]


def test_alertmanager_caido_503(monkeypatch):
    _no_audit(monkeypatch)

    def fake_request(method, path, payload=None, base=None):
        raise alerts_admin_svc.UpstreamUnavailable("conexión rechazada")

    monkeypatch.setattr(alerts_admin_svc, "_request", fake_request)

    client = TestClient(app)
    r = client.get("/api/alerts/admin/silences", headers=VIEWER)
    assert r.status_code == 503
    assert r.json()["detail"] == "Alertmanager no disponible: conexión rechazada"


def test_matchers_vacios_422(monkeypatch):
    _no_audit(monkeypatch)

    def must_not_call(method, path, payload=None, base=None):
        raise AssertionError("no debe llamarse a Alertmanager con matchers vacíos")

    monkeypatch.setattr(alerts_admin_svc, "_request", must_not_call)

    client = TestClient(app)
    r = client.post("/api/alerts/admin/silences", headers=OPERATOR, json={"duration": "2h"})
    assert r.status_code == 422
    assert "matcher" in r.json()["detail"].lower()


def test_duracion_invalida_422(monkeypatch):
    _no_audit(monkeypatch)
    client = TestClient(app)
    r = client.post(
        "/api/alerts/admin/silences",
        headers=OPERATOR,
        json={"device_id": "cisco-core-01", "duration": "30m"},
    )
    assert r.status_code == 422
    assert "Duración inválida" in r.json()["detail"]


# ── DELETE /silences/{id} ─────────────────────────────────────


def test_delete_silence_inexistente_404(monkeypatch):
    _no_audit(monkeypatch)

    def fake_request(method, path, payload=None, base=None):
        assert method == "DELETE"
        raise alerts_admin_svc.UpstreamNotFound("not found")

    monkeypatch.setattr(alerts_admin_svc, "_request", fake_request)

    client = TestClient(app)
    r = client.delete("/api/alerts/admin/silences/sil-nope", headers=OPERATOR)
    assert r.status_code == 404
    assert r.json()["detail"] == "Silencio no encontrado: not found"


def test_delete_silence_feliz(monkeypatch):
    _no_audit(monkeypatch)

    def fake_request(method, path, payload=None, base=None):
        assert method == "DELETE"
        assert path == "/api/v2/silence/sil-123"
        return {}

    monkeypatch.setattr(alerts_admin_svc, "_request", fake_request)

    client = TestClient(app)
    r = client.delete("/api/alerts/admin/silences/sil-123", headers=OPERATOR)
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "deleted", "id": "sil-123"}


# ── GET /rules ────────────────────────────────────────────────


def test_list_rules_feliz(monkeypatch):
    _no_audit(monkeypatch)

    def fake_request(method, path, payload=None, base=None):
        assert method == "GET"
        assert path == "/api/v1/rules"
        return {
            "status": "success",
            "data": {
                "groups": [
                    {
                        "name": "netpulse-devices",
                        "rules": [
                            {
                                "name": "DeviceDown",
                                "type": "alerting",
                                "query": "netpulse_devices_up == 0",
                                "duration": 300,
                                "labels": {"severity": "critical", "team": "netops"},
                            }
                        ],
                    }
                ]
            },
        }

    monkeypatch.setattr(alerts_admin_svc, "_request", fake_request)

    client = TestClient(app)
    r = client.get("/api/alerts/admin/rules", headers=VIEWER)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    rule = body["rules"][0]
    assert rule["name"] == "DeviceDown"
    assert rule["expr"] == "netpulse_devices_up == 0"
    assert rule["for"] == 300
    assert rule["severity"] == "critical"
    assert rule["labels"] == {"severity": "critical", "team": "netops"}


def test_rules_prometheus_caido_503(monkeypatch):
    _no_audit(monkeypatch)

    def fake_request(method, path, payload=None, base=None):
        raise alerts_admin_svc.UpstreamUnavailable("timeout")

    monkeypatch.setattr(alerts_admin_svc, "_request", fake_request)

    client = TestClient(app)
    r = client.get("/api/alerts/admin/rules", headers=VIEWER)
    assert r.status_code == 503
    assert r.json()["detail"] == "Prometheus no disponible: timeout"


# ── RBAC ──────────────────────────────────────────────────────


def test_rbac_viewer_no_puede_crear_403(monkeypatch):
    _no_audit(monkeypatch)

    def must_not_call(method, path, payload=None, base=None):
        raise AssertionError("viewer no debe llegar a crear un silencio")

    monkeypatch.setattr(alerts_admin_svc, "_request", must_not_call)

    client = TestClient(app)
    r = client.post(
        "/api/alerts/admin/silences",
        headers=VIEWER,
        json={"device_id": "cisco-core-01", "duration": "2h"},
    )
    assert r.status_code == 403


def test_silences_sin_token_401():
    client = TestClient(app)
    r = client.get("/api/alerts/admin/silences")
    assert r.status_code in (401, 403)
