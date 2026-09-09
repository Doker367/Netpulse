"""NetPulse — Config Management Router.

Endpoints para backup, diff, y restauración de configuraciones.
"""

from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from app.middleware.auth import JWTBearer
from app.middleware.rbac import requires_role
from app.core.settings import BACKUP_DIR
from app.models.schemas import ConfigBackup, ConfigDiff, ConfigDiffRequest, ConfigDeployRequest, ConfigDeployResponse, ConfigDryRunResponse, ConfigRollbackResponse
from app.services import napalm_svc

router = APIRouter(prefix="/api/config", tags=["Config"])


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
def backup(device_id: str):
    """Hace backup de la running-config a disco."""
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
def diff_config(device_id: str, body: ConfigDiffRequest):
    """Compara running-config con una configuración propuesta."""
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
def deploy_dry_run(device_id: str, body: ConfigDeployRequest):
    """Dry-run: compara la config candidata con la running sin hacer cambios.
    
    Solo muestra el diff. No modifica el dispositivo.
    """
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
def deploy_config(device_id: str, body: ConfigDeployRequest):
    """Deploy completo: backup + compare + commit.
    
    Flujo:
    1. Backup pre-deploy
    2. Compara (dry-run) y devuelve el diff
    3. Si hay cambios, commitea
    4. Si commit falla, auto-rollback
    5. Backup post-deploy (auditoría)
    """
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
def rollback_device_config(device_id: str):
    """Rollback: revierte a la configuración anterior.
    
    Estrategia en cascada:
    1. NAPALM rollback() nativo
    2. discard_config() si hay candidato pendiente
    3. Restauración desde último backup en disco
    """
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
    """Lista los backups disponibles para un dispositivo."""
    import os, glob
    pattern = str(BACKUP_DIR / f"{device_id}_*.cfg")
    files = sorted(glob.glob(pattern), reverse=True)[:20]
    return [
        {
            "filename": os.path.basename(f),
            "path": str(f),
            "size": os.path.getsize(f),
            "timestamp": os.path.getmtime(f),
        }
        for f in files
    ]
