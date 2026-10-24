"""Contrato API de disponibilidad: observaciones nativas, no Prometheus."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.main import app
from app.services import report_svc

CLIENT = TestClient(app)


def auth(role):
    return {"Authorization": "Bearer " + create_access_token({"sub": role, "role": role})}


def window(hours=1):
    end = datetime.now(timezone.utc) - timedelta(minutes=2)
    return {"desde": (end - timedelta(hours=hours)).isoformat(), "hasta": end.isoformat()}


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    report_svc._cache.clear()
    monkeypatch.setattr(report_svc.inventory_svc, "list_devices", lambda: [
        {"id": "sw1", "group": "Core"}, {"id": "sw2", "group": "Edge"},
    ])


def test_happy_window_incidents_and_cache(monkeypatch):
    params = window()
    first = datetime.fromisoformat(params["desde"]).timestamp() + 120
    monkeypatch.setattr(report_svc.metrics_history_svc, "overview", lambda hours: {
        "devices": {"sw1": [
            {"ts": first, "online": True}, {"ts": first + 60, "online": False},
            {"ts": first + 120, "online": False}, {"ts": first + 180, "online": True},
        ]},
    })
    body = {**params, "device_ids": ["sw1"], "group": "Core"}
    response = CLIENT.post("/api/reports/availability", json=body, headers=auth("operator"))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["estado"] == "con_datos"
    equipment = result["equipos"][0]
    assert equipment["uptime_percent"] == 50.0
    assert equipment["cobertura"]["n_muestras"] == 4
    assert equipment["cobertura"]["parcial"] is True
    assert "de 1.00 h" in equipment["cobertura"]["descripcion"]
    assert equipment["incidentes"][0]["duracion_segundos"] == 60
    cached = CLIENT.get("/api/reports/availability/cached", params={
        **params, "device_ids": "sw1", "group": "Core",
    }, headers=auth("viewer"))
    assert cached.status_code == 200
    assert cached.json() == result
    assert CLIENT.post("/api/reports/availability", json=body, headers=auth("viewer")).status_code == 403


def test_missing_device_series_excluded(monkeypatch):
    params = window()
    ts = datetime.fromisoformat(params["desde"]).timestamp() + 60
    monkeypatch.setattr(report_svc.metrics_history_svc, "overview", lambda hours: {
        "devices": {"sw1": [{"ts": ts, "online": True}], "sw2": [{"ts": ts, "cpu": 3}]},
    })
    result = CLIENT.post("/api/reports/availability", json=params, headers=auth("admin")).json()
    assert [device["device_id"] for device in result["equipos"]] == ["sw1"]
    assert result["excluidos"] == [{"device_id": "sw2", "reason": "sin serie online en la ventana"}]


@pytest.mark.parametrize("change", [
    lambda p: {**p, "desde": p["hasta"]},
    lambda p: {**p, "desde": "ayer"},
    lambda p: {**p, "hasta": "2026-01-01T00:00:00"},
])
def test_invalid_window(change):
    assert CLIENT.post("/api/reports/availability", json=change(window()),
                       headers=auth("operator")).status_code == 422


def test_too_long():
    assert CLIENT.post("/api/reports/availability", json=window(32 * 24),
                       headers=auth("operator")).status_code == 422


def test_empty_history_never_claims_uptime(monkeypatch):
    monkeypatch.setattr(report_svc.metrics_history_svc, "overview", lambda hours: {"devices": {}})
    monkeypatch.setattr(report_svc, "METRICS_ENABLED", False)
    result = CLIENT.post("/api/reports/availability", json=window(), headers=auth("operator")).json()
    assert result["estado"] == "sin_datos"
    assert result["equipos"] == []
    assert len(result["excluidos"]) == 2
    assert "recolección desactivada" in result["mensaje"]
    assert "uptime_percent" not in result


def test_cache_miss():
    assert CLIENT.get("/api/reports/availability/cached", params=window(),
                      headers=auth("viewer")).status_code == 404
