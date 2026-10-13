"""NetPulse — Backend Laya (System One) por HTTP.

Cliente fino contra ``laya-serve`` (docker-compose.ai.yml). Envía
``state`` + ``questions`` y devuelve la respuesta cruda del modelo, que
incluye ``answers`` con probabilidades y confianza. No conoce nada del
dominio: eso lo arma cada servicio (p. ej. deception fp_svc).
"""

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class LayaBackend:
    """Cliente HTTP del motor de decisiones Laya."""

    def __init__(
        self,
        base_url: str,
        model: Optional[str] = None,
        timeout: float = 8.0,
        jev_strict: bool = True,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.model = model or None
        self.timeout = timeout
        self.jev_strict = jev_strict

    def decide(
        self,
        state,
        questions: dict,
        *,
        min_confidence: Optional[float] = None,
        max_len: Optional[int] = None,
    ) -> dict:
        """Llama al modelo y devuelve la respuesta cruda (dict).

        Raises:
            httpx.HTTPError: si el servicio no responde o devuelve error.
        """
        body: dict = {"state": state, "questions": questions}
        if self.model:
            body["model"] = self.model
        if min_confidence is not None:
            body["min_confidence"] = min_confidence
        if max_len is not None:
            body["max_len"] = max_len

        last_exc: Optional[Exception] = None
        for path in ("/v1/systemone", "/predict"):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(f"{self.base_url}{path}", json=body)
                    if resp.status_code == 404:
                        last_exc = httpx.HTTPStatusError(
                            "endpoint no encontrado", request=resp.request, response=resp
                        )
                        continue
                    resp.raise_for_status()
                    return resp.json()
            except httpx.HTTPStatusError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    last_exc = exc
                    continue
                raise
        if last_exc is not None:
            raise last_exc
        raise httpx.HTTPError("sin endpoint de decisión disponible")

    def health(self) -> dict:
        """Estado del servicio (best-effort)."""
        try:
            with httpx.Client(timeout=min(self.timeout, 3.0)) as client:
                resp = client.get(f"{self.base_url}/health")
                resp.raise_for_status()
                return {"reachable": True, "health": resp.json()}
        except Exception as exc:  # noqa: BLE001
            return {"reachable": False, "error": str(exc)}
