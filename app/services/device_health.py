"""NetPulse — Salud del inventario sin falsos verdes.

Sustituye el sondeo "todo a la vez" de ``GET /api/devices/health`` por un
estado **por dispositivo** con tres garantías:

1. **TTL por equipo**: un resultado solo se reutiliza mientras su edad sea
   menor que ``NETPULSE_HEALTH_TTL`` (12 s por defecto). Pasado el TTL, si no
   se pudo volver a sondear, la respuesta es ``online: null`` (desconocido),
   nunca el último verde.
2. **Sondeo incremental acotado**: cada petición sondea como mucho
   ``NETPULSE_HEALTH_INLINE_BUDGET`` equipos caducados (24 por defecto) y
   espera como máximo ``NETPULSE_HEALTH_INLINE_DEADLINE`` (0.8 s). El resto
   se devuelve como desconocido; la respuesta no depende del tamaño del
   inventario.
3. **Refresco en segundo plano**: un único task por event loop sondea los
   caducados con concurrencia acotada (``NETPULSE_HEALTH_CONCURRENCY``, 96)
   para que el dashboard acabe viendo el estado real de los 500 equipos sin
   que ninguna petición pague ese coste.

Semántica de estados (igual que ``collectors/probe_svc``):

* ``online=True``  — la sonda respondió (TCP handshake o ICMP reply).
* ``online=False`` — dos sondas consecutivas sin respuesta/refused. Se exige
  doble fallo para no parpadear; un solo fallo se reporta como desconocido,
  **nunca** como verde.
* ``online=None``  — no hay medición dentro del TTL o no se pudo medir
  (DNS, permiso ICMP, host vacío). No implica caído.

Las credenciales y el inventario no se tocan: esto solo lee ``list_devices``.
La caché es por proceso; con varios workers cada uno mantiene la suya (ver
``docs/RENDIMIENTO.md``).
"""

from __future__ import annotations

import asyncio
import dataclasses
import errno
import logging
import os
import re
import shutil
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from app.services import inventory_svc

log = logging.getLogger(__name__)

# ── Configuración (se lee en cada uso para poder ajustar en tests) ──

TTL_DEFAULT = 12.0
PROBE_TIMEOUT_DEFAULT = 1.0
CONCURRENCY_DEFAULT = 96
INLINE_BUDGET_DEFAULT = 24
INLINE_DEADLINE_DEFAULT = 0.8
REFRESH_INTERVAL_DEFAULT = 1.0
UNKNOWN_RETRY_DEFAULT = 5.0
FAILS_TO_OFFLINE = 2


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def ttl() -> float:
    return max(0.0, _env_float("NETPULSE_HEALTH_TTL", TTL_DEFAULT))


def probe_timeout() -> float:
    return max(0.05, _env_float("NETPULSE_HEALTH_TIMEOUT", PROBE_TIMEOUT_DEFAULT))


def concurrency() -> int:
    return max(1, _env_int("NETPULSE_HEALTH_CONCURRENCY", CONCURRENCY_DEFAULT))


def inline_budget() -> int:
    return max(0, _env_int("NETPULSE_HEALTH_INLINE_BUDGET", INLINE_BUDGET_DEFAULT))


def inline_deadline() -> float:
    return max(0.0, _env_float("NETPULSE_HEALTH_INLINE_DEADLINE", INLINE_DEADLINE_DEFAULT))


def refresh_interval() -> float:
    return max(0.1, _env_float("NETPULSE_HEALTH_REFRESH_INTERVAL", REFRESH_INTERVAL_DEFAULT))


def unknown_retry() -> float:
    return max(0.0, _env_float("NETPULSE_HEALTH_UNKNOWN_RETRY", UNKNOWN_RETRY_DEFAULT))


def background_enabled() -> bool:
    return os.getenv("NETPULSE_HEALTH_BACKGROUND", "true").lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


# ── Estado ────────────────────────────────────────────────────

_NETWORK_UNREACHABLE = {
    errno.EHOSTUNREACH,
    errno.ENETUNREACH,
    errno.EHOSTDOWN,
    errno.ENETDOWN,
}

@dataclasses.dataclass
class Entry:
    """Último estado conocido de un equipo."""

    online: Optional[bool] = None
    latency_ms: Optional[float] = None
    rx: str = "—"
    tx: str = "—"
    checked_at: Optional[float] = None      # epoch de la última medición válida
    last_probe_mono: Optional[float] = None  # monotonic del último intento
    fails: int = 0


_state: dict[str, Entry] = {}
_state_lock = threading.Lock()
_generation = 0
# Equipos con una sonda en vuelo: evita que el refresco de fondo y una
# petición simultánea cuenten dos fallos del mismo intento y lo declaren
# offline por duplicado.
_inflight: set[str] = set()

_task: Optional[asyncio.Task] = None
_task_loop: Optional[asyncio.AbstractEventLoop] = None

# Tráfico rx/tx (docker stats). Se refresca SOLO desde el task de fondo:
# antes se ejecutaba en el hilo del event loop con timeout de 2 s y podía
# sumar 2 s a la primera respuesta de health.
_traffic: dict[str, tuple[str, str]] = {}
_traffic_updated = 0.0
_traffic_retry_after = 0.0
_TRAFFIC_TTL = 5.0
_TRAFFIC_RETRY = 30.0


@dataclasses.dataclass(frozen=True)
class ProbeResult:
    """Resultado de una sonda. ``online=None`` significa "no medible"."""

    online: Optional[bool]
    latency_ms: Optional[float] = None
    error: Optional[str] = None


# ── Sondas ────────────────────────────────────────────────────


async def _tcp_probe(host: str, port: int, timeout: float) -> ProbeResult:
    start = time.monotonic()
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
    except asyncio.TimeoutError:
        return ProbeResult(False, None, "timeout TCP")
    except ConnectionRefusedError:
        return ProbeResult(False, None, "conexión rechazada")
    except socket.gaierror as exc:
        return ProbeResult(None, None, f"DNS: {exc}")
    except OSError as exc:
        if exc.errno in _NETWORK_UNREACHABLE:
            return ProbeResult(False, None, f"red inalcanzable: {exc}")
        return ProbeResult(None, None, f"error local: {exc}")
    else:
        latency = round(max(0.1, (time.monotonic() - start) * 1000), 1)
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, asyncio.TimeoutError):
            pass
        return ProbeResult(True, latency)


async def _icmp_probe(host: str, timeout: float) -> ProbeResult:
    timeout_s = max(1, int(timeout + 0.999))
    if sys.platform == "darwin":
        ping_args = ["ping", "-c", "1", "-W", str(int(timeout * 1000)), host]
    else:
        ping_args = ["ping", "-c", "1", "-W", str(timeout_s), host]

    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *ping_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return ProbeResult(None, None, "ping no disponible")
    except PermissionError:
        return ProbeResult(None, None, "sin permiso ICMP")
    except OSError as exc:
        return ProbeResult(None, None, f"error local: {exc}")

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout + 0.5)
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except (ProcessLookupError, OSError):
            pass
        return ProbeResult(False, None, "timeout ICMP")

    if proc.returncode == 0:
        out = stdout.decode("utf-8", errors="ignore")
        match = re.search(r"time[=<]\s*([\d.]+)\s*ms", out)
        latency = (
            float(match.group(1))
            if match
            else round(max(0.1, (time.monotonic() - start) * 1000), 1)
        )
        return ProbeResult(True, max(0.1, latency))

    err = stderr.decode("utf-8", errors="ignore").lower()
    if "operation not permitted" in err or "permission denied" in err:
        return ProbeResult(None, None, "sin permiso ICMP")
    if (
        "name or service not known" in err
        or "unknown host" in err
        or "cannot resolve" in err
        or "could not find host" in err
    ):
        return ProbeResult(None, None, "DNS: nombre no resuelto")
    return ProbeResult(False, None, "sin respuesta ICMP")


async def probe_device(dev: dict, timeout: Optional[float] = None) -> ProbeResult:
    """Sondea un equipo según su protocolo, sin lanzar por fallos esperados."""
    timeout = probe_timeout() if timeout is None else timeout
    host = str(dev.get("hostname") or "").strip()
    if not host:
        return ProbeResult(None, None, "host vacío")
    try:
        port = int(dev.get("port", 22) or 22)
    except (TypeError, ValueError):
        return ProbeResult(None, None, "puerto inválido")
    protocol = str(dev.get("protocol") or "").lower()
    driver = str(dev.get("driver") or "").lower()
    if protocol == "snmp" or driver == "snmp" or port == 161:
        return await _icmp_probe(host, timeout)
    return await _tcp_probe(host, port, timeout)


# ── Tráfico (solo decorativo, nunca decide online/offline) ────


def _docker_stats_sync() -> dict[str, tuple[str, str]]:
    import subprocess

    traffic: dict[str, tuple[str, str]] = {}
    out = subprocess.check_output(
        ["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.NetIO}}"],
        stderr=subprocess.DEVNULL,
        timeout=0.8,
    ).decode()
    for line in out.strip().splitlines():
        parts = line.split("\t")
        if len(parts) == 2 and " / " in parts[1]:
            cname = parts[0].strip()
            rx, tx = parts[1].split(" / ")
            traffic[cname] = (rx.strip(), tx.strip())
            if cname.startswith("netpulse-"):
                short = cname[len("netpulse-"):]
                traffic[short] = (rx.strip(), tx.strip())
    return traffic


async def refresh_traffic(force: bool = False) -> None:
    """Actualiza la caché de rx/tx sin bloquear el event loop."""
    global _traffic, _traffic_updated, _traffic_retry_after
    now = time.monotonic()
    if not force:
        if now < _traffic_retry_after:
            return
        if _traffic and now - _traffic_updated < _TRAFFIC_TTL:
            return
    if shutil.which("docker") is None:
        _traffic_retry_after = now + _TRAFFIC_RETRY
        return
    try:
        traffic = await asyncio.to_thread(_docker_stats_sync)
    except Exception:
        _traffic_retry_after = now + _TRAFFIC_RETRY
        return
    with _state_lock:
        _traffic = traffic
        _traffic_updated = now
        _traffic_retry_after = 0.0


def _lookup_traffic(dev_id: str) -> tuple[str, str]:
    traffic = _traffic  # lectura atómica de la referencia
    prefix = dev_id.split("-")[0]
    for candidate in (
        dev_id,
        f"netpulse-{dev_id}",
        prefix,
        f"netpulse-{prefix}",
        dev_id.replace("_", "-"),
    ):
        if candidate in traffic:
            return traffic[candidate]
    return ("0.0 Mb/s", "0.0 Mb/s")


# ── Estado y payload ─────────────────────────────────────────


def _iso(epoch: Optional[float]) -> Optional[str]:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _fresh(entry: Entry, now_mono: float, ttl_s: float) -> bool:
    return (
        entry.online is not None
        and entry.last_probe_mono is not None
        and (now_mono - entry.last_probe_mono) <= ttl_s
    )


def _due(entry: Entry, now_mono: float, ttl_s: float, retry_s: float) -> bool:
    """¿Toca sondear? Se refresca al 80 % del TTL para no caducar en caliente.

    El estado se sigue sirviendo como válido hasta el TTL completo; adelantar
    el sondeo es lo que evita que el dashboard vea ``null`` en cada vuelta.
    """
    if entry.last_probe_mono is None:
        return True
    elapsed = now_mono - entry.last_probe_mono
    if entry.online is None:
        return elapsed >= retry_s
    return elapsed >= ttl_s * 0.8


def _apply(dev_id: str, result: ProbeResult, gen: int) -> None:
    now_mono = time.monotonic()
    now_epoch = time.time()
    with _state_lock:
        if gen != _generation:
            return
        entry = _state.get(dev_id)
        if entry is None:
            entry = Entry()
            _state[dev_id] = entry
        entry.last_probe_mono = now_mono
        if result.online is True:
            entry.online = True
            entry.fails = 0
            entry.latency_ms = result.latency_ms
            entry.rx, entry.tx = _lookup_traffic(dev_id)
            entry.checked_at = now_epoch
        elif result.online is False:
            entry.fails += 1
            entry.latency_ms = None
            entry.rx = entry.tx = "—"
            if entry.fails >= FAILS_TO_OFFLINE:
                entry.online = False
                entry.checked_at = now_epoch
            else:
                # Un fallo aislado no demuestra caída ni autoriza verde viejo.
                entry.online = None
        else:
            # No medible (DNS, permiso, host inválido): desconocido y no
            # cuenta como fallo de red; una racha de fallos se interrumpe.
            entry.online = None
            entry.fails = 0
            entry.latency_ms = None
            entry.rx = entry.tx = "—"


def _payload(entry: Optional[Entry], now_mono: float, ttl_s: float) -> dict:
    if entry is None or not _fresh(entry, now_mono, ttl_s):
        return {
            "online": None,
            "latency_ms": None,
            "rx": "—",
            "tx": "—",
            "checked_at": _iso(entry.checked_at) if entry else None,
            "stale": True,
        }
    return {
        "online": entry.online,
        "latency_ms": entry.latency_ms if entry.online else None,
        "rx": entry.rx if entry.online else "—",
        "tx": entry.tx if entry.online else "—",
        "checked_at": _iso(entry.checked_at),
        "stale": False,
    }


# ── Sondeo por lotes ────────────────────────────────────────


async def _probe_devices(devices: list[dict], deadline: Optional[float]) -> int:
    """Sondea ``devices`` con concurrencia acotada; aplica lo que termine.

    Returns:
        Número de equipos reclamados (0 si todos estaban ya en vuelo).
    """
    if not devices:
        return 0
    with _state_lock:
        claimed = [dev for dev in devices if dev["id"] not in _inflight]
        _inflight.update(dev["id"] for dev in claimed)
    if not claimed:
        return 0
    gen = _generation
    sem = asyncio.Semaphore(concurrency())

    async def run(dev: dict):
        async with sem:
            result = await probe_device(dev)
        _apply(dev["id"], result, gen)

    tasks = [asyncio.create_task(run(dev)) for dev in claimed]
    try:
        if deadline is None:
            await asyncio.gather(*tasks, return_exceptions=True)
        else:
            _done, pending = await asyncio.wait(tasks, timeout=deadline)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
    finally:
        # Si cancelan este lote (shutdown, reset, cliente que corta) hay que
        # finalizar las tasks pendientes y liberar TODOS los ids reclamados,
        # incluso los que nunca llegaron a ejecutar su cuerpo.
        unfinished = [task for task in tasks if not task.done()]
        for task in unfinished:
            task.cancel()
        if unfinished:
            await asyncio.gather(*unfinished, return_exceptions=True)
        with _state_lock:
            _inflight.difference_update(dev["id"] for dev in claimed)
    return len(claimed)


async def _refresh_loop() -> None:
    """Refresca en segundo plano los equipos caducados, ola a ola.

    No lanza 500 tareas de golpe: procesa tandas del tamaño de la
    concurrencia y **recalcula los caducados entre tandas**. Así los equipos
    que expiran mientras la tanda anterior sigue en vuelo no esperan una
    vuelta completa (y el dashboard no los ve desconocidos).
    """
    while True:
        try:
            devices = await asyncio.to_thread(inventory_svc.list_devices)
            while True:
                now = time.monotonic()
                ttl_s, retry_s = ttl(), unknown_retry()
                with _state_lock:
                    due = [
                        dev
                        for dev in devices
                        if _due(_state.get(dev["id"], Entry()), now, ttl_s, retry_s)
                    ]
                    due.sort(
                        key=lambda dev: (
                            _state.get(dev["id"], Entry()).last_probe_mono or 0.0
                        )
                    )
                batch = due[: concurrency()]
                if not batch:
                    break
                claimed = await _probe_devices(batch, deadline=None)
                if claimed == 0:
                    # Todo lo caducado está en vuelo (p. ej. sonda inline).
                    break
            await refresh_traffic()
        except asyncio.CancelledError:
            raise
        except Exception:  # nunca debe tumbar el task
            log.exception("refresco de salud falló")
        await asyncio.sleep(refresh_interval())


def _task_done(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        log.error("task de refresco de salud terminó: %r", exc)


def ensure_background() -> None:
    """Arranca el refresco si hace falta (uno por event loop)."""
    global _task, _task_loop
    if not background_enabled():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _task is not None and not _task.done() and _task_loop is loop:
        return
    _task_loop = loop
    _task = loop.create_task(_refresh_loop())
    _task.add_done_callback(_task_done)


# ── API pública ──────────────────────────────────────────────


async def collect(devices: list[dict], simple: bool = False) -> dict:
    """Devuelve el mapa de salud para ``devices`` respetando TTL y deadline."""
    ensure_background()
    now = time.monotonic()
    ttl_s, retry_s = ttl(), unknown_retry()

    stale = [
        dev
        for dev in devices
        if not _fresh(_state.get(dev["id"], Entry()), now, ttl_s)
        and _due(_state.get(dev["id"], Entry()), now, ttl_s, retry_s)
    ]
    # Los más viejos (o nunca vistos) primero: cobertura justa por petición.
    stale.sort(
        key=lambda dev: (
            (_state.get(dev["id"]) or Entry()).last_probe_mono or 0.0
        )
    )
    inline = stale[: inline_budget()]
    if inline:
        await _probe_devices(inline, deadline=inline_deadline())

    now = time.monotonic()
    with _state_lock:
        entries = {dev["id"]: _state.get(dev["id"]) for dev in devices}

    result: dict = {}
    for dev in devices:
        dev_id = dev["id"]
        if simple:
            entry = entries.get(dev_id)
            # ``simple`` solo admite bool: desconocido ⇒ False (conservador,
            # nunca un falso verde). El formato rico sí expresa null.
            result[dev_id] = (
                bool(entry.online)
                if entry is not None and _fresh(entry, now, ttl_s)
                else False
            )
        else:
            result[dev_id] = _payload(entries.get(dev_id), now, ttl_s)
    return result


def reset_state() -> None:
    """Vacía el estado y cancela el refresco (tests y banco)."""
    global _generation, _task, _task_loop
    global _traffic, _traffic_updated, _traffic_retry_after
    with _state_lock:
        _generation += 1
        _state.clear()
        _inflight.clear()
        _traffic = {}
        _traffic_updated = 0.0
        _traffic_retry_after = 0.0
    task = _task
    _task = None
    _task_loop = None
    if task is not None and not task.done():
        task.cancel()


def expire_all() -> None:
    """Marca todo el estado como caducado sin borrarlo (tests y banco)."""
    with _state_lock:
        for entry in _state.values():
            entry.last_probe_mono = None
