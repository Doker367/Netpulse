"""NetPulse — Device Inventory Service.

CRUD operations on devices.yaml. Passwords stored in config
(Phase 1 — will migrate to Vault in Phase 2).
"""

from typing import Optional

import yaml

from app.core.settings import DEVICES_FILE
from app.services.crypto_svc import encrypt

DEFAULT_CONFIG = {
    "devices": [],
    "options": {
        "timeout": 60,
        "max_retries": 3,
        "pool_size": 10,
        "backup_dir": "backups",
    },
}




def _sanitize_for_yaml(data: dict) -> dict:
    """Convierte objetos enum/Pydantic a strings planos para yaml.safe_dump."""
    import json

    def _default(obj):
        if hasattr(obj, 'value'):
            return obj.value
        if hasattr(obj, 'dict'):
            return obj.dict()
        return str(obj)

    return json.loads(json.dumps(data, default=_default))

def _read() -> dict:
    if not DEVICES_FILE.exists():
        _write(DEFAULT_CONFIG)
        return DEFAULT_CONFIG
    with open(DEVICES_FILE) as f:
        data = yaml.safe_load(f) or DEFAULT_CONFIG
    
    # Auto-fix ROS ports for existing devices added wrongly via SSH
    for d in data.get("devices", []):
        if d.get("driver") == "ros" and d.get("port") == 22:
            d["port"] = 8728
            
    return data


def _write(data: dict):
    DEVICES_FILE.parent.mkdir(parents=True, exist_ok=True)
    clean = _sanitize_for_yaml(data)
    with open(DEVICES_FILE, "w") as f:
        yaml.safe_dump(clean, f, default_flow_style=False, allow_unicode=True)


def list_devices() -> list[dict]:
    """Lista todos los dispositivos (sin passwords)."""
    data = _read()
    return [
        {
            "id": d["id"],
            "hostname": d["hostname"],
            "port": d.get("port", 22),
            "driver": d["driver"],
            "protocol": d.get("protocol", "ssh"),
            "community": (d.get("credentials", {}) or {}).get("community") or d.get("community"),
            "type": d.get("type", "router"),
            "group": d.get("group"),
            "tags": d.get("tags", []),
            "description": d.get("description", ""),
        }
        for d in data.get("devices", [])
    ]


def get_device(device_id: str) -> Optional[dict]:
    for d in list_devices():
        if d["id"] == device_id:
            return d
    return None


def add_device(device: dict) -> dict:
    """Agrega un dispositivo al inventario."""
    data = _read()
    # Prevent duplicates
    if any(d["id"] == device["id"] for d in data.get("devices", [])):
        raise ValueError(f"Device {device['id']} already exists")

    driver = device.get("driver", "ios")
    if hasattr(driver, "value"):
        driver = driver.value
    # Normalizar aliases
    if driver == "mikrotik":
        driver = "ros"
    elif driver == "hp_procurve":
        driver = "procurve"
    elif driver == "hp_comware":
        driver = "comware"

    protocol = device.get("protocol") or ("snmp" if driver == "snmp" else "ssh")
    default_port = 161 if protocol == "snmp" or driver == "snmp" else 22
    port = int(device.get("port") or default_port)
    community = device.get("snmp_ro") or device.get("community") or "public"

    entry = {
        "id": device["id"],
        "hostname": device["hostname"],
        "port": port,
        "driver": driver,
        "protocol": protocol,
        "type": device.get("type", "router"),
        "group": device.get("group"),
        "tags": device.get("tags", []),
        "description": device.get("description", ""),
        "credentials": {
            "username": device.get("username", ""),
            "password": encrypt(device.get("password", "")),
            "community": community,
        },
    }
    data.setdefault("devices", []).append(entry)
    _write(data)
    # Return without password
    result = dict(entry)
    del result["credentials"]
    result["community"] = community
    return result


def update_device(device_id: str, updates: dict) -> Optional[dict]:
    """Actualiza un dispositivo existente."""
    data = _read()
    for i, d in enumerate(data.get("devices", [])):
        if d["id"] == device_id:
            for key in ["hostname", "port", "driver", "protocol", "type", "tags", "description", "group"]:
                if key in updates and updates[key] is not None:
                    d[key] = updates[key]
            comm = updates.get("community") or updates.get("snmp_ro")
            if comm or "username" in updates or "password" in updates:
                creds = d.setdefault("credentials", {})
                if comm:
                    creds["community"] = comm
                if "username" in updates:
                    creds["username"] = updates["username"]
                if "password" in updates:
                    creds["password"] = encrypt(updates["password"])
            _write(data)
            return get_device(device_id)
    return None


def delete_device(device_id: str) -> bool:
    data = _read()
    original_len = len(data.get("devices", []))
    data["devices"] = [d for d in data.get("devices", []) if d["id"] != device_id]
    if len(data["devices"]) < original_len:
        _write(data)
        return True
    return False


def add_lab_devices():
    """Agrega los dispositivos del laboratorio al inventario."""
    lab_devices = [
        {
            "id": "cisco-core-01",
            "hostname": "127.0.0.1",
            "port": 2222,
            "driver": "ios",
            "username": "admin",
            "password": "admin",
            "type": "router",
            "group": "Core",
            "tags": ["lab", "cisco", "frr"],
            "description": "Cisco Core Router (FRRouting)",
        },
        {
            "id": "juniper-edge-01",
            "hostname": "127.0.0.1",
            "port": 2223,
            "driver": "ios",
            "username": "admin",
            "password": "admin",
            "type": "router",
            "group": "Edge",
            "tags": ["lab", "juniper", "frr"],
            "description": "Juniper Edge Router (FRRouting)",
        },
        {
            "id": "switch-access-01",
            "hostname": "127.0.0.1",
            "port": 2224,
            "driver": "ios",
            "username": "admin",
            "password": "admin",
            "type": "switch",
            "group": "Access",
            "tags": ["lab", "switch", "frr"],
            "description": "Access Switch (FRRouting)",
        },
        {
            "id": "mikrotik-edge-01",
            "hostname": "127.0.0.1",
            "port": 8728,
            "driver": "ros",
            "username": "admin",
            "password": "",
            "type": "router",
            "group": "Edge",
            "tags": ["lab", "mikrotik", "routeros"],
            "description": "MikroTik Edge Router (RouterOS 7.22)",
        },
        {
            "id": "arista-core-01",
            "hostname": "127.0.0.1",
            "port": 443,
            "driver": "eos",
            "username": "vrnetlab",
            "password": "***REMOVED***",
            "type": "router",
            "group": "Core",
            "tags": ["lab", "arista", "veos"],
            "description": "Arista Core Router (vEOS 4.27.3F)",
        },
    ]
    for dev in lab_devices:
        try:
            add_device(dev)
        except ValueError:
            pass  # already exists
    return list_devices()


# ── Group Operations ─────────────────────────────────────────


def list_groups() -> list[dict]:
    """Lista todos los grupos con conteo de dispositivos."""
    devices = list_devices()
    groups: dict[str, dict] = {}
    for d in devices:
        g = d.get("group")
        if g:
            if g not in groups:
                groups[g] = {"name": g, "description": "", "device_count": 0}
            groups[g]["device_count"] += 1
    return list(groups.values())


def create_group(name: str, description: str = "") -> dict:
    """Crea un grupo (solo registro en memoria)."""
    # Groups are implicit from device group field, but we store metadata
    groups_file = DEVICES_FILE.parent / "groups.yaml"
    if groups_file.exists():
        with open(groups_file) as f:
            groups_data = yaml.safe_load(f) or {}
    else:
        groups_data = {}
    if name in groups_data:
        raise ValueError(f"Group '{name}' already exists")
    groups_data[name] = {"name": name, "description": description}
    with open(groups_file, "w") as f:
        yaml.safe_dump(groups_data, f, default_flow_style=False, allow_unicode=True)
    return {"name": name, "description": description, "device_count": 0}


def get_group_devices(group_name: str) -> list[dict]:
    """Lista dispositivos que pertenecen a un grupo."""
    devices = list_devices()
    return [d for d in devices if d.get("group") == group_name]


def _load_groups_meta() -> dict:
    """Carga metadata de grupos desde groups.yaml."""
    groups_file = DEVICES_FILE.parent / "groups.yaml"
    if not groups_file.exists():
        return {}
    with open(groups_file) as f:
        return yaml.safe_load(f) or {}
