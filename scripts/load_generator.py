#!/usr/bin/env python3
"""Generador de carga SINTÉTICA contra ``POST /api/remote/ingest``.

SIMULACIÓN, NO INVENTARIO REAL. Los ``device_id`` llevan siempre el prefijo
``sim-`` y nunca coinciden con equipos del inventario. Este script alimenta el
mismo endpoint que usan los colectores remotos de producción (API key
``kind=collector``), no escribe jamás en la DB por la puerta de atrás.

Modelo de datos
---------------
Cada "tick" equivale a un ciclo de sonda de todos los dispositivos:

- ``reachable``  (bool)      — siempre.
- ``latency_ms`` (float)     — solo si el dispositivo responde.
- ``packet_loss_pct`` (float) — solo si responde y la sonda es ICMP.

Un dispositivo está caído con probabilidad ``--down-prob`` (por defecto 0.5 %);
en ese tick solo envía ``reachable=false`` con un error de timeout. La latencia
sigue una log-normal estable por dispositivo (determinista con ``--seed``).

Ritmo y tiempo
--------------
``--rate`` es la carga objetivo en muestras/segundo medida en el cliente. El
reloj sintético de los ``ts`` avanza ``--probe-interval`` segundos por tick,
pero el envío se comprime si ``--rate`` es mayor que el ritmo de producción
(``devices * 3 / probe_interval``). Es decir: un replay acelerado, no una
sonda real cada 60 s. La proyección a filas/día usa los ticks por día reales
(``86400 / probe_interval``) multiplicados por las filas realmente aceptadas
por tick, y se documenta como proyección, no como medición.

El generador respeta el rate-limit del servidor (``120/minute`` por IP en
``/api/remote/ingest``) con ``--max-requests-per-minute`` (defecto 90) para no
medir 429 en lugar de capacidad. Si el límite de peticiones obliga a bajar el
ritmo, se informa del ritmo efectivo.

Uso típico para el banco de capacidad (API local ya levantada)::

    scripts/load_generator.py --url http://127.0.0.1:8099 \\
        --keys-file /tmp/np-keys.json --devices 500 --sites 5 \\
        --rate 450 --duration 300 --seed 7 \\
        --report-json /tmp/np-cap-500.json

``--keys-file`` es un JSON ``{"site-1": {"collector_id": "sim-col-1",
"api_key": "np_..."}, ...}``. Para una sola sede también valen
``--site``, ``--collector`` y ``--api-key`` (o la variable
``NETPULSE_COLLECTOR_KEY``, que evita exponer la clave en ``ps``).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx

SCHEMA_VERSION = 1
PROBE_KIND = "icmp"  # permite packet_loss_pct (contrato de ingesta)
MAX_SAMPLES_PER_REQUEST = 500
DEFAULT_BATCH_SIZE = 400
DEVICE_PREFIX = "sim-"
STRATEGY = "synthetic-collector-ingest-v1"


# ── Modelo determinista ──────────────────────────────────────


@dataclass(frozen=True)
class SimDevice:
    """Perfil estable de un dispositivo simulado."""

    device_id: str
    site_id: str
    base_latency_ms: float
    jitter_sigma: float


@dataclass(frozen=True)
class SiteEndpoint:
    site_id: str
    collector_id: str
    api_key: str


def build_devices(devices: int, sites: int, *, seed: int = 1) -> dict[str, list[SimDevice]]:
    """Reparte ``devices`` dispositivos entre ``sites`` de forma determinista.

    Los identificadores son ``sim-<site>-<n>`` (el site ya empieza por
    ``sim-`` solo si el usuario lo pidió; aquí se usa ``site-1``). El reparto
    es round-robin, así las diferencias entre tandas no vienen del azar.
    """
    if devices < 1:
        raise ValueError("--devices debe ser >= 1")
    if sites < 1:
        raise ValueError("--sites debe ser >= 1")
    if sites > devices:
        raise ValueError("--sites no puede superar --devices")

    by_site: dict[str, list[SimDevice]] = {
        f"site-{index + 1}": [] for index in range(sites)
    }
    site_ids = list(by_site)
    for index in range(devices):
        site_id = site_ids[index % sites]
        # Semilla estable por dispositivo, independiente del orden de envío.
        rng = random.Random(seed * 1_000_003 + index)
        # Mezcla de equipos rápidos (LAN) y algunos enlaces WAN lentos.
        base = rng.choice((0.8, 1.5, 2.5, 4.0, 12.0, 28.0)) * rng.uniform(0.85, 1.15)
        device = SimDevice(
            device_id=f"{DEVICE_PREFIX}{site_id}-{index + 1:04d}",
            site_id=site_id,
            base_latency_ms=round(base, 3),
            jitter_sigma=rng.uniform(0.18, 0.45),
        )
        by_site[site_id].append(device)
    return by_site


def _device_rng(seed: int, device_index: int, tick: int) -> random.Random:
    """RNG reproducible por (semilla, dispositivo, tick)."""
    return random.Random((seed * 1_000_003 + device_index) * 1_000_033 + tick)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def build_tick(
    devices: Iterable[SimDevice],
    *,
    tick: int,
    ts: datetime,
    seed: int,
    down_prob: float = 0.005,
    first_index: int = 0,
) -> list[dict[str, Any]]:
    """Muestras de un tick para una lista de dispositivos.

    Función pura y determinista: mismos argumentos → mismas muestras (incluidos
    valores, orden y timestamps). No depende del reloj ni del orden de las
    peticiones.
    """
    if not (0.0 <= down_prob <= 1.0):
        raise ValueError("down_prob debe estar entre 0 y 1")
    samples: list[dict[str, Any]] = []
    timestamp = _iso(ts)
    for offset, device in enumerate(devices):
        rng = _device_rng(seed, first_index + offset, tick)
        down = rng.random() < down_prob
        if down:
            samples.append({
                "device_id": device.device_id,
                "ts": timestamp,
                "metric": "reachable",
                "value_num": None,
                "value_bool": False,
                "ok": True,
                "probe_kind": PROBE_KIND,
                "error": "timeout ICMP (simulado)",
            })
            continue
        # Latencia log-normal alrededor del perfil del equipo.
        mu = math.log(max(device.base_latency_ms, 0.1))
        latency = math.exp(mu + device.jitter_sigma * rng.gauss(0.0, 1.0))
        # Pérdida casi siempre 0; de vez en cuando un enlace degradado.
        loss = 0.0
        if rng.random() < 0.004:
            loss = round(min(100.0, max(0.5, rng.uniform(0.5, 4.0))), 3)
        samples.append({
            "device_id": device.device_id,
            "ts": timestamp,
            "metric": "reachable",
            "value_num": None,
            "value_bool": True,
            "ok": True,
            "probe_kind": PROBE_KIND,
            "error": None,
        })
        samples.append({
            "device_id": device.device_id,
            "ts": timestamp,
            "metric": "latency_ms",
            "value_num": round(min(latency, 5000.0), 3),
            "value_bool": None,
            "ok": True,
            "probe_kind": PROBE_KIND,
            "error": None,
        })
        samples.append({
            "device_id": device.device_id,
            "ts": timestamp,
            "metric": "packet_loss_pct",
            "value_num": loss,
            "value_bool": None,
            "ok": True,
            "probe_kind": PROBE_KIND,
            "error": None,
        })
    return samples


def chunk_samples(
    samples: list[dict[str, Any]], batch_size: int = DEFAULT_BATCH_SIZE
) -> list[list[dict[str, Any]]]:
    """Trocea un tick en lotes que respetan el máximo del contrato (500)."""
    if batch_size < 1 or batch_size > MAX_SAMPLES_PER_REQUEST:
        raise ValueError(f"batch_size debe estar entre 1 y {MAX_SAMPLES_PER_REQUEST}")
    return [samples[offset:offset + batch_size] for offset in range(0, len(samples), batch_size)]


# ── Ritmo ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Pace:
    """Plan de ritmo calculado antes de enviar nada."""

    samples_per_tick: int
    batches_per_tick: int
    wall_tick_s: float
    effective_rate: float
    requested_rate: float
    limited_by: str  # "rate" | "request_budget"


def plan_pace(
    *,
    devices: int,
    sites: int,
    rate: float,
    probe_interval_s: float,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_requests_per_minute: float = 90.0,
) -> Pace:
    """Calcula el intervalo de pared y el ritmo efectivo.

    Se pide ``rate`` muestras/s, pero nunca se envían más de
    ``max_requests_per_minute`` peticiones/min (el endpoint limita a 120/min
    por IP). Si el presupuesto de peticiones manda, ``limited_by`` lo dice.
    """
    if rate <= 0:
        rate = devices * 3.0 / probe_interval_s
    if max_requests_per_minute <= 0:
        raise ValueError("max_requests_per_minute debe ser > 0")
    samples_per_tick = devices * 3
    per_site = math.ceil(devices / sites) * 3
    batches_per_site = max(1, math.ceil(per_site / batch_size))
    batches_per_tick = batches_per_site * sites
    by_rate = samples_per_tick / rate
    by_requests = batches_per_tick / (max_requests_per_minute / 60.0)
    wall_tick = max(by_rate, by_requests)
    limited_by = "request_budget" if by_requests > by_rate else "rate"
    return Pace(
        samples_per_tick=samples_per_tick,
        batches_per_tick=batches_per_tick,
        wall_tick_s=round(wall_tick, 4),
        effective_rate=round(samples_per_tick / wall_tick, 2),
        requested_rate=round(rate, 2),
        limited_by=limited_by,
    )


def percentile(values: list[float], fraction: float) -> float | None:
    """Percentil por interpolación lineal (sin dependencias)."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def latency_summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "p50": None, "p95": None, "p99": None,
                "min": None, "max": None, "mean": None}
    return {
        "count": len(values),
        "p50": round(statistics.median(values), 2),
        "p95": round(percentile(values, 0.95) or 0.0, 2),
        "p99": round(percentile(values, 0.99) or 0.0, 2),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
        "mean": round(statistics.fmean(values), 2),
    }


# ── Configuración de endpoints ──────────────────────────────


def load_endpoints(args: argparse.Namespace, site_ids: list[str]) -> dict[str, SiteEndpoint]:
    """Carga una clave de colector por site desde JSON o desde argv/env."""
    endpoints: dict[str, SiteEndpoint] = {}
    if args.keys_file:
        raw = json.loads(Path(args.keys_file).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise SystemExit("--keys-file debe contener un objeto {site: {...}}")
        for site_id, item in raw.items():
            if not isinstance(item, dict) or not item.get("api_key"):
                raise SystemExit(f"--keys-file: falta api_key para {site_id}")
            endpoints[site_id] = SiteEndpoint(
                site_id=site_id,
                collector_id=str(item.get("collector_id") or f"sim-col-{site_id}"),
                api_key=str(item["api_key"]),
            )
    else:
        key = args.api_key or os.getenv("NETPULSE_COLLECTOR_KEY", "")
        if not key:
            raise SystemExit(
                "Falta la API key de colector: use --keys-file, --api-key "
                "o NETPULSE_COLLECTOR_KEY"
            )
        if len(site_ids) != 1:
            raise SystemExit(
                "Con una sola clave solo se puede simular un site; use --keys-file "
                "para varios sites (cada clave está ligada a su site)"
            )
        endpoints[site_ids[0]] = SiteEndpoint(
            site_id=site_ids[0],
            collector_id=args.collector or f"sim-col-{site_ids[0]}",
            api_key=key,
        )

    missing = [site_id for site_id in site_ids if site_id not in endpoints]
    extra = [site_id for site_id in endpoints if site_id not in site_ids]
    if missing:
        raise SystemExit(f"Faltan claves para los sites: {', '.join(missing)}")
    if extra:
        print(f"aviso: se ignoran claves de sites no simulados: {', '.join(extra)}")
    return {site_id: endpoints[site_id] for site_id in site_ids}


def build_payload(samples: list[dict[str, Any]], *, sent_at: datetime,
                  probe_interval_s: float, buffer_epoch: str,
                  batch_id: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "collector_version": "sim-load-generator/1.0",
        "probe_interval_s": max(1, int(round(probe_interval_s))),
        "sync_batch_id": batch_id,
        "sent_at": _iso(sent_at),
        "buffer": {
            "buffer_epoch": buffer_epoch,
            "pending": 0,
            "oldest_pending_ts": None,
            "dropped_total": 0,
            "gap_since": None,
            "paused_reason": None,
        },
        "sync": {"consecutive_failures": 0, "auth_state": "ok"},
        "samples": samples,
    }


# ── CLI ─────────────────────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generador de carga SINTÉTICA para POST /api/remote/ingest. "
            "Los device_id llevan prefijo 'sim-' y jamás representan inventario real."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--url", default="http://127.0.0.1:8082",
                        help="base URL de la API")
    parser.add_argument("--devices", type=int, default=100,
                        help="dispositivos simulados (100-500 en el banco)")
    parser.add_argument("--sites", type=int, default=1,
                        help="sites simulados; requieren una clave por site")
    parser.add_argument("--rate", type=float, default=0.0,
                        help="muestras/segundo objetivo en pared (0 = ritmo de producción)")
    parser.add_argument("--probe-interval", type=float, default=60.0,
                        help="segundos entre ticks del reloj sintético")
    parser.add_argument("--duration", type=float, default=60.0,
                        help="duración de pared en segundos")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                        help=f"muestras por lote (máx {MAX_SAMPLES_PER_REQUEST})")
    parser.add_argument("--max-requests-per-minute", type=float, default=90.0,
                        help="presupuesto de peticiones/min (el endpoint limita a 120)")
    parser.add_argument("--down-prob", type=float, default=0.005,
                        help="probabilidad de equipo no alcanzable por tick")
    parser.add_argument("--seed", type=int, default=1,
                        help="semilla; mismos datos con la misma semilla")
    parser.add_argument("--start-ts", default=None,
                        help="timestamp ISO inicial del reloj sintético (por defecto ahora)")
    parser.add_argument("--keys-file", default=None,
                        help='JSON {"site": {"collector_id": ..., "api_key": ...}}')
    parser.add_argument("--site", default=None,
                        help="site de la clave única (por defecto site-1)")
    parser.add_argument("--collector", default=None,
                        help="collector_id de la clave única")
    parser.add_argument("--api-key", default=None,
                        help="API key de colector (mejor NETPULSE_COLLECTOR_KEY)")
    parser.add_argument("--timeout", type=float, default=15.0,
                        help="timeout HTTP por petición en segundos")
    parser.add_argument("--report-json", default=None,
                        help="escribe el resumen medido en este archivo JSON")
    parser.add_argument("--dry-run", action="store_true",
                        help="no envía nada; imprime el plan y un tick de muestra")
    parser.add_argument("--quiet", action="store_true", help="sin progreso por tick")
    return parser.parse_args(argv)


def _parse_start(value: str | None) -> datetime:
    if value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def _print_plan(devices: dict[str, list[SimDevice]], pace: Pace,
                args: argparse.Namespace, start: datetime) -> None:
    total = sum(len(items) for items in devices.values())
    print("SIMULACIÓN (no inventario real): device_id con prefijo 'sim-'")
    print(f"  devices={total} sites={len(devices)} seed={args.seed}")
    print(f"  ticks sintéticos cada {args.probe_interval:g}s; "
          f"{pace.samples_per_tick} muestras/tick en {pace.batches_per_tick} lotes")
    print(f"  rate pedido={pace.requested_rate:g} muestras/s; "
          f"efectivo={pace.effective_rate:g} muestras/s "
          f"(limitado por {pace.limited_by})")
    print(f"  tick de pared={pace.wall_tick_s:g}s durante {args.duration:g}s "
          f"→ {int(args.duration / pace.wall_tick_s)} ticks")
    print(f"  reloj sintético desde {_iso(start)}")
    for site_id, items in devices.items():
        print(f"  - {site_id}: {len(items)} devices "
              f"({items[0].device_id} … {items[-1].device_id})")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.batch_size < 1 or args.batch_size > MAX_SAMPLES_PER_REQUEST:
        raise SystemExit(f"--batch-size debe estar entre 1 y {MAX_SAMPLES_PER_REQUEST}")
    if not (0.0 <= args.down_prob <= 1.0):
        raise SystemExit("--down-prob debe estar entre 0 y 1")
    if args.sites > args.devices:
        raise SystemExit("--sites no puede superar --devices")

    devices = build_devices(args.devices, args.sites, seed=args.seed)
    site_ids = list(devices)
    pace = plan_pace(
        devices=args.devices,
        sites=args.sites,
        rate=args.rate,
        probe_interval_s=args.probe_interval,
        batch_size=args.batch_size,
        max_requests_per_minute=args.max_requests_per_minute,
    )
    start = _parse_start(args.start_ts)
    _print_plan(devices, pace, args, start)

    if args.dry_run:
        first_site = site_ids[0]
        tick = build_tick(devices[first_site], tick=0, ts=start,
                          seed=args.seed, down_prob=args.down_prob)
        print(json.dumps({
            "simulation": True,
            "pace": pace.__dict__,
            "samples_tick_0_first_site": tick[:6],
            "samples_in_tick_first_site": len(tick),
        }, indent=2, ensure_ascii=False))
        return 0

    endpoints = load_endpoints(args, site_ids)
    run = _run(args, devices, endpoints, pace, start)
    if args.report_json:
        Path(args.report_json).write_text(
            json.dumps(run, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"reporte: {args.report_json}")
    _print_summary(run)
    return 0 if run["http_errors"] == 0 and run["rejected"] == 0 else 2


def _run(
    args: argparse.Namespace,
    devices: dict[str, list[SimDevice]],
    endpoints: dict[str, SiteEndpoint],
    pace: Pace,
    start: datetime,
) -> dict[str, Any]:
    total_devices = sum(len(items) for items in devices.values())
    buffer_epoch = f"sim-{args.seed}-{uuid.uuid4().hex[:8]}"
    latencies: list[float] = []
    status_counts: dict[str, int] = {}
    accepted = duplicates = rejected = http_errors = 0
    bytes_sent = 0
    started_wall = time.monotonic()
    ticks = 0
    fatal: str | None = None
    rows_per_tick: list[int] = []

    connect_started = time.monotonic()
    headers = {"Content-Type": "application/json"}
    with httpx.Client(
        base_url=args.url.rstrip("/"),
        timeout=args.timeout,
        headers=headers,
        limits=httpx.Limits(max_keepalive_connections=8, max_connections=8),
    ) as client:
        # Verificación de contrato: quien no puede ingerir, no debe medirse.
        for endpoint in endpoints.values():
            try:
                response = client.get(
                    "/api/remote/whoami",
                    headers={"Authorization": f"Bearer {endpoint.api_key}"},
                )
            except httpx.HTTPError as exc:
                raise SystemExit(f"no se pudo contactar {args.url}: {exc}") from exc
            if response.status_code != 200:
                raise SystemExit(
                    f"whoami {endpoint.site_id}: HTTP {response.status_code} "
                    f"{response.text[:200]}"
                )
        if not args.quiet:
            print(f"conexión y whoami OK ({time.monotonic() - connect_started:.2f}s)")

        next_tick = time.monotonic()
        while True:
            elapsed = time.monotonic() - started_wall
            if elapsed >= args.duration:
                break
            synthetic_ts = start + timedelta(seconds=args.probe_interval * ticks)
            accepted_tick = 0
            for site_id, site_devices in devices.items():
                samples = build_tick(
                    site_devices, tick=ticks, ts=synthetic_ts,
                    seed=args.seed, down_prob=args.down_prob,
                )
                endpoint = endpoints[site_id]
                for batch in chunk_samples(samples, args.batch_size):
                    body = build_payload(
                        batch,
                        sent_at=datetime.now(timezone.utc),
                        probe_interval_s=args.probe_interval,
                        buffer_epoch=buffer_epoch,
                        batch_id=str(uuid.uuid4()),
                    )
                    payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
                    bytes_sent += len(payload)
                    request_started = time.perf_counter()
                    try:
                        response = client.post(
                            "/api/remote/ingest",
                            content=payload,
                            headers={
                                "Authorization": f"Bearer {endpoint.api_key}",
                                "Content-Type": "application/json",
                            },
                        )
                    except httpx.HTTPError as exc:
                        http_errors += 1
                        latencies.append((time.perf_counter() - request_started) * 1000)
                        status_counts["network_error"] = status_counts.get("network_error", 0) + 1
                        if not args.quiet:
                            print(f"  tick={ticks} {site_id}: error de red: {exc}")
                        continue
                    latencies.append((time.perf_counter() - request_started) * 1000)
                    status_counts[str(response.status_code)] = (
                        status_counts.get(str(response.status_code), 0) + 1
                    )
                    if response.status_code == 401 or response.status_code == 403:
                        fatal = f"HTTP {response.status_code}: {response.text[:300]}"
                        break
                    if response.status_code == 429:
                        http_errors += 1
                        time.sleep(2.0)
                        continue
                    if response.status_code != 200:
                        http_errors += 1
                        continue
                    try:
                        data = response.json()
                    except ValueError:
                        http_errors += 1
                        continue
                    tick_accepted = int(data.get("accepted", 0) or 0)
                    accepted += tick_accepted
                    accepted_tick += tick_accepted
                    duplicates += int(data.get("duplicates", 0) or 0)
                    rejected += int(data.get("rejected", 0) or 0)
                if fatal:
                    break
            rows_per_tick.append(accepted_tick)
            if fatal:
                break
            ticks += 1
            if not args.quiet and (ticks == 1 or ticks % 10 == 0):
                print(
                    f"  tick={ticks} t={time.monotonic() - started_wall:.1f}s "
                    f"aceptadas={accepted} p50={latency_summary(latencies)['p50']}ms"
                )
            next_tick += pace.wall_tick_s
            sleep_for = next_tick - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)
            else:
                # El servidor no da abasto: se informa y no se acumula deuda.
                next_tick = time.monotonic()

    finished_wall = time.monotonic()
    wall_seconds = finished_wall - started_wall
    production_ticks_per_day = 86400.0 / args.probe_interval
    measured_ticks = sum(1 for value in rows_per_tick)
    rows_per_tick_mean = (sum(rows_per_tick) / measured_ticks) if measured_ticks else 0.0
    projected_rows_day = rows_per_tick_mean * production_ticks_per_day
    # Tamaño por fila medido en la DB del banco; aquí solo se proyecta el peso
    # lógico de las filas aceptadas (sin índices: se mide aparte en docs).
    return {
        "simulation": True,
        "strategy": STRATEGY,
        "url": args.url,
        "seed": args.seed,
        "devices": total_devices,
        "sites": len(devices),
        "device_prefix": DEVICE_PREFIX,
        "down_prob": args.down_prob,
        "probe_interval_s": args.probe_interval,
        "batch_size": args.batch_size,
        "requested_rate": pace.requested_rate,
        "effective_rate": pace.effective_rate,
        "limited_by": pace.limited_by,
        "wall_tick_s": pace.wall_tick_s,
        "duration_s": round(wall_seconds, 2),
        "ticks": measured_ticks,
        "started_at": _iso(start),
        "samples_sent": accepted + duplicates + rejected,
        "accepted": accepted,
        "duplicates": duplicates,
        "rejected": rejected,
        "requests": len(latencies),
        "http_errors": http_errors,
        "status_counts": status_counts,
        "bytes_sent": bytes_sent,
        "latency_ms": latency_summary(latencies),
        "rows_per_tick_mean": round(rows_per_tick_mean, 2),
        "production_ticks_per_day": round(production_ticks_per_day, 2),
        "projected_rows_per_day": round(projected_rows_day, 0),
        "accepted_per_s": round(accepted / wall_seconds, 2) if wall_seconds else 0.0,
        "fatal": fatal,
    }


def _print_summary(run: dict[str, Any]) -> None:
    latency = run["latency_ms"]
    print("\nRESUMEN (SIMULACIÓN)")
    print(f"  devices={run['devices']} sites={run['sites']} seed={run['seed']}")
    print(f"  duración={run['duration_s']}s ticks={run['ticks']} "
          f"rate efectivo={run['effective_rate']} muestras/s")
    print(f"  enviadas={run['samples_sent']} aceptadas={run['accepted']} "
          f"duplicadas={run['duplicates']} rechazadas={run['rejected']} "
          f"peticiones={run['requests']} errores={run['http_errors']}")
    print(f"  latencia HTTP ms: p50={latency['p50']} p95={latency['p95']} "
          f"p99={latency['p99']} max={latency['max']}")
    print(f"  filas/tick medio={run['rows_per_tick_mean']} "
          f"→ proyección {run['projected_rows_per_day']:.0f} filas/día a "
          f"{run['probe_interval_s']:g}s")
    if run.get("fatal"):
        print(f"  FATAL: {run['fatal']}")


if __name__ == "__main__":
    raise SystemExit(main())
