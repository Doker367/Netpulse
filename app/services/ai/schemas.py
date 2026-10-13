"""NetPulse — Esquemas de decisiones IA (primitivas tipo System One).

Tres primitivas, cada una un dict JSON listo para el modelo:

- ``noul``   → sí/no; devuelve la probabilidad de "sí".
- ``choice`` → una opción de una lista; devuelve elección, distribución y confianza.
- ``score``  → un nivel en una escala ordenada; devuelve posición, distribución y confianza.

Las respuestas se envuelven en ``Answer``/``Decision`` para leerlas de forma
tipada, sin depender del formato exacto del backend.
"""

from dataclasses import dataclass, field
from typing import Any, Optional


def noul(instructions: str, criteria: Optional[dict] = None) -> dict:
    """Pregunta de sí/no. ``criteria`` describe opcionalmente qué es verdadeo/falso."""
    q: dict = {"type": "noul", "instructions": instructions}
    if criteria:
        q["criteria"] = criteria
    return q


def choice(instructions: str, criteria: dict[str, str]) -> dict:
    """Pregunta de opción única. ``criteria`` mapea opción → descripción."""
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def score(instructions: str, criteria: list[str]) -> dict:
    """Pregunta de escala ordenada. ``criteria`` es la lista de niveles."""
    return {"type": "score", "instructions": instructions, "criteria": criteria}


@dataclass
class Answer:
    """Respuesta tipada de una pregunta."""

    qid: str
    type: str
    noul: Optional[float] = None
    choice: Optional[str] = None
    score: Optional[float] = None
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: Optional[float] = None
    legend: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_raw(cls, qid: str, data: dict) -> "Answer":
        data = data if isinstance(data, dict) else {}
        if "noul" in data:
            qtype = "noul"
        elif "choice" in data:
            qtype = "choice"
        elif "score" in data:
            qtype = "score"
        else:
            qtype = "unknown"
        return cls(
            qid=qid,
            type=qtype,
            noul=_as_float(data.get("noul")),
            choice=data.get("choice"),
            score=_as_float(data.get("score")),
            probabilities=_as_prob_map(data.get("probabilities")),
            confidence=_as_float(data.get("confidence")),
            legend=list(data.get("legend") or []),
            raw=data,
        )


@dataclass
class Decision:
    """Conjunto de respuestas de una llamada + metadatos."""

    answers: dict[str, Answer] = field(default_factory=dict)
    model: Optional[str] = None
    latency_ms: Optional[float] = None
    cached: bool = False
    raw: dict = field(default_factory=dict)

    def get(self, qid: str) -> Optional[Answer]:
        return self.answers.get(qid)

    def probability(self, qid: str) -> Optional[float]:
        """Probabilidad de 'sí' (noul) o confianza de la opción elegida."""
        ans = self.answers.get(qid)
        if not ans:
            return None
        if ans.type == "noul":
            return ans.noul
        return ans.confidence

    def choice_of(self, qid: str) -> Optional[str]:
        ans = self.answers.get(qid)
        return ans.choice if ans else None


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_prob_map(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, float] = {}
    for key, val in value.items():
        f = _as_float(val)
        if f is not None:
            out[str(key)] = f
    return out
