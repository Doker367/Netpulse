"""NetPulse — Export Inventory Router.

Exporta el inventario de dispositivos en formato CSV, JSON,
o descarga todos los backups de configuración como ZIP.
"""

import csv
import io
import json
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.middleware.auth import JWTBearer
from app.middleware.rbac import requires_role
from app.services import inventory_svc
from app.core.settings import BACKUP_DIR

router = APIRouter(prefix="/api/export", tags=["Export"])


@router.get(
    "/csv",
    dependencies=[Depends(JWTBearer()), Depends(requires_role("operator"))],
)
def export_csv():
    """Exporta dispositivos como CSV."""
    devices = inventory_svc.list_devices()
    if not devices:
        raise HTTPException(404, "No hay dispositivos para exportar")

    output = io.StringIO()
    fieldnames = ["id", "hostname", "port", "driver", "type", "group", "tags", "description"]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for d in devices:
        row = dict(d)
        if isinstance(row.get("tags"), list):
            row["tags"] = ";".join(row["tags"])
        writer.writerow(row)

    csv_content = output.getvalue()
    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=netpulse_devices_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv",
        },
    )


@router.get(
    "/json",
    dependencies=[Depends(JWTBearer()), Depends(requires_role("operator"))],
)
def export_json():
    """Exporta dispositivos como JSON."""
    devices = inventory_svc.list_devices()
    if not devices:
        raise HTTPException(404, "No hay dispositivos para exportar")

    json_content = json.dumps(devices, indent=2, ensure_ascii=False)
    return StreamingResponse(
        iter([json_content]),
        media_type="application/json",
        headers={
            "Content-Disposition": f"attachment; filename=netpulse_devices_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json",
        },
    )


@router.get(
    "/configs",
    dependencies=[Depends(JWTBearer()), Depends(requires_role("operator"))],
)
def export_configs():
    """Descarga todos los archivos de backup de configuración como ZIP."""
    backup_dir = Path(BACKUP_DIR)
    if not backup_dir.exists():
        raise HTTPException(404, "No hay directorio de backups")

    cfg_files = list(backup_dir.glob("*.cfg"))
    if not cfg_files:
        raise HTTPException(404, "No hay archivos de configuración para exportar")

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for cfg in cfg_files:
            zf.write(cfg, arcname=cfg.name)

    zip_buffer.seek(0)
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=netpulse_configs_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.zip",
        },
    )
