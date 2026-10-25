"""Watchdog de sites enmudecidos: disparo único, recuperación y estado honesto."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.main import app
from app.services import inventory_svc, notifications_svc
from app.services.remote import store, watchdog_svc
from app.services.security import api_keys_svc

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
SITE = "site-lima"
COLLECTOR = "collector-lima-01"
OLD = "2020-01-01T00:00:00.000000+00:00"


@pytest.fixture
def ctx(monkeypatch, tmp_path):
    """Aísla DB, estado, claves y captura las notificaciones emitidas."""
    monkeypatch.setenv("NETPULSE_REMOTE_DB", str(tmp_path / "remote.db"))
    monkeypatch.setenv("NETPULSE_SITE_WATCHDOG_STATE", str(tmp_path / "watchdog.json"))
    monkeypatch.setenv("NETPULSE_SITE_WATCHDOG_ENABLED", "true")
    monkeypatch.setenv("NETPULSE_SITE_WATCHDOG_INTERVAL_S", "5")
    monkeypatch.setenv("NETPULSE_SITE_STALE_AFTER_S", "300")
    monkeypatch.setattr(api_keys_svc, "API_KEYS_FILE", tmp_path / "api_keys.json")
    monkeypatch.setattr("app.middleware.audit._log_async", lambda **kwargs: None)
    monkeypatch.setattr("app.middleware.audit._safe_log", lambda **kwargs: None)

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        notifications_svc,
        "notify_event",
        lambda event_type, data: events.append((event_type, data)),
    )
    return SimpleNamespace(
        events=events,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )


def add_collector(
    site=SITE,
    collector=COLLECTOR,
    *,
    last_seen,
    probe_interval_s=60,
    last_error=None,
    last_error_at=None,
):
    """Inserta una fila mínima de colector con el ``last_seen_at`` indicado."""
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO remote_collector ("
            "site_id, collector_id, probe_interval_s, first_seen_at, last_seen_at, "
            "last_sync_error, last_sync_error_at"
            ") VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT (site_id, collector_id) DO UPDATE SET "
            "last_seen_at=excluded.last_seen_at, "
            "last_sync_error=excluded.last_sync_error, "
            "last_sync_error_at=excluded.last_sync_error_at",
            (site, collector, probe_interval_s, last_seen, last_seen, last_error, last_error_at),
        )


def iso(moment: datetime) -> str:
    return moment.isoformat(timespec="microseconds")


def stale_events(ctx):
    return [data for event, data in ctx.events if event == "site_stale"]


def recovered_events(ctx):
    return [data for event, data in ctx.events if event == "site_recovered"]


def test_fresh_site_does_not_alert(ctx):
    add_collector(last_seen=iso(NOW - timedelta(seconds=10)))

    result = watchdog_svc.evaluate(now=NOW)

    assert result["status"] == "ok"
    assert result["fired"] == []
    assert result["resolved"] == []
    assert watchdog_svc.active_alerts(now=NOW) == []
    assert ctx.events == []
    assert not watchdog_svc.state_file().exists()


def test_stale_site_fires_once_and_is_not_duplicated(ctx):
    add_collector(
        last_seen=OLD,
        last_error="connection refused",
        last_error_at="2026-10-25T08:00:00.000000+00:00",
    )

    first = watchdog_svc.evaluate(now=NOW)
    second = watchdog_svc.evaluate(now=NOW)

    assert first["status"] == "ok"
    assert len(first["fired"]) == 1
    assert first["fired"][0]["reason"] == "sin contacto"
    assert second["fired"] == []
    assert len(stale_events(ctx)) == 1

    data = stale_events(ctx)[0]
    assert data["site_id"] == SITE
    assert data["collector_id"] == COLLECTOR
    assert data["reason"] == "sin contacto"
    assert data["last_error"] == "connection refused"
    assert data["minutes_without_contact"] > 300
    assert data["severity"] == "critical"

    active = watchdog_svc.active_alerts(now=NOW)
    assert len(active) == 1
    assert active[0]["name"] == "SiteSilent"
    assert active[0]["state"] == "firing"
    assert active[0]["device_id"] == SITE
    assert active[0]["source"] == "watchdog"


def test_recovery_resolves_the_alert(ctx):
    add_collector(last_seen=OLD)
    watchdog_svc.evaluate(now=NOW)
    assert len(watchdog_svc.active_alerts(now=NOW)) == 1

    # El colector vuelve a reportar: el site pasa a fresh.
    add_collector(last_seen=iso(NOW - timedelta(seconds=5)))
    result = watchdog_svc.evaluate(now=NOW)

    assert result["resolved"] == [SITE]
    assert len(recovered_events(ctx)) == 1
    assert recovered_events(ctx)[0]["site_id"] == SITE
    assert watchdog_svc.active_alerts(now=NOW) == []

    # Un segundo ciclo no vuelve a resolver ni a notificar.
    again = watchdog_svc.evaluate(now=NOW)
    assert again["resolved"] == []
    assert len(recovered_events(ctx)) == 1


def test_two_sites_are_independent(ctx):
    add_collector(SITE, COLLECTOR, last_seen=OLD)
    add_collector(
        "site-cusco",
        "collector-cusco-01",
        last_seen=iso(NOW - timedelta(seconds=10)),
    )

    result = watchdog_svc.evaluate(now=NOW)

    assert [alert["site_id"] for alert in result["fired"]] == [SITE]
    active = watchdog_svc.active_alerts(now=NOW)
    assert [alert["site_id"] for alert in active] == [SITE]

    # Lima se recupera y Cusco se cae en el mismo ciclo.
    add_collector(SITE, COLLECTOR, last_seen=iso(NOW - timedelta(seconds=1)))
    add_collector(
        "site-cusco",
        "collector-cusco-01",
        last_seen=OLD,
    )
    result = watchdog_svc.evaluate(now=NOW)

    assert result["resolved"] == [SITE]
    assert [alert["site_id"] for alert in result["fired"]] == ["site-cusco"]
    assert [alert["site_id"] for alert in watchdog_svc.active_alerts(now=NOW)] == [
        "site-cusco"
    ]


def test_disabled_watchdog_does_nothing(ctx):
    ctx.monkeypatch.setenv("NETPULSE_SITE_WATCHDOG_ENABLED", "false")
    add_collector(last_seen=OLD)

    result = watchdog_svc.evaluate(now=NOW)

    assert result["status"] == "disabled"
    assert result["fired"] == []
    assert ctx.events == []
    assert not watchdog_svc.state_file().exists()
    assert asyncio.run(watchdog_svc.start()) is False


def test_fresh_site_stays_silent_across_cycles(ctx):
    add_collector(last_seen=iso(NOW - timedelta(seconds=20)))

    for offset in (0, 60, 120):
        watchdog_svc.evaluate(now=NOW + timedelta(seconds=offset))

    assert ctx.events == []
    assert watchdog_svc.active_alerts(now=NOW + timedelta(seconds=120)) == []


def test_site_without_heartbeat_is_stale_never_green(ctx):
    api_keys_svc.create_api_key(
        "colector-nuevo",
        kind="collector",
        site_id="site-nuevo",
        collector_id="collector-nuevo-01",
    )
    path = api_keys_svc.API_KEYS_FILE
    keys = json.loads(path.read_text(encoding="utf-8"))
    keys[0]["created_at"] = iso(NOW - timedelta(hours=2))
    path.write_text(json.dumps(keys), encoding="utf-8")

    result = watchdog_svc.evaluate(now=NOW)

    assert result["status"] == "ok"
    assert len(result["fired"]) == 1
    alert = result["fired"][0]
    assert alert["site_id"] == "site-nuevo"
    assert alert["collector_id"] == "collector-nuevo-01"
    assert alert["reason"] == "sin heartbeat"
    assert alert["last_seen_at"] is None
    assert alert["minutes_without_contact"] == 120


def test_new_key_has_a_deployment_grace_period(ctx):
    api_keys_svc.create_api_key(
        "colector-recien-creado",
        kind="collector",
        site_id="site-nuevo",
        collector_id="collector-nuevo-01",
    )
    path = api_keys_svc.API_KEYS_FILE
    keys = json.loads(path.read_text(encoding="utf-8"))
    keys[0]["created_at"] = iso(NOW - timedelta(seconds=60))
    path.write_text(json.dumps(keys), encoding="utf-8")

    result = watchdog_svc.evaluate(now=NOW)

    assert result["fired"] == []
    assert ctx.events == []


def test_store_failure_is_reported_without_losing_state(ctx, monkeypatch):
    add_collector(last_seen=OLD)
    watchdog_svc.evaluate(now=NOW)
    assert len(watchdog_svc.active_alerts(now=NOW)) == 1

    def boom(now=None):
        raise store.RemoteStoreUnavailable("DB remota ilegible")

    monkeypatch.setattr(watchdog_svc.query_svc, "list_sites", boom)
    result = watchdog_svc.evaluate(now=NOW)

    assert result["status"] == "error"
    assert "ilegible" in result["error"]
    # No se inventa una recuperación cuando no se puede leer el almacén.
    assert len(watchdog_svc.active_alerts(now=NOW)) == 1
    assert len(recovered_events(ctx)) == 0


def test_notification_failure_does_not_lose_the_episode(ctx, monkeypatch):
    add_collector(last_seen=OLD)

    def explode(event_type, data):
        raise RuntimeError("webhook caído")

    monkeypatch.setattr(notifications_svc, "notify_event", explode)
    result = watchdog_svc.evaluate(now=NOW)

    assert len(result["fired"]) == 1
    assert len(watchdog_svc.active_alerts(now=NOW)) == 1

    # Siguiente ciclo con notificación sana: no hay duplicado.
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        notifications_svc,
        "notify_event",
        lambda event_type, data: events.append((event_type, data)),
    )
    second = watchdog_svc.evaluate(now=NOW)
    assert second["fired"] == []
    assert events == []


def test_start_stop_leaves_no_orphan_task(ctx):
    async def scenario():
        assert await watchdog_svc.start() is True
        assert watchdog_svc.is_running() is True
        await watchdog_svc.stop()
        assert watchdog_svc.is_running() is False

    asyncio.run(scenario())


def test_api_exposes_watchdog_alert_without_prometheus(ctx, monkeypatch):
    add_collector(last_seen=OLD)
    watchdog_svc.evaluate(now=NOW)

    def prometheus_down(*args, **kwargs):
        raise OSError("prometheus caído")

    monkeypatch.setattr("app.routers.alerts.urllib.request.urlopen", prometheus_down)
    token = create_access_token({"sub": "viewer", "role": "viewer"})
    client = TestClient(app)

    active = client.get(
        "/api/alerts/active", headers={"Authorization": f"Bearer {token}"}
    )
    assert active.status_code == 200
    body = active.json()
    assert body["status"] == "success"
    assert body["prometheus_available"] is False
    assert body["firing"] == 1
    assert body["alerts"][0]["source"] == "watchdog"
    assert body["alerts"][0]["site_id"] == SITE

    status = client.get(
        "/api/remote/watchdog", headers={"Authorization": f"Bearer {token}"}
    )
    assert status.status_code == 200
    snapshot = status.json()
    assert snapshot["enabled"] is True
    assert snapshot["active_count"] == 1
    assert snapshot["alerts"][0]["site_id"] == SITE


def test_app_lifespan_starts_and_stops_the_watchdog(ctx):
    devices = ctx.tmp_path / "devices.yaml"
    devices.write_text(
        "devices:\n"
        "  - id: sw-1\n    hostname: 10.0.0.1\n    port: 22\n    driver: ios\n",
        encoding="utf-8",
    )
    ctx.monkeypatch.setattr(inventory_svc, "DEVICES_FILE", devices)
    inventory_svc.invalidate_cache()

    try:
        with TestClient(app) as client:
            assert client.get("/api/health").status_code == 200
            assert watchdog_svc.is_running() is True
        assert watchdog_svc.is_running() is False
    finally:
        inventory_svc.invalidate_cache()
