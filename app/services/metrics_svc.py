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
