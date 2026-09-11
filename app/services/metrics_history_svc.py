"""NetPulse — Historial de Métricas (nativo, sin Prometheus).

Guarda series temporales del host y de los dispositivos recolectadas por
el poller, en memoria (deque acotado) y persistidas en disco para
sobrevivir reinicios.

Formato de cada muestra::

    {
      "ts": 1789150000.0,
      "host": {"cpu": 12.3, "memory_percent": 54.0, "disk_percent": 3.5, "load1": 1.2},
      "devices": {
        "sw-core-01": {"cpu": 4.0, "memory_percent": 20.0, "disk_percent": 10.0,
                        "latency_ms": 0.9, "packet_loss": 0.0, "online": true}
      }
    }

El tamaño máximo se controla con ``NETPULSE_METRICS_HISTORY`` (nº de
muestras; por defecto 1440 = 24 h con intervalo de 60 s).
"""

import json
import logging
import os
import threading
import time
from collections import deque
from typing import Optional

from app.core.settings import CONFIG_DIR

logger = logging.getLogger(__name__)

HISTORY_FILE = CONFIG_DIR / "metrics_history.json"
MAX_SAMPLES = int(os.getenv("NETPULSE_METRICS_HISTORY", "1440"))

_lock = threading.Lock()
_samples: deque = deque(maxlen=MAX_SAMPLES)
_loaded = False
_writes_since_save = 0
_SAVE_EVERY = 5


def _load() -> None:
    """Carga el historial persistido (una sola vez)."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        if HISTORY_FILE.exists():
            data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                for sample in data[-MAX_SAMPLES:]:
                    if isinstance(sample, dict) and "ts" in sample:
                        _samples.append(sample)
            logger.info("[history] %d muestras cargadas", len(_samples))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[history] no se pudo cargar %s: %s", HISTORY_FILE, exc)


def _save() -> None:
    """Persiste el historial a disco (atómico)."""
    with _lock:
        data = list(_samples)
    try:
        tmp = HISTORY_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(HISTORY_FILE)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[history] no se pudo guardar: %s", exc)


def append(sample: dict) -> None:
    """Agrega una muestra al historial y persiste cada N inserciones."""
    global _writes_since_save
    _load()
    with _lock:
        _samples.append(sample)
        _writes_since_save += 1
        should_save = _writes_since_save >= _SAVE_EVERY
        if should_save:
            _writes_since_save = 0
    if should_save:
        _save()


def flush() -> None:
    """Fuerza el guardado a disco (ej. en shutdown)."""
    _save()


def _in_window(ts: float, hours: float) -> bool:
    return ts >= time.time() - hours * 3600


def host_series(hours: float = 6) -> list[dict]:
    """Serie temporal del host en la ventana indicada."""
    _load()
    with _lock:
        data = list(_samples)
    out = []
    for s in data:
        if not _in_window(s.get("ts", 0), hours):
            continue
        h = s.get("host") or {}
        out.append({
            "ts": s.get("ts"),
            "cpu": h.get("cpu", 0) or 0,
            "memory_percent": h.get("memory_percent", 0) or 0,
            "disk_percent": h.get("disk_percent", 0) or 0,
            "load1": h.get("load1", 0) or 0,
        })
    return out


def device_series(device_id: str, hours: float = 6) -> list[dict]:
    """Serie temporal de un dispositivo en la ventana indicada."""
    _load()
    with _lock:
        data = list(_samples)
    out = []
    for s in data:
        if not _in_window(s.get("ts", 0), hours):
            continue
        d = (s.get("devices") or {}).get(device_id)
        if not d:
            continue
        out.append({"ts": s.get("ts"), **d})
    return out


def overview(hours: float = 6, device_id: Optional[str] = None) -> dict:
    """Resumen del historial: host + series por dispositivo.

    Args:
        hours: ventana temporal en horas.
        device_id: si se indica, solo devuelve la serie de ese dispositivo.
    """
    _load()
    with _lock:
        data = list(_samples)
    window = [s for s in data if _in_window(s.get("ts", 0), hours)]

    host = []
    devices: dict = {}
    for s in window:
        ts = s.get("ts")
        h = s.get("host") or {}
        host.append({
            "ts": ts,
            "cpu": h.get("cpu", 0) or 0,
            "memory_percent": h.get("memory_percent", 0) or 0,
            "disk_percent": h.get("disk_percent", 0) or 0,
            "load1": h.get("load1", 0) or 0,
        })
        for dev, m in (s.get("devices") or {}).items():
            if device_id and dev != device_id:
                continue
            devices.setdefault(dev, []).append({"ts": ts, **m})

    return {
        "hours": hours,
        "count": len(window),
        "first_ts": window[0].get("ts") if window else None,
        "last_ts": window[-1].get("ts") if window else None,
        "host": host,
        "devices": devices,
    }
