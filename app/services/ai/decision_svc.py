"""NetPulse — Fachada de decisiones IA.

Punto único por el que pasan los servicios de dominio para pedir una
decisión tipada al motor (Laya por defecto, stub en tests/offline).

Aplica: redacción de secretos, umbral de confianza, caché, timeout y
métricas, y **fail-open** (devuelve ``None`` si está deshabilitado o
falla, para que el llamante siga con su lógica por reglas).
"""

import hashlib
import json
import logging
import re
import threading
import time
from typing import Optional

from app.core import settings
from app.services.ai.schemas import Answer, Decision

logger = logging.getLogger(__name__)

# Backend perezoso (monkeypatch-able en tests)
_backend = None
_cache: dict[str, tuple[float, Decision]] = {}
_cache_lock = threading.Lock()

_REDACT_PATTERNS = [
    re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|authorization|bearer|community|snmp[_ ]?ro|enable[_ ]?password)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
]


def redact(text: str, max_len: int = 200) -> str:
    """Quita secretos y recorta texto libre antes de enviarlo al modelo."""
    if not text:
        return ""
    out = str(text)
    for pat in _REDACT_PATTERNS:
        out = pat.sub("***", out)
    if len(out) > max_len:
        out = out[:max_len] + "…"
    return out


def set_backend(backend) -> None:
    """Inyecta un backend (tests)."""
    global _backend
    _backend = backend


def _get_backend():
    global _backend
    if _backend is not None:
        return _backend
    kind = (settings.AI_BACKEND or "laya").lower()
    if kind == "stub":
        from app.services.ai.backends import StubBackend
        _backend = StubBackend()
    else:
        from app.services.ai.backends import LayaBackend
        _backend = LayaBackend(
            base_url=settings.AI_BASE_URL,
            model=settings.AI_MODEL,
            timeout=settings.AI_TIMEOUT,
            jev_strict=settings.AI_JEV_STRICT,
        )
    return _backend


def is_enabled() -> bool:
    """True si la capa IA está activa por configuración."""
    return bool(settings.AI_ENABLED)


def _cache_key(state, questions, min_confidence) -> str:
    blob = json.dumps(
        {"s": state, "q": questions, "m": settings.AI_MODEL, "c": min_confidence},
        sort_keys=True, default=str, ensure_ascii=False,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def decide(
    state,
    questions: dict,
    *,
    kind: str = "generic",
    min_confidence: Optional[float] = None,
) -> Optional[Decision]:
    """Pide una decisión al motor IA.

    Returns:
        ``Decision`` o ``None`` si la IA está deshabilitada o falló.
    """
    if not is_enabled() or not questions:
        return None

    ck = _cache_key(state, questions, min_confidence)
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(ck)
        if hit and now - hit[0] < settings.AI_CACHE_TTL:
            cached = hit[1]
            cached.cached = True
            _metric(kind, "cache")
            return cached

    try:
        raw = _get_backend().decide(
            state, questions,
            min_confidence=min_confidence,
            max_len=getattr(settings, "AI_MAX_LEN", None),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ai] decisión falló (%s): %s", kind, exc)
        _metric(kind, "error")
        return None

    answers_raw = raw.get("answers") if isinstance(raw, dict) else None
    if not isinstance(answers_raw, dict):
        logger.warning("[ai] respuesta sin 'answers' (%s)", kind)
        _metric(kind, "error")
        return None

    routing = raw.get("routing") if isinstance(raw, dict) else None
    model = None
    if isinstance(routing, dict):
        model = routing.get("model")
    if not model and isinstance(raw, dict):
        model = raw.get("model")
    model = model or settings.AI_MODEL
    decision = Decision(
        answers={qid: Answer.from_raw(qid, data) for qid, data in answers_raw.items()},
        model=model,
        latency_ms=raw.get("latency_ms") if isinstance(raw, dict) else None,
        raw=raw if isinstance(raw, dict) else {},
    )
    with _cache_lock:
        _cache[ck] = (time.monotonic(), decision)
    _metric(kind, "ok")
    return decision


def status() -> dict:
    """Estado de la capa IA para el endpoint de diagnóstico."""
    enabled = is_enabled()
    out = {
        "enabled": enabled,
        "backend": settings.AI_BACKEND,
        "base_url": settings.AI_BASE_URL,
        "model": settings.AI_MODEL,
        "confidence_floor": settings.AI_CONFIDENCE_FLOOR,
        "timeout": settings.AI_TIMEOUT,
    }
    if enabled:
        try:
            out.update(_get_backend().health())
        except Exception as exc:  # noqa: BLE001
            out.update({"reachable": False, "error": str(exc)})
    return out


def _metric(kind: str, result: str) -> None:
    try:
        from app.services import metrics_svc
        metrics_svc.netpulse_ai_decisions_total.labels(
            kind=kind or "generic", result=result
        ).inc()
    except Exception:  # noqa: BLE001
        pass
