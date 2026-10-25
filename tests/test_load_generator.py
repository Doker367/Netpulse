"""Tests del generador de carga sintética (determinismo, reparto y ritmo)."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "scripts" / "load_generator.py"
spec = importlib.util.spec_from_file_location("load_generator", MODULE_PATH)
load_generator = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = load_generator
spec.loader.exec_module(load_generator)

TS = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


def test_build_devices_prefix_and_repartition():
    devices = load_generator.build_devices(500, 5, seed=7)
    assert len(devices) == 5
    assert all(len(items) == 100 for items in devices.values())
    ids = [device.device_id for items in devices.values() for device in items]
    assert len(ids) == 500
    assert len(set(ids)) == 500
    assert all(device_id.startswith("sim-") for device_id in ids)
    assert set(devices) == {"site-1", "site-2", "site-3", "site-4", "site-5"}


def test_build_tick_is_deterministic_with_seed():
    devices = load_generator.build_devices(20, 2, seed=11)
    first = load_generator.build_tick(
        devices["site-1"], tick=3, ts=TS, seed=11, down_prob=0.0
    )
    again = load_generator.build_tick(
        devices["site-1"], tick=3, ts=TS, seed=11, down_prob=0.0
    )
    assert first == again
    assert len(first) == len(devices["site-1"]) * 3
    assert {sample["metric"] for sample in first} == {
        "reachable", "latency_ms", "packet_loss_pct"
    }
    assert all(sample["ts"] == TS.isoformat(timespec="microseconds") for sample in first)

    other_seed = load_generator.build_tick(
        devices["site-1"], tick=3, ts=TS, seed=12, down_prob=0.0
    )
    assert other_seed != first


def test_pace_respects_batch_and_request_budget():
    pace = load_generator.plan_pace(
        devices=500, sites=5, rate=999, probe_interval_s=60,
        batch_size=400, max_requests_per_minute=90,
    )
    # 5 lotes/tick como máximo con 90 req/min → tick >= 3.33 s.
    assert pace.batches_per_tick == 5
    assert pace.wall_tick_s >= 5 / (90 / 60) - 1e-3
    assert pace.limited_by == "request_budget"
    assert pace.effective_rate <= 999

    batches = load_generator.chunk_samples([{"i": index} for index in range(1000)], 400)
    assert [len(batch) for batch in batches] == [400, 400, 200]

    production = load_generator.plan_pace(
        devices=100, sites=1, rate=0, probe_interval_s=60, batch_size=400,
        max_requests_per_minute=90,
    )
    assert production.wall_tick_s == 60.0
    assert production.effective_rate == 5.0
