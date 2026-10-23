"""Buffer SQLite: esquema, idempotencia local, poda y guardas de disco."""

import sqlite3
from datetime import timedelta

from app.remote_collector.buffer import Buffer
from app.remote_collector.config import BufferConfig, DeviceSpec
from app.remote_collector.timeutil import iso_now, utcnow
from app.services.collectors.probe_svc import ProbeResult


def make_buffer(tmp_path, **overrides):
    values = dict(
        path=tmp_path / "buffer.sqlite3",
        max_rows=500000,
        max_age_hours=72,
        synced_retention_hours=6,
        min_free_mb=200,
    )
    values.update(overrides)
    return Buffer(BufferConfig(**values))


def dev(name="sw-1"):
    return DeviceSpec(name, "10.0.0.1", 22, "ssh", "ios")


def ok(latency=1.2, kind="tcp"):
    return ProbeResult(True, True, latency, 0.0 if kind == "icmp" else None, kind)


def down(kind="tcp", error="timeout TCP"):
    return ProbeResult(True, False, None, 100.0 if kind == "icmp" else None, kind, error)


def unmeasured(kind="tcp", error="DNS: fallo"):
    return ProbeResult(False, None, None, None, kind, error)


def test_schema_and_metrics(tmp_path):
    buf = make_buffer(tmp_path)
    ts = iso_now()
    inserted = buf.insert_probe(ts, [(dev(), ok(1.5))])
    assert inserted == 2  # reachable + latency (TCP no packet_loss)
    counts = buf.counts()
    assert counts == {"pending": 2, "synced": 0, "dead": 0, "total": 2}
    with sqlite3.connect(buf.path) as conn:
        rows = conn.execute(
            "SELECT metric, value_num, value_bool, ok, probe_kind FROM sample ORDER BY metric"
        ).fetchall()
    assert rows[0][0] == "latency_ms"
    assert rows[0][1] == 1.5
    assert rows[1][0] == "reachable"
    assert rows[1][2] == 1


def test_icmp_adds_packet_loss(tmp_path):
    buf = make_buffer(tmp_path)
    buf.insert_probe(iso_now(), [(dev(), ok(1.0, "icmp"))])
    metrics = {row["metric"] for row in buf.peek_pending(10)}
    assert metrics == {"reachable", "latency_ms", "packet_loss_pct"}


def test_unmeasured_is_null_not_false(tmp_path):
    buf = make_buffer(tmp_path)
    buf.insert_probe(iso_now(), [(dev(), unmeasured())])
    row = buf.peek_pending(10)[0]
    assert row["metric"] == "reachable"
    assert row["ok"] == 0
    assert row["value_bool"] is None
    assert row["error"] == "DNS: fallo"


def test_local_unique_dedup(tmp_path):
    buf = make_buffer(tmp_path)
    ts = iso_now()
    assert buf.insert_probe(ts, [(dev(), ok())]) == 2
    assert buf.insert_probe(ts, [(dev(), ok())]) == 0
    assert buf.counts()["total"] == 2


def test_peek_and_mark_synced(tmp_path):
    buf = make_buffer(tmp_path)
    ts = iso_now()
    buf.insert_probe(ts, [(dev("a"), ok()), (dev("b"), down())])
    pending = buf.peek_pending(2)
    assert [row["device_id"] for row in pending] == ["a", "a"]
    ids = [row["id"] for row in pending]
    assert buf.mark_synced(ids) == 2
    assert buf.counts()["pending"] == 1  # queda el reachable de b
    assert buf.peek_pending(10)[0]["device_id"] == "b"


def test_mark_attempt_and_dead(tmp_path):
    buf = make_buffer(tmp_path)
    ts = iso_now()
    buf.insert_probe(ts, [(dev(), ok())])
    rows = buf.peek_pending(10)
    assert buf.mark_attempt([rows[0]["id"]]) == 1
    assert buf.counts()["pending"] == 2
    assert buf.mark_dead(
        [(rows[0]["device_id"], rows[0]["ts"], rows[0]["metric"])], "inválida"
    ) == 1
    assert buf.counts()["dead"] == 1
    assert buf.counts()["pending"] == 1


def test_prune_synced_by_age(tmp_path):
    buf = make_buffer(tmp_path, synced_retention_hours=1)
    old = (utcnow() - timedelta(hours=3)).isoformat(timespec="microseconds")
    buf.insert_probe(old, [(dev(), ok())])
    ids = [row["id"] for row in buf.peek_pending(10)]
    buf.mark_synced(ids)
    stats = buf.prune()
    assert stats["deleted_synced"] == 2
    assert buf.counts()["total"] == 0


def test_prune_unsynced_by_age_declares_gap(tmp_path):
    buf = make_buffer(tmp_path, max_age_hours=1)
    old = (utcnow() - timedelta(hours=3)).isoformat(timespec="microseconds")
    buf.insert_probe(old, [(dev(), ok())])
    stats = buf.prune()
    assert stats["dropped_unsynced"] == 2
    summary = buf.summary()
    assert summary["dropped_total"] == 2
    assert summary["gap_since"] == old
    assert buf.counts()["total"] == 0


def test_prune_by_max_rows(tmp_path):
    buf = make_buffer(tmp_path, max_rows=1000)
    devices = [DeviceSpec(f"d{i}", f"10.0.0.{i}", 22, "ssh", "ios") for i in range(400)]
    buf.insert_probe(iso_now(), [(d, ok()) for d in devices])  # 800 filas TCP
    # 400 reachable + 400 latency = 800, no supera 1000
    assert buf.counts()["total"] == 800
    devices2 = [DeviceSpec(f"e{i}", f"10.0.1.{i}", 22, "ssh", "ios") for i in range(200)]
    buf.insert_probe(iso_now(), [(d, ok()) for d in devices2])  # +400 = 1200
    stats = buf.prune()
    assert stats["dropped_by_rows"] == 200
    assert buf.counts()["total"] == 1000
    assert buf.summary()["dropped_total"] == 200


class _Usage:
    def __init__(self, free_mb):
        self.free = int(free_mb * 1024 * 1024)


def test_disk_pause_and_hysteresis(tmp_path, monkeypatch):
    buf = make_buffer(tmp_path, min_free_mb=200)
    monkeypatch.setattr("app.remote_collector.buffer.shutil.disk_usage",
                        lambda *a, **k: _Usage(100))
    assert buf.check_disk() == "disk_low"
    assert buf.summary()["paused_reason"] == "disk_low"
    monkeypatch.setattr("app.remote_collector.buffer.shutil.disk_usage",
                        lambda *a, **k: _Usage(220))
    assert buf.check_disk() == "disk_low"
    monkeypatch.setattr("app.remote_collector.buffer.shutil.disk_usage",
                        lambda *a, **k: _Usage(260))
    assert buf.check_disk() is None
    assert buf.summary()["paused_reason"] is None


def test_buffer_epoch_stable(tmp_path):
    buf = make_buffer(tmp_path)
    epoch = buf.buffer_epoch()
    assert epoch
    buf2 = Buffer(buf.config)
    assert buf2.buffer_epoch() == epoch
