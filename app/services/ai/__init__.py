"""NetPulse — Capa de decisiones IA.

Motor de decisiones "System One" (Laya, alternativa libre y multilingüe a
Jev): recibe un ``state`` + preguntas tipadas (``noul``/``choice``/``score``)
y devuelve respuestas con probabilidad y confianza que el código consume
directamente. Se habla con él por HTTP (``laya-serve``); si está apagado o
falla, los llamantes usan su lógica por reglas (fail-open).
"""

from app.services.ai.decision_svc import decide, is_enabled, status
from app.services.ai.schemas import Answer, Decision, choice, noul, score

__all__ = ["decide", "is_enabled", "status", "Answer", "Decision", "choice", "noul", "score"]
