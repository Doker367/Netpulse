"""NetPulse — Alertmanager Admin Service (silencios y reglas).

Capa de acceso a la API v2 de Alertmanager y a la API v1 de Prometheus para
la administración de alertas: listar/crear/cancelar silencios y leer reglas.

La única puerta de salida HTTP es ``_request``, de modo que los tests pueden
monkeypatchearla sin tocar la red. Cualquier fallo de conexión o estado
inesperado se traduce a ``UpstreamUnavailable`` (→ 503) y un recurso ausente a
``UpstreamNotFound`` (→ 404); nunca se devuelven datos simulados ni listas
vacías «verdes».
"""

import json
import logging
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Configuración de upstream ─────────────────────────────────
# No hay una variable central en app/core/settings.py para la URL de
# Alertmanager (solo NETPULSE_ALERTMANAGER_TOKEN, que es el token del webhook).
# Se lee aquí con un default compatible con docker-compose.monitoring.yml
# (Alertmanager: 9093, Prometheus: 9090) y se puede sobrescribir por entorno.
ALERTMANAGER_URL = os.getenv("NETPULSE_ALERTMANAGER_URL", "http://localhost:9093").rstrip("/")
PROMETHEUS_URL = os.getenv("NETPULSE_PROMETHEUS_URL", "http://localhost:9090").rstrip("/")

_TIMEOUT = float(os.getenv("NETPULSE_ALERTMANAGER_TIMEOUT", "8"))

# Duraciones permitidas para un silencio (clave → segundos).
DURATION_SECONDS = {
    "2h": 2 * 3600,
    "8h": 8 * 3600,
    "24h": 24 * 3600,
    "7d": 7 * 24 * 3600,
}


class UpstreamUnavailable(Exception):
    """El upstream (Alertmanager/Prometheus) no respondió o devolvió un error."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class UpstreamNotFound(Exception):
    """El recurso no existe en el upstream (ej. silencio inexistente)."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


# ── Helpers de HTTP y fechas ──────────────────────────────────


def _request(
    method: str,
    path: str,
    payload: Optional[dict] = None,
    base: Optional[str] = None,
) -> dict | list:
    """Llamada HTTP JSON a un upstream.

    Args:
        method: Método HTTP (GET/POST/DELETE).
        path: Ruta (ej. ``/api/v2/silences``).
        payload: Cuerpo JSON opcional.
        base: URL base opcional (por defecto Alertmanager).

    Returns:
        Cuerpo JSON parseado (``{}`` si la respuesta está vacía).

    Raises:
        UpstreamNotFound: El upstream respondió 404.
        UpstreamUnavailable: Cualquier otro fallo de red o estado inesperado.
    """
    base_url = (base or ALERTMANAGER_URL).rstrip("/")
    url = f"{base_url}{path}"
    headers = {"Content-Type": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
            if not raw.strip():
                return {}
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        reason = _http_error_reason(exc)
        if exc.code == 404:
            raise UpstreamNotFound(reason) from exc
        raise UpstreamUnavailable(f"HTTP {exc.code}: {reason}") from exc
    except urllib.error.URLError as exc:
        raise UpstreamUnavailable(_short_reason(exc)) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise UpstreamUnavailable("timeout") from exc
    except Exception as exc:  # noqa: BLE001
        logger.warning("[alerts_admin] fallo inesperado llamando %s %s: %s", method, url, exc)
        raise UpstreamUnavailable(_short_reason(exc)) from exc


def _http_error_reason(exc: urllib.error.HTTPError) -> str:
    """Extrae un mensaje corto y legible del cuerpo de un error HTTP."""
    try:
        body = exc.read().decode("utf-8", "replace").strip()
        if body:
            try:
                parsed = json.loads(body)
                msg = parsed.get("error") or parsed.get("message") or body
            except (json.JSONDecodeError, AttributeError):
                msg = body
        else:
            msg = exc.reason or ""
    except Exception:  # noqa: BLE001
        msg = str(exc)
    return str(msg).strip()[:160] or f"HTTP {exc.code}"


def _short_reason(exc: Exception) -> str:
    """Traduce una excepción de red a una causa corta en español."""
    reason = getattr(exc, "reason", None)
    text = str(reason) if reason is not None else str(exc)
    low = text.lower()
    if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in low or "timeout" in low:
        return "timeout"
    if isinstance(reason, ConnectionRefusedError) or "refused" in low:
        return "conexión rechazada"
    if "name or service not known" in low or "nodename" in low:
        return "host no resuelto"
    return (text or exc.__class__.__name__)[:160]


def _parse_ts(value: str) -> Optional[datetime]:
    """Parsea una marca RFC3339 (con o sin ``Z``) a datetime aware."""
    if not value:
        return None
    try:
        s = value.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _fmt_ts(dt: datetime) -> str:
    """Formatea un datetime a RFC3339 UTC con sufijo ``Z``."""
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# ── Silencios ─────────────────────────────────────────────────


def list_active_silences() -> list[dict]:
    """Devuelve únicamente los silencios ACTIVOS ahora desde Alertmanager."""
    data = _request("GET", "/api/v2/silences")
    if isinstance(data, list):
        silences = data
    elif isinstance(data, dict) and isinstance(data.get("data"), list):
        silences = data["data"]
    elif isinstance(data, dict) and "error" in data:
        # Estado honesto: no disfrazar un error de upstream de lista vacía.
        raise UpstreamUnavailable(str(data["error"]))
    else:
        silences = []

    now = datetime.now(timezone.utc)
    active: list[dict] = []
    for s in silences:
        state = (s.get("status") or {}).get("state")
        ends_dt = _parse_ts(s.get("endsAt", ""))
        # Un silencio está activo si no está expirado y su fin es futuro.
        if state == "expired":
            continue
        if ends_dt is None or ends_dt <= now:
            continue
        active.append(_format_silence(s, ends_dt, now))

    active.sort(key=lambda x: x["endsAt"])
    return active


def create_silence(
    matchers: list[dict],
    duration: str,
    comment: Optional[str],
    created_by: str,
) -> dict:
    """Crea un silencio en Alertmanager.

    Args:
        matchers: Lista de matchers ``{name, value, isRegex}`` ya validados.
        duration: Duración permitida (clave de ``DURATION_SECONDS``).
        comment: Comentario libre.
        created_by: Usuario que crea el silencio (``sub`` del JWT).
    """
    seconds = DURATION_SECONDS[duration]
    now = datetime.now(timezone.utc)
    ends_at = now + timedelta(seconds=seconds)

    payload = {
        "matchers": matchers,
        "startsAt": _fmt_ts(now),
        "endsAt": _fmt_ts(ends_at),
        "createdBy": created_by,
        "comment": comment or "",
    }
    resp = _request("POST", "/api/v2/silences", payload)
    silence_id = resp.get("silenceID") or resp.get("id", "")
    return {
        "id": silence_id,
        "startsAt": _fmt_ts(now),
        "endsAt": _fmt_ts(ends_at),
        "createdBy": created_by,
        "comment": comment or "",
    }


def delete_silence(silence_id: str) -> None:
    """Cancela (expira) un silencio vía ``DELETE /api/v2/silence/{id}``."""
    path = f"/api/v2/silence/{urllib.parse.quote(str(silence_id), safe='')}"
    _request("DELETE", path)


def _format_silence(s: dict, ends_dt: datetime, now: datetime) -> dict:
    """Proyecta un silencio de Alertmanager al contrato del router."""
    expires_in = max(0, int((ends_dt - now).total_seconds()))
    return {
        "id": s.get("id", ""),
        "matchers": _format_matchers(s.get("matchers", [])),
        "startsAt": s.get("startsAt", ""),
        "endsAt": s.get("endsAt", ""),
        "createdBy": s.get("createdBy", ""),
        "comment": s.get("comment", ""),
        "expires_in": expires_in,
    }


def _format_matchers(matchers: list) -> list[dict]:
    out = []
    for m in matchers or []:
        out.append({
            "name": m.get("name", ""),
            "value": m.get("value", ""),
            "isRegex": bool(m.get("isRegex", False)),
        })
    return out


# ── Reglas (solo lectura) ─────────────────────────────────────


def list_rules() -> list[dict]:
    """Devuelve las reglas de alerta actuales desde Prometheus ``/api/v1/rules``.

    Alertmanager no expone un endpoint de reglas: las reglas viven en Prometheus.
    """
    data = _request("GET", "/api/v1/rules", base=PROMETHEUS_URL)
    if not isinstance(data, dict) or data.get("status") != "success":
        error = (data or {}).get("error", "respuesta inesperada de Prometheus")
        raise UpstreamUnavailable(error)

    out: list[dict] = []
    for group in data.get("data", {}).get("groups", []):
        group_name = group.get("name", "")
        for rule in group.get("rules", []):
            labels = rule.get("labels") or {}
            out.append({
                "name": rule.get("name", ""),
                "type": rule.get("type", "alerting"),
                "expr": rule.get("query") or rule.get("expr", ""),
                "for": int(rule.get("duration", 0) or 0),
                "severity": labels.get("severity", ""),
                "labels": labels,
                "group": group_name,
            })
    return out
