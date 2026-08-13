"""NetPulse — Netmiko Legacy Service.

Motor de respaldo para equipos legacy que NAPALM no soporta
(telnet, drivers antiguos). Netmiko soporta SSH y TELNET para
Cisco, HP ProCurve/Comware, Juniper, MikroTik, etc.

Se usa automáticamente cuando NAPALM falla por auth/connection
y el dispositivo es legacy (puerto 23 = telnet, o driver
procurve/comware/hpe/ios antiguo).
"""

import logging
import re
from typing import Optional

from netmiko import ConnectHandler

from app.services.crypto_svc import decrypt

logger = logging.getLogger(__name__)

# Mapeo driver NetPulse → device_type netmiko (ssh y telnet)
_DEVICE_TYPES = {
    "ios":      {"ssh": "cisco_ios",           "telnet": "cisco_ios_telnet"},
    "iosxr":    {"ssh": "cisco_xr",            "telnet": "cisco_xr_telnet"},
    "nxos":     {"ssh": "cisco_nxos",          "telnet": "cisco_nxos_telnet"},
    "junos":    {"ssh": "juniper_junos",       "telnet": "juniper_junos_telnet"},
    "eos":      {"ssh": "arista_eos",          "telnet": "arista_eos_telnet"},
    "procurve": {"ssh": "hp_procurve",         "telnet": "hp_procurve_telnet"},
    "comware":  {"ssh": "hp_comware",          "telnet": "hp_comware_telnet"},
    "hpe":      {"ssh": "hp_procurve",         "telnet": "hp_procurve_telnet"},
    "aruba":    {"ssh": "aruba_procurve",      "telnet": "aruba_procurve_telnet"},
    "ros":      {"ssh": "mikrotik_routeros",   "telnet": None},  # MikroTik no usa telnet
    "linux":    {"ssh": "linux",               "telnet": None},
}

# Drivers legacy que merecen intento netmiko aunque NAPALM falle
LEGACY_DRIVERS = {"procurve", "comware", "hpe", "aruba", "ios", "iosxr", "nxos", "junos", "eos"}


def is_legacy_device(device: dict) -> bool:
    """True si el dispositivo debe intentar el motor netmiko."""
    driver = str(device.get("driver", "")).lower()
    port = device.get("port", 22)
    # Telnet (23) siempre es legacy
    if port == 23:
        return driver in _DEVICE_TYPES
    return driver in LEGACY_DRIVERS


def _device_type(device: dict) -> Optional[str]:
    driver = str(device.get("driver", "")).lower()
    port = device.get("port", 22)
    mapping = _DEVICE_TYPES.get(driver)
    if not mapping:
        return None
    mode = "telnet" if port == 23 else "ssh"
    return mapping.get(mode)


def _connect(device: dict):
    """Conecta vía netmiko y devuelve la sesión."""
    creds = device.get("credentials", {})
    username = creds.get("username") or "manager"
    password_raw = creds.get("password", "")
    try:
        password = decrypt(password_raw) if password_raw else ""
    except Exception:
        password = password_raw  # ya plano

    dt = _device_type(device)
    if not dt:
        raise ValueError(f"Driver {device.get('driver')} no soportado por netmiko")

    conn = {
        "device_type": dt,
        "host": device["hostname"],
        "username": username,
        "password": password,
        "port": device.get("port", 22),
        "timeout": 20,
        "conn_timeout": 15,
        "banner_timeout": 15,
        "auth_timeout": 15,
        "fast_cli": False,
        "global_delay_factor": 2,  # equipos viejos: más lento
    }
    logger.info("[netmiko] conectando %s a %s:%s (driver=%s)",
                dt, device["hostname"], conn["port"], device.get("driver"))
    return ConnectHandler(**conn)


def execute_command(device: dict, command: str) -> dict:
    """Ejecuta un comando y devuelve {output, error}."""
    conn = None
    try:
        conn = _connect(device)
        output = conn.send_command(command, read_timeout=30)
        return {"output": output, "error": None}
    except Exception as e:
        logger.warning("[netmiko] comando '%s' falló: %s", command, e)
        return {"output": "", "error": str(e)}
    finally:
        if conn:
            try:
                conn.disconnect()
            except Exception:
                pass


def get_facts(device: dict) -> Optional[dict]:
    """Facts básicos vía netmiko (show version)."""
    conn = None
    try:
        conn = _connect(device)
        out = conn.send_command("show version", read_timeout=30)
    except Exception as e:
        logger.warning("[netmiko] facts falló para %s: %s", device["id"], e)
        return None
    finally:
        if conn:
            try:
                conn.disconnect()
            except Exception:
                pass

    if not out:
        return None

    facts = {
        "hostname": device["id"],
        "vendor": "HP" if "procurve" in str(device.get("driver", "")).lower() else None,
        "model": None,
        "os_version": None,
        "serial_number": None,
        "uptime": None,
        "_via": "netmiko",
    }

    # Modelo: "HP J4905A Switch 3400cl-24G" o "ProCurve ..."
    m = re.search(r"(?:HP\s+)?(?:J\d+[A-Z]?\s+)?(Switch\s+\S+|[A-Za-z0-9\-]+\s+\d+[a-z]+)", out)
    if m:
        facts["model"] = m.group(1).strip()
    m = re.search(r"Software revision\s+([\w.]+)", out)
    if m:
        facts["os_version"] = m.group(1)
    m = re.search(r"Serial\s*[Nn]umber\s*[:=]?\s*(\S+)", out)
    if m:
        facts["serial_number"] = m.group(1)
    m = re.search(r"up\s+(\d+\s+\w+(?:\s+\d+\s+\w+)*)", out)
    if m:
        facts["uptime"] = m.group(1)

    logger.info("[netmiko] facts %s → %s", device["id"], facts)
    return facts


def get_interfaces(device: dict) -> Optional[dict]:
    """Interfaces con estado vía netmiko (show interfaces status)."""
    conn = None
    try:
        conn = _connect(device)
        out = conn.send_command("show interfaces status", read_timeout=30)
    except Exception as e:
        logger.warning("[netmiko] interfaces falló para %s: %s", device["id"], e)
        return None
    finally:
        if conn:
            try:
                conn.disconnect()
            except Exception:
                pass

    if not out:
        return None

    interfaces = {}
    # Formato ProCurve: Port  Type  Status  Mode  ...
    for line in out.splitlines():
        m = re.match(r"^(\S+)\s+\S+\s+(Up|Down|Disabled)", line, re.IGNORECASE)
        if m:
            name, state = m.group(1), m.group(2).lower()
            interfaces[name] = {
                "is_up": state == "up",
                "is_enabled": state != "disabled",
                "description": "",
                "speed": None,
                "mac_address": "",
            }
    # Fallback: show interfaces summary
    if not interfaces:
        out2 = None
        try:
            conn = _connect(device)
            out2 = conn.send_command("show interfaces summary", read_timeout=30)
        except Exception:
            pass
        if out2:
            for line in out2.splitlines():
                m = re.match(r"^(\S+)\s+\S+\s+(up|down)", line, re.IGNORECASE)
                if m:
                    interfaces[m.group(1)] = {"is_up": m.group(2).lower() == "up"}

    logger.info("[netmiko] interfaces %s → %d", device["id"], len(interfaces))
    return interfaces or None


def get_resources(device: dict) -> Optional[dict]:
    """Recursos del sistema vía netmiko (show system / memory).

    HP ProCurve: 'show system' da memory y cpu en modelos que lo soportan.
    """
    conn = None
    out = ""
    try:
        conn = _connect(device)
        for cmd in ("show system", "show memory", "show cpu"):
            try:
                res = conn.send_command(cmd, read_timeout=20)
                if res and "Invalid" not in res:
                    out += f"\n--- {cmd} ---\n{res}"
            except Exception:
                continue
    except Exception as e:
        logger.warning("[netmiko] resources falló para %s: %s", device["id"], e)
        return None
    finally:
        if conn:
            try:
                conn.disconnect()
            except Exception:
                pass

    if not out:
        return None

    resources = {"cpu": 0, "memory_total": 0, "memory_used": 0,
                 "hdd_total": 0, "hdd_free": 0}

    # Memoria: "Memory: 12345678 total, 2345678 used, ..."
    m = re.search(r"memory[^\n]*?(\d+)[^\n]*?total", out, re.IGNORECASE)
    m2 = re.search(r"memory[^\n]*?(\d+)[^\n]*?used", out, re.IGNORECASE)
    if m:
        resources["memory_total"] = int(m.group(1))
    if m2:
        resources["memory_used"] = int(m2.group(1))

    logger.info("[netmiko] resources %s → %s", device["id"], resources)
    return resources
