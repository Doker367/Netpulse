"""Esquema central, retención y salud del inventario."""

import sqlite3

import pytest

from app.services import inventory_svc
from app.services.remote import store


@pytest.fixture(autouse=True)
def db(monkeypatch, tmp_path):
    monkeypatch.setenv("NETPULSE_REMOTE_DB", str(tmp_path / "remote.db"))
    yield tmp_path


def test_schema_created_with_user_version():
    with store.read_connection() as conn:
        tables = {
            row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert {"remote_collector", "remote_sample", "remote_device_state", "remote_meta"} <= tables
    assert version == 1


def test_future_schema_fails_clearly(monkeypatch, tmp_path):
    path = tmp_path / "future.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version=2")
    conn.close()
    monkeypatch.setenv("NETPULSE_REMOTE_DB", str(path))
    with pytest.raises(store.RemoteStoreUnavailable, match="más nuevo"):
        with store.read_connection():
            pass


def test_retention_days_env(monkeypatch):
    monkeypatch.setenv("NETPULSE_REMOTE_RETENTION_DAYS", "45")
    assert store.retention_days() == 45
    monkeypatch.setenv("NETPULSE_REMOTE_RETENTION_DAYS", "basura")
    assert store.retention_days() == 30


def test_inventory_health_missing_and_corrupt(monkeypatch, tmp_path):
    missing = tmp_path / "missing.yaml"
    monkeypatch.setattr(inventory_svc, "DEVICES_FILE", missing)
    inventory_svc.invalidate_cache()
    assert inventory_svc.inventory_health()[0] is False

    corrupt = tmp_path / "corrupt.yaml"
    corrupt.write_text("devices: [", encoding="utf-8")
    monkeypatch.setattr(inventory_svc, "DEVICES_FILE", corrupt)
    inventory_svc.invalidate_cache()
    available, error = inventory_svc.inventory_health()
    assert available is False
    assert error

    valid = tmp_path / "valid.yaml"
    valid.write_text("devices: []\n", encoding="utf-8")
    monkeypatch.setattr(inventory_svc, "DEVICES_FILE", valid)
    inventory_svc.invalidate_cache()
    assert inventory_svc.inventory_health() == (True, None)
    inventory_svc.invalidate_cache()


def test_prune_old_sample_batches(monkeypatch):
    monkeypatch.setenv("NETPULSE_REMOTE_RETENTION_DAYS", "1")
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO remote_sample (site_id, collector_id, device_id, metric, ts, "
            "received_at, value_bool, ok, probe_kind) "
            "VALUES ('s','c','d','reachable','2020-01-01T00:00:00.000000+00:00',"
            "'2020-01-01T00:00:00.000000+00:00',1,1,'tcp')"
        )
    with store.transaction() as conn:
        assert store.prune_samples(conn) == 1
    with store.read_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM remote_sample").fetchone()[0] == 0
