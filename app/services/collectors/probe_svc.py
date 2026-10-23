"""NetPulse — primitiva de sonda compartida (ICMP/TCP).

Solo stdlib: la usan el poller central y el colector remoto. No importa
napalm, netmiko ni psutil para que el colector pueda ejecutarse en un nodo
con dependencias mínimas.

Semántica (contrato de ``ProbeResult``):

- ``ok=True, reachable=True``: la sonda se ejecutó y el destino respondió.
- ``ok=True, reachable=False``: la sonda se ejecutó y el destino no respondió
  (timeout, refused, sin reply ICMP). No prueba que el equipo esté caído.
- ``ok=False``: no se pudo medir (DNS, ``ping`` ausente, permiso, config).
  ``reachable`` es ``None`` y nunca debe contarse como offline.
"""

from __future__ import annotations

import errno
import math
import platform
import re
import socket
import subprocess
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class ProbeResult:
    """Resultado tipado de una sonda."""

    ok: bool
    reachable: bool | None
    latency_ms: float | None
    packet_loss_pct: float | None
    probe_kind: str
    error: str | None = None


_NETWORK_UNREACHABLE = {
    errno.EHOSTUNREACH,
    errno.ENETUNREACH,
    errno.EHOSTDOWN,
    errno.ENETDOWN,
}


def _short_error(text: str, limit: int = 200) -> str:
    text = " ".join(text.strip().split())
    return text[:limit]


def _icmp_probe(host: str, timeout_ms: int) -> ProbeResult:
    """Sonda ICMP de un paquete usando el binario ``ping`` del sistema."""
    timeout_s = max(1, int(math.ceil(timeout_ms / 1000)))
    system = platform.system()
    if system == "Windows":
        cmd = ["ping", "-n", "1", "-w", str(timeout_ms), host]
    elif system == "Darwin":
        cmd = ["ping", "-c", "1", "-t", str(timeout_s), host]
    else:
        cmd = ["ping", "-c", "1", "-W", str(timeout_s), host]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s + 2,
        )
    except FileNotFoundError:
        return ProbeResult(False, None, None, None, "icmp", "ping no disponible")
    except PermissionError:
        return ProbeResult(False, None, None, None, "icmp", "sin permiso ICMP")
    except subprocess.TimeoutExpired:
        # Se intentó medir y no hubo respuesta dentro del timeout.
        return ProbeResult(True, False, None, 100.0, "icmp", "timeout ICMP")
    except OSError as exc:
        return ProbeResult(False, None, None, None, "icmp", _short_error(f"error local: {exc}"))

    out = proc.stdout or ""
    err = proc.stderr or ""
    if proc.returncode != 0:
        lower_err = err.lower()
        if "operation not permitted" in lower_err or "permission denied" in lower_err:
            return ProbeResult(False, None, None, None, "icmp", "sin permiso ICMP")
        if (
            "name or service not known" in lower_err
            or "unknown host" in lower_err
            or "cannot resolve" in lower_err
            or "could not find host" in lower_err
        ):
            return ProbeResult(False, None, None, None, "icmp", _short_error(f"DNS: {err}"))
        return ProbeResult(True, False, None, 100.0, "icmp", "sin respuesta ICMP")

    match = re.search(r"time[=<]\s*([\d.]+)\s*ms", out)
    latency = float(match.group(1)) if match else None
    return ProbeResult(True, True, latency, 0.0, "icmp", None)


def _tcp_probe(host: str, port: int, timeout_ms: int) -> ProbeResult:
    """Sonda TCP: mide el RTT del handshake contra ``host:port``."""
    timeout_s = max(0.1, timeout_ms / 1000.0)
    start = time.monotonic()
    sock = None
    try:
        sock = socket.create_connection((host, port), timeout=timeout_s)
        latency = round(max(0.1, (time.monotonic() - start) * 1000), 1)
        return ProbeResult(True, True, latency, None, "tcp", None)
    except socket.gaierror as exc:
        return ProbeResult(False, None, None, None, "tcp", _short_error(f"DNS: {exc}"))
    except ConnectionRefusedError:
        return ProbeResult(True, False, None, None, "tcp", "conexión rechazada")
    except (TimeoutError, socket.timeout):
        return ProbeResult(True, False, None, None, "tcp", "timeout TCP")
    except OSError as exc:
        if exc.errno in _NETWORK_UNREACHABLE:
            return ProbeResult(True, False, None, None, "tcp", _short_error(f"red inalcanzable: {exc}"))
        return ProbeResult(False, None, None, None, "tcp", _short_error(f"error local: {exc}"))
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def probe(
    host: str,
    port: int = 22,
    protocol: str = "ssh",
    driver: str | None = None,
    timeout_ms: int = 1500,
) -> ProbeResult:
    """Sondea un destino según su protocolo.

    Args:
        host: IP o nombre.
        port: puerto TCP (o 161 para SNMP).
        protocol: ``ssh``, ``telnet`` o ``snmp``.
        driver: driver del inventario; ``snmp`` fuerza ICMP.
        timeout_ms: timeout de la sonda en milisegundos.

    Returns:
        ``ProbeResult``; nunca lanza por un fallo de red esperado.
    """
    if not host or not str(host).strip():
        return ProbeResult(False, None, None, None, "tcp", "host vacío")
    host = str(host).strip()
    protocol = (protocol or "ssh").lower()
    driver = (driver or "").lower()
    try:
        port_number = int(port)
    except (TypeError, ValueError):
        return ProbeResult(False, None, None, None, "tcp", "puerto inválido")
    if protocol == "snmp" or driver == "snmp" or port_number == 161:
        return _icmp_probe(host, timeout_ms)
    return _tcp_probe(host, port_number, timeout_ms)
