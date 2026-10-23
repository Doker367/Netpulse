"""API de retención: inventario legible y purga de auditoría confirmada."""

from fastapi import APIRouter, Depends, HTTPException, Request

from app.middleware.auth import JWTBearer
from app.middleware.rbac import get_current_user, requires_role
from app.models.schemas import (
    RetentionBackupsResponse, RetentionDryRunResponse, RetentionInventoryResponse,
    RetentionPruneRequest, RetentionPruneResponse,
)
from app.services import retention_svc

router = APIRouter(prefix="/api/retention", tags=["Retention"])
VIEWER = [Depends(JWTBearer()), Depends(requires_role("viewer"))]
ADMIN = [Depends(JWTBearer()), Depends(requires_role("admin"))]


@router.get("/inventory", dependencies=VIEWER, response_model=RetentionInventoryResponse)
def inventory():
    try:
        return retention_svc.inventory()
    except retention_svc.RetentionUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/backups", dependencies=VIEWER, response_model=RetentionBackupsResponse)
def backups():
    try:
        return retention_svc.list_backups()
    except retention_svc.RetentionUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/audit-prune", dependencies=ADMIN,
             response_model=RetentionDryRunResponse | RetentionPruneResponse)
def audit_prune(req: RetentionPruneRequest, request: Request):
    try:
        return retention_svc.prune_audit(req.dias, req.dry_run,
                                         get_current_user(request).get("sub", "anonymous"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except retention_svc.RetentionUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
