"""Watchdog de sites remotos: avisa cuando un colector deja de reportar.

El central ya calcula ``contact = "stale"`` en ``query_svc`` y lo pinta en la
UI, pero nadie recibe un aviso si el operador no está mirando la pantalla. Este
módulo evalúa periódicamente los sites y, cuando uno lleva en silencio más de
``NETPULSE_SITE_STALE_AFTER_S``:

- emite **una sola alerta por episodio** (deduplicada en
  ``runtime/site_watchdog.json``) por los canales que ya existen
  (``notifications_svc``: webhooks, Telegram, Slack y email),
- la expone en ``GET /api/alerts/active`` junto a las de Prometheus,
- y la resuelve automáticamente cuando el site vuelve a estar ``fresh``.

Un site **sin heartbeat** (clave de colector emitida pero nunca vista en la DB
central) también se alerta con motivo ``"sin heartbeat"``: nunca se asume
verde. Un site presente en la DB con ``last_seen_at`` viejo se alerta como
``"sin contacto"``.

No es un sistema de alertas paralelo: reutiliza la lectura de ``query_svc``,
el envío de ``notifications_svc`` y el endpoint de alertas activas. La
deduplicación persistida es solo el estado del episodio (qué sites ya se
avisaron y cuándo), no un motor de reglas.

Límite honesto: si el central entero se cae, este watchdog no corre y no puede
alertar de su propia caída; eso lo cubre el monitoreo externo del proceso/API
(p. ej. Prometheus o el orquestador).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core import settings
from app.remote_collector.timeutil import iso_now, parse_iso, utcnow
from app.services import notifications_svc
from app.services.remote import query_svc, store

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_S = 60
DEFAULT_STALE_AFTER_S = 300
MIN_INTERVAL_S = 5
MIN_STALE_AFTER_S = 30
STATE_VERSION = 1
EVENT_STALE = "site_stale"
EVENT_RECOVERED = "site_recovered"
REASON_NO_CONTACT = "sin contacto"
REASON_NO_HEARTBEAT = "sin heartbeat"

_LOCK = threading.RLock()
_run_state: dict[str, Any] = {
    "last_run": None,
    "last_duration_ms": None,
    "last_error": None,
    "cycles": 0,
    "last_fired": 0,
    "last_resolved": 0,
}
_task: asyncio.Task | None = None
_stop: asyncio.Event | None = None


# ── Configuración (entorno, releído en cada ciclo) ────────────


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def enabled() -> bool:
    """``NETPULSE_SITE_WATCHDOG_ENABLED`` (default: activado)."""
    return _env_bool(
        "NETPULSE_SITE_WATCHDOG_ENABLED",
        settings.SITE_WATCHDOG_ENABLED_DEFAULT,
    )


def interval_s() -> int:
    """``NETPULSE_SITE_WATCHDOG_INTERVAL_S`` (default 60, mínimo 5)."""
    return _env_int(
        "NETPULSE_SITE_WATCHDOG_INTERVAL_S",
        settings.SITE_WATCHDOG_INTERVAL_S_DEFAULT,
        MIN_INTERVAL_S,
    )


def stale_after_s() -> int:
    """``NETPULSE_SITE_STALE_AFTER_S`` (default 300, mínimo 30)."""
    return _env_int(
        "NETPULSE_SITE_STALE_AFTER_S",
        settings.SITE_STALE_AFTER_S_DEFAULT,
        MIN_STALE_AFTER_S,
    )


def state_file() -> Path:
    """Ruta del estado de deduplicación (``runtime/site_watchdog.json``)."""
    raw = os.getenv("NETPULSE_SITE_WATCHDOG_STATE")
    if raw:
        return Path(raw).expanduser()
    return Path(settings.BASE_DIR) / "runtime" / "site_watchdog.json"


# ── Estado persistido (deduplicación por episodio) ───────────


def _load_alerts() -> dict[str, dict[str, Any]]:
    path = state_file()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("[watchdog] estado ilegible (%s); se reinicia el seguimiento", exc)
        return {}
    if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
        logger.warning("[watchdog] estado con versión no soportada; se reinicia")
        return {}
    alerts = data.get("alerts")
    if not isinstance(alerts, dict):
        return {}
    return {str(k): v for k, v in alerts.items() if isinstance(v, dict)}


def _save_alerts(alerts: dict[str, dict[str, Any]]) -> None:
    path = state_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        payload = {
            "version": STATE_VERSION,
            "updated_at": iso_now(),
            "alerts": alerts,
        }
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError as exc:
        logger.error("[watchdog] no se pudo guardar el estado %s: %s", path, exc)


# ── Cálculo de silencio ──────────────────────────────────────


def _age_seconds(value: str | None, now: datetime) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, (now - parse_iso(value)).total_seconds())
    except (TypeError, ValueError):
        return None


def _site_threshold(collector: dict[str, Any]) -> int:
    """Umbral por colector: ``NETPULSE_SITE_STALE_AFTER_S`` o 3 × cadencia.

    Con el default de 300 s coincide con la regla que usa ``query_svc`` para
    pintar el site. Si el operador baja el umbral, la alerta puede adelantarse
    a la UI; si lo sube, espera más. Nunca se alerta un site que la UI
    considere fresh con el umbral por defecto.
    """
    try:
        interval = int(collector.get("probe_interval_s") or 0)
    except (TypeError, ValueError):
        interval = 0
    return max(stale_after_s(), 3 * interval)


def _collector_error(collectors: list[tuple[float, dict[str, Any]]]) -> tuple[str | None, str | None]:
    """Primer error de sincronización conocido, del colector más reciente."""
    for _, collector in sorted(collectors, key=lambda item: item[0]):
        sync = collector.get("sync") or {}
        error = sync.get("last_error")
        if error:
            return str(error), sync.get("last_error_at")
    return None, None


def _stale_details(site: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    """Devuelve el detalle del silencio o ``None`` si el site está fresh."""
    collectors = site.get("collectors") or []
    ages: list[tuple[float | None, dict[str, Any]]] = [
        (_age_seconds(c.get("last_seen_at"), now), c) for c in collectors
    ]
    # Un site está en contacto si al menos un colector está dentro de su umbral;
    # es la misma regla que usa query_svc para pintar el site.
    for age, collector in ages:
        if age is not None and age <= _site_threshold(collector):
            return None

    known = [(age, c) for age, c in ages if age is not None]
    if not known:
        return {
            "collector_id": collectors[0].get("collector_id") if collectors else None,
            "reason": REASON_NO_HEARTBEAT,
            "silent_since": None,
            "last_seen_at": None,
            "last_error": None,
            "last_error_at": None,
        }

    _, primary = min(known, key=lambda item: item[0])
    last_error, last_error_at = _collector_error(known)
    return {
        "collector_id": primary.get("collector_id"),
        "reason": REASON_NO_CONTACT,
        "silent_since": primary.get("last_seen_at"),
        "last_seen_at": primary.get("last_seen_at"),
        "last_error": last_error,
        "last_error_at": last_error_at,
    }


def _configured_sites() -> dict[str, list[dict[str, Any]]]:
    """Sites declarados por claves de colector activas (aunque nunca reporten)."""
    try:
        from app.services.security import api_keys_svc

        keys = api_keys_svc.list_api_keys()
    except Exception as exc:  # noqa: BLE001 - nunca debe tumbar el ciclo
        logger.debug("[watchdog] no se pudieron leer las API keys: %s", exc)
        return {}

    grouped: dict[str, list[dict[str, Any]]] = {}
    for key in keys:
        if key.get("kind") != "collector" or key.get("revoked"):
            continue
        site_id = key.get("site_id")
        collector_id = key.get("collector_id")
        if not site_id or not collector_id:
            continue
        grouped.setdefault(str(site_id), []).append(key)
    return grouped


def _no_heartbeat_details(
    keys: list[dict[str, Any]], now: datetime
) -> dict[str, Any] | None:
    """Site provisionado sin ninguna fila en la DB central.

    Se da un margen desde la creación de la clave para no alertar mientras el
    colector se está desplegando.
    """
    candidates: list[tuple[float, dict[str, Any]]] = []
    for key in keys:
        age = _age_seconds(key.get("created_at"), now)
        candidates.append((float("inf") if age is None else age, key))
    youngest_age, _ = min(candidates, key=lambda item: item[0])
    if youngest_age <= stale_after_s():
        return None
    # Ya pasó el margen de la clave más reciente: se reporta la espera más
    # larga (la clave más antigua) para no maquillar el silencio.
    _, oldest_key = max(candidates, key=lambda item: item[0])
    return {
        "collector_id": oldest_key.get("collector_id"),
        "reason": REASON_NO_HEARTBEAT,
        "silent_since": oldest_key.get("created_at"),
        "last_seen_at": None,
        "last_error": None,
        "last_error_at": None,
    }


# ── Payload de alerta y notificación ─────────────────────────


def _minutes(value: str | None, now: datetime) -> int | None:
    age = _age_seconds(value, now)
    return None if age is None else int(round(age / 60.0))


def _alert_payload(entry: dict[str, Any], now: datetime) -> dict[str, Any]:
    site_id = str(entry.get("site_id") or "?")
    collector_id = entry.get("collector_id")
    reason = entry.get("reason") or REASON_NO_CONTACT
    minutes = _minutes(entry.get("silent_since") or entry.get("last_seen_at"), now)
    last_seen = entry.get("last_seen_at")
    if reason == REASON_NO_HEARTBEAT:
        summary = f"Site {site_id}: sin heartbeat"
        if collector_id:
            summary += f" (colector {collector_id})"
        description = (
            f"El site {site_id} tiene un colector provisionado que nunca ha "
            "contactado con el central."
        )
        if minutes is not None:
            description += f" Lleva {minutes} min sin heartbeat."
    else:
        summary = f"Site {site_id} sin contacto hace {minutes if minutes is not None else '?'} min"
        description = (
            f"El site {site_id} no reporta desde {last_seen or 'hora desconocida'}."
        )
        if collector_id:
            description += f" Último colector: {collector_id}."
    if entry.get("last_error"):
        description += f" Último error conocido: {entry['last_error']}"
        if entry.get("last_error_at"):
            description += f" ({entry['last_error_at']})"
    return {
        "name": "SiteSilent",
        "state": "firing",
        "severity": "critical",
        "device_id": site_id,  # compatibilidad con la vista de alertas actual
        "site_id": site_id,
        "collector_id": collector_id,
        "minutes_without_contact": minutes,
        "reason": reason,
        "last_seen_at": last_seen,
        "last_error": entry.get("last_error"),
        "last_error_at": entry.get("last_error_at"),
        "summary": summary,
        "description": description,
        "active_at": entry.get("fired_at"),
        "source": "watchdog",
    }


def _notify_stale(entry: dict[str, Any], now: datetime) -> None:
    payload = _alert_payload(entry, now)
    data = {
        "title": payload["summary"],
        "message": payload["description"],
        "site_id": payload["site_id"],
        "collector_id": payload["collector_id"],
        "minutes_without_contact": payload["minutes_without_contact"],
        "reason": payload["reason"],
        "last_seen_at": payload["last_seen_at"],
        "last_error": payload["last_error"],
        "last_error_at": payload["last_error_at"],
        "severity": "critical",
    }
    logger.warning("[watchdog] %s", payload["summary"])
    try:
        notifications_svc.notify_event(EVENT_STALE, data)
    except Exception as exc:  # noqa: BLE001 - el estado se guarda igualmente
        logger.error("[watchdog] fallo notificando site_stale %s: %s", payload["site_id"], exc)


def _notify_recovered(entry: dict[str, Any], now: datetime) -> None:
    site_id = str(entry.get("site_id") or "?")
    duration = _minutes(entry.get("fired_at"), now)
    data = {
        "title": f"Site {site_id} recuperado",
        "message": (
            f"El site {site_id} vuelve a reportar al central"
            + (f" tras {duration} min" if duration is not None else "")
            + "."
        ),
        "site_id": site_id,
        "collector_id": entry.get("collector_id"),
        "minutes_down": duration,
        "resolved_at": now.isoformat(timespec="microseconds"),
        "severity": "info",
    }
    logger.info("[watchdog] site %s recuperado", site_id)
    try:
        notifications_svc.notify_event(EVENT_RECOVERED, data)
    except Exception as exc:  # noqa: BLE001
        logger.error("[watchdog] fallo notificando site_recovered %s: %s", site_id, exc)


# ── Evaluación ───────────────────────────────────────────────


def evaluate(now: datetime | None = None) -> dict[str, Any]:
    """Ejecuta un ciclo: alerta sites enmudecidos y resuelve los recuperados.

    Es síncrono a propósito para poder probarlo con un reloj inyectado; el
    bucle async lo llama con ``asyncio.to_thread`` para no bloquear el event
    loop con SQLite ni con los webhooks.
    """
    if not enabled():
        return {
            "status": "disabled",
            "fired": [],
            "resolved": [],
            "active": [],
        }

    current = now or utcnow()
    started = time.monotonic()
    try:
        snapshot = query_svc.list_sites(current)
    except store.RemoteStoreUnavailable as exc:
        _record_run(started, error=str(exc))
        logger.warning("[watchdog] almacén remoto no disponible: %s", exc)
        return {"status": "error", "error": str(exc), "fired": [], "resolved": [], "active": []}
    except Exception as exc:  # noqa: BLE001 - un fallo no debe matar el bucle
        _record_run(started, error=str(exc))
        logger.exception("[watchdog] fallo leyendo sites: %s", exc)
        return {"status": "error", "error": str(exc), "fired": [], "resolved": [], "active": []}

    with _LOCK:
        alerts = _load_alerts()

    sites = {str(s.get("site_id")): s for s in (snapshot.get("sites") or [])}
    stale_now: dict[str, dict[str, Any]] = {}
    for site_id, site in sites.items():
        details = _stale_details(site, current)
        if details is not None:
            stale_now[site_id] = details

    # Sites con clave de colector pero sin ninguna fila en la DB central.
    for site_id, keys in _configured_sites().items():
        if site_id in sites or site_id in stale_now:
            continue
        details = _no_heartbeat_details(keys, current)
        if details is not None:
            stale_now[site_id] = details

    fired: list[dict[str, Any]] = []
    resolved: list[dict[str, Any]] = []

    for site_id, details in sorted(stale_now.items()):
        if site_id in alerts:
            continue
        entry = {
            "site_id": site_id,
            **details,
            "fired_at": current.isoformat(timespec="microseconds"),
            "severity": "critical",
        }
        alerts[site_id] = entry
        fired.append(entry)
        _notify_stale(entry, current)

    for site_id in sorted(alerts):
        if site_id in stale_now:
            continue
        entry = alerts.pop(site_id)
        resolved.append(entry)
        _notify_recovered(entry, current)

    if fired or resolved:
        with _LOCK:
            _save_alerts(alerts)

    _record_run(started, fired=len(fired), resolved=len(resolved))
    return {
        "status": "ok",
        "generated_at": current.isoformat(timespec="microseconds"),
        "fired": [_alert_payload(entry, current) for entry in fired],
        "resolved": [str(entry.get("site_id")) for entry in resolved],
        "active": active_alerts(current),
    }


def _record_run(
    started: float,
    *,
    error: str | None = None,
    fired: int = 0,
    resolved: int = 0,
) -> None:
    with _LOCK:
        _run_state["last_run"] = iso_now()
        _run_state["last_duration_ms"] = int((time.monotonic() - started) * 1000)
        _run_state["last_error"] = error
        _run_state["cycles"] = int(_run_state.get("cycles") or 0) + 1
        _run_state["last_fired"] = fired
        _run_state["last_resolved"] = resolved


# ── Lectura para endpoints ───────────────────────────────────


def active_alerts(now: datetime | None = None) -> list[dict[str, Any]]:
    """Alertas de site activas (firing), en el formato de ``/api/alerts``."""
    current = now or utcnow()
    with _LOCK:
        alerts = _load_alerts()
    return [
        _alert_payload(entry, current)
        for _, entry in sorted(alerts.items())
    ]


def status_snapshot(now: datetime | None = None) -> dict[str, Any]:
    """Estado operativo del watchdog para ``GET /api/remote/watchdog``."""
    current = now or utcnow()
    with _LOCK:
        state = dict(_run_state)
    alerts = active_alerts(current)
    return {
        "enabled": enabled(),
        "running": is_running(),
        "interval_s": interval_s(),
        "stale_after_s": stale_after_s(),
        "last_run": state.get("last_run"),
        "last_duration_ms": state.get("last_duration_ms"),
        "last_error": state.get("last_error"),
        "cycles": state.get("cycles", 0),
        "last_fired": state.get("last_fired", 0),
        "last_resolved": state.get("last_resolved", 0),
        "active_count": len(alerts),
        "alerts": alerts,
    }


# ── Ciclo de vida (asyncio task) ─────────────────────────────


def is_running() -> bool:
    task = _task
    return task is not None and not task.done()


async def _loop() -> None:
    logger.info(
        "[watchdog] iniciado (intervalo=%ss, stale_after=%ss)",
        interval_s(),
        stale_after_s(),
    )
    while True:
        stop_event = _stop
        if stop_event is None or stop_event.is_set():
            break
        try:
            await asyncio.to_thread(evaluate)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - el bucle sigue vivo
            logger.exception("[watchdog] ciclo falló: %s", exc)
            _record_run(time.monotonic(), error=str(exc))
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_s())
        except asyncio.TimeoutError:
            continue
        break
    logger.info("[watchdog] detenido")


async def start() -> bool:
    """Arranca el watchdog en la tarea async actual.

    Returns:
        True si quedó corriendo, False si está deshabilitado o ya corría.
    """
    global _task, _stop
    if not enabled():
        logger.info("[watchdog] deshabilitado (NETPULSE_SITE_WATCHDOG_ENABLED=false)")
        return False
    if is_running():
        return True
    _stop = asyncio.Event()
    _task = asyncio.create_task(_loop(), name="netpulse-site-watchdog")
    return True


async def stop(timeout: float = 5.0) -> None:
    """Detiene la tarea y espera a que termine (sin fugas en shutdown)."""
    global _task, _stop
    task = _task
    stop_event = _stop
    if stop_event is not None:
        stop_event.set()
    if task is not None:
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except asyncio.TimeoutError:
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("[watchdog] terminó con error: %s", exc)
    _task = None
    _stop = None
