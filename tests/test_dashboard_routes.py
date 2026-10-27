"""La interfaz HTML y la API siguen disponibles sin la ruta alternativa."""

from fastapi.testclient import TestClient

from app import main


def test_dashboard_is_the_only_root_interface_even_with_old_env_flag(monkeypatch):
    monkeypatch.setenv("NETPULSE_FRONTEND", "react")
    response = TestClient(main.app).get("/")
    assert response.status_code == 200
    assert "NetPulse — NMS Dashboard" in response.text
    assert "no-cache" in response.headers["cache-control"]


def test_legacy_alias_serves_the_same_panel():
    client = TestClient(main.app)
    root = client.get("/")
    alias = client.get("/legacy")
    assert alias.status_code == 200
    assert alias.text == root.text
    assert "no-cache" in alias.headers["cache-control"]


def test_removed_interface_and_its_static_build_are_not_served():
    client = TestClient(main.app)
    assert client.get("/modern").status_code == 404
    assert client.get("/static/react/index.html").status_code == 404


def test_dev_ui_origin_is_removed_but_grafana_origin_remains():
    client = TestClient(main.app)
    removed = client.get("/", headers={"Origin": "http://localhost:5173"})
    grafana = client.get("/", headers={"Origin": "http://localhost:3000"})
    assert "access-control-allow-origin" not in removed.headers
    assert grafana.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_dashboard_login_and_status_are_not_misleading():
    html = TestClient(main.app).get("/").text
    assert '<form class="login-card"' in html
    assert 'role="alert"' in html
    for status in ("401", "429"):
        assert (
            f"r.status === {status}" in html
            or f"r.status==={status}" in html
            or f"status: {status}" in html
        )
    for key in ("login_err_401", "login_err_429", "login_err_conn"):
        assert key in html
    assert "alertSummary" in html
    assert "state_unknown" in html
    assert "SYS OK" not in html
    assert "0 ALERTS" not in html


def test_dashboard_overview_keeps_real_status_and_actions():
    client = TestClient(main.app)
    html = client.get("/").text
    assert 'id="ops-state"' in html
    assert 'id="ops-focus-items"' in html
    assert "ops-signal" in html
    assert "focusable" in html
    assert 'id="grid"' in html
    assert 'onclick="showAddDevicePanel()"' in html
    assert 'onclick="swView(\'topology\')"' in html
    assert '/static/overview.css?v=3' in html
    css = client.get("/static/overview.css")
    assert css.status_code == 200
    assert "#v-dashboard .ops-hero" in css.text
    assert '.ops-hero[data-network-state="state_down"]' in css.text
    assert "prefers-reduced-motion" in css.text
