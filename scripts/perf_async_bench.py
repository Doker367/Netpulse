#!/usr/bin/env python3
"""NetPulse — banco de pruebas de I/O bloqueante en handlers async.

Dos modos, ambos reproducibles y sin datos de laboratorio:

``live``
    Arranca la API real (uvicorn, poller desactivado) y mide el *lag* del
    event loop mientras se ejecuta el endpoint objetivo. El lag se observa
    con peticiones canario ``GET /api/health`` lanzadas cada 25 ms: si un
    handler bloquea el loop, el canario se queda esperando y su latencia
    refleja el bloqueo. Además lanza N peticiones concurrentes a
    ``/api/system/overview`` para reproducir el "se congela el dashboard".

``docker-demo``
    No necesita la API ni Docker real. Crea un ``docker`` falso en el PATH
    que tarda 2 s en responder (el mismo caso medido en producción) y
    compara, en el mismo proceso y con el mismo comando:

    * ``old``: ``subprocess.run`` síncrono dentro de un handler ``async``
      (patrón que congela el event loop).
    * ``new``: ``await docker_stats_svc.snapshot_async()`` (to_thread con
      caché y timeout).

Uso::

    PYTHONPATH=. python scripts/perf_async_bench.py --mode live
    PYTHONPATH=. python scripts/perf_async_bench.py --mode docker-demo

Salida: tabla por escenario + JSON opcional con ``--json out.json``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


# ── utilidades de medición ───────────────────────────────────


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = min(len(s) - 1, max(0, int(round((p / 100.0) * (len(s) - 1)))))
    return round(s[k], 1)


def _summary(values: list[float]) -> dict:
    return {
        "n": len(values),
        "p50_ms": _pct(values, 50),
        "p95_ms": _pct(values, 95),
        "max_ms": round(max(values), 1) if values else 0.0,
        "mean_ms": round(statistics.fmean(values), 1) if values else 0.0,
    }


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _wait_ready(client, url: str, timeout: float = 20.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            r = await client.get(url)
            if r.status_code < 500:
                return
        except Exception:
            pass
        await asyncio.sleep(0.1)
    raise RuntimeError(f"La API no arrancó en {timeout}s")


# ── modo live ────────────────────────────────────────────────


async def _live(args: argparse.Namespace) -> dict:
    import httpx

    from app.core.security import create_access_token

    token = create_access_token({"sub": "bench", "role": "viewer"})
    headers = {"Authorization": f"Bearer {token}"}

    port = _free_port()
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO),
        "NETPULSE_METRICS_ENABLED": "false",
        "NETPULSE_SYSLOG_ENABLED": "false",
        "NETPULSE_METRICS_INTERVAL": "0",
    }
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=REPO, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    results: dict = {"mode": "live", "base_url": base, "endpoint": args.endpoint}

    try:
        async with httpx.AsyncClient(base_url=base, timeout=30.0) as client:
            await _wait_ready(client, "/api/health")

            # Calentamiento (imports, pool de threads, JIT de plantillas).
            await client.get("/api/system/overview", headers=headers)
            await client.get("/api/metrics")

            async def canary_loop(stop: asyncio.Event, samples: list[float]) -> None:
                while not stop.is_set():
                    t0 = time.perf_counter()
                    try:
                        await client.get("/api/health")
                        samples.append((time.perf_counter() - t0) * 1000)
                    except Exception:
                        pass
                    await asyncio.sleep(args.canary_interval)

            async def timed(path: str) -> tuple[int, float]:
                t0 = time.perf_counter()
                r = await client.get(path, headers=headers)
                return r.status_code, (time.perf_counter() - t0) * 1000

            for round_no in range(args.rounds):
                canary: list[float] = []
                stop = asyncio.Event()
                watcher = asyncio.create_task(canary_loop(stop, canary))

                # El endpoint afectado + 20 peticiones concurrentes al
                # dashboard. Si el handler bloquea el loop, todas esperan.
                burst = [asyncio.create_task(timed(args.endpoint))]
                burst += [
                    asyncio.create_task(timed("/api/system/overview"))
                    for _ in range(args.concurrency)
                ]
                out = await asyncio.gather(*burst)
                stop.set()
                await watcher

                codes = [c for c, _ in out]
                target_ms = out[0][1]
                concurrent = [ms for _, ms in out[1:]]
                results.setdefault("rounds", []).append({
                    "target_status": codes[0],
                    "target_ms": round(target_ms, 1),
                    "concurrent_codes": sorted(set(codes[1:])),
                    "concurrent_overview": _summary(concurrent),
                    "canary_under_load": _summary(canary),
                })
                await asyncio.sleep(0.2)
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()

    rounds = results.get("rounds", [])
    results["aggregate"] = {
        "target_p95_ms": _pct([r["target_ms"] for r in rounds], 95),
        "canary_p95_ms": _pct(
            [r["canary_under_load"]["p95_ms"] for r in rounds], 50
        ),
        "canary_max_ms": max((r["canary_under_load"]["max_ms"] for r in rounds), default=0),
        "overview_p95_ms": _pct(
            [r["concurrent_overview"]["p95_ms"] for r in rounds], 50
        ),
        "overview_max_ms": max(
            (r["concurrent_overview"]["max_ms"] for r in rounds), default=0
        ),
    }
    return results


# ── modo docker-demo ─────────────────────────────────────────


_FAKE_DOCKER = """#!/bin/sh
sleep 2
printf 'netpulse-lab1\\t1.2MB / 3.4MB\\n'
printf 'netpulse-lab2\\t0.5MB / 0.1MB\\n'
"""


def _old_docker_stats() -> dict[str, tuple[str, str]]:
    """Patrón antiguo: subprocess síncrono dentro de un handler async."""
    out = subprocess.check_output(
        ["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.NetIO}}"],
        stderr=subprocess.DEVNULL,
        timeout=5.0,
    ).decode()
    result: dict[str, tuple[str, str]] = {}
    for line in out.strip().splitlines():
        parts = line.split("\t")
        if len(parts) == 2 and " / " in parts[1]:
            rx, tx = parts[1].split(" / ")
            result[parts[0].strip()] = (rx.strip(), tx.strip())
    return result


async def _lag_probe(stop: asyncio.Event) -> float:
    """Máximo retraso del loop muestreando cada 20 ms."""
    worst = 0.0
    while not stop.is_set():
        t0 = time.perf_counter()
        await asyncio.sleep(0.02)
        worst = max(worst, (time.perf_counter() - t0 - 0.02) * 1000)
    return worst


async def _docker_demo(args: argparse.Namespace) -> dict:
    from fastapi import FastAPI
    import httpx

    with tempfile.TemporaryDirectory(prefix="np-bench-docker-") as tmp:
        fake = Path(tmp) / "docker"
        fake.write_text(_FAKE_DOCKER)
        fake.chmod(0o755)
        os.environ["PATH"] = tmp + os.pathsep + os.environ.get("PATH", "")
        # El docker falso tarda 2 s; damos margen al timeout del servicio.
        os.environ["NETPULSE_DOCKER_STATS_TIMEOUT"] = "5"

        # Importar el servicio nuevo si esta rama lo trae; si no, old como
        # referencia (permite medir el "antes" sin tocar código).
        try:
            from app.services import docker_stats_svc
            has_service = True
        except Exception:
            docker_stats_svc = None
            has_service = False

        app = FastAPI()

        @app.get("/old")
        async def old_handler():
            return _old_docker_stats()

        @app.get("/new")
        async def new_handler():
            if not has_service:
                return _old_docker_stats()
            return await docker_stats_svc.snapshot_async(refresh=True)

        result: dict = {
            "mode": "docker-demo",
            "fake_docker_sleep_s": 2.0,
            "service_available": has_service,
            "scenarios": {},
        }

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://bench") as client:
            for name, path in (("old_sync_in_async", "/old"), ("new_to_thread", "/new")):
                if name == "new_to_thread" and not has_service:
                    result["scenarios"][name] = {"skipped": "docker_stats_svc no existe"}
                    continue
                gaps: list[float] = []
                latencies: list[float] = []
                for _ in range(args.rounds):
                    stop = asyncio.Event()
                    probe = asyncio.create_task(_lag_probe(stop))
                    await asyncio.sleep(0.05)  # deja arrancar el muestreador
                    t0 = time.perf_counter()
                    r = await client.get(path, timeout=30)
                    latencies.append((time.perf_counter() - t0) * 1000)
                    stop.set()
                    gaps.append(await probe)
                    assert r.status_code == 200, r.text
                result["scenarios"][name] = {
                    "target_request_ms": _summary(latencies),
                    "event_loop_max_block_ms": round(max(gaps), 1),
                    "event_loop_gaps_ms": [round(g, 1) for g in gaps],
                }
        return result


# ── CLI ──────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["live", "docker-demo"], default="live")
    parser.add_argument("--endpoint", default="/api/metrics",
                        help="Endpoint objetivo del modo live")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--canary-interval", type=float, default=0.025,
                        help="Segundos entre peticiones canario (modo live)")
    parser.add_argument("--json", default="", help="Guardar el resultado en JSON")
    args = parser.parse_args()

    sys.path.insert(0, str(REPO))
    runner = _live if args.mode == "live" else _docker_demo
    results = asyncio.run(runner(args))

    print(json.dumps(results, indent=2, ensure_ascii=False))
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2, ensure_ascii=False))
        print(f"\n→ {args.json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
