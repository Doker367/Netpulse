#!/usr/bin/env python3
"""Banco de rendimiento del inventario (tarjeta 13A).

Mide, sobre un ``devices.yaml`` temporal con N equipos y el pipeline ASGI
real (middleware incluido, sin lifespan), dos rutas:

* ``GET /api/devices/health`` — coste de sondeo/caché.
* ``PUT /api/devices/{id}`` — coste de una actualización puntual.

No toca ``config/devices.yaml`` ni el inventario real: crea el YAML en un
directorio temporal y redirige ``inventory_svc.DEVICES_FILE``.

Uso::

    PYTHONPATH=. .venv/bin/python scripts/perf_devices_bench.py \
        --devices 500 --put-iterations 30 --health-iterations 5 --cold

Los equipos sintéticos se reparten en tres perfiles:

* ``open``      — un listener TCP local efímero (responde).
* ``refused``   — ``127.0.0.1`` en un puerto cerrado (ECONNREFUSED rápido).
* ``blackhole`` — ``192.0.2.0/24`` (TEST-NET-1), sin respuesta: fuerza timeout.

La mezcla es configurable con ``--open/--refused/--blackhole``. Es una
simulación: las cifras no representan una red real concreta.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def _summary(values_ms: list[float]) -> dict:
    return {
        "n": len(values_ms),
        "p50": round(statistics.median(values_ms), 1),
        "p95": round(_percentile(values_ms, 95), 1),
        "p99": round(_percentile(values_ms, 99), 1),
        "min": round(min(values_ms), 1),
        "max": round(max(values_ms), 1),
    }


def _make_devices(n: int, open_host: str, open_port: int,
                  n_open: int, n_refused: int, n_blackhole: int) -> list[dict]:
    # Perfiles intercalados (semilla fija): si el inventario tuviera primero
    # todos los equipos vivos, el presupuesto incremental de health siempre
    # acertaría y la medición sería optimista. El peor caso realista es una
    # mezcla, que es lo que se mide.
    kinds = ["open"] * n_open + ["refused"] * n_refused + ["blackhole"] * n_blackhole
    random.Random(13).shuffle(kinds)
    devices = []
    for i, kind in enumerate(kinds):
        if kind == "open":
            hostname, port, protocol = open_host, open_port, "ssh"
        elif kind == "refused":
            hostname, port, protocol = "127.0.0.1", 1, "ssh"
        else:
            hostname = f"192.0.2.{(i % 250) + 1}"
            port, protocol = 22, "ssh"
        devices.append(
            {
                "id": f"bench-{i:04d}",
                "hostname": hostname,
                "port": port,
                "driver": "ios",
                "protocol": protocol,
                "type": "switch" if i % 3 else "router",
                "group": f"g{i % 10}",
                "tags": ["bench"],
                "description": "equipo sintético del banco 13A",
                "netmiko_device_type": None,
                "cli_transport": None,
                "cli_port": None,
                "noauth_telnet": False,
                "credentials": {
                    "username": "bench",
                    "password": "gAAAAA-bench-no-real",
                    "community": "public",
                },
            }
        )
    return devices


def _write_config(path: Path, devices: list[dict]) -> None:
    payload = {
        "devices": devices,
        "options": {
            "timeout": 60,
            "max_retries": 3,
            "pool_size": 10,
            "backup_dir": "backups",
        },
    }
    with open(path, "w") as f:
        yaml.safe_dump(payload, f, default_flow_style=False, allow_unicode=True)


async def _bench(args) -> dict:
    # Importar DESPUÉS de fijar PYTHONPATH y desactivar el rate limiter:
    # el banco mide cómputo, no el limitador (que por IP cortaría a 100/min).
    from httpx import ASGITransport, AsyncClient

    from app.core.limiter import limiter

    limiter.enabled = False

    from app.core.security import create_access_token
    from app.main import app
    from app.services import inventory_svc

    tmpdir = Path(tempfile.mkdtemp(prefix="np13a-bench-"))
    devices_file = tmpdir / "devices.yaml"

    async def _accept_and_close(reader, writer):
        try:
            writer.close()
            await writer.wait_closed()
        except OSError:
            pass

    server = await asyncio.start_server(_accept_and_close, "127.0.0.1", 0)
    open_port = server.sockets[0].getsockname()[1]

    total = args.devices
    n_open = int(total * args.open)
    n_refused = int(total * args.refused)
    n_blackhole = total - n_open - n_refused
    devices = _make_devices(
        total, "127.0.0.1", open_port, n_open, n_refused, n_blackhole
    )
    _write_config(devices_file, devices)
    size_kib = devices_file.stat().st_size / 1024.0

    inventory_svc.DEVICES_FILE = devices_file
    inventory_svc.invalidate_cache()

    token = create_access_token({"sub": "bench", "role": "admin"})
    headers = {"Authorization": f"Bearer {token}"}

    transport = ASGITransport(app=app)
    out: dict = {
        "config": {
            "devices": total,
            "yaml_kib": round(size_kib, 1),
            "mix": {
                "open": n_open,
                "refused": n_refused,
                "blackhole": n_blackhole,
            },
            "python": sys.version.split()[0],
        },
        "health": {},
        "put": {},
    }

    async with AsyncClient(transport=transport, base_url="http://bench",
                           timeout=120.0) as client:
        # ── Health ────────────────────────────────────────────
        from app.services import device_health

        reset = getattr(device_health, "reset_state", None)

        async def one_health() -> tuple[float, dict]:
            t0 = time.perf_counter()
            r = await client.get("/api/devices/health")
            elapsed = (time.perf_counter() - t0) * 1000.0
            r.raise_for_status()
            return elapsed, r.json()

        # Caliente: primera llamada (con el estado que traiga) + una segunda.
        first_ms, body = await one_health()
        cold_unknown = sum(1 for v in body.values() if v.get("online") is None)
        cold_online = sum(1 for v in body.values() if v.get("online") is True)
        cold_offline = sum(1 for v in body.values() if v.get("online") is False)
        out["health"]["first"] = round(first_ms, 1)
        out["health"]["first_states"] = {
            "online": cold_online,
            "offline": cold_offline,
            "unknown": cold_unknown,
        }
        second_ms, _ = await one_health()
        out["health"]["second"] = round(second_ms, 1)

        # Régimen estable: se deja trabajar al refresco de fondo y se mide el
        # latido normal del dashboard (todo dentro del TTL).
        if args.settle > 0:
            await asyncio.sleep(args.settle)
        warm = []
        for _ in range(args.warm_iterations):
            ms, body = await one_health()
            warm.append(ms)
        out["health"]["warm"] = _summary(warm)
        out["health"]["warm_states"] = {
            "online": sum(1 for v in body.values() if v.get("online") is True),
            "offline": sum(1 for v in body.values() if v.get("online") is False),
            "unknown": sum(1 for v in body.values() if v.get("online") is None),
        }

        # "cold": estado vacío → peor caso del contrato.
        cold = []
        for _ in range(args.health_iterations):
            reset()
            ms, _ = await one_health()
            cold.append(ms)
        out["health"]["cold"] = _summary(cold)

        # "stale": estado expirado pero con valores previos; debe devolver
        # desconocido (o refrescar acotado), nunca verde viejo.
        device_health.expire_all()
        stale = []
        for _ in range(args.health_iterations):
            ms, body = await one_health()
            stale.append(ms)
        out["health"]["stale"] = _summary(stale)
        out["health"]["stale_states"] = {
            "online": sum(1 for v in body.values() if v.get("online") is True),
            "offline": sum(1 for v in body.values() if v.get("online") is False),
            "unknown": sum(1 for v in body.values() if v.get("online") is None),
        }

        # ── PUT ───────────────────────────────────────────────
        put_ms: list[float] = []
        for i in range(args.put_iterations):
            target = devices[(i * 7) % len(devices)]["id"]
            t0 = time.perf_counter()
            r = await client.put(
                f"/api/devices/{target}",
                json={"description": f"actualizado {i}"},
                headers=headers,
            )
            elapsed = (time.perf_counter() - t0) * 1000.0
            if r.status_code != 200:
                raise RuntimeError(f"PUT {target} → {r.status_code}: {r.text[:200]}")
            put_ms.append(elapsed)
        out["put"] = _summary(put_ms)

        # Comprobación de integridad: siguen los N equipos y las credenciales.
        raw = yaml.safe_load(devices_file.read_text())
        out["integrity"] = {
            "devices_on_disk": len(raw.get("devices", [])),
            "credentials_present": all(
                "credentials" in d and "password" in d["credentials"]
                for d in raw.get("devices", [])
            ),
        }

        # GET /api/devices (regresión de lectura) como referencia.
        t0 = time.perf_counter()
        r = await client.get("/api/devices", headers=headers)
        r.raise_for_status()
        out["list_devices_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)

    server.close()
    await server.wait_closed()
    out["config"]["tmpdir"] = str(tmpdir)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--devices", type=int, default=500)
    ap.add_argument("--put-iterations", type=int, default=30)
    ap.add_argument("--health-iterations", type=int, default=5)
    ap.add_argument("--warm-iterations", type=int, default=20,
                    help="llamadas seguidas en régimen estable (todo fresco)")
    ap.add_argument("--settle", type=float, default=13.0,
                    help="segundos para que el refresco de fondo llene el estado")
    ap.add_argument("--open", type=float, default=0.2,
                    help="fracción de equipos con puerto abierto")
    ap.add_argument("--refused", type=float, default=0.4,
                    help="fracción con puerto cerrado (fallo rápido)")
    ap.add_argument("--profile", action="store_true",
                    help="perfila con cProfile una actualización y una salud")
    args = ap.parse_args()

    if args.profile:
        import cProfile
        import pstats

        from app.services import inventory_svc

        # Perfil del servicio puro: aísla deepcopy/sanitize/yaml de FastAPI.
        tmpdir = Path(tempfile.mkdtemp(prefix="np13a-profile-"))
        path = tmpdir / "devices.yaml"
        _write_config(path, _make_devices(args.devices, "127.0.0.1", 1,
                                          int(args.devices * .2),
                                          int(args.devices * .4),
                                          args.devices - int(args.devices * .2)
                                          - int(args.devices * .4)))
        inventory_svc.DEVICES_FILE = path
        inventory_svc.invalidate_cache()
        pr = cProfile.Profile()
        pr.enable()
        for i in range(10):
            inventory_svc.update_device(f"bench-{(i * 7) % args.devices:04d}",
                                        {"description": f"p{i}"})
        pr.disable()
        pstats.Stats(pr).sort_stats("cumulative").print_stats(25)
        return 0

    result = asyncio.run(_bench(args))

    import json

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
