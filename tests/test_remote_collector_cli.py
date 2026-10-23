"""CLI del colector: check, status, dry-run y once sin red real."""

import json
from datetime import timedelta

import yaml

from app.remote_collector import cli
from app.remote_collector.timeutil import utcnow
from app.services.collectors.probe_svc import ProbeResult


def write_config(tmp_path):
    path = tmp_path / "collector.yaml"
    path.write_text(yaml.safe_dump({
        "schema_version": 1,
        "site_id": "site-lima",
        "collector_id": "collector-lima-01",
        "central": {"base_url": "https://noc.example.net", "timeout_s": 5},
        "probe": {"interval_s": 60, "timeout_ms": 500, "workers": 2, "icmp_count": 1},
        "buffer": {
            "path": str(tmp_path / "buffer.sqlite3"),
            "max_rows": 500000, "max_age_hours": 72,
            "synced_retention_hours": 6, "min_free_mb": 200,
        },
        "sync": {
            "interval_s": 30, "heartbeat_s": 60, "batch_max_samples": 200,
            "backoff_base_s": 5, "backoff_max_s": 300,
        },
        "devices": [{"id": "sw-1", "hostname": "10.0.0.1", "port": 22, "protocol": "ssh"}],
    }), encoding="utf-8")
    return path


def test_dry_run(monkeypatch, tmp_path, capsys):
    path = write_config(tmp_path)
    monkeypatch.setattr(
        cli.probe_svc, "probe",
        lambda *a, **k: ProbeResult(True, True, 1.1, None, "tcp"),
    )
    assert cli.main(["--config", str(path), "--dry-run"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["dry_run"] is True
    assert data["results"][0]["device_id"] == "sw-1"
    assert data["results"][0]["latency_ms"] == 1.1


def test_status_missing_is_exit_3(tmp_path):
    path = write_config(tmp_path)
    assert cli.main(["--config", str(path), "--status"]) == 3


def test_status_stale(monkeypatch, tmp_path, capsys):
    path = write_config(tmp_path)
    status = tmp_path / "status.json"
    old = (utcnow() - timedelta(hours=1)).isoformat(timespec="microseconds")
    status.write_text(json.dumps({"updated_at": old}), encoding="utf-8")
    assert cli.main(["--config", str(path), "--status"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["state"] == "stale"


def test_check_rejects_site_mismatch(monkeypatch, tmp_path):
    path = write_config(tmp_path)
    monkeypatch.setenv("NETPULSE_COLLECTOR_API_KEY", "np_test")
    monkeypatch.setattr(
        cli.SyncClient, "whoami",
        lambda self: {"site_id": "otro-site", "collector_id": "collector-lima-01"},
    )
    assert cli.main(["--config", str(path), "--check"]) == 1


def test_check_ok(monkeypatch, tmp_path, capsys):
    path = write_config(tmp_path)
    monkeypatch.setenv("NETPULSE_COLLECTOR_API_KEY", "np_test")
    monkeypatch.setattr(
        cli.SyncClient, "whoami",
        lambda self: {"site_id": "site-lima", "collector_id": "collector-lima-01",
                       "key_name": "k"},
    )
    assert cli.main(["--config", str(path), "--check"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_check_without_key_is_exit_2(monkeypatch, tmp_path):
    path = write_config(tmp_path)
    monkeypatch.delenv("NETPULSE_COLLECTOR_API_KEY", raising=False)
    assert cli.main(["--config", str(path), "--check"]) == 2


def test_invalid_config_is_exit_2(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("schema_version: 1\n", encoding="utf-8")
    assert cli.main(["--config", str(path), "--dry-run"]) == 2


def test_once(monkeypatch, tmp_path, capsys):
    path = write_config(tmp_path)
    monkeypatch.setenv("NETPULSE_COLLECTOR_API_KEY", "np_test")
    monkeypatch.setattr(
        cli, "_run_cycle",
        lambda buffer, config: {
            "ts": "2026-10-23T00:00:00.000000+00:00", "duration_ms": 1,
            "devices": 1, "reachable": 1, "unreachable": 0,
            "unverifiable": 0, "inserted": 0,
        },
    )

    class FakeClient:
        def __init__(self, config, key):
            pass

        def post_batch(self, payload):
            return {"status": "ok", "accepted": 0, "duplicates": 0,
                    "rejected": 0, "rejected_samples": []}

        def close(self):
            pass

    monkeypatch.setattr(cli, "SyncClient", FakeClient)
    assert cli.main(["--config", str(path), "--once"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["cycle"]["reachable"] == 1
    assert data["sync"]["status"] == "ok"
