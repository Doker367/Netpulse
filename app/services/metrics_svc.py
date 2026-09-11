"""NetPulse — Prometheus Metrics Service.

Define todas las métricas de Prometheus para la API NetPulse.
Todas las métricas usan el prefijo 'netpulse_'.
"""

from prometheus_client import Counter, Gauge, Histogram

# ── Request Metrics ──────────────────────────────────────────

netpulse_requests_total = Counter(
    "netpulse_requests_total",
    "Total number of HTTP requests processed",
    ["method", "path", "status"],
)

netpulse_request_duration_seconds = Histogram(
    "netpulse_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

# ── Device Metrics ───────────────────────────────────────────

netpulse_devices_up = Gauge(
    "netpulse_devices_up",
    "Whether a device is reachable (1=up, 0=down)",
    ["device_id", "driver"],
)

netpulse_device_cpu_percent = Gauge(
    "netpulse_device_cpu_percent",
    "Device CPU utilization in percent",
    ["device_id", "driver"],
)

netpulse_device_memory_percent = Gauge(
    "netpulse_device_memory_percent",
    "Device memory utilization in percent",
    ["device_id", "driver"],
)

netpulse_device_memory_used_bytes = Gauge(
    "netpulse_device_memory_used_bytes",
    "Device memory used in bytes",
    ["device_id", "driver"],
)

netpulse_device_memory_total_bytes = Gauge(
    "netpulse_device_memory_total_bytes",
    "Device memory total in bytes",
    ["device_id", "driver"],
)

netpulse_device_disk_percent = Gauge(
    "netpulse_device_disk_percent",
    "Device disk utilization in percent",
    ["device_id", "driver"],
)

netpulse_device_uptime_seconds = Gauge(
    "netpulse_device_uptime_seconds",
    "Device uptime in seconds",
    ["device_id", "driver"],
)

netpulse_device_ping_latency_ms = Gauge(
    "netpulse_device_ping_latency_ms",
    "Device ICMP ping average latency in milliseconds",
    ["device_id", "driver"],
)

netpulse_device_ping_loss_percent = Gauge(
    "netpulse_device_ping_loss_percent",
    "Device ICMP ping packet loss in percent",
    ["device_id", "driver"],
)

netpulse_device_resources_ok = Gauge(
    "netpulse_device_resources_ok",
    "Whether the last resource poll succeeded (1=ok, 0=failed)",
    ["device_id", "driver"],
)

netpulse_device_last_poll_timestamp = Gauge(
    "netpulse_device_last_poll_timestamp",
    "Unix timestamp of the last successful resource poll",
    ["device_id", "driver"],
)

# ── Host / Server Metrics ────────────────────────────────────

netpulse_host_cpu_percent = Gauge(
    "netpulse_host_cpu_percent",
    "NetPulse host CPU utilization in percent",
)

netpulse_host_cpu_count = Gauge(
    "netpulse_host_cpu_count",
    "NetPulse host logical CPU count",
)

netpulse_host_load1 = Gauge(
    "netpulse_host_load1",
    "NetPulse host 1-minute load average",
)

netpulse_host_load5 = Gauge(
    "netpulse_host_load5",
    "NetPulse host 5-minute load average",
)

netpulse_host_load15 = Gauge(
    "netpulse_host_load15",
    "NetPulse host 15-minute load average",
)

netpulse_host_memory_percent = Gauge(
    "netpulse_host_memory_percent",
    "NetPulse host memory utilization in percent",
)

netpulse_host_memory_used_bytes = Gauge(
    "netpulse_host_memory_used_bytes",
    "NetPulse host memory used in bytes",
)

netpulse_host_memory_total_bytes = Gauge(
    "netpulse_host_memory_total_bytes",
    "NetPulse host memory total in bytes",
)

netpulse_host_swap_percent = Gauge(
    "netpulse_host_swap_percent",
    "NetPulse host swap utilization in percent",
)

netpulse_host_disk_percent = Gauge(
    "netpulse_host_disk_percent",
    "NetPulse host disk utilization in percent",
    ["mountpoint", "device"],
)

netpulse_host_disk_used_bytes = Gauge(
    "netpulse_host_disk_used_bytes",
    "NetPulse host disk used in bytes",
    ["mountpoint", "device"],
)

netpulse_host_disk_total_bytes = Gauge(
    "netpulse_host_disk_total_bytes",
    "NetPulse host disk total in bytes",
    ["mountpoint", "device"],
)

netpulse_host_network_bytes_sent = Gauge(
    "netpulse_host_network_bytes_sent",
    "NetPulse host bytes sent per network interface",
    ["interface"],
)

netpulse_host_network_bytes_recv = Gauge(
    "netpulse_host_network_bytes_recv",
    "NetPulse host bytes received per network interface",
    ["interface"],
)

netpulse_host_uptime_seconds = Gauge(
    "netpulse_host_uptime_seconds",
    "NetPulse host uptime in seconds",
)

netpulse_host_processes_total = Gauge(
    "netpulse_host_processes_total",
    "Total number of processes on the NetPulse host",
)

netpulse_host_connections_total = Gauge(
    "netpulse_host_connections_total",
    "Total number of network connections on the NetPulse host",
)

netpulse_process_cpu_percent = Gauge(
    "netpulse_process_cpu_percent",
    "NetPulse API process CPU utilization in percent",
)

netpulse_process_memory_percent = Gauge(
    "netpulse_process_memory_percent",
    "NetPulse API process memory utilization in percent",
)

netpulse_process_memory_rss_bytes = Gauge(
    "netpulse_process_memory_rss_bytes",
    "NetPulse API process resident memory in bytes",
)

netpulse_process_threads = Gauge(
    "netpulse_process_threads",
    "Number of threads in the NetPulse API process",
)

netpulse_process_uptime_seconds = Gauge(
    "netpulse_process_uptime_seconds",
    "NetPulse API process uptime in seconds",
)

# ── NAPALM Operation Metrics ─────────────────────────────────

netpulse_napalm_operations_total = Counter(
    "netpulse_napalm_operations_total",
    "Total number of NAPALM operations executed",
    ["device_id", "operation", "status"],
)

netpulse_napalm_duration_seconds = Histogram(
    "netpulse_napalm_duration_seconds",
    "NAPALM operation duration in seconds",
    ["device_id", "operation"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)

# ── Audit Metrics ────────────────────────────────────────────

netpulse_audit_events_total = Counter(
    "netpulse_audit_events_total",
    "Total number of audit events logged",
)

# ── Auth Metrics ─────────────────────────────────────────────

netpulse_auth_total = Counter(
    "netpulse_auth_total",
    "Total authentication attempts",
    ["status"],
)


def update_device_metrics(device_id: str, driver: str, is_up: bool) -> None:
    """Actualiza el gauge netpulse_devices_up para un dispositivo.

    Args:
        device_id: ID del dispositivo (label).
        driver: Driver NAPALM del dispositivo (label).
        is_up: True si el dispositivo responde, False en caso contrario.
    """
    netpulse_devices_up.labels(device_id=device_id, driver=driver).set(
        1 if is_up else 0
    )


def _as_float(value, default: float = 0.0) -> float:
    """Convierte un valor a float de forma segura."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def update_device_resources(device_id: str, driver: str, data: dict) -> None:
    """Actualiza los gauges de recursos (CPU/RAM/disco/uptime) de un dispositivo.

    Acepta el dict devuelto por ``napalm_svc.get_resources`` (se toleran
    claves ausentes). Calcula porcentajes a partir de usados/totales.
    """
    if not isinstance(data, dict):
        return
    labels = {"device_id": device_id, "driver": str(driver or "")}

    cpu = _as_float(data.get("cpu"))
    mem_total = _as_float(data.get("memory_total"))
    mem_used = _as_float(data.get("memory_used"))
    if not mem_used and mem_total:
        mem_used = mem_total - _as_float(data.get("memory_free"))
    mem_pct = (mem_used / mem_total * 100.0) if mem_total > 0 else 0.0

    hdd_total = _as_float(data.get("hdd_total"))
    hdd_used = _as_float(data.get("hdd_used"))
    if not hdd_used and hdd_total:
        hdd_used = hdd_total - _as_float(data.get("hdd_free"))
    hdd_pct = (hdd_used / hdd_total * 100.0) if hdd_total > 0 else 0.0

    netpulse_device_cpu_percent.labels(**labels).set(cpu)
    netpulse_device_memory_percent.labels(**labels).set(mem_pct)
    netpulse_device_memory_used_bytes.labels(**labels).set(mem_used)
    netpulse_device_memory_total_bytes.labels(**labels).set(mem_total)
    netpulse_device_disk_percent.labels(**labels).set(hdd_pct)
    netpulse_device_uptime_seconds.labels(**labels).set(_as_float(data.get("uptime")))


def update_device_ping(
    device_id: str, driver: str, latency_ms: float, loss_percent: float
) -> None:
    """Actualiza latencia y pérdida de paquetes (ping) de un dispositivo."""
    labels = {"device_id": device_id, "driver": str(driver or "")}
    netpulse_device_ping_latency_ms.labels(**labels).set(_as_float(latency_ms))
    netpulse_device_ping_loss_percent.labels(**labels).set(_as_float(loss_percent))


def mark_device_poll(device_id: str, driver: str, ok: bool, ts: float) -> None:
    """Registra el resultado y timestamp del último poll de recursos."""
    labels = {"device_id": device_id, "driver": str(driver or "")}
    netpulse_device_resources_ok.labels(**labels).set(1 if ok else 0)
    if ok:
        netpulse_device_last_poll_timestamp.labels(**labels).set(ts)
