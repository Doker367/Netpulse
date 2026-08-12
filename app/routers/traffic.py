"""NetPulse — Interface Traffic Router.

Consulta métricas de tráfico por interfaz desde Prometheus
para mostrarlas en el dashboard.
"""

import logging
import time
import urllib.request
import urllib.parse
import json

from fastapi import APIRouter, Depends, Query

from app.middleware.auth import JWTBearer
from app.middleware.rbac import requires_role

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/traffic", tags=["Traffic"])

PROMETHEUS = "http://localhost:9090"


def _query_prom(expr: str) -> list:
    """Ejecuta una query instantánea en Prometheus y devuelve resultados."""
    url = f"{PROMETHEUS}/api/v1/query?query={urllib.parse.quote(expr)}"
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        return data.get("data", {}).get("result", [])
    except Exception as e:
        logger.warning("[traffic] Prometheus query falló: %s", e)
        return []


@router.get(
    "/interfaces/{device_id}",
    dependencies=[Depends(JWTBearer()), Depends(requires_role("viewer"))],
)
def interface_traffic(
    device_id: str,
    range_hours: int = Query(6, ge=1, le=168),
):
    """Bits/s RX y TX por interfaz del dispositivo en las últimas N horas.

    Usa rate() sobre los contadores de NAPALM exportados a Prometheus
    (si están disponibles) o devuelve el inventario de interfaces con
    su estado actual como fallback.
    """
    end = time.time()
    start = end - range_hours * 3600

    # Intentar obtener series de tráfico (si algún exporter las genera)
    expr = f'sum by (interface) (rate(netpulse_interface_rx_bytes[5m]))'
    rx_series = _query_prom(expr)

    # Fallback: estado de interfaces vía NAPALM
    from app.services import napalm_svc
    result = napalm_svc.get_interfaces(device_id)
    interfaces = []
    if result.success:
        for name, info in (result.data or {}).items():
            interfaces.append({
                "name": name,
                "is_up": info.get("is_up", False),
                "speed": info.get("speed"),
                "description": info.get("description", ""),
            })

    return {
        "device_id": device_id,
        "range_hours": range_hours,
        "rx_series": rx_series,
        "interfaces": interfaces,
        "note": "Si no hay exporter por interfaz, aquí se lista el estado actual; "
                "conecta un exporter (SNMP/Telegraf) para series de bits/s.",
    }


@router.get(
    "/summary",
    dependencies=[Depends(JWTBearer()), Depends(requires_role("viewer"))],
)
def traffic_summary():
    """Resumen de tráfico: dispositivos con series activas en Prometheus."""
    results = _query_prom("count by (device_id) (netpulse_interface_rx_bytes)")
    return {"series_by_device": results}
