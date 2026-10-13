"""NetPulse — Clasificador de falsos positivos de deception (IA).

Usa el motor de decisiones (Laya, alternativa libre a Jev) para juzgar si
un evento señuelo es **benigno** (escáner autorizado, health-check, backup,
monitorización, prueba interna) o una **intrusión real**, y enriquece la
alerta con severidad, táctica MITRE y sugerencia de bloqueo.

Es *advisory + gating*: solo actúa (suprime/enriquece) si la confianza
alcanza ``DECEPTION_AI_FP_MIN_CONFIDENCE``; si la IA está apagada o falla,
devuelve un veredicto ``unknown`` y el flujo por reglas continúa (fail-open).
"""

import ipaddress
import logging
from typing import Optional

from app.core.settings import (
    DECEPTION_AI_FP_ENABLED,
    DECEPTION_AI_FP_MIN_CONFIDENCE,
    DECEPTION_AI_FP_SUPPRESS,
)
from app.services import ai
from app.services.deception import (
    allowlist_svc,
    blocklist_svc,
    geoip_svc,
    store_svc,
    topology_svc,
)

logger = logging.getLogger(__name__)

_QUESTIONS = {
    "is_benign": ai.noul(
        "Is `event` benign or a false positive? Examples: an authorized internal "
        "scanner, a health check or uptime monitor, a backup/inventory job, a "
        "misconfigured management tool, or an administrator testing the environment "
        "— rather than a real adversary interacting with the decoy."
    ),
    "real_intrusion": ai.noul(
        "Does `event` indicate a real adversary interacting with the decoy host "
        "(attempting unauthorized access, exploitation, credential theft, or "
        "reconnaissance), rather than expected or authorized traffic?"
    ),
    "severity": ai.choice(
        "How severe is `event` if it is a real intrusion?",
        {
            "informational": "no real threat, expected or authorized traffic",
            "low": "suspicious but low impact, likely noise",
            "medium": "credible attempt without strong attacker tradecraft",
            "high": "clear attacker behavior with real intent",
            "critical": "active compromise, malware, exfiltration or lateral movement",
        },
    ),
    "mitre_tactic": ai.choice(
        "Which single MITRE ATT&CK tactic best matches `event`? Use `none` if benign.",
        {
            "none": "no attacker behavior",
            "reconnaissance": "scanning, probing, enumeration",
            "initial_access": "exploiting a service or logging in",
            "execution": "running code or commands on the host",
            "persistence": "maintaining access",
            "privilege_escalation": "gaining higher privileges",
            "defense_evasion": "avoiding detection",
            "credential_access": "stealing or brute-forcing credentials",
            "discovery": "exploring the host or network",
            "lateral_movement": "moving to other hosts",
            "collection": "gathering data of interest",
            "command_and_control": "communicating with attacker infrastructure",
            "exfiltration": "stealing data out",
            "impact": "disrupting or destroying systems",
        },
    ),
    "should_block": ai.noul(
        "Should the source IP be blocked? Answer true only for real intrusions, "
        "not for benign/authorized traffic."
    ),
}


def classify(record: dict, alert: Optional[dict] = None) -> dict:
    """Clasifica un evento de deception como benigno o intrusión real.

    Returns:
        dict con ``enabled``, ``verdict`` (``benign``/``malicious``/``unknown``),
        ``confidence``, ``suppress`` y un bloque ``ai`` para adjuntar a la alerta.
    """
    if not DECEPTION_AI_FP_ENABLED or not ai.is_enabled():
        return {"enabled": False, "verdict": "unknown", "suppress": False}

    state = _build_state(record)
    decision = ai.decide(
        state, _QUESTIONS, kind="deception_fp",
        min_confidence=DECEPTION_AI_FP_MIN_CONFIDENCE,
    )
    if decision is None:
        _metric("unknown")
        return {"enabled": True, "verdict": "unknown", "suppress": False,
                "reason": "ai_unavailable"}

    p_benign = decision.probability("is_benign")
    p_real = decision.probability("real_intrusion")
    floor = DECEPTION_AI_FP_MIN_CONFIDENCE

    if p_benign is not None and p_real is not None:
        if p_benign >= floor and p_benign >= p_real:
            verdict, confidence = "benign", p_benign
        elif p_real >= floor and p_real > p_benign:
            verdict, confidence = "malicious", p_real
        else:
            verdict, confidence = "unknown", max(p_benign, p_real)
    else:
        verdict, confidence = "unknown", None

    suppress = verdict == "benign" and bool(DECEPTION_AI_FP_SUPPRESS)
    ai_block = {
        "verdict": verdict,
        "is_false_positive": verdict == "benign",
        "confidence": round(confidence, 4) if confidence is not None else None,
        "p_benign": round(p_benign, 4) if p_benign is not None else None,
        "p_intrusion": round(p_real, 4) if p_real is not None else None,
        "severity": decision.choice_of("severity"),
        "mitre_tactic": decision.choice_of("mitre_tactic"),
        "should_block": (decision.get("should_block").noul if decision.get("should_block") else None),
        "model": decision.model,
        "cached": decision.cached,
    }
    _metric(verdict)
    return {
        "enabled": True,
        "verdict": verdict,
        "confidence": confidence,
        "suppress": suppress,
        "ai": ai_block,
    }


# ── Contexto (state) ─────────────────────────────────────────


def _build_state(record: dict) -> dict:
    """Construye el `state` mínimo y redactado que se envía al modelo."""
    detail = record.get("detail") or {}
    device = topology_svc.get_device(record.get("device_id") or "") or {}
    persona = device.get("persona") or {}
    src_ip = record.get("src_ip") or ""

    event = {
        "device_id": record.get("device_id"),
        "device_role": device.get("role"),
        "device_segment": device.get("segment"),
        "device_persona": {
            "vendor": persona.get("vendor"),
            "os": persona.get("os"),
            "hostname": persona.get("hostname"),
        },
        "proto": record.get("proto"),
        "src_port": record.get("src_port"),
        "type": record.get("type"),
        "username": record.get("username"),
        "success": record.get("success"),
        "command": ai.decision_svc.redact(detail.get("command", "")),
        "url": ai.decision_svc.redact(detail.get("url", "")),
    }
    source = {
        "ip": src_ip,
        "scope": _scope(src_ip),
        "country": _country(src_ip),
        "allowlisted": bool(src_ip and allowlist_svc.is_allowed(src_ip)),
        "blocklisted": bool(src_ip and blocklist_svc.is_blocked(src_ip)),
        "events_from_source": _count(device.get("id"), src_ip, None),
        "login_attempts_from_source": _count(device.get("id"), src_ip, "login_attempt"),
    }
    return {
        "event": event,
        "source": source,
        "context": {"device_is_decoy": True},
    }


def _scope(ip: str) -> str:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "unknown"
    if addr.is_loopback:
        return "loopback"
    if addr.is_private:
        return "private"
    return "public"


def _country(ip: str) -> Optional[str]:
    try:
        data = geoip_svc.lookup(ip)
    except Exception:  # noqa: BLE001
        return None
    if not data:
        return None
    return data.get("country") or data.get("country_code")


def _count(device_id: Optional[str], src_ip: str, event_type: Optional[str]) -> int:
    if not src_ip:
        return 0
    try:
        events = store_svc.get_events(
            device_id=device_id, src_ip=src_ip, event_type=event_type, limit=200
        )
        return len(events)
    except Exception:  # noqa: BLE001
        return 0


def _metric(verdict: str) -> None:
    try:
        from app.services import metrics_svc
        metrics_svc.netpulse_deception_fp_total.labels(verdict=verdict).inc()
    except Exception:  # noqa: BLE001
        pass
