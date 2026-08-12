"""NetPulse — Proyección de Capacidad.

Proyección lineal simple de crecimiento basada en el contador
netpulse_napalm_operations_total (promedio de ops/día), usando
netpulse_devices_up como proxy de disponibilidad.

Los datos se consultan a Prometheus (http://localhost:9090/api/v1).
"""

import json
import logging
import urllib.parse
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

PROMETHEUS_URL = "http://localhost:9090"
_SECONDS_PER_DAY = 86400.0


def _prometheus_get(path: str, params: dict) -> dict:
    """GET a la API HTTP de Prometheus y devuelve el JSON parseado."""
    url = f"{PROMETHEUS_URL}{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _query(promql: str) -> list:
    """Ejecuta una query instantánea y devuelve la lista de series result."""
    payload = _prometheus_get("/api/v1/query", {"query": promql})
    if payload.get("status") != "success":
        raise RuntimeError(f"Prometheus query falló: {payload.get('error', payload)}")
    return payload.get("data", {}).get("result", [])


def _instant_value(promql: str) -> Optional[float]:
    """Devuelve el valor de la primera serie de una query instantánea."""
    results = _query(promql)
    if not results:
        return None
    try:
        return float(results[0]["value"][1])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def project_growth(device_id: str, days: int = 90) -> dict:
    """Proyecta el crecimiento de operaciones NAPALM de un dispositivo.

    - current_ops: valor actual del contador netpulse_napalm_operations_total.
    - rate_per_day: promedio de ops/día calculado con rate() sobre el
      contador (ventana de 'days' días, con fallback a 7 días).
    - projected_ops: current_ops + rate_per_day * days (proyección lineal).
    """
    days = max(int(days), 1)
    current_ops = 0
    device_up = False
    rate_per_day = 0.0
    error = None

    try:
        value = _instant_value(
            f'sum(netpulse_napalm_operations_total{{device_id="{device_id}"}})'
        )
        current_ops = int(value or 0) if value is not None else 0

        up_value = _instant_value(
            f'sum(netpulse_devices_up{{device_id="{device_id}"}})'
        )
        device_up = bool(up_value) if up_value is not None else False

        # Tasa promedio de ops/día desde el contador (rate() maneja resets)
        rate = _instant_value(
            f'sum(rate(netpulse_napalm_operations_total{{device_id="{device_id}"}}[{days}d]))'
        )
        if rate is None:
            # Fallback: ventana de 7 días si no hay datos suficientes
            rate = _instant_value(
                f'sum(rate(netpulse_napalm_operations_total{{device_id="{device_id}"}}[7d]))'
            )
        if rate is not None:
            rate_per_day = max(0.0, rate * _SECONDS_PER_DAY)
    except Exception as e:
        error = str(e)
        logger.warning("[%s] project_growth: %s", device_id, e)

    projected_ops = current_ops + int(rate_per_day * days)

    result = {
        "device_id": device_id,
        "days": days,
        "current_ops": current_ops,
        "rate_per_day": round(rate_per_day, 2),
        "projected_ops": projected_ops,
        "device_up": device_up,
        "note": "proyección lineal simple",
    }
    if error:
        result["error"] = error
    return result
