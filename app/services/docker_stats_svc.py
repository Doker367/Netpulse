"""NetPulse — Docker stats con timeout, caché corta y estado honesto.

``docker stats`` es un comando caro: arranca un cliente, habla con el daemon
y, si Docker no está sano, puede tardar segundos. Ejecutarlo directamente
dentro de un handler ``async`` congela el event loop y con él el dashboard y
el WebSocket de terminal.

Este servicio centraliza la llamada con:

- **Timeout** acotado (``NETPULSE_DOCKER_STATS_TIMEOUT``, 2 s por defecto).
- **Caché corta** (``NETPULSE_DOCKER_STATS_TTL``, 8 s por defecto) para no
  lanzar el proceso en cada request. Los fallos se cachean menos tiempo
  (``NETPULSE_DOCKER_STATS_ERROR_TTL``, 3 s) para recuperarse rápido cuando
  Docker vuelve.
- **Estado honesto**: si Docker no está instalado, el daemon no responde o
  se agota el timeout, se devuelve ``available: false`` con un ``reason``;
  nunca se propaga un error 500.
- **Sin bloquear el loop**: ``snapshot_async()`` usa ``asyncio.to_thread``
  (ejecutor acotado de asyncio) y ``asyncio.wait_for``.

El contrato de ``snapshot()`` es::

    {
      "available": bool,
      "reason": None | "docker_no_disponible" | "timeout" | "error",
      "detail": str | None,
      "containers": {name: {"rx": "1.2MB", "tx": "3.4MB"}},
      "count": int,
      "cached": bool,
      "age_seconds": float,
      "ttl_seconds": float,
      "checked_at": ISO-8601,
    }
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 2.0
DEFAULT_TTL = 8.0
DEFAULT_ERROR_TTL = 3.0
_COMMAND = ["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.NetIO}}"]

_lock = threading.Lock()
# Último resultado (también los fallos) y su antigüedad.
_snapshot: Optional[dict] = None
_snapshot_at: float = 0.0


def _timeout() -> float:
    try:
        return max(0.01, float(os.getenv("NETPULSE_DOCKER_STATS_TIMEOUT", DEFAULT_TIMEOUT)))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT


def _ttl() -> float:
    try:
        return max(0.0, float(os.getenv("NETPULSE_DOCKER_STATS_TTL", DEFAULT_TTL)))
    except (TypeError, ValueError):
        return DEFAULT_TTL


def _error_ttl() -> float:
    try:
        return max(0.0, float(os.getenv("NETPULSE_DOCKER_STATS_ERROR_TTL", DEFAULT_ERROR_TTL)))
    except (TypeError, ValueError):
        return DEFAULT_ERROR_TTL


def reset_cache() -> None:
    """Limpia la caché (tests y cambios de configuración)."""
    global _snapshot, _snapshot_at
    with _lock:
        _snapshot = None
        _snapshot_at = 0.0


def _parse(output: str) -> dict[str, dict[str, str]]:
    """Convierte la salida de ``docker stats`` en ``{nombre: {rx, tx}}``.

    Se aceptan tanto nombres reales (``netpulse-lab1``) como el alias corto
    sin prefijo (``lab1``), igual que hacía el consumidor histórico.
    """
    containers: dict[str, dict[str, str]] = {}
    for line in (output or "").strip().splitlines():
        parts = line.split("\t")
        if len(parts) != 2 or " / " not in parts[1]:
            continue
        name = parts[0].strip()
        rx, tx = (p.strip() for p in parts[1].split(" / ", 1))
        if not name:
            continue
        containers[name] = {"rx": rx, "tx": tx}
        if name.startswith("netpulse-"):
            containers.setdefault(name[len("netpulse-"):], {"rx": rx, "tx": tx})
    return containers


def _unavailable(reason: str, detail: Optional[str], *, cached: bool, age: float) -> dict:
    return {
        "available": False,
        "reason": reason,
        "detail": detail,
        "containers": {},
        "count": 0,
        "cached": cached,
        "age_seconds": round(age, 3),
        "ttl_seconds": _ttl(),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def _run() -> dict:
    """Ejecuta ``docker stats`` una vez (síncrono; llamar desde un hilo)."""
    if shutil.which("docker") is None:
        return _unavailable(
            "docker_no_disponible",
            "comando 'docker' no encontrado en PATH",
            cached=False,
            age=0.0,
        )

    started = time.monotonic()
    try:
        proc = subprocess.run(
            _COMMAND,
            capture_output=True,
            text=True,
            timeout=_timeout(),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _unavailable(
            "timeout",
            f"docker stats superó {_timeout():.2f}s",
            cached=False,
            age=time.monotonic() - started,
        )
    except (OSError, ValueError) as exc:
        return _unavailable("error", f"{type(exc).__name__}: {exc}", cached=False, age=0.0)

    stderr = (proc.stderr or "").strip()
    if proc.returncode != 0:
        return _unavailable(
            "docker_no_disponible",
            stderr.splitlines()[-1] if stderr else f"docker stats salió con {proc.returncode}",
            cached=False,
            age=time.monotonic() - started,
        )

    containers = _parse(proc.stdout or "")
    return {
        "available": True,
        "reason": None,
        "detail": None,
        "containers": containers,
        "count": len({k for k in containers if k.startswith("netpulse-")})
        or len(containers),
        "cached": False,
        "age_seconds": 0.0,
        "ttl_seconds": _ttl(),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def snapshot(refresh: bool = False) -> dict:
    """Snapshot de tráfico por contenedor con caché y sin lanzar el comando
    si hay un resultado fresco. Seguro para llamar desde varios hilos.

    Args:
        refresh: Ignora la caché y ejecuta ``docker stats`` ahora.
    """
    global _snapshot, _snapshot_at

    now = time.monotonic()
    cached = _snapshot
    if not refresh and cached is not None:
        ttl = _ttl() if cached.get("available") else _error_ttl()
        if now - _snapshot_at <= ttl:
            out = dict(cached)
            out["cached"] = True
            out["age_seconds"] = round(now - _snapshot_at, 3)
            return out

    # Single-flight: si varios hilos piden a la vez, solo uno ejecuta Docker.
    with _lock:
        now = time.monotonic()
        cached = _snapshot
        if not refresh and cached is not None:
            ttl = _ttl() if cached.get("available") else _error_ttl()
            if now - _snapshot_at <= ttl:
                out = dict(cached)
                out["cached"] = True
                out["age_seconds"] = round(now - _snapshot_at, 3)
                return out
        result = _run()
        _snapshot = result
        _snapshot_at = time.monotonic()
        return dict(result)


def last_snapshot() -> Optional[dict]:
    """Último snapshot conocido (aunque esté vencido) o ``None``."""
    if _snapshot is None:
        return None
    out = dict(_snapshot)
    out["cached"] = True
    out["age_seconds"] = round(time.monotonic() - _snapshot_at, 3)
    return out


async def snapshot_async(refresh: bool = False, timeout: Optional[float] = None) -> dict:
    """Igual que :func:`snapshot`, pero fuera del event loop.

    ``timeout`` acota la espera total. Si vence, se devuelve el último
    snapshot conocido (marcado como tal) o un estado honesto de timeout.
    """
    wait = timeout if timeout is not None else _timeout() + 1.0
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(snapshot, refresh), timeout=wait
        )
    except asyncio.TimeoutError:
        logger.warning("docker stats no respondió en %.2fs", wait)
        stale = last_snapshot()
        if stale is not None:
            return stale
        return _unavailable(
            "timeout", f"docker stats no respondió en {wait:.2f}s", cached=False, age=wait
        )


def traffic(refresh: bool = False) -> dict[str, tuple[str, str]]:
    """Mapa ``{nombre: (rx, tx)}`` compatible con el consumidor histórico."""
    snap = snapshot(refresh=refresh)
    return {k: (v["rx"], v["tx"]) for k, v in snap["containers"].items()}


async def traffic_async(refresh: bool = False) -> dict[str, tuple[str, str]]:
    snap = await snapshot_async(refresh=refresh)
    return {k: (v["rx"], v["tx"]) for k, v in snap["containers"].items()}
