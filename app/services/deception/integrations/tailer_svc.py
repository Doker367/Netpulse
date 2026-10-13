"""Tailer de logs de honeypots reales (Fase C).

Sigue (tipo ``tail -f``) los archivos de log de Cowrie/OpenCanary, parsea
cada línea JSON y la persiste en la telemetría de deception mediante
``telemetry_svc.emit_sync`` (corre en su propio thread).

Los honeypots reales no conocen los ids de nuestros dispositivos, por lo
que cada fuente se asocia a un ``device_id`` en la configuración.
"""

import json
import logging
import os
import threading
import time
from typing import Callable

from app.core.settings import (
    DECEPTION_COWRIE_DEVICE,
    DECEPTION_COWRIE_LOG,
    DECEPTION_OPENCANARY_DEVICE,
    DECEPTION_OPENCANARY_LOG,
)
from app.services.deception import telemetry_svc
from app.services.deception.integrations import cowrie_svc, opencanary_svc

logger = logging.getLogger(__name__)

POLL_INTERVAL = 1.0

# Definición de fuentes: nombre → (ruta, device_id, parser)
SOURCES: dict[str, dict] = {
    "cowrie": {
        "path": DECEPTION_COWRIE_LOG,
        "device_id": DECEPTION_COWRIE_DEVICE,
        "parser": cowrie_svc.parse,
    },
    "opencanary": {
        "path": DECEPTION_OPENCANARY_LOG,
        "device_id": DECEPTION_OPENCANARY_DEVICE,
        "parser": opencanary_svc.parse,
    },
}

_stop = threading.Event()
_threads: list[threading.Thread] = []
_running = False
_stats = {"lines": 0, "events": 0, "errors": 0}


def is_running() -> bool:
    """True si el tailer está activo."""
    return _running


def _tail_loop(name: str, path: str, device_id: str,
               parser: Callable, stop_event: threading.Event,
               initial_fh=None) -> None:
    """Sigue un archivo y emite los eventos parseados."""
    logger.info("[deception] tailer %s siguiendo %s", name, path)
    fh = initial_fh
    try:
        while not stop_event.is_set():
            if fh is None:
                if not os.path.exists(path):
                    time.sleep(POLL_INTERVAL)
                    continue
                try:
                    fh = open(path, "r", encoding="utf-8", errors="replace")
                    fh.seek(0, os.SEEK_END)  # tail -f: solo eventos nuevos
                except OSError as exc:
                    logger.debug("[deception] no se pudo abrir %s: %s", path, exc)
                    time.sleep(POLL_INTERVAL)
                    continue

            line = fh.readline()
            if not line:
                # ¿Rotación/truncado del archivo?
                try:
                    if os.path.getsize(path) < fh.tell():
                        fh.seek(0)
                except OSError:
                    pass
                time.sleep(POLL_INTERVAL)
                continue

            _stats["lines"] += 1
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                _stats["errors"] += 1
                continue
            try:
                parsed = parser(raw, device_id)
            except Exception as exc:  # noqa: BLE001
                _stats["errors"] += 1
                logger.debug("[deception] parser %s falló: %s", name, exc)
                continue
            if not parsed:
                continue
            try:
                telemetry_svc.emit_sync(**parsed)
                _stats["events"] += 1
            except Exception as exc:  # noqa: BLE001
                _stats["errors"] += 1
                logger.debug("[deception] emit falló (%s): %s", name, exc)
    finally:
        if fh is not None:
            fh.close()


def start() -> dict:
    """Arranca un thread por fuente de log configurada."""
    global _running
    if _running:
        return {"status": "already_running", "sources": list(SOURCES)}

    _stop.clear()
    _threads.clear()
    for name, cfg in SOURCES.items():
        # Abrimos el archivo (si existe) y nos posicionamos al final ANTES
        # de arrancar el thread, para no perder eventos escritos justo
        # después de start() ni releer los antiguos.
        initial_fh = None
        try:
            if os.path.exists(cfg["path"]):
                initial_fh = open(cfg["path"], "r", encoding="utf-8", errors="replace")
                initial_fh.seek(0, os.SEEK_END)
        except OSError:
            initial_fh = None
        thread = threading.Thread(
            target=_tail_loop,
            args=(name, cfg["path"], cfg["device_id"], cfg["parser"], _stop, initial_fh),
            name=f"deception-tail-{name}",
            daemon=True,
        )
        thread.start()
        _threads.append(thread)
    _running = True
    logger.info("[deception] tailer de integraciones iniciado (%s)", list(SOURCES))
    return {"status": "started", "sources": list(SOURCES)}


def stop() -> dict:
    """Detiene el tailer."""
    global _running
    _stop.set()
    for thread in _threads:
        thread.join(timeout=3)
    _threads.clear()
    _running = False
    logger.info("[deception] tailer de integraciones detenido")
    return {"status": "stopped"}


def status() -> dict:
    """Estado y contadores del tailer."""
    return {
        "running": _running,
        "stats": dict(_stats),
        "sources": [
            {"name": name, "path": cfg["path"], "device_id": cfg["device_id"],
             "exists": os.path.exists(cfg["path"])}
            for name, cfg in SOURCES.items()
        ],
    }
