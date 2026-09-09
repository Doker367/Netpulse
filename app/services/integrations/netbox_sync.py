"""NetPulse — NetBox Sync Service.

Sincronización del inventario con la API de NetBox
(http://localhost:8000/api/). El token se lee de ``config/.netbox_token``
(string plano). Si no hay token configurado, ``fetch_netbox_devices()``
devuelve ``[]`` sin lanzar errores.
"""

import json
import re
import urllib.error
import urllib.request

from app.core.settings import CONFIG_DIR
from app.services import inventory_svc

NETBOX_URL = "http://localhost:8000"
NETBOX_TOKEN_FILE = CONFIG_DIR / ".netbox_token"
TIMEOUT = 15  # segundos


class NetboxError(RuntimeError):
    """Error de comunicación con NetBox (red, HTTP o falta de token)."""


def _netbox_token() -> str | None:
    """Lee el token de NetBox desde config/.netbox_token."""
    if not NETBOX_TOKEN_FILE.exists():
        return None
    try:
        token = NETBOX_TOKEN_FILE.read_text(encoding="utf-8").strip()
        return token or None
    except OSError:
        return None


def _netbox_get(path: str) -> dict:
    """GET autenticado a la API de NetBox."""
    token = _netbox_token()
    if not token:
        raise NetboxError("No hay token de NetBox configurado (config/.netbox_token)")
    request = urllib.request.Request(
        NETBOX_URL + path,
        headers={"Accept": "application/json", "Authorization": f"Token {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        raise NetboxError(f"NetBox HTTP {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise NetboxError(f"NetBox no disponible: {e.reason}") from e


def fetch_netbox_devices() -> list[dict]:
    """Obtiene los dispositivos desde NetBox, normalizados.

    Returns:
        list[dict]: Lista de ``{name, model, status}``. Vacía si no hay
        token configurado o si NetBox no responde.
    """
    if not _netbox_token():
        return []

    try:
        data = _netbox_get("/api/dcim/devices/?limit=500")
    except NetboxError:
        return []

    devices: list[dict] = []
    for raw in data.get("results", []):
        device_type = raw.get("device_type") or {}
        model = device_type.get("display") or device_type.get("model")
        status = raw.get("status")
        status_value = status.get("value") if isinstance(status, dict) else status
        devices.append(
            {
                "name": raw.get("name") or f"device-{raw.get('id')}",
                "model": model,
                "status": status_value,
            }
        )
    return devices


def _slugify(name: str) -> str:
    """Convierte un nombre en un id seguro (minúsculas, guiones)."""
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-")
    return slug or "netbox-device"


def sync_netbox_to_inventory() -> dict:
    """Sincroniza NetBox → inventario local (devices.yaml).

    Compara los dispositivos de NetBox contra ``inventory_svc.list_devices()``
    (por id y por hostname) y agrega los que faltan con driver ``ios``
    por defecto. Nunca lanza excepción: los fallos se reportan en el
    resultado.

    Returns:
        dict: ``{total, added, skipped, errors, message}``.
    """
    try:
        netbox_devices = fetch_netbox_devices()
    except NetboxError as e:
        return {
            "total": 0,
            "added": 0,
            "skipped": 0,
            "errors": 0,
            "message": str(e),
        }

    if not netbox_devices:
        return {
            "total": 0,
            "added": 0,
            "skipped": 0,
            "errors": 0,
            "message": "No hay dispositivos en NetBox o token no configurado",
        }

    existing = inventory_svc.list_devices()
    existing_ids = {d["id"] for d in existing}
    existing_hostnames = {d["hostname"] for d in existing}

    added = skipped = errors = 0
    for nd in netbox_devices:
        dev_id = _slugify(nd["name"])
        if dev_id in existing_ids or nd["name"] in existing_hostnames:
            skipped += 1
            continue
        try:
            inventory_svc.add_device(
                {
                    "id": dev_id,
                    "hostname": nd["name"],
                    "port": 22,
                    "driver": "ios",
                    "type": "router",
                    "group": None,
                    "tags": ["netbox"],
                    "description": (
                        "Sincronizado desde NetBox "
                        f"(modelo: {nd.get('model') or 'desconocido'}, "
                        f"estado: {nd.get('status') or 'desconocido'})"
                    ),
                }
            )
            added += 1
        except Exception:
            errors += 1  # no romper la sincronización por un dispositivo

    return {
        "total": len(netbox_devices),
        "added": added,
        "skipped": skipped,
        "errors": errors,
        "message": "Sincronización completada",
    }
