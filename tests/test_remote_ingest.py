"""Ingesta central: auth, idempotencia, rechazo parcial y estado honesto."""

import sqlite3
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.main import app
from app.services import inventory_svc
from app.remote_collector.timeutil import iso_now, utcnow
from app.services.remote import store
from app.services.security import api_keys_svc

CLIENT = TestClient(app)
SITE = "site-lima"
COLLECTOR = "collector-lima-01"
TS = "2026-10-23T10:00:00.000000+00:00"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("NETPULSE_REMOTE_DB", str(tmp_path / "remote.db"))
    monkeypatch.setenv("NETPULSE_REMOTE_RETENTION_DAYS", "30")
    monkeypatch.setattr(api_keys_svc, "API_KEYS_FILE", tmp_path / "api_keys.json")
    monkeypatch.setattr("app.middleware.audit._log_async", lambda **kwargs: None)
    monkeypatch.setattr("app.middleware.audit._safe_log", lambda **kwargs: None)
    monkeypatch.setattr("app.routers.security_ext.audit_svc.log_event", lambda **kwargs: None)
    devices = tmp_path / "devices.yaml"
    devices.write_text(
        "devices:\n"
        "  - id: sw-1\n"
        "    hostname: 10.0.0.1\n"
        "    port: 22\n"
        "    driver: ios\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(inventory_svc, "DEVICES_FILE", devices)
    inventory_svc.invalidate_cache()
    yield
    inventory_svc.invalidate_cache()


def collector_key():
    return api_keys_svc.create_api_key(
        "collector-lima-01", kind="collector", site_id=SITE, collector_id=COLLECTOR
    )["key"]


def user_key(role="viewer"):
    return api_keys_svc.create_api_key(f"user-{role}", role)["key"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def jwt(role):
    return create_access_token({"sub": f"{role}-user", "role": role})


def reachable(device="sw-1", ts=TS, kind="tcp", value=True):
    return {
        "device_id": device, "ts": ts, "metric": "reachable",
        "value_bool": value, "value_num": None, "ok": True,
        "probe_kind": kind, "error": None if value else "timeout TCP",
    }


def latency(device="sw-1", ts=TS, value=1.5, kind="tcp"):
    return {
        "device_id": device, "ts": ts, "metric": "latency_ms",
        "value_bool": None, "value_num": value, "ok": True,
        "probe_kind": kind, "error": None,
    }


def payload(samples, *, sent_at=None, **overrides):
    data = {
        "schema_version": 1,
        "collector_version": "0.1.0",
        "probe_interval_s": 60,
        "sync_batch_id": "batch-1",
        "sent_at": sent_at or iso_now(),
        "buffer": {
            "buffer_epoch": "epoch-1", "pending": len(samples),
            "oldest_pending_ts": None, "dropped_total": 0,
            "gap_since": None, "paused_reason": None,
        },
        "samples": samples,
    }
    data.update(overrides)
    return data


def post(token, data):
    return CLIENT.post("/api/remote/ingest", headers=auth(token), json=data)


def test_missing_and_user_key_rejected():
    assert post("np_no_existe", payload([reachable()])).status_code == 401
    response = post(user_key("viewer"), payload([reachable()]))
    assert response.status_code == 403
    assert "collector" in response.json()["detail"]


def test_valid_ingest_and_idempotency():
    key = collector_key()
    data = payload([reachable(), latency()])
    first = post(key, data)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["accepted"] == 2
    assert body["duplicates"] == 0
    assert body["site_id"] == SITE
    assert body["collector_id"] == COLLECTOR
    second = post(key, data)
    assert second.status_code == 200
    assert second.json()["accepted"] == 0
    assert second.json()["duplicates"] == 2

    devices = CLIENT.get(f"/api/remote/sites/{SITE}/devices", headers=auth(jwt("viewer")))
    assert devices.status_code == 200
    row = devices.json()["devices"][0]
    assert row["device_id"] == "sw-1"
    assert row["inventory_match"] is True
    assert row["reachable"] is True
    assert row["latency_ms"] == 1.5
    assert row["freshness"]["current"] is True


def test_partial_reject_keeps_valid_rows():
    key = collector_key()
    bad = latency(ts="2026-10-23T10:01:00.000000+00:00")
    bad["ok"] = False  # latency con ok=false es inválida
    response = post(key, payload([
        reachable(ts="2026-10-23T10:00:00.000000+00:00"),
        bad,
    ]))
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "partial"
    assert body["accepted"] == 1
    assert body["rejected"] == 1
    assert len(body["rejected_samples"]) == 1
    with sqlite3.connect(store.db_path()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM remote_sample").fetchone()[0] == 1


def test_ok_false_is_unverifiable_not_offline():
    key = collector_key()
    unknown = {
        "device_id": "sw-1", "ts": TS, "metric": "reachable",
        "value_bool": None, "value_num": None, "ok": False,
        "probe_kind": "tcp", "error": "DNS: fallo",
    }
    assert post(key, payload([unknown])).status_code == 200
    response = CLIENT.get(f"/api/remote/sites/{SITE}/devices", headers=auth(jwt("viewer")))
    row = response.json()["devices"][0]
    assert row["ok"] is False
    assert row["reachable"] is None
    assert row["error"] == "DNS: fallo"


def test_out_of_order_does_not_rollback_state():
    key = collector_key()
    newer = "2026-10-23T10:05:00.000000+00:00"
    older = "2026-10-23T10:00:00.000000+00:00"
    assert post(key, payload([reachable(ts=newer, value=True)])).status_code == 200
    response = post(key, payload([reachable(ts=older, value=False)]))
    assert response.status_code == 200
    assert response.json()["out_of_order"] == 1
    state = CLIENT.get(f"/api/remote/sites/{SITE}/devices", headers=auth(jwt("viewer")))
    assert state.json()["devices"][0]["reachable"] is True
    assert state.json()["devices"][0]["freshness"]["last_sample_ts"] == newer


def test_clock_skew_and_time_quality():
    key = collector_key()
    old = (utcnow() - timedelta(minutes=10)).isoformat(timespec="microseconds")
    response = post(key, payload([reachable()], sent_at=old))
    assert response.status_code == 200
    assert response.json()["clock_skew_s"] > 300
    site = CLIENT.get(f"/api/remote/sites/{SITE}", headers=auth(jwt("viewer"))).json()
    assert site["collectors"][0]["time_quality"] == "skewed"


def test_heartbeat_and_empty_sites():
    empty = CLIENT.get("/api/remote/sites", headers=auth(jwt("viewer")))
    assert empty.status_code == 200
    assert empty.json()["sites"] == []

    key = collector_key()
    response = post(key, payload([]))
    assert response.status_code == 200
    assert response.json()["accepted"] == 0
    sites = CLIENT.get("/api/remote/sites", headers=auth(jwt("viewer"))).json()
    assert [site["site_id"] for site in sites["sites"]] == [SITE]


def test_unknown_site_404():
    response = CLIENT.get("/api/remote/sites/no-existe", headers=auth(jwt("viewer")))
    assert response.status_code == 404
    assert "sin contacto" in response.json()["detail"]


def test_collector_key_cannot_read():
    key = collector_key()
    response = CLIENT.get("/api/remote/sites", headers=auth(key))
    assert response.status_code == 403


def test_schema_version_and_batch_limit():
    key = collector_key()
    data = payload([reachable()])
    data["schema_version"] = 2
    assert post(key, data).status_code == 422
    too_many = payload([reachable(device=f"d{i}") for i in range(501)])
    assert post(key, too_many).status_code == 413


def test_retention_purges_old_samples():
    key = collector_key()
    assert post(key, payload([reachable()])).status_code == 200
    with sqlite3.connect(store.db_path()) as conn:
        conn.execute(
            "UPDATE remote_sample SET ts = '2020-01-01T00:00:00.000000+00:00'"
        )
    with store.transaction() as conn:
        deleted = store.prune_samples(conn)
    assert deleted == 1
    with sqlite3.connect(store.db_path()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM remote_sample").fetchone()[0] == 0


def test_buffer_metadata_epoch_and_sync_are_reported():
    key = collector_key()
    data = payload([reachable()])
    data["buffer"].update({
        "buffer_epoch": "epoch-2", "pending": 7, "dropped_total": 5,
        "gap_since": "2026-10-23T09:00:00.000000+00:00",
        "paused_reason": "disk_low",
    })
    data["sync"] = {
        "last_error": "HTTP 503", "last_error_at": "2026-10-23T09:10:00.000000+00:00",
        "consecutive_failures": 0, "auth_state": "ok",
    }
    assert post(key, data).status_code == 200
    site = CLIENT.get(f"/api/remote/sites/{SITE}", headers=auth(jwt("viewer"))).json()
    collector = site["collectors"][0]
    assert collector["buffer"]["buffer_epoch"] == "epoch-2"
    assert collector["buffer"]["pending"] == 7
    assert collector["buffer"]["dropped_total"] == 5
    assert collector["buffer"]["gap_since"] == "2026-10-23T09:00:00.000000+00:00"
    assert collector["buffer"]["paused_reason"] == "disk_low"
    assert collector["sync"]["last_error"] == "HTTP 503"
    assert collector["sync"]["consecutive_failures"] == 0
    assert site["buffer_epoch_changed_at"] is None  # primer epoch conocido

    data["buffer"]["buffer_epoch"] = "epoch-3"
    assert post(key, data).status_code == 200
    site = CLIENT.get(f"/api/remote/sites/{SITE}", headers=auth(jwt("viewer"))).json()
    assert site["collectors"][0]["buffer"]["buffer_epoch"] == "epoch-3"
    assert site["buffer_epoch_changed_at"] is not None
