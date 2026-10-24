"""Cliente de sincronización del colector: lotes HTTP idempotentes."""

from __future__ import annotations

import logging
import random
import uuid
from typing import Any, Callable

import httpx

from app.remote_collector import __version__
from app.remote_collector.buffer import Buffer
from app.remote_collector.config import CollectorConfig
from app.remote_collector.timeutil import age_seconds, iso_now

logger = logging.getLogger(__name__)


class SyncError(RuntimeError):
    """Fallo de red o del servidor; el lote queda pendiente."""


class AuthError(SyncError):
    """401/403: la clave no sirve o está revocada."""


class PayloadTooLarge(SyncError):
    """413: hay que reducir el lote."""


class ContractError(SyncError):
    """422 o respuesta que no respeta el contrato; no se descartan datos."""


class SyncClient:
    """Cliente HTTP mínimo contra ``/api/remote``."""

    def __init__(
        self,
        config: CollectorConfig,
        api_key: str,
        transport: httpx.BaseTransport | None = None,
    ):
        verify: bool | str = config.central.verify_tls
        if config.central.ca_file is not None:
            verify = str(config.central.ca_file)
        kwargs: dict[str, Any] = {
            "base_url": config.central.base_url,
            "headers": {"Authorization": f"Bearer {api_key}"},
            "timeout": config.central.timeout_s,
            "verify": verify,
        }
        if transport is not None:
            kwargs["transport"] = transport
        self._client = httpx.Client(**kwargs)

    def close(self) -> None:
        self._client.close()

    def whoami(self) -> dict[str, Any]:
        try:
            response = self._client.get("api/remote/whoami")
        except httpx.HTTPError as exc:
            raise SyncError(f"error de red: {exc}") from exc
        self._raise_for_status(response)
        return _json_object(response)

    def post_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.post("api/remote/ingest", json=payload)
        except httpx.HTTPError as exc:
            raise SyncError(f"error de red: {exc}") from exc
        self._raise_for_status(response)
        return _json_object(response)

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code == 200:
            return
        detail = _detail(response)
        if response.status_code in (401, 403):
            raise AuthError(f"HTTP {response.status_code}: {detail}")
        if response.status_code == 413:
            raise PayloadTooLarge(f"HTTP 413: {detail}")
        if response.status_code == 422:
            raise ContractError(f"HTTP 422: {detail}")
        raise SyncError(f"HTTP {response.status_code}: {detail}")


def _detail(response: httpx.Response) -> str:
    try:
        data = response.json()
        if isinstance(data, dict) and data.get("detail"):
            return str(data["detail"])[:300]
    except ValueError:
        pass
    return (response.text or "")[:300]


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise SyncError("respuesta no es JSON") from exc
    if not isinstance(data, dict):
        raise SyncError("respuesta no es un objeto JSON")
    return data


def heartbeat_due(buffer: Buffer, config: CollectorConfig) -> bool:
    last = buffer.get_state("last_success_at")
    if not last:
        return True
    try:
        return age_seconds(last) >= config.sync.heartbeat_s
    except (ValueError, TypeError):
        return True


def build_payload(
    buffer: Buffer,
    config: CollectorConfig,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    summary = buffer.summary()
    samples = []
    for row in rows:
        samples.append({
            "device_id": row["device_id"],
            "ts": row["ts"],
            "metric": row["metric"],
            "value_num": row["value_num"],
            "value_bool": (
                None if row["value_bool"] is None else bool(row["value_bool"])
            ),
            "ok": bool(row["ok"]),
            "probe_kind": row["probe_kind"],
            "error": row["error"],
        })
    return {
        "schema_version": 1,
        "collector_version": __version__,
        "probe_interval_s": config.probe.interval_s,
        "sync_batch_id": str(uuid.uuid4()),
        "sent_at": iso_now(),
        "buffer": {
            "buffer_epoch": summary["buffer_epoch"],
            "pending": summary["pending"],
            "oldest_pending_ts": summary["oldest_pending_ts"],
            "dropped_total": summary["dropped_total"],
            "gap_since": summary["gap_since"],
            "paused_reason": summary["paused_reason"],
        },
        "sync": {
            key: value for key, value in buffer.sync_state().items()
            if key in ("last_error", "last_error_at", "consecutive_failures", "auth_state")
        },
        "samples": samples,
    }


def sync_once(
    buffer: Buffer,
    client: SyncClient,
    config: CollectorConfig,
    *,
    force_heartbeat: bool = False,
) -> dict[str, Any] | None:
    """Sincroniza un lote o envía heartbeat.

    Devuelve la respuesta del servidor (con contadores locales) o ``None`` si
    no tocaba enviar nada. Los fallos se propagan como ``SyncError``; el
    llamador decide el backoff. Nunca se borra nada antes de un 200.
    """
    rows = buffer.peek_pending(min(config.sync.batch_max_samples, 500))
    if not rows and not force_heartbeat and not heartbeat_due(buffer, config):
        return None

    payload = build_payload(buffer, config, rows)
    ids = [int(row["id"]) for row in rows]
    triples = {(row["device_id"], row["ts"], row["metric"]) for row in rows}
    try:
        response = client.post_batch(payload)
    except SyncError as exc:
        buffer.mark_attempt(ids)
        buffer.record_sync_failure(str(exc), auth_error=isinstance(exc, AuthError))
        raise

    rejected = response.get("rejected_samples") or []
    if not isinstance(rejected, list):
        buffer.mark_attempt(ids)
        buffer.record_sync_failure("respuesta rechazada inválida")
        raise ContractError("rejected_samples no es una lista")
    rejected_count = response.get("rejected", len(rejected))
    if rejected_count != len(rejected):
        buffer.mark_attempt(ids)
        buffer.record_sync_failure("rejected no coincide con rejected_samples")
        raise ContractError("rejected no coincide con rejected_samples")

    rejected_triples: set[tuple[str, str, str]] = set()
    by_reason: dict[str, list[tuple[str, str, str]]] = {}
    for item in rejected:
        if not isinstance(item, dict):
            buffer.mark_attempt(ids)
            buffer.record_sync_failure("rejected_samples inválido")
            raise ContractError("rejected_samples inválido")
        triple = (item.get("device_id"), item.get("ts"), item.get("metric"))
        if triple not in triples:
            buffer.mark_attempt(ids)
            buffer.record_sync_failure("muestra rechazada fuera del lote")
            raise ContractError("el servidor rechazó una muestra que no está en el lote")
        rejected_triples.add(triple)  # type: ignore[arg-type]
        reason = str(item.get("reason") or "rechazado por el servidor")
        by_reason.setdefault(reason, []).append(triple)  # type: ignore[arg-type]

    for reason, items in by_reason.items():
        buffer.mark_dead(items, reason)
    synced_ids = [
        int(row["id"]) for row in rows
        if (row["device_id"], row["ts"], row["metric"]) not in rejected_triples
    ]
    buffer.mark_synced(synced_ids)
    buffer.record_sync_success()
    response = dict(response)
    response["marked_synced"] = len(synced_ids)
    response["marked_dead"] = len(rejected_triples)
    return response


def next_delay(
    consecutive_failures: int,
    base_s: float,
    max_s: float,
    rng: Callable[[float, float], float] | None = None,
) -> float:
    """Backoff exponencial con jitter; ``consecutive_failures`` >= 1."""
    rng = rng or random.uniform
    exponent = min(max(0, consecutive_failures), 10)
    raw = min(max_s, base_s * (2 ** exponent))
    return max(0.1, raw * rng(0.5, 1.5))
