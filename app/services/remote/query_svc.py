"""Consultas de solo lectura sobre el almacén de colectores remotos."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.remote_collector.timeutil import iso_now, parse_iso, utcnow
from app.services.remote import store

MAX_POINTS = 20000
MIN_STALE_S = 300


class SiteNotFound(LookupError):
    pass


class DeviceNotFound(LookupError):
    pass


class WindowTooLarge(ValueError):
    pass


def _age_seconds(value: str | None, now: datetime) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, (now - parse_iso(value)).total_seconds())
    except (ValueError, TypeError):
        return None


def _stale_after(probe_interval_s: int | None) -> int:
    interval = int(probe_interval_s or 0)
    return max(3 * interval, MIN_STALE_S)


def _contact(last_seen_at: str | None, stale_after_s: int, now: datetime) -> tuple[str, float | None]:
    age = _age_seconds(last_seen_at, now)
    if age is None:
        return "stale", None
    return ("fresh" if age <= stale_after_s else "stale"), age


def _configured_collector_keys() -> dict[str, list[dict[str, Any]]]:
    """Sites declarados por claves de colector vigentes (aunque nunca reporten).

    Es la misma fuente que usa ``watchdog_svc`` para alertar un site sin
    heartbeat: una API key de tipo ``collector`` activa con ``site_id`` y
    ``collector_id``. Se lee en modo perezoso y cualquier fallo se degrada a
    "sin claves": listar sites no puede caerse por el almacén de API keys.
    """
    try:
        from app.services.security import api_keys_svc

        keys = api_keys_svc.list_api_keys()
    except Exception:  # noqa: BLE001 - la lectura de sites nunca debe fallar por esto
        return {}

    grouped: dict[str, list[dict[str, Any]]] = {}
    for key in keys:
        if key.get("kind") != "collector" or key.get("revoked"):
            continue
        site_id = key.get("site_id")
        collector_id = key.get("collector_id")
        if not site_id or not collector_id:
            continue
        grouped.setdefault(str(site_id), []).append(key)
    return grouped


def _never_collector_payload(key: dict[str, Any]) -> dict[str, Any]:
    """Colector provisionado del que no hay ni una fila en el central.

    Todo lo que no se ha observado va a ``None`` (nunca a 0): no hay versión,
    cadencia, buffer, lote ni sincronización que reportar todavía.
    """
    return {
        "collector_id": key.get("collector_id"),
        "version": None,
        "contact": "never",
        "probe_interval_s": None,
        "stale_after_s": None,
        "first_seen_at": None,
        "last_seen_at": None,
        "last_contact_age_s": None,
        "time_quality": "unknown",
        "clock_skew_s": None,
        "buffer": {
            "pending": None,
            "oldest_pending_ts": None,
            "dropped_total": None,
            "dropped_total_max": None,
            "gap_since": None,
            "paused_reason": None,
            "buffer_epoch": None,
        },
        "last_batch": {"accepted": None, "duplicates": None, "rejected": None},
        "sync": {
            "last_error": None,
            "last_error_at": None,
            "consecutive_failures": None,
            "auth_state": None,
        },
    }


def _never_site_payload(site_id: str, keys: list[dict[str, Any]]) -> dict[str, Any]:
    """Site provisionado sin heartbeat: estado honesto y sin métricas inventadas."""
    return {
        "site_id": site_id,
        "contact": "never",
        "last_seen_at": None,
        "last_contact_age_s": None,
        "collectors": [_never_collector_payload(key) for key in keys],
        "devices": {
            "total": 0,
            "reachable": 0,
            "unreachable": 0,
            "unverifiable": 0,
            "unmatched": 0,
        },
    }


def _collector_payload(row, now: datetime) -> dict[str, Any]:
    stale_after = _stale_after(row["probe_interval_s"])
    contact, age = _contact(row["last_seen_at"], stale_after, now)
    return {
        "collector_id": row["collector_id"],
        "version": row["collector_version"],
        "contact": contact,
        "probe_interval_s": row["probe_interval_s"],
        "stale_after_s": stale_after,
        "first_seen_at": row["first_seen_at"],
        "last_seen_at": row["last_seen_at"],
        "last_contact_age_s": None if age is None else int(round(age)),
        "time_quality": row["time_quality"] or "unknown",
        "clock_skew_s": row["clock_skew_s"],
        "buffer": {
            "pending": row["buffer_pending"],
            "oldest_pending_ts": row["buffer_oldest_ts"],
            "dropped_total": row["dropped_total"],
            "dropped_total_max": row["dropped_total_max"],
            "gap_since": row["gap_since"],
            "paused_reason": row["paused_reason"],
            "buffer_epoch": row["buffer_epoch"],
        },
        "last_batch": {
            "accepted": row["last_batch_accepted"],
            "duplicates": row["last_batch_duplicates"],
            "rejected": row["last_batch_rejected"],
        },
        "sync": {
            "last_error": row["last_sync_error"],
            "last_error_at": row["last_sync_error_at"],
            "consecutive_failures": row["sync_failures"],
            "auth_state": row["auth_state"],
        },
    }


def list_sites(
    now: datetime | None = None,
    *,
    include_never: bool | None = None,
) -> dict[str, Any]:
    """Lista los sites conocidos y los provisionados sin heartbeat.

    ``include_never`` controla los sites que tienen una clave de colector
    vigente pero ninguna fila en el central: aparecen con ``contact="never"``
    y ``last_seen_at=None`` (sin inventar métricas). Por defecto se incluyen
    en la lectura de la API (llamada sin reloj inyectado). Cuando el llamador
    inyecta ``now`` se excluyen: esa es la lectura determinista que usa el
    watchdog para decidir episodios, y él ya aplica su propia gracia de
    despliegue a las claves recién emitidas.
    """
    current = now or utcnow()
    generated = iso_now()
    if include_never is None:
        include_never = now is None
    if not store.db_exists():
        return {"generated_at": generated, "sites": []}

    with store.read_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM remote_collector ORDER BY site_id, collector_id"
        ).fetchall()
        counts = {
            row["site_id"]: {
                "total": int(row["total"] or 0),
                "reachable": int(row["reachable"] or 0),
                "unreachable": int(row["unreachable"] or 0),
                "unverifiable": int(row["unverifiable"] or 0),
                "unmatched": int(row["unmatched"] or 0),
            }
            for row in conn.execute(
                "SELECT site_id, COUNT(*) AS total, "
                "SUM(CASE WHEN ok=1 AND reachable=1 THEN 1 ELSE 0 END) AS reachable, "
                "SUM(CASE WHEN ok=1 AND reachable=0 THEN 1 ELSE 0 END) AS unreachable, "
                "SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) AS unverifiable, "
                "SUM(CASE WHEN inventory_match=0 THEN 1 ELSE 0 END) AS unmatched "
                "FROM remote_device_state GROUP BY site_id"
            ).fetchall()
        }

    sites: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = _collector_payload(row, current)
        site = sites.setdefault(row["site_id"], {
            "site_id": row["site_id"],
            "contact": payload["contact"],
            "last_seen_at": payload["last_seen_at"],
            "last_contact_age_s": payload["last_contact_age_s"],
            "collectors": [],
            "devices": counts.get(row["site_id"], {
                "total": 0, "reachable": 0, "unreachable": 0,
                "unverifiable": 0, "unmatched": 0,
            }),
        })
        site["collectors"].append(payload)
        # El site queda en contacto si al menos un collector lo está.
        if payload["contact"] == "fresh":
            if (
                site["contact"] != "fresh"
                or site["last_contact_age_s"] is None
                or payload["last_contact_age_s"] is None
                or payload["last_contact_age_s"] <= site["last_contact_age_s"]
            ):
                site["contact"] = "fresh"
                site["last_seen_at"] = payload["last_seen_at"]
                site["last_contact_age_s"] = payload["last_contact_age_s"]
        elif site["contact"] != "fresh":
            site["contact"] = "stale"
            if site["last_contact_age_s"] is None or (
                payload["last_contact_age_s"] is not None
                and payload["last_contact_age_s"] < site["last_contact_age_s"]
            ):
                site["last_seen_at"] = payload["last_seen_at"]
                site["last_contact_age_s"] = payload["last_contact_age_s"]

    if include_never:
        for site_id, keys in sorted(_configured_collector_keys().items()):
            if site_id not in sites:
                sites[site_id] = _never_site_payload(site_id, keys)

    return {
        "generated_at": generated,
        "sites": [sites[key] for key in sorted(sites)],
    }


def _require_site(conn, site_id: str) -> None:
    row = conn.execute(
        "SELECT 1 FROM remote_collector WHERE site_id=? LIMIT 1", (site_id,)
    ).fetchone()
    if row is not None:
        return
    # Un site provisionado (clave de colector vigente) existe aunque aún no
    # tenga heartbeat; su detalle es honesto: contact="never", sin métricas.
    if site_id in _configured_collector_keys():
        return
    raise SiteNotFound(site_id)


def get_site(site_id: str, now: datetime | None = None) -> dict[str, Any]:
    current = now or utcnow()
    if not store.db_exists():
        raise SiteNotFound(site_id)
    with store.read_connection() as conn:
        _require_site(conn, site_id)
        oldest = conn.execute(
            "SELECT MIN(ts) AS ts FROM remote_sample WHERE site_id=?", (site_id,)
        ).fetchone()["ts"]
        latest = conn.execute(
            "SELECT * FROM remote_collector WHERE site_id=? "
            "ORDER BY last_seen_at DESC, collector_id LIMIT 1",
            (site_id,),
        ).fetchone()
    data = list_sites(current, include_never=True)
    site = next((item for item in data["sites"] if item["site_id"] == site_id), None)
    if site is None:
        raise SiteNotFound(site_id)
    site = dict(site)
    site["retention_days"] = store.retention_days()
    site["oldest_sample_ts"] = oldest
    site["buffer_epoch_changed_at"] = (
        latest["buffer_epoch_changed_at"] if latest is not None else None
    )
    return site


def list_devices(
    site_id: str,
    include_unmatched: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or utcnow()
    if not store.db_exists():
        raise SiteNotFound(site_id)
    with store.read_connection() as conn:
        _require_site(conn, site_id)
        latest = conn.execute(
            "SELECT * FROM remote_collector WHERE site_id=? "
            "ORDER BY last_seen_at DESC, collector_id LIMIT 1",
            (site_id,),
        ).fetchone()
        rows = conn.execute(
            "SELECT * FROM remote_device_state WHERE site_id=? ORDER BY device_id",
            (site_id,),
        ).fetchall()

    if latest is None:
        # Site provisionado sin heartbeat: existe, pero no hay nada medido.
        return {
            "site_id": site_id,
            "generated_at": iso_now(),
            "contact": "never",
            "last_seen_at": None,
            "stale_after_s": None,
            "devices": [],
        }

    stale_after = _stale_after(latest["probe_interval_s"])
    contact, _ = _contact(latest["last_seen_at"], stale_after, current)

    devices = []
    for row in rows:
        if not include_unmatched and row["inventory_match"] == 0:
            continue
        age = _age_seconds(row["last_received_at"], current)
        is_current = contact == "fresh" and age is not None and age <= stale_after
        devices.append({
            "device_id": row["device_id"],
            "source": {
                "kind": "remote_collector",
                "site_id": site_id,
                "collector_id": row["collector_id"],
            },
            "inventory_match": (
                None if row["inventory_match"] is None else bool(row["inventory_match"])
            ),
            "inventory_error": row["inventory_error"],
            "freshness": {
                "state": "fresh" if is_current else "stale",
                "current": is_current,
                "last_sample_ts": row["last_ts"],
                "last_received_at": row["last_received_at"],
                "age_s": None if age is None else int(round(age)),
            },
            "ok": bool(row["ok"]),
            "reachable": None if row["reachable"] is None else bool(row["reachable"]),
            "latency_ms": row["latency_ms"],
            "packet_loss_pct": row["packet_loss_pct"],
            "probe_kind": row["probe_kind"],
            "error": row["error"],
        })

    return {
        "site_id": site_id,
        "generated_at": iso_now(),
        "contact": contact,
        "last_seen_at": latest["last_seen_at"],
        "stale_after_s": stale_after,
        "devices": devices,
    }


def history(
    site_id: str,
    device_id: str,
    hours: float = 6,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or utcnow()
    if not store.db_exists():
        raise SiteNotFound(site_id)
    cutoff = (current - timedelta(hours=hours)).astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    )
    with store.read_connection() as conn:
        _require_site(conn, site_id)
        any_sample = conn.execute(
            "SELECT 1 FROM remote_sample WHERE site_id=? AND device_id=? LIMIT 1",
            (site_id, device_id),
        ).fetchone()
        if any_sample is None:
            raise DeviceNotFound(device_id)
        rows = conn.execute(
            "SELECT ts, collector_id, metric, value_num, value_bool, ok, probe_kind, error "
            "FROM remote_sample WHERE site_id=? AND device_id=? AND ts >= ? "
            "ORDER BY ts ASC, metric ASC",
            (site_id, device_id, cutoff),
        ).fetchall()
        latest = conn.execute(
            "SELECT * FROM remote_collector WHERE site_id=? "
            "ORDER BY last_seen_at DESC, collector_id LIMIT 1",
            (site_id,),
        ).fetchone()

    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        point = grouped.setdefault(row["ts"], {
            "ts": row["ts"],
            "collector_id": row["collector_id"],
            "ok": None,
            "reachable": None,
            "latency_ms": None,
            "packet_loss_pct": None,
            "probe_kind": row["probe_kind"],
            "error": None,
        })
        if row["metric"] == "reachable":
            point["collector_id"] = row["collector_id"]
            point["ok"] = bool(row["ok"])
            point["reachable"] = (
                None if row["value_bool"] is None else bool(row["value_bool"])
            )
            point["probe_kind"] = row["probe_kind"]
            point["error"] = row["error"]
        elif row["metric"] == "latency_ms":
            point["latency_ms"] = row["value_num"]
            if point["ok"] is None:
                point["ok"] = bool(row["ok"])
                point["probe_kind"] = row["probe_kind"]
                point["error"] = row["error"]
        elif row["metric"] == "packet_loss_pct":
            point["packet_loss_pct"] = row["value_num"]
            if point["ok"] is None:
                point["ok"] = bool(row["ok"])
                point["probe_kind"] = row["probe_kind"]
                point["error"] = row["error"]

    points = [grouped[key] for key in sorted(grouped)]
    if len(points) > MAX_POINTS:
        raise WindowTooLarge(
            f"ventana demasiado grande: {len(points)} puntos > {MAX_POINTS}; reduzca hours"
        )
    latest_collector = latest["collector_id"] if latest is not None else None
    return {
        "site_id": site_id,
        "device_id": device_id,
        "hours": hours,
        "source": {
            "kind": "remote_collector",
            "site_id": site_id,
            "collector_id": latest_collector,
        },
        "count": len(points),
        "first_ts": points[0]["ts"] if points else None,
        "last_ts": points[-1]["ts"] if points else None,
        "points": points,
        "coverage": {
            "buffer_gap_since": latest["gap_since"] if latest is not None else None,
            "dropped_samples_reported": (
                latest["dropped_total"] if latest is not None else None
            ),
            "expected_samples": None,
        },
    }
