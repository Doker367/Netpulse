"""Ingesta central de lotes del colector: validación, idempotencia y estado."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from app.models.remote_schemas import RemoteIngestIn, RemoteSampleIn
from app.remote_collector.timeutil import utcnow
from app.services import inventory_svc
from app.services.remote import store

logger = logging.getLogger(__name__)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _reason(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "muestra inválida"
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ()) if part != "samples")
    message = str(first.get("msg", "inválida"))
    return (f"{location}: {message}" if location else message)[:200]


def _validate_samples(raw_samples: list[dict[str, Any]]) -> tuple[list[RemoteSampleIn], list[dict[str, Any]]]:
    valid: list[RemoteSampleIn] = []
    rejected: list[dict[str, Any]] = []
    for raw in raw_samples:
        if not isinstance(raw, dict):
            rejected.append({
                "device_id": None, "ts": None, "metric": None,
                "reason": "la muestra no es un objeto",
            })
            continue
        try:
            valid.append(RemoteSampleIn.model_validate(raw))
        except ValidationError as exc:
            rejected.append({
                "device_id": raw.get("device_id"),
                "ts": raw.get("ts"),
                "metric": raw.get("metric"),
                "reason": _reason(exc),
            })
    return valid, rejected


def _load_group(
    conn: sqlite3.Connection, site_id: str, device_id: str, ts: str
) -> dict[str, sqlite3.Row]:
    rows = conn.execute(
        "SELECT metric, value_num, value_bool, ok, probe_kind, error "
        "FROM remote_sample WHERE site_id=? AND device_id=? AND ts=?",
        (site_id, device_id, ts),
    ).fetchall()
    return {row["metric"]: row for row in rows}


def _inventory_map() -> tuple[dict[str, Any] | None, str | None]:
    available, error = inventory_svc.inventory_health()
    if not available:
        return None, error
    return {device["id"]: device for device in inventory_svc.list_devices()}, None


def ingest(
    site_id: str,
    collector_id: str,
    payload: RemoteIngestIn,
    remote_addr: str | None = None,
) -> dict[str, Any]:
    """Procesa un lote. Lanza ``RemoteStoreUnavailable`` si la DB falla."""
    valid, rejected = _validate_samples(payload.samples)
    received = utcnow()
    received_iso = received.isoformat(timespec="microseconds")
    sent_iso = _iso(payload.sent_at)
    clock_skew = (received - payload.sent_at.astimezone(timezone.utc)).total_seconds()
    time_quality = "skewed" if abs(clock_skew) > 300 else "ok"

    # Normaliza y deduplica dentro del lote para construir el estado; la
    # inserción conserva la clave única del servidor.
    rows: list[tuple[Any, ...]] = []
    keys: set[tuple[str, str]] = set()
    for sample in valid:
        ts = sample.ts.astimezone(timezone.utc).isoformat(timespec="microseconds")
        rows.append((
            site_id, collector_id, sample.device_id, sample.metric, ts,
            received_iso, payload.sync_batch_id,
            sample.value_num,
            None if sample.value_bool is None else int(sample.value_bool),
            1 if sample.ok else 0, sample.probe_kind, sample.error,
        ))
        keys.add((sample.device_id, ts))

    inventory, inventory_error = _inventory_map()
    accepted = 0
    duplicates = 0
    out_of_order = 0

    with store.transaction() as conn:
        existing = conn.execute(
            "SELECT * FROM remote_collector WHERE site_id=? AND collector_id=?",
            (site_id, collector_id),
        ).fetchone()
        prev_epoch = existing["buffer_epoch"] if existing else None
        incoming_epoch = payload.buffer.buffer_epoch
        epoch_changed = bool(prev_epoch and incoming_epoch and prev_epoch != incoming_epoch)
        epoch_changed_at = existing["buffer_epoch_changed_at"] if existing else None
        if epoch_changed:
            epoch_changed_at = received_iso
        buffer_epoch = incoming_epoch or prev_epoch

        for row in rows:
            cur = conn.execute(
                "INSERT INTO remote_sample "
                "(site_id, collector_id, device_id, metric, ts, received_at, "
                "sync_batch_id, value_num, value_bool, ok, probe_kind, error) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT (site_id, device_id, ts, metric) DO NOTHING",
                row,
            )
            if cur.rowcount:
                accepted += 1
            else:
                duplicates += 1

        for device_id, ts in sorted(keys, key=lambda item: item[1]):
            metrics = _load_group(conn, site_id, device_id, ts)
            reachable = metrics.get("reachable")
            if reachable is None:
                continue
            prev_state = conn.execute(
                "SELECT last_ts FROM remote_device_state WHERE site_id=? AND device_id=?",
                (site_id, device_id),
            ).fetchone()
            if prev_state is not None and ts < prev_state["last_ts"]:
                out_of_order += 1
                continue
            # ts == last_ts también se actualiza: un lote pudo cortar las
            # métricas del mismo ciclo (reachable/latency/loss) y la segunda
            # mitad debe completar el estado, no quedar ignorada.
            latency = metrics.get("latency_ms")
            loss = metrics.get("packet_loss_pct")
            reachable_bool = (
                None if reachable["value_bool"] is None
                else bool(reachable["value_bool"])
            )
            inv_match: int | None
            if inventory is None:
                inv_match = None
            else:
                inv_match = 1 if device_id in inventory else 0
            conn.execute(
                "INSERT INTO remote_device_state "
                "(site_id, device_id, collector_id, last_ts, last_received_at, "
                "reachable, latency_ms, packet_loss_pct, probe_kind, ok, error, "
                "inventory_match, inventory_error) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT (site_id, device_id) DO UPDATE SET "
                "collector_id=excluded.collector_id, last_ts=excluded.last_ts, "
                "last_received_at=excluded.last_received_at, "
                "reachable=excluded.reachable, latency_ms=excluded.latency_ms, "
                "packet_loss_pct=excluded.packet_loss_pct, "
                "probe_kind=excluded.probe_kind, ok=excluded.ok, error=excluded.error, "
                "inventory_match=excluded.inventory_match, "
                "inventory_error=excluded.inventory_error",
                (
                    site_id, device_id, collector_id, ts, received_iso,
                    None if reachable_bool is None else int(reachable_bool),
                    (
                        latency["value_num"]
                        if latency is not None and reachable_bool
                        else None
                    ),
                    loss["value_num"] if loss is not None else None,
                    reachable["probe_kind"],
                    int(reachable["ok"]),
                    reachable["error"],
                    inv_match,
                    inventory_error if inv_match is None else None,
                ),
            )

        prev_dropped = existing["dropped_total"] if existing else None
        prev_max = existing["dropped_total_max"] if existing else None
        incoming_dropped = payload.buffer.dropped_total
        if epoch_changed:
            dropped_max = incoming_dropped
        elif incoming_dropped is None:
            dropped_max = prev_max
        else:
            dropped_max = max(prev_max or 0, incoming_dropped)
        if epoch_changed:
            gap_since = _iso(payload.buffer.gap_since)
        else:
            incoming_gap = _iso(payload.buffer.gap_since)
            gap_since = (
                min(existing["gap_since"], incoming_gap)
                if existing and existing["gap_since"] and incoming_gap
                else (incoming_gap or (existing["gap_since"] if existing else None))
            )
        sync = payload.sync
        last_error = sync.last_error if sync is not None else (existing["last_sync_error"] if existing else None)
        last_error_at = (
            _iso(sync.last_error_at) if sync is not None and sync.last_error_at is not None
            else (existing["last_sync_error_at"] if existing else None)
        )
        sync_failures = (
            sync.consecutive_failures if sync is not None
            else (existing["sync_failures"] if existing else None)
        )
        auth_state = (
            sync.auth_state if sync is not None and sync.auth_state is not None
            else (existing["auth_state"] if existing else None)
        )

        conn.execute(
            "INSERT INTO remote_collector ("
            "site_id, collector_id, collector_version, probe_interval_s, buffer_epoch, "
            "buffer_epoch_changed_at, first_seen_at, last_seen_at, last_sent_at, "
            "clock_skew_s, time_quality, buffer_pending, buffer_oldest_ts, "
            "dropped_total, dropped_total_max, gap_since, paused_reason, "
            "last_batch_accepted, last_batch_duplicates, last_batch_rejected, "
            "last_sync_error, last_sync_error_at, sync_failures, auth_state, "
            "last_remote_addr) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (site_id, collector_id) DO UPDATE SET "
            "collector_version=excluded.collector_version, "
            "probe_interval_s=excluded.probe_interval_s, "
            "buffer_epoch=excluded.buffer_epoch, "
            "buffer_epoch_changed_at=excluded.buffer_epoch_changed_at, "
            "last_seen_at=excluded.last_seen_at, last_sent_at=excluded.last_sent_at, "
            "clock_skew_s=excluded.clock_skew_s, time_quality=excluded.time_quality, "
            "buffer_pending=excluded.buffer_pending, "
            "buffer_oldest_ts=excluded.buffer_oldest_ts, "
            "dropped_total=excluded.dropped_total, "
            "dropped_total_max=excluded.dropped_total_max, "
            "gap_since=excluded.gap_since, paused_reason=excluded.paused_reason, "
            "last_batch_accepted=excluded.last_batch_accepted, "
            "last_batch_duplicates=excluded.last_batch_duplicates, "
            "last_batch_rejected=excluded.last_batch_rejected, "
            "last_sync_error=excluded.last_sync_error, "
            "last_sync_error_at=excluded.last_sync_error_at, "
            "sync_failures=excluded.sync_failures, auth_state=excluded.auth_state, "
            "last_remote_addr=excluded.last_remote_addr",
            (
                site_id, collector_id, payload.collector_version,
                payload.probe_interval_s, buffer_epoch, epoch_changed_at,
                existing["first_seen_at"] if existing else received_iso,
                received_iso, sent_iso, clock_skew, time_quality,
                payload.buffer.pending, _iso(payload.buffer.oldest_pending_ts),
                (
                    incoming_dropped
                    if (epoch_changed or incoming_dropped is not None)
                    else prev_dropped
                ),
                dropped_max, gap_since, payload.buffer.paused_reason,
                accepted, duplicates, len(rejected),
                last_error, last_error_at, sync_failures, auth_state, remote_addr,
            ),
        )

    _maybe_prune(received)

    return {
        "status": "partial" if rejected else "ok",
        "site_id": site_id,
        "collector_id": collector_id,
        "server_time": received_iso,
        "accepted": accepted,
        "duplicates": duplicates,
        "rejected": len(rejected),
        "out_of_order": out_of_order,
        "clock_skew_s": round(clock_skew, 3),
        "rejected_samples": rejected,
    }


def _maybe_prune(now: datetime) -> None:
    try:
        with store.transaction() as conn:
            if store.should_prune(conn, now):
                store.prune_samples(conn, now)
    except (store.RemoteStoreUnavailable, sqlite3.Error) as exc:
        # La poda no debe hacer fallar un lote ya confirmado.
        logger.warning("[remote] poda falló: %s", exc)
