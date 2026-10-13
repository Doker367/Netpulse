"""NetPulse — Deception Topology Service.

Carga, valida y persiste la red señuelo declarada en
``config/deception/network.yaml``: segmentos (VLANs) y dispositivos
simulados con su persona y servicios.

Sigue el patrón de ``app/services/inventory_svc.py``: caché en memoria
por firma mtime/tamaño y escritura atómica (tmp + os.replace).
"""

import copy
import logging
import os
import tempfile
from typing import Optional

import yaml
from pydantic import ValidationError

from app.core.settings import DECEPTION_NETWORK_FILE
from app.models.deception import (
    DeceptionDevice,
    DeceptionNetwork,
    DeceptionSegment,
)

logger = logging.getLogger(__name__)

# Ruta del YAML (monkeypatch-able en tests)
NETWORK_FILE = DECEPTION_NETWORK_FILE

DEFAULT_NETWORK = DeceptionNetwork()

# Caché en memoria (parseo único por firma de archivo)
_cache_signature: Optional[tuple] = None
_cache_data: Optional[DeceptionNetwork] = None


def invalidate_cache() -> None:
    """Invalida la caché de topología (tras escrituras o en tests)."""
    global _cache_signature, _cache_data
    _cache_signature = None
    _cache_data = None


def _signature() -> Optional[tuple]:
    if not NETWORK_FILE.exists():
        return None
    st = NETWORK_FILE.stat()
    return (str(NETWORK_FILE), st.st_mtime_ns, st.st_size)


def _read() -> DeceptionNetwork:
    """Lee y valida el YAML, usando la caché si el archivo no cambió."""
    global _cache_signature, _cache_data
    sig = _signature()
    if sig is None:
        return _cache_data or DEFAULT_NETWORK
    if _cache_data is not None and _cache_signature == sig:
        return _cache_data
    try:
        with open(NETWORK_FILE, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        data = DeceptionNetwork.model_validate(raw)
    except (yaml.YAMLError, ValidationError) as exc:
        logger.error("network.yaml inválido (%s); usando caché o vacío", exc)
        return _cache_data or DEFAULT_NETWORK

    _cache_signature = sig
    _cache_data = data
    return data


def _write(network: DeceptionNetwork) -> None:
    """Escritura atómica del YAML (tmp en el mismo dir + os.replace)."""
    NETWORK_FILE.parent.mkdir(parents=True, exist_ok=True)
    clean = network.model_dump(mode="json", exclude_none=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(NETWORK_FILE.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(clean, f, default_flow_style=False, allow_unicode=True)
        os.replace(tmp_path, NETWORK_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    finally:
        invalidate_cache()


# ── Lectura ──────────────────────────────────────────────────


def load_network() -> DeceptionNetwork:
    """Devuelve la red señuelo completa (copia profunda)."""
    return copy.deepcopy(_read())


def list_segments() -> list[dict]:
    """Lista segmentos/VLANs."""
    return [s.model_dump(mode="json") for s in _read().segments]


def list_devices() -> list[dict]:
    """Lista dispositivos señuelo."""
    return [d.model_dump(mode="json") for d in _read().devices]


def get_device(device_id: str) -> Optional[dict]:
    """Busca un dispositivo señuelo por id."""
    for d in _read().devices:
        if d.id == device_id:
            return d.model_dump(mode="json")
    return None


def get_segment(segment_id: str) -> Optional[dict]:
    """Busca un segmento por id."""
    for s in _read().segments:
        if s.id == segment_id:
            return s.model_dump(mode="json")
    return None


# ── Escritura (CRUD) ─────────────────────────────────────────


def add_device(device: DeceptionDevice | dict) -> dict:
    """Agrega un dispositivo señuelo (valida y persiste)."""
    new = device if isinstance(device, DeceptionDevice) else DeceptionDevice(**device)
    network = copy.deepcopy(_read())
    if any(d.id == new.id for d in network.devices):
        raise ValueError(f"El dispositivo señuelo '{new.id}' ya existe")
    if new.segment and not any(s.id == new.segment for s in network.segments):
        raise ValueError(f"El segmento '{new.segment}' no existe")
    network.devices.append(new)
    _write(network)
    return new.model_dump(mode="json")


def update_device(device_id: str, updates: dict) -> Optional[dict]:
    """Actualiza un dispositivo señuelo existente."""
    network = copy.deepcopy(_read())
    for i, d in enumerate(network.devices):
        if d.id != device_id:
            continue
        merged = d.model_dump(mode="python")
        merged.update({k: v for k, v in updates.items() if v is not None})
        merged["id"] = device_id
        try:
            updated = DeceptionDevice.model_validate(merged)
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc
        if updated.segment and not any(s.id == updated.segment for s in network.segments):
            raise ValueError(f"El segmento '{updated.segment}' no existe")
        network.devices[i] = updated
        _write(network)
        return updated.model_dump(mode="json")
    return None


def delete_device(device_id: str) -> bool:
    """Elimina un dispositivo señuelo."""
    network = copy.deepcopy(_read())
    original = len(network.devices)
    network.devices = [d for d in network.devices if d.id != device_id]
    if len(network.devices) < original:
        _write(network)
        return True
    return False


def add_segment(segment: DeceptionSegment | dict) -> dict:
    """Agrega un segmento/VLAN."""
    new = segment if isinstance(segment, DeceptionSegment) else DeceptionSegment(**segment)
    network = copy.deepcopy(_read())
    if any(s.id == new.id for s in network.segments):
        raise ValueError(f"El segmento '{new.id}' ya existe")
    network.segments.append(new)
    _write(network)
    return new.model_dump(mode="json")


def delete_segment(segment_id: str) -> bool:
    """Elimina un segmento si no tiene dispositivos asociados."""
    network = copy.deepcopy(_read())
    if any(d.segment == segment_id for d in network.devices):
        raise ValueError(f"El segmento '{segment_id}' tiene dispositivos asociados")
    original = len(network.segments)
    network.segments = [s for s in network.segments if s.id != segment_id]
    if len(network.segments) < original:
        _write(network)
        return True
    return False


# ── Resumen ──────────────────────────────────────────────────


def summary() -> dict:
    """Conteo de segmentos y dispositivos (total/habilitados) por rol."""
    network = _read()
    by_role: dict[str, int] = {}
    for d in network.devices:
        by_role[d.role.value] = by_role.get(d.role.value, 0) + 1
    return {
        "segments": len(network.segments),
        "devices": len(network.devices),
        "devices_enabled": sum(1 for d in network.devices if d.enabled),
        "by_role": by_role,
    }


# ── Sync desde NetBox ────────────────────────────────────────

_ROLE_HINTS = {
    "firewall": "firewall", "fortigate": "firewall", "palo": "firewall",
    "switch": "switch", "catalyst": "switch", "nexus": "switch",
    "camera": "camera", "cam": "camera", "hikvision": "camera",
    "server": "server", "poweredge": "server", "proliant": "server",
    "router": "router", "isr": "router", "mikrotik": "router",
}

_DEFAULT_SERVICES = {
    "pc": [{"port": 22, "proto": "ssh"}, {"port": 80, "proto": "http"}],
    "server": [{"port": 22, "proto": "ssh"}, {"port": 445, "proto": "smb"}],
    "camera": [{"port": 80, "proto": "http"}, {"port": 554, "proto": "rtsp"}],
    "switch": [{"port": 22, "proto": "ssh"}, {"port": 161, "proto": "snmp"}],
    "firewall": [{"port": 443, "proto": "https"}, {"port": 22, "proto": "ssh"}],
    "router": [{"port": 22, "proto": "ssh"}, {"port": 161, "proto": "snmp"}],
}


def _role_from_model(model: Optional[str]) -> str:
    text = (model or "").lower()
    for hint, role in _ROLE_HINTS.items():
        if hint in text:
            return role
    return "pc"


def sync_from_netbox(segment_id: str = "vlan-netbox", cidr: str = "10.250.0.0/24") -> dict:
    """Importa dispositivos de NetBox como señuelos (best-effort).

    Crea un segmento de importación si no existe y agrega dispositivos
    nuevos con servicios por defecto según el modelo. No lanza excepción.
    """
    from app.services.integrations import netbox_sync

    try:
        netbox_devices = netbox_sync.fetch_netbox_devices()
    except Exception as exc:  # noqa: BLE001
        return {"total": 0, "added": 0, "skipped": 0, "message": str(exc)}

    if not netbox_devices:
        return {"total": 0, "added": 0, "skipped": 0,
                "message": "No hay dispositivos en NetBox o token no configurado"}

    network = copy.deepcopy(_read())
    if not any(s.id == segment_id for s in network.segments):
        network.segments.append(DeceptionSegment(id=segment_id, cidr=cidr, isolated=True,
                                                 description="Importado de NetBox"))
    existing = {d.id for d in network.devices}
    prefix = cidr.rsplit(".", 1)[0]

    added = skipped = 0
    host = 10
    for nd in netbox_devices:
        dev_id = _slugify(nd.get("name", "device"))
        if dev_id in existing:
            skipped += 1
            continue
        role = _role_from_model(nd.get("model"))
        dev = DeceptionDevice(
            id=dev_id,
            role=role,
            segment=segment_id,
            ip=f"{prefix}.{host}",
            persona={"hostname": nd.get("name", dev_id)[:30]},
            services=[dict(s, engine="native") for s in _DEFAULT_SERVICES[role]],
            tags=["netbox"],
            description=f"Importado de NetBox ({nd.get('model') or 'n/d'})",
        )
        network.devices.append(dev)
        existing.add(dev_id)
        added += 1
        host += 1

    if added:
        _write(network)
    return {"total": len(netbox_devices), "added": added, "skipped": skipped,
            "message": "ok"}


def _slugify(name: str) -> str:
    import re
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(name)).strip("-").lower()
    return slug or "netbox-device"
