"""NetPulse — Alertas de deception.

Evalúa los eventos de la red señuelo y dispara alertas ante actividad de
alto valor: login exitoso, descarga de archivos, escaneo de puertos y
fuerza bruta. Cada alerta se registra en la auditoría, se reenvía a los
canales de notificación (webhooks/Telegram/Slack/Email) y se cuenta en
Prometheus.

El envío de notificaciones es bloqueante, por lo que ``submit`` lo
despacha en un executor para no frenar a los listeners.
"""

import ipaddress
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional

from app.core.settings import (
    DECEPTION_ALERTS_ENABLED,
    DECEPTION_ALERT_COOLDOWN,
    DECEPTION_ALERT_IGNORE_LOOPBACK,
    DECEPTION_ALERT_IGNORE_PRIVATE,
    DECEPTION_ALERT_IGNORE_SIMULATED,
    DECEPTION_AUTOBLOCK_CRITICAL,
    DECEPTION_BRUTE_FORCE_THRESHOLD,
    DECEPTION_BRUTE_FORCE_WINDOW,
)
from app.services import audit_svc, metrics_svc, notifications_svc
from app.services.deception import allowlist_svc, blocklist_svc, store_svc

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dcn-alert")
_lock = threading.Lock()
_last_fired: dict[tuple, float] = {}

# tipo de evento → (regla, severidad, título)
_EVENT_RULES: dict[str, tuple[str, str, str]] = {
    "login_success": ("login_success", "high", "Login exitoso en dispositivo señuelo"),
    "file_download": ("file_download", "critical", "Descarga de archivo desde señuelo"),
    "port_scan": ("port_scan", "high", "Escaneo de puertos contra señuelo"),
}


def submit(record: dict) -> None:
    """Programa la evaluación de un evento sin bloquear al llamante."""
    if not DECEPTION_ALERTS_ENABLED:
        return
    try:
        _executor.submit(evaluate, record)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[deception] no se pudo programar alerta: %s", exc)


def evaluate(record: dict) -> list[dict]:
    """Evalúa un evento almacenado y dispara las alertas correspondientes.

    Returns:
        Lista de alertas efectivamente disparadas (tras cooldown).
    """
    if not DECEPTION_ALERTS_ENABLED:
        return []

    if _is_noise(record):
        return []

    etype = record.get("type")
    alerts: list[dict] = []

    rule_info = _EVENT_RULES.get(etype)
    if rule_info:
        alerts.append(_make_alert(record, *rule_info))
    elif etype == "login_attempt" and _brute_force(record):
        alerts.append(_make_alert(
            record, "brute_force", "high",
            "Posible fuerza bruta contra señuelo",
        ))

    fired: list[dict] = []
    for alert in alerts:
        if not _cooldown_ok(alert):
            continue
        # Filtro de falsos positivos con IA (advisory + gating, fail-open).
        triage = _ai_triage(record, alert)
        if triage.get("suppress"):
            _dispatch_suppressed(alert, triage)
            continue
        if triage.get("ai"):
            alert["ai"] = triage["ai"]
        fired.append(alert)

    for alert in fired:
        _dispatch(alert)
    return fired


def recent(limit: int = 50) -> list[dict]:
    """Alertas recientes desde la auditoría (más nuevas primero)."""
    return audit_svc.get_events(limit=limit, action="deception_alert")


def recent_suppressed(limit: int = 50) -> list[dict]:
    """Alertas suprimidas por la IA (falsos positivos detectados)."""
    return audit_svc.get_events(limit=limit, action="deception_alert_suppressed")


# ── Filtro de falsos positivos con IA ────────────────────────


def _ai_triage(record: dict, alert: dict) -> dict:
    """Pide a la IA si el evento es benigno (fail-open si falla)."""
    try:
        from app.services.deception import fp_svc
        return fp_svc.classify(record, alert)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[deception] triage IA falló: %s", exc)
        return {"enabled": False, "verdict": "unknown", "suppress": False}


def _dispatch_suppressed(alert: dict, triage: dict) -> None:
    """Registra una alerta suprimida por ser falso positivo (sin notificar)."""
    ai_block = triage.get("ai") or {}
    try:
        audit_svc.log_event(
            action="deception_alert_suppressed",
            device_id=alert.get("device_id"),
            username=alert.get("username") or "attacker",
            details=json.dumps({"alert": alert, "ai": ai_block}, ensure_ascii=False),
            ip_address=alert.get("src_ip"),
            success=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[deception] no se pudo auditar supresión: %s", exc)
    logger.info(
        "[deception] alerta SUPRIMIDA por IA (falso positivo p=%s): %s",
        ai_block.get("confidence"), alert.get("message"),
    )


# ── Internos ─────────────────────────────────────────────────


def _is_noise(record: dict) -> bool:
    """Filtra eventos que no deben generar alertas (anti falsos positivos)."""
    detail = record.get("detail") or {}
    if DECEPTION_ALERT_IGNORE_SIMULATED and detail.get("simulated"):
        return True

    src_ip = record.get("src_ip") or ""
    if src_ip and allowlist_svc.is_allowed(src_ip):
        return True

    try:
        addr = ipaddress.ip_address(src_ip)
    except ValueError:
        return False

    if DECEPTION_ALERT_IGNORE_LOOPBACK and addr.is_loopback:
        return True
    if DECEPTION_ALERT_IGNORE_PRIVATE and addr.is_private and not addr.is_loopback:
        return True
    return False


def _make_alert(record: dict, rule: str, severity: str, title: str) -> dict:
    username = record.get("username")
    detail = record.get("detail") or {}
    extra = ""
    if detail.get("command"):
        extra = f" comando='{detail['command']}'"
    elif detail.get("url"):
        extra = f" url='{detail['url']}'"
    message = (
        f"[{severity.upper()}] {title}: {record.get('device_id')} "
        f"desde {record.get('src_ip')} ({record.get('proto')})"
        + (f" user={username}" if username else "")
        + extra
    )
    return {
        "rule": rule,
        "severity": severity,
        "title": title,
        "message": message,
        "device_id": record.get("device_id"),
        "src_ip": record.get("src_ip"),
        "proto": record.get("proto"),
        "username": username,
        "detail": detail,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _brute_force(record: dict) -> bool:
    """True si hay suficientes intentos recientes desde la misma IP."""
    device_id = record.get("device_id")
    src_ip = record.get("src_ip")
    if not device_id or not src_ip:
        return False
    cutoff = time.time() - DECEPTION_BRUTE_FORCE_WINDOW
    attempts = 0
    for event in store_svc.get_events(
        device_id=device_id, src_ip=src_ip, event_type="login_attempt", limit=200
    ):
        ts = _parse_ts(event.get("timestamp"))
        if ts is None:
            continue
        if ts < cutoff:
            break  # vienen ordenados desc por id; el resto es más viejo
        attempts += 1
    return attempts >= DECEPTION_BRUTE_FORCE_THRESHOLD


def _parse_ts(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _cooldown_ok(alert: dict) -> bool:
    """Aplica el silencio por (dispositivo, atacante, regla)."""
    key = (alert.get("device_id"), alert.get("src_ip"), alert.get("rule"))
    now = time.time()
    with _lock:
        last = _last_fired.get(key, 0.0)
        if now - last < DECEPTION_ALERT_COOLDOWN:
            return False
        _last_fired[key] = now
    return True


def _dispatch(alert: dict) -> None:
    """Registra, notifica y contabiliza una alerta."""
    try:
        metrics_svc.netpulse_deception_alerts_total.labels(
            severity=alert["severity"], rule=alert["rule"]
        ).inc()
    except Exception:  # noqa: BLE001
        pass

    try:
        audit_svc.log_event(
            action="deception_alert",
            device_id=alert.get("device_id"),
            username=alert.get("username") or "attacker",
            details=json.dumps(alert, ensure_ascii=False),
            ip_address=alert.get("src_ip"),
            success=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[deception] no se pudo auditar alerta: %s", exc)

    logger.warning("[deception] ALERTA %s — %s", alert["severity"], alert["message"])

    if DECEPTION_AUTOBLOCK_CRITICAL and alert.get("severity") == "critical":
        try:
            blocklist_svc.auto_block(alert)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[deception] autoblock falló: %s", exc)

    try:
        notifications_svc.notify_event("deception_alert", alert)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[deception] notificación de alerta falló: %s", exc)
