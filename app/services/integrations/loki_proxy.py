"""NetPulse — Loki Proxy Client.

Cliente HTTP (urllib) para la API de Loki (http://localhost:3100).

Proporciona consultas de rango, consultas instantáneas y listado de
labels, devolviendo el JSON crudo de Loki. Los errores de red/HTTP se
elevan como :class:`LokiError` para que los routers los traduzcan a
HTTP 503/502 según corresponda.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

LOKI_URL = "http://localhost:3100"
TIMEOUT = 15  # segundos


class LokiError(RuntimeError):
    """Error de comunicación con Loki (red o HTTP)."""


def _get(path: str, params: dict | None = None) -> dict:
    """Ejecuta un GET contra la API de Loki y devuelve el JSON parseado."""
    url = LOKI_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        raise LokiError(f"Loki HTTP {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise LokiError(f"Loki no disponible: {e.reason}") from e


def query_loki_range(query: str, start_s: float, end_s: float, limit: int = 100) -> dict:
    """Consulta un rango de logs en Loki (LogQL).

    Args:
        query: Expresión LogQL (p. ej. ``{job="netpulse"} |= "ERROR"``).
        start_s: Inicio del rango en segundos (epoch Unix).
        end_s: Fin del rango en segundos (epoch Unix).
        limit: Máximo de resultados a devolver (default 100).

    Returns:
        dict: Respuesta JSON de ``/loki/api/v1/query_range``
        (``{"status": "success", "data": {...}}``).
    """
    params = {
        "query": query,
        "start": int(start_s * 1_000_000_000),  # segundos → nanosegundos
        "end": int(end_s * 1_000_000_000),
        "limit": limit,
    }
    return _get("/loki/api/v1/query_range", params)


def list_loki_labels() -> dict:
    """Lista los labels conocidos por Loki.

    Returns:
        dict: JSON de ``/loki/api/v1/labels`` con la lista de labels.
    """
    return _get("/loki/api/v1/labels")


def query_loki_instant(query: str, time_s: float | None = None) -> dict:
    """Consulta instantánea en Loki (LogQL).

    Args:
        query: Expresión LogQL.
        time_s: Timestamp Unix en segundos para la consulta
            (default: ahora).

    Returns:
        dict: Respuesta JSON de ``/loki/api/v1/query``.
    """
    params: dict[str, Any] = {"query": query}
    if time_s is not None:
        params["time"] = int(time_s * 1_000_000_000)
    return _get("/loki/api/v1/query", params)
