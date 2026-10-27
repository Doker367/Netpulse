"""NetPulse — Prometheus Metrics Router.

Expone el endpoint /api/metrics en formato texto de Prometheus.
Endpoint público (sin autenticación) para que Prometheus pueda scrape.
"""

import asyncio

from fastapi import APIRouter
from fastapi.responses import Response
from prometheus_client import generate_latest, REGISTRY

from app.services import host_metrics_svc

router = APIRouter(tags=["Metrics"])


@router.get("/api/metrics")
async def metrics():
    """Retorna todas las métricas en formato texto de Prometheus.

    Endpoint público — no requiere autenticación.
    Refresca los gauges ``netpulse_host_*`` (CPU/RAM/disco/red/proceso)
    antes de serializar para que el scrape siempre vea datos actuales.
    """
    try:
        # psutil es síncrono (~100 ms de muestra de CPU): fuera del loop.
        await host_metrics_svc.update_prometheus_async()
    except asyncio.TimeoutError:
        # Prometheus seguirá viendo los últimos gauges publicados.
        pass
    except Exception:
        pass
    return Response(
        content=generate_latest(REGISTRY),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
