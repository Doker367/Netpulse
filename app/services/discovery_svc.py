"""NetPulse — Auto-discovery.

Descubre vecinos de un equipo (LLDP o CDP), los normaliza a candidatos
propuestos y permite importarlos al inventario.

Reglas de honestidad:
- Un candidato solo existe si se puede determinar su IP de gestión (la
  reportada por el protocolo o resuelta por DNS). Si no se resuelve, se
  devuelve en ``omitidos``, nunca se inventa una IP.
- La plataforma es **sugerida**: sale del inventario, del dato del protocolo
  o de una heurística por nombre. Puede ser ``null``; en ese caso la importación
  exige un driver explícito en vez de adivinar.
- Un fallo de un equipo se reporta en su entrada de ``origenes``; nunca
  tumba el escaneo completo.
- El estado de un candidato es siempre ``propuesto``. La importación no
  reescribe el último escaneo: repetirla responde «ya existía».

LLDP se delega en ``napalm_svc.get_lldp_neighbors`` (incluye el fallback
``lldpcli`` por SSH). CDP se lee con ``napalm_svc.run_commands`` en drivers
que lo soportan.
"""

from __future__ import annotations

import copy
import ipaddress
import logging
import os
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import datetime, timezone
from typing import Any, Optional

from app.services import inventory_svc, napalm_svc

logger = logging.getLogger(__name__)

# Presupuesto global del escaneo. Un equipo colgado no debe retener la API.
def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


SCAN_BUDGET = _env_float("NETPULSE_DISCOVERY_TIMEOUT", 25.0)

# Drivers que se intentan por método. LLDP acepta el fallback lldpcli por SSH;
# CDP solo tiene sentido en plataformas que lo hablan (Cisco/Arista).
_LLDP_DRIVERS = {
    "ios", "eos", "junos", "nxos", "iosxr", "ros", "linux",
    "procurve", "comware", "fortios", "huawei",
    "alcatel_aos", "alcatel_sros", "h3c_comware", "generic",
}
_CDP_DRIVERS = {"ios", "nxos", "iosxr", "eos"}

_SUPPORTED = {"lldp": _LLDP_DRIVERS, "cdp": _CDP_DRIVERS}
_METHODS = ("lldp", "cdp")

# Drivers concretos que tiene sentido proponer al importar. Se excluyen
# ``snmp`` y ``generic``: no describen una plataforma, solo un modo de acceso.
_SUGGESTIBLE_DRIVERS = {
    "ios", "iosxr", "nxos", "junos", "eos", "ros", "fortios",
    "linux", "procurve", "comware", "huawei", "hpe",
    "alcatel_aos", "alcatel_sros", "h3c_comware",
}

# Alias aceptados al importar (mismos que normaliza inventory_svc.add_device).
_DRIVER_ALIASES = {
    "mikrotik": "ros",
    "hp_procurve": "procurve",
    "hp_comware": "comware",
}

# Heurística de plataforma por texto libre (nombre, modelo o Platform de CDP).
# El orden importa: nexus antes que cisco, iosxr antes que ios.
_PLATFORM_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"nexus|nx-?os|\bn[3-9]k\b", re.I), "nxos"),
    (re.compile(r"ios-?xr|\bxr[0-9]", re.I), "iosxr"),
    (re.compile(r"juniper|junos|\bmx[0-9]|\bex[0-9]|\bqfx", re.I), "junos"),
    (re.compile(r"arista|\beos\b", re.I), "eos"),
    (re.compile(r"mikrotik|routeros|\brb[0-9]|\bccr[0-9]", re.I), "ros"),
    (re.compile(r"fortigate|fortios|\bforti", re.I), "fortios"),
    (re.compile(r"comware|\bh3c\b", re.I), "comware"),
    (re.compile(r"huawei|\bs[0-9]{4}", re.I), "huawei"),
    (re.compile(r"aruba|procurve|\bhp\b", re.I), "procurve"),
    (re.compile(r"alcatel|sros|\baos\b", re.I), "alcatel_sros"),
    (re.compile(r"cisco|ios\b|cat[0-9]{4}", re.I), "ios"),
    (re.compile(r"linux|ubuntu|debian", re.I), "linux"),
)

_lock = threading.Lock()
_last_scan: Optional[dict[str, Any]] = None


# ── Utilidades ───────────────────────────────────────────────


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _norm(value: Any) -> str:
    return str(value or "").strip().rstrip(".").lower()


def _clean(value: Any) -> str:
    return str(value or "").strip().rstrip(".")


def _normalize_ip(value: Any) -> Optional[str]:
    """Devuelve la IP normalizada o None si el texto no es una IP."""
    text = _clean(value)
    if not text:
        return None
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return None


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug[:48]


def _normalize_driver(value: Any) -> Optional[str]:
    """Normaliza un driver a un identificador conocido del inventario."""
    from app.models.schemas import DeviceDriver

    text = str(getattr(value, "value", value) or "").strip().lower()
    if not text:
        return None
    text = _DRIVER_ALIASES.get(text, text)
    return text if text in {d.value for d in DeviceDriver} else None


def _resolve_management_ip(name: str, by_name: dict[str, dict]) -> Optional[str]:
    """IP de gestión del vecino: directa, del inventario o por DNS.

    No inventa valores: si el nombre no es una IP y no resuelve, devuelve None.
    """
    ip = _normalize_ip(name)
    if ip:
        return ip
    key = _norm(name)
    if not key:
        return None
    known = by_name.get(key)
    if known:
        known_ip = _normalize_ip(known.get("hostname"))
        if known_ip:
            return known_ip
    try:
        return _normalize_ip(socket.gethostbyname(key))
    except (OSError, UnicodeError):
        return None


def _suggest_platform(
    name: Optional[str],
    hint: Optional[str],
    by_name: dict[str, dict],
) -> Optional[str]:
    """Plataforma sugerida (driver) a partir del dato del protocolo o del nombre.

    Devuelve None cuando no hay evidencia suficiente; el import exige driver.
    """
    known = by_name.get(_norm(name)) if name else None
    if known:
        driver = _normalize_driver(known.get("driver"))
        if driver in _SUGGESTIBLE_DRIVERS:
            return driver

    for text in (hint, name):
        text = _clean(text)
        if not text:
            continue
        for pattern, driver in _PLATFORM_PATTERNS:
            if pattern.search(text):
                return driver
    return None


def _has_credentials(raw_device: dict) -> bool:
    creds = raw_device.get("credentials") or {}
    return bool(creds.get("username") or creds.get("password"))


# ── Normalización de vecinos ─────────────────────────────────


def _neighbor(
    hostname: Any = None,
    ip_hint: Any = None,
    port: Any = None,
    platform: Any = None,
    local_iface: Any = None,
) -> dict[str, Any]:
    return {
        "hostname": _clean(hostname),
        "ip_hint": _clean(ip_hint),
        "port": _clean(port),
        "platform": _clean(platform),
        "local_iface": _clean(local_iface),
    }


def _normalize_lldp(data: Any) -> list[dict[str, Any]]:
    """Convierte la salida de NAPALM (o del fallback lldpcli) a lista plana."""
    if not isinstance(data, dict):
        return []
    neighbors: list[dict[str, Any]] = []
    for local_iface, entries in data.items():
        if isinstance(entries, dict):
            entries = [entries]
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, str):
                neighbors.append(_neighbor(hostname=entry, local_iface=local_iface))
                continue
            if not isinstance(entry, dict):
                continue
            ip_hint = (
                entry.get("ip")
                or entry.get("management_ip")
                or entry.get("ip_address")
                or entry.get("management_address")
            )
            neighbors.append(
                _neighbor(
                    hostname=entry.get("hostname") or entry.get("sys_name") or entry.get("system_name"),
                    ip_hint=ip_hint,
                    port=entry.get("port") or entry.get("port_id"),
                    platform=entry.get("platform") or entry.get("model") or entry.get("system_description"),
                    local_iface=local_iface,
                )
            )
    return neighbors


_CDP_DEVICE_RE = re.compile(r"^Device ID:\s*(.+)$", re.M | re.I)
_CDP_IP_RE = re.compile(r"^\s*IP(?:v4)? address:\s*(\S+)", re.M | re.I)
_CDP_PLATFORM_RE = re.compile(r"^\s*Platform:\s*(.+)$", re.M | re.I)
_CDP_IFACE_RE = re.compile(r"^\s*Interface:\s*([^,]+)", re.M | re.I)
_CDP_PORT_RE = re.compile(r"Port ID \(outgoing port\):\s*(\S+)", re.I)
_CDP_UNSUPPORTED = (
    "not enabled",
    "not supported",
    "invalid input",
    "unrecognized command",
    "incomplete command",
)


def _parse_cdp(output: str) -> list[dict[str, Any]]:
    """Parsea ``show cdp neighbors detail`` (IOS/NXOS) a lista plana."""
    if not output:
        return []
    # Cada vecino empieza en una línea "Device ID:"; el bloque llega hasta el
    # siguiente Device ID (los separadores ---- pueden o no estar presentes).
    matches = list(_CDP_DEVICE_RE.finditer(output))
    neighbors: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(output)
        block = output[match.start():end]
        hostname = match.group(1).strip()
        ip_match = _CDP_IP_RE.search(block)
        platform_match = _CDP_PLATFORM_RE.search(block)
        iface_match = _CDP_IFACE_RE.search(block)
        port_match = _CDP_PORT_RE.search(block)
        neighbors.append(
            _neighbor(
                hostname=hostname,
                ip_hint=ip_match.group(1) if ip_match else None,
                port=port_match.group(1) if port_match else None,
                platform=platform_match.group(1) if platform_match else None,
                local_iface=iface_match.group(1) if iface_match else None,
            )
        )
    return neighbors


# ── Escaneo ──────────────────────────────────────────────────


def _origin(
    device_id: str,
    hostname: Optional[str],
    driver: Optional[str],
    metodo: str,
    estado: str,
    *,
    vecinos: int = 0,
    candidatos: int = 0,
    motivo: Optional[str] = None,
    error_type: Optional[str] = None,
    omitidos: Optional[list[dict[str, str]]] = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": device_id,
        "hostname": hostname,
        "driver": driver,
        "metodo": metodo,
        "estado": estado,
        "vecinos": vecinos,
        "candidatos": candidatos,
        "omitidos": omitidos or [],
    }
    if motivo:
        entry["motivo"] = motivo
    if error_type:
        entry["error_type"] = error_type
    return entry


def _precheck(dev: dict, raw_device: dict, metodo: str) -> Optional[dict[str, Any]]:
    """Devuelve una entrada de origen si el equipo no puede escanearse."""
    driver = _norm(dev.get("driver"))
    protocol = _norm(dev.get("protocol"))
    base = (dev["id"], dev.get("hostname"), dev.get("driver"), metodo)

    if driver == "snmp" or (protocol == "snmp" and not dev.get("cli_transport")):
        return _origin(
            *base,
            "sin_soporte",
            motivo="Equipo solo SNMP: no expone CLI para consultar vecinos.",
        )
    if driver not in _SUPPORTED[metodo]:
        return _origin(
            *base,
            "sin_soporte",
            motivo=f"El driver '{dev.get('driver') or 'desconocido'}' no soporta {metodo.upper()} en este backend.",
        )
    if not _has_credentials(raw_device):
        return _origin(
            *base,
            "sin_credenciales",
            motivo="El equipo no tiene usuario ni contraseña configurados.",
        )
    return None


def _fetch_lldp(device_id: str) -> tuple[str, Optional[str], Optional[str], list[dict[str, Any]]]:
    result = napalm_svc.get_lldp_neighbors(device_id)
    if not result.success:
        estado = "sin_soporte" if result.error_type in ("command_error", "driver_error") else "error"
        return estado, result.error or "No se pudo consultar LLDP.", result.error_type, []
    neighbors = _normalize_lldp(result.data or {})
    return "ok" if neighbors else "sin_vecinos", None, None, neighbors


def _fetch_cdp(device_id: str) -> tuple[str, Optional[str], Optional[str], list[dict[str, Any]]]:
    result = napalm_svc.run_commands(device_id, ["show cdp neighbors detail"])
    if not result.success:
        estado = "sin_soporte" if result.error_type in ("command_error", "driver_error") else "error"
        return estado, result.error or "No se pudo consultar CDP.", result.error_type, []

    outputs = result.data or []
    if isinstance(outputs, dict):
        outputs = [outputs]
    text = ""
    command_error = None
    for item in outputs:
        if not isinstance(item, dict):
            continue
        text += "\n" + str(item.get("output") or "")
        command_error = command_error or item.get("error")

    low = text.lower()
    if any(marker in low for marker in _CDP_UNSUPPORTED):
        return "sin_soporte", "El equipo respondió que CDP no está disponible.", "command_error", []
    if command_error:
        return "error", str(command_error), "command_error", []

    neighbors = _parse_cdp(text)
    return "ok" if neighbors else "sin_vecinos", None, None, neighbors


def _collect(
    origin: dict[str, Any],
    neighbors: list[dict[str, Any]],
    candidatos: dict[str, dict[str, Any]],
    by_name: dict[str, dict],
    inventory_ips: set[str],
    inventory_names: set[str],
) -> None:
    omitidos: list[dict[str, str]] = []
    contributed = 0
    for nbr in neighbors:
        name = nbr.get("hostname") or ""
        ip = _normalize_ip(nbr.get("ip_hint")) or _resolve_management_ip(name, by_name)
        if not ip:
            omitidos.append({
                "nombre": name or "sin nombre",
                "motivo": "Sin IP de gestión: el vecino no reportó dirección y el nombre no resuelve.",
            })
            continue

        existing = candidatos.get(ip)
        if existing:
            if origin["id"] not in existing["origenes"]:
                existing["origenes"].append(origin["id"])
            if not existing.get("nombre_sugerido") and name:
                existing["nombre_sugerido"] = name
            if not existing.get("plataforma_sugerida"):
                platform = _suggest_platform(name, nbr.get("platform"), by_name)
                if platform:
                    existing["plataforma_sugerida"] = platform
            continue

        contributed += 1
        candidate: dict[str, Any] = {
            "ip": ip,
            "origen": origin["id"],
            "origenes": [origin["id"]],
            "estado": "propuesto",
            "en_inventario": ip in inventory_ips or _norm(name) in inventory_names,
        }
        if name:
            candidate["nombre_sugerido"] = name
        platform = _suggest_platform(name, nbr.get("platform"), by_name)
        if platform:
            candidate["plataforma_sugerida"] = platform
        if nbr.get("local_iface"):
            candidate["interfaz_local"] = nbr["local_iface"]
        if nbr.get("port"):
            candidate["interfaz_remota"] = nbr["port"]
        candidatos[ip] = candidate

    origin["vecinos"] = len(neighbors)
    origin["candidatos"] = contributed
    origin["omitidos"] = omitidos


def scan(source_ids: Optional[list[str]] = None, metodo: str = "lldp") -> dict[str, Any]:
    """Escanea orígenes y devuelve candidatos deduplicados por IP de gestión.

    No lanza por fallos de dispositivos: cada problema queda en ``origenes``.
    """
    metodo = (metodo or "").strip().lower()
    if metodo not in _METHODS:
        raise ValueError(f"Método no soportado: {metodo!r}. Usa 'lldp' o 'cdp'.")

    public = inventory_svc.list_devices()
    raw = {d["id"]: d for d in inventory_svc.get_raw_devices()}

    by_name: dict[str, dict] = {}
    inventory_ips: set[str] = set()
    inventory_names: set[str] = set()
    for dev in public:
        by_name[_norm(dev["id"])] = dev
        by_name[_norm(dev.get("hostname"))] = dev
        inventory_names.add(_norm(dev["id"]))
        inventory_names.add(_norm(dev.get("hostname")))
        ip = _normalize_ip(dev.get("hostname"))
        if ip:
            inventory_ips.add(ip)

    origins: list[dict[str, Any]] = []
    selected: list[dict] = []
    if source_ids is not None:
        seen: set[str] = set()
        for source_id in source_ids:
            source_id = str(source_id).strip()
            if not source_id or source_id in seen:
                continue
            seen.add(source_id)
            dev = next((d for d in public if d["id"] == source_id), None)
            if dev is None:
                origins.append(_origin(
                    source_id, None, None, metodo, "error",
                    motivo="El equipo no existe en el inventario.",
                    error_type="not_found",
                ))
            else:
                selected.append(dev)
    else:
        selected = list(public)

    fetchable: list[dict] = []
    for dev in selected:
        blocked = _precheck(dev, raw.get(dev["id"], {}), metodo)
        if blocked:
            origins.append(blocked)
        else:
            fetchable.append(dev)

    candidatos: dict[str, dict[str, Any]] = {}
    executor: Optional[ThreadPoolExecutor] = None
    if fetchable:
        executor = ThreadPoolExecutor(max_workers=min(8, len(fetchable)))
        futures = {}
        for dev in fetchable:
            fetch = _fetch_lldp if metodo == "lldp" else _fetch_cdp
            futures[dev["id"]] = executor.submit(fetch, dev["id"])

        deadline = time.monotonic() + max(1.0, SCAN_BUDGET)
        try:
            for dev in fetchable:
                future = futures[dev["id"]]
                try:
                    if not future.done():
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            origins.append(_origin(
                                dev["id"], dev.get("hostname"), dev.get("driver"), metodo, "error",
                                motivo=f"Sin respuesta dentro de {SCAN_BUDGET:.0f} s.",
                                error_type="timeout",
                            ))
                            continue
                        estado, motivo, error_type, neighbors = future.result(timeout=remaining)
                    else:
                        estado, motivo, error_type, neighbors = future.result()
                except FuturesTimeout:
                    origins.append(_origin(
                        dev["id"], dev.get("hostname"), dev.get("driver"), metodo, "error",
                        motivo=f"Sin respuesta dentro de {SCAN_BUDGET:.0f} s.",
                        error_type="timeout",
                    ))
                    continue
                except Exception as exc:  # noqa: BLE001 — un equipo no rompe el escaneo
                    logger.warning("[discovery] %s falló: %s", dev["id"], exc)
                    origins.append(_origin(
                        dev["id"], dev.get("hostname"), dev.get("driver"), metodo, "error",
                        motivo=f"Error inesperado al consultar el equipo: {exc}",
                        error_type="internal_error",
                    ))
                    continue

                entry = _origin(
                    dev["id"], dev.get("hostname"), dev.get("driver"), metodo, estado,
                    motivo=motivo, error_type=error_type,
                )
                if neighbors:
                    _collect(entry, neighbors, candidatos, by_name, inventory_ips, inventory_names)
                origins.append(entry)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    order = {dev["id"]: i for i, dev in enumerate(public)}
    origins.sort(key=lambda item: order.get(item["id"], len(order)))
    candidatos_list = list(candidatos.values())
    candidatos_list.sort(key=lambda item: (not item.get("en_inventario"), item["ip"]))

    omitidos = sum(len(origin["omitidos"]) for origin in origins)
    resumen = {
        "origenes": len(origins),
        "origenes_ok": sum(1 for o in origins if o["estado"] in ("ok", "sin_vecinos")),
        "origenes_con_error": sum(
            1 for o in origins if o["estado"] in ("error", "sin_soporte", "sin_credenciales")
        ),
        "vecinos": sum(o["vecinos"] for o in origins),
        "candidatos": len(candidatos_list),
        "omitidos": omitidos,
    }

    result = {
        "timestamp": _utcnow(),
        "metodo": metodo,
        "origenes": origins,
        "candidatos": candidatos_list,
        "resumen": resumen,
        "mensaje": None if candidatos_list else _mensaje_sin_candidatos(origins),
    }

    global _last_scan
    with _lock:
        _last_scan = copy.deepcopy(result)
    return result


def _mensaje_sin_candidatos(origins: list[dict[str, Any]]) -> str:
    if not origins:
        return "No hay equipos en el inventario para escanear."
    if all(o["estado"] in ("error", "sin_soporte", "sin_credenciales") for o in origins):
        return "Ningún origen pudo consultarse. Revisa credenciales, driver y conectividad."
    return "El escaneo terminó sin vecinos nuevos que proponer."


def get_last_scan() -> Optional[dict[str, Any]]:
    """Copia del último escaneo, o None si aún no se ha ejecutado."""
    with _lock:
        return copy.deepcopy(_last_scan)


def clear_cache() -> None:
    """Olvida el último escaneo (tests y reinicio lógico)."""
    global _last_scan
    with _lock:
        _last_scan = None


# ── Importación ──────────────────────────────────────────────


def import_candidates(
    ips: list[str],
    *,
    username: str = "",
    password: str = "",
    group: Optional[str] = None,
    tipo: Any = None,
    driver: Any = None,
) -> dict[str, Any]:
    """Registra candidatos del último escaneo usando ``inventory_svc.add_device``.

    Es la misma función de registro que ``POST /api/devices``. Un fallo por
    candidato queda en ``fallidos``; nunca aborta el lote ni devuelve 500.
    """
    last = get_last_scan()
    by_ip = {c["ip"]: c for c in (last or {}).get("candidatos", [])}
    metodo = (last or {}).get("metodo") or "lldp"

    existing = inventory_svc.list_devices()
    by_host = {_norm(d.get("hostname")): d for d in existing}
    used_ids = {d["id"] for d in existing}

    fixed_driver = _normalize_driver(driver)
    driver_was_given = bool(getattr(driver, "value", driver))
    created: list[dict[str, Any]] = []
    already: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    seen: set[str] = set()

    for raw_ip in ips:
        ip = _normalize_ip(raw_ip)
        if not ip:
            failed.append({"ip": str(raw_ip), "motivo": "Dirección IP inválida."})
            continue
        if ip in seen:
            continue
        seen.add(ip)

        known = by_host.get(_norm(ip))
        if known:
            already.append({
                "ip": ip,
                "id": known["id"],
                "motivo": f"Ya existe en el inventario como '{known['id']}'.",
            })
            continue

        candidate = by_ip.get(ip)
        if not candidate:
            failed.append({
                "ip": ip,
                "motivo": "No está en el último escaneo. Vuelve a escanear antes de importar.",
            })
            continue

        chosen_driver = fixed_driver or _normalize_driver(candidate.get("plataforma_sugerida"))
        if driver_was_given and not fixed_driver:
            failed.append({
                "ip": ip,
                "motivo": "El driver indicado no es válido.",
            })
            continue
        if not chosen_driver:
            failed.append({
                "ip": ip,
                "motivo": "Plataforma no identificada. Indica el driver manualmente para importar.",
            })
            continue

        name = candidate.get("nombre_sugerido") or ""
        base = _slugify(name) or f"dev-{ip.replace('.', '-').replace(':', '-')}"
        new_id = base
        suffix = 2
        while new_id in used_ids:
            new_id = f"{base}-{suffix}"
            suffix += 1

        payload = {
            "id": new_id,
            "hostname": ip,
            "port": 22,
            "driver": chosen_driver,
            "protocol": "ssh",
            "username": username,
            "password": password,
            "type": getattr(tipo, "value", tipo) if tipo else "router",
            "group": group,
            "tags": ["discovery"],
            "description": f"Descubierto vía {metodo.upper()} desde {candidate.get('origen', '?')}",
        }

        try:
            registered = inventory_svc.add_device(payload)
        except ValueError as exc:
            message = str(exc)
            if "already exists" in message.lower():
                already.append({
                    "ip": ip,
                    "id": new_id,
                    "motivo": f"Ya existe en el inventario como '{new_id}'.",
                })
                used_ids.add(new_id)
            else:
                failed.append({"ip": ip, "motivo": message})
            continue
        except Exception as exc:  # noqa: BLE001 — el lote no se aborta
            logger.warning("[discovery] no se pudo importar %s: %s", ip, exc)
            failed.append({"ip": ip, "motivo": f"No se pudo registrar: {exc}"})
            continue

        used_ids.add(new_id)
        by_host[_norm(ip)] = {"id": registered.get("id", new_id), "hostname": ip}
        created.append({
            "ip": ip,
            "id": registered.get("id", new_id),
            "driver": chosen_driver,
            "nombre_sugerido": name or None,
        })

    return {
        "timestamp": _utcnow(),
        "creados": created,
        "ya_existian": already,
        "fallidos": failed,
        "resumen": {
            "solicitados": len(created) + len(already) + len(failed),
            "creados": len(created),
            "ya_existian": len(already),
            "fallidos": len(failed),
        },
    }
