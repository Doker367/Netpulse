"""Tests de la Fase 1: hardening y correcciones críticas."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from starlette.requests import Request

from app.core.limiter import client_key
from app.core.security import create_access_token
from app.main import app
from app.middleware.metrics import MetricsMiddleware
from app.routers import alerts as alerts_router


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def _admin_token():
    return create_access_token({"sub": "admin", "role": "admin"})


def _operator_token():
    return create_access_token({"sub": "op", "role": "operator"})


# ── 1.1 /api/audit/stats protegido ────────────────────────────

def test_audit_stats_sin_token(client):
    assert client.get("/api/audit/stats").status_code in (401, 403)


def test_audit_stats_operator_denegado(client):
    r = client.get("/api/audit/stats", headers={"Authorization": f"Bearer {_operator_token()}"})
    assert r.status_code == 403


def test_audit_stats_admin_ok(client):
    r = client.get("/api/audit/stats", headers={"Authorization": f"Bearer {_admin_token()}"})
    assert r.status_code == 200


# ── 1.2 Webhook Alertmanager por token de servicio ────────────

def test_webhook_token_servicio_ok(client, monkeypatch):
    monkeypatch.setattr(alerts_router, "ALERTMANAGER_TOKEN", "s3cr3t-token")
    r = client.post(
        "/api/alerts/webhook",
        json={"alerts": []},
        headers={"X-Alertmanager-Token": "s3cr3t-token"},
    )
    assert r.status_code == 200, r.text


def test_webhook_bearer_token_servicio_ok(client, monkeypatch):
    monkeypatch.setattr(alerts_router, "ALERTMANAGER_TOKEN", "s3cr3t-token")
    r = client.post(
        "/api/alerts/webhook",
        json={"alerts": []},
        headers={"Authorization": "Bearer s3cr3t-token"},
    )
    assert r.status_code == 200, r.text


def test_webhook_token_incorrecto_denegado(client, monkeypatch):
    monkeypatch.setattr(alerts_router, "ALERTMANAGER_TOKEN", "s3cr3t-token")
    r = client.post(
        "/api/alerts/webhook",
        json={"alerts": []},
        headers={"X-Alertmanager-Token": "otro"},
    )
    assert r.status_code in (401, 403)


def test_webhook_sin_token_configurado_exige_jwt(client, monkeypatch):
    monkeypatch.setattr(alerts_router, "ALERTMANAGER_TOKEN", "")
    assert client.post("/api/alerts/webhook", json={"alerts": []}).status_code in (401, 403)
    r = client.post(
        "/api/alerts/webhook",
        json={"alerts": []},
        headers={"Authorization": f"Bearer {_admin_token()}"},
    )
    assert r.status_code == 200, r.text


# ── 1.3 Orden de middlewares (Metrics el más externo) ─────────

def test_metrics_middleware_es_el_mas_externo():
    # El último añadido queda en el índice 0 (outermost).
    assert app.user_middleware[0].cls is MetricsMiddleware


# ── 1.5 Rate limiting respeta X-Forwarded-For ─────────────────

def _req(headers, client_ip="9.9.9.9"):
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "server": ("test", 80),
        "scheme": "http",
        "headers": headers,
        "client": (client_ip, 12345),
    }
    return Request(scope)


def test_limiter_usa_xff():
    req = _req([(b"x-forwarded-for", b"1.2.3.4, 5.6.7.8")])
    assert client_key(req) == "1.2.3.4"


def test_limiter_usa_x_real_ip():
    req = _req([(b"x-real-ip", b"8.8.8.8")])
    assert client_key(req) == "8.8.8.8"


def test_limiter_fallback_ip_directa():
    assert client_key(_req([])) == "9.9.9.9"
