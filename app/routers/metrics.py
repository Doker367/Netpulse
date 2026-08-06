"""NetPulse — Prometheus Metrics Router.

Expone el endpoint /api/metrics en formato texto de Prometheus.
Endpoint público (sin autenticación) para que Prometheus pueda scrape.
"""

from fastapi import APIRouter
from fastapi.responses import Response
from prometheus_client import generate_latest, REGISTRY

router = APIRouter(tags=["Metrics"])


@router.get("/api/metrics")
async def metrics():
    """Retorna todas las métricas en formato texto de Prometheus.

    Endpoint público — no requiere autenticación.
    """
    return Response(
        content=generate_latest(REGISTRY),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
