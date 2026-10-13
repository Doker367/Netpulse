"""Tests del router de monitoreo unificado (sin dependencias externas)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.main import app

VIEWER = {"Authorization": "Bearer " + create_access_token({"sub": "viewer", "role": "viewer"})}


def test_monitoring_overview_shape():
    client = TestClient(app)
    r = client.get("/api/monitoring/overview", headers=VIEWER)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["services"]) >= {"prometheus", "loki", "grafana", "alertmanager"}
    for svc in body["services"].values():
        assert "up" in svc and "url" in svc
    assert "metrics" in body and "targets" in body and "links" in body
    assert body["links"]["grafana_dashboard"].startswith("http")


def test_monitoring_requires_auth():
    client = TestClient(app)
    assert client.get("/api/monitoring/overview").status_code in (401, 403)


def test_monitoring_series_sin_prometheus():
    client = TestClient(app)
    r = client.get("/api/monitoring/series?query=up&minutes=5", headers=VIEWER)
    assert r.status_code == 200
    # Sin Prometheus accesible devuelve estructura vacía, no un 500
    assert "series" in r.json()
