"""NetPulse — Integrations Router.

Proxy Loki (log viewer del dashboard), sync NetBox, reportes CSV.
"""

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse

from app.middleware.auth import JWTBearer
from app.middleware.rbac import requires_role
from app.services.integrations import loki_proxy, netbox_sync
from app.services.reports import csv_report

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["Integrations"])

AUTH = [Depends(JWTBearer()), Depends(requires_role("viewer"))]
ADMIN = [Depends(JWTBearer()), Depends(requires_role("admin"))]


# ── Loki Log Viewer ──────────────────────────────────────────

@router.get("/logs/query", dependencies=AUTH)
def logs_query(
    query: str = Query('{compose_project="netpulse"}'),
    hours: int = Query(1, ge=1, le=168),
    limit: int = Query(100, ge=1, le=1000),
):
    """Consulta logs en Loki para el dashboard."""
    end = int(time.time())
    start = end - hours * 3600
    return loki_proxy.query_loki_range(query, start, end, limit)


@router.get("/logs/labels", dependencies=AUTH)
def logs_labels():
    """Lista las labels conocidas por Loki."""
    return loki_proxy.list_loki_labels()


# ── NetBox Sync ──────────────────────────────────────────────

@router.get("/netbox/devices", dependencies=AUTH)
def netbox_devices():
    """Lista dispositivos desde NetBox (fuente de verdad)."""
    return netbox_sync.fetch_netbox_devices()


@router.post("/netbox/sync", dependencies=ADMIN)
def netbox_sync_inventory():
    """Sincroniza dispositivos de NetBox hacia el inventario local."""
    added, errors = netbox_sync.sync_netbox_to_inventory()
    return {"added": added, "errors": errors}


# ── CSV Reports ──────────────────────────────────────────────

@router.get("/reports/inventory.csv", dependencies=AUTH, response_class=PlainTextResponse)
def inventory_csv():
    """Reporte CSV del inventario completo."""
    return csv_report.generate_inventory_csv()


@router.get("/reports/health.csv", dependencies=AUTH, response_class=PlainTextResponse)
def health_csv():
    """Reporte CSV con health scores de todos los dispositivos."""
    from app.services.ops import health_svc
    rows = health_svc.health_all()
    return csv_report.generate_devices_csv(rows)


# ── PDF Reports ──────────────────────────────────────────────

def _pdf_response(pdf_path: str, filename: str):
    """Devuelve el PDF generado como attachment."""
    from fastapi.responses import FileResponse
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=filename,
    )


@router.get("/reports/inventory.pdf", dependencies=AUTH)
def inventory_pdf():
    """Reporte PDF del inventario completo."""
    from app.services.reports import pdf_report
    info = pdf_report.generate_inventory_pdf()
    return _pdf_response(info["path"], "inventory.pdf")


@router.get("/reports/health.pdf", dependencies=AUTH)
def health_pdf():
    """Reporte PDF con health scores."""
    from app.services.reports import pdf_report
    info = pdf_report.generate_health_pdf()
    return _pdf_response(info["path"], "health.pdf")


@router.get("/reports/availability.pdf", dependencies=AUTH)
def availability_pdf(device_id: str = Query("", description="Filtrar por dispositivo")):
    """Reporte PDF de disponibilidad."""
    from app.services.reports import pdf_report
    info = pdf_report.generate_availability_pdf(device_id)
    return _pdf_response(info["path"], "availability.pdf")
