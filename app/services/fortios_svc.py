"""NetPulse — Conector Fortinet FortiOS (FortiGate).

Consultas vía REST API (HTTPS). Autenticación por API token (cabecera
``Authorization: Bearer``); si no hay token, se intenta login usuario/clave
en ``/logincheck`` para obtener una sesión.

Devuelve datos normalizados de: estado del sistema, interfaces, políticas
de firewall, sesiones (NAT) y backup de configuración (texto).
"""

import logging
from typing import Optional

import httpx

from app.core.settings import FORTIOS_TIMEOUT, FORTIOS_VERIFY

logger = logging.getLogger(__name__)


class FortiOSError(Exception):
    """Error de comunicación/autenticación con el FortiGate."""


def _base(device: dict) -> str:
    host = device.get("hostname", "")
    # La API REST vive en HTTPS (443 por defecto); permitir api_port explícito.
    port = int(device.get("api_port") or 443)
    return f"https://{host}:{port}"


def _token(device: dict) -> str:
    creds = device.get("credentials") or {}
    return creds.get("api_token") or creds.get("token") or ""


def _auth_headers(device: dict) -> dict:
    token = _token(device)
    if token:
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    return {"Accept": "application/json"}


def _login_session(device: dict) -> Optional[str]:
    """Obtiene una cookie de sesión con usuario/clave (fallback)."""
    creds = device.get("credentials") or {}
    user = creds.get("username") or ""
    password = creds.get("password") or ""
    if not user:
        return None
    try:
        with httpx.Client(verify=FORTIOS_VERIFY, timeout=FORTIOS_TIMEOUT) as client:
            resp = client.post(
                f"{_base(device)}/logincheck",
                data={"username": user, "secretkey": password},
            )
            if resp.status_code == 200 and "APSCOOKIE" in resp.headers.get("set-cookie", ""):
                return resp.headers["set-cookie"].split(";")[0]
    except Exception as exc:  # noqa: BLE001
        logger.debug("[fortios] login falló: %s", exc)
    return None


def _client(device: dict) -> httpx.Client:
    headers = _auth_headers(device)
    session = None
    if not _token(device):
        session = _login_session(device)
        if session:
            headers["Cookie"] = session
    return httpx.Client(headers=headers, verify=FORTIOS_VERIFY, timeout=FORTIOS_TIMEOUT)


def _get_json(device: dict, path: str, params: Optional[dict] = None) -> dict:
    url = f"{_base(device)}{path}"
    try:
        with _client(device) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as exc:
        raise FortiOSError(f"{device.get('id')}: {exc}") from exc
    except ValueError as exc:
        raise FortiOSError(f"respuesta no JSON: {exc}") from exc


def _get_text(device: dict, path: str, params: Optional[dict] = None) -> str:
    url = f"{_base(device)}{path}"
    try:
        with _client(device) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            return resp.text
    except httpx.HTTPError as exc:
        raise FortiOSError(f"{device.get('id')}: {exc}") from exc


# ── Operaciones ──────────────────────────────────────────────


def status(device: dict) -> dict:
    """Estado del sistema (version/modelo/uptime)."""
    data = _get_json(device, "/api/v2/monitor/system/status")
    results = data.get("results", {}) if isinstance(data, dict) else {}
    return {
        "device_id": device.get("id"),
        "model": results.get("model_name") or results.get("model"),
        "hostname": results.get("hostname"),
        "version": results.get("version"),
        "serial": results.get("serial"),
        "uptime": results.get("uptime"),
    }


def interfaces(device: dict) -> list[dict]:
    """Interfaces del FortiGate (nombre, ip, estado, tipo)."""
    data = _get_json(device, "/api/v2/cmdb/system/interface")
    out = []
    for it in data.get("results", []) if isinstance(data, dict) else []:
        ip = it.get("ip")
        if isinstance(ip, dict):
            ip = f"{ip.get('ip')}/{ip.get('mask')}"
        out.append({
            "name": it.get("name"),
            "alias": it.get("alias"),
            "type": it.get("type"),
            "ip": ip,
            "status": (it.get("status") or "").lower(),
            "speed": (it.get("speed") or {}).get("value") if isinstance(it.get("speed"), dict) else it.get("speed"),
            "vlanid": it.get("vlanid"),
        })
    return out


def policies(device: dict) -> list[dict]:
    """Políticas de firewall (src/dst/servicio/acción)."""
    data = _get_json(device, "/api/v2/cmdb/firewall/policy")
    out = []
    for p in data.get("results", []) if isinstance(data, dict) else []:
        out.append({
            "policyid": p.get("policyid"),
            "name": p.get("name"),
            "srcintf": _names(p.get("srcintf")),
            "dstintf": _names(p.get("dstintf")),
            "srcaddr": _names(p.get("srcaddr")),
            "dstaddr": _names(p.get("dstaddr")),
            "service": _names(p.get("service")),
            "action": (p.get("action") or "").lower(),
            "status": (p.get("status") or "").lower(),
            "schedule": p.get("schedule"),
            "nat": p.get("nat"),
            "logtraffic": p.get("logtraffic"),
        })
    return out


def sessions(device: dict, limit: int = 200) -> list[dict]:
    """Sesiones activas (incluye NAT). Puede no estar disponible por modelo."""
    data = _get_json(device, "/api/v2/monitor/firewall/session/select",
                     params={"start": 0, "count": limit})
    out = []
    for s in data.get("results", []) if isinstance(data, dict) else []:
        out.append({
            "proto": s.get("proto"),
            "src": f"{s.get('src')}:{s.get('srcport')}" if s.get("src") else None,
            "dst": f"{s.get('dst')}:{s.get('dstport')}" if s.get("dst") else None,
            "policyid": s.get("policyid"),
            "nat": s.get("natsrc") or s.get("natdst") or None,
            "state": s.get("state"),
            "expiry": s.get("expiry"),
        })
    return out


def running_config(device: dict) -> str:
    """Backup de la configuración global (texto)."""
    return _get_text(device, "/api/v2/monitor/system/config/backup",
                     params={"scope": "global"})


def _names(value) -> list:
    """Normaliza campos que pueden ser lista de dicts {'name': ...} o strings."""
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if isinstance(item, dict):
            out.append(item.get("name") or item.get("q_origin_key") or "")
        else:
            out.append(str(item))
    return out
