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

# OIDs por defecto (Cisco / estándar)
DEFAULT_OIDS = {
    "cpu": "1.3.6.1.4.1.9.9.109.1.1.1.1.3",      # cpmCPUTotal5minRev
    "mem": "1.3.6.1.4.1.9.9.48.1.1.1.5",          # ciscoMemoryPoolUsed
    "uptime": "1.3.6.1.2.1.1.3.0",                # sysUpTime
}

SNMP_COMMUNITY = "public"
SNMP_PORT = 161
SNMP_TIMEOUT = 5  # segundos

# Drivers NAPALM que normalmente exponen SNMP
SNMP_DRIVERS = [
    "ios", "iosxr", "nxos", "junos", "eos", "procurve", "comware", "hpe",
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
        if value:
            results[name] = value

    return results


def poll_device_snmp(
    host: str,
    community: str = SNMP_COMMUNITY,
    oids: Optional[dict] = None,
    timeout: int = SNMP_TIMEOUT,
) -> dict:
    """Consulta SNMP v2c a un dispositivo y devuelve {oid: valor_str}.

    Usa pysnmp si está disponible; si no responde o no está instalado,
    hace fallback al binario ``snmpget``.

    Args:
        host: IP o hostname del dispositivo.
        community: Community SNMP (por defecto "public").
        oids: Dict {nombre: oid_numérico}. Por defecto, CPU, memoria
            y uptime (DEFAULT_OIDS).
        timeout: Timeout total en segundos (5 por defecto).

    Returns:
        Dict {nombre_oid: valor_str} con los OIDs que respondieron.
        Vacío si el dispositivo no responde.
    """
    oid_map = oids or DEFAULT_OIDS

    if PYSNMP_AVAILABLE:
        results = _poll_with_pysnmp(host, oid_map, community, timeout)
        if results:
            return results
        logger.debug("pysnmp sin resultados para %s, probando snmpget", host)

    return _poll_with_snmpget(host, oid_map, community, timeout)


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
        if driver not in SNMP_DRIVERS:
            logger.debug("Driver %s sin soporte SNMP — omitido", driver)
            continue

        host = dev.get("hostname")
        if not host:
            logger.warning("Dispositivo sin hostname: %s", dev.get("id", "?"))
            continue

        # Desencriptar credenciales por si el flujo las requiere
        # (SNMP v2c usa community, no user/password, pero el archivo
        # guarda los passwords cifrados con crypto_svc)
        creds = dev.get("credentials") or {}
        if creds.get("password"):
            creds["password"] = decrypt(creds["password"])

        logger.info("Polling SNMP a %s (%s)", host, driver)
        metrics = poll_device_snmp(host, community=community, timeout=timeout)
        if metrics:
            results[dev.get("id", host)] = metrics

    return results
