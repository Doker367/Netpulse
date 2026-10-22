"""React y dashboard legado conviven sin perder las rutas del motor."""

from fastapi.testclient import TestClient

from app import main


def test_legacy_is_default_even_when_react_build_exists(monkeypatch):
    monkeypatch.delenv("NETPULSE_FRONTEND", raising=False)
    response = TestClient(main.app).get("/")
    assert response.status_code == 200
    assert "NetPulse — NOC Dashboard" in response.text


def test_legacy_is_always_available():
    response = TestClient(main.app).get("/legacy")
    assert response.status_code == 200
    assert "NetPulse — NOC Dashboard" in response.text
    assert "no-cache" in response.headers["cache-control"]


def test_modern_is_always_available(monkeypatch):
    monkeypatch.setenv("NETPULSE_FRONTEND", "legacy")
    response = TestClient(main.app).get("/modern")
    assert response.status_code == 200
    assert "NetPulse | Centro de operaciones" in response.text


def test_react_mode_is_opt_in_at_root(monkeypatch):
    monkeypatch.setenv("NETPULSE_FRONTEND", "react")
    response = TestClient(main.app).get("/")
    assert response.status_code == 200
    assert "NetPulse | Centro de operaciones" in response.text
    assert "/static/react/assets/" in response.text


def test_missing_build_falls_back_to_legacy(monkeypatch, tmp_path):
    monkeypatch.setenv("NETPULSE_FRONTEND", "react")
    monkeypatch.setattr(main, "_REACT_DASHBOARD", tmp_path / "not-built.html")
    response = TestClient(main.app).get("/")
    assert response.status_code == 200
    assert "NetPulse — NOC Dashboard" in response.text


def test_legacy_login_and_status_are_not_misleading(monkeypatch):
    monkeypatch.delenv("NETPULSE_FRONTEND", raising=False)
    html = TestClient(main.app).get("/").text
    assert '<form class="login-card"' in html
    assert 'role="alert"' in html
    # El login distingue credenciales inválidas, límite de intentos y caída de la API.
    for status in ("401", "429"):
        assert f"r.status === {status}" in html or f"r.status==={status}" in html or f"status: {status}" in html
    for key in ("login_err_401", "login_err_429", "login_err_conn"):
        assert key in html
    # Las alertas sin verificar se muestran como desconocidas, nunca como cero.
    assert "alertSummary" in html
    assert "state_unknown" in html
    assert "SYS OK" not in html
    assert "0 ALERTS" not in html


def test_legacy_overview_keeps_real_status_and_actions(monkeypatch):
    monkeypatch.delenv("NETPULSE_FRONTEND", raising=False)
    client = TestClient(main.app)
    html = client.get("/").text
    assert 'id="ops-state"' in html
    assert 'id="ops-focus-items"' in html
    assert "ops-signal" in html
    assert "focusable" in html
    assert 'id="grid"' in html
    assert 'onclick="showAddDevicePanel()"' in html
    assert 'onclick="swView(\'topology\')"' in html
    assert '/static/overview.css?v=2' in html
    css = client.get("/static/overview.css")
    assert css.status_code == 200
    assert "#v-dashboard .ops-hero" in css.text
    assert '.ops-hero[data-network-state="state_down"]' in css.text
    assert "prefers-reduced-motion" in css.text
