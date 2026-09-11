"""NetPulse — System Metrics Router.

Expone métricas del servidor donde corre NetPulse y el resumen de
recursos de todos los dispositivos (recolectado por el poller en
segundo plano).
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.middleware.auth import JWTBearer
from app.middleware.rbac import requires_role
from app.services import host_metrics_svc, metrics_history_svc
from app.services.collectors import metrics_poller

router = APIRouter(prefix="/api/system", tags=["System Metrics"])

AUTH = [Depends(JWTBearer()), Depends(requires_role("viewer"))]
ADMIN = [Depends(JWTBearer()), Depends(requires_role("admin"))]


@router.post("/ping", dependencies=AUTH)
def host_ping(
    target: str = Query(..., description="IP o hostname a sondear desde el servidor"),
    count: int = Query(3, ge=1, le=10, description="Número de paquetes ICMP"),
):
    """Ping ICMP desde el servidor donde corre NetPulse hacia un destino.

    Mide conectividad real del NOC (server → destino), sin depender de la
    CLI de los equipos.
    """
    import platform
    import re
    import subprocess

    system = platform.system()
    if system == "Windows":
        cmd = ["ping", "-n", str(count), "-w", "1500", target]
    elif system == "Darwin":
        cmd = ["ping", "-c", str(count), "-t", "3", target]
    else:
        cmd = ["ping", "-c", str(count), "-W", "3", target]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=count * 3 + 3)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {
            "target": target, "success": False, "probes_sent": count,
            "probes_received": 0, "rtt_min": 0.0, "rtt_avg": 0.0,
            "rtt_max": 0.0, "packet_loss": 100, "error": str(exc),
        }

    out = proc.stdout or ""
    tx, rx = count, 0
    rtt_min = rtt_avg = rtt_max = 0.0
    m = re.search(r"(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets )?received", out)
    if m:
        tx, rx = int(m.group(1)), int(m.group(2))
    else:
        m2 = re.search(r"(\d+)\s+packets received", out)
        if m2:
            rx = int(m2.group(1))
    r = re.search(r"=\s*([\d.]+)/([\d.]+)/([\d.]+)", out)
    if r:
        rtt_min, rtt_avg, rtt_max = float(r.group(1)), float(r.group(2)), float(r.group(3))

    loss = round((tx - rx) / tx * 100) if tx else 100
    return {
        "target": target,
        "success": rx > 0,
        "probes_sent": tx,
        "probes_received": rx,
        "rtt_min": rtt_min,
        "rtt_avg": rtt_avg,
        "rtt_max": rtt_max,
        "packet_loss": loss,
    }


@router.get("/host", dependencies=AUTH)
def host_metrics():
    """Métricas en vivo del servidor NetPulse (CPU, RAM, disco, red, proceso)."""
    return host_metrics_svc.collect()


@router.get("/devices", dependencies=AUTH)
def device_metrics(refresh: bool = Query(False, description="Forzar un poll inmediato")):
    """Recursos de todos los dispositivos (último snapshot del poller)."""
    if refresh:
        return metrics_poller.poll_all()
    snap = metrics_poller.snapshot()
    devices = list(snap.get("devices", {}).values())
    online = [d for d in devices if d.get("online")]
    avg = (lambda k: round(sum(float(d.get(k, 0) or 0) for d in online) / len(online), 2)) if online else (lambda k: 0)
    return {
        **snap,
        "summary": {
            "total": len(devices),
            "online": len(online),
            "offline": len(devices) - len(online),
            "avg_cpu": avg("cpu"),
            "avg_memory_percent": avg("memory_percent"),
            "avg_disk_percent": avg("disk_percent"),
            "avg_latency_ms": avg("latency_ms"),
        },
    }


@router.post("/poll", dependencies=ADMIN)
def poll_now():
    """Fuerza un ciclo de recolección inmediato (host + dispositivos)."""
    return metrics_poller.poll_all()


@router.get("/history", dependencies=AUTH)
def history(
    hours: float = Query(6, ge=0.1, le=168, description="Ventana temporal en horas"),
    device_id: Optional[str] = Query(None, description="Limitar a un dispositivo"),
):
    """Historial de métricas nativo (host + dispositivos) sin Prometheus."""
    return metrics_history_svc.overview(hours=hours, device_id=device_id)


@router.get("/overview", dependencies=AUTH)
def overview():
    """Vista consolidada: host + resumen de dispositivos + estado del poller."""
    snap = metrics_poller.snapshot()
    devices = list(snap.get("devices", {}).values())
    online = [d for d in devices if d.get("online")]
    avg = (lambda k: round(sum(float(d.get(k, 0) or 0) for d in online) / len(online), 2)) if online else (lambda k: 0)
    return {
        "host": snap.get("host") or host_metrics_svc.collect(),
        "poller": {
            "running": snap.get("running", False),
            "interval": snap.get("interval"),
            "last_run": snap.get("last_run"),
            "duration_ms": snap.get("duration_ms"),
        },
        "devices_summary": {
            "total": len(devices),
            "online": len(online),
            "offline": len(devices) - len(online),
            "avg_cpu": avg("cpu"),
            "avg_memory_percent": avg("memory_percent"),
            "avg_disk_percent": avg("disk_percent"),
            "avg_latency_ms": avg("latency_ms"),
        },
    }
