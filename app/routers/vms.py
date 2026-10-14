"""NetPulse — Router de máquinas señuelo (VMs con QEMU).

Permite crear VMs, subir ISO, arrancarlas/pararlas y ver su consola VNC.
Ver `app/services/deception/vm_svc.py`. Requiere QEMU en el host.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.middleware.auth import JWTBearer
from app.middleware.rbac import requires_role
from app.services.deception import vm_svc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/vms", tags=["VMs (deception)"])

AUTH = [Depends(JWTBearer()), Depends(requires_role("viewer"))]
ADMIN = [Depends(JWTBearer()), Depends(requires_role("admin"))]


class VmCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, examples=["decoy-win"])
    os: str = Field(default="linux", examples=["windows", "ubuntu", "kali"])
    arch: str = Field(default="x86_64", examples=["x86_64", "aarch64"])
    ram_mb: int = Field(default=1024, ge=256, le=16384)
    cpus: int = Field(default=1, ge=1, le=16)
    iso: Optional[str] = Field(default=None, description="Nombre de ISO subida")


@router.get("", dependencies=AUTH)
def list_vms():
    """Lista las VMs señuelo y su estado + disponibilidad de QEMU."""
    return {"enabled": vm_svc.is_enabled(), "qemu": vm_svc.qemu_available(), "vms": vm_svc.list_vms()}


@router.get("/isos", dependencies=AUTH)
def list_isos():
    """Lista las ISO disponibles."""
    return vm_svc.list_isos()


@router.post("/isos", dependencies=ADMIN)
async def upload_iso(file: UploadFile = File(...)):
    """Sube una ISO para usar como disco de arranque de una VM."""
    data = await file.read()
    if not data:
        raise HTTPException(400, "Archivo vacío")
    if len(data) > 8 * 1024 * 1024 * 1024:
        raise HTTPException(413, "ISO demasiado grande (máx 8 GB)")
    return vm_svc.save_iso(file.filename or "imagen.iso", data)


@router.post("", dependencies=ADMIN, status_code=201)
def create_vm(body: VmCreate):
    """Crea una VM señuelo (sin arrancarla)."""
    return vm_svc.create_vm(
        name=body.name, os_name=body.os, arch=body.arch,
        ram_mb=body.ram_mb, cpus=body.cpus, iso=body.iso,
    )


@router.post("/{vm_id}/start", dependencies=ADMIN)
def start_vm(vm_id: str):
    """Arranca la VM con QEMU (consola por VNC)."""
    res = vm_svc.start_vm(vm_id)
    if not res.get("success"):
        raise HTTPException(502, res.get("error", "No se pudo arrancar"))
    return res


@router.post("/{vm_id}/stop", dependencies=ADMIN)
def stop_vm(vm_id: str):
    """Detiene la VM."""
    res = vm_svc.stop_vm(vm_id)
    if not res.get("success"):
        raise HTTPException(502, res.get("error", "No se pudo detener"))
    return res


@router.delete("/{vm_id}", dependencies=ADMIN, status_code=204)
def delete_vm(vm_id: str):
    """Elimina la VM (y su disco)."""
    if not vm_svc.delete_vm(vm_id):
        raise HTTPException(404, "VM no encontrada")
