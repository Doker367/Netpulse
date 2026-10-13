"""NetPulse — Monitoring Router.

Unifica la observabilidad en el dashboard: estado de los servicios
(Prometheus/Loki/Grafana/Alertmanager), métricas clave de Prometheus,
series para gráficos y URLs de Grafana para incrustar.

Cada servicio sigue en su propio puerto; esto es solo la capa de
agregación que consume el dashboard.
"""

import json
import logging
import urllib.parse
import urllib.request
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.middleware.auth import JWTBearer
from app.middleware.rbac import requires_role

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/monitoring", tags=["Monitoring"])

AUTH = [Depends(JWTBearer()), Depends(requires_role("viewer"))]

PROMETHEUS_URL = "http://localhost:9090"
LOKI_URL = "http://localhost:3100"
GRAFANA_URL = "http://localhost:3000"
ALERTMANAGER_URL = "http://localhost:9093"

GRAFANA_DASHBOARD = "/d/netpulse-deception/netpulse-e28094-deception-and-red"

# consultas instantáneas por métrica del resumen
_METRICS = {
    "host_cpu": "netpulse_host_cpu_percent",
    "host_mem": "netpulse_host_memory_percent",
    "host_disk": "max(netpulse_host_disk_percent)",
    "deception_events": "sum(netpulse_deception_events_total)",
    "deception_alerts": "sum(netpulse_deception_alerts_total)",
    "deception_devices": "netpulse_deception_devices_enabled",
    "devices_up": "sum(netpulse_devices_up)",
    "devices_total": "count(netpulse_devices_up)",
}


def _http_ok(url: str, timeout: float = 4.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= resp.status < 500
    except Exception:  # noqa: BLE001
        return False


def _get_json(url: str, timeout: float = 8.0) -> Optional[dict]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001
        logger.debug("[monitoring] %s falló: %s", url, exc)
        return None


def _instant(query: str) -> Optional[float]:
    """Valor de una consulta instantánea de Prometheus."""
    url = f"{PROMETHEUS_URL}/api/v1/query?query={urllib.parse.quote(query)}"
    data = _get_json(url)
    if not data or data.get("status") != "success":
        return None
    result = data.get("data", {}).get("result", [])
    if not result:
        return None
    try:
        return float(result[0]["value"][1])
    except (KeyError, IndexError, ValueError):
        return None


def _instant_vector(query: str, label: str) -> list[dict]:
    """Consulta instantánea que devuelve etiquetas y valores (para barras)."""
    url = f"{PROMETHEUS_URL}/api/v1/query?query={urllib.parse.quote(query)}"
    data = _get_json(url)
    if not data or data.get("status") != "success":
        return []
    out = []
    for item in data.get("data", {}).get("result", []):
        try:
            out.append({"label": item.get("metric", {}).get(label, "?"),
                        "value": float(item["value"][1])})
        except (KeyError, IndexError, ValueError):
            continue
    out.sort(key=lambda x: x["value"], reverse=True)
    return out


def _targets() -> list[dict]:
    data = _get_json(f"{PROMETHEUS_URL}/api/v1/targets")
    if not data or data.get("status") != "success":
        return []
    out = []
    for t in data.get("data", {}).get("activeTargets", []):
        out.append({
            "job": t.get("labels", {}).get("job"),
            "url": t.get("scrapeUrl"),
            "health": t.get("health"),
        })
    return out


@router.get("/overview", dependencies=AUTH)
def overview():
    """Estado consolidado de la observabilidad + métricas clave."""
    return {
        "services": {
            "prometheus": {"url": PROMETHEUS_URL, "up": _http_ok(f"{PROMETHEUS_URL}/-/ready")},
            "loki": {"url": LOKI_URL, "up": _http_ok(f"{LOKI_URL}/ready")},
            "grafana": {"url": GRAFANA_URL, "up": _http_ok(f"{GRAFANA_URL}/api/health")},
            "alertmanager": {"url": ALERTMANAGER_URL,
                             "up": _http_ok(f"{ALERTMANAGER_URL}/-/ready")},
        },
        "targets": _targets(),
        "metrics": {name: _instant(q) for name, q in _METRICS.items()},
        "events_by_type": _instant_vector(
            "sum by (event_type) (netpulse_deception_events_total)", "event_type"
        ),
        "alerts_by_severity": _instant_vector(
            "sum by (severity) (netpulse_deception_alerts_total)", "severity"
        ),
        "links": {
            "grafana_dashboard": f"{GRAFANA_URL}{GRAFANA_DASHBOARD}?kiosk",
            "prometheus": PROMETHEUS_URL,
            "loki": LOKI_URL,
        },
    }


@router.get("/series", dependencies=AUTH)
def series(
    query: str = Query(..., description="Consulta PromQL"),
    minutes: int = Query(60, ge=1, le=1440),
    step: str = Query("30s"),
):
    """Serie temporal (query_range) para los gráficos del dashboard."""
    import time

    end = time.time()
    start = end - minutes * 60
    url = (
        f"{PROMETHEUS_URL}/api/v1/query_range?"
        f"query={urllib.parse.quote(query)}&start={start}&end={end}&step={step}"
    )
    data = _get_json(url)
    if not data or data.get("status") != "success":
        return {"series": [], "error": (data or {}).get("error", "Prometheus no disponible")}
    result = data.get("data", {}).get("result", [])
    series = []
    for item in result:
        series.append({
            "label": item.get("metric", {}).get("__name__") or "value",
            "points": [[float(ts), float(v)] for ts, v in item.get("values", [])],
        })
    return {"series": series}
