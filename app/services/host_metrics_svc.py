"""NetPulse — Host / Server Metrics Service.

Recolecta métricas del servidor donde está desplegado NetPulse usando
``psutil``: CPU, memoria, swap, discos, interfaces de red, load average,
uptime, procesos y estado del propio proceso de la API.

Se usa en dos lugares:

- ``GET /api/system/host`` — snapshot JSON para el dashboard.
- ``/api/metrics`` — actualiza los gauges ``netpulse_host_*`` que scrapea
  Prometheus en cada petición.
"""

import logging
import os
import platform
import time
from datetime import datetime, timezone
from typing import Any, Optional

import psutil

from app.services import metrics_svc

logger = logging.getLogger(__name__)

# Proceso de la API (se referencia una sola vez; psutil es lazy).
_PROCESS: Optional[psutil.Process] = None
_BOOT_TIME = psutil.boot_time()

# Primera lectura de CPU: en algunas plataformas la primera llamada a
# cpu_percent devuelve 0.0; un muestreo corto al importar deja el baseline listo.
psutil.cpu_percent(interval=0.1)


def _get_process() -> psutil.Process:
    """Devuelve el objeto psutil.Process de la API (cacheado)."""
    global _PROCESS
    if _PROCESS is None:
        try:
            _PROCESS = psutil.Process(os.getpid())
        except Exception as exc:  # pragma: no cover
            logger.warning("No se pudo obtener el proceso de la API: %s", exc)
    return _PROCESS


def _collect_cpu() -> dict:
    """Métricas de CPU del host."""
    freq = None
    try:
        f = psutil.cpu_freq()
        if f:
            freq = round(f.current, 1)
    except Exception:
        pass
    try:
        load1, load5, load15 = os.getloadavg()
    except (AttributeError, OSError):
        load1 = load5 = load15 = 0.0
    # Muestra corta (100 ms) para que el porcentaje sea representativo
    # independientemente de la frecuencia con la que se llame a collect().
    return {
        "percent": psutil.cpu_percent(interval=0.1),
        "per_cpu": psutil.cpu_percent(interval=None, percpu=True),
        "count_logical": psutil.cpu_count(logical=True),
        "count_physical": psutil.cpu_count(logical=False),
        "freq_mhz": freq,
        "load1": round(load1, 2),
        "load5": round(load5, 2),
        "load15": round(load15, 2),
    }


def _collect_memory() -> dict:
    """Métricas de memoria y swap del host."""
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    return {
        "total": vm.total,
        "used": vm.used,
        "available": vm.available,
        "percent": vm.percent,
        "swap_total": sw.total,
        "swap_used": sw.used,
        "swap_percent": sw.percent,
    }


def _collect_disks() -> list[dict]:
    """Uso de disco por punto de montaje (omite pseudo-filesystems)."""
    disks: list[dict] = []
    try:
        partitions = psutil.disk_partitions(all=False)
    except Exception as exc:
        logger.debug("disk_partitions error: %s", exc)
        return disks

    seen = set()
    for part in partitions:
        if part.mountpoint in seen:
            continue
        seen.add(part.mountpoint)
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        disks.append({
            "device": part.device,
            "mountpoint": part.mountpoint,
            "fstype": part.fstype,
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "percent": usage.percent,
        })
    return disks


def _collect_network() -> list[dict]:
    """Contadores por interfaz de red."""
    nics: list[dict] = []
    try:
        stats = psutil.net_io_counters(pernic=True)
    except Exception as exc:
        logger.debug("net_io_counters error: %s", exc)
        return nics
    for name, s in stats.items():
        if name.startswith(("lo", "utun", "gif", "stf", "awdl", "llw", "bridge")):
            continue
        nics.append({
            "interface": name,
            "bytes_sent": s.bytes_sent,
            "bytes_recv": s.bytes_recv,
            "packets_sent": s.packets_sent,
            "packets_recv": s.packets_recv,
            "errin": s.errin,
            "errout": s.errout,
            "dropin": s.dropin,
            "dropout": s.dropout,
        })
    return nics


def _collect_process() -> dict:
    """Métricas del proceso de la API NetPulse."""
    proc = _get_process()
    if proc is None:
        return {}
    try:
        with proc.oneshot():
            create_time = proc.create_time()
            return {
                "pid": proc.pid,
                "cpu_percent": proc.cpu_percent(interval=None),
                "memory_percent": round(proc.memory_percent(), 2),
                "memory_rss": proc.memory_info().rss,
                "threads": proc.num_threads(),
                "uptime_seconds": int(time.time() - create_time),
            }
    except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
        logger.debug("process metrics error: %s", exc)
        return {}


def _safe(func, default: Any) -> Any:
    """Ejecuta ``func`` y devuelve ``default`` si falla."""
    try:
        return func()
    except Exception as exc:  # noqa: BLE001
        logger.debug("host metric error: %s", exc)
        return default


def collect() -> dict:
    """Recolecta todas las métricas del host y las devuelve como dict."""
    net = _safe(lambda: len(psutil.net_connections(kind="inet")), 0)
    try:
        procs = len(psutil.pids())
    except Exception:
        procs = 0

    return {
        "hostname": platform.node(),
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "arch": platform.machine(),
        "python": platform.python_version(),
        "boot_time": datetime.fromtimestamp(_BOOT_TIME, tz=timezone.utc).isoformat(),
        "uptime_seconds": int(time.time() - _BOOT_TIME),
        "cpu": _safe(_collect_cpu, {}),
        "memory": _safe(_collect_memory, {}),
        "disks": _safe(_collect_disks, []),
        "network": _safe(_collect_network, []),
        "process": _safe(_collect_process, {}),
        "processes_total": procs,
        "connections_total": net,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def update_prometheus(data: Optional[dict] = None) -> dict:
    """Actualiza los gauges ``netpulse_host_*`` con las métricas actuales.

    Args:
        data: Snapshot ya recolectado; si es ``None`` se recolecta ahora.

    Returns:
        El snapshot usado.
    """
    d = data or collect()

    cpu = d.get("cpu") or {}
    metrics_svc.netpulse_host_cpu_percent.set(cpu.get("percent", 0) or 0)
    metrics_svc.netpulse_host_cpu_count.set(cpu.get("count_logical", 0) or 0)
    metrics_svc.netpulse_host_load1.set(cpu.get("load1", 0) or 0)
    metrics_svc.netpulse_host_load5.set(cpu.get("load5", 0) or 0)
    metrics_svc.netpulse_host_load15.set(cpu.get("load15", 0) or 0)

    mem = d.get("memory") or {}
    metrics_svc.netpulse_host_memory_percent.set(mem.get("percent", 0) or 0)
    metrics_svc.netpulse_host_memory_used_bytes.set(mem.get("used", 0) or 0)
    metrics_svc.netpulse_host_memory_total_bytes.set(mem.get("total", 0) or 0)
    metrics_svc.netpulse_host_swap_percent.set(mem.get("swap_percent", 0) or 0)

    for disk in d.get("disks") or []:
        labels = {
            "mountpoint": str(disk.get("mountpoint", "")),
            "device": str(disk.get("device", "")),
        }
        metrics_svc.netpulse_host_disk_percent.labels(**labels).set(disk.get("percent", 0) or 0)
        metrics_svc.netpulse_host_disk_used_bytes.labels(**labels).set(disk.get("used", 0) or 0)
        metrics_svc.netpulse_host_disk_total_bytes.labels(**labels).set(disk.get("total", 0) or 0)

    for nic in d.get("network") or []:
        name = str(nic.get("interface", ""))
        metrics_svc.netpulse_host_network_bytes_sent.labels(interface=name).set(nic.get("bytes_sent", 0) or 0)
        metrics_svc.netpulse_host_network_bytes_recv.labels(interface=name).set(nic.get("bytes_recv", 0) or 0)

    metrics_svc.netpulse_host_uptime_seconds.set(d.get("uptime_seconds", 0) or 0)
    metrics_svc.netpulse_host_processes_total.set(d.get("processes_total", 0) or 0)
    metrics_svc.netpulse_host_connections_total.set(d.get("connections_total", 0) or 0)

    proc = d.get("process") or {}
    metrics_svc.netpulse_process_cpu_percent.set(proc.get("cpu_percent", 0) or 0)
    metrics_svc.netpulse_process_memory_percent.set(proc.get("memory_percent", 0) or 0)
    metrics_svc.netpulse_process_memory_rss_bytes.set(proc.get("memory_rss", 0) or 0)
    metrics_svc.netpulse_process_threads.set(proc.get("threads", 0) or 0)
    metrics_svc.netpulse_process_uptime_seconds.set(proc.get("uptime_seconds", 0) or 0)

    return d
