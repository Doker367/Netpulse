"""NetPulse — Polling SNMP (v2c).

Recopila métricas de dispositivos de red vía SNMP. Usa ``pysnmp``
si está instalado; si no, hace fallback al binario ``snmpget`` del
sistema (net-snmp) ejecutado como subprocess con timeout de 5s.

Los equipos reales suelen responder a SNMPv2c con community
``public``, que es el valor por defecto.
"""

import logging
import shutil
import subprocess
from typing import Optional

import yaml

from app.core.settings import DEVICES_FILE
from app.services.crypto_svc import decrypt

logger = logging.getLogger(__name__)

# OIDs por defecto (Cisco / estándar MIB-II)
DEFAULT_OIDS = {
    "cpu": "1.3.6.1.4.1.9.9.109.1.1.1.1.3",      # cpmCPUTotal5minRev
    "mem": "1.3.6.1.4.1.9.9.48.1.1.1.5",          # ciscoMemoryPoolUsed
    "uptime": "1.3.6.1.2.1.1.3.0",                # sysUpTime
    "descr": "1.3.6.1.2.1.1.1.0",                 # sysDescr
    "name": "1.3.6.1.2.1.1.5.0",                  # sysName
}

# OIDs específicos para switches HP ProCurve / ArubaOS-S
HP_PROCURVE_OIDS = {
    "cpu": "1.3.6.1.4.1.11.2.14.11.5.1.9.6.1.0",           # hpSwitchCpuStat (1 min avg)
    "mem_total": "1.3.6.1.4.1.11.2.14.11.5.1.1.2.1.1.1.5.1",# hpGlobalMemTotalBytes
    "mem_free": "1.3.6.1.4.1.11.2.14.11.5.1.1.2.1.1.1.6.1", # hpGlobalMemFreeBytes
    "mem_alloc": "1.3.6.1.4.1.11.2.14.11.5.1.1.2.1.1.1.7.1",# hpGlobalMemAllocBytes
    "uptime": "1.3.6.1.2.1.1.3.0",                         # sysUpTime
    "descr": "1.3.6.1.2.1.1.1.0",                          # sysDescr
    "name": "1.3.6.1.2.1.1.5.0",                           # sysName
}

SNMP_COMMUNITY = "public"
SNMP_PORT = 161
SNMP_TIMEOUT = 5  # segundos

# Drivers que soportan o representan SNMP
SNMP_DRIVERS = [
    "ios", "iosxr", "nxos", "junos", "eos", "procurve", "comware", "hpe",
    "snmp", "generic", "ros", "mikrotik", "linux", "huawei", "alcatel_aos",
]

# pysnmp es opcional — si no está instalado se usa `snmpget`
try:
    from pysnmp.hlapi import *  # type: ignore  # noqa: F401,F403

    PYSNMP_AVAILABLE = True
except ImportError:
    PYSNMP_AVAILABLE = False


def _load_devices() -> list[dict]:
    """Carga la lista de dispositivos desde config/devices.yaml.

    Returns:
        Lista de dicts con la estructura
        ``{id, hostname, port, driver, credentials: {username, password}}``.
        Si el archivo no existe o es inválido, devuelve lista vacía.
    """
    try:
        with open(DEVICES_FILE, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return data.get("devices", []) or []
    except Exception as exc:
        logger.error("No se pudo leer %s: %s", DEVICES_FILE, exc)
        return []


def _poll_with_pysnmp(host: str, oids: dict, community: str, timeout: int) -> dict:
    """Polling SNMP vía pysnmp (getCmd por cada OID, SNMPv2c).

    Returns:
        Dict {nombre_oid: valor_str} con los OIDs respondidos.
    """
    results: dict = {}
    for name, oid in oids.items():
        try:
            error_indication, error_status, _, var_binds = next(
                getCmd(
                    SnmpEngine(),
                    CommunityData(community, mpModel=1),  # 1 = SNMPv2c
                    UdpTransportTarget((host, SNMP_PORT), timeout=timeout, retries=0),
                    ContextData(),
                    ObjectType(ObjectIdentity(oid)),
                )
            )
        except Exception as exc:
            logger.debug("pysnmp error %s %s: %s", host, oid, exc)
            continue

        if error_indication or error_status:
            logger.debug(
                "SNMP error %s %s: %s",
                host, oid, error_indication or error_status.prettyPrint(),
            )
            continue

        try:
            results[name] = var_binds[0][1].prettyPrint()
        except Exception:
            results[name] = str(var_binds[0][1])

    return results


def _poll_with_snmpget(
    host: str, oids: dict, community: str, timeout: int
) -> dict:
    """Polling SNMP vía el binario `snmpget` (net-snmp) como subprocess.

    Ejecuta un ``snmpget -v2c`` por OID con timeout de 5s y parsea
    la salida (``-Oqv`` → solo el valor, sin OID ni tipo).

    Returns:
        Dict {nombre_oid: valor_str} con los OIDs respondidos.
    """
    if shutil.which("snmpget") is None:
        logger.warning("snmpget no está instalado en el sistema")
        return {}

    results: dict = {}
    for name, oid in oids.items():
        cmd = [
            "snmpget", "-v2c", "-c", community,
            "-t", "2", "-r", "1",  # 2s de timeout por intento, 1 reintento
            "-Oqv",  # solo el valor
            host, oid,
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            logger.warning("snmpget timeout para %s %s", host, oid)
            continue

        if proc.returncode != 0:
            logger.debug(
                "snmpget falló para %s %s: %s", host, oid, proc.stderr.strip()
            )
            continue

        value = proc.stdout.strip().strip('"')
        if value and not value.startswith("No Such") and not value.startswith("Error"):
            results[name] = value

    return results


def poll_device_snmp(
    host: str,
    community: str = SNMP_COMMUNITY,
    oids: Optional[dict] = None,
    timeout: int = SNMP_TIMEOUT,
    driver: str = "",
) -> dict:
    """Consulta SNMP v2c a un dispositivo y devuelve {oid: valor_str}.

    Usa pysnmp si está disponible; si no responde o no está instalado,
    hace fallback al binario ``snmpget``.
    """
    oid_map = oids
    if not oid_map:
        drv = (driver or "").lower()
        if "procurve" in drv or "hpe" in drv or drv == "comware":
            oid_map = HP_PROCURVE_OIDS
        else:
            oid_map = DEFAULT_OIDS

    results = {}
    if PYSNMP_AVAILABLE:
        results = _poll_with_pysnmp(host, oid_map, community, timeout)
        if not results:
            logger.debug("pysnmp sin resultados para %s, probando snmpget", host)

    if not results:
        results = _poll_with_snmpget(host, oid_map, community, timeout)

    # Si se usó DEFAULT_OIDS pero no se obtuvo CPU, intentar con HP_PROCURVE_OIDS
    if "cpu" not in results and oids is None and oid_map != HP_PROCURVE_OIDS:
        hp_res = _poll_with_snmpget(host, HP_PROCURVE_OIDS, community, timeout)
        results.update(hp_res)

    return results


def get_snmp_interfaces(
    host: str, community: str = SNMP_COMMUNITY, timeout: int = 5
) -> dict:
    """Obtiene interfaces del switch vía SNMP ifTable."""
    interfaces = {}
    try:
        cmd_desc = ["snmpwalk", "-v2c", "-c", community, "-t", "2", "-r", "1", "-Oqv", host, "1.3.6.1.2.1.2.2.1.2"]
        p_desc = subprocess.run(cmd_desc, capture_output=True, text=True, timeout=timeout)
        names = [x.strip().strip('"') for x in p_desc.stdout.strip().splitlines() if x.strip()]

        cmd_stat = ["snmpwalk", "-v2c", "-c", community, "-t", "2", "-r", "1", "-Oqv", host, "1.3.6.1.2.1.2.2.1.8"]
        p_stat = subprocess.run(cmd_stat, capture_output=True, text=True, timeout=timeout)
        stats = [x.strip() for x in p_stat.stdout.strip().splitlines() if x.strip()]

        cmd_admin = ["snmpwalk", "-v2c", "-c", community, "-t", "2", "-r", "1", "-Oqv", host, "1.3.6.1.2.1.2.2.1.7"]
        p_admin = subprocess.run(cmd_admin, capture_output=True, text=True, timeout=timeout)
        admins = [x.strip() for x in p_admin.stdout.strip().splitlines() if x.strip()]

        cmd_spd = ["snmpwalk", "-v2c", "-c", community, "-t", "2", "-r", "1", "-Oqv", host, "1.3.6.1.2.1.2.2.1.5"]
        p_spd = subprocess.run(cmd_spd, capture_output=True, text=True, timeout=timeout)
        speeds = [x.strip() for x in p_spd.stdout.strip().splitlines() if x.strip()]

        cmd_mac = ["snmpwalk", "-v2c", "-c", community, "-t", "2", "-r", "1", "-Oqv", host, "1.3.6.1.2.1.2.2.1.6"]
        p_mac = subprocess.run(cmd_mac, capture_output=True, text=True, timeout=timeout)
        macs = [x.strip() for x in p_mac.stdout.strip().splitlines() if x.strip()]

        for i, raw_name in enumerate(names):
            if not raw_name or raw_name.startswith("No Such"):
                continue
            oper = stats[i] if i < len(stats) else ""
            admin = admins[i] if i < len(admins) else "up"
            # snmpwalk -Oqv devuelve etiquetas ("up"/"down") en vez de
            # enteros ("1"/"2") — aceptar ambos formatos.
            is_up = oper.lower() in ("up", "1", "true")
            is_enabled = admin.lower() in ("up", "1", "true")
            speed_bps = int(speeds[i]) if i < len(speeds) and speeds[i].isdigit() else 1000000000
            speed_mbps = speed_bps // 1000000
            mac = macs[i] if i < len(macs) else "N/A"
            name = f"Port {raw_name}" if raw_name.isdigit() else raw_name
            interfaces[name] = {
                "name": name,
                "is_up": is_up,
                "is_enabled": is_enabled,
                "speed": speed_mbps,
                "mac_address": mac,
                "description": f"{name} (SNMP Managed)",
            }
    except Exception as e:
        logger.debug("get_snmp_interfaces error para %s: %s", host, e)
    return interfaces


def poll_all_devices(
    community: str = SNMP_COMMUNITY, timeout: int = SNMP_TIMEOUT
) -> dict:
    """Hace polling SNMP a todos los dispositivos de config/devices.yaml.

    Solo se consultan dispositivos cuyo driver está en
    ``SNMP_DRIVERS`` (ios, iosxr, nxos, junos, eos, procurve,
    comware, hpe), ya que son los que suelen exponer SNMP.

    Returns:
        Dict ``{device_id: {oid: valor}}``. Los dispositivos que no
        responden se omiten.
    """
    devices = _load_devices()
    results: dict = {}

    for dev in devices:
        driver = (dev.get("driver") or "").lower()
        protocol = (dev.get("protocol") or "").lower()
        if protocol != "snmp" and driver not in SNMP_DRIVERS:
            logger.debug("Driver %s / Protocolo %s sin soporte SNMP — omitido", driver, protocol)
            continue

        host = dev.get("hostname")
        if not host:
            logger.warning("Dispositivo sin hostname: %s", dev.get("id", "?"))
            continue

        creds = dev.get("credentials") or {}
        if creds.get("password"):
            creds["password"] = decrypt(creds["password"])

        dev_comm = creds.get("community") or dev.get("community") or dev.get("snmp_ro") or community
        logger.info("Polling SNMP a %s (%s) [comm=%s]", host, driver, dev_comm)
        metrics = poll_device_snmp(host, community=dev_comm, timeout=timeout)
        if metrics:
            results[dev.get("id", host)] = metrics

    return results
