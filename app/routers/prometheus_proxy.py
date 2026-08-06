"""NetPulse — Prometheus Proxy Router (bypass CORS)."""
import logging
import urllib.request
import urllib.parse
import json
from fastapi import APIRouter, Depends, Query
from app.middleware.auth import JWTBearer

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/prometheus", tags=["prometheus"])

@router.get("/v1/query", dependencies=[Depends(JWTBearer())])
def prom_query(query: str = Query(...)):
    """Proxy Prometheus instant query."""
    try:
        url = f"http://localhost:9090/api/v1/query?query={urllib.parse.quote(query)}"
        r = urllib.request.urlopen(url, timeout=8)
        return json.loads(r.read().decode())
    except Exception as e:
        return {"status": "error", "error": str(e)}

@router.get("/v1/query_range", dependencies=[Depends(JWTBearer())])
def prom_query_range(
    query: str = Query(...),
    start: float = Query(...),
    end: float = Query(...),
    step: str = Query("60s"),
):
    """Proxy Prometheus range query."""
    try:
        url = f"http://localhost:9090/api/v1/query_range"
        params = f"query={urllib.parse.quote(query)}&start={start}&end={end}&step={step}"
        r = urllib.request.urlopen(f"{url}?{params}", timeout=10)
        return json.loads(r.read().decode())
    except Exception as e:
        return {"status": "error", "error": str(e)}
