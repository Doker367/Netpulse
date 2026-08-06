"""NetPulse — Audit Log Router.

GET /api/audit — list immutable audit events.
Admin-only endpoint (auth middleware will be added later).
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.middleware.auth import JWTBearer
from app.middleware.rbac import requires_role
from app.services.audit_svc import get_events, get_audit_stats

router = APIRouter(prefix="/api/audit", tags=["Audit"])


@router.get(
    "",
    dependencies=[Depends(JWTBearer()), Depends(requires_role("admin"))],
)
def list_audit_events(
    limit: int = Query(100, ge=1, le=1000, description="Max events to return"),
    offset: int = Query(0, ge=0, description="Skip first N events"),
    device_id: Optional[str] = Query(None, description="Filter by device ID"),
    username: Optional[str] = Query(None, description="Filter by username"),
    action: Optional[str] = Query(None, description="Filter by action"),
):
    """List audit events (append-only, immutable log).

    Results are returned newest-first. Supports filtering and pagination.
    """
    events = get_events(
        limit=limit,
        offset=offset,
        device_id=device_id,
        username=username,
        action=action,
    )
    return {
        "total": len(events),
        "limit": limit,
        "offset": offset,
        "events": events,
    }


@router.get("/stats")
def audit_stats():
    """Get audit log statistics (total events, file size)."""
    return get_audit_stats()
