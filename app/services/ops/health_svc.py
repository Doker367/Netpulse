"""NetPulse — Health Score de Dispositivos.

Calcula un score 0-100 a partir de CPU, memoria y disco obtenidos
vía NAPALM (napalm_svc.get_resources).

Reglas:
- Se parte de 100 puntos.
- CPU > 70%: resta min(cpu - 70, 30).
- Memoria > 80%: resta min(pct - 80, 20).
- Disco > 85%: resta min(pct - 85, 15).
- El score nunca baja de 0.
- Status: healthy >= 80, degraded >= 50, critical < 50.
"""

import logging

from app.services import inventory_svc, napalm_svc

logger = logging.getLogger(__name__)


def _pct(used, total) -> float:
    """Porcentaje de uso; 0 si el total no es válido."""
    total = float(total or 0)
    if total <= 0:
        return 0.0
    return float(used or 0) / total * 100.0


def _status_for(score: int) -> str:
    """Clasifica el score: healthy / degraded / critical."""
    if score >= 80:
        return "healthy"
    if score >= 50:
        return "degraded"
    return "critical"


def health_score(device_id: str) -> dict:
    """Calcula el health score (0-100) de un dispositivo."""
    result = napalm_svc.get_resources(device_id)
    if not result.success or not isinstance(result.data, dict):
        logger.warning("[%s] health_score: sin datos de recursos: %s",
                       device_id, result.error)
        return {
            "device_id": device_id,
            "score": 0,
            "cpu": None,
            "memory_pct": None,
            "disk_pct": None,
            "status": "critical",
            "error": result.error or "Sin datos de recursos",
        }

    data = result.data
    cpu_raw = data.get("cpu", 0)
    cpu = float(cpu_raw or 0)
    memory_pct = _pct(data.get("memory_used"), data.get("memory_total"))
    disk_pct = _pct(data.get("hdd_used"), data.get("hdd_total"))

    score = 100
    if cpu > 70:
        score -= min(cpu - 70, 30)
    if memory_pct > 80:
        score -= min(memory_pct - 80, 20)
    if disk_pct > 85:
        score -= min(disk_pct - 85, 15)
    score = max(0, int(round(score)))

    return {
        "device_id": device_id,
        "score": score,
        "cpu": cpu_raw,
        "memory_pct": round(memory_pct, 2),
        "disk_pct": round(disk_pct, 2),
        "status": _status_for(score),
    }


def health_all() -> list[dict]:
    """Calcula el health score de todos los dispositivos del inventario."""
    results = []
    for device in inventory_svc.list_devices():
        device_id = device["id"]
        try:
            results.append(health_score(device_id))
        except Exception as e:
            logger.exception("[%s] Error en health_score: %s", device_id, e)
            results.append({
                "device_id": device_id,
                "score": 0,
                "cpu": None,
                "memory_pct": None,
                "disk_pct": None,
                "status": "critical",
                "error": str(e),
            })
    return results
