"""NetPulse — Personas de dispositivos señuelo.

Define credenciales falsas, banners y comportamientos por rol. Son el
"guión" que siguen los listeners nativos: qué credenciales aceptar
(aunque en SSH se registra todo intento), qué banner devolver y qué
respuestas simular a comandos.

IMPORTANTE: todas las credenciales son de mentira. Nunca deben coincidir
con credenciales reales del entorno.
"""

from app.models.deception import DeceptionRole

# Credenciales débiles por defecto típicas de cada tipo de dispositivo.
# Se usan para decidir si un login se considera "exitoso" (en señuelos
# normalmente aceptamos igual para capturar el resto de la sesión).
DEFAULT_CREDENTIALS: dict[DeceptionRole, list[tuple[str, str]]] = {
    DeceptionRole.PC: [("admin", "admin"), ("user", "password"), ("administrador", "1234")],
    DeceptionRole.SERVER: [("root", "root"), ("root", "toor"), ("admin", "admin")],
    DeceptionRole.CAMERA: [("admin", "admin"), ("admin", "12345"), ("admin", "")],
    DeceptionRole.SWITCH: [("admin", "admin"), ("cisco", "cisco"), ("manager", "manager")],
    DeceptionRole.FIREWALL: [("admin", "admin"), ("admin", "Fortinet"), ("root", "fortinet")],
    DeceptionRole.ROUTER: [("admin", ""), ("admin", "admin"), ("cisco", "cisco")],
}

# Banners de identificación por rol (fallback si el servicio no define uno).
DEFAULT_BANNER: dict[DeceptionRole, str] = {
    DeceptionRole.PC: "OpenSSH_8.9",
    DeceptionRole.SERVER: "OpenSSH_8.2p1 Ubuntu-4ubuntu0.5",
    DeceptionRole.CAMERA: "Hikvision-Webs",
    DeceptionRole.SWITCH: "Cisco IOS SSH 1.25",
    DeceptionRole.FIREWALL: "FortiGate-60F",
    DeceptionRole.ROUTER: "MikroTik RouterOS 7.11",
}

# Prompt falso que se muestra tras un login "exitoso" (shell/CLI).
SHELL_PROMPT: dict[DeceptionRole, str] = {
    DeceptionRole.PC: "$ ",
    DeceptionRole.SERVER: "# ",
    DeceptionRole.CAMERA: "> ",
    DeceptionRole.SWITCH: "sw-access-01# ",
    DeceptionRole.FIREWALL: "fw-edge-01 # ",
    DeceptionRole.ROUTER: "[admin@router-core-01] > ",
}

# Respuestas simuladas a comandos frecuentes de atacantes (best-effort).
# Si el comando no está, se devuelve una respuesta genérica vacía.
COMMAND_RESPONSES: dict[str, str] = {
    "id": "uid=0(root) gid=0(root) groups=0(root)",
    "whoami": "root",
    "uname -a": "Linux decoy 5.15.0-91-generic #101-Ubuntu SMP x86_64 GNU/Linux",
    "hostname": "decoy",
    "pwd": "/root",
    "ls": "bin  boot  dev  etc  home  root  tmp  usr  var",
    "ls -la": "total 8\ndrwx------ 2 root root 4096 .\ndrwxr-xr-x 1 root root 4096 ..",
    "cat /etc/passwd": "root:x:0:0:root:/root:/bin/bash\nwww-data:x:33:33:www-data:/var/www:/usr/sbin/nologin",
    "ifconfig": "eth0: flags=4163<UP,BROADCAST,RUNNING,MULTICAST>  mtu 1500",
    "ip addr": "1: lo: <LOOPBACK,UP> mtu 65536\n2: eth0: <BROADCAST,MULTICAST,UP> mtu 1500",
    "show version": "Cisco IOS Software, Version 15.2(4)E",
    "enable": "",
}


def credentials_for(role: DeceptionRole | str) -> list[tuple[str, str]]:
    """Credenciales señuelo para un rol (por defecto, las de PC)."""
    try:
        return DEFAULT_CREDENTIALS[DeceptionRole(role)]
    except (ValueError, KeyError):
        return DEFAULT_CREDENTIALS[DeceptionRole.PC]


def banner_for(role: DeceptionRole | str, override: str | None = None) -> str:
    """Banner para un rol, con override explícito si se indica."""
    if override:
        return override
    try:
        return DEFAULT_BANNER[DeceptionRole(role)]
    except (ValueError, KeyError):
        return DEFAULT_BANNER[DeceptionRole.PC]


def prompt_for(role: DeceptionRole | str) -> str:
    """Prompt de shell/CLI falso para un rol."""
    try:
        return SHELL_PROMPT[DeceptionRole(role)]
    except (ValueError, KeyError):
        return "$ "


def response_for(command: str) -> str:
    """Respuesta simulada a un comando (compat; sin vendor)."""
    return shell_response(None, None, command)


# ── Respuestas contextuales por familia de dispositivo ───────

_FAMILY_COMMANDS: dict[str, dict[str, str]] = {
    "linux": {
        "id": "uid=0(root) gid=0(root) groups=0(root)",
        "whoami": "root",
        "uname -a": "Linux decoy 5.15.0-91-generic #101-Ubuntu SMP x86_64 GNU/Linux",
        "hostname": "decoy",
        "pwd": "/root",
        "ls": "bin  boot  dev  etc  home  root  tmp  usr  var",
        "ls -la": "total 8\ndrwx------ 2 root root 4096 .\ndrwxr-xr-x 1 root root 4096 ..",
        "cat /etc/passwd": "root:x:0:0:root:/root:/bin/bash\nwww-data:x:33:33:www-data:/var/www:/usr/sbin/nologin",
        "ifconfig": "eth0: flags=4163<UP,BROADCAST,RUNNING,MULTICAST>  mtu 1500",
        "ip addr": "1: lo: <LOOPBACK,UP> mtu 65536\n2: eth0: <BROADCAST,MULTICAST,UP> mtu 1500",
    },
    "cisco": {
        "enable": "",
        "show version": "Cisco IOS Software, C2960 Software (C2960-LANBASEK9-M), "
                        "Version 15.2(4)E, RELEASE SOFTWARE (fc3)\r\n"
                        "sw-access-01 uptime is 3 weeks, 2 days",
        "show running-config": "Building configuration...\r\n\r\nCurrent configuration : 2194 bytes\r\n"
                               "!\r\nhostname sw-access-01\r\n!\r\n"
                               "interface Vlan1\r\n ip address 10.40.0.11 255.255.255.0\r\n!\r\nend",
        "show interfaces": "GigabitEthernet0/1 is up, line protocol is up\r\n"
                           "GigabitEthernet0/2 is down, line protocol is down",
        "show ip interface brief": "Interface              IP-Address      OK? Method Status\r\n"
                                   "Vlan1                  10.40.0.11      YES NVRAM  up",
        "configure terminal": "",
    },
    "mikrotik": {
        "show version": None,
        "/system resource print": "             uptime: 3w2d6h\r\n"
                                  "            version: 7.11 (stable)\r\n"
                                  "        free-memory: 118.9MiB\r\n"
                                  "       total-memory: 256.0MiB\r\n"
                                  "                cpu: MIPS 1004Kc",
        "/ip address print": "Flags: X - disabled\r\n"
                             " #   ADDRESS            INTERFACE\r\n"
                             " 0   10.40.0.2/24       ether1",
        "/system identity print": "name: router-core-01",
    },
    "fortios": {
        "get system status": "Version: FortiGate-60F v7.4.1\r\n"
                             "Serial-Number: FG60F0000000001\r\n"
                             "Hostname: fw-edge-01",
        "get system interface": "== [ port1 ]\r\n  mode: dhcp\r\n  ip: 192.168.1.99 255.255.255.0",
        "show": "#config-version=FG60F-7.4.1\r\nconfig system global\r\n set hostname \"fw-edge-01\"\r\nend",
        "diagnose sys top": "Run Time:  0 days, 3 hours\r\n  0  0 2.0 1.0",
    },
    "camera": {},
    "generic": {},
}

_FAMILY_ERRORS: dict[str, str] = {
    "linux": "bash: {cmd}: command not found",
    "cisco": "% Invalid input detected at '^' marker.",
    "mikrotik": "bad command name {cmd} (line 1 column 1)",
    "fortios": "Unknown action 0",
    "camera": "Invalid input",
    "generic": "% Invalid command",
}


def family(role, vendor=None) -> str:
    """Familia de dispositivo para elegir respuestas/banners coherentes."""
    v = (vendor or "").lower()
    r = str(role.value if hasattr(role, "value") else role or "").lower()
    if "cisco" in v or "ios" in v or r == "switch":
        return "cisco"
    if "mikrotik" in v or "routeros" in v:
        return "mikrotik"
    if "fortinet" in v or "forti" in v or r == "firewall":
        return "fortios"
    if "hikvision" in v or "dahua" in v or "axis" in v or r == "camera":
        return "camera"
    if "linux" in v or "ubuntu" in v or "debian" in v or r in ("pc", "server"):
        return "linux"
    return "generic"


def shell_response(role, vendor, command: str) -> str:
    """Respuesta de shell/CLI coherente con la familia; error si es desconocido."""
    cmd = (command or "").strip()
    if not cmd:
        return ""
    fam = family(role, vendor)
    table = _FAMILY_COMMANDS.get(fam, {})
    if cmd in table and table[cmd] is not None:
        return table[cmd]
    # Coincidencias por prefijo (comandos con argumentos)
    if fam == "linux":
        if cmd.startswith("cat "):
            return f"cat: {cmd[4:]}: No such file or directory"
        if cmd.startswith("cd ") or cmd in ("cd", "exit", "logout"):
            return ""
    if fam == "cisco" and cmd.startswith("show "):
        return "% Ambiguous command"
    if cmd in ("exit", "quit", "logout"):
        return ""
    return _FAMILY_ERRORS.get(fam, "% Invalid command").format(cmd=cmd)


# Identidad SNMP por familia (sysDescr / sysName / sysObjectID)
_SNMP_IDENTITY: dict[str, dict[str, str]] = {
    "linux": {
        "sysdescr": "Linux files01 5.15.0-91-generic #101-Ubuntu SMP x86_64",
        "sysobjectid": "1.3.6.1.4.1.8072.3.2.10",
    },
    "cisco": {
        "sysdescr": "Cisco IOS Software, C2960 Software (C2960-LANBASEK9-M), Version 15.2(4)E",
        "sysobjectid": "1.3.6.1.4.1.9.1.716",
    },
    "mikrotik": {
        "sysdescr": "RouterOS RB750Gr3",
        "sysobjectid": "1.3.6.1.4.1.14988.1",
    },
    "fortios": {
        "sysdescr": "FortiGate-60F v7.4.1 build2463",
        "sysobjectid": "1.3.6.1.4.1.12356.101.1.60",
    },
    "camera": {
        "sysdescr": "Hikvision IP Camera",
        "sysobjectid": "1.3.6.1.4.1.39165.1.5",
    },
    "generic": {
        "sysdescr": "NetPulse decoy device",
        "sysobjectid": "1.3.6.1.4.1.0",
    },
}


def snmp_identity(role, vendor, hostname: str = "") -> dict[str, str]:
    """Valores SNMP coherentes con la persona (sysDescr/sysName/sysObjectID)."""
    fam = family(role, vendor)
    ident = dict(_SNMP_IDENTITY.get(fam, _SNMP_IDENTITY["generic"]))
    ident["sysname"] = hostname or "decoy"
    return ident
