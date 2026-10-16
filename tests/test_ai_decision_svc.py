"""Tests de la capa de decisiones IA (fachada + backend stub)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import settings
from app.services import ai
from app.services.ai import decision_svc
from app.services.ai.backends import StubBackend


@pytest.fixture(autouse=True)
def _ai_stub(monkeypatch):
    monkeypatch.setattr(settings, "AI_ENABLED", True)
    monkeypatch.setattr(settings, "AI_BACKEND", "stub")
    monkeypatch.setattr(settings, "AI_CACHE_TTL", 300)
    decision_svc.set_backend(StubBackend())
    decision_svc._cache.clear()
    yield
    decision_svc.set_backend(None)
    decision_svc._cache.clear()


def test_decide_devuelve_respuestas_tipadas():
    decision = ai.decide(
        {"body": "internal inventory health scanner"},
        {
            "is_benign": ai.noul("¿Es tráfico benigno?"),
            "severity": ai.choice("Severidad", {"info": "x", "high": "y"}),
        },
        kind="test",
    )
    assert decision is not None
    assert decision.get("is_benign").noul >= 0.9
    assert decision.choice_of("severity") == "info"


def test_decide_malicioso():
    decision = ai.decide(
        {"body": "hydra brute force root /etc/passwd reverse shell"},
        {"is_benign": ai.noul("¿Es benigno?"), "real": ai.noul("¿Intrusión real?")},
        kind="test",
    )
    assert decision.get("is_benign").noul < 0.5
    assert decision.get("real").noul >= 0.9


def test_cache_evita_segunda_llamada():
    state = {"body": "internal scanner"}
    questions = {"is_benign": ai.noul("¿Es benigno?")}
    assert ai.decide(state, questions, kind="test").cached is False
    assert ai.decide(state, questions, kind="test").cached is True


def test_deshabilitado_devuelve_none(monkeypatch):
    monkeypatch.setattr(settings, "AI_ENABLED", False)
    assert ai.decide({"body": "x"}, {"q": ai.noul("y")}, kind="test") is None


def test_redacta_secretos_y_recorta():
    out = decision_svc.redact("password=SuperSecreto cmd=whoami", 200)
    assert "SuperSecreto" not in out
    assert "***" in out
    assert len(decision_svc.redact("a" * 500, 200)) <= 201
