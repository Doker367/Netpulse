"""NetPulse — NetBox Inventory Router.

Endpoints para consultar y sincronizar el inventario con NetBox.

Todos los endpoints requieren rol **admin**.
Si NetBox no está configurado o no está disponible, devuelven 503 con un
``detail`` en español que explica la causa.

I/O: las llamadas HTTP a NetBox usan ``httpx.AsyncClient`` (no bloquean el
loop y no se mueven a un hilo: hacerlo sería un paso atrás). Lo que sí es
síncrono —leer y escribir ``devices.yaml`` con PyYAML— se ejecuta en un hilo
con tope de tiempo (tarjeta 13C), conservando la escritura atómica.

Endpoints de esta tarjeta (contrato completo en ``docs/handoffs/netbox-ui.md``):

- ``GET  /api/netbox/status`` → checklist previo a sincronizar (no contacta NetBox).
- ``POST /api/netbox/sync``   → importa dispositivos y devuelve un resumen estable.
"""

import os
import tempfile
from typing import Optional
from urllib.parse import urlparse, urlunparse

import httpx
import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.core.settings import (
    NETBOX_TOKEN,
    NETBOX_URL,
    DEVICES_FILE,
    netbox_auth_header,
)
from app.middleware.auth import JWTBearer
from app.middleware.rbac import get_current_user, requires_role, scope_error
from app.models.schemas import UserRole
from app.services.inventory_svc import invalidate_cache
from app.utils.async_io import BlockingCallTimeout, env_timeout, run_blocking

router = APIRouter(
    prefix="/api/netbox",
    tags=["NetBox"],
    dependencies=[Depends(JWTBearer()), Depends(requires_role(UserRole.ADMIN))],
)

#: Tope para leer/escribir devices.yaml (parseo/dump YAML + replace atómico).
#: Con 500 equipos el dump ronda 100 ms; 10 s sigue siendo una red de
#: seguridad holgada y configurable.
_INVENTORY_IO_TIMEOUT_ENV = "NETPULSE_INVENTORY_IO_TIMEOUT_S"
_INVENTORY_IO_TIMEOUT_S = 10.0

# ── Errores estables ─────────────────────────────────────────

#: ``detail`` literal cuando faltan URL o token. La UI puede compararlo.
NETBOX_SIN_CONFIGURAR = (
    "NetBox no configurado: define NETBOX_URL y NETBOX_TOKEN "
    "(o crea config/.netbox_token)"
)


def _netbox_configured() -> bool:
    """¿Hay URL y token utilizables? No contacta con NetBox."""
    return bool((NETBOX_URL or "").strip() and (NETBOX_TOKEN or "").strip())


def _require_netbox_configured() -> None:
    """Aborta con 503 y un ``detail`` accionable si falta configuración."""
    if not _netbox_configured():
        raise HTTPException(status_code=503, detail=NETBOX_SIN_CONFIGURAR)


def _short_cause(exc: Exception) -> str:
    """Causa corta en español para un fallo de red/HTTP (sin traceback)."""
    if isinstance(exc, httpx.TimeoutException):
        return "tiempo de espera agotado"
    text = str(exc).lower()
    if "refused" in text:
        return "conexión rechazada"
    if "nodename" in text or "name or service" in text or "name resolution" in text:
        return "host no resuelto"
    if "unreachable" in text:
        return "host no alcanzable"
    if "certificate" in text or "ssl" in text:
        return "error TLS"
    message = str(exc).strip() or exc.__class__.__name__
    return message[:120]


def _netbox_unavailable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail=f"NetBox no disponible: {_short_cause(exc)}",
    )


def _netbox_status_error(exc: httpx.HTTPStatusError, prefix: str = "") -> HTTPException:
    """Traduce un error HTTP de NetBox.

    Un 5xx significa que NetBox está caído o mal: se devuelve 503 y no el
    código crudo, para que la UI no lo confunda con un error del inventario.
    Los 4xx (token inválido, permiso) se conservan: son accionables tal cual.
    """
    code = exc.response.status_code
    if code >= 500:
        return HTTPException(
            status_code=503,
            detail=f"NetBox no disponible: error {code} de NetBox",
        )
    return HTTPException(
        status_code=code,
        detail=f"{prefix}Error de NetBox: {exc.response.text[:300]}",
    )


def _masked_netbox_url(url: str) -> str:
    """URL de NetBox sin credenciales embebidas (usuario/contraseña)."""
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.username is None and parsed.password is None:
        return url
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunparse(parsed._replace(netloc=f"***@{host}"))


def _netbox_headers() -> dict:
    """Cabeceras para autenticación con NetBox API."""
    headers = {"Accept": "application/json"}
    headers.update(netbox_auth_header(NETBOX_TOKEN))
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
        raise _netbox_status_error(e)
    except httpx.HTTPError as e:
        raise _netbox_unavailable(e)


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
                raise _netbox_status_error(e)
            except httpx.HTTPError as e:
                raise _netbox_unavailable(e)
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
        raise _netbox_status_error(e, prefix="al crear: ")
    except httpx.HTTPError as e:
        raise _netbox_unavailable(e)


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


# ── Estado / checklist ────────────────────────────────────────

_STATUS_ALCANCE = (
    "Dispositivos de NetBox → devices.yaml. Campos: id local 'netbox-<id>', "
    "hostname (primary_ip sin máscara), driver (fabricante/modelo), type (rol), "
    "tags (rol y site), description (comments), netbox_id, netbox_name y "
    "netbox_url. Solo se importan dispositivos con primary_ip."
)

_STATUS_NO_HACE = [
    "no borra dispositivos del inventario local (los equipos que no están en NetBox se conservan)",
    "no sobrescribe credenciales locales (username, password, enable_password)",
    "no cambia el id local de un equipo existente (solo lo asigna al crear)",
    "no sobrescribe el puerto de un equipo ya existente (solo fija 22 al crear)",
    "no escribe ni modifica nada en NetBox (para eso existe POST /api/netbox/sync/push)",
    "no importa sites, racks, VLANs, prefijos ni direcciones IP (solo dispositivos)",
]


@router.get("/status")
async def netbox_status():
    """Checklist previo a sincronizar. **No contacta con NetBox.**

    Devuelve si hay URL y token, la URL enmascarada y, sobre todo, qué hace y
    qué no hace la sincronización. Un NetBox no configurado no es un error de
    este endpoint: es ``configurado: false`` con un mensaje accionable.
    """
    configurado = _netbox_configured()
    return {
        "configurado": configurado,
        "url_enmascarada": _masked_netbox_url(NETBOX_URL),
        "tiene_token": bool((NETBOX_TOKEN or "").strip()),
        "alcance": _STATUS_ALCANCE,
        "no_hace": list(_STATUS_NO_HACE),
        "sync_endpoint": "/api/netbox/sync",
        "mensaje": "" if configurado else NETBOX_SIN_CONFIGURAR,
    }


# ── Sync ──────────────────────────────────────────────────────


def _read_devices_yaml() -> dict:
    """Lee devices.yaml y devuelve el diccionario completo."""
    if not DEVICES_FILE.exists():
        return {"devices": [], "options": {}}
    with open(DEVICES_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {"devices": [], "options": {}}


def _write_devices_yaml(data: dict) -> None:
    """Escribe devices.yaml de forma atómica (tmp + ``os.replace``).

    Un fallo de escritura deja intacto el archivo anterior: nunca queda un
    YAML a medias.
    """
    DEVICES_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(DEVICES_FILE.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)
        os.replace(tmp_path, DEVICES_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    finally:
        invalidate_cache()


async def _read_devices_yaml_async() -> dict:
    """``_read_devices_yaml`` fuera del event loop.

    Parsear 500 equipos cuesta decenas de ms (cientos con inventarios
    mayores) y el archivo puede estar en un disco lento: en un handler
    ``async`` eso congela todo el loop.
    """
    return await run_blocking(
        _read_devices_yaml,
        timeout=env_timeout(_INVENTORY_IO_TIMEOUT_ENV, _INVENTORY_IO_TIMEOUT_S),
        label="leer devices.yaml",
    )


async def _write_devices_yaml_async(data: dict) -> None:
    """``_write_devices_yaml`` fuera del event loop.

    La atomicidad la sigue garantizando tmp + ``os.replace`` dentro del hilo;
    el tope solo acota cuánto espera el handler.
    """
    await run_blocking(
        _write_devices_yaml,
        data,
        timeout=env_timeout(_INVENTORY_IO_TIMEOUT_ENV, _INVENTORY_IO_TIMEOUT_S),
        label="escribir devices.yaml",
    )


def _build_local_entry(nb_dev: dict) -> dict:
    """Convierte un dispositivo de NetBox en la entrada de devices.yaml.

    ``group`` solo se copia si NetBox lo entrega como texto no vacío; el
    payload estándar de NetBox no lo incluye, así que en la práctica el grupo
    de un equipo existente se conserva y uno nuevo queda sin grupo.
    """
    dev_name = nb_dev.get("name") or f"netbox-{nb_dev['id']}"
    dev_id = f"netbox-{nb_dev['id']}"
    entry = {
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
    group = nb_dev.get("group")
    if isinstance(group, str) and group.strip():
        entry["group"] = group
    return entry


@router.post("/sync")
async def sync_from_netbox(request: Request):
    """Sincroniza desde NetBox → devices.yaml local y devuelve un resumen.

    NetBox es la fuente de verdad para los campos importados. Garantías:

    - **Nunca borra** dispositivos locales: los que no están en NetBox se
      conservan tal cual.
    - **No toca credenciales** locales ni el puerto de un equipo existente.
    - Los equipos de NetBox **sin primary_ip se saltan** con motivo: no se
      inventa ``127.0.0.1``.
    - Con un token restringido, cada equipo candidato se valida contra el
      grupo destino real antes de escribir. Los que quedan fuera de scope se
      informan en ``saltados[{device_id, motivo}]`` (no son errores) y el
      resto se importa con normalidad; no hay 403 global.
    - Listas ordenadas por ``device_id`` (respuesta determinista).
    - Escritura atómica; si falla, el inventario anterior queda intacto.

    Returns:
        ``{status, solicitados, creados, actualizados, sin_cambios, saltados,
        errores, inventario_total, netbox_total}``. Se mantienen además los
        contadores legados ``created``/``updated``/``skipped`` para la UI
        actual.
    """
    _require_netbox_configured()
    user = get_current_user(request)

    netbox_devices = await _netbox_paginated("/dcim/devices/")

    try:
        data = await _read_devices_yaml_async()
    except BlockingCallTimeout as e:
        raise HTTPException(
            status_code=504,
            detail=(
                "No se pudo leer el inventario local a tiempo: "
                f"superó {env_timeout(_INVENTORY_IO_TIMEOUT_ENV, _INVENTORY_IO_TIMEOUT_S):g} s "
                f"({_INVENTORY_IO_TIMEOUT_ENV}). No se aplicó ningún cambio."
            ),
        ) from e
    except (yaml.YAMLError, OSError) as e:
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo leer el inventario local: {_short_cause(e)}",
        )

    if not isinstance(data, dict):
        raise HTTPException(
            status_code=500,
            detail="No se pudo leer el inventario local: devices.yaml no contiene un objeto",
        )

    local_devices = data.get("devices") or []
    if not isinstance(local_devices, list):
        raise HTTPException(
            status_code=500,
            detail="No se pudo leer el inventario local: 'devices' no es una lista",
        )

    existing = {
        d["id"]: d
        for d in local_devices
        if isinstance(d, dict) and isinstance(d.get("id"), str) and d.get("id")
    }

    creados: list[str] = []
    actualizados: list[str] = []
    saltados: list[dict] = []
    errores: list[dict] = []
    sin_cambios = 0

    for nb_dev in netbox_devices:
        raw_id = nb_dev.get("id") if isinstance(nb_dev, dict) else None
        dev_id = f"netbox-{raw_id}" if raw_id is not None else "netbox-?"

        if not isinstance(nb_dev, dict):
            errores.append({
                "device_id": dev_id,
                "detalle": "respuesta de NetBox inválida: el dispositivo no es un objeto",
            })
            continue

        primary_ip = nb_dev.get("primary_ip")
        if not (isinstance(primary_ip, dict) and primary_ip.get("address")):
            saltados.append({
                "device_id": dev_id,
                "motivo": "sin primary_ip en NetBox: no hay dirección de gestión para monitorizar",
            })
            continue

        try:
            local_entry = _build_local_entry(nb_dev)
        except (KeyError, TypeError, AttributeError, ValueError) as e:
            errores.append({
                "device_id": dev_id,
                "detalle": f"datos incompletos en NetBox: {e}",
            })
            continue

        dev_name = local_entry["netbox_name"]
        target = existing.get(dev_name) or existing.get(dev_id)

        # Grupo destino real: el que quedará escrito en devices.yaml. Un
        # equipo existente conserva su grupo salvo que NetBox aporte uno;
        # uno nuevo solo lo tiene si NetBox lo entrega (si no, queda sin
        # grupo y un token restringido no puede crearlo).
        if target is not None:
            current_group = target.get("group")
            dest_group = local_entry.get("group")
            scope_detail = scope_error(user, current_group)
            if scope_detail is None and dest_group is not None:
                scope_detail = scope_error(user, dest_group)
            target_id = target.get("id") or dev_id
        else:
            scope_detail = scope_error(user, local_entry.get("group"))
            target_id = dev_id

        if scope_detail:
            saltados.append({"device_id": target_id, "motivo": scope_detail})
            continue

        if target is None:
            existing[dev_id] = local_entry
            local_devices.append(local_entry)
            creados.append(dev_id)
            continue

        # Actualización: solo campos importados; el puerto y el id local se
        # respetan (cambiar el id rompería credenciales y referencias).
        changed = False
        for key, value in local_entry.items():
            if key == "id" and target.get("id"):
                continue
            if key == "port":
                if not target.get("port"):
                    target["port"] = value
                    changed = True
                continue
            if target.get(key) != value:
                target[key] = value
                changed = True

        if changed:
            actualizados.append(target.get("id") or dev_id)
        else:
            sin_cambios += 1

    if creados or actualizados:
        data["devices"] = local_devices
        try:
            await _write_devices_yaml_async(data)
        except BlockingCallTimeout as e:
            raise HTTPException(
                status_code=504,
                detail=(
                    "La escritura del inventario local superó "
                    f"{env_timeout(_INVENTORY_IO_TIMEOUT_ENV, _INVENTORY_IO_TIMEOUT_S):g} s "
                    f"({_INVENTORY_IO_TIMEOUT_ENV}); la escritura es atómica, pero "
                    "no se puede confirmar si llegó a aplicarse. Verifica "
                    "devices.yaml antes de reintentar."
                ),
            ) from e
        except OSError as e:
            raise HTTPException(
                status_code=500,
                detail=(
                    "No se pudo escribir el inventario local: "
                    f"{_short_cause(e)}. No se aplicó ningún cambio."
                ),
            )

    status = "partial" if (saltados or errores) else "ok"

    return {
        "status": status,
        "solicitados": len(netbox_devices),
        "creados": sorted(creados),
        "actualizados": sorted(actualizados),
        "sin_cambios": sin_cambios,
        "saltados": sorted(saltados, key=lambda item: item["device_id"]),
        "errores": sorted(errores, key=lambda item: item["device_id"]),
        "inventario_total": len(local_devices),
        "netbox_total": len(netbox_devices),
        # Compatibilidad con la UI actual (dashboard.html): contadores legados.
        "created": len(creados),
        "updated": len(actualizados),
        "skipped": len(saltados),
    }



@router.post("/sync/push")
async def push_to_netbox():
    """Empuja dispositivos desde devices.yaml local → NetBox.

    Reverse sync: el inventario local se sincroniza hacia NetBox.
    Solo crea dispositivos que no existen aún en NetBox (por nombre).

    Returns:
        dict con contadores: ``{created: N, skipped: N, errors: N}``.
    """
    try:
        data = await _read_devices_yaml_async()
    except BlockingCallTimeout as e:
        raise HTTPException(
            status_code=504,
            detail=(
                "No se pudo leer el inventario local a tiempo: "
                f"superó {env_timeout(_INVENTORY_IO_TIMEOUT_ENV, _INVENTORY_IO_TIMEOUT_S):g} s. "
                "No se envió nada a NetBox."
            ),
        ) from e
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
