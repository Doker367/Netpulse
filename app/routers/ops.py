"""NetPulse — Ops Router.

Expone ventanas de mantenimiento, drift detection, health score,
proyección de capacidad, versiones de config y backup de NetPulse.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.middleware.auth import JWTBearer
from app.middleware.rbac import requires_role
from app.services.ops import (
    maintenance_svc,
    drift_svc,
    config_versions_svc,
    health_svc,
    capacity_svc,
    backup_netpulse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ops", tags=["Ops"])

AUTH = [Depends(JWTBearer()), Depends(requires_role("viewer"))]
ADMIN = [Depends(JWTBearer()), Depends(requires_role("admin"))]


# ── Maintenance Windows ──────────────────────────────────────

@router.get("/maintenance", dependencies=AUTH)
def list_maintenance():
    return maintenance_svc.list_windows()


@router.get("/maintenance/active", dependencies=AUTH)
def active_maintenance():
    return maintenance_svc.active_windows()


@router.post("/maintenance", dependencies=ADMIN, status_code=201)
def create_maintenance(payload: dict):
    try:
        return maintenance_svc.create_window(
            device_id=payload["device_id"],
            start_iso=payload["start"],
            end_iso=payload["end"],
            reason=payload.get("reason", ""),
        )
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.delete("/maintenance/{window_id}", dependencies=ADMIN, status_code=204)
def delete_maintenance(window_id: str):
    if not maintenance_svc.delete_window(window_id):
        raise HTTPException(404, "Ventana no encontrada")


# ── Drift Detection ──────────────────────────────────────────

@router.get("/drift/{device_id}", dependencies=AUTH)
def check_drift(device_id: str):
    return drift_svc.check_drift(device_id)


@router.post("/drift/check-all", dependencies=ADMIN)
def run_drift_all():
    return drift_svc.run_drift_check_all()


# ── Config Versions ──────────────────────────────────────────

@router.get("/config-versions", dependencies=AUTH)
def index_versions():
    return config_versions_svc.index_backups()


@router.get("/config-versions/{device_id}/{filename}", dependencies=AUTH)
def get_version(device_id: str, filename: str):
    v = config_versions_svc.get_version(device_id, filename)
    if not v:
        raise HTTPException(404, "Versión no encontrada")
    return v


@router.get("/config-versions/diff/{device_id}", dependencies=AUTH)
def diff_versions(device_id: str, a: str = Query(...), b: str = Query(...)):
    return {"diff": config_versions_svc.diff_versions(device_id, a, b)}


# ── Health Score ─────────────────────────────────────────────

@router.get("/health/all", dependencies=AUTH)
def all_health_scores():
    return health_svc.health_all()


@router.get("/health/{device_id}", dependencies=AUTH)
def device_health_score(device_id: str):
    return health_svc.health_score(device_id)


# ── Capacity Projection ──────────────────────────────────────

@router.get("/capacity/{device_id}", dependencies=AUTH)
def capacity_projection(device_id: str, days: int = Query(90, ge=1, le=365)):
    return capacity_svc.project_growth(device_id, days)


# ── NetPulse Self Backup ─────────────────────────────────────

@router.post("/backup", dependencies=ADMIN, status_code=201)
def create_self_backup():
    return backup_netpulse.create_backup()
