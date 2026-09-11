"""NetPulse — Background Metrics Poller.

Tarea periódica (hilo daemon) que recolecta métricas de todos los
dispositivos y del host, y las publica:

- Gauges Prometheus (``netpulse_device_*`` / ``netpulse_host_*``).
- Snapshot en memoria para el endpoint ``/api/system/devices``.

El intervalo se controla con ``NETPULSE_METRICS_INTERVAL`` (segundos,
por defecto 60). Un valor ``0`` deshabilita el poller automático.

El ICMP se lanza con 1 paquete y timeout corto para no penalizar el ciclo.
"""

import logging
import platform
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from app.services import host_metrics_svc, inventory_svc, metrics_history_svc, metrics_svc, napalm_svc

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL = 60

_lock = threading.Lock()
_stop_event = threading.Event()
_thread: Optional[threading.Thread] = None
_interval: int = _DEFAULT_INTERVAL

_state: dict = {
    "last_run": None,
    "interval": _DEFAULT_INTERVAL,
    "duration_ms": None,
    "devices": {},
    "host": {},
}


def is_running() -> bool:
    """True si el hilo del poller está activo."""
    return _thread is not None and _thread.is_alive()


def _icmp_ping(host: str, timeout: int = 2) -> tuple[float, float]:
    """Ping ICMP desde el servidor NetPulse al host indicado.

    Returns:
        ``(latencia_ms, perdida_pct)``; ``(0.0, 100.0)`` si no hay respuesta.
    """
    if not host:
        return 0.0, 100.0
    if platform.system() == "Windows":
        cmd = ["ping", "-n", "1", "-w", "1000", host]
    elif platform.system() == "Darwin":
        cmd = ["ping", "-c", "1", "-t", "1", host]
    else:
        cmd = ["ping", "-c", "1", "-W", "1", host]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return 0.0, 100.0
    out = proc.stdout or ""
    if proc.returncode != 0:
        return 0.0, 100.0
    m = re.search(r"time[=<]([\d.]+)\s*ms", out)
    return (float(m.group(1)), 0.0) if m else (0.0, 0.0)


def _metrics_from_resources(data: dict) -> dict:
    """Normaliza el dict de get_resources a un resumen plano."""
    if not isinstance(data, dict):
        return {}
    cpu = data.get("cpu", 0) or 0
    try:
        cpu = float(str(cpu).split()[0])
    except (TypeError, ValueError):
        cpu = 0.0

    mem_total = float(data.get("memory_total", 0) or 0)
    mem_used = float(data.get("memory_used", 0) or 0)
    if not mem_used and mem_total:
        mem_used = mem_total - float(data.get("memory_free", 0) or 0)
    mem_pct = (mem_used / mem_total * 100.0) if mem_total > 0 else 0.0

    hdd_total = float(data.get("hdd_total", 0) or 0)
    hdd_used = float(data.get("hdd_used", 0) or 0)
    if not hdd_used and hdd_total:
        hdd_used = hdd_total - float(data.get("hdd_free", 0) or 0)
    hdd_pct = (hdd_used / hdd_total * 100.0) if hdd_total > 0 else 0.0

    uptime = data.get("uptime", 0) or 0
    if isinstance(uptime, str):
        uptime = 0
    return {
        "cpu": round(cpu, 2),
        "memory_percent": round(mem_pct, 2),
        "memory_used": int(mem_used),
        "memory_total": int(mem_total),
        "disk_percent": round(hdd_pct, 2),
        "uptime_seconds": int(uptime),
    }


def collect_device(device_id: str) -> dict:
    """Recolecta recursos + latencia de un dispositivo y publica gauges.

    Returns:
        Resumen con ``online``, ``cpu``, ``memory_percent``, ``disk_percent``,
        ``uptime_seconds``, ``latency_ms``, ``packet_loss``, ``error``.
    """
    device = inventory_svc.get_device(device_id)
    driver = (device or {}).get("driver", "")
    host = (device or {}).get("hostname", "")

    latency_ms, loss_pct = _icmp_ping(host)
    metrics_svc.update_device_ping(device_id, driver, latency_ms, loss_pct)

    summary = {
        "device_id": device_id,
        "driver": driver,
        "online": loss_pct < 100,
        "latency_ms": latency_ms,
        "packet_loss": loss_pct,
        "cpu": 0.0,
        "memory_percent": 0.0,
        "memory_used": 0,
        "memory_total": 0,
        "disk_percent": 0.0,
        "uptime_seconds": 0,
        "error": None,
    }

    if not device:
        summary["error"] = "Dispositivo no encontrado"
        metrics_svc.mark_device_poll(device_id, driver, False, time.time())
        metrics_svc.update_device_metrics(device_id, driver, False)
        return summary

    result = napalm_svc.get_resources(device_id)
    now = time.time()
    if result.success and isinstance(result.data, dict):
        normalized = _metrics_from_resources(result.data)
        summary.update(normalized)
        # Si se obtuvieron recursos, el equipo está operativo aunque ICMP esté bloqueado.
        summary["online"] = True
        metrics_svc.update_device_resources(device_id, driver, result.data)
        metrics_svc.mark_device_poll(device_id, driver, True, now)
    else:
        summary["error"] = result.error or "Sin datos de recursos"
        metrics_svc.mark_device_poll(device_id, driver, False, now)

    metrics_svc.update_device_metrics(device_id, driver, summary["online"])
    return summary


def poll_all() -> dict:
    """Ejecuta un ciclo completo: host + todos los dispositivos.

    Returns:
        Snapshot actualizado (``last_run``, ``duration_ms``, ``devices``,
        ``host``).
    """
    start = time.monotonic()
    host = host_metrics_svc.update_prometheus()
    # Muestra rápida solo-host: aparece en segundos, sin esperar a los equipos.
    _record_history(host, {})

    devices: dict = {}
    for device in inventory_svc.list_devices():
        dev_id = device.get("id")
        if not dev_id:
            continue
        try:
            devices[dev_id] = collect_device(dev_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[poller] %s falló: %s", dev_id, exc)
            devices[dev_id] = {"device_id": dev_id, "error": str(exc)}

    with _lock:
        _state["last_run"] = datetime.now(timezone.utc).isoformat()
        _state["duration_ms"] = int((time.monotonic() - start) * 1000)
        _state["devices"] = devices
        _state["host"] = host
        _state["interval"] = _interval
    _record_history(host, devices)
    return snapshot()


def _record_history(host: dict, devices: dict) -> None:
    """Guarda una muestra compacta en el historial nativo."""
    try:
        cpu = host.get("cpu") or {}
        mem = host.get("memory") or {}
        disks = host.get("disks") or []
        root = next((d for d in disks if d.get("mountpoint") == "/"), disks[0] if disks else {})
        host_compact = {
            "cpu": cpu.get("percent", 0) or 0,
            "memory_percent": mem.get("percent", 0) or 0,
            "disk_percent": root.get("percent", 0) or 0,
            "load1": cpu.get("load1", 0) or 0,
        }
        dev_compact = {}
        for dev_id, m in devices.items():
            if not isinstance(m, dict):
                continue
            dev_compact[dev_id] = {
                "cpu": m.get("cpu", 0) or 0,
                "memory_percent": m.get("memory_percent", 0) or 0,
                "disk_percent": m.get("disk_percent", 0) or 0,
                "latency_ms": m.get("latency_ms", 0) or 0,
                "packet_loss": m.get("packet_loss", 0) or 0,
                "online": bool(m.get("online", False)),
            }
        metrics_history_svc.append({
            "ts": time.time(),
            "host": host_compact,
            "devices": dev_compact,
        })
    except Exception as exc:  # noqa: BLE001
        logger.debug("[poller] no se pudo registrar historial: %s", exc)


def snapshot() -> dict:
    """Devuelve una copia del último snapshot recolectado."""
    with _lock:
        return {
            "last_run": _state["last_run"],
            "duration_ms": _state["duration_ms"],
            "interval": _state["interval"],
            "running": is_running(),
            "host": dict(_state["host"]),
            "devices": dict(_state["devices"]),
        }


def _loop() -> None:
    """Bucle principal del poller."""
    logger.info("[poller] métricas iniciado (intervalo=%ss)", _interval)
    # Primer ciclo inmediato
    try:
        poll_all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[poller] primer ciclo falló: %s", exc)
    while not _stop_event.wait(_interval):
        try:
            poll_all()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[poller] ciclo falló: %s", exc)
    logger.info("[poller] métricas detenido")


def start(interval_seconds: int = _DEFAULT_INTERVAL) -> bool:
    """Arranca el poller en un hilo daemon.

    Args:
        interval_seconds: Segundos entre ciclos. ``<= 0`` no arranca nada.

    Returns:
        True si quedó corriendo.
    """
    global _thread, _interval
    if interval_seconds <= 0:
        logger.info("[poller] deshabilitado (intervalo=%s)", interval_seconds)
        return False
    if is_running():
        return True
    _interval = interval_seconds
    _stop_event.clear()
    _thread = threading.Thread(target=_loop, name="netpulse-metrics-poller", daemon=True)
    _thread.start()
    return True


def stop() -> None:
    """Detiene el poller y espera a que termine el hilo."""
    _stop_event.set()
    if _thread is not None:
        _thread.join(timeout=5)
