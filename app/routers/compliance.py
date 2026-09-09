"""NetPulse — Compliance Validation Router.

Endpoints para verificar y gestionar compliance de configuraciones
de red contra líneas base (baselines) por grupo de dispositivos.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.middleware.auth import JWTBearer
from app.middleware.rbac import requires_role
from app.models.schemas import (
    ComplianceBaselineRequest,
    ComplianceBaselineResponse,
    ComplianceCheckRequest,
)
from app.services import compliance_svc
from app.services.notifications_svc import notify_compliance_violation

router = APIRouter(
    prefix="/api/compliance",
    tags=["Compliance"],
    dependencies=[Depends(JWTBearer()), Depends(requires_role("admin"))],
)


@router.post("/check", response_model=dict)
def check_compliance(body: ComplianceCheckRequest):
    """Verifica compliance de uno o más dispositivos contra su baseline.

    Si se envía device_id individual, retorna un solo resultado.
    Si se envía device_ids (lista), retorna un dict con resultados por dispositivo.

    La comparación es línea por línea contra el baseline del grupo
    (primer tag del dispositivo).
    """
    # Individual
    if body.device_id:
        result = compliance_svc.check_compliance(body.device_id)

        # Notificar si hay violaciones
        if not result["compliant"] and result.get("violations"):
            notify_compliance_violation(body.device_id, result["violations"])

        return result

    # Múltiple
    if body.device_ids:
        results = {}
        for did in body.device_ids:
            dev_result = compliance_svc.check_compliance(did)
            results[did] = dev_result

            if not dev_result["compliant"] and dev_result.get("violations"):
                notify_compliance_violation(did, dev_result["violations"])

        return {
            "total": len(body.device_ids),
            "compliant": sum(1 for r in results.values() if r["compliant"]),
            "non_compliant": sum(1 for r in results.values() if not r["compliant"]),
            "results": results,
        }

    raise HTTPException(status_code=400, detail="Se requiere device_id o device_ids")


@router.post("/baseline", response_model=ComplianceBaselineResponse)
def set_baseline(body: ComplianceBaselineRequest):
    """Establece la configuración baseline para un grupo de dispositivos.

    El baseline se almacena en config/baselines/<group>.cfg y sirve
    como referencia para las verificaciones de compliance.
    """
    config = compliance_svc.set_baseline(body.group, body.config)
    return ComplianceBaselineResponse(
        group=body.group,
        config=config,
        size_bytes=len(config),
    )


@router.get("/baseline/{group}", response_model=ComplianceBaselineResponse)
def get_baseline(group: str):
    """Obtiene la configuración baseline de un grupo de dispositivos.

    Retorna 404 si el grupo no tiene baseline configurado.
    """
    config = compliance_svc.get_baseline(group)
    if config is None:
        raise HTTPException(
            status_code=404,
            detail=f"No hay baseline configurado para el grupo '{group}'. "
                   f"Grupos disponibles: {compliance_svc.list_baseline_groups()}",
        )
    return ComplianceBaselineResponse(
        group=group,
        config=config,
        size_bytes=len(config),
    )


@router.get("/report", response_model=dict)
def get_compliance_report():
    """Genera un reporte completo de compliance para todos los dispositivos.

    Evalúa cada dispositivo contra el baseline de su grupo y retorna
    un resumen general más los resultados detallados por dispositivo.
    """
    report = compliance_svc.generate_report()
    return report


@router.get("/baselines", response_model=list[str])
def list_baselines():
    """Lista los grupos que tienen baseline configurado."""
    return compliance_svc.list_baseline_groups()
