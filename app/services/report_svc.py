"""Disponibilidad observada a partir del histórico nativo del poller.

No se interpolan huecos: una muestra representa solo su propia observación.
"""

import hashlib
import json
import math
import threading
from datetime import datetime, timezone

from app.core.settings import METRICS_ENABLED, METRICS_INTERVAL
from app.services import inventory_svc, metrics_history_svc

_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _key(desde: datetime, hasta: datetime, device_ids: list[str] | None, group: str | None) -> str:
    params = {
        "desde": _iso(desde.timestamp()),
        "hasta": _iso(hasta.timestamp()),
        "device_ids": sorted(set(device_ids)) if device_ids is not None else None,
        "group": group,
    }
    return hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()


def cached(desde: datetime, hasta: datetime, device_ids: list[str] | None, group: str | None) -> dict | None:
    """Última generación en este proceso para parámetros equivalentes."""
    with _cache_lock:
        return _cache.get(_key(desde, hasta, device_ids, group))


def generate(desde: datetime, hasta: datetime, device_ids: list[str] | None = None,
             group: str | None = None) -> dict:
    """Calcula disponibilidad sin inferir estado durante intervalos sin muestras."""
    start, end = desde.timestamp(), hasta.timestamp()
    now = datetime.now(timezone.utc).timestamp()
    # overview filtra por edad respecto a ahora (hasta 168 h); después filtramos
    # ambos extremos, incluso cuando la solicitud termina antes de ahora.
    # El límite de overview es 168 h: fuera de ese horizonte no hay serie recuperable.
    hours = min(168.0, max(0.1, (now - start) / 3600 + 0.01))
    history = metrics_history_svc.overview(hours=hours)
    inventory = {d["id"]: d for d in inventory_svc.list_devices()}
    requested = sorted(set(device_ids)) if device_ids is not None else sorted(inventory)
    if group is not None:
        requested = [dev for dev in requested if inventory.get(dev, {}).get("group") == group]
    devices = []
    excluded = []
    window_seconds = end - start

    for dev_id in requested:
        raw = history.get("devices", {}).get(dev_id, [])
        samples = sorted(
            (float(s["ts"]), s["online"]) for s in raw
            if isinstance(s, dict) and "ts" in s and isinstance(s.get("online"), bool)
            and isinstance(s["ts"], (int, float)) and math.isfinite(s["ts"])
            and start <= s["ts"] <= end
        )
        if not samples:
            excluded.append({"device_id": dev_id, "reason": "sin serie online en la ventana"})
            continue
        first, last = samples[0][0], samples[-1][0]
        incidents = []
        down_start = None
        down_last = None
        for ts, online in samples:
            if not online:
                if down_start is None:
                    down_start = ts
                down_last = ts
            elif down_start is not None:
                incidents.append({"inicio": _iso(down_start), "fin": _iso(down_last),
                                  "duracion_segundos": down_last - down_start})
                down_start = down_last = None
        if down_start is not None:
            incidents.append({"inicio": _iso(down_start), "fin": _iso(down_last),
                              "duracion_segundos": down_last - down_start})
        n = len(samples)
        observed_hours = (last - first) / 3600
        # Los huecos internos no se consideran cobertura observada. El intervalo
        # nominal es solo una estimación de cobertura, nunca el denominador SLA.
        interval = max(1, METRICS_INTERVAL)
        covered_seconds = min(window_seconds, sum(
            min(interval, max(0, samples[i + 1][0] - ts))
            for i, (ts, _) in enumerate(samples[:-1])
        ) + min(interval, max(0, end - last)))
        partial = covered_seconds < window_seconds
        devices.append({
            "device_id": dev_id,
            "uptime_percent": round(100 * sum(online for _, online in samples) / n, 2),
            "incidentes": incidents,
            "cobertura": {
                "first_ts": _iso(first), "last_ts": _iso(last), "n_muestras": n,
                "horas_observadas": round(observed_hours, 3),
                "horas_cubiertas_estimadas": round(covered_seconds / 3600, 3),
                "horas_solicitadas": round(window_seconds / 3600, 3),
                "parcial": partial,
                "descripcion": (f"cobertura estimada {covered_seconds / 3600:.2f} h de "
                                f"{window_seconds / 3600:.2f} h"),
            },
        })

    result = {
        "report_id": _key(desde, hasta, device_ids, group),
        "desde": _iso(start), "hasta": _iso(end),
        "device_ids": sorted(set(device_ids)) if device_ids is not None else None,
        "group": group,
        "estado": "con_datos" if devices else "sin_datos",
        "mensaje": ("Disponibilidad calculada solo sobre muestras observadas; revisar cobertura parcial."
                    if devices else "Sin datos en la ventana / recolección desactivada."
                    if not METRICS_ENABLED else "Sin datos en la ventana."),
        "recoleccion_activa": METRICS_ENABLED,
        "equipos": devices,
        "excluidos": excluded,
    }
    with _cache_lock:
        _cache[result["report_id"]] = result
    return result
