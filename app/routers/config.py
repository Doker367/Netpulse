"""NetPulse — Config Management Router.

Endpoints para backup, diff, y restauración de configuraciones.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.middleware.auth import JWTBearer
from app.middleware.rbac import get_current_user, partition_device_scope, requires_role, requires_scope
from app.core.settings import BACKUP_DIR
from app.models.schemas import ConfigBackup, ConfigDiff, ConfigDiffRequest, ConfigDeployRequest, ConfigDeployResponse, ConfigDryRunResponse, ConfigRollbackResponse
from app.services import backup_offsite_svc, inventory_svc, napalm_svc

router = APIRouter(prefix="/api/config", tags=["Config"])


# ── Backup offsite (S3/MinIO o SFTP) ────────────────────────

def _offsite_response(result: dict) -> JSONResponse | dict:
    """HTTP coherente: fallo remoto total → 502 con causa; parcial → 200 visible."""
    if result.get("status") == "error":
        error = result.get("error") or {}
        detail = error.get("message") or (result.get("errors") or ["fallo offsite"])[0]
        code = error.get("code")
        message = f"Offsite {result.get('backend', '')}: {detail}" + (f" [{code}]" if code else "")
        return JSONResponse(status_code=backup_offsite_svc.http_status_for(result),
                            content={"detail": message, **result})
    return result


@router.get(
    "/offsite/status",
    dependencies=[Depends(JWTBearer()), Depends(requires_role("admin"))],
)
def offsite_status(probe: bool = False):
    """Estado del destino offsite.

    ``probe=true`` comprueba de verdad la conexión y cuenta objetos remotos;
    sin él no se toca la red y ``last_export`` es el último resultado real
    (``null`` si nunca se exportó).
    """
    return backup_offsite_svc.status(probe=probe)


@router.post(
    "/offsite/export",
    dependencies=[Depends(JWTBearer()), Depends(requires_role("admin"))],
)
def offsite_export(request: Request, device_id: Optional[str] = None):
    """Exporta backups; sin device_id solo exporta dispositivos autorizados."""
    if device_id is not None:
        requires_scope(device_id=device_id, request=request)
        return _offsite_response(backup_offsite_svc.export_all(device_id))
    # export_all(None) exportaría archivos de TODOS los grupos.
    user = get_current_user(request)
    if user.get("scope_group") is None and user.get("scope_sites") is None:
        return _offsite_response(backup_offsite_svc.export_all(None))
    allowed, rejected = partition_device_scope(request, [d["id"] for d in inventory_svc.list_devices()])
    results = [{"device_id": did, "result": backup_offsite_svc.export_all(did)} for did in allowed]
    statuses = [row["result"].get("status") for row in results]
    aggregate = {
        "status": "error" if results and all(s == "error" for s in statuses)
        else ("partial" if any(s in {"error", "partial"} for s in statuses) else "ok"),
        "total": len(allowed) + len(rejected),
        "results": results,
        "errors": rejected,
    }
    return _offsite_response(aggregate)


@router.post(
    "/offsite/verify",
    dependencies=[Depends(JWTBearer()), Depends(requires_role("admin"))],
)
def offsite_verify(request: Request, device_id: Optional[str] = None):
    """Comprueba que los backups locales existen en el destino y coinciden."""
    if device_id is not None:
        requires_scope(device_id=device_id, request=request)
    elif get_current_user(request).get("scope_group") is not None or \
            get_current_user(request).get("scope_sites") is not None:
        raise HTTPException(
            403,
            "La verificación offsite compara todos los backups locales; "
            "indica device_id para un alcance restringido",
        )
    return _offsite_response(backup_offsite_svc.verify_all(device_id))


@router.post(
    "/offsite/restore",
    dependencies=[Depends(JWTBearer()), Depends(requires_role("admin"))],
)
def offsite_restore(request: Request, dest: Optional[str] = None,
                    device_id: Optional[str] = None, overwrite: bool = False):
    """Restaura desde el destino a un subdirectorio del directorio configurado.

    ``dest`` es un nombre relativo bajo ``NETPULSE_OFFSITE_RESTORE_DIR``
    (por defecto ``backups/restored``). No se aceptan rutas absolutas ni
    ``..``: la restauración no puede escribir fuera de ese árbol.
    """
    if device_id is not None:
        requires_scope(device_id=device_id, request=request)
    base = Path(backup_offsite_svc.RESTORE_DIR).resolve()
    if dest:
        candidate = Path(dest)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise HTTPException(400, "dest debe ser un subdirectorio relativo sin '..'")
        target = (base / candidate).resolve()
        if target != base and base not in target.parents:
            raise HTTPException(400, "dest fuera del directorio de restauración")
    else:
        target = base
    result = backup_offsite_svc.restore_all(
        device_id=device_id, dest_dir=target, overwrite=overwrite
    )
    return _offsite_response(result)


@router.post(
    "/{device_id}/save",
    dependencies=[Depends(JWTBearer()), Depends(requires_role("admin"))],
)
def save_config(device_id: str, request: Request):
    """Guarda la running-config a persistente (write memory / save)."""
    requires_scope(device_id=device_id, request=request)
    result = napalm_svc.save_config(device_id)
    return {
        "device_id": device_id,
        "success": result.success,
        "output": (result.data or [{}])[0].get("output", "") if isinstance(result.data, list) else "",
        "error": result.error,
    }


@router.get(
    "/{device_id}",
    response_model=ConfigBackup,
    dependencies=[Depends(JWTBearer()), Depends(requires_role("operator"))],
)
def get_running_config(device_id: str):
    """Obtiene la running-config del dispositivo."""
    result = napalm_svc.get_config(device_id, retrieve="running")
    if not result.success:
        return ConfigBackup(
            device_id=device_id,
            timestamp=datetime.now(),
            running_config=f"⚠️ Config no disponible: {result.error}",
            startup_config=None,
            size_bytes=0,
        )

    cfg = result.data
    if isinstance(cfg, dict):
        running = cfg.get("running", "")
    else:
        running = str(cfg) if cfg else ""
    return ConfigBackup(
        device_id=device_id,
        timestamp=datetime.utcnow(),
        running_config=running,
        size_bytes=len(running),
    )


@router.post(
    "/{device_id}/backup",
    response_model=dict,
    dependencies=[Depends(JWTBearer()), Depends(requires_role("admin"))],
)
def backup(device_id: str, request: Request):
    """Hace backup de la running-config a disco."""
    requires_scope(device_id=device_id, request=request)
    result = napalm_svc.backup_config(device_id)
    if not result.success:
        return ConfigBackup(
            device_id=device_id,
            timestamp=datetime.now(),
            running_config=f"⚠️ Config no disponible: {result.error}",
            startup_config=None,
            size_bytes=0,
        )
    data = result.data or {}
    data["success"] = True
    return data


@router.post(
    "/{device_id}/diff",
    response_model=ConfigDiff,
    dependencies=[Depends(JWTBearer()), Depends(requires_role("admin"))],
)
def diff_config(device_id: str, body: ConfigDiffRequest, request: Request):
    """Compara running-config con una configuración propuesta."""
    requires_scope(device_id=device_id, request=request)
    result = napalm_svc.get_config(device_id, retrieve="running")
    if not result.success:
        return ConfigBackup(
            device_id=device_id,
            timestamp=datetime.now(),
            running_config=f"⚠️ Config no disponible: {result.error}",
            startup_config=None,
            size_bytes=0,
        )

    running = ""
    if isinstance(result.data, dict):
        running = result.data.get("running", "")
    else:
        running = str(result.data) if result.data else ""

    # Simple line-based diff
    import difflib
    diff_lines = list(
        difflib.unified_diff(
            running.splitlines(keepends=True),
            (body.candidate_config + "\n").splitlines(keepends=True),
            fromfile=f"{device_id}-running",
            tofile=f"{device_id}-candidate",
        )
    )
    return ConfigDiff(device_id=device_id, diff="".join(diff_lines))


@router.get(
    "/{device_id}/raw",
    response_class=PlainTextResponse,
    dependencies=[Depends(JWTBearer()), Depends(requires_role("operator"))],
)
def raw_config(device_id: str):
    """Obtiene la running-config en texto plano."""
    result = napalm_svc.get_config(device_id, retrieve="running")
    if not result.success:
        return f"⚠️ Config no disponible para {device_id}: {result.error}"

    cfg = result.data
    if isinstance(cfg, dict):
        return cfg.get("running", cfg.get("startup", str(cfg)))
    return str(cfg) if cfg else ""


# ── Deploy / Dry-Run / Rollback ─────────────────────────────

@router.post(
    "/{device_id}/deploy/dry-run",
    response_model=ConfigDryRunResponse,
    dependencies=[Depends(JWTBearer()), Depends(requires_role("operator"))],
)
def deploy_dry_run(device_id: str, body: ConfigDeployRequest, request: Request):
    """Dry-run: compara la config candidata con la running sin hacer cambios.
    
    Solo muestra el diff. No modifica el dispositivo.
    """
    requires_scope(device_id=device_id, request=request)
    result = napalm_svc.compare_config(device_id, body.candidate_config)
    if not result.success:
        return ConfigBackup(
            device_id=device_id,
            timestamp=datetime.now(),
            running_config=f"⚠️ Config no disponible: {result.error}",
            startup_config=None,
            size_bytes=0,
        )
    return ConfigDryRunResponse(device_id=device_id, diff=result.data or "")


@router.post(
    "/{device_id}/deploy",
    response_model=ConfigDeployResponse,
    dependencies=[Depends(JWTBearer()), Depends(requires_role("admin"))],
)
def deploy_config(device_id: str, body: ConfigDeployRequest, request: Request):
    """Deploy completo: backup + compare + commit.
    
    Flujo:
    1. Backup pre-deploy
    2. Compara (dry-run) y devuelve el diff
    3. Si hay cambios, commitea
    4. Si commit falla, auto-rollback
    5. Backup post-deploy (auditoría)
    """
    requires_scope(device_id=device_id, request=request)
    result = napalm_svc.deploy_config(device_id, body.candidate_config)
    return ConfigDeployResponse(
        device_id=result["device_id"],
        backup_file=result.get("backup_file"),
        diff=result.get("diff", ""),
        committed=result.get("committed", False),
        error=result.get("error"),
    )


@router.post(
    "/{device_id}/rollback",
    response_model=ConfigRollbackResponse,
    dependencies=[Depends(JWTBearer()), Depends(requires_role("admin"))],
)
def rollback_device_config(device_id: str, request: Request):
    """Rollback: revierte a la configuración anterior.
    
    Estrategia en cascada:
    1. NAPALM rollback() nativo
    2. discard_config() si hay candidato pendiente
    3. Restauración desde último backup en disco
    """
    requires_scope(device_id=device_id, request=request)
    result = napalm_svc.rollback_config(device_id)
    return ConfigRollbackResponse(
        device_id=device_id,
        rolled_back=result.success,
        error=result.error,
    )    


@router.get(
    "/{device_id}/backups",
    dependencies=[Depends(JWTBearer()), Depends(requires_role("viewer"))],
)
def list_backups(device_id: str):
    """Lista los backups disponibles para un dispositivo.

    El nombre en disco usa un ``device_id`` saneado (espacios → ``_``) y
    puede tener extensión ``.cfg`` (config) o ``.json`` (snapshot SNMP/ROS).
    """
    import os
    import glob

    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in device_id)
    files: list[str] = []
    for ext in ("cfg", "json"):
        files.extend(glob.glob(str(BACKUP_DIR / f"{safe_id}_*.{ext}")))
    files = sorted(set(files), key=os.path.getmtime, reverse=True)[:20]
    return [
        {
            "filename": os.path.basename(f),
            "path": str(f),
            "size": os.path.getsize(f),
            "timestamp": os.path.getmtime(f),
        }
        for f in files
    ]


@router.get(
    "/{device_id}/backups/diff",
    dependencies=[Depends(JWTBearer()), Depends(requires_role("viewer"))],
)
def diff_backups(device_id: str, a: str, b: str):
    """Diff lado a lado entre dos backups del mismo dispositivo.

    Devuelve filas alineadas (``equal``/``add``/``del``/``change``) para el
    visor side-by-side y estadísticas de añadidas/eliminadas.
    """
    import difflib
    import os

    from fastapi import HTTPException

    def _read(filename: str) -> list[str]:
        safe = os.path.basename(filename)
        path = (BACKUP_DIR / safe).resolve()
        if BACKUP_DIR.resolve() not in path.parents or not path.is_file():
            raise HTTPException(404, f"Backup '{safe}' no encontrado")
        return path.read_text(encoding="utf-8", errors="replace").splitlines()

    la, lb = _read(a), _read(b)
    rows: list[dict] = []
    added = removed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=la, b=lb).get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                rows.append({"type": "equal", "a": la[i1 + k], "b": lb[j1 + k]})
        elif tag == "delete":
            for k in range(i1, i2):
                rows.append({"type": "del", "a": la[k], "b": None})
                removed += 1
        elif tag == "insert":
            for k in range(j1, j2):
                rows.append({"type": "add", "a": None, "b": lb[k]})
                added += 1
        else:  # replace
            n = max(i2 - i1, j2 - j1)
            for k in range(n):
                av = la[i1 + k] if i1 + k < i2 else None
                bv = lb[j1 + k] if j1 + k < j2 else None
                rows.append({"type": "change", "a": av, "b": bv})
                if av is not None:
                    removed += 1
                if bv is not None:
                    added += 1

    return {
        "device_id": device_id,
        "a": {"filename": os.path.basename(a), "lines": len(la)},
        "b": {"filename": os.path.basename(b), "lines": len(lb)},
        "rows": rows,
        "stats": {"added": added, "removed": removed, "total": len(rows)},
    }


@router.get(
    "/{device_id}/backups/{filename}",
    dependencies=[Depends(JWTBearer()), Depends(requires_role("viewer"))],
)
def download_backup(device_id: str, filename: str):
    """Descarga un archivo de backup concreto (solo dentro de BACKUP_DIR)."""
    import os

    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    safe_name = os.path.basename(filename)
    path = (BACKUP_DIR / safe_name).resolve()
    if BACKUP_DIR.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404, f"Backup '{safe_name}' no encontrado")
    media = "application/json" if path.suffix == ".json" else "text/plain"
    return FileResponse(path, filename=safe_name, media_type=media)
