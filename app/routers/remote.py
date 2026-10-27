"""Endpoints de colectores remotos por site.

- ``POST /api/remote/ingest``: server-to-server, API key ``kind=collector``.
- ``GET  /api/remote/whoami``: valida la clave y el binding site/collector.
- ``GET  /api/remote/sites...``: lectura para ``viewer`` o superior.

No escribe inventario ni configuración; solo almacena y expone mediciones.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError

from app.core.limiter import limiter
from app.middleware.auth import JWTBearer
from app.middleware.rbac import requires_role
from app.models.remote_schemas import RemoteIngestIn
from app.services.remote import ingest_svc, query_svc, store
from app.utils.async_io import BlockingCallTimeout, env_timeout, run_blocking

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/remote", tags=["Remote Collectors"])

VIEWER = [Depends(JWTBearer()), Depends(requires_role("viewer"))]
MAX_BODY_BYTES = 1024 * 1024
MAX_SAMPLES = 500

#: Tope de una ingesta completa (SQLite + validación de inventario). Un lote
#: normal tarda decenas de ms; 20 s es una red de seguridad, no el caso real.
_INGEST_TIMEOUT_ENV = "NETPULSE_REMOTE_INGEST_TIMEOUT_S"
_INGEST_TIMEOUT_S = 20.0


def collector_auth(request: Request) -> dict[str, Any]:
    """Valida una API key de colector y su binding site/collector."""
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail="API key de colector requerida",
            headers={"WWW-Authenticate": "Bearer"},
        )
    parts = header.split(None, 1)
    if len(parts) != 2 or not parts[1].strip():
        raise HTTPException(
            status_code=401,
            detail="API key de colector requerida",
            headers={"WWW-Authenticate": "Bearer"},
        )
    key = parts[1].strip()
    from app.services.security import api_keys_svc

    info = api_keys_svc.validate_api_key(key)
    if info is None:
        raise HTTPException(
            status_code=401,
            detail="API key inválida o revocada",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if info.get("kind", "user") != "collector":
        raise HTTPException(
            status_code=403,
            detail="La clave no es de tipo collector",
        )
    site_id = info.get("site_id")
    collector_id = info.get("collector_id")
    if (
        not isinstance(site_id, str) or not isinstance(collector_id, str)
        or not site_id or not collector_id
    ):
        raise HTTPException(
            status_code=403,
            detail="La clave no corresponde a este colector",
        )
    request.state.collector = {
        "name": info.get("name"),
        "site_id": site_id,
        "collector_id": collector_id,
        "key_id": info.get("id"),
    }
    # Para que AuditMiddleware registre apikey:<name>; no concede RBAC.
    request.state.user = {
        "sub": f"apikey:{info.get('name', 'collector')}",
        "role": None,
        "api_key_id": info.get("id"),
    }
    return request.state.collector


@router.post("/ingest")
@limiter.limit("120/minute")
async def ingest(
    request: Request,
    collector: dict[str, Any] = Depends(collector_auth),
):
    """Recibe un lote; idempotente por (site, device, ts, metric)."""
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Lote demasiado grande: máximo 500 muestras y 1 MiB",
        )
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="JSON inválido") from exc
    if isinstance(data, dict) and isinstance(data.get("samples"), list):
        if len(data["samples"]) > MAX_SAMPLES:
            raise HTTPException(
                status_code=413,
                detail="Lote demasiado grande: máximo 500 muestras y 1 MiB",
            )
    try:
        payload = RemoteIngestIn.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    if payload.schema_version != 1:
        raise HTTPException(
            status_code=422,
            detail=f"schema_version no soportada: {payload.schema_version}",
        )

    try:
        # SQLite + lectura del inventario en un hilo: la idempotencia y el
        # orden los garantiza la clave única/transacción del store, no el
        # hilo. Solo cambia dónde corre, no lo que hace.
        return await run_blocking(
            ingest_svc.ingest,
            collector["site_id"],
            collector["collector_id"],
            payload,
            timeout=env_timeout(_INGEST_TIMEOUT_ENV, _INGEST_TIMEOUT_S),
            label=f"ingesta del colector {collector['collector_id']}",
            remote_addr=request.client.host if request.client else None,
        )
    except BlockingCallTimeout as exc:
        raise HTTPException(
            status_code=504,
            detail=(
                "La ingesta superó el tiempo máximo de espera; "
                "reintenta el lote (es idempotente)"
            ),
        ) from exc
    except store.RemoteStoreUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Almacén remoto no disponible: {exc}",
        ) from exc


@router.get("/whoami")
def whoami(collector: dict[str, Any] = Depends(collector_auth)):
    from app.remote_collector.timeutil import iso_now

    return {
        "site_id": collector["site_id"],
        "collector_id": collector["collector_id"],
        "key_name": collector.get("name"),
        "key_id": collector.get("key_id"),
        "server_time": iso_now(),
    }


@router.get("/sites", dependencies=VIEWER)
def list_sites():
    """Sites que han contactado al menos una vez; si no, lista vacía."""
    try:
        return query_svc.list_sites()
    except store.RemoteStoreUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Almacén remoto no disponible: {exc}",
        ) from exc


@router.get("/watchdog", dependencies=VIEWER)
def watchdog_status():
    """Estado del watchdog de sites enmudecidos y sus alertas activas.

    ``active_count > 0`` significa que hay sites sin contacto (o sin heartbeat)
    que ya se notificaron por los canales existentes. No evalúa: solo lee el
    estado persistido y el último ciclo ejecutado.
    """
    from app.services.remote import watchdog_svc

    return watchdog_svc.status_snapshot()


@router.get("/sites/{site_id}", dependencies=VIEWER)
def get_site(site_id: str):
    try:
        return query_svc.get_site(site_id)
    except query_svc.SiteNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Site sin contacto registrado: {site_id}",
        ) from exc
    except store.RemoteStoreUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Almacén remoto no disponible: {exc}",
        ) from exc


@router.get("/sites/{site_id}/devices", dependencies=VIEWER)
def list_devices(
    site_id: str,
    include_unmatched: bool = Query(True),
):
    try:
        return query_svc.list_devices(site_id, include_unmatched=include_unmatched)
    except query_svc.SiteNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Site sin contacto registrado: {site_id}",
        ) from exc
    except store.RemoteStoreUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Almacén remoto no disponible: {exc}",
        ) from exc


@router.get("/sites/{site_id}/devices/{device_id}/history", dependencies=VIEWER)
def history(
    site_id: str,
    device_id: str,
    hours: float = Query(6, ge=0.1, le=168),
):
    try:
        return query_svc.history(site_id, device_id, hours=hours)
    except query_svc.SiteNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Site sin contacto registrado: {site_id}",
        ) from exc
    except query_svc.DeviceNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Dispositivo sin muestras para el site {site_id}: {device_id}",
        ) from exc
    except query_svc.WindowTooLarge as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except store.RemoteStoreUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Almacén remoto no disponible: {exc}",
        ) from exc
