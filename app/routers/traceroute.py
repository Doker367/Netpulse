"""NetPulse — Traceroute Router.

Ejecuta traceroutes hacia un destino desde el servidor NMS o desde un
dispositivo del inventario, y devuelve los saltos enriquecidos con
geolocalización (GeoIP) para dibujarlos sobre el mapa interactivo.

POST /api/topology/traceroute
"""

import logging
import platform
import re
import subprocess

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.middleware.auth import JWTBearer
from app.middleware.rbac import get_current_user, requires_role
from app.services import napalm_svc, audit_svc, geoip_svc, inventory_svc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/topology", tags=["Traceroute"])

AUTH = [Depends(JWTBearer()), Depends(requires_role("operator"))]

MAX_HOPS = 30

# Destino permitido: IP, FQDN, o '*' de vendors. Bloquea shell injection.
_DEST_RE = re.compile(r"^[A-Za-z0-9._:-]{1,253}$")

_PRIVATE_IP_RE = re.compile(r"^(?:127\.|10\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.|::1|fc|fd)")


class TracerouteRequest(BaseModel):
    """Petición de traceroute con origen flexible."""

    origin: str = Field(
        default="noc",
        description="'noc' (servidor NetPulse) o device_id del inventario",
        examples=["noc", "cisco-core-01"],
    )
    destination: str = Field(
        ...,
        description="IP o FQDN de destino",
        examples=["8.8.8.8", "edge.mi-red.com"],
    )


def _hop(h: int, ip: str, rtt: float | None, hostname: str = "") -> dict:
    return {
        "hop": h,
        "ip": ip,
        "hostname": hostname,
        "rtt_ms": rtt,
        "geo": geoip_svc.lookup(ip),
        "device_id": None,
    }


def _parse_server_traceroute(text: str) -> list[dict]:
    """Parsea salida de traceroute -n (Linux/macOS) o tracert -d (Windows).

    Formatos soportados:
      '  3  10.0.0.1  1.234 ms'                    (linux/macOS, modo -n)
      '  4  gw.local (10.0.0.4)  [AS65100] 12 ms'  (con hostname)
      '  4  * * *'                                 (salto sin respuesta)
    """
    hops: dict[int, dict] = {}
    for line in text.splitlines():
        # Linux/macOS '  3  10.0.0.1' · iputils tracepath '1:  172.30.0.11'
        m = re.match(r"\s*(\d{1,3})\s*[:\.\s]\s+\S", line)
        if not m:
            continue
        ttl = int(m.group(1))
        if not (1 <= ttl <= MAX_HOPS):
            continue
        mtls = re.search(r"([\d.]+)\s*ms", line)
        rtt = float(mtls.group(1)) if mtls else None
        mip4 = re.search(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})", line)
        mip6 = re.search(r"([0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]*){2,6})", line)
        ip = mip4.group(1) if mip4 else (mip6.group(1) if mip6 else "")
        if not ip and rtt is None:
            # Línea de asteriscos: salto sin respuesta pero cuenta en la ruta.
            hops.setdefault(ttl, _hop(ttl, "", None))
            continue
        hostname = ""
        mi = re.match(r"\s*\d+[:\.\s]+\s*(\S+)\s*\(", line)
        if mi and ip and mi.group(1) != ip:
            hostname = mi.group(1)
        hops.setdefault(ttl, _hop(ttl, ip, rtt, hostname))
    return [hops[k] for k in sorted(hops)]


def _parse_device_traceroute(text: str) -> list[dict]:
    """Parsea salida multi-vendor de traceroute CLI.

    Cisco IOS/EOS/NX-OS:
        '  1 10.90.18.1 [MPLS: Label 16] 1 msec 1 msec 1 msec'
        '  2 edge-router(10.90.18.254) 3 msec 3 msec 3 msec'
    JunOS:
        ' 2  gw-core.dc-hosting.net (203.0.113.2) 20.4 ms 21 ms'
    MikroTik ROS:
        '  1 10.0.0.1 0.5ms' / '  2 host.example.net 64.2ms'
    """
    hops: dict[int, dict] = {}
    for line in text.splitlines():
        m = re.match(r"\s*(\d{1,3})\s+(?:\*|\S.*)", line)
        if not m:
            continue
        ttl = int(m.group(1))
        if not (1 <= ttl <= MAX_HOPS):
            continue
        ip, hostname = None, ""
        mi = re.search(r"\(\s*(\d{1,3}(?:\.\d{1,3}){3})\s*\)", line)
        if mi:
            hostname = (line[: mi.start()].strip().split(" ") or [""])[-1]
            ip = mi.group(1)
        else:
            mv4 = re.findall(r"(\d{1,3}(?:\.\d{1,3}){3})", line)
            if mv4:
                ip = mv4[0]
            else:
                mi6 = re.search(r"\b([0-9A-Fa-f:]{6,}[0-9A-Fa-f])\b", line)
                ip = mi6.group(1) if mi6 else ""
        mm = re.search(r"([\d.]+)\s*msec|([\d.]+)\s*ms\b", line)
        rtt = float(mm.group(1) or mm.group(2)) if mm else None
        hops.setdefault(ttl, _hop(ttl, ip or "", rtt, hostname))
    return [hops[k] for k in sorted(hops)]


def _run_server_traceroute(destination: str) -> tuple[list[dict], str]:
    """Traceroute ICMP/UDP desde el servidor NMS (subprocess)."""
    system = platform.system()
    if system == "Windows":
        cmd = ["tracert", "-d", "-h", str(MAX_HOPS), "-w", "2000", destination]
    else:
        cmd = [
            "traceroute", "-n", "-q", "1", "-w", "2", "-m", str(MAX_HOPS), destination,
        ]
    try:
        proc = subprocess.run(  # noqa: S603 — destino validado con _DEST_RE
            cmd, capture_output=True, text=True, timeout=90,
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=501,
            detail="No hay binario 'traceroute' en el servidor NMS — "
            "instala el paquete 'traceroute' (o usa origen = dispositivo).",
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Traceroute excedió el timeout (90s)")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    out = (proc.stdout or "") if proc and proc.stdout else ""
    if not out and proc and proc.stderr:
        out = proc.stderr
    hops = _parse_server_traceroute(out) if out else []
    return hops, out


def _run_device_traceroute(device_id: str, destination: str) -> tuple[list[dict], str]:
    """Traceroute desde un dispositivo del inventario.

    1) NAPALM CLI (`traceroute <dest>` — routers IOS/EOS/JunOS reales).
    2) Fallback shell del SO (`tracepath`, para equipos FRR/lldpd del lab,
       donde el CLI de vtysh no tiene traceroute y el usuario no es root).
    """
    result = napalm_svc.run_commands(device_id, [f"traceroute {destination}"])
    if not result.success:
        output = _tracepath(device_id, destination)
        if output:
            return _parse_server_traceroute(output), output
        raise HTTPException(
            status_code=502,
            detail={
                "error": result.error,
                "error_type": result.error_type or "unknown",
                "device_id": device_id,
            },
        )
    rows = result.data or []
    output = ""
    for row in rows:
        if isinstance(row, dict) and row.get("command", "").startswith("traceroute"):
            output = row.get("output", "") or ""
    hops = _parse_device_traceroute(output)
    if not hops:
        # vtysh devolvió '% Unknown command' etc. — fallback shell del host.
        out2 = _tracepath(device_id, destination)
        if out2:
            return _parse_server_traceroute(out2), out2
    return hops, output


def _tracepath(device_id: str, destination: str) -> str:
    """tracepath desde el shell del equipo (funciona sin NET_RAW)."""
    out = napalm_svc.run_shell_command(
        device_id, f"tracepath -m {MAX_HOPS} {destination}", timeout=60,
    )
    return out or ""


def _enrich_devices(hops: list[dict]) -> None:
    """Marca saltos que coinciden con IPs de gestión del inventario."""
    by_ip: dict[str, str] = {}
    for d in inventory_svc.list_devices():
        host = str(d.get("hostname", "")).strip()
        if host:
            by_ip.setdefault(host, d["id"])
    for h in hops:
        if h.get("ip") in by_ip:
            h["device_id"] = by_ip[h["ip"]]


@router.post("/traceroute", dependencies=AUTH)
def traceroute(body: TracerouteRequest, request: Request):
    """Ejecuta un traceroute (desde el servidor o un dispositivo) y
    devuelve saltos con geolocalización para el mapa interactivo.

    Requiere rol: operator+
    """
    destination = (body.destination or "").strip()
    if not _DEST_RE.match(destination):
        raise HTTPException(
            status_code=422,
            detail="Destino inválido: usa una IP o FQDN (letras, números, punto, guion).",
        )

    origin = (body.origin or "noc").strip()
    user = get_current_user(request)
    username = user.get("sub", "anonymous")
    audit_svc.log_event(
        action="traceroute_execute",
        device_id=None if origin == "noc" else origin,
        username=username,
        details=f"Traceroute {destination} desde {origin}",
        ip_address=request.client.host if request.client else None,
        path="/api/topology/traceroute",
        method="POST",
    )

    raw = ""
    if origin == "noc":
        hops, raw = _run_server_traceroute(destination)
        origin_info = {"type": "noc", "id": "noc", "label": "Servidor NetPulse"}
    else:
        dev = next((d for d in inventory_svc.list_devices() if d["id"] == origin), None)
        if not dev:
            raise HTTPException(status_code=404, detail=f"Dispositivo {origin} no encontrado")
        hops, raw = _run_device_traceroute(origin, destination)
        origin_info = {
            "type": "device",
            "id": origin,
            "label": dev.get("hostname", origin),
        }

    _enrich_devices(hops)
    return {
        "origin": origin_info,
        "destination": destination,
        "hops": hops,
        "raw_output": raw,
        "geoip_available": geoip_svc.is_available(),
    }
