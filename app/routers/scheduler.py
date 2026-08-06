"""NetPulse — Scheduler Router.

Gestiona la programación de backups únicos y recurrentes.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.middleware.auth import JWTBearer
from app.middleware.rbac import requires_role
from app.models.schemas import BackupScheduleRequest, RecurringBackupRequest, ScheduledJobResponse
from app.services import scheduler_svc

router = APIRouter(prefix="/api/scheduler", tags=["Scheduler"])


@router.post(
    "/backup",
    response_model=ScheduledJobResponse,
    status_code=201,
    dependencies=[Depends(JWTBearer()), Depends(requires_role("operator"))],
)
def schedule_backup(req: BackupScheduleRequest):
    """Programa un backup único para un dispositivo."""
    job_id = scheduler_svc.schedule_backup(req.device_id, req.delay_minutes)
    jobs = scheduler_svc.list_jobs()
    job = next((j for j in jobs if j["job_id"] == job_id), None)
    if not job:
        raise HTTPException(500, "Error al crear el trabajo programado")
    return job


@router.post(
    "/backup/recurring",
    response_model=ScheduledJobResponse,
    status_code=201,
    dependencies=[Depends(JWTBearer()), Depends(requires_role("operator"))],
)
def schedule_recurring(req: RecurringBackupRequest):
    """Programa backups recurrentes para un dispositivo."""
    job_id = scheduler_svc.schedule_recurring(req.device_id, req.interval_minutes)
    jobs = scheduler_svc.list_jobs()
    job = next((j for j in jobs if j["job_id"] == job_id), None)
    if not job:
        raise HTTPException(500, "Error al crear el trabajo programado")
    return job


@router.get(
    "/jobs",
    response_model=list[ScheduledJobResponse],
    dependencies=[Depends(JWTBearer()), Depends(requires_role("operator"))],
)
def list_jobs():
    """Lista todos los trabajos programados."""
    return scheduler_svc.list_jobs()


@router.delete(
    "/jobs/{job_id}",
    status_code=204,
    dependencies=[Depends(JWTBearer()), Depends(requires_role("operator"))],
)
def cancel_job(job_id: str):
    """Cancela un trabajo programado."""
    if not scheduler_svc.cancel_job(job_id):
        raise HTTPException(404, f"Trabajo '{job_id}' no encontrado")
