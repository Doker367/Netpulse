"""NetPulse — Router de la capa de decisiones IA (Laya / System One).

Endpoints:
- GET  /api/ai/status  — estado del motor (habilitado, backend, alcance).
- POST /api/ai/decide  — decisión genérica (state + preguntas tipadas).
"""

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.middleware.auth import JWTBearer
from app.middleware.rbac import requires_role
from app.services import ai

router = APIRouter(
    prefix="/api/ai",
    tags=["AI — Decisiones"],
    dependencies=[Depends(JWTBearer()), Depends(requires_role("admin"))],
)


class AiDecideRequest(BaseModel):
    """Cuerpo de una petición de decisión genérica."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "state": {"body": "Login exitoso desde un escáner de inventario interno."},
                "questions": {
                    "is_benign": {
                        "type": "noul",
                        "instructions": "¿Es tráfico benigno o un falso positivo?",
                    }
                },
                "kind": "generic",
            }
        }
    )

    state: Any = Field(..., description="Texto o JSON que el modelo evalúa")
    questions: dict = Field(..., description="Preguntas tipadas (noul/choice/score)")
    kind: str = Field(default="generic", description="Etiqueta para métricas/auditoría")
    min_confidence: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="Umbral de abstención opcional"
    )


@router.get("/status")
def ai_status() -> dict:
    """Estado de la capa IA (habilitada, backend, modelo, alcance)."""
    return ai.status()


@router.post("/decide")
def ai_decide(body: AiDecideRequest) -> dict:
    """Ejecuta una decisión tipada contra el motor IA.

    Devuelve las respuestas con probabilidad y confianza. Responde 409 si la
    capa IA está deshabilitada.
    """
    if not ai.is_enabled():
        raise HTTPException(409, "La capa IA está deshabilitada (NETPULSE_AI_ENABLED=false)")

    decision = ai.decide(
        body.state, body.questions, kind=body.kind, min_confidence=body.min_confidence
    )
    if decision is None:
        raise HTTPException(502, "El motor IA no devolvió una decisión")

    return {
        "model": decision.model,
        "cached": decision.cached,
        "latency_ms": decision.latency_ms,
        "answers": {qid: ans.raw for qid, ans in decision.answers.items()},
    }
