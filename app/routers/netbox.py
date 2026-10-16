"""NetPulse — NetBox Inventory Router.

Endpoints para consultar y sincronizar el inventario con NetBox.

Todos los endpoints requieren rol **admin**.
Si NetBox no está disponible, devuelven 503.
"""

from typing import Optional

import httpx
import yaml
from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.settings import NETBOX_TOKEN, NETBOX_URL, DEVICES_FILE
from app.middleware.auth import JWTBearer
from app.middleware.rbac import requires_role
from app.models.schemas import UserRole

router = APIRouter(
    prefix="/api/netbox",
    tags=["NetBox"],
    dependencies=[Depends(JWTBearer()), Depends(requires_role(UserRole.ADMIN))],
)

# ── Helpers ───────────────────────────────────────────────────


def _netbox_headers() -> dict:
    """Cabeceras para autenticación con NetBox API."""
    headers = {"Accept": "application/json"}
    if NETBOX_TOKEN:
        headers["Authorization"] = f"Token {NETBOX_TOKEN}"
    return headers


async def _netbox_get(path: str, params: dict | None = None) -> dict:
    """GET a NetBox API. Lanza HTTPException 503 si no está disponible."""
    url = f"{NETBOX_URL}/api{path}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=_netbox_headers(), params=params)
            if resp.status_code == 404:
                raise HTTPException(status_code=404, detail="Recurso no encontrado en NetBox")
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Error de NetBox: {e.response.text[:300]}",
        )
    except (httpx.ConnectError, httpx.TimeoutException, httpx.ConnectTimeout):
        raise HTTPException(
            status_code=503,
            detail="NetBox no está disponible",
        )


async def _netbox_paginated(path: str, params: dict | None = None) -> list[dict]:
    """Recorre todas las páginas de un endpoint paginado de NetBox."""
    all_results: list[dict] = []
    url = f"{NETBOX_URL}/api{path}"
    merged_params = dict(params or {})
    merged_params.setdefault("limit", 200)

    async with httpx.AsyncClient(timeout=30.0) as client:
        while url:
            try:
                resp = await client.get(url, headers=_netbox_headers(), params=merged_params)
                resp.raise_for_status()
                data = resp.json()
                all_results.extend(data.get("results", []))
                url = data.get("next")
                merged_params = {}  # next URL ya incluye params
            except httpx.HTTPStatusError as e:
                raise HTTPException(
                    status_code=e.response.status_code,
                    detail=f"Error de NetBox: {e.response.text[:300]}",
                )
            except (httpx.ConnectError, httpx.TimeoutException, httpx.ConnectTimeout):
                raise HTTPException(
                    status_code=503,
                    detail="NetBox no está disponible",
                )
    return all_results


async def _netbox_post(path: str, body: dict) -> dict:
    """POST a NetBox API."""
    url = f"{NETBOX_URL}/api{path}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                url, headers=_netbox_headers(), json=body
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Error de NetBox al crear: {e.response.text[:300]}",
        )
    except (httpx.ConnectError, httpx.TimeoutException, httpx.ConnectTimeout):
        raise HTTPException(
            status_code=503,
            detail="NetBox no está disponible",
        )


# ── Devices ───────────────────────────────────────────────────


@router.get("/devices")
async def list_netbox_devices(
    site: Optional[str] = Query(None, description="Filtrar por nombre de site"),
    role: Optional[str] = Query(None, description="Filtrar por nombre de role"),
    status: Optional[str] = Query(None, description="Filtrar por estado (active, planned, ...)"),
    manufacturer: Optional[str] = Query(None, description="Filtrar por nombre de manufacturer"),
):
    """Lista todos los dispositivos desde NetBox.

    Filtros opcionales: ``?site=``, ``?role=``, ``?status=``, ``?manufacturer=``.

    Devuelve: name, device_type, role, site, status, primary_ip.
    """
    params: dict = {}
    if site:
        params["site"] = site
    if role:
        params["role"] = role
    if status:
        params["status"] = status
    if manufacturer:
        params["manufacturer"] = manufacturer

    raw = await _netbox_paginated("/dcim/devices/", params)

    return [
        {
            "id": d["id"],
            "name": d.get("name") or f"device-{d['id']}",
            "device_type": (
                d["device_type"]["model"] if d.get("device_type") else None
            ),
            "role": d["role"]["name"] if d.get("role") else None,
            "site": d["site"]["name"] if d.get("site") else None,
            "status": d["status"]["value"] if isinstance(d.get("status"), dict) else d.get("status"),
            "primary_ip": (
                d["primary_ip"]["address"]
                if d.get("primary_ip") and isinstance(d["primary_ip"], dict)
                else d.get("primary_ip")
            ),
        }
        for d in raw
    ]


@router.get("/devices/{device_id}")
async def get_netbox_device(device_id: int):
    """Obtiene un dispositivo individual desde NetBox."""
    raw = await _netbox_get(f"/dcim/devices/{device_id}/")
    return {
        "id": raw["id"],
        "name": raw.get("name") or f"device-{raw['id']}",
        "device_type": (
            raw["device_type"]["model"] if raw.get("device_type") else None
        ),
        "role": raw["role"]["name"] if raw.get("role") else None,
        "site": raw["site"]["name"] if raw.get("site") else None,
        "status": raw["status"]["value"] if isinstance(raw.get("status"), dict) else raw.get("status"),
        "primary_ip": (
            raw["primary_ip"]["address"]
            if raw.get("primary_ip") and isinstance(raw["primary_ip"], dict)
            else raw.get("primary_ip"),
        ),
        "serial": raw.get("serial"),
        "asset_tag": raw.get("asset_tag"),
        "position": raw.get("position"),
        "comments": raw.get("comments"),
    }


# ── Sites ─────────────────────────────────────────────────────


@router.get("/sites")
async def list_netbox_sites():
    """Lista todos los sites desde NetBox."""
    raw = await _netbox_paginated("/dcim/sites/")
    return [
        {
            "id": s["id"],
            "name": s["name"],
            "slug": s.get("slug"),
            "status": (
                s["status"]["value"]
                if isinstance(s.get("status"), dict)
                else s.get("status")
            ),
            "region": s["region"]["name"] if s.get("region") else None,
            "facility": s.get("facility"),
            "description": s.get("description"),
            "device_count": s.get("device_count", 0),
        }
        for s in raw
    ]


# ── IPAM ──────────────────────────────────────────────────────


@router.get("/ipam/prefixes")
async def list_prefixes():
    """Lista todos los prefijos IP desde NetBox."""
    raw = await _netbox_paginated("/ipam/prefixes/")
    return [
        {
            "id": p["id"],
            "prefix": p.get("prefix"),
            "site": p["site"]["name"] if p.get("site") else None,
            "vlan": p["vlan"]["vid"] if p.get("vlan") else None,
            "status": (
                p["status"]["value"]
                if isinstance(p.get("status"), dict)
                else p.get("status")
            ),
            "description": p.get("description"),
        }
        for p in raw
    ]


@router.get("/ipam/ip-addresses")
async def list_ip_addresses():
    """Lista todas las direcciones IP desde NetBox."""
    raw = await _netbox_paginated("/ipam/ip-addresses/")
    return [
        {
            "id": ip["id"],
            "address": ip.get("address"),
            "status": (
                ip["status"]["value"]
                if isinstance(ip.get("status"), dict)
                else ip.get("status"),
            ),
            "assigned_device": (
                ip["assigned_object"]["name"]
                if ip.get("assigned_object")
                else ip.get("assigned_object_id")
            ),
            "description": ip.get("description"),
        }
        for ip in raw
    ]


@router.get("/vlans")
async def list_vlans():
    """Lista todas las VLANs desde NetBox."""
    raw = await _netbox_paginated("/ipam/vlans/")
    return [
        {
            "id": v["id"],
            "vid": v.get("vid"),
            "name": v.get("name"),
            "site": v["site"]["name"] if v.get("site") else None,
            "status": (
                v["status"]["value"]
                if isinstance(v.get("status"), dict)
                else v.get("status"),
            ),
            "description": v.get("description"),
        }
        for v in raw
    ]


# ── Sync ──────────────────────────────────────────────────────


def _read_devices_yaml() -> dict:
    """Lee devices.yaml y devuelve el diccionario completo."""
    if not DEVICES_FILE.exists():
        return {"devices": [], "options": {}}
    with open(DEVICES_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {"devices": [], "options": {}}


def _write_devices_yaml(data: dict):
    """Escribe el diccionario completo a devices.yaml."""
    DEVICES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DEVICES_FILE, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


@router.post("/sync")
async def sync_from_netbox():
    """Sincroniza desde NetBox → devices.yaml local.

    NetBox es la fuente de verdad. Los dispositivos de NetBox se
    reflejan en el inventario local.

    Returns:
        dict con contadores: ``{created: N, updated: N, skipped: N}``.
    """
    netbox_devices = await _netbox_paginated("/dcim/devices/")

    data = _read_devices_yaml()
    existing = {d["id"]: d for d in data.get("devices", [])}

    created = 0
    updated = 0
    skipped = 0

    for nb_dev in netbox_devices:
        dev_name = nb_dev.get("name") or f"netbox-{nb_dev['id']}"
        dev_id = f"netbox-{nb_dev['id']}"

        # Construir entrada local
        local_entry = {
            "id": dev_id,
            "hostname": _extract_ip(nb_dev),
            "port": 22,
            "driver": _map_device_type_to_driver(nb_dev),
            "type": _map_role_to_type(nb_dev),
            "tags": _build_tags(nb_dev),
            "description": nb_dev.get("comments") or "",
            "netbox_id": nb_dev["id"],
            "netbox_name": dev_name,
            "netbox_url": f"{NETBOX_URL}/dcim/devices/{nb_dev['id']}/",
        }

        if dev_name in existing or dev_id in existing:
            # Update existing
            key = dev_name if dev_name in existing else dev_id
            existing_device = existing[key]
            for k, v in local_entry.items():
                existing_device[k] = v
            updated += 1
        else:
            existing[dev_id] = local_entry
            created += 1

    data["devices"] = list(existing.values())
    _write_devices_yaml(data)

    return {"created": created, "updated": updated, "skipped": skipped}


@router.post("/sync/push")
async def push_to_netbox():
    """Empuja dispositivos desde devices.yaml local → NetBox.

    Reverse sync: el inventario local se sincroniza hacia NetBox.
    Solo crea dispositivos que no existen aún en NetBox (por nombre).

    Returns:
        dict con contadores: ``{created: N, skipped: N, errors: N}``.
    """
    data = _read_devices_yaml()
    local_devices = data.get("devices", [])

    if not local_devices:
        return {"created": 0, "skipped": 0, "errors": 0, "message": "Inventario local vacío"}

    # Obtener lista de nombres en NetBox para evitar duplicados
    try:
        existing_nb = await _netbox_paginated("/dcim/devices/")
    except HTTPException:
        # Si NetBox no responde, no podemos continuar
        raise

    existing_names = {
        d.get("name") for d in existing_nb if d.get("name")
    }

    created = 0
    skipped = 0
    errors = 0

    for dev in local_devices:
        dev_name = dev.get("netbox_name") or dev.get("id", "")
        if dev_name in existing_names:
            skipped += 1
            continue

        payload = {
            "name": dev_name,
            "device_type": None,  # Requiere ID de device_type en NetBox
            "role": None,         # Requiere ID de role en NetBox
            "site": None,         # Requiere ID de site en NetBox
            "status": "active",
            "comments": dev.get("description", ""),
        }

        try:
            await _netbox_post("/dcim/devices/", payload)
            created += 1
        except HTTPException:
            errors += 1

    return {"created": created, "skipped": skipped, "errors": errors}


# ── Mapping helpers ───────────────────────────────────────────


def _extract_ip(nb_dev: dict) -> str:
    """Extrae la IP del primary_ip de NetBox."""
    primary_ip = nb_dev.get("primary_ip")
    if primary_ip and isinstance(primary_ip, dict):
        addr: str = primary_ip.get("address", "")
        # NetBox devuelve "10.0.0.1/24" — quitamos la máscara
        return addr.split("/")[0] if "/" in addr else addr
    return "127.0.0.1"


def _map_device_type_to_driver(nb_dev: dict) -> str:
    """Mapea device_type de NetBox a driver NAPALM."""
    dt = nb_dev.get("device_type")
    if dt and isinstance(dt, dict):
        model: str = (dt.get("model") or "").lower()
        manufacturer = (dt.get("manufacturer") or {}).get("name", "").lower() if isinstance(dt.get("manufacturer"), dict) else ""
        full = f"{manufacturer} {model}"
        if "cisco" in full:
            return "ios"
        if "arista" in full:
            return "eos"
        if "juniper" in full:
            return "junos"
        if "mikrotik" in full:
            return "ros"
        if "fortinet" in full or "forti" in full:
            return "fortios"
        if "linux" in full:
            return "linux"
    return "ios"


def _map_role_to_type(nb_dev: dict) -> str:
    """Mapea role de NetBox a tipo de dispositivo local."""
    role = nb_dev.get("role")
    if role and isinstance(role, dict):
        name: str = (role.get("name") or "").lower()
        if "switch" in name:
            return "switch"
        if "firewall" in name:
            return "firewall"
        if "server" in name:
            return "server"
    return "router"


def _build_tags(nb_dev: dict) -> list[str]:
    """Construye tags a partir de los datos de NetBox."""
    tags: list[str] = ["netbox"]
    role = nb_dev.get("role")
    if role and isinstance(role, dict):
        tags.append(role.get("slug", role.get("name", "")))
    site = nb_dev.get("site")
    if site and isinstance(site, dict):
        tags.append(site.get("slug", site.get("name", "")))
    return tags
