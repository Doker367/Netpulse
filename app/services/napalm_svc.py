"""NetPulse — NAPALM Service Layer.

Wrapper multi-driver con SSL fix para Arista vEOS y manejo
de credenciales desde inventario (sin passwords en código).

Mejoras:
- Soporte MikroTik RouterOS 7.22 (fallback via librouteros)
- Clasificación inteligente de errores
- Retry automático en errores de conexión
- Logging contextual por dispositivo
"""

import logging
import ssl
import time
import http.client
from datetime import datetime, timezone
from typing import Optional

from napalm import get_network_driver

from app.core.settings import BACKUP_DIR, NAPALM_TIMEOUT
from app.services.concurrency import limited
from app.services.crypto_svc import decrypt
from app.services import netmiko_svc
from app.services.metrics_svc import (
    netpulse_napalm_duration_seconds,
    netpulse_napalm_operations_total,
    update_device_metrics,
)
from app.utils.errors import map_napalm_error

logger = logging.getLogger(__name__)

# ── SSL Fix para Arista vEOS (TLS legacy ciphers) ──────────

_ssl_fixed = False


def _fix_arista_ssl():
    """Permite que Python 3.14 se conecte a Arista vEOS con ciphers legacy."""
    global _ssl_fixed
    if _ssl_fixed:
        return
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.set_ciphers("AES256-SHA:AES128-SHA:DEFAULT:@SECLEVEL=0")

    original_init = http.client.HTTPSConnection.__init__

    def patched(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self._context = ctx

    http.client.HTTPSConnection.__init__ = patched
    _ssl_fixed = True


_fix_arista_ssl()


# ── Error Classification ────────────────────────────────────

_ERROR_PATTERNS = {
    "connection_error": [
        "connection refused",
        "connection reset",
        "connection timed out",
        "no route to host",
        "network is unreachable",
        "eof occurred",
        "broken pipe",
        "cannot assign requested address",
        "tcp connection",
        "socket",
        "errno 111",
        "errno 113",
        "errno 104",
        "errno 110",
    ],
    "auth_error": [
        "authentication failed",
        "authorization failed",
        "login failed",
        "invalid credentials",
        "access denied",
        "permission denied",
        "unauthorized",
        "bad password",
        "wrong password",
    ],
    "timeout": [
        "timed out",
        "timeout",
        "read timed out",
    ],
    "command_error": [
        "routerboard",
        "command not found",
        "unknown command",
        "syntax error",
        "invalid command",
        "no such command",
        "bad command",
    ],
    "driver_error": [
        "no driver",
        "driver not found",
        "unknown driver",
        "not supported",
        "not implemented",
        "attributeerror",
        "object has no attribute",
    ],
}


def _classify_error(error: Exception) -> str:
    """Clasifica un error en una categoría para mejor diagnóstico."""
    error_msg = str(error).lower()
    error_type_name = type(error).__name__.lower()

    # Check by exception type first
    if "connection" in error_type_name or "socket" in error_type_name:
        return "connection_error"
    if "auth" in error_type_name or "login" in error_type_name:
        return "auth_error"
    if "timeout" in error_type_name or "time" in error_type_name:
        return "timeout"
    if "attribute" in error_type_name:
        return "driver_error"

    # Check by message patterns
    for category, patterns in _ERROR_PATTERNS.items():
        for pattern in patterns:
            if pattern in error_msg:
                return category

    return "unknown"


def _is_retryable(error_type: str) -> bool:
    """Determina si un error merece un reintento."""
    return error_type in ("connection_error", "timeout")


# ── Inventory ───────────────────────────────────────────────

def _load_devices() -> list[dict]:
    """Carga el inventario (cacheado) con passwords descifrados (efímero)."""
    from app.services import inventory_svc
    devices = inventory_svc.get_raw_devices()
    # Decrypt passwords transparently for NAPALM/netmiko (sobre la copia)
    for d in devices:
        creds = d.get("credentials", {})
        pw = creds.get("password", "")
        if pw:
            creds["password"] = decrypt(pw)
        ep = d.get("enable_password")
        if ep:
            d["enable_password"] = decrypt(ep)
    return devices


def _find_device(device_id: str) -> Optional[dict]:
    for d in _load_devices():
        if d["id"] == device_id:
            return d
    return None


def _netmiko_direct(device: dict) -> bool:
    """True si la operación debe ir por netmiko directo (sin intentar NAPALM).

    Casos: telnet (protocolo o puerto 23), override ``netmiko_device_type``,
    o drivers sin driver NAPALM real (h3c_comware, alcatel_aos, alcatel_sros).
    """
    if not device:
        return False
    proto = str(device.get("protocol") or "").lower()
    if proto == "snmp":
        return False
    if proto == "telnet":
        return True
    try:
        port = int(device.get("port", 0) or 0)
    except (TypeError, ValueError):
        port = 0
    if port == 23:
        return True
    if device.get("netmiko_device_type"):
        return True
    return str(device.get("driver", "")).lower() in netmiko_svc.NETMIKO_DIRECT_DRIVERS


def _build_optional_args(device: dict) -> dict:
    """Construye optional_args para NAPALM según el driver."""
    args = {}
    if device.get("port") and device["port"] != 22:
        args["port"] = device["port"]
    if device["driver"] == "eos":
        args["transport"] = "https"
    # Enable password for Cisco/HP devices that require enable mode
    if device.get("enable_password"):
        args["secret"] = device["enable_password"]
    return args


# ── Driver aliases (HP ProCurve/Comware use Cisco-like CLI) ──

_DRIVER_ALIASES = {
    "procurve": "ios",
    "comware": "ios",
    "hpe": "ios",
}


def _resolve_driver(driver_name: str) -> str:
    """Resuelve aliases de drivers a su driver NAPALM real."""
    return _DRIVER_ALIASES.get(driver_name, driver_name)


# ── Core Operations ─────────────────────────────────────────

class NapalmResult:
    """Resultado de operación NAPALM."""

    def __init__(self, device_id: str):
        self.device_id = device_id
        self.success = False
        self.data = None
        self.error = None          # Mensaje en español para el usuario
        self.error_raw = None      # Mensaje original en inglés para debugging
        self.error_type: Optional[str] = None  # connection_error, auth_error, timeout, command_error, driver_error
        self.duration_ms: Optional[float] = None
        self.timestamp = datetime.now(timezone.utc)

    def set_error(self, error_type: str, raw_msg: str) -> None:
        """Almacena el error original (inglés) y su traducción al español."""
        self.error_type = error_type
        self.error_raw = raw_msg
        self.error = map_napalm_error(error_type, self.device_id)


@limited
def _execute(device: dict, operation: str, *args, retry: bool = True, **kwargs) -> NapalmResult:
    """Ejecuta una operación NAPALM genérica con retry y logging contextual."""
    result = NapalmResult(device["id"])
    device_id = device["id"]

    creds = device.get("credentials", {})
    username = kwargs.pop("username", creds.get("username"))
    password = kwargs.pop("password", creds.get("password"))

    # Permitir password vacío (ej. MikroTik con password en blanco)
    # Solo fallar si username está ausente o password es None (no configurado)
    if not username or password is None:
        result.set_error("auth_error", f"No credentials for {device['id']}")
        logger.warning("[%s] Credenciales ausentes — username=%s, password_set=%s",
                       device_id, bool(username), password is not None)
        return result

    start = time.monotonic()
    last_error = None
    last_error_type = None
    max_attempts = 2 if retry else 1

    for attempt in range(1, max_attempts + 1):
        try:
            driver_name = device["driver"]
            napalm_driver = _resolve_driver(driver_name)
            driver_cls = get_network_driver(napalm_driver)
            opts = _build_optional_args(device)
            opts["timeout"] = NAPALM_TIMEOUT

            logger.debug("[%s] attempt=%d/%d → driver=%s host=%s port=%s",
                         device_id, attempt, max_attempts,
                         driver_name, device["hostname"], device.get("port", "default"))

            with driver_cls(
                hostname=device["hostname"],
                username=username,
                password=password,
                optional_args=opts,
                timeout=NAPALM_TIMEOUT,
            ) as dev:
                dev.open()
                fn = getattr(dev, operation)
                result.data = fn(*args, **kwargs)
                dev.close()
                result.success = True

            result.duration_ms = round((time.monotonic() - start) * 1000, 2)

            # ── Prometheus metrics ──────────────────────────
            netpulse_napalm_operations_total.labels(
                device_id=device_id,
                operation=operation,
                status="success",
            ).inc()
            netpulse_napalm_duration_seconds.labels(
                device_id=device_id,
                operation=operation,
            ).observe(result.duration_ms / 1000.0)
            update_device_metrics(device_id, device["driver"], is_up=True)
            # ─────────────────────────────────────────────────

            logger.info("[%s] %s → OK (%.0fms, attempt=%d)",
                        device_id, operation, result.duration_ms, attempt)
            return result

        except Exception as e:
            last_error = str(e)
            last_error_type = _classify_error(e)

            logger.error("[%s] %s → attempt=%d/%d error_type=%s: %s",
                         device_id, operation, attempt, max_attempts,
                         last_error_type, last_error)

            if not retry or attempt >= max_attempts or not _is_retryable(last_error_type):
                break

            # Retry con delay
            logger.info("[%s] Retrying in 2s...", device_id)
            time.sleep(2)

    result.set_error(last_error_type or "unknown", last_error or "Unknown error")
    result.duration_ms = round((time.monotonic() - start) * 1000, 2)

    # ── Prometheus metrics (error path) ────────────────────
    netpulse_napalm_operations_total.labels(
        device_id=device_id,
        operation=operation,
        status=last_error_type or "unknown",
    ).inc()
    netpulse_napalm_duration_seconds.labels(
        device_id=device_id,
        operation=operation,
    ).observe(result.duration_ms / 1000.0)
    update_device_metrics(device_id, device["driver"], is_up=False)
    # ─────────────────────────────────────────────────────────

    return result


# ── MikroTik-specific workarounds ───────────────────────────

@limited
def _execute_mikrotik_facts(device: dict) -> NapalmResult:
    """Obtiene facts de MikroTik con fallback via librouteros.

    RouterOS 7.22 eliminó el comando 'routerboard' que napalm-ros
    usa en get_facts(). Esta función intenta get_facts() primero y,
    si falla por command_error, consulta manualmente interfaces
    vía librouteros para devolver facts parciales.
    """
    result = NapalmResult(device["id"])
    device_id = device["id"]
    start = time.monotonic()

    creds = device.get("credentials", {})
    username = creds.get("username", "admin")
    password = creds.get("password", "")

    # Intentar napalm-ros get_facts() primero
    try:
        driver_cls = get_network_driver("ros")
        opts = _build_optional_args(device)
        # Use short timeout for ROS — fast fail to librouteros fallback
        opts["timeout"] = 5

        logger.debug("[%s] mikrotik_facts: intentando napalm-ros get_facts()", device_id)

        with driver_cls(
            hostname=device["hostname"],
            username=username,
            password=password,
            optional_args=opts,
            timeout=NAPALM_TIMEOUT,
        ) as dev:
            dev.open()
            result.data = dev.get_facts()
            dev.close()
            result.success = True
            result.duration_ms = round((time.monotonic() - start) * 1000, 2)

            # ── Prometheus metrics ──────────────────────────
            netpulse_napalm_operations_total.labels(
                device_id=device_id,
                operation="get_facts",
                status="success",
            ).inc()
            netpulse_napalm_duration_seconds.labels(
                device_id=device_id,
                operation="get_facts",
            ).observe(result.duration_ms / 1000.0)
            update_device_metrics(device_id, device["driver"], is_up=True)
            # ─────────────────────────────────────────────────

            logger.info("[%s] mikrotik_facts: napalm-ros OK (%.0fms)", device_id, result.duration_ms)
            return result

    except Exception as e:
        error_type = _classify_error(e)
        logger.warning("[%s] mikrotik_facts: napalm-ros falló (error_type=%s): %s",
                       device_id, error_type, str(e))

        # Si NO es command_error, no tiene sentido intentar fallback
        if error_type != "command_error":
            # Igual intentamos fallback si parece ser problema de routerboard
            if "routerboard" not in str(e).lower():
                result.set_error(error_type, str(e))
                result.duration_ms = round((time.monotonic() - start) * 1000, 2)

                # ── Prometheus metrics (error, no fallback) ──
                netpulse_napalm_operations_total.labels(
                    device_id=device_id,
                    operation="get_facts",
                    status=error_type,
                ).inc()
                netpulse_napalm_duration_seconds.labels(
                    device_id=device_id,
                    operation="get_facts",
                ).observe(result.duration_ms / 1000.0)
                update_device_metrics(device_id, device["driver"], is_up=False)
                # ─────────────────────────────────────────────

                return result

    # Fallback: usar librouteros para datos básicos
    logger.info("[%s] mikrotik_facts: activando fallback librouteros", device_id)
    try:
        from librouteros import connect

        port = device.get("port", 8728)
        host = device["hostname"]

        logger.debug("[%s] librouteros: conectando a %s:%s", device_id, host, port)

        api = connect(
            username=username,
            password=password,
            host=host,
            port=port,
            timeout=10.0,
        )

        # Obtener hostname
        hostname = "Unknown"
        try:
            identity = list(api.path("/system/identity").select("name"))
            if identity:
                hostname = identity[0].get("name", "Unknown")
        except Exception:
            pass

        # Obtener modelo
        model = "MikroTik RouterOS"
        try:
            resources = list(api.path("/system/resource").select(
                "board-name", "version", "architecture-name"
            ))
            if resources:
                r = resources[0]
                board = r.get("board-name", "")
                model = f"MikroTik {board}" if board else "MikroTik RouterOS"
        except Exception:
            pass

        # Obtener lista de interfaces
        interface_list = []
        try:
            interfaces = list(api.path("/interface").select("name", "type", "mac-address"))
            for iface in interfaces:
                interface_list.append(iface.get("name", "unknown"))
        except Exception:
            pass

        api.close()

        # Construir facts parciales
        result.data = {
            "hostname": hostname,
            "fqdn": hostname,
            "vendor": "MikroTik",
            "model": model,
            "os_version": resources[0].get("version", "") if resources else "RouterOS 7.x",
            "serial_number": "",
            "interface_list": interface_list,
            "_fallback": True,
            "_note": "Datos obtenidos via librouteros (fallback por incompatibilidad RouterOS 7.22)",
        }

        result.success = True
        result.duration_ms = round((time.monotonic() - start) * 1000, 2)

        # ── Prometheus metrics (fallback success) ───────────
        netpulse_napalm_operations_total.labels(
            device_id=device_id,
            operation="get_facts",
            status="success",
        ).inc()
        netpulse_napalm_duration_seconds.labels(
            device_id=device_id,
            operation="get_facts",
        ).observe(result.duration_ms / 1000.0)
        update_device_metrics(device_id, device["driver"], is_up=True)
        # ─────────────────────────────────────────────────────

        logger.info("[%s] mikrotik_facts: fallback librouteros OK (%.0fms) — interfaces=%d",
                    device_id, result.duration_ms, len(interface_list))
        return result

    except ImportError:
        result.set_error("driver_error", "librouteros no instalado — no se puede usar fallback para MikroTik")
        result.duration_ms = round((time.monotonic() - start) * 1000, 2)

        # ── Prometheus metrics (fallback ImportError) ───────
        netpulse_napalm_operations_total.labels(
            device_id=device_id,
            operation="get_facts",
            status="driver_error",
        ).inc()
        netpulse_napalm_duration_seconds.labels(
            device_id=device_id,
            operation="get_facts",
        ).observe(result.duration_ms / 1000.0)
        update_device_metrics(device_id, device["driver"], is_up=False)
        # ─────────────────────────────────────────────────────

        logger.error("[%s] mikrotik_facts: librouteros no disponible", device_id)
        return result

    except Exception as e:
        result.set_error(_classify_error(e), f"Fallback librouteros falló: {e}")
        result.duration_ms = round((time.monotonic() - start) * 1000, 2)

        # ── Prometheus metrics (fallback error) ─────────────
        netpulse_napalm_operations_total.labels(
            device_id=device_id,
            operation="get_facts",
            status=_classify_error(e),
        ).inc()
        netpulse_napalm_duration_seconds.labels(
            device_id=device_id,
            operation="get_facts",
        ).observe(result.duration_ms / 1000.0)
        update_device_metrics(device_id, device["driver"], is_up=False)
        # ─────────────────────────────────────────────────────

        logger.error("[%s] mikrotik_facts: fallback falló: %s", device_id, e)
        return result


@limited
def _execute_mikrotik_interfaces(device: dict) -> NapalmResult:
    """Obtiene interfaces de MikroTik vía librouteros como fallback."""
    result = NapalmResult(device["id"])
    device_id = device["id"]
    start = time.monotonic()

    creds = device.get("credentials", {})
    username = creds.get("username", "admin")
    password = creds.get("password", "")

    # Intentar napalm-ros primero
    try:
        driver_cls = get_network_driver("ros")
        opts = _build_optional_args(device)
        opts["timeout"] = NAPALM_TIMEOUT

        with driver_cls(
            hostname=device["hostname"],
            username=username,
            password=password,
            optional_args=opts,
            timeout=NAPALM_TIMEOUT,
        ) as dev:
            dev.open()
            result.data = dev.get_interfaces()
            dev.close()
            result.success = True
            result.duration_ms = round((time.monotonic() - start) * 1000, 2)

            # ── Prometheus metrics ──────────────────────────
            netpulse_napalm_operations_total.labels(
                device_id=device_id,
                operation="get_interfaces",
                status="success",
            ).inc()
            netpulse_napalm_duration_seconds.labels(
                device_id=device_id,
                operation="get_interfaces",
            ).observe(result.duration_ms / 1000.0)
            update_device_metrics(device_id, device["driver"], is_up=True)
            # ─────────────────────────────────────────────────

            logger.info("[%s] mikrotik_interfaces: napalm-ros OK (%.0fms)", device_id, result.duration_ms)
            return result
    except Exception as e:
        logger.warning("[%s] mikrotik_interfaces: napalm-ros falló: %s", device_id, e)

    # Fallback librouteros
    logger.info("[%s] mikrotik_interfaces: activando fallback librouteros", device_id)
    try:
        from librouteros import connect

        port = device.get("port", 8728)
        host = device["hostname"]

        api = connect(
            username=username,
            password=password,
            host=host,
            port=port,
            timeout=NAPALM_TIMEOUT,
        )

        interfaces_data = {}
        raw_interfaces = list(api.path("/interface").select(
            "name", "type", "mac-address", "running", "disabled",
            "mtu", "rx-byte", "tx-byte"
        ))

        for iface in raw_interfaces:
            name = iface.get("name", "unknown")
            interfaces_data[name] = {
                "is_up": iface.get("running", "false") == "true" and iface.get("disabled", "true") == "false",
                "is_enabled": iface.get("disabled", "true") == "false",
                "description": "",
                "mac_address": iface.get("mac-address", ""),
                "mtu": int(iface.get("mtu", 1500)),
                "speed": 0,  # MikroTik API doesn't expose speed in /interface
            }

        api.close()

        result.data = interfaces_data
        result.success = True
        result.duration_ms = round((time.monotonic() - start) * 1000, 2)

        # ── Prometheus metrics (fallback success) ───────────
        netpulse_napalm_operations_total.labels(
            device_id=device_id,
            operation="get_interfaces",
            status="success",
        ).inc()
        netpulse_napalm_duration_seconds.labels(
            device_id=device_id,
            operation="get_interfaces",
        ).observe(result.duration_ms / 1000.0)
        update_device_metrics(device_id, device["driver"], is_up=True)
        # ─────────────────────────────────────────────────────

        logger.info("[%s] mikrotik_interfaces: fallback librouteros OK (%.0fms) — count=%d",
                    device_id, result.duration_ms, len(interfaces_data))
        return result

    except ImportError:
        result.set_error("driver_error", "librouteros no instalado")
        result.duration_ms = round((time.monotonic() - start) * 1000, 2)

        # ── Prometheus metrics (fallback ImportError) ───────
        netpulse_napalm_operations_total.labels(
            device_id=device_id,
            operation="get_interfaces",
            status="driver_error",
        ).inc()
        netpulse_napalm_duration_seconds.labels(
            device_id=device_id,
            operation="get_interfaces",
        ).observe(result.duration_ms / 1000.0)
        update_device_metrics(device_id, device["driver"], is_up=False)
        # ─────────────────────────────────────────────────────

        return result
    except Exception as e:
        result.set_error(_classify_error(e), f"Fallback librouteros interfaces: {e}")
        result.duration_ms = round((time.monotonic() - start) * 1000, 2)

        # ── Prometheus metrics (fallback error) ─────────────
        netpulse_napalm_operations_total.labels(
            device_id=device_id,
            operation="get_interfaces",
            status=_classify_error(e),
        ).inc()
        netpulse_napalm_duration_seconds.labels(
            device_id=device_id,
            operation="get_interfaces",
        ).observe(result.duration_ms / 1000.0)
        update_device_metrics(device_id, device["driver"], is_up=False)
        # ─────────────────────────────────────────────────────

        return result


def _execute_snmp_facts(device: dict) -> NapalmResult:
    """Obtiene facts de dispositivo SNMPv2c."""
    result = NapalmResult(device["id"])
    start = time.monotonic()
    try:
        from app.services.collectors import snmp_svc
        creds = device.get("credentials") or {}
        community = creds.get("community") or device.get("community") or device.get("snmp_ro") or "public"
        host = device.get("hostname", "")
        driver = device.get("driver", "")
        snmp_data = snmp_svc.poll_device_snmp(host, community=community, timeout=4, driver=driver)

        uptime_raw = str(snmp_data.get("uptime", "0"))
        uptime_sec = 0
        if ":" in uptime_raw:
            parts = uptime_raw.split(":")
            try:
                if len(parts) == 4:
                    uptime_sec = int(int(parts[0]) * 86400 + int(parts[1]) * 3600 + int(parts[2]) * 60 + float(parts[3]))
                elif len(parts) == 3:
                    uptime_sec = int(int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2]))
            except Exception:
                pass
        else:
            try:
                uptime_sec = int(uptime_raw) // 100
            except Exception:
                pass

        ifaces = snmp_svc.get_snmp_interfaces(host, community=community, timeout=3)
        if_names = list(ifaces.keys()) if ifaces else ["snmp0"]

        facts = {
            "uptime": uptime_sec,
            "vendor": device.get("driver", "Generic SNMP").upper(),
            "model": snmp_data.get("name") or "SNMP Managed Switch",
            "hostname": snmp_data.get("name") or device.get("hostname"),
            "fqdn": device.get("hostname"),
            "os_version": snmp_data.get("descr", "SNMPv2c"),
            "serial_number": "N/A",
            "interface_list": if_names,
            "_note": "Datos obtenidos vía SNMP v2c",
        }
        result.data = facts
        result.success = True
    except Exception as e:
        result.set_error("connection_error", f"SNMP query falló: {e}")
    result.duration_ms = round((time.monotonic() - start) * 1000, 2)
    return result


def _execute_snmp_interfaces(device: dict) -> NapalmResult:
    """Retorna interfaces del switch obtenidas vía SNMP."""
    result = NapalmResult(device["id"])
    start = time.monotonic()
    try:
        from app.services.collectors import snmp_svc
        creds = device.get("credentials") or {}
        community = creds.get("community") or device.get("community") or device.get("snmp_ro") or "public"
        host = device.get("hostname", "")
        ifaces = snmp_svc.get_snmp_interfaces(host, community=community, timeout=4)
        if not ifaces:
            ifaces = {
                "snmp0": {
                    "is_up": True,
                    "is_enabled": True,
                    "description": "SNMP Monitored Interface",
                    "speed": 1000,
                    "mac_address": "N/A",
                }
            }
        result.data = ifaces
        result.success = True
    except Exception as e:
        result.set_error("connection_error", f"SNMP interfaces falló: {e}")
    result.duration_ms = round((time.monotonic() - start) * 1000, 2)
    return result


# ── Public API ──────────────────────────────────────────────

def _netmiko_facts(device: dict) -> NapalmResult:
    """Facts vía netmiko (legacy/telnet), enriqueciendo con interface_list."""
    device_id = device["id"]
    result = NapalmResult(device_id)
    try:
        facts = netmiko_svc.get_facts(device)
        if not facts:
            result.set_error("driver_error", f"netmiko facts falló para {device_id}")
            return result
        # interface_list para FactsResponse.interface_count
        try:
            ifaces = netmiko_svc.get_interfaces(device)
            if ifaces:
                facts["interface_list"] = list(ifaces.keys())
        except Exception:
            pass
        facts.setdefault("interface_list", [])
        result.data = facts
        result.success = True
        logger.info("[%s] get_facts → OK via netmiko", device_id)
    except Exception as e:
        result.set_error("connection_error", f"netmiko facts: {e}")
    return result


def _netmiko_interfaces(device: dict) -> NapalmResult:
    """Interfaces vía netmiko (legacy/telnet)."""
    device_id = device["id"]
    result = NapalmResult(device_id)
    try:
        ifaces = netmiko_svc.get_interfaces(device)
        if not ifaces:
            result.set_error("driver_error", f"netmiko interfaces vacías para {device_id}")
            return result
        result.data = ifaces
        result.success = True
        logger.info("[%s] get_interfaces → OK via netmiko (%d)", device_id, len(ifaces))
    except Exception as e:
        result.set_error("connection_error", f"netmiko interfaces: {e}")
    return result


def _netmiko_running_config(device: dict) -> Optional[str]:
    """Running-config vía netmiko para diff/deploy."""
    try:
        return netmiko_svc.get_running_config(device)
    except Exception as e:
        logger.error("[%s] netmiko running-config: %s", device["id"], e)
        return None


def _netmiko_compare_config(device: dict, candidate: str) -> NapalmResult:
    """Dry-run por netmiko: diff contra la running-config (sin cambios)."""
    import difflib
    device_id = device["id"]
    result = NapalmResult(device_id)
    running = _netmiko_running_config(device)
    if running is None:
        result.set_error("driver_error", f"netmiko dry-run falló (running-config no disponible) para {device_id}")
        return result
    candidate_fixed = candidate if candidate.endswith("\n") else candidate + "\n"
    diff_lines = list(difflib.unified_diff(
        running.splitlines(keepends=True),
        candidate_fixed.splitlines(keepends=True),
        fromfile=f"{device_id}-running",
        tofile=f"{device_id}-candidate",
    ))
    result.data = "".join(diff_lines)
    result.success = True
    result.rollback_mode = "best-effort"
    return result


def _netmiko_deploy_session(device: dict, candidate: str) -> dict:
    """Deploy por netmiko (legacy/telnet). Best-effort, sin candidate transaccional."""
    start = time.monotonic()
    device_id = device["id"]
    # Diff previo para el payload (dry-run implícito antes de aplicar)
    compare = _netmiko_compare_config(device, candidate)
    diff = compare.data if compare.success else ""

    if not compare.success:
        return {"diff": diff, "committed": False,
                "error": compare.error, "duration_ms": 0,
                "rollback_mode": "best-effort"}

    res = netmiko_svc.send_config_lines(device, candidate)
    duration = round((time.monotonic() - start) * 1000, 2)
    if not res["ok"]:
        return {"diff": diff, "committed": False, "error": res["error"],
                "duration_ms": duration, "rollback_mode": "best-effort",
                "error_type": "connection_error" if _is_connection_like(res["error"] or "") else "command_error"}
    logger.info("[%s] deploy → OK via netmiko (best-effort, %.0fms)", device_id, duration)
    return {"diff": diff, "committed": True, "error": None,
            "duration_ms": duration, "rollback_mode": "best-effort"}


def get_facts(device_id: str) -> NapalmResult:
    d = _find_device(device_id)
    if not d:
        r = NapalmResult(device_id)
        r.set_error("driver_error", f"Device {device_id} not found")
        return r

    # Dispositivos SNMP
    if d.get("protocol") == "snmp" or d.get("driver") == "snmp":
        return _execute_snmp_facts(d)

    # MikroTik RouterOS 7.22 workaround
    if d.get("driver") == "ros":
        return _execute_mikrotik_facts(d)

    # Legacy/telnet: netmiko directo (sin esperar timeout NAPALM)
    if _netmiko_direct(d):
        return _netmiko_facts(d)

    result = _execute(d, "get_facts")

    # Fallback netmiko para equipos legacy (telnet / drivers antiguos)
    if not result.success and netmiko_svc.is_legacy_device(d):
        logger.info("[%s] NAPALM falló (%s) — intentando netmiko...",
                    device_id, result.error_type)
        facts = netmiko_svc.get_facts(d)
        if facts:
            result.data = facts
            result.success = True
            result.error = None
            result.error_type = None
            result.data["_note"] = "Datos vía netmiko (fallback legacy)"

    return result


def get_interfaces(device_id: str) -> NapalmResult:
    d = _find_device(device_id)
    if not d:
        r = NapalmResult(device_id)
        r.set_error("driver_error", f"Device {device_id} not found")
        return r

    # Dispositivos SNMP
    if d.get("protocol") == "snmp" or d.get("driver") == "snmp":
        return _execute_snmp_interfaces(d)

    # MikroTik RouterOS 7.22 workaround
    if d.get("driver") == "ros":
        return _execute_mikrotik_interfaces(d)

    # Legacy/telnet: netmiko directo (sin esperar timeout NAPALM)
    if _netmiko_direct(d):
        return _netmiko_interfaces(d)

    result = _execute(d, "get_interfaces")

    # Fallback netmiko para equipos legacy
    if not result.success and netmiko_svc.is_legacy_device(d):
        logger.info("[%s] NAPALM falló (%s) — intentando netmiko...",
                    device_id, result.error_type)
        ifaces = netmiko_svc.get_interfaces(d)
        if ifaces:
            result.data = ifaces
            result.success = True
            result.error = None
            result.error_type = None
            result.data["_note"] = "Datos vía netmiko (fallback legacy)"

    return result


def get_bgp_neighbors(device_id: str) -> NapalmResult:
    d = _find_device(device_id)
    if not d:
        r = NapalmResult(device_id)
        r.set_error("driver_error", f"Device {device_id} not found")
        return r
    return _execute(d, "get_bgp_neighbors", retry=False)


def get_bgp_config(device_id: str) -> NapalmResult:
    """Obtiene configuración BGP (groups/neighbors). Útil como fallback
    cuando get_bgp_neighbors no es compatible (ej. FRRouting)."""
    d = _find_device(device_id)
    if not d:
        r = NapalmResult(device_id)
        r.set_error("driver_error", f"Device {device_id} not found")
        return r
    return _execute(d, "get_bgp_config", retry=False)


def get_arp_table(device_id: str) -> NapalmResult:
    """Obtiene la tabla ARP del dispositivo (IP → MAC)."""
    d = _find_device(device_id)
    if not d:
        r = NapalmResult(device_id)
        r.set_error("driver_error", f"Device {device_id} not found")
        return r
    return _execute(d, "get_arp_table", retry=False)


def get_mac_address_table(device_id: str) -> NapalmResult:
    """Obtiene la tabla de direcciones MAC del dispositivo."""
    d = _find_device(device_id)
    if not d:
        r = NapalmResult(device_id)
        r.set_error("driver_error", f"Device {device_id} not found")
        return r
    return _execute(d, "get_mac_address_table", retry=False)


def get_lldp_neighbors(device_id: str) -> NapalmResult:
    """Obtiene vecinos LLDP del dispositivo (topología física)."""
    d = _find_device(device_id)
    if not d:
        r = NapalmResult(device_id)
        r.set_error("driver_error", f"Device {device_id} not found")
        return r
    return _execute(d, "get_lldp_neighbors", retry=False)


def get_ntp_servers(device_id: str) -> NapalmResult:
    """Obtiene los servidores NTP configurados en el dispositivo."""
    d = _find_device(device_id)
    if not d:
        r = NapalmResult(device_id)
        r.set_error("driver_error", f"Device {device_id} not found")
        return r
    return _execute(d, "get_ntp_servers", retry=False)


def get_environment(device_id: str) -> NapalmResult:
    """Obtiene el estado del hardware: temperatura, ventiladores, PSU."""
    d = _find_device(device_id)
    if not d:
        r = NapalmResult(device_id)
        r.set_error("driver_error", f"Device {device_id} not found")
        return r
    return _execute(d, "get_environment", retry=False)


def get_config(device_id: str, retrieve: str = "running") -> NapalmResult:
    d = _find_device(device_id)
    if not d:
        r = NapalmResult(device_id)
        r.set_error("driver_error", f"Device {device_id} not found")
        return r
    # ROS driver doesn't have get_config - run /export via librouteros
    if d.get("driver") == "ros":
        try:
            from librouteros import connect
            creds = d.get("credentials", {})
            api = connect(username=creds.get("username","admin"), password=creds.get("password",""),
                         host=d["hostname"], port=d.get("port",8728))
            sections = {}
            for cmd, label in [("/system/identity/print","System Identity"),("/ip/address/print","IP Addresses"),
                ("/ip/route/print","Routing Table"),("/interface/print","Interfaces"),
                ("/ip/dns/print","DNS"),("/system/clock/print","Clock")]:
                try:
                    r = list(api(cmd))
                    if r:
                        lines = "\n".join(f"  {k}: {v}" for item in r for k, v in dict(item).items())
                        sections[label] = lines
                except Exception:
                    pass
            api.close()
            if sections:
                r = NapalmResult(device_id)
                r.data = "\n\n".join(f"! {k}\n{v}" for k,v in sections.items())
                r.success = True
                return r
        except Exception as e:
            r = NapalmResult(device_id)
            r.data = f"⚠️ No se pudo obtener config: {e}"
            r.success = True
            return r
    # SNMP devices: raw CLI running-config is not available over SNMP
    if d.get("protocol") == "snmp" or d.get("driver") == "snmp":
        r = NapalmResult(device_id)
        r.data = (
            f"! NetPulse Telemetría — Dispositivo gestionado por SNMP v2c ({d['hostname']})\n"
            f"! La configuración raw (running-config) no está soportada vía SNMP.\n"
            f"! Consulta las pestañas 'Overview' e 'Interfaces' para métricas en vivo (CPU, RAM, Puertos)."
        )
        r.success = True
        return r

    # Legacy/telnet: running-config vía netmiko
    if _netmiko_direct(d):
        cfg_text = netmiko_svc.get_running_config(d)
        if cfg_text:
            r = NapalmResult(device_id)
            r.data = {"running": cfg_text}
            r.success = True
            logger.info("[%s] get_config → OK via netmiko (%d bytes)",
                        device_id, len(cfg_text))
            return r
        r = NapalmResult(device_id)
        r.set_error("driver_error", f"netmiko get_config falló para {device_id}")
        return r

    return _execute(d, "get_config", retrieve=retrieve)


def backup_config(device_id: str) -> NapalmResult:
    """Backup de running config a disco."""
    d = _find_device(device_id)
    if not d:
        r = NapalmResult(device_id)
        r.set_error("driver_error", f"Device {device_id} not found")
        return r

    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in device_id)

    # ROS: backup facts + interfaces instead of config
    if d.get("driver") == "ros":
        facts_r = get_facts(device_id)
        iface_r = get_interfaces(device_id)
        data = {
            "facts": facts_r.data if facts_r.success else {},
            "interfaces": iface_r.data if iface_r.success else {},
            "note": "MikroTik backup: facts + interfaces (no config via NAPALM)",
        }
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        fname = BACKUP_DIR / f"{safe_id}_{ts}.json"
        import json as _json
        fname.write_text(_json.dumps(data, indent=2))
        result = NapalmResult(device_id)
        result.success = True
        result.data = {"file": str(fname), "size": fname.stat().st_size, "timestamp": ts, "success": True}
        return result

    # SNMP: backup snapshot of facts + interfaces + resources
    if d.get("protocol") == "snmp" or d.get("driver") == "snmp":
        facts_r = get_facts(device_id)
        iface_r = get_interfaces(device_id)
        res_r = get_resources(device_id)
        data = {
            "device_id": device_id,
            "hostname": d.get("hostname"),
            "facts": facts_r.data if facts_r.success else {},
            "interfaces": iface_r.data if iface_r.success else {},
            "resources": res_r.data if res_r.success else {},
            "note": "SNMP backup snapshot: facts + interfaces + resources",
        }
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        fname = BACKUP_DIR / f"{safe_id}_{ts}.json"
        import json as _json
        fname.write_text(_json.dumps(data, indent=2))
        result = NapalmResult(device_id)
        result.success = True
        result.data = {"file": str(fname), "size": fname.stat().st_size, "timestamp": ts, "success": True}
        return result

    # Legacy/telnet: backup vía netmiko (show running-config)
    if _netmiko_direct(d):
        cfg_text = netmiko_svc.get_running_config(d)
        if not cfg_text:
            result = NapalmResult(device_id)
            result.set_error("driver_error", f"netmiko get_config falló para {device_id}")
            return result
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        fname = BACKUP_DIR / f"{safe_id}_{ts}.cfg"
        fname.write_text(cfg_text)
        result = NapalmResult(device_id)
        result.success = True
        result.data = {"file": str(fname), "size": len(cfg_text), "timestamp": ts, "success": True}
        logger.info("[%s] backup → OK via netmiko (%s, %d bytes)",
                    device_id, fname.name, len(cfg_text))
        return result

    result = _execute(d, "get_config", retrieve="running")
    if result.success and result.data:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        fname = BACKUP_DIR / f"{safe_id}_{ts}.cfg"
        config_text = result.data.get("running", "") if isinstance(result.data, dict) else str(result.data)
        fname.write_text(config_text)
        result.data = {"file": str(fname), "size": len(config_text), "timestamp": ts, "success": True}
    return result


# ── Config Deploy / Rollback ────────────────────────────────

@limited
def _execute_config_session(device: dict, candidate: str, commit: bool) -> dict:
    """Abre una conexión NAPALM y ejecuta load + compare + (opcional) commit.

    Retorna dict con diff, committed, error, duration_ms.
    Útil para operaciones que requieren estado (candidate cargado).
    """
    device_id = device["id"]
    creds = device.get("credentials", {})
    username = creds.get("username")
    password = creds.get("password")

    if not username or password is None:
        return {"diff": "", "committed": False, "error": f"No credentials for {device_id}", "duration_ms": 0}

    start = time.monotonic()
    try:
        driver_name = device["driver"]
        driver_cls = get_network_driver(driver_name)
        opts = _build_optional_args(device)
        opts["timeout"] = NAPALM_TIMEOUT

        logger.info("[%s] config_session: connecting driver=%s host=%s commit=%s",
                    device_id, driver_name, device["hostname"], commit)

        with driver_cls(
            hostname=device["hostname"],
            username=username,
            password=password,
            optional_args=opts,
            timeout=NAPALM_TIMEOUT,
        ) as dev:
            dev.open()

            # Load candidate config via merge
            logger.debug("[%s] config_session: loading merge candidate (%d bytes)",
                         device_id, len(candidate))
            dev.load_merge_candidate(config=candidate)

            # Compare (dry-run)
            logger.debug("[%s] config_session: comparing config", device_id)
            diff = dev.compare_config()

            committed = False
            if commit and diff.strip():
                logger.info("[%s] config_session: committing changes", device_id)
                dev.commit_config()
                committed = True
                logger.info("[%s] config_session: commit OK", device_id)
            elif commit and not diff.strip():
                logger.info("[%s] config_session: no changes to commit", device_id)

            dev.close()

        duration_ms = round((time.monotonic() - start) * 1000, 2)

        # ── Prometheus metrics (success) ───────────────────
        operation = "config_commit" if commit else "config_compare"
        netpulse_napalm_operations_total.labels(
            device_id=device_id,
            operation=operation,
            status="success",
        ).inc()
        netpulse_napalm_duration_seconds.labels(
            device_id=device_id,
            operation=operation,
        ).observe(duration_ms / 1000.0)
        # ─────────────────────────────────────────────────────

        logger.info("[%s] config_session: done diff_lines=%d committed=%s (%.0fms)",
                    device_id, diff.count("\n") if diff else 0, committed, duration_ms)
        return {"diff": diff, "committed": committed, "error": None, "duration_ms": duration_ms}

    except Exception as e:
        duration_ms = round((time.monotonic() - start) * 1000, 2)
        error_type = _classify_error(e)

        # ── Prometheus metrics (error) ─────────────────────
        operation = "config_commit" if commit else "config_compare"
        netpulse_napalm_operations_total.labels(
            device_id=device_id,
            operation=operation,
            status=error_type,
        ).inc()
        netpulse_napalm_duration_seconds.labels(
            device_id=device_id,
            operation=operation,
        ).observe(duration_ms / 1000.0)
        # ─────────────────────────────────────────────────────

        logger.error("[%s] config_session: FAILED error_type=%s: %s", device_id, error_type, e)
        return {"diff": "", "committed": False, "error": str(e), "duration_ms": duration_ms, "error_type": error_type}


def _compare_via_difflib(device_id: str, running: str, candidate: str) -> str:
    """Fallback: compara dos configuraciones usando difflib unificado."""
    import difflib
    candidate_fixed = candidate if candidate.endswith("\n") else candidate + "\n"
    diff_lines = list(
        difflib.unified_diff(
            running.splitlines(keepends=True),
            candidate_fixed.splitlines(keepends=True),
            fromfile=f"{device_id}-running",
            tofile=f"{device_id}-candidate",
        )
    )
    return "".join(diff_lines)


def compare_config(device_id: str, candidate: str) -> NapalmResult:
    """Dry-run: carga la configuración candidata, compara con running y descarta.

    Usa NAPALM load_merge_candidate + compare_config + discard_config.
    Si el driver no soporta compare_config, usa fallback con difflib.
    NO modifica la configuración del dispositivo.
    """
    d = _find_device(device_id)
    if not d:
        r = NapalmResult(device_id)
        r.set_error("driver_error", f"Device {device_id} not found")
        return r

    result = NapalmResult(device_id)
    start = time.monotonic()

    # Legacy/telnet: dry-run por netmiko (difflib contra running-config)
    if _netmiko_direct(d):
        return _netmiko_compare_config(d, candidate)

    # Try NAPALM full flow: load → compare → discard
    session = _execute_config_session(d, candidate, commit=False)

    if session["error"] is None:
        result.success = True
        result.data = session["diff"]
        result.duration_ms = session["duration_ms"]
        logger.info("[%s] compare_config: NAPALM dry-run OK", device_id)
        return result

    # NAPALM compare_config failed → fallback to difflib
    logger.warning("[%s] compare_config: NAPALM failed (%s), using difflib fallback",
                   device_id, session["error"])

    # Get running config for diff fallback
    running_result = _execute(d, "get_config", retrieve="running")
    if running_result.success and running_result.data:
        running = running_result.data.get("running", "") if isinstance(running_result.data, dict) else str(running_result.data)
        diff_text = _compare_via_difflib(device_id, running, candidate)
        result.success = True
        result.data = diff_text
        result.duration_ms = round((time.monotonic() - start) * 1000, 2)
        logger.info("[%s] compare_config: difflib fallback OK (%d diff lines)",
                    device_id, diff_text.count("\n"))
    else:
        result.set_error(
            session.get("error_type", "driver_error"),
            f"Compare failed and fallback also failed: {session['error']}"
        )
        result.duration_ms = round((time.monotonic() - start) * 1000, 2)

    return result


def deploy_config(device_id: str, candidate: str) -> dict:
    """Flujo completo de deploy con dry-run, commit y rollback automático.

    Flujo:
    1. Backup pre-deploy de la running config
    2. Load merge candidate + compare (dry-run)
    3. Si hay diff, commit
    4. Si commit falla → auto-rollback
    5. Backup post-deploy (auditoría)

    Retorna dict: {device_id, backup_file, diff, committed, error, duration_ms}
    """
    result = {
        "device_id": device_id,
        "backup_file": None,
        "diff": "",
        "committed": False,
        "error": None,
        "duration_ms": 0,
    }

    d = _find_device(device_id)
    if not d:
        result["error"] = f"Device {device_id} not found"
        return result

    start = time.monotonic()

    # Step 1: Backup pre-deploy
    backup_result = backup_config(device_id)
    if not backup_result.success:
        result["error"] = f"Pre-deploy backup failed: {backup_result.error}"
        result["duration_ms"] = round((time.monotonic() - start) * 1000, 2)
        logger.error("[%s] deploy_config: pre-deploy backup FAILED: %s", device_id, backup_result.error)
        return result
    result["backup_file"] = (backup_result.data.get("file")
                             if isinstance(backup_result.data, dict) else str(backup_result.data))

    # Step 2 & 3: Load + Compare + Commit
    if _netmiko_direct(d):
        session = _netmiko_deploy_session(d, candidate)
    else:
        session = _execute_config_session(d, candidate, commit=True)

    result["diff"] = session["diff"]

    if session["error"] is not None:
        # Commit (or compare) failed
        result["error"] = session["error"]
        result["duration_ms"] = session["duration_ms"]

        # Auto-rollback if we attempted commit
        if session.get("committed") is False and "commit" in session["error"].lower():
            # The error happened during commit — try to rollback
            logger.error("[%s] deploy_config: commit failed, attempting auto-rollback", device_id)
            rb_result = rollback_config(device_id)
            if rb_result.success:
                result["error"] += " | Rollback: OK"
            else:
                result["error"] += f" | Rollback: FAILED ({rb_result.error})"

        result["duration_ms"] = round((time.monotonic() - start) * 1000, 2)
        return result

    result["committed"] = session["committed"]

    if not session["committed"]:
        # No changes detected
        result["error"] = "No changes detected"
    else:
        # Step 5: Post-deploy audit backup
        audit_result = backup_config(device_id)
        if audit_result.success:
            logger.info("[%s] deploy_config: post-deploy audit backup OK", device_id)

    result["duration_ms"] = round((time.monotonic() - start) * 1000, 2)
    logger.info("[%s] deploy_config: complete committed=%s diff_lines=%d (%.0fms)",
                device_id, result["committed"],
                session["diff"].count("\n") if session["diff"] else 0,
                result["duration_ms"])
    return result


def rollback_config(device_id: str) -> NapalmResult:
    """Rollback: revierte a la configuración anterior al último commit.

    Primero intenta NAPALM rollback() (nativo).
    Si falla, intenta discard_config() (descarta candidato pendiente).
    Si todo falla, busca el último backup en disco y lo despliega.
    """
    d = _find_device(device_id)
    if not d:
        r = NapalmResult(device_id)
        r.set_error("driver_error", f"Device {device_id} not found")
        return r

    # Legacy/telnet: sin rollback transaccional NAPALM → restaurar desde backup
    if _netmiko_direct(d):
        logger.info("[%s] rollback_config: netmiko (best-effort) → restaurando último backup",
                    device_id)
        return _restore_from_backup(device_id, d)

    # Attempt 1: NAPALM rollback() — reverts last commit
    result = _execute(d, "rollback")
    if result.success:
        logger.info("[%s] rollback_config: NAPALM rollback() OK (%.0fms)", device_id, result.duration_ms)
        return result

    logger.warning("[%s] rollback_config: NAPALM rollback() failed: %s — trying discard_config",
                   device_id, result.error)

    # Attempt 2: discard_config() — discards any loaded (uncommitted) candidate
    result = _execute(d, "discard_config")
    if result.success:
        logger.info("[%s] rollback_config: discard_config OK (uncommitted candidate discarded)", device_id)
        return result

    logger.warning("[%s] rollback_config: discard_config also failed: %s — trying backup restore",
                   device_id, result.error)

    return _restore_from_backup(device_id, d)


def _restore_from_backup(device_id: str, device: dict) -> NapalmResult:
    """Restaura la configuración desde el último backup en disco (best-effort)."""
    result = NapalmResult(device_id)
    try:
        backups = sorted(BACKUP_DIR.glob(f"{device_id}_*.cfg"), reverse=True)
        if backups:
            last_backup = backups[0]
            logger.info("[%s] rollback_config: restoring from backup %s", device_id, last_backup.name)
            candidate = last_backup.read_text()
            deploy_result = deploy_config(device_id, candidate)

            result.success = deploy_result["committed"]
            result.data = {
                "restored_from": str(last_backup),
                "committed": deploy_result["committed"],
                "rollback_mode": "best-effort",
            }
            if not deploy_result["committed"]:
                result.set_error("command_error", deploy_result.get("error", "Restore deploy failed"))
            else:
                result.error = None
                result.error_type = None
                result.error_raw = None
            logger.info("[%s] rollback_config: backup restore committed=%s",
                        device_id, deploy_result["committed"])
        else:
            result.set_error("driver_error", "No backups found to restore from")
            logger.error("[%s] rollback_config: no backups in %s", device_id, BACKUP_DIR)
    except Exception as e:
        result.set_error(_classify_error(e), f"Backup restore failed: {e}")
        logger.error("[%s] rollback_config: backup restore exception: %s", device_id, e)

    return result


# ── Command Execution (CLI) ─────────────────────────────────


_SAFE_COMMAND_PREFIXES = [
    "show", "display", "get", "ping", "traceroute", "trace",
    "tracer", "tracert", "sh",  # sh = abbreviation for show (Cisco)
    "/",  # MikroTik RouterOS API commands (ej: /system resource print)
]

_BLOCKED_COMMAND_KEYWORDS = [
    "configure", "conf t", "write", "copy", "delete",
    "erase", "reload", "format", "rm ", "remove",
    "install", "upgrade", "downgrade", "unmount",
    "mkfs", "dd ", "fdisk", "parted", "reboot",
    "shutdown", "halt", "poweroff", "init ",
    "commit",  # JunOS commit / save changes
    "request system",  # JunOS operational changes
    "restart",  # Service restart
]


def _validate_commands(commands: list[str], device_driver: str) -> tuple[bool, Optional[str]]:
    """Valida que los comandos sean seguros (solo lectura/diagnóstico).

    Retorna (is_valid, error_message).
    """
    for cmd in commands:
        cmd_lower = cmd.strip().lower()

        # Skip empty commands
        if not cmd_lower:
            continue

        # Check blocked keywords first (more specific)
        for blocked in _BLOCKED_COMMAND_KEYWORDS:
            if blocked in cmd_lower:
                return False, f"Comando bloqueado: '{cmd}' contiene palabra clave peligrosa '{blocked}'"

        # Check allowed prefixes
        allowed = False
        for prefix in _SAFE_COMMAND_PREFIXES:
            if cmd_lower.startswith(prefix):
                allowed = True
                break

        if not allowed:
            return False, (
                f"Comando no permitido: '{cmd}' no empieza con un prefijo seguro. "
                f"Prefijos permitidos: {', '.join(_SAFE_COMMAND_PREFIXES)}"
            )

    return True, None


def _execute_netmiko_commands(device_id: str, commands: list[str], device: dict) -> NapalmResult:
    """Ejecuta comandos CLI vía netmiko (legacy/telnet).

    Un comando por sesión (los equipos viejos no permiten colas).
    Mantiene la estructura {command, output, error} y éxito si la
    conexión fue OK (los errores de comando van por comando).
    """
    result = NapalmResult(device_id)
    outputs = []
    connection_errors = 0
    for cmd in commands:
        res = netmiko_svc.execute_command(device, cmd)
        error = res.get("error")
        if error and _is_connection_like(error):
            connection_errors += 1
        outputs.append({"command": cmd, "output": res.get("output", ""), "error": error})

    result.data = outputs
    if connection_errors == len(commands):
        result.set_error("connection_error", outputs[0]["error"])
    else:
        result.success = True
        logger.info("[%s] run_commands → OK via netmiko (%d comandos)",
                    device_id, len(commands))
    return result


def _is_connection_like(error: str) -> bool:
    """Heurística: ¿el error es de conexión/auth y no del comando en sí?"""
    if not error:
        return False
    low = error.lower()
    return any(k in low for k in (
        "connection", "timed out", "timeout", "refused", "unreachable",
        "authentication", "unable to connect", "socket", "eof",
    ))


def _execute_ros_commands(device_id: str, commands: list[str], device: dict) -> NapalmResult:
    """Ejecuta comandos en MikroTik RouterOS vía API librouteros."""
    result = NapalmResult(device_id)
    try:
        from librouteros import connect
        host = device["hostname"]
        port = device.get("port", 8728)
        creds = device.get("credentials", {})
        username = creds.get("username", "admin")
        password = creds.get("password", "")

        api = connect(username=username, password=password, host=host, port=port)
        outputs = []
        for cmd in commands:
            try:
                # Normalize ROS command: replace spaces with / for path
                ros_cmd = cmd.strip()
                if ros_cmd.startswith("/"):
                    # Convert "/ip address print" → "/ip/address/print"
                    parts = ros_cmd[1:].split()
                    ros_cmd = "/" + "/".join(parts)
                response = list(api(ros_cmd))
                if response:
                    # Format each response item as a dict
                    formatted = [dict(r) for r in response]
                    out = "\n".join(f"{k}: {v}" for item in formatted for k, v in item.items())
                    outputs.append({"command": cmd, "output": out, "error": None})
                else:
                    outputs.append({"command": cmd, "output": "(comando ejecutado, sin datos)", "error": None})
            except Exception as e:
                outputs.append({"command": cmd, "output": "", "error": str(e)})
        api.close()
        result.data = outputs
        result.success = True
    except Exception as e:
        result.set_error("connection_error", f"ROS API error: {e}")
        result.data = [{"command": cmd, "output": "", "error": str(e)} for cmd in commands]
    return result


def run_commands(device_id: str, commands: list[str]) -> NapalmResult:
    """Ejecuta comandos CLI en el dispositivo vía NAPALM.

    Usa NAPALM's cli() que ejecuta comandos arbitrarios vía SSH/API.
    Incluye filtro de seguridad que bloquea comandos de escritura/configuración.
    Compatible con todos los drivers (ios, eos, ros, junos, nxos, iosxr, linux).

    Args:
        device_id: ID del dispositivo en inventario.
        commands: Lista de comandos a ejecutar (solo lectura/diagnóstico).

    Returns:
        NapalmResult con data = [
            {"command": str, "output": str, "error": str|None},
            ...
        ]
    """
    d = _find_device(device_id)
    if not d:
        r = NapalmResult(device_id)
        r.set_error("driver_error", f"Device {device_id} not found")
        return r

    if not commands:
        r = NapalmResult(device_id)
        r.set_error("command_error", "No commands provided — lista de comandos vacía")
        return r

    # Validate commands
    is_valid, error_msg = _validate_commands(commands, d.get("driver", ""))
    if not is_valid:
        r = NapalmResult(device_id)
        r.set_error("command_error", error_msg)
        logger.warning("[%s] run_commands: BLOQUEADO — %s", device_id, error_msg)
        return r

    logger.info("[%s] run_commands: ejecutando %d comandos — %s",
                device_id, len(commands), commands[:3])

    # ROS driver: use librouteros API instead of NAPALM cli()
    if d.get("driver") == "ros":
        return _execute_ros_commands(device_id, commands, d)

    # SNMP devices: no CLI execution
    if d.get("protocol") == "snmp" or d.get("driver") == "snmp":
        r = NapalmResult(device_id)
        r.data = [
            {
                "command": cmd,
                "output": "",
                "error": f"Dispositivo '{device_id}' gestionado vía SNMP. La ejecución de comandos CLI interactivos no está soportada vía SNMP.",
            }
            for cmd in commands
        ]
        r.success = True
        return r

    # Legacy/telnet: ejecución vía netmiko (un comando por sesión)
    if _netmiko_direct(d):
        return _execute_netmiko_commands(device_id, commands, d)

    # Execute via NAPALM cli() — passes commands as list
    result = _execute(d, "cli", commands=commands, retry=False)

    # If NAPALM cli() returned a dict {cmd: output}, normalize to list of dicts
    if result.success and isinstance(result.data, dict):
        normalized = []
        for cmd in commands:
            output = result.data.get(cmd, "")
            # NAPALM may return output as string or nested dict with "error"
            if isinstance(output, dict) and "error" in output:
                normalized.append({
                    "command": cmd,
                    "output": "",
                    "error": output["error"],
                })
            else:
                normalized.append({
                    "command": cmd,
                    "output": str(output),
                    "error": None,
                })
        result.data = normalized
    elif result.success and isinstance(result.data, str):
        # Single command returned as string
        normalized = [{
            "command": commands[0],
            "output": result.data,
            "error": None,
        }]
        result.data = normalized
    elif not result.success:
        # Error already set by _execute
        # Provide empty results structure for consistency
        result.data = [
            {"command": cmd, "output": "", "error": result.error}
            for cmd in commands
        ]

    # Fallback netmiko para legacy SSH cuando NAPALM cli() no es soportado
    if not result.success and netmiko_svc.is_legacy_device(d):
        logger.info("[%s] run_commands: NAPALM cli() falló (%s) — reintentando via netmiko",
                    device_id, result.error)
        return _execute_netmiko_commands(device_id, commands, d)

    return result


def ping(device_id: str, target: str) -> NapalmResult:
    d = _find_device(device_id)
    if not d:
        r = NapalmResult(device_id)
        r.set_error("driver_error", f"Device {device_id} not found")
        return r

    # SNMP devices: ICMP ping from server to target / device
    if d.get("protocol") == "snmp" or d.get("driver") == "snmp":
        result = NapalmResult(device_id)
        import subprocess, sys
        dest = target or d.get("hostname", "")
        cmd = ["ping", "-c", "3", "-t", "2", dest] if sys.platform == "darwin" else ["ping", "-c", "3", "-W", "2", dest]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            out = proc.stdout
            tx, rx = 3, 0
            rtt_min, rtt_avg, rtt_max = 0.0, 0.0, 0.0
            for line in out.splitlines():
                if "packets transmitted" in line:
                    parts = line.split()
                    if len(parts) >= 4:
                        tx = int(parts[0])
                        rx = int(parts[3])
                elif "round-trip" in line or "rtt" in line:
                    try:
                        vals = line.split("=")[1].strip().split()[0].split("/")
                        rtt_min = float(vals[0])
                        rtt_avg = float(vals[1])
                        rtt_max = float(vals[2])
                    except Exception:
                        pass
            result.data = {
                "success": {
                    "probes_sent": tx,
                    "packet_loss": round((tx - rx) / tx * 100) if tx else 100,
                    "rtt_min": rtt_min,
                    "rtt_avg": rtt_avg,
                    "rtt_max": rtt_max,
                }
            }
            result.success = rx > 0
            return result
        except Exception as e:
            result.set_error("ping_error", str(e))
            return result

    # Legacy/telnet: ping vía netmiko (CLI del equipo, por familia)
    if _netmiko_direct(d):
        try:
            out = netmiko_svc.ping(d, target)
        except Exception as e:
            result = NapalmResult(device_id)
            result.set_error("connection_error", f"netmiko ping: {e}")
            return result
        stats = out.get("success", {})
        result = NapalmResult(device_id)
        result.data = {"success": stats}
        result.success = bool(stats.get("packet_loss", 100) < 100)
        if out.get("raw"):
            result.data["raw"] = out["raw"]
        return result

    result = _execute(d, "ping", destination=target, retry=False)
    # NAPALM devuelve {"error": "..."} cuando el comando no es soportado
    # (ej. FRRouting no soporta la sintaxis IOS de ping)
    if result.success and isinstance(result.data, dict) and "error" in result.data:
        result.success = False
        result.set_error("command_error", result.data["error"])
    return result


def get_resources(device_id: str) -> NapalmResult:
    """Obtiene recursos del dispositivo: CPU, memoria, disco."""
    result = NapalmResult(device_id)
    d = _find_device(device_id)
    if not d:
        result.set_error("driver_error", f"Device {device_id} not found")
        return result
    if d.get("protocol") == "snmp" or d.get("driver") == "snmp":
        try:
            from app.services.collectors import snmp_svc
            creds = d.get("credentials") or {}
            community = creds.get("community") or d.get("community") or d.get("snmp_ro") or "public"
            snmp_data = snmp_svc.poll_device_snmp(d["hostname"], community=community, timeout=3, driver=d.get("driver", ""))

            # Safe CPU parsing
            cpu_raw = snmp_data.get("cpu", 0)
            try:
                cpu = float(str(cpu_raw).split()[0])
            except Exception:
                cpu = 0.0

            # Memory handling (HP ProCurve returns mem_total, mem_free, mem_alloc in bytes)
            if "mem_total" in snmp_data:
                try:
                    total_bytes = int(snmp_data["mem_total"])
                    free_bytes = int(snmp_data.get("mem_free", 0))
                    used_bytes = int(snmp_data.get("mem_alloc", total_bytes - free_bytes))
                except Exception:
                    total_bytes, used_bytes, free_bytes = 100, 50, 50
            else:
                mem_raw = snmp_data.get("mem", 0)
                try:
                    used_bytes = int(str(mem_raw).split()[0])
                    total_bytes = 100
                    free_bytes = max(0, 100 - used_bytes)
                except Exception:
                    total_bytes, used_bytes, free_bytes = 100, 50, 50

            result.data = {
                "cpu": cpu,
                "memory_total": total_bytes,
                "memory_used": used_bytes,
                "memory_free": free_bytes,
                "hdd_total": 100,
                "hdd_free": 50,
                "hdd_used": 50,
                "uptime": snmp_data.get("uptime", ""),
            }
            result.success = True
            return result
        except Exception:
            result.data = {"cpu": 0, "memory_total": 0, "memory_used": 0, "hdd_total": 0, "hdd_free": 0}
            result.success = True
            return result
    if d.get("driver") == "ros":
        try:
            from librouteros import connect
            creds = d.get("credentials", {})
            api = connect(username=creds.get("username","admin"), password=creds.get("password",""),
                         host=d["hostname"], port=d.get("port",8728), timeout=10.0)
            sys_res = list(api("/system/resource/print"))
            if sys_res:
                r = dict(sys_res[0])
                total_mem = int(r.get("total-memory", 0))
                free_mem = int(r.get("free-memory", 0))
                total_hdd = int(r.get("total-hdd-space", 0))
                free_hdd = int(r.get("free-hdd-space", 0))
                result.data = {
                    "cpu": r.get("cpu-load", 0),
                    "cpu_count": r.get("cpu-count", 0),
                    "cpu_frequency": r.get("cpu-frequency", 0),
                    "memory_total": total_mem,
                    "memory_used": total_mem - free_mem,
                    "memory_free": free_mem,
                    "hdd_total": total_hdd,
                    "hdd_free": free_hdd,
                    "hdd_used": total_hdd - free_hdd,
                    "uptime": r.get("uptime", ""),
                    "board_name": r.get("board-name", ""),
                    "version": r.get("version", ""),
                    "architecture": r.get("architecture-name", ""),
                    "platform": r.get("platform", ""),
                }
                result.success = True
            api.close()
        except Exception:
            result.data = {"cpu": 0, "memory_total": 0, "memory_used": 0, "hdd_total": 0, "hdd_free": 0}
            result.success = True
    elif d.get("driver") == "eos":
        try:
            driver_cls = get_network_driver("eos")
            opts = _build_optional_args(d)
            with driver_cls(hostname=d["hostname"],username=d["credentials"]["username"],
                          password=d["credentials"]["password"],optional_args=opts) as dev:
                dev.open()
                f = dev.get_facts()
                output = dev.cli(["show system resource"])	
                dev.close()
                lines = output.get("show system resource","")
                import re
                cpu_m = re.search(r'CPU busy\s*:\s*(\d+)', lines)
                mem_m = re.search(r'Memory used\s*:\s*(\d+)', lines)
                mem_t = re.search(r'Memory total\s*:\s*(\d+)', lines)
                free_m = re.search(r'Memory free\s*:\s*(\d+)', lines)
                result.data = {
                    "cpu": int(cpu_m.group(1)) if cpu_m else 0,
                    "cpu_count": f.get("num_cpus", 0) or 0,
                    "memory_total": int(mem_t.group(1)) if mem_t else 0,
                    "memory_used": int(mem_m.group(1)) if mem_m else 0,
                    "memory_free_percent": int(free_m.group(1)) if free_m else 50,
                    "uptime": f.get("uptime", ""),
                }
                result.success = True
        except Exception:
            # EOS resources no disponible (SSL legacy) — return defaults
            result.data = {"cpu": 0, "memory_total": 0, "memory_used": 0, "hdd_total": 0, "hdd_free": 0}
            result.success = True
    else:
        # Intentar NAPALM get_environment (no soportado por todos) y luego netmiko
        napalm_ok = False
        try:
            if not netmiko_svc.is_legacy_device(d):
                driver_cls = get_network_driver(_resolve_driver(d["driver"]))
                opts = _build_optional_args(d)
                with driver_cls(hostname=d["hostname"], username=d["credentials"]["username"],
                              password=d["credentials"]["password"], optional_args=opts) as dev:
                    dev.open()
                    env = dev.get_environment()
                    dev.close()
                cpu = env.get("cpu", 0) or 0
                if isinstance(cpu, dict):
                    vals = [v for v in cpu.values() if isinstance(v, (int, float))]
                    cpu = int(sum(vals) // len(vals)) if vals else 0
                mem = env.get("memory", {})
                result.data = {
                    "cpu": cpu,
                    "memory_total": mem.get("available_ram", 0) or 0,
                    "memory_used": mem.get("used_ram", 0) or 0,
                    "hdd_total": 0,
                    "hdd_free": 0,
                }
                result.success = True
                napalm_ok = True
        except Exception as e:
            logger.info("[%s] get_environment NAPALM no disponible (%s) — netmiko fallback", device_id, str(e)[:60])

        # Fallback netmiko para legacy (telnet o drivers antiguos)
        if not napalm_ok and netmiko_svc.is_legacy_device(d):
            logger.info("[%s] intentando resources vía netmiko...", device_id)
            resources = netmiko_svc.get_resources(d)
            if resources:
                result.data = resources
                result.data["_note"] = "Datos vía netmiko (fallback legacy)"
                result.success = True
            else:
                result.data = {"cpu": 0, "memory_total": 0, "memory_used": 0, "hdd_total": 0, "hdd_free": 0}
                result.success = True
        elif not napalm_ok:
            result.data = {"cpu": 0, "memory_total": 0, "memory_used": 0, "hdd_total": 0, "hdd_free": 0}
            result.success = True
    return result


def check_device(device_id: str) -> NapalmResult:
    """Verifica conectividad básica."""
    d = _find_device(device_id)
    if not d:
        r = NapalmResult(device_id)
        r.set_error("driver_error", f"Device {device_id} not found")
        return r
    return _execute(d, "get_facts")


# ── Bulk Operations (via Nornir en Fase 1.5) ────────────────

def bulk_get_facts(device_ids: list[str] = None) -> list[NapalmResult]:
    """Ejecuta get_facts en todos los dispositivos (o en los especificados)."""
    if device_ids is None:
        devices = _load_devices()
        device_ids = [d["id"] for d in devices]
    return [get_facts(did) for did in device_ids]


def list_devices() -> list[dict]:
    """Lista todos los dispositivos (sin passwords)."""
    devices = _load_devices()
    return [
        {
            "id": d["id"],
            "hostname": d["hostname"],
            "port": d.get("port", 22),
            "driver": d["driver"],
            "type": d.get("type", "router"),
            "tags": d.get("tags", []),
            "description": d.get("description", ""),
        }
        for d in devices
    ]


def bash_command(device_id: str, cmd: str) -> NapalmResult:
    """Ejecuta un comando bash directo via Docker exec (para FRR)."""
    result = NapalmResult(device_id)
    d = _find_device(device_id)
    if not d:
        result.set_error("driver_error", f"Device {device_id} not found")
        return result

    import subprocess
    try:
        docker_name = f"netpulse-{device_id.split('-')[0]}"
        proc = subprocess.run(
            ["docker", "exec", docker_name, "sh", "-c", cmd],
            capture_output=True, text=True, timeout=20,
        )
        if proc.returncode != 0 and not proc.stdout:
            # Try with full container name
            proc = subprocess.run(
                ["docker", "exec", device_id.replace("_", "-"), "sh", "-c", cmd],
                capture_output=True, text=True, timeout=20,
            )
        result.data = proc.stdout or proc.stderr
        result.success = True
    except Exception as e:
        result.set_error("connection_error", str(e))
        logger.warning("[%s] bash_command falló: %s", device_id, e)

    return result
