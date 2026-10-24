"""NetPulse — Scheduler Router.

Gestiona la programación de backups únicos y recurrentes.

RBAC por grupo: crear un backup (único o recurrente) y cancelar un trabajo
validan el dispositivo real del inventario contra ``scope_group``/``scope_sites``
(``requires_scope``), igual que ``POST /api/command/{device_id}``. Los trabajos
de reporte son globales por diseño (``device_id = "all"``): generan los CSV de
todo el inventario, que en esta fase es lectura no restringida, así que no se
les impone scope. Listar trabajos también es lectura global.
"""

from fastapi import APIRouter, Depends, HTTPException, Request

from app.middleware.auth import JWTBearer
from app.middleware.rbac import requires_role, requires_scope
from app.models.schemas import BackupScheduleRequest, RecurringBackupRequest, ScheduledJobResponse
from app.services import scheduler_svc

router = APIRouter(prefix="/api/scheduler", tags=["Scheduler"])

# Los trabajos de reporte se crean con este device_id centinela: no apuntan a
# un equipo concreto y no se pueden validar contra un grupo del inventario.
GLOBAL_DEVICE_ID = "all"


def _find_job(job_id: str) -> dict | None:
    return next((job for job in scheduler_svc.list_jobs() if job["job_id"] == job_id), None)


@router.post(
    "/backup",
    response_model=ScheduledJobResponse,
    status_code=201,
    dependencies=[Depends(JWTBearer()), Depends(requires_role("operator"))],
)
def schedule_backup(req: BackupScheduleRequest, request: Request):
    """Programa un backup único para un dispositivo."""
    requires_scope(device_id=req.device_id, request=request)
    job_id = scheduler_svc.schedule_backup(req.device_id, req.delay_minutes)
    job = _find_job(job_id)
    if not job:
        raise HTTPException(500, "Error al crear el trabajo programado")
    return job


@router.post(
    "/backup/recurring",
    response_model=ScheduledJobResponse,
    status_code=201,
    dependencies=[Depends(JWTBearer()), Depends(requires_role("operator"))],
)
def schedule_recurring(req: RecurringBackupRequest, request: Request):
    """Programa backups recurrentes para un dispositivo."""
    requires_scope(device_id=req.device_id, request=request)
    job_id = scheduler_svc.schedule_recurring(req.device_id, req.interval_minutes)
    job = _find_job(job_id)
    if not job:
        raise HTTPException(500, "Error al crear el trabajo programado")
    return job


@router.post(
    "/report",
    status_code=201,
    dependencies=[Depends(JWTBearer()), Depends(requires_role("operator"))],
)
def schedule_report(payload: dict):
    """Programa generación de reportes CSV.

    Body: {"delay_minutes": 1, "interval_minutes": 1440} — con
    interval_minutes se repite (ej. 1440 = diario); sin él, una vez.

    No se aplica scope: el trabajo no apunta a un equipo sino al inventario
    completo (``device_id = "all"``) y la lectura sigue siendo global en esta
    fase. Restringir reportes exige antes filtrar inventario y telemetría.
    """
    delay = int(payload.get("delay_minutes", 1))
    interval = payload.get("interval_minutes")
    interval = int(interval) if interval is not None else None

    job_id = scheduler_svc.schedule_report(delay, interval)
    job = _find_job(job_id)
    if not job:
        raise HTTPException(500, "Error al crear el trabajo de reporte")
    return job


@router.get(
    "/jobs",
    response_model=list[ScheduledJobResponse],
    dependencies=[Depends(JWTBearer()), Depends(requires_role("operator"))],
)
def list_jobs():
    """Lista todos los trabajos programados (lectura global en esta fase)."""
    return scheduler_svc.list_jobs()


@router.delete(
    "/jobs/{job_id}",
    status_code=204,
    dependencies=[Depends(JWTBearer()), Depends(requires_role("operator"))],
)
def cancel_job(job_id: str, request: Request):
    """Cancela un trabajo programado.

    Un trabajo de backup exige scope sobre su dispositivo; los de reporte
    (``device_id = "all"``) son globales. Si el dispositivo ya no está en el
    inventario, un token restringido recibe 404 (mismo criterio que
    ``requires_scope``) y un token legado conserva el comportamiento anterior.
    """
    job = _find_job(job_id)
    if job and job.get("device_id") != GLOBAL_DEVICE_ID:
        requires_scope(device_id=job["device_id"], request=request)
    if not scheduler_svc.cancel_job(job_id):
        raise HTTPException(404, f"Trabajo '{job_id}' no encontrado")
