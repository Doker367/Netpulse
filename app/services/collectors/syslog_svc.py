"""NetPulse — Colector de Syslog (UDP → Loki).

Servidor syslog UDP que escucha en 0.0.0.0:514, parsea mensajes
RFC3164 básicos y los reenvía a Loki (http://localhost:3100)
mediante el endpoint ``/loki/api/v1/push``.

El servidor corre en un thread daemon para no bloquear el proceso
principal de la API. El envío a Loki usa ``urllib.request`` con un
timeout corto para no bloquear el loop de recepción.
"""

import json
import logging
import os
import re
import socket
import threading
import time
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

# Cabecera RFC3164: "Mmm dd hh:mm:ss HOST ..." (el día puede llevar
# espacio de relleno, ej. "Feb  2", por eso se usan \s+)
RFC3164_HEADER_RE = re.compile(
    r"^(?P<month>\w{3})\s+(?P<day>\d{1,2})\s+"
    r"(?P<time>\d{2}:\d{2}:\d{2})\s+"
    r"(?P<host>\S+)\s+(?P<rest>.*)$"
)

LOKI_URL = "http://localhost:3100/loki/api/v1/push"
LOKI_TIMEOUT = 2  # segundos — envío corto y sin bloqueo

DEFAULT_BIND_HOST = "0.0.0.0"
# 514 es el estándar de syslog pero requiere root en Linux.
# Por defecto usamos 5514 (alto) para que funcione sin privilegios;
# en producción se puede cambiar a 514 con root o redirigir syslog.
DEFAULT_BIND_PORT = int(os.getenv("NETPULSE_SYSLOG_PORT", "5514"))

# Nombres de severidad RFC5424 (PRI % 8 → índice)
SEVERITY_NAMES = [
    "emerg", "alert", "crit", "err", "warning", "notice", "info", "debug",
]

# Estado global del colector
_sock: Optional[socket.socket] = None
_thread: Optional[threading.Thread] = None
_running = threading.Event()


def parse_rfc3164(raw: bytes) -> dict:
    """Parsea un mensaje syslog RFC3164 básico.

    Formato típico: ``<PRI>Mmm dd hh:mm:ss HOST TAG[pid]: CONTENT``

    Returns:
        Dict con las claves ``severity``, ``facility``, ``host``,
        ``tag`` y ``content``. Si el mensaje no tiene priority, se
        devuelve el contenido completo con valores por defecto.
    """
    line = raw.decode("utf-8", errors="replace").strip("\x00").strip()
    result = {
        "severity": "info",
        "facility": 0,
        "host": "unknown",
        "tag": "syslog",
        "content": line,
    }

    if not line.startswith("<"):
        return result

    end = line.find(">")
    if end <= 1 or not line[1:end].isdigit():
        return result

    # PRI = facility * 8 + severity
    pri = int(line[1:end])
    result["facility"] = pri // 8
    sev = pri % 8
    result["severity"] = SEVERITY_NAMES[sev] if sev < len(SEVERITY_NAMES) else "info"

    rest = line[end + 1:]

    # Cabecera RFC3164 completa: "Mmm dd hh:mm:ss HOST TAG: CONTENT"
    m = RFC3164_HEADER_RE.match(rest)
    if m:
        result["host"] = m.group("host")
        tag_content = m.group("rest")
    else:
        # Sin cabecera de fecha: "HOST TAG: CONTENT" o directamente el contenido
        parts = rest.split(None, 1)
        if len(parts) >= 2:
            result["host"] = parts[0]
            tag_content = parts[1]
        else:
            tag_content = rest

    # Separar TAG (opcionalmente con [pid]) del contenido
    if ":" in tag_content:
        tag, content = tag_content.split(":", 1)
        result["tag"] = tag.strip() or result["tag"]
        result["content"] = content.strip() or result["tag"]
    else:
        result["content"] = tag_content.strip()

    return result


def push_to_loki(stream: dict, message: str, timestamp_ns: Optional[int] = None) -> bool:
    """Envía un mensaje a Loki vía HTTP POST.

    Body según la API de Loki::

        {"streams": [{"stream": {...}, "values": [["<ts_ns>", "<msg>"]]}]}

    Args:
        stream: Labels del stream (container, source, severity, ...).
        message: Texto del mensaje (valor de la línea de log).
        timestamp_ns: Timestamp en nanosegundos (str); si es None se
            usa ``time.time_ns()``.

    Returns:
        True si Loki respondió con status 2xx (el push devuelve 204).
    """
    ts_ns = str(timestamp_ns if timestamp_ns is not None else time.time_ns())
    payload = {"streams": [{"stream": stream, "values": [[ts_ns, message]]}]}

    req = urllib.request.Request(
        LOKI_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=LOKI_TIMEOUT) as resp:
            ok = 200 <= resp.status < 300
            if not ok:
                logger.warning("Loki respondió status %d", resp.status)
            return ok
    except Exception as exc:
        logger.warning("Error enviando syslog a Loki: %s", exc)
        return False


def _handle_datagram(data: bytes, source_host: str) -> None:
    """Parsea un datagrama syslog y lo reenvía a Loki.

    El envío es síncrono pero con timeout corto (2s) — suficiente
    para no bloquear el loop de recepción UDP.
    """
    parsed = parse_rfc3164(data)
    stream = {
        "container": "syslog",
        "source": parsed["host"] if parsed["host"] != "unknown" else source_host,
        "severity": parsed["severity"],
        "facility": str(parsed["facility"]),
    }
    message = f"{parsed['tag']}: {parsed['content']}"
    push_to_loki(stream, message)


def _serve() -> None:
    """Loop principal del thread: recibe datagramas y los reenvía."""
    logger.info(
        "Colector syslog escuchando en %s:%d → %s",
        DEFAULT_BIND_HOST, DEFAULT_BIND_PORT, LOKI_URL,
    )
    while _running.is_set():
        try:
            data, addr = _sock.recvfrom(65535)  # type: ignore[union-attr]
        except OSError:
            # Socket cerrado por stop_syslog_collector() — salir del loop
            if not _running.is_set():
                break
            continue
        try:
            _handle_datagram(data, addr[0])
        except Exception as exc:
            logger.error("Error procesando datagrama de %s: %s", addr[0], exc)


def start_syslog_collector(
    host: str = DEFAULT_BIND_HOST, port: int = DEFAULT_BIND_PORT
) -> bool:
    """Arranca el servidor syslog UDP en un thread daemon.

    Args:
        host: Interfaz a la que enlazar (por defecto 0.0.0.0).
        port: Puerto UDP (por defecto 514 — requiere root en Linux).

    Returns:
        True si el socket se abrió y el thread arrancó correctamente.
    """
    global _sock, _thread

    if _running.is_set():
        logger.info("Colector syslog ya está en ejecución")
        return True

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
    except OSError as exc:
        logger.error(
            "No se pudo abrir %s:%d — %s (¿permisos de root?)", host, port, exc
        )
        return False

    _sock = sock
    _running.set()
    _thread = threading.Thread(target=_serve, name="syslog-collector", daemon=True)
    _thread.start()
    return True


def is_running() -> bool:
    """True si el colector syslog está activo."""
    return _running.is_set()


def stop_syslog_collector() -> bool:
    """Detiene el colector syslog.

    Cierra el socket (desbloquea ``recvfrom``) y espera a que el
    thread daemon termine.

    Returns:
        True siempre; idempotente.
    """
    global _sock, _thread

    _running.clear()
    if _sock is not None:
        try:
            _sock.close()
        except OSError:
            pass
        _sock = None
    if _thread is not None:
        _thread.join(timeout=3)
        _thread = None

    logger.info("Colector syslog detenido")
    return True
