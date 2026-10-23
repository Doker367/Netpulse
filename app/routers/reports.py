"""API de reportes de disponibilidad basados en observaciones nativas."""

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, root_validator, validator

from app.middleware.auth import JWTBearer
from app.middleware.rbac import requires_role
from app.services import report_svc

router = APIRouter(prefix="/api/reports", tags=["Reports"])
VIEWER = [Depends(JWTBearer()), Depends(requires_role("viewer"))]
OPERATOR = [Depends(JWTBearer()), Depends(requires_role("operator"))]


class AvailabilityParams(BaseModel):
    desde: datetime
    hasta: datetime
    device_ids: list[str] | None = None
    group: str | None = None

    @validator("desde", "hasta", pre=True)
    def require_iso_with_timezone(cls, value):
        if not isinstance(value, str) or "T" not in value:
            raise ValueError("Se requiere fecha ISO 8601 con zona horaria")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Fecha ISO 8601 inválida") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("La fecha requiere zona horaria")
        return parsed

    @root_validator(skip_on_failure=True)
    def valid_window(cls, values):
        start, end = values.get("desde"), values.get("hasta")
        if start and end:
            if start >= end:
                raise ValueError("desde debe ser anterior a hasta")
            if (end - start).total_seconds() > 31 * 86400:
                raise ValueError("La ventana máxima es 31 días")
            if end > datetime.now(timezone.utc):
                raise ValueError("hasta no puede estar en el futuro")
        return values


class Incident(BaseModel):
    inicio: datetime
    fin: datetime
    duracion_segundos: float


class Coverage(BaseModel):
    first_ts: datetime
    last_ts: datetime
    n_muestras: int
    horas_observadas: float
    horas_cubiertas_estimadas: float
    horas_solicitadas: float
    parcial: bool
    descripcion: str


class DeviceAvailability(BaseModel):
    device_id: str
    uptime_percent: float
    incidentes: list[Incident]
    cobertura: Coverage


class ExcludedDevice(BaseModel):
    device_id: str
    reason: str


class AvailabilityReport(BaseModel):
    report_id: str
    desde: datetime
    hasta: datetime
    device_ids: list[str] | None
    group: str | None
    estado: Literal["con_datos", "sin_datos"]
    mensaje: str
    recoleccion_activa: bool
    equipos: list[DeviceAvailability]
    excluidos: list[ExcludedDevice]


@router.post("/availability", response_model=AvailabilityReport, dependencies=OPERATOR)
def generate_availability(params: AvailabilityParams):
    """Regenera el resultado y reemplaza el caché para los mismos filtros."""
    return report_svc.generate(params.desde, params.hasta, params.device_ids, params.group)


@router.get("/availability/cached", response_model=AvailabilityReport, dependencies=VIEWER)
def get_cached_availability(
    desde: str = Query(...), hasta: str = Query(...),
    device_ids: list[str] | None = Query(None), group: str | None = Query(None),
):
    """Consulta el último reporte del proceso por hash de filtros, sin regenerar."""
    from pydantic import ValidationError

    try:
        params = AvailabilityParams(desde=desde, hasta=hasta, device_ids=device_ids, group=group)
    except ValidationError as exc:
        raise HTTPException(422, detail=exc.errors()) from exc
    result = report_svc.cached(params.desde, params.hasta, params.device_ids, params.group)
    if result is None:
        raise HTTPException(404, "No hay reporte en caché para estos parámetros")
    return result
