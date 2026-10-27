#!/usr/bin/env python3
"""NetPulse — banco de pruebas de PODA y CONCURRENCIA del almacén SQLite remoto.

Tarjeta 12E. Mide, sobre una DB **temporal**, lo que ``docs/capacidad.md`` dejó
como "no medido":

- tamaño real (archivo + WAL + SHM) de 30 días de muestras sintéticas;
- tiempo de la función REAL ``app.services.remote.store.prune_samples``, con el
  mismo esquema, los mismos PRAGMA (WAL, ``synchronous=NORMAL``,
  ``busy_timeout=5000``) y el mismo envoltorio transaccional que usa la ingesta
  (``store.transaction()`` → ``BEGIN IMMEDIATE``);
- duración de esa poda con 0, 1 y 2 **procesos** escritores concurrentes, cada
  uno con conexiones cortas y lotes de 300 filas (el patrón de la ingesta);
- errores ``database is locked`` y filas/s de cada escritor.

Lo que NO mide (a propósito, y así se reporta):

- la ruta HTTP ``POST /api/remote/ingest`` (validación Pydantic, JWT, métricas):
  aquí se escribe con el mismo SQL y las mismas transacciones, no por HTTP;
- varios workers Uvicorn (el bench usa N procesos escritores sueltos, que es el
  peor caso de contención sin el ``_WRITE_LOCK`` de un solo proceso);
- discos lentos/NFS: usa el disco local de la máquina donde se ejecuta.

Nunca toca la DB del repo: crea todo en ``tempfile.mkdtemp()`` y lo borra al
terminar (``--keep`` lo conserva para inspección). Los ``device_id`` son
sintéticos (``bench-dev-*``) y el reloj es fijo, para que la carga sea
determinista; la contención temporal no lo es y se reporta tal cual.

Ejemplos::

    PYTHONPATH=. python scripts/capacity_prune_bench.py
    PYTHONPATH=. python scripts/capacity_prune_bench.py --devices 80 --keep
    PYTHONPATH=. python scripts/capacity_prune_bench.py --help
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# Reloj sintético fijo: 30 días de historia que terminan aquí. Determinista.
SYNTHETIC_END = datetime(2026, 10, 1, tzinfo=timezone.utc)
PROBE_INTERVAL_S = 60
METRICS = ("reachable", "latency_ms", "packet_loss_pct")
INSERT_SQL = (
    "INSERT INTO remote_sample "
    "(site_id, collector_id, device_id, metric, ts, received_at, "
    "sync_batch_id, value_num, value_bool, ok, probe_kind, error) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
    "ON CONFLICT (site_id, device_id, ts, metric) DO NOTHING"
)
SITES = 2
SEED_ROWS_PER_TRANSACTION = 5000
WRITER_BATCH = 300


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _set_db(path: Path, retention_days: int | None = None) -> None:
    """Apunta el módulo real del almacén a la DB del bench (nunca la del repo)."""
    resolved = Path(path).resolve()
    if REPO.resolve() in resolved.parents:
        raise SystemExit(f"DB dentro del repo: {resolved} — el bench no toca el repo")
    os.environ["NETPULSE_REMOTE_DB"] = str(resolved)
    if retention_days is not None:
        os.environ["NETPULSE_REMOTE_RETENTION_DAYS"] = str(retention_days)
    else:
        os.environ.setdefault("NETPULSE_REMOTE_RETENTION_DAYS", "30")


def _db_size(path: Path) -> int:
    total = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            total += Path(str(path) + suffix).stat().st_size
        except OSError:
            pass
    return total


def _mib(n: int) -> float:
    return round(n / 1024 / 1024, 2)


def _db_stats(path: Path) -> dict:
    """Páginas totales/libres: un DELETE deja huecos reutilizables, no encoge."""
    try:
        conn = sqlite3.connect(str(path), timeout=10)
        try:
            page_count = conn.execute("PRAGMA page_count").fetchone()[0]
            freelist = conn.execute("PRAGMA freelist_count").fetchone()[0]
            page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return {"page_count": None, "freelist_count": None, "page_size": None}
    return {
        "page_count": page_count,
        "freelist_count": freelist,
        "page_size": page_size,
        "freelist_pct": round(freelist / page_count * 100, 1) if page_count else None,
    }


def _hardware() -> dict:
    def sysctl(key: str) -> str | None:
        try:
            return subprocess.check_output(["sysctl", "-n", key], text=True).strip()
        except Exception:
            return None

    ram = sysctl("hw.memsize")
    disk = sysctl("hw.model")
    ssd = None
    try:
        out = subprocess.check_output(
            ["diskutil", "info", "/System/Volumes/Data"], text=True, stderr=subprocess.DEVNULL
        )
        for line in out.splitlines():
            if "Solid State" in line:
                ssd = line.split(":", 1)[1].strip()
    except Exception:
        ssd = None
    try:
        os_name = subprocess.check_output(["sw_vers", "-productVersion"], text=True).strip()
    except Exception:
        os_name = platform.platform()
    return {
        "os": f"macOS {os_name}" if os_name[0].isdigit() else os_name,
        "model": disk,
        "cpu": sysctl("machdep.cpu.brand_string"),
        "ram_gb": round(int(ram) / 1024**3, 1) if ram else None,
        "solid_state": ssd,
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
    }


# ── Fase 1: sembrar 30 días de muestras sintéticas ───────────────────────────


def seed(path: Path, devices: int, days: int, seed_value: int, retention: int) -> dict:
    _set_db(path, retention)
    from app.services.remote import store  # importa después de fijar la ruta

    rnd = random.Random(seed_value)
    start = SYNTHETIC_END - timedelta(days=days)
    probes = days * 86400 // PROBE_INTERVAL_S
    per_device = [f"bench-dev-{i:04d}" for i in range(devices)]
    site_of = {dev: f"site-{i % SITES + 1}" for i, dev in enumerate(per_device)}
    col_of = {dev: f"col-{i % SITES + 1}" for i, dev in enumerate(per_device)}
    total_rows = probes * devices * len(METRICS)

    if path.exists():
        path.unlink()
    # store._connect() aplica el esquema REAL y los PRAGMA de producción.
    conn = store._connect()
    t0 = time.perf_counter()
    written = 0
    batch: list[tuple] = []
    try:
        for p in range(probes):
            ts = _iso(start + timedelta(seconds=p * PROBE_INTERVAL_S))
            for dev in per_device:
                site, col = site_of[dev], col_of[dev]
                reachable = 0 if rnd.random() < 0.005 else 1
                latency = round(max(0.1, rnd.lognormvariate(1.2, 0.6)), 3)
                loss = 0.0 if rnd.random() > 0.01 else round(rnd.random() * 25, 3)
                batch.append((site, col, dev, "reachable", ts, ts, "bench", None,
                              1 if reachable else 0, 1, "icmp", None))
                batch.append((site, col, dev, "latency_ms", ts, ts, "bench",
                              latency if reachable else None, None, 1, "icmp", None))
                batch.append((site, col, dev, "packet_loss_pct", ts, ts, "bench",
                              loss, None, 1, "icmp", None))
                if len(batch) >= SEED_ROWS_PER_TRANSACTION:
                    with store.transaction() as c:
                        c.executemany(INSERT_SQL, batch)
                    written += len(batch)
                    batch = []
        if batch:
            with store.transaction() as c:
                c.executemany(INSERT_SQL, batch)
            written += len(batch)
            batch = []
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    elapsed = time.perf_counter() - t0
    with store.read_connection() as c:
        in_db = c.execute("SELECT COUNT(*) FROM remote_sample").fetchone()[0]
    return {
        "rows_requested": total_rows,
        "rows_written": written,
        "rows_in_db": in_db,
        "seconds": round(elapsed, 2),
        "rows_per_s": round(written / elapsed, 0) if elapsed else None,
        "db_mib": _mib(_db_size(path)),
        "bytes_per_row": round(_db_size(path) / in_db, 1) if in_db else None,
        "days": days,
        "devices": devices,
        "probe_interval_s": PROBE_INTERVAL_S,
        "synthetic_end": _iso(SYNTHETIC_END),
    }


# ── Fase 2: poda real (store.prune_samples) ──────────────────────────────────


def prune_once(path: Path, now: datetime, retention: int, rows_before: int | None = None) -> dict:
    """Ejecuta la poda REAL con el mismo envoltorio que la ingesta."""
    _set_db(path, retention)
    from app.services.remote import store

    if rows_before is None:
        conn = sqlite3.connect(str(path), timeout=10)
        try:
            rows_before = conn.execute("SELECT COUNT(*) FROM remote_sample").fetchone()[0]
        finally:
            conn.close()
    before_rows = rows_before
    before_bytes = _db_size(path)
    stats_before = _db_stats(path)
    t0 = time.perf_counter()
    deleted = None
    error = None
    try:
        # Igual que ingest_svc._maybe_prune: BEGIN IMMEDIATE + commit del lote.
        with store.transaction() as c:
            deleted = store.prune_samples(c, now)
        ok = True
    except Exception as exc:  # RemoteStoreUnavailable / sqlite3.Error
        ok = False
        error = f"{type(exc).__name__}: {exc}"
    elapsed = time.perf_counter() - t0
    cut_bytes = _db_size(path)
    # Checkpoint para saber cuánto ocupa de verdad tras la poda (no es VACUUM).
    try:
        conn = sqlite3.connect(str(path), timeout=10)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    except sqlite3.Error:
        pass
    after_bytes = _db_size(path)
    try:
        conn = sqlite3.connect(str(path), timeout=10)
        after_rows = conn.execute("SELECT COUNT(*) FROM remote_sample").fetchone()[0]
        conn.close()
    except sqlite3.Error:
        after_rows = None
    return {
        "ok": ok,
        "error": error,
        "now": _iso(now),
        "deleted": deleted,
        "rows_before": before_rows,
        "rows_after": after_rows,
        "seconds": round(elapsed, 2),
        "rows_per_s": round((deleted or 0) / elapsed, 0) if elapsed else None,
        "mib_before": _mib(before_bytes),
        "mib_after_delete_incl_wal": _mib(cut_bytes),
        "mib_after_checkpoint": _mib(after_bytes),
        "pages_before": stats_before,
        "pages_after": _db_stats(path),
        "locked": bool(error and "locked" in error.lower()),
    }


# ── Fase 3: escritores concurrentes en procesos separados ────────────────────


def writer_main(path: Path, duration: float, writer_id: int, start_after: float, retention: int) -> None:
    """Proceso escritor: lotes de 300 filas con conexión corta por transacción."""
    _set_db(path, retention)
    from app.services.remote import store

    if start_after > 0:
        time.sleep(start_after)
    rnd = random.Random(9000 + writer_id)
    t_end = time.perf_counter() + duration
    base = SYNTHETIC_END + timedelta(days=1, seconds=writer_id * 3600)
    rows = 0
    batches = 0
    locked = 0
    other_errors = 0
    seq = 0
    t0 = time.perf_counter()
    while time.perf_counter() < t_end:
        batch = []
        for _ in range(WRITER_BATCH):
            ts = _iso(base + timedelta(microseconds=seq))
            seq += 1
            batch.append((
                f"site-{writer_id + 1}", f"col-{writer_id + 1}",
                f"bench-w{writer_id}-{seq % 10}", "latency_ms", ts, ts,
                "bench-live", round(1 + rnd.random() * 40, 3), None, 1, "icmp", None,
            ))
        try:
            with store.transaction() as c:
                c.executemany(INSERT_SQL, batch)
            rows += WRITER_BATCH
            batches += 1
        except Exception as exc:  # RemoteStoreUnavailable habitualmente
            if "locked" in str(exc).lower():
                locked += 1
            else:
                other_errors += 1
    elapsed = time.perf_counter() - t0
    print(json.dumps({
        "writer_id": writer_id,
        "seconds": round(elapsed, 2),
        "rows": rows,
        "batches": batches,
        "rows_per_s": round(rows / elapsed, 0) if elapsed else 0,
        "locked": locked,
        "other_errors": other_errors,
    }))


def scenario(path: Path, base_db: Path, name: str, writers: int, prune_now: datetime,
             writer_seconds: float, retention: int, base_rows: int) -> dict:
    """Copia la DB base y mide la poda con N escritores ya en marcha."""
    for suffix in ("", "-wal", "-shm"):
        target = Path(str(path) + suffix)
        if target.exists():
            target.unlink()
    shutil.copy2(base_db, path)  # base con checkpoint: sin -wal pendiente
    procs = [
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--writer", str(path),
             str(writer_seconds), str(i), "0.5", str(retention)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for i in range(writers)
    ]
    time.sleep(1.2)  # los escritores ya están dentro de su bucle
    result = prune_once(path, prune_now, retention, base_rows)
    writer_results = []
    for p in procs:
        out, err = p.communicate(timeout=writer_seconds + 120)
        try:
            writer_results.append(json.loads(out.strip().splitlines()[-1]))
        except Exception:
            writer_results.append({"error": (err or out or "sin salida")[-400:]})
    result["scenario"] = name
    result["writers"] = writers
    result["writer_results"] = writer_results
    result["writer_rows"] = sum(w.get("rows", 0) for w in writer_results)
    result["writer_rows_per_s"] = sum(w.get("rows_per_s", 0) for w in writer_results)
    result["writer_locked_errors"] = sum(w.get("locked", 0) for w in writer_results)
    result["writer_other_errors"] = sum(w.get("other_errors", 0) for w in writer_results)
    result["mib_after_scenario"] = _mib(_db_size(path))
    return result


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Banco de pruebas de poda y concurrencia SQLite (DB temporal, nunca la del repo).",
    )
    ap.add_argument("--devices", type=int, default=40,
                    help="equipos sintéticos (40 × 30 días ≈ 5.18 M filas; por defecto)")
    ap.add_argument("--days", type=int, default=30, help="días de historia a sembrar (30)")
    ap.add_argument("--prune-days", type=int, default=7,
                    help="antigüedad de la poda medida: borra los primeros N días (7)")
    ap.add_argument("--retention-days", type=int, default=30,
                    help="retención aplicada por prune_samples (30, = NETPULSE_REMOTE_RETENTION_DAYS)")
    ap.add_argument("--writer-seconds", type=float, default=25.0,
                    help="duración de cada proceso escritor concurrente (25 s)")
    ap.add_argument("--seed", type=int, default=1205, help="semilla del generador (1205)")
    ap.add_argument("--max-rows", type=int, default=8_000_000,
                    help="aborta si la siembra proyectada supera este tope (8 M)")
    ap.add_argument("--keep", action="store_true", help="conserva la DB temporal al terminar")
    ap.add_argument("--json", type=str, default=None, help="escribe el resultado JSON en esta ruta")
    args = ap.parse_args()

    projected = args.days * 86400 // PROBE_INTERVAL_S * args.devices * len(METRICS)
    if projected > args.max_rows:
        ap.error(
            f"la siembra proyectada ({projected:,} filas) supera --max-rows "
            f"({args.max_rows:,}); baja --devices o --days"
        )
    # Corte = end + prune_days - retention. Solo hay algo que borrar si cae
    # dentro de la ventana sembrada: days > retention - prune_days.
    if args.days <= args.retention_days - args.prune_days:
        ap.error(
            f"con --days {args.days}, --prune-days {args.prune_days} y retención "
            f"{args.retention_days} no hay ninguna fila más antigua que el corte: "
            f"sube --days o --prune-days"
        )

    workdir = Path(tempfile.mkdtemp(prefix="np-prune-bench-"))
    base_db = workdir / "base.db"
    print(f"workdir temporal: {workdir}")
    print(f"filas proyectadas: {projected:,} "
          f"({args.devices} equipos × {args.days} días × {len(METRICS)} métricas × "
          f"{86400 // PROBE_INTERVAL_S} sondas/día)")
    report: dict = {"hardware": _hardware(), "params": vars(args), "projected_rows": projected}

    try:
        # 1) Siembra
        t0 = time.perf_counter()
        report["seed"] = seed(base_db, args.devices, args.days, args.seed, args.retention_days)
        report["seed"]["wall_s"] = round(time.perf_counter() - t0, 2)
        print(f"siembra: {report['seed']['rows_in_db']:,} filas, "
              f"{report['seed']['db_mib']} MiB, {report['seed']['seconds']} s "
              f"({report['seed']['rows_per_s']:,} filas/s)")

        # 2) Poda de régimen (1 h de retención) sobre una copia: el coste que
        #    paga la ingesta cada hora en producción.
        steady_path = workdir / "steady.db"
        shutil.copy2(base_db, steady_path)
        report["prune_steady_1h"] = prune_once(
            steady_path, SYNTHETIC_END + timedelta(hours=1),
            args.retention_days, report["seed"]["rows_in_db"],
        )
        print(f"poda de régimen (+1 h): {report['prune_steady_1h']['seconds']} s · "
              f"borradas {report['prune_steady_1h']['deleted']:,} filas")
        for suffix in ("", "-wal", "-shm"):
            try:
                Path(str(steady_path) + suffix).unlink()
            except OSError:
                pass

        # 3) Poda atrasada (N días) sola + con 1 y 2 escritores (misma DB copiada)
        prune_now = SYNTHETIC_END + timedelta(days=args.prune_days)
        report["prune_cutoff"] = _iso(prune_now)
        scenarios = []
        for name, writers in (("poda sola", 0), ("poda + 1 escritor", 1), ("poda + 2 escritores", 2)):
            sc_path = workdir / f"scenario-{writers}.db"
            print(f"escenario: {name}…")
            sc = scenario(sc_path, base_db, name, writers, prune_now, args.writer_seconds,
                          args.retention_days, report["seed"]["rows_in_db"])
            scenarios.append(sc)
            print(f"  poda {sc['seconds']} s · borradas {sc['deleted']:,} filas · "
                  f"escritores {sc['writer_rows']:,} filas ({sc['writer_rows_per_s']:,} filas/s) · "
                  f"locks {sc['writer_locked_errors']} · error poda: {sc['error']}")
            if not args.keep:
                for suffix in ("", "-wal", "-shm"):
                    try:
                        Path(str(sc_path) + suffix).unlink()
                    except OSError:
                        pass
        report["scenarios"] = scenarios
    finally:
        if args.keep:
            print(f"DB conservada en {workdir} (--keep)")
        else:
            shutil.rmtree(workdir, ignore_errors=True)
            print("DBs temporales borradas")

    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"JSON: {args.json}")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    # Modo escritor (proceso separado).
    if len(sys.argv) >= 6 and sys.argv[1] == "--writer":
        writer_main(Path(sys.argv[2]), float(sys.argv[3]), int(sys.argv[4]),
                    float(sys.argv[5]) if len(sys.argv) > 5 else 0.0,
                    int(sys.argv[6]) if len(sys.argv) > 6 else 30)
        sys.exit(0)
    sys.exit(main())
