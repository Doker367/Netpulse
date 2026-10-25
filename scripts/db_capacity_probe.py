#!/usr/bin/env python3
"""Mide el almacén SQLite de colectores remotos con las consultas reales del panel.

No modifica la DB (solo lectura + PRAGMA query_only). Devuelve JSON por stdout.
Uso:
    python scripts/db_capacity_probe.py /ruta/remote.db --iterations 20
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import time
from pathlib import Path

# SQL copiado de app/services/remote/query_svc.py (vista "Sites" del panel).
LIST_SITES_COLLECTORS = (
    "SELECT * FROM remote_collector ORDER BY site_id, collector_id"
)
LIST_SITES_COUNTS = (
    "SELECT site_id, COUNT(*) AS total, "
    "SUM(CASE WHEN ok=1 AND reachable=1 THEN 1 ELSE 0 END) AS reachable, "
    "SUM(CASE WHEN ok=1 AND reachable=0 THEN 1 ELSE 0 END) AS unreachable, "
    "SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) AS unverifiable, "
    "SUM(CASE WHEN inventory_match=0 THEN 1 ELSE 0 END) AS unmatched "
    "FROM remote_device_state GROUP BY site_id"
)
# Vista de dispositivos de una sede (list_devices).
SITE_DEVICES = "SELECT * FROM remote_device_state WHERE site_id=? ORDER BY device_id"
# Histórico de un equipo, 6 h (history).
DEVICE_HISTORY = (
    "SELECT ts, collector_id, metric, value_num, value_bool, ok, probe_kind, error "
    "FROM remote_sample WHERE site_id=? AND device_id=? AND ts >= ? "
    "ORDER BY ts ASC, metric ASC"
)
# Tamaño lógico de la tabla + índices.
SIZE_BREAKDOWN = (
    "SELECT name, SUM(pgsize) AS bytes FROM dbstat GROUP BY name ORDER BY bytes DESC"
)


def _timeit(conn: sqlite3.Connection, sql: str, params: tuple = (), iterations: int = 20) -> dict:
    samples: list[float] = []
    rows = 0
    for _ in range(iterations):
        started = time.perf_counter()
        rows = len(conn.execute(sql, params).fetchall())
        samples.append((time.perf_counter() - started) * 1000)
    ordered = sorted(samples)
    return {
        "iterations": iterations,
        "rows_returned": rows,
        "p50_ms": round(statistics.median(samples), 3),
        "p95_ms": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 3),
        "max_ms": round(max(samples), 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("db", type=Path)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--site", default="site-1")
    parser.add_argument("--device", default="sim-site-1-0001")
    parser.add_argument("--hours", type=float, default=6.0)
    args = parser.parse_args()

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("remote_collector", "remote_sample", "remote_device_state")
        }
        cutoff = conn.execute(
            "SELECT MAX(ts) FROM remote_sample"
        ).fetchone()[0]
        # Ventana relativa al último ts recibido (no al reloj del bench).
        from datetime import datetime, timedelta
        last = datetime.fromisoformat(cutoff) if cutoff else None
        history = None
        if last is not None:
            since = (last - timedelta(hours=args.hours)).isoformat(timespec="microseconds")
            history = _timeit(
                conn, DEVICE_HISTORY, (args.site, args.device, since), args.iterations
            )
        size_bytes = args.db.stat().st_size
        wal = args.db.with_name(args.db.name + "-wal")
        shm = args.db.with_name(args.db.name + "-shm")
        size_bytes += wal.stat().st_size if wal.exists() else 0
        size_bytes += shm.stat().st_size if shm.exists() else 0
        page_count = conn.execute("PRAGMA page_count").fetchone()[0]
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        try:
            dbstat = {
                row["name"]: row["bytes"]
                for row in conn.execute(SIZE_BREAKDOWN).fetchall()
            }
        except sqlite3.DatabaseError:
            dbstat = {}
        result = {
            "db": str(args.db),
            "size_bytes": size_bytes,
            "size_mb": round(size_bytes / (1024 * 1024), 3),
            "page_count": page_count,
            "page_size": page_size,
            "counts": counts,
            "bytes_per_row": round(size_bytes / counts["remote_sample"], 2)
            if counts["remote_sample"] else None,
            "oldest_ts": conn.execute("SELECT MIN(ts) FROM remote_sample").fetchone()[0],
            "newest_ts": cutoff,
            "queries": {
                "list_sites_collectors": _timeit(conn, LIST_SITES_COLLECTORS, (), args.iterations),
                "list_sites_counts": _timeit(conn, LIST_SITES_COUNTS, (), args.iterations),
                "site_devices": _timeit(conn, SITE_DEVICES, (args.site,), args.iterations),
                "device_history_6h": history,
            },
            "dbstat_bytes": dbstat,
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
