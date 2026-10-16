"""NetPulse — Colector de Syslog (UDP → Loki) con batching.

Servidor syslog UDP que parsea mensajes RFC3164 básicos y los reenvía a
Loki (``/loki/api/v1/push``). Para no colapsar ante una tormenta de logs,
los mensajes **no** se envían uno a uno: se encolan y un flusher los agrupa
por stream y los empuja en lotes cada ``BATCH_INTERVAL`` (500 ms por
defecto). La cola es acotada; si se llena, se descartan los más nuevos y se
cuenta el descarte.

El envío síncrono directo sigue disponible en ``push_to_loki`` para usos
puntuales (p. ej. telemetría de deception).
"""

import json
import logging
import os
import queue
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

# Batching
BATCH_INTERVAL = float(os.getenv("NETPULSE_SYSLOG_BATCH_INTERVAL", "0.5"))
BATCH_MAX = int(os.getenv("NETPULSE_SYSLOG_BATCH_MAX", "1000"))
QUEUE_MAXSIZE = int(os.getenv("NETPULSE_SYSLOG_QUEUE_MAX", "20000"))

# Nombres de severidad RFC5424 (PRI % 8 → índice)
SEVERITY_NAMES = [
    "emerg", "alert", "crit", "err", "warning", "notice", "info", "debug",
]

# Estado global del colector
_sock: Optional[socket.socket] = None
_thread: Optional[threading.Thread] = None
_flusher: Optional[threading.Thread] = None
_running = threading.Event()

_queue: "queue.Queue[tuple]" = queue.Queue(maxsize=QUEUE_MAXSIZE)
_stats = {"queued": 0, "pushed": 0, "dropped": 0, "loki_errors": 0}
_stats_lock = threading.Lock()


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


# ── Envío a Loki ─────────────────────────────────────────────


def _post_loki(streams: list) -> bool:
    """POST a Loki con uno o varios streams. Body JSON de la API de Loki."""
    payload = {"streams": streams}
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
    except Exception as exc:  # noqa: BLE001
        with _stats_lock:
            _stats["loki_errors"] += 1
        logger.warning("Error enviando a Loki: %s", exc)
        return False


def push_to_loki(stream: dict, message: str, timestamp_ns: Optional[int] = None) -> bool:
    """Envía **un** mensaje a Loki de forma síncrona (uso puntual).

    Para el colector syslog se usa ``enqueue`` (batching).
    """
    ts_ns = str(timestamp_ns if timestamp_ns is not None else time.time_ns())
    return _post_loki([{"stream": stream, "values": [[ts_ns, message]]}])


# ── Batching ─────────────────────────────────────────────────


def enqueue(stream: dict, message: str, timestamp_ns: Optional[int] = None) -> bool:
    """Encola un mensaje para el próximo flush. Devuelve False si se descarta."""
    ts_ns = str(timestamp_ns if timestamp_ns is not None else time.time_ns())
    try:
        _queue.put_nowait((stream, ts_ns, message))
        with _stats_lock:
            _stats["queued"] += 1
        return True
    except queue.Full:
        with _stats_lock:
            _stats["dropped"] += 1
        return False


def _flush_once() -> int:
    """Drena la cola (hasta BATCH_MAX), agrupa por stream y los envía."""
    entries = []
    for _ in range(BATCH_MAX):
        try:
            entries.append(_queue.get_nowait())
        except queue.Empty:
            break
    if not entries:
        return 0

    grouped: dict[str, dict] = {}
    for stream, ts_ns, message in entries:
        key = json.dumps(stream, sort_keys=True, ensure_ascii=False)
        item = grouped.setdefault(key, {"stream": stream, "values": []})
        item["values"].append([ts_ns, message])

    streams = list(grouped.values())
    ok = _post_loki(streams)
    total = sum(len(s["values"]) for s in streams)
    with _stats_lock:
        if ok:
            _stats["pushed"] += total
    return total


def _flush_loop() -> None:
    """Bucle del flusher: agrupa y envía cada BATCH_INTERVAL."""
    while _running.is_set():
        try:
            _flush_once()
        except Exception as exc:  # noqa: BLE001
            logger.debug("flush syslog falló: %s", exc)
        time.sleep(BATCH_INTERVAL)
    # Flush final al detener.
    try:
        _flush_once()
    except Exception:  # noqa: BLE001
        pass


def stats() -> dict:
    """Contadores del colector (cola, enviados, descartados, errores)."""
    with _stats_lock:
        s = dict(_stats)
    s["queue_size"] = _queue.qsize()
    return s


# ── Recepción ────────────────────────────────────────────────


def _handle_datagram(data: bytes, source_host: str) -> None:
    """Parsea un datagrama syslog y lo **encola** para batching."""
    parsed = parse_rfc3164(data)
    stream = {
        "container": "syslog",
        "source": parsed["host"] if parsed["host"] != "unknown" else source_host,
        "severity": parsed["severity"],
        "facility": str(parsed["facility"]),
    }
    message = f"{parsed['tag']}: {parsed['content']}"
    enqueue(stream, message)


def _serve() -> None:
    """Loop principal del thread: recibe datagramas y los encola."""
    logger.info(
        "Colector syslog escuchando en %s:%d → %s (batching %ss)",
        DEFAULT_BIND_HOST, DEFAULT_BIND_PORT, LOKI_URL, BATCH_INTERVAL,
    )
    while _running.is_set():
        try:
            data, addr = _sock.recvfrom(65535)  # type: ignore[union-attr]
        except OSError:
            if not _running.is_set():
                break
            continue
        try:
            _handle_datagram(data, addr[0])
        except Exception as exc:  # noqa: BLE001
            logger.error("Error procesando datagrama de %s: %s", addr[0], exc)


def start_syslog_collector(
    host: str = DEFAULT_BIND_HOST, port: int = DEFAULT_BIND_PORT
) -> bool:
    """Arranca el servidor syslog UDP y el flusher de batching (threads daemon)."""
    global _sock, _thread, _flusher

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
    _flusher = threading.Thread(target=_flush_loop, name="syslog-flusher", daemon=True)
    _flusher.start()
    _thread = threading.Thread(target=_serve, name="syslog-collector", daemon=True)
    _thread.start()
    return True


def is_running() -> bool:
    """True si el colector syslog está activo."""
    return _running.is_set()


def stop_syslog_collector() -> bool:
    """Detiene el colector (y hace un flush final). Idempotente."""
    global _sock, _thread, _flusher

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
    if _flusher is not None:
        _flusher.join(timeout=3)
        _flusher = None

    logger.info("Colector syslog detenido")
    return True
