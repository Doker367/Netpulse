"""NetPulse — Netmiko Legacy Service (multi-familia SSH + Telnet).

Motor para equipos legacy / telnet que NAPALM no soporta o no cubre
(HP ProCurve/Aruba, HP/H3C Comware, Cisco Small Business SG, Alcatel
AOS y Nokia SR OS). Se selecciona por protocolo/puerto (telnet = 23),
por driver, o por el override ``netmiko_device_type`` por dispositivo.

Para Alcatel AOS no existe driver telnet en netmiko: se registra aquí
un driver custom ``alcatel_aos_telnet`` siguiendo el patrón oficial
(``CiscoIosTelnet(CiscoIosBase)``); netmiko elige el transporte por el
sufijo ``_telnet`` del ``device_type``.
"""

import logging
import re
import time
from typing import Optional

from netmiko import ConnectHandler

from app.services.concurrency import limited
from app.services.crypto_svc import decrypt

logger = logging.getLogger(__name__)

# ── Registro de drivers telnet no incluidos en netmiko ────────────────────
# netmiko elige el transporte por el sufijo ``_telnet`` del device_type,
# por lo que basta con registrar una subclase del driver SSH equivalente.
# Ver ``BaseConnection.__init__`` (``if "_telnet" in device_type``).


def _register_telnet_driver(name: str, base) -> None:
    """Registra un driver telnet custom en netmiko.

    Además de ``CLASS_MAPPER`` hay que añadirlo a las listas ``platforms``
    y ``telnet_platforms``: ``ConnectHandler`` valida el device_type contra
    esas listas (snapshot tomado en el import de ``ssh_dispatcher``).
    """
    from netmiko.ssh_dispatcher import CLASS_MAPPER
    import importlib

    # Ojo: ``import netmiko.ssh_dispatcher`` devolvería la función homónima
    # reexportada por el paquete, no el módulo. Usamos importlib.
    _sd = importlib.import_module("netmiko.ssh_dispatcher")

    if name in CLASS_MAPPER:
        return

    custom = type(name.title().replace("_", ""), (base,), {"__doc__": "Custom telnet driver"})
    CLASS_MAPPER[name] = custom
    if name not in _sd.platforms:
        _sd.platforms.append(name)
        _sd.platforms.sort()
    if name not in _sd.telnet_platforms:
        _sd.telnet_platforms.append(name)
        _sd.telnet_platforms.sort()
        _sd.telnet_platforms_str = "\n" + "\n".join(_sd.telnet_platforms)
    logger.debug("[netmiko] driver custom '%s' registrado", name)


try:
    from netmiko.alcatel.alcatel_aos_ssh import AlcatelAosSSH

    _register_telnet_driver("alcatel_aos_telnet", AlcatelAosSSH)
except Exception as e:  # pragma: no cover
    logger.warning("[netmiko] No se pudo registrar alcatel_aos_telnet: %s", e)

try:
    from netmiko.enterasys.enterasys_ssh import EnterasysSSH

    _register_telnet_driver("enterasys_telnet", EnterasysSSH)
except Exception as e:  # pragma: no cover
    logger.warning("[netmiko] No se pudo registrar enterasys_telnet: %s", e)


# ── Mapeo driver NetPulse → device_type netmiko (ssh y telnet) ────────────

_DEVICE_TYPES = {
    "ios":      {"ssh": "cisco_ios",           "telnet": "cisco_ios_telnet"},
    "iosxr":    {"ssh": "cisco_xr",            "telnet": "cisco_xr_telnet"},
    "nxos":     {"ssh": "cisco_nxos",          "telnet": "cisco_nxos_telnet"},
    "junos":    {"ssh": "juniper_junos",       "telnet": "juniper_junos_telnet"},
    "eos":      {"ssh": "arista_eos",          "telnet": "arista_eos_telnet"},
    "procurve": {"ssh": "hp_procurve",         "telnet": "hp_procurve_telnet"},
    "comware":  {"ssh": "hp_comware",          "telnet": "hp_comware_telnet"},
    "h3c_comware": {"ssh": "hp_comware",       "telnet": "hp_comware_telnet"},
    "hpe":      {"ssh": "hp_procurve",         "telnet": "hp_procurve_telnet"},
    "aruba":    {"ssh": "aruba_procurve",      "telnet": "aruba_procurve_telnet"},
    "alcatel_aos": {"ssh": "alcatel_aos",      "telnet": "alcatel_aos_telnet"},
    "alcatel_sros": {"ssh": "alcatel_sros",    "telnet": "nokia_sros_telnet"},
    "ros":      {"ssh": "mikrotik_routeros",   "telnet": None},  # MikroTik no usa telnet
    "linux":    {"ssh": "linux",               "telnet": None},
}

# Drivers sin driver NAPALM real → netmiko es el motor primario (no fallback)
NETMIKO_DIRECT_DRIVERS = {"h3c_comware", "alcatel_aos", "alcatel_sros"}

# Drivers legacy que merecen intento netmiko aunque NAPALM falle
LEGACY_DRIVERS = {
    "procurve", "comware", "h3c_comware", "hpe", "aruba",
    "alcatel_aos", "alcatel_sros",
    "ios", "iosxr", "nxos", "junos", "eos",
}


# ── Familia (para comandos y parsing) según device_type netmiko ───────────

def _family(dt: str) -> str:
    dt = (dt or "").lower()
    if dt.startswith("cisco_s") or dt.startswith("cisco_wlc") or dt.startswith("cisco_s200"):
        return "sg"
    if dt.startswith("enterasys") or dt.startswith("extreme"):
        return "enterasys"
    if dt.startswith("cisco") or dt.startswith(("iosxr", "nxos")):
        return "ios"
    if "procurve" in dt or "aruba" in dt:
        return "procurve"
    if "comware" in dt:
        return "comware"
    if "alcatel_aos" in dt:
        return "aos"
    if "sros" in dt or "alcatel" in dt:
        return "sros"
    if "arista" in dt:
        return "eos"
    return "ios"


# Comandos de diagnóstico por familia (varios candidatos por orden)
_VERSION_CMDS = {
    "ios": "show version",
    "sg": "show version",
    "procurve": "show version",
    "comware": "display version",
    "aos": "show system",
    "sros": "show version",
    "eos": "show version",
    "enterasys": "show version",
}

_RUNNING_CMDS = {
    "ios": ["show running-config"],
    "sg": ["show running-config"],
    "procurve": ["show running-config", "show config"],
    "comware": ["display current-configuration"],
    "aos": ["show configuration snapshot", "show running-configuration"],
    "sros": ["admin display-config", "show running-config"],
    "eos": ["show running-config"],
    "enterasys": ["show config"],
}

_INTERFACES_CMDS = {
    "ios": ["show interfaces status", "show ip interface brief", "show interfaces"],
    "sg": ["show interfaces status", "show port status"],
    "procurve": ["show interfaces brief", "show interfaces status", "show interfaces summary"],
    "comware": ["display interface brief", "display interface"],
    "aos": ["show interfaces status", "show interfaces summary"],
    "sros": ["show port", "show interfaces"],
    "eos": ["show interfaces status"],
    "enterasys": ["show port status", "show interfaces"],
}


# ── Detección de transporte / driver ──────────────────────────────────────

def _cli_channel(device: dict) -> Optional[str]:
    """Devuelve el canal CLI del dispositivo si lo declara (modo dual SNMP+CLI).

    Un equipo puede estar monitoreado por SNMP (protocol='snmp') pero tener
    además acceso CLI por telnet/ssh (campo ``cli_transport``). En ese caso
    las operaciones de red (interfaces, config, comandos) viajan por netmiko
    usando este canal y su puerto (``cli_port``).
    """
    transport = str(device.get("cli_transport") or "").lower()
    if transport not in ("ssh", "telnet"):
        return None
    return transport


def _effective_port(device: dict) -> int:
    """Puerto efectivo para la conexión netmiko (CLI dual o el del equipo)."""
    transport = _cli_channel(device)
    if transport:
        return int(device.get("cli_port") or (23 if transport == "telnet" else 22))
    try:
        return int(device.get("port", 22) or 22)
    except (TypeError, ValueError):
        return 22


# ── Motor telnet ligero para ProCurve/Aruba sin login (noauth) ────────────

_NOAUTH_ANSI = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]|\x1b[=>]|[\x07]")


def is_noauth_telnet(device: dict) -> bool:
    """True si el equipo usa el motor ligero telnet (banner → shell directo).

    Aplica a switches legacy tipo HP ProCurve/Aruba cuya sesión telnet no
    pide usuario/contraseña (solo 'Press any key to continue').
    """
    return bool(device.get("noauth_telnet")) and is_telnet_device(device)


def _noauth_clean(data: bytes) -> bytes:
    return _NOAUTH_ANSI.sub(b"", data)


def _noauth_family(device: dict) -> Optional[str]:
    """Familia para el motor ligero, o None si no aplica."""
    if not is_noauth_telnet(device):
        return None
    driver = str(device.get("driver") or "").lower()
    if driver in ("procurve", "hpe", "aruba"):
        return "procurve"
    return None


def _noauth_login(device: dict, timeout: float = 12.0):
    """Abre sesión telnet y deja el shell listo. Devuelve (socket, prompt_bytes)."""
    import socket as _socket
    s = _socket.create_connection(
        (device["hostname"], _effective_port(device)), timeout=8
    )
    s.settimeout(0.25)
    buf = b""
    t0 = time.monotonic()
    while b"Press any key to continue" not in buf and time.monotonic() - t0 < timeout:
        try:
            d = s.recv(4096)
            if d:
                buf += d
        except _socket.timeout:
            continue
    s.sendall(b"\r")
    buf = b""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            d = s.recv(4096)
            if d:
                buf += d
                if b"#" in buf:
                    break
        except _socket.timeout:
            continue
    text = _noauth_clean(buf)
    idx = max(text.rfind(b"\n"), text.rfind(b"\r"))
    base = text[idx + 1:].strip() if idx >= 0 else text.strip()
    if not base:
        base = b"#"
    return s, base


def _noauth_exec(device: dict, command: str, read_timeout: float = 25.0) -> str:
    """Ejecuta un comando por telnet ligero y devuelve la salida limpia."""
    import socket as _socket
    s = None
    try:
        s, base = _noauth_login(device)
        # Desactivar paginación (equivalente a netmiko disable_paging)
        s.sendall(b"no page\r")
        t0 = time.monotonic()
        while time.monotonic() - t0 < 2.0:
            try:
                d = s.recv(4096)
                if d and b"# " in d or (d and base in d):
                    break
            except _socket.timeout:
                break

        s.sendall(command.encode("utf-8", "replace") + b"\r")
        buf = b""
        t0 = time.monotonic()
        last = time.monotonic()
        while time.monotonic() - t0 < read_timeout:
            try:
                d = s.recv(4096)
                if d:
                    buf += d
                    last = time.monotonic()
            except _socket.timeout:
                pass
            t = _noauth_clean(buf)
            # Avanzar paginación "-- MORE --"
            if b"MORE" in t[-100:] and time.monotonic() - last > 0.15:
                s.sendall(b" ")
                last = time.monotonic()
            if base and (t.endswith(base) or base in t[-len(base) - 60:]):
                if time.monotonic() - last > 0.25:
                    break
        clean = _noauth_clean(buf)
        # Quitar el eco del comando (hasta el primer salto de línea)
        cut = clean.find(b"\n\r")
        if cut < 0:
            cut = clean.find(b"\r\n")
        if cut < 0:
            cut = clean.find(b"\n")
        out = clean[cut + 2:] if cut >= 0 else clean
        if base:
            i = out.rfind(base)
            if i >= 0:
                out = out[:i]
        return out.decode("ascii", "replace").strip()
    finally:
        if s:
            try:
                s.close()
            except Exception:
                pass


def is_telnet_device(device: dict) -> bool:
    """True si el dispositivo usa telnet (protocolo explícito o puerto 23)."""
    transport = _cli_channel(device)
    if transport == "telnet":
        return True
    if transport == "ssh":
        return False
    proto = str(device.get("protocol") or "").lower()
    if proto == "telnet":
        return True
    if proto == "snmp":
        return False
    port = int(device.get("port", 22) or 22)
    if str(device.get("netmiko_device_type") or "").lower().endswith("_telnet"):
        return True
    return port == 23


def is_legacy_device(device: dict) -> bool:
    """True si el dispositivo debe usar el motor netmiko."""
    if device.get("netmiko_device_type"):
        return True
    if _cli_channel(device):
        return True
    if is_telnet_device(device):
        return True
    driver = str(device.get("driver", "")).lower()
    return driver in LEGACY_DRIVERS


def _device_type(device: dict) -> Optional[str]:
    """Resuelve el device_type netmiko (override > mapa driver)."""
    telnet = is_telnet_device(device)
    override = str(device.get("netmiko_device_type") or "").strip().lower()

    if override:
        dt = override
        # Asegurar transporte telnet si el equipo es telnet
        if telnet and not dt.endswith("_telnet"):
            twin = f"{dt}_telnet"
            try:
                from netmiko.ssh_dispatcher import CLASS_MAPPER
                if twin in CLASS_MAPPER:
                    dt = twin
            except Exception:
                pass
        return dt or None

    driver = str(device.get("driver", "")).lower()
    mapping = _DEVICE_TYPES.get(driver)
    if not mapping:
        return None
    return mapping.get("telnet" if telnet else "ssh")


def _family_device_type(device: dict) -> Optional[str]:
    dt = _device_type(device)
    if not dt:
        return None
    return _family(dt)


def _connect(device: dict):
    """Conecta vía netmiko y devuelve la sesión (SSH o telnet según el equipo)."""
    creds = device.get("credentials", {})
    username = creds.get("username") or "manager"
    password_raw = creds.get("password", "")
    try:
        password = decrypt(password_raw) if password_raw else ""
    except Exception:
        password = password_raw  # ya plano

    dt = _device_type(device)
    if not dt:
        raise ValueError(
            f"Driver {device.get('driver')} no soportado por netmiko "
            f"(usa 'netmiko_device_type' para forzar uno, ej. cisco_s300)"
        )

    conn_params = {
        "device_type": dt,
        "host": device["hostname"],
        "username": username,
        "password": password,
        "port": _effective_port(device),
        "timeout": 12,
        "conn_timeout": 8,
        "banner_timeout": 8,
        "auth_timeout": 8,
        "fast_cli": False,
        "global_delay_factor": 1,  # equipos viejos: ritmo normal
    }

    # Enable secret (Cisco/equipos que lo piden)
    enable_raw = device.get("enable_password") or ""
    try:
        secret = decrypt(enable_raw) if enable_raw else None
    except Exception:
        secret = enable_raw or None
    if secret:
        conn_params["secret"] = secret

    logger.info("[netmiko] conectando %s a %s:%s (driver=%s, protocolo=%s)",
                dt, device["hostname"], conn_params["port"],
                device.get("driver"), "telnet" if is_telnet_device(device) else "ssh")

    # HP ProCurve "seem to fail more on connection than they should" (netmiko);
    # reintenta logins intermitentes sin dejar sockets colgados.
    import time as _time
    last_err: Optional[Exception] = None
    conn = None
    for attempt in range(3):
        try:
            conn = ConnectHandler(**conn_params)
            break
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if attempt < 2:
                logger.warning("[netmiko] login/connect a %s falló (intento %d): %s — reintentando",
                               device["hostname"], attempt + 1, exc)
                _time.sleep(1.0)
    if conn is None:
        raise last_err if last_err else ConnectionError(f"No se pudo conectar a {device['hostname']}")

    # Entrar a enable mode si el equipo lo requiere (Cisco, SG, etc.)
    if secret and hasattr(conn, "check_enable_mode"):
        try:
            if not conn.check_enable_mode():
                conn.enable()
                logger.info("[netmiko] enable mode activado en %s", device["id"])
        except Exception as e:
            logger.warning("[netmiko] no se pudo activar enable en %s: %s", device["id"], e)
    return conn


def _run(device: dict, command: str, read_timeout: int = 30) -> str:
    """Abre sesión, ejecuta un comando y devuelve su salida."""
    conn = None
    try:
        conn = _connect(device)
        return conn.send_command(command, read_timeout=read_timeout)
    finally:
        if conn:
            try:
                conn.disconnect()
            except Exception:
                pass


@limited
def execute_command(device: dict, command: str) -> dict:
    """Ejecuta un comando y devuelve {output, error}."""
    # Motor ligero (ProCurve/Aruba sin login por telnet)
    if _noauth_family(device):
        try:
            output = _noauth_exec(device, command, read_timeout=30)
            return {"output": output, "error": None}
        except Exception as e:
            logger.warning("[netmiko] noauth comando '%s' falló en %s: %s",
                           command, device.get("id"), e)
            return {"output": "", "error": str(e)}

    conn = None
    try:
        conn = _connect(device)
        output = conn.send_command(command, read_timeout=30)
        return {"output": output, "error": None}
    except Exception as e:
        logger.warning("[netmiko] comando '%s' falló en %s: %s",
                       command, device.get("id"), e)
        return {"output": "", "error": str(e)}
    finally:
        if conn:
            try:
                conn.disconnect()
            except Exception:
                pass


# ── Running config ────────────────────────────────────────────────────────

@limited
def get_running_config(device: dict) -> Optional[str]:
    """Obtiene la running-config (o equivalente) por familia."""
    # Motor ligero (ProCurve/Aruba sin login por telnet)
    fam = _noauth_family(device)
    if fam:
        for cmd in _RUNNING_CMDS.get(fam, ["show running-config"]):
            try:
                out = _noauth_exec(device, cmd, read_timeout=40)
            except Exception as e:
                logger.warning("[netmiko] noauth running-config falló para %s: %s",
                               device["id"], e)
                continue
            if out and "Invalid" not in out and "% Unknown" not in out:
                logger.info("[netmiko] running-config %s via noauth telnet (%d bytes)",
                            device["id"], len(out))
                return out
        return None

    fam = _family_device_type(device)
    if not fam:
        return None
    for cmd in _RUNNING_CMDS.get(fam, ["show running-config"]):
        out = _run(device, cmd, read_timeout=40)
        if out and "Invalid" not in out and "% Unknown" not in out:
            return out
    return None


# ── Facts ─────────────────────────────────────────────────────────────────

@limited
def get_facts(device: dict) -> Optional[dict]:
    """Facts básicos vía netmiko (show version / display version / show system)."""
    fam = _family_device_type(device)
    if not fam:
        return None
    cmd = _VERSION_CMDS.get(fam, "show version")
    conn = None
    try:
        conn = _connect(device)
        out = conn.send_command(cmd, read_timeout=30)
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

    vendor = {
        "procurve": "HP ProCurve",
        "comware": "HP/H3C Comware",
        "aos": "Alcatel-Lucent Enterprise (AOS)",
        "sros": "Nokia/Alcatel-Lucent SR OS",
        "sg": "Cisco Small Business",
        "ios": "Cisco",
        "eos": "Arista",
    }.get(fam, fam.upper())

    facts = {
        "hostname": device["id"],
        "vendor": vendor,
        "model": None,
        "os_version": None,
        "serial_number": None,
        "uptime": None,
        "_via": "netmiko",
        "_family": fam,
    }

    if fam in ("procurve", "comware"):
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
        return facts

    # Cisco IOS / SG / SROS / EOS / AOS (estilo IOS o variantes)
    m = re.search(r"(\S+)\s+uptime", out) or re.search(r"hostname\s+(\S+)", out)
    if m:
        facts["hostname"] = m.group(1).strip().rstrip("#>")
    m = re.search(r"Software Version[:\s]+([\w.()\-]+)", out) \
        or re.search(r"Version\s+([\w.()\-]+)", out) \
        or re.search(r"(?:Operating System|Software)\s+(?:Version|version)?\s*[:=]?\s*([\w.\-]+)", out)
    if m:
        facts["os_version"] = m.group(1)
    m = re.search(r"([\w\-]+\s+[A-Z]{2,}\s+[\d.\w]+)", out) or re.search(r"Model(?: Number)?\s*[:=]?\s*(\S+)", out)
    if m:
        facts["model"] = m.group(1).strip()
    m = re.search(r"Serial\s*(?:Number|#)?\s*[:=]?\s*(\S+)", out)
    if m:
        facts["serial_number"] = m.group(1)
    m = re.search(r"up\s+(\d+\s+\w+(?:\s+\d+\s+\w+)*)", out, re.IGNORECASE)
    if m:
        facts["uptime"] = m.group(1)

    logger.info("[netmiko] facts %s → %s", device["id"], facts)
    return facts


# ── Interfaces ────────────────────────────────────────────────────────────

@limited
def get_interfaces(device: dict) -> Optional[dict]:
    """Interfaces con estado vía netmiko, por familia."""
    # Motor ligero (ProCurve/Aruba sin login por telnet)
    fam = _noauth_family(device)
    if fam:
        for cmd in _INTERFACES_CMDS.get(fam, ["show interfaces brief"]):
            try:
                out = _noauth_exec(device, cmd, read_timeout=30)
            except Exception as e:
                logger.warning("[netmiko] noauth interfaces falló para %s (%s): %s",
                               device["id"], cmd, e)
                continue
            parsed = _parse_interfaces(out, fam)
            if parsed:
                logger.info("[netmiko] interfaces %s (%s) → %d vía noauth '%s'",
                            device["id"], fam, len(parsed), cmd)
                return parsed
        return None

    fam = _family_device_type(device)
    if not fam:
        return None
    for cmd in _INTERFACES_CMDS.get(fam, ["show interfaces status"]):
        out = _run(device, cmd, read_timeout=30)
        parsed = _parse_interfaces(out, fam)
        if parsed:
            logger.info("[netmiko] interfaces %s (%s) → %d vía '%s'",
                        device["id"], fam, len(parsed), cmd)
            return parsed
    return None


def _parse_interfaces(out: str, fam: str) -> dict:
    """Parsea la salida de estado de interfaces según la familia."""
    interfaces: dict[str, dict] = {}
    if not out:
        return interfaces

    if fam == "comware":
        # display interface brief:  GE1/0/1   UP     ...
        for line in out.splitlines():
            m = re.match(r"^\s*(GE|GigabitEthernet|XGE|Ten-GigabitEthernet|Eth|Ethernet)\S*\s+(UP|DOWN)\b", line, re.IGNORECASE)
            if m:
                name, state = m.group(1), m.group(2).lower()
                interfaces[name] = {
                    "is_up": state == "up",
                    "is_enabled": state != "down",
                    "description": "",
                    "speed": None,
                    "mac_address": "",
                }
        return interfaces

    if fam == "procurve":
        # Formato ProCurve/Aruba "show interfaces brief|status":
        #   Port  Type      | Intrusion ... Enabled Status Mode ...
        #   1     100/1000T | No        Yes     Up     1000FDx   MDIX ...
        for line in out.splitlines():
            m = re.match(
                r"^\s*(\d+)\s+\S+\s+\|\s+(?:No|Yes)\s+(Yes|No)\s+(Up|Down|Disabled)(?:\s+(\S+))?",
                line, re.IGNORECASE,
            )
            if not m:
                # Sin columna separador "|" (algunos firmware)
                m = re.match(
                    r"^\s*(\d+)\s+\S+\s+(Yes|No)\s+(Up|Down|Disabled)(?:\s+(\S+))?",
                    line, re.IGNORECASE,
                )
            if m:
                name = m.group(1)
                state = m.group(3).lower()
                enabled = m.group(2).lower() == "yes"
                speed = None
                mode = m.group(4)
                if mode:
                    sm = re.search(r"(\d{2,4})(?:FD|HD)x?", mode, re.IGNORECASE)
                    if sm:
                        speed = int(sm.group(1))
                interfaces[f"Port {name}"] = {
                    "is_up": state == "up",
                    "is_enabled": enabled and state != "disabled",
                    "description": "",
                    "speed": speed if state == "up" else None,
                    "mac_address": "",
                }
        return interfaces

    if fam == "enterasys":
        # Enterasys/Extreme "show port status":
        #   fe.1.1   Alias   Up   Up   100.0M  full  BaseT RJ45
        for line in out.splitlines():
            m = re.match(
                r"^\s*((?:fe|ge|tg|gi|xg|eth)\S*?)\s+.*?(Up|Down|Disabled)\s+(Up|Down|Disabled)(?:\s+(\S+))?",
                line, re.IGNORECASE,
            )
            if not m:
                continue
            name, oper, admin, speed_raw = m.group(1), m.group(2).lower(), m.group(3).lower(), m.group(4)
            speed = None
            if speed_raw:
                sm = re.match(r"([\d.]+)\s*([MG]?)", speed_raw, re.IGNORECASE)
                if sm:
                    val = float(sm.group(1))
                    unit = sm.group(2).upper()
                    speed = int(val * 1000) if unit == "G" else int(val)
            interfaces[name] = {
                "is_up": oper == "up",
                "is_enabled": admin == "up" and oper != "disabled",
                "description": "",
                "speed": speed if oper == "up" else None,
                "mac_address": "",
            }
        return interfaces

    # IOS / SG / EOS / AOS / SROS (formato "show interfaces status" similar)
    for line in out.splitlines():
        # Port / Name / Status (connected|notconnect|disabled|up|down)
        m = re.match(r"^(\S+)\s+\S*\s*(connected|notconnect|disabled|up|down)\b", line, re.IGNORECASE)
        if not m:
            m = re.match(r"^(\S+)\s+(up|down|administratively down)\b", line, re.IGNORECASE)
        if m:
            name, state = m.group(1), m.group(2).lower()
            interfaces[name] = {
                "is_up": state == "up" or state == "connected",
                "is_enabled": state not in ("disabled", "administratively down"),
                "description": "",
                "speed": None,
                "mac_address": "",
            }
    return interfaces


# ── Recursos del sistema ─────────────────────────────────────────────────

@limited
def get_resources(device: dict) -> Optional[dict]:
    """Recursos del sistema vía netmiko (show system / memory)."""
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

    m = re.search(r"memory[^\n]*?(\d+)[^\n]*?total", out, re.IGNORECASE)
    m2 = re.search(r"memory[^\n]*?(\d+)[^\n]*?used", out, re.IGNORECASE)
    if m:
        resources["memory_total"] = int(m.group(1))
    if m2:
        resources["memory_used"] = int(m2.group(1))

    logger.info("[netmiko] resources %s → %s", device["id"], resources)
    return resources


# ── Ping ──────────────────────────────────────────────────────────────────

_PING_CMDS = {
    "ios":     "ping {ip} repeat {count} timeout 2",
    "sg":      "ping {ip}",
    "procurve": "ping {ip}",
    "comware": "ping -c {count} {ip}",
    "aos":     "ping {ip} count {count}",
    "sros":    "ping {ip} count {count}",
    "eos":     "ping {ip} repeat {count}",
}


@limited
def ping(device: dict, target: str, count: int = 3) -> dict:
    """Ping desde el dispositivo vía CLI (por familia).

    Devuelve estructura similar a NAPALM:
        {"success": {"probes_sent", "packet_loss", "rtt_min/avg/max"}}
    o, si no se puede parsear, {"raw": out, "success": bool}.
    """
    fam = _family_device_type(device)
    if not fam:
        return {"success": False, "error": f"Sin familia netmiko para {device.get('driver')}"}

    cmd = _PING_CMDS.get(fam, "ping {ip}").format(ip=target, count=count)
    # Fallback universal si la sintaxis de la familia falla
    attempts = [cmd]
    if cmd != f"ping {target}":
        attempts.append(f"ping {target}")

    last_out = ""
    for c in attempts:
        out = _run(device, c, read_timeout=40)
        if out and not out.lower().startswith("% "):
            last_out = out
            parsed = _parse_ping(out)
            if parsed is not None:
                return {"success": parsed}
            # Salida sin estructura clara pero con respuesta: guardar y seguir
            if _looks_reachable(out):
                return {"success": {"probes_sent": count, "packet_loss": 0,
                                    "rtt_min": 0.0, "rtt_avg": 0.0, "rtt_max": 0.0},
                        "raw": out}
        else:
            last_out = out or last_out

    return {"success": {"probes_sent": count, "packet_loss": 100,
                        "rtt_min": 0.0, "rtt_avg": 0.0, "rtt_max": 0.0},
            "raw": last_out or "sin respuesta del comando ping"}


def _looks_reachable(out: str) -> bool:
    low = out.lower()
    return ("reply from" in low or "bytes from" in low or "icmp_seq" in low
            or "success rate is 100" in low or "0% packet loss" in low
            or "0% (0/5)" in low or "packet loss is 0" in low)


def _parse_ping(out: str) -> Optional[dict]:
    """Parsea la salida de ping. Retorna dict o None si no reconoce formato."""
    low = out.lower()
    rtt_min = rtt_avg = rtt_max = 0.0
    probes_sent = probes_received = None
    loss = None

    # RTT: Cisco/ProCurve/IOS-style y linux-style
    m = re.search(r"round-trip min/avg/max\s*=\s*([\d.]+)/([\d.]+)/([\d.]+)", out, re.IGNORECASE)
    if m:
        rtt_min, rtt_avg, rtt_max = float(m.group(1)), float(m.group(2)), float(m.group(3))
    else:
        m = re.search(r"rtt min/avg/max/mdev\s*=\s*([\d.]+)/([\d.]+)/([\d.]+)", low)
        if m:
            rtt_min, rtt_avg, rtt_max = float(m.group(1)), float(m.group(2)), float(m.group(3))
        else:
            m = re.search(r"minimum\s*=\s*([\d.]+).*?average\s*=\s*([\d.]+).*?maximum\s*=\s*([\d.]+)", low, re.DOTALL)
            if m:
                rtt_min, rtt_avg, rtt_max = float(m.group(1)), float(m.group(2)), float(m.group(3))

    # Paquetes enviados/recibidos / pérdida
    m = re.search(r"success rate is (\d+) percent \((\d+)/(\d+)\)", low)
    if m:
        loss = 100 - int(m.group(1))
        probes_sent, probes_received = int(m.group(2)), int(m.group(3))
    else:
        m = re.search(r"success rate is (\d+) percent", low)
        if m:
            loss = 100 - int(m.group(1))
    m = re.search(r"(\d+) packets? transmitted[^\d]*(\d+) packets? received", low)
    if m:
        probes_sent, probes_received = int(m.group(1)), int(m.group(2))
    else:
        # Formato linux/procurve: "5 packets transmitted, 5 received"
        m = re.search(r"(\d+) packets? transmitted[^\d]*(\d+) received", low)
        if m:
            probes_sent, probes_received = int(m.group(1)), int(m.group(2))
        else:
            m = re.search(r"(\d+)\s+packet\(s\) transmitted[^\d]*(\d+)\s+packet\(s\) received", low)
            if m:
                probes_sent, probes_received = int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d+)% packet loss", low) \
        or re.search(r"packet loss(?: is| rate)?[:\s]*(\d+)%", low) \
        or re.search(r"(\d+)% loss", low)
    if m:
        loss = int(m.group(1))
    elif probes_sent and probes_received is not None:
        loss = round((probes_sent - probes_received) / probes_sent * 100) if probes_sent else 100

    replies = len(re.findall(r"(reply from|bytes from|icmp_seq)", low))

    if probes_sent is None and loss is None and not replies:
        return None

    if probes_received is None:
        probes_received = replies if replies else (0 if loss == 100 else probes_sent or 0)
    if loss is None:
        loss = round((probes_sent - probes_received) / probes_sent * 100) if probes_sent else 100

    return {
        "probes_sent": probes_sent or 0,
        "packet_loss": loss,
        "rtt_min": rtt_min,
        "rtt_avg": rtt_avg,
        "rtt_max": rtt_max,
    }


# ── Envío de configuración (deploy por telnet/legacy) ────────────────────

@limited
def send_config_lines(device: dict, config: str) -> dict:
    """Aplica líneas de configuración vía send_config_set (best-effort).

    Devuelve {"ok": bool, "output": str, "error": str|None}.
    """
    conn = None
    try:
        conn = _connect(device)
        lines = [line for line in config.splitlines()
                 if line.strip() and not line.strip().startswith("!")]
        if not lines:
            return {"ok": True, "output": "", "error": None}
        output = conn.send_config_set(lines, read_timeout=40)
        # Persistir cambios (write memory / save) según driver
        try:
            conn.save_config()
        except Exception as e:
            logger.warning("[netmiko] save_config no disponible (%s): %s", device["id"], e)
        return {"ok": True, "output": output, "error": None}
    except Exception as e:
        logger.error("[netmiko] send_config falló en %s: %s", device.get("id"), e)
        return {"ok": False, "output": "", "error": str(e)}
    finally:
        if conn:
            try:
                conn.disconnect()
            except Exception:
                pass
