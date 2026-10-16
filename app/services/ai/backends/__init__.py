"""Backends de la capa de decisiones IA."""

from app.services.ai.backends.laya_backend import LayaBackend
from app.services.ai.backends.stub_backend import StubBackend

__all__ = ["LayaBackend", "StubBackend"]
