"""NetPulse — Backend stub (tests y modo offline).

No usa red. Por defecto aplica una heurística por palabras clave para
devolver decisiones plausibles; en tests se puede forzar la respuesta
exacta pasando ``state = {"_stub_answers": {...}}``.
"""

import json
from typing import Optional


_BENIGN_HINTS = (
    "scanner", "nmap", "health", "monitor", "zabbix", "nagios", "prometheus",
    "backup", "authorized", "legit", "inventory", "internal", "test", "probe",
)
_REAL_HINTS = (
    "malware", "exploit", "payload", "hydra", "brute", "root", "/etc/passwd",
    "reverse shell", "c2", "command and control", "wget", "curl ", "nc -e",
    "shell", "upload", "webshell",
)


class StubBackend:
    """Backend determinista para tests y para correr sin el servicio Laya."""

    def __init__(self, model: str = "stub", benign_confidence: float = 0.96, real_confidence: float = 0.94):
        self.model = model
        self.benign_confidence = benign_confidence
        self.real_confidence = real_confidence

    def decide(
        self,
        state,
        questions: dict,
        *,
        min_confidence: Optional[float] = None,
        max_len: Optional[int] = None,
    ) -> dict:
        if isinstance(state, dict) and isinstance(state.get("_stub_answers"), dict):
            return {"answers": state["_stub_answers"], "routing": {"model": self.model}}

        text = json.dumps(state, ensure_ascii=False, default=str).lower()
        benign = any(h in text for h in _BENIGN_HINTS)
        real = any(h in text for h in _REAL_HINTS)
        # Si no hay señales, tratar como sospechoso (no benigno).
        is_benign = benign and not real

        answers: dict = {}
        for qid, q in (questions or {}).items():
            qtype = (q or {}).get("type", "noul")
            answers[qid] = self._answer(qid, qtype, q, is_benign)
        return {"answers": answers, "routing": {"model": self.model}}

    def health(self) -> dict:
        return {"reachable": True, "health": {"status": "stub"}}

    def _answer(self, qid: str, qtype: str, q: dict, is_benign: bool) -> dict:
        qid_l = qid.lower()
        if qtype == "noul":
            # Preguntas "benigno/noise" vs "ataque/bloqueo"
            benign_q = any(k in qid_l for k in ("benign", "false_positive", "noise", "fp"))
            if benign_q:
                p = self.benign_confidence if is_benign else 1.0 - self.real_confidence
            else:
                p = 1.0 - self.benign_confidence if is_benign else self.real_confidence
            return {"type": "noul", "noul": round(p, 4)}

        criteria = q.get("criteria")
        if qtype == "choice":
            options = list(criteria.keys()) if isinstance(criteria, dict) else []
            pick = self._pick_choice(qid_l, options, is_benign)
            probs = {opt: (0.9 if opt == pick else 0.1 / max(1, len(options) - 1)) for opt in options}
            return {
                "type": "choice",
                "choice": pick,
                "probabilities": probs,
                "confidence": round(0.9 if is_benign else self.real_confidence, 4),
            }

        # score
        legend = list(criteria) if isinstance(criteria, list) else []
        idx = 0 if is_benign else max(0, len(legend) - 1)
        return {
            "type": "score",
            "score": float(idx),
            "legend": legend,
            "probabilities": {str(i): (1.0 if i == idx else 0.0) for i in range(len(legend))},
            "confidence": round(0.85, 4),
        }

    @staticmethod
    def _pick_choice(qid: str, options: list[str], is_benign: bool) -> str:
        if not options:
            return ""
        if is_benign:
            for opt in options:
                if any(k in opt.lower() for k in ("benign", "none", "info", "authorized")):
                    return opt
        else:
            for opt in options:
                if any(k in opt.lower() for k in ("critical", "high", "initial", "credential")):
                    return opt
        return options[0]
