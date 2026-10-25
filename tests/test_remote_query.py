"""Consultas: frescura, conteos, inventario no verificable e histórico."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.main import app
from app.remote_collector.timeutil import iso_now
from app.services import inventory_svc
from app.services.remote import query_svc, store
from app.services.security import api_keys_svc

CLIENT = TestClient(app)
SITE = "site-lima"
COLLECTOR = "collector-lima-01"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("NETPULSE_REMOTE_DB", str(tmp_path / "remote.db"))
    monkeypatch.setattr(api_keys_svc, "API_KEYS_FILE", tmp_path / "api_keys.json")
    monkeypatch.setattr("app.middleware.audit._log_async", lambda **kwargs: None)
    monkeypatch.setattr("app.middleware.audit._safe_log", lambda **kwargs: None)
    devices = tmp_path / "devices.yaml"
    devices.write_text(
        "devices:\n"
        "  - id: sw-1\n    hostname: 10.0.0.1\n    port: 22\n    driver: ios\n"
        "  - id: sw-2\n    hostname: 10.0.0.2\n    port: 22\n    driver: ios\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(inventory_svc, "DEVICES_FILE", devices)
    inventory_svc.invalidate_cache()
    yield
    inventory_svc.invalidate_cache()


def key():
    return api_keys_svc.create_api_key(
        "collector", kind="collector", site_id=SITE, collector_id=COLLECTOR
    )["key"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def viewer():
    return create_access_token({"sub": "viewer", "role": "viewer"})


def sample(device="sw-1", ts=None, metric="reachable", value_bool=True,
           value_num=None, ok=True, error=None, kind="tcp"):
    return {
        "device_id": device, "ts": ts or iso_now(), "metric": metric,
        "value_bool": value_bool, "value_num": value_num, "ok": ok,
        "probe_kind": kind, "error": error,
    }


def ingest(samples, sent_at=None):
    data = {
        "schema_version": 1,
        "collector_version": "0.1.0",
        "probe_interval_s": 60,
        "sync_batch_id": "b",
        "sent_at": sent_at or iso_now(),
        "buffer": {
            "buffer_epoch": "e", "pending": len(samples), "oldest_pending_ts": None,
            "dropped_total": 0, "gap_since": None, "paused_reason": None,
        },
        "samples": samples,
    }
    response = CLIENT.post("/api/remote/ingest", headers=auth(key()), json=data)
    assert response.status_code == 200, response.text


def test_stale_site_and_device_are_not_current():
    ingest([sample()])
    with sqlite3.connect(store.db_path()) as conn:
        old = "2026-01-01T00:00:00.000000+00:00"
        conn.execute("UPDATE remote_collector SET last_seen_at=?", (old,))
        conn.execute("UPDATE remote_device_state SET last_received_at=?", (old,))
    site = CLIENT.get(f"/api/remote/sites/{SITE}", headers=auth(viewer())).json()
    assert site["contact"] == "stale"
    devices = CLIENT.get(f"/api/remote/sites/{SITE}/devices", headers=auth(viewer())).json()
    assert devices["contact"] == "stale"
    assert devices["devices"][0]["freshness"]["current"] is False
    assert devices["devices"][0]["freshness"]["state"] == "stale"
    assert devices["devices"][0]["reachable"] is True  # último estado conocido


def test_device_counts_and_unmatched_filter():
    ingest([
        sample("sw-1", value_bool=True),
        sample("sw-2", value_bool=False, error="timeout TCP"),
        sample("desconocido", value_bool=None, ok=False, error="DNS: fallo"),
    ])
    site = CLIENT.get(f"/api/remote/sites/{SITE}", headers=auth(viewer())).json()
    assert site["devices"] == {
        "total": 3, "reachable": 1, "unreachable": 1,
        "unverifiable": 1, "unmatched": 1,
    }
    all_devices = CLIENT.get(
        f"/api/remote/sites/{SITE}/devices", headers=auth(viewer())
    ).json()["devices"]
    assert len(all_devices) == 3
    only_known = CLIENT.get(
        f"/api/remote/sites/{SITE}/devices?include_unmatched=false",
        headers=auth(viewer()),
    ).json()["devices"]
    assert [d["device_id"] for d in only_known] == ["sw-1", "sw-2"]


def test_inventory_unreadable_is_null(monkeypatch, tmp_path):
    missing = tmp_path / "no-devices.yaml"
    monkeypatch.setattr(inventory_svc, "DEVICES_FILE", missing)
    inventory_svc.invalidate_cache()
    ingest([sample("sw-1")])
    devices = CLIENT.get(f"/api/remote/sites/{SITE}/devices", headers=auth(viewer())).json()
    row = devices["devices"][0]
    assert row["inventory_match"] is None
    assert row["inventory_error"]


def test_history_and_empty_window():
    old = "2020-01-01T00:00:00.000000+00:00"
    ingest([sample("sw-1", ts=old), sample("sw-1")])
    ingest([sample("sw-2", ts=old)])
    response = CLIENT.get(
        f"/api/remote/sites/{SITE}/devices/sw-1/history?hours=6",
        headers=auth(viewer()),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["points"][0]["reachable"] is True
    assert body["coverage"]["expected_samples"] is None

    empty = CLIENT.get(
        f"/api/remote/sites/{SITE}/devices/sw-2/history?hours=0.1",
        headers=auth(viewer()),
    )
    assert empty.status_code == 200
    assert empty.json()["count"] == 0
    assert empty.json()["points"] == []

    missing = CLIENT.get(
        f"/api/remote/sites/{SITE}/devices/no-existe/history",
        headers=auth(viewer()),
    )
    assert missing.status_code == 404


def test_history_too_many_points(monkeypatch):
    monkeypatch.setattr(query_svc, "MAX_POINTS", 1)
    ingest([
        sample("sw-1", ts="2026-10-23T10:00:00.000000+00:00"),
        sample("sw-1", ts="2026-10-23T10:01:00.000000+00:00"),
    ])
    response = CLIENT.get(
        f"/api/remote/sites/{SITE}/devices/sw-1/history?hours=168",
        headers=auth(viewer()),
    )
    assert response.status_code == 422
    assert "ventana demasiado grande" in response.json()["detail"]


def test_site_detail_has_retention_and_oldest():
    ingest([sample("sw-1")])
    site = CLIENT.get(f"/api/remote/sites/{SITE}", headers=auth(viewer())).json()
    assert site["retention_days"] == 30
    assert site["oldest_sample_ts"] is not None
    assert site["collectors"][0]["buffer"]["buffer_epoch"] == "e"


def test_empty_sites_when_no_db():
    response = CLIENT.get("/api/remote/sites", headers=auth(viewer()))
    assert response.status_code == 200
    assert response.json()["sites"] == []


# ── Sites provisionados sin heartbeat (Tarjeta 12D) ───────────


NEVER_SITE = "site-nuevo"
NEVER_COLLECTOR = "collector-nuevo-01"


def _never_key(monkeypatch=None):
    return api_keys_svc.create_api_key(
        "colector-nuevo",
        kind="collector",
        site_id=NEVER_SITE,
        collector_id=NEVER_COLLECTOR,
    )


def _sites():
    response = CLIENT.get("/api/remote/sites", headers=auth(viewer()))
    assert response.status_code == 200, response.text
    return {site["site_id"]: site for site in response.json()["sites"]}


def test_site_without_heartbeat_is_listed_as_never():
    """Una clave de colector sin ninguna muestra aparece, pero nunca como verde."""
    ingest([sample()])  # el central ya tiene datos: la DB existe
    _never_key()

    sites = _sites()
    assert set(sites) == {SITE, NEVER_SITE}
    never = sites[NEVER_SITE]
    assert never["contact"] == "never"
    assert never["contact"] != "fresh"
    assert never["last_seen_at"] is None
    assert never["last_contact_age_s"] is None
    assert never["devices"] == {
        "total": 0,
        "reachable": 0,
        "unreachable": 0,
        "unverifiable": 0,
        "unmatched": 0,
    }
    collector = never["collectors"][0]
    assert collector["collector_id"] == NEVER_COLLECTOR
    assert collector["contact"] == "never"
    # Nada medido todavía: null, nunca 0 inventado.
    assert collector["version"] is None
    assert collector["probe_interval_s"] is None
    assert collector["last_seen_at"] is None
    assert collector["buffer"]["pending"] is None
    assert collector["last_batch"]["accepted"] is None
    assert collector["sync"]["auth_state"] is None


def test_site_without_heartbeat_detail_and_devices_are_honest():
    ingest([sample()])
    _never_key()

    detail = CLIENT.get(f"/api/remote/sites/{NEVER_SITE}", headers=auth(viewer()))
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["contact"] == "never"
    assert body["last_seen_at"] is None
    assert body["oldest_sample_ts"] is None
    assert body["buffer_epoch_changed_at"] is None
    assert body["retention_days"] == 30

    devices = CLIENT.get(
        f"/api/remote/sites/{NEVER_SITE}/devices", headers=auth(viewer())
    )
    assert devices.status_code == 200, devices.text
    payload = devices.json()
    assert payload["contact"] == "never"
    assert payload["last_seen_at"] is None
    assert payload["stale_after_s"] is None
    assert payload["devices"] == []


def test_site_without_heartbeat_stops_being_never_after_first_batch():
    ingest([sample()])
    never_token = _never_key()["key"]
    payload = {
        "schema_version": 1,
        "collector_version": "0.1.0",
        "probe_interval_s": 60,
        "sync_batch_id": "nuevo-1",
        "sent_at": iso_now(),
        "buffer": {
            "buffer_epoch": "e",
            "pending": 0,
            "oldest_pending_ts": None,
            "dropped_total": 0,
            "gap_since": None,
            "paused_reason": None,
        },
        "samples": [sample()],
    }
    response = CLIENT.post("/api/remote/ingest", headers=auth(never_token), json=payload)
    assert response.status_code == 200, response.text

    sites = _sites()
    assert set(sites) == {SITE, NEVER_SITE}
    assert sites[NEVER_SITE]["contact"] == "fresh"
    assert sites[NEVER_SITE]["last_seen_at"] is not None


def test_revoked_collector_key_is_not_a_site():
    ingest([sample()])
    created = _never_key()
    assert api_keys_svc.revoke_api_key(created["id"]) is True

    assert set(_sites()) == {SITE}


def test_injected_clock_read_keeps_watchdog_grace():
    """La lectura con reloj inyectado (watchdog) solo ve sites observados.

    El watchdog decide el silencio y la gracia de despliegue de las claves
    nuevas con su propia regla; no debe recibir sites ``never`` derivados de
    claves que todavía no han tenido oportunidad de reportar.
    """
    from datetime import datetime, timezone

    ingest([sample()])
    _never_key()

    snapshot = query_svc.list_sites(now=datetime.now(timezone.utc))
    assert [site["site_id"] for site in snapshot["sites"]] == [SITE]