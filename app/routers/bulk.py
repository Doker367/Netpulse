"""NetPulse — Bulk Operations Router.

Endpoints para operaciones masivas en paralelo sobre múltiples
dispositivos, usando Nornir (con fallback serial a NAPALM).
"""

from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException

from app.middleware.auth import JWTBearer
from app.middleware.rbac import requires_role
from app.models.schemas import BulkDeployRequest, BulkDeployResponse, BulkDeployDeviceResult, BulkResult, FactsResponse, DeviceStatus
from app.services import napalm_svc, nornir_svc

router = APIRouter(prefix="/api/bulk", tags=["Bulk Operations"])


def _build_bulk_response(results: list) -> BulkResult:
    """Convierte lista de resultados en BulkResult."""
    success_list = []
    error_list = []
    success_count = 0
    failed_count = 0

    for r in results:
        # Manejar NapalmResult
        if hasattr(r, "success"):
            if r.success:
                success_count += 1
                success_list.append({
                    "device_id": r.device_id,
                    "data": r.data,
                })
            else:
                failed_count += 1
                error_list.append({
                    "device_id": r.device_id,
                    "error": r.error,
                })
        # Manejar dict (status)
        elif isinstance(r, dict):
            if r.get("online", False):
                success_count += 1
                success_list.append(r)
            else:
                failed_count += 1
                error_list.append(r)

    return BulkResult(
        total=len(results),
        success=success_count,
        failed=failed_count,
        results=success_list,
        errors=error_list,
    )


@router.post(
    "/facts",
    response_model=BulkResult,
    dependencies=[Depends(JWTBearer()), Depends(requires_role("operator"))],
)
def bulk_get_facts(device_ids: Optional[list[str]] = Body(None)):
    """Obtiene facts de todos los dispositivos (o los especificados).

    Request body (opcional):
        {"device_ids": ["cisco-core-01", "arista-core-01"]}

    Si no se envían device_ids, se consultan todos los dispositivos.
    """
    results = nornir_svc.bulk_get_facts(device_ids)
    return _build_bulk_response(results)


@router.post(
    "/backup",
    response_model=BulkResult,
    dependencies=[Depends(JWTBearer()), Depends(requires_role("admin"))],
)
def bulk_backup(device_ids: Optional[list[str]] = Body(None)):
    """Hace backup de la running-config de todos los dispositivos.

    Guarda cada config en backups/<device_id>_<timestamp>.cfg

    Request body (opcional):
        {"device_ids": ["cisco-core-01"]}
    """
    results = nornir_svc.bulk_backup(device_ids)
    return _build_bulk_response(results)


@router.get(
    "/status",
    response_model=BulkResult,
    dependencies=[Depends(JWTBearer()), Depends(requires_role("operator"))],
)
def bulk_check_status():
    """Verifica el estado (online/offline) de todos los dispositivos.

    No requiere body — siempre chequea todos los dispositivos.
    """
    results = nornir_svc.bulk_check_status(device_ids=None)
    return _build_bulk_response(results)


@router.post(
    "/status",
    response_model=BulkResult,
    dependencies=[Depends(JWTBearer()), Depends(requires_role("operator"))],
)
def bulk_check_status_filtered(device_ids: Optional[list[str]] = Body(None)):
    """Verifica el estado de los dispositivos especificados.

    Request body (opcional):
        {"device_ids": ["cisco-core-01", "switch-access-01"]}
    """
    results = nornir_svc.bulk_check_status(device_ids=device_ids)
    return _build_bulk_response(results)


# ── Bulk Config Deploy ──────────────────────────────────────────


def _deploy_single(device_id: str, candidate_config: str, dry_run: bool) -> BulkDeployDeviceResult:
    """Despliega configuración en un solo dispositivo."""
    try:
        if dry_run:
            result = napalm_svc.compare_config(device_id, candidate_config)
            return BulkDeployDeviceResult(
                device_id=device_id,
                success=result.success,
                diff=result.data or "",
                committed=False,
                error=result.error,
            )
        else:
            result = napalm_svc.deploy_config(device_id, candidate_config)
            return BulkDeployDeviceResult(
                device_id=result.get("device_id", device_id),
                success=result.get("committed", False) or "error" not in result,
                diff=result.get("diff", ""),
                committed=result.get("committed", False),
                error=result.get("error"),
            )
    except Exception as e:
        return BulkDeployDeviceResult(
            device_id=device_id,
            success=False,
            diff="",
            committed=False,
            error=str(e),
        )


@router.post(
    "/deploy",
    response_model=BulkDeployResponse,
    dependencies=[Depends(JWTBearer()), Depends(requires_role("admin"))],
)
def bulk_deploy(body: BulkDeployRequest):
    """Despliega una configuración en múltiples dispositivos simultáneamente.

    Flujo:
    1. Para cada dispositivo, ejecuta compare (dry-run si dry_run=True)
    2. Si dry_run=False, hace deploy completo (backup + compare + commit)
    3. Retorna resultados individuales por dispositivo

    Request body:
        {"device_ids": [...], "candidate_config": "...", "dry_run": false}
    """
    import concurrent.futures

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(_deploy_single, did, body.candidate_config, body.dry_run): did
            for did in body.device_ids
        }
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    success_count = sum(1 for r in results if r.success)
    failed_count = sum(1 for r in results if not r.success)

    return BulkDeployResponse(
        total=len(results),
        success=success_count,
        failed=failed_count,
        results=results,
    )
