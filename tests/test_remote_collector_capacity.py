"""Medición real del buffer: 100 devices × 1 día a 60 s (sección 5.3).

No es una extrapolación: inserta 432 000 filas y mide el archivo resultante.
"""

import sqlite3
import time
from datetime import datetime, timedelta, timezone

from app.remote_collector.buffer import Buffer
from app.remote_collector.config import BufferConfig, DeviceSpec
from app.services.collectors.probe_svc import ProbeResult


def test_capacity_100_devices_24h(tmp_path):
    buf = Buffer(BufferConfig(
        path=tmp_path / "capacity.sqlite3",
        max_rows=500000,
        max_age_hours=72,
        synced_retention_hours=6,
        min_free_mb=200,
    ))
    devices = [
        DeviceSpec(f"dev-{i:03d}", f"10.10.{i // 250}.{i % 250}", 22, "ssh", "ios")
        for i in range(100)
    ]
    result = ProbeResult(True, True, 1.0, 0.0, "icmp")
    started = time.monotonic()
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for cycle in range(1440):
        # Timestamps únicos del día, separados 60 s.
        ts = (base + timedelta(minutes=cycle)).isoformat(timespec="microseconds")
        buf.insert_probe(ts, [(device, result) for device in devices])
    elapsed = time.monotonic() - started
    with sqlite3.connect(buf.path) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    size_mb = buf.db_size_bytes() / (1024 * 1024)
    counts = buf.counts()
    print(
        f"\ncapacity: rows={counts['total']} size_mb={size_mb:.1f} "
        f"seconds={elapsed:.1f} rows_per_s={counts['total'] / max(elapsed, 0.001):.0f}"
    )
    assert counts["total"] == 432000
    assert size_mb < 250
