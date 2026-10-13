"""NetPulse — Deception Router.

API de la red señuelo: topología de dispositivos simulados, telemetría
de eventos de atacantes, sesiones agregadas y estadísticas.

El endpoint ``/ingest`` es el contrato que usan los motores (listeners
nativos, engine en Go, Cowrie/OpenCanary) para reportar interacciones.
Se autentica con ``X-Deception-Token`` (si está configurado) o, en su
defecto, con JWT de administrador.
"""

import hmac
import logging
import random
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app.core.settings import DECEPTION_ENABLED, DECEPTION_INGEST_TOKEN
from app.middleware.auth import JWTBearer
from app.middleware.rbac import requires_role
from app.models.deception import (
    DeceptionDevice,
    DeceptionEventIn,
    DeceptionSegment,
)
from app.services.deception import engine_svc, store_svc, topology_svc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/deception", tags=["Deception"])

AUTH = [Depends(JWTBearer()), Depends(requires_role("viewer"))]
ADMIN = [Depends(JWTBearer()), Depends(requires_role("admin"))]


async def ingest_auth(request: Request) -> dict:
    """Autoriza el ingest por token del data plane o por JWT admin."""
    supplied = request.headers.get("X-Deception-Token", "")
    if DECEPTION_INGEST_TOKEN and hmac.compare_digest(supplied, DECEPTION_INGEST_TOKEN):
        return {"sub": "deception-engine", "role": "admin"}
    bearer = JWTBearer()
    await bearer(request)
    checker = requires_role("admin")
    return await checker(request)


# ── Estado / Topología ───────────────────────────────────────


@router.get("/status", dependencies=AUTH)
def status():
    """Estado del subsistema de deception y resumen de la red señuelo."""
    summary = topology_svc.summary()
    return {"enabled": DECEPTION_ENABLED, **summary}


@router.get("/network", dependencies=AUTH)
def get_network():
    """Red señuelo completa (segmentos + dispositivos)."""
    network = topology_svc.load_network()
    return network.model_dump(mode="json")


@router.get("/segments", dependencies=AUTH)
def list_segments():
    """Lista segmentos/VLANs de la red señuelo."""
    return topology_svc.list_segments()


@router.get("/devices", dependencies=AUTH)
def list_devices():
    """Lista dispositivos señuelo."""
    return topology_svc.list_devices()


@router.get("/devices/{device_id}", dependencies=AUTH)
def get_device(device_id: str):
    """Detalle de un dispositivo señuelo."""
    device = topology_svc.get_device(device_id)
    if not device:
        raise HTTPException(404, "Dispositivo señuelo no encontrado")
    return device


@router.post("/devices", status_code=201, dependencies=ADMIN)
def create_device(device: DeceptionDevice):
    """Agrega un dispositivo señuelo."""
    try:
        return topology_svc.add_device(device)
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@router.put("/devices/{device_id}", dependencies=ADMIN)
def update_device(device_id: str, updates: dict):
    """Actualiza un dispositivo señuelo."""
    try:
        result = topology_svc.update_device(device_id, updates)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if result is None:
        raise HTTPException(404, "Dispositivo señuelo no encontrado")
    return result


@router.delete("/devices/{device_id}", status_code=204, dependencies=ADMIN)
def delete_device(device_id: str):
    """Elimina un dispositivo señuelo."""
    if not topology_svc.delete_device(device_id):
        raise HTTPException(404, "Dispositivo señuelo no encontrado")


@router.post("/segments", status_code=201, dependencies=ADMIN)
def create_segment(segment: DeceptionSegment):
    """Agrega un segmento/VLAN."""
    try:
        return topology_svc.add_segment(segment)
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@router.delete("/segments/{segment_id}", status_code=204, dependencies=ADMIN)
def delete_segment(segment_id: str):
    """Elimina un segmento sin dispositivos asociados."""
    try:
        deleted = topology_svc.delete_segment(segment_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    if not deleted:
        raise HTTPException(404, "Segmento no encontrado")


# ── Telemetría ───────────────────────────────────────────────


@router.post("/ingest", status_code=202)
async def ingest(event: DeceptionEventIn, _user: dict = Depends(ingest_auth)):
    """Recibe un evento normalizado de un motor de deception."""
    from app.services.deception import telemetry_svc
    record = await telemetry_svc.emit(
        device_id=event.device_id,
        src_ip=event.src_ip,
        proto=event.proto,
        event_type=event.type,
        detail=event.detail,
        username=event.username,
        success=event.success,
        src_port=event.src_port,
        ts=event.ts,
    )
    return {"status": "accepted", "id": record.get("id", 0)}


@router.get("/events", dependencies=AUTH)
def list_events(
    limit: int = 100,
    offset: int = 0,
    device_id: Optional[str] = None,
    src_ip: Optional[str] = None,
    event_type: Optional[str] = None,
):
    """Lista eventos de deception (más recientes primero)."""
    return store_svc.get_events(
        limit=limit, offset=offset, device_id=device_id,
        src_ip=src_ip, event_type=event_type,
    )


@router.get("/sessions", dependencies=AUTH)
def list_sessions(limit: int = 100):
    """Sesiones agregadas por (dispositivo, atacante)."""
    return store_svc.get_sessions(limit=limit)


@router.get("/alerts", dependencies=AUTH)
def list_alerts(limit: int = 50):
    """Alertas de deception disparadas (desde la auditoría)."""
    from app.services.deception import alert_svc
    return alert_svc.recent(limit=limit)


@router.get("/false-positives", dependencies=AUTH)
def list_false_positives(limit: int = 50):
    """Alertas suprimidas por la IA al detectar falsos positivos."""
    from app.services.deception import alert_svc
    return alert_svc.recent_suppressed(limit=limit)


@router.get("/timeline", dependencies=AUTH)
def timeline(minutes: int = 30):
    """Serie de eventos por minuto (gráfico de actividad)."""
    return store_svc.get_timeline(minutes=minutes)


@router.get("/events.csv", dependencies=AUTH, response_class=PlainTextResponse)
def events_csv(limit: int = 1000):
    """Exporta los eventos de deception a CSV."""
    return store_svc.events_to_csv(limit=limit)


@router.post("/simulate", dependencies=ADMIN)
async def simulate(count: int = 6, country: Optional[str] = None):
    """Genera actividad de atacante simulada (para demos y pruebas de UI).

    Args:
        count: bloques de eventos a generar.
        country: código ISO-2 para atacar desde un país concreto
            (US, DE, GB, FR, JP, BR, IN, CN, RU, AU, CA, MX, NL, SG, ZA, KR).
    """
    from app.services.deception import geo_svc, telemetry_svc, topology_svc

    devices = [d for d in topology_svc.list_devices() if d.get("enabled", True)]
    if not devices:
        raise HTTPException(409, "No hay dispositivos señuelo habilitados")

    sources = geo_svc.source_ips(country)
    if not sources:
        raise HTTPException(422, f"País sin fuentes de ataque: {country}")
    attacker, cc = random.choice(sources)

    emitted = 0
    for _ in range(max(1, min(count, 50))):
        dev = random.choice(devices)
        proto = random.choice([s.get("proto", "tcp") for s in dev.get("services", [])] or ["tcp"])
        await telemetry_svc.emit(dev["id"], attacker, proto, "connect", detail={})
        await telemetry_svc.emit(
            dev["id"], attacker, proto, "login_attempt", username="admin", success=False,
            detail={"password": random.choice(["admin", "123456", "toor"])},
        )
        emitted += 2

    dev = random.choice(devices)
    await telemetry_svc.emit(dev["id"], attacker, "ssh", "login_success",
                             username="root", success=True,
                             detail={"password": "toor"})
    await telemetry_svc.emit(dev["id"], attacker, "ssh", "command", username="root",
                             detail={"command": "cat /etc/passwd"})
    return {"status": "ok", "events": emitted + 2, "attacker": attacker, "country": cc}


@router.get("/stats", dependencies=AUTH)
def get_stats():
    """Estadísticas agregadas de la red señuelo."""
    data = store_svc.get_stats()
    data.update(topology_svc.summary())
    return data


# ── Motor nativo ─────────────────────────────────────────────


@router.get("/engine/status", dependencies=AUTH)
def engine_status():
    """Estado del motor nativo (listeners activos)."""
    return engine_svc.status()


@router.post("/engine/start", dependencies=ADMIN)
async def engine_start():
    """Levanta los listeners nativos de la red señuelo."""
    return await engine_svc.start()


@router.post("/engine/stop", dependencies=ADMIN)
async def engine_stop():
    """Detiene los listeners nativos."""
    return await engine_svc.stop()


# ── Integraciones de honeypots reales (Cowrie/OpenCanary) ────


@router.get("/integrations/status", dependencies=AUTH)
def integrations_status():
    """Estado del tailer de logs de honeypots reales."""
    from app.services.deception.integrations import tailer_svc
    return tailer_svc.status()


@router.post("/integrations/start", dependencies=ADMIN)
def integrations_start():
    """Inicia el tailer de logs de Cowrie/OpenCanary."""
    from app.services.deception.integrations import tailer_svc
    return tailer_svc.start()


@router.post("/integrations/stop", dependencies=ADMIN)
def integrations_stop():
    """Detiene el tailer de logs de Cowrie/OpenCanary."""
    from app.services.deception.integrations import tailer_svc
    return tailer_svc.stop()


# ── Blocklist / IOCs / NetBox ────────────────────────────────


@router.get("/blocklist", dependencies=AUTH)
def list_blocklist():
    """Lista las IPs atacantes bloqueadas."""
    from app.services.deception import blocklist_svc
    return blocklist_svc.list_blocked()


@router.post("/blocklist", status_code=201, dependencies=ADMIN)
def block_ip(payload: dict):
    """Bloquea una IP atacante."""
    from app.services.deception import blocklist_svc
    ip = (payload or {}).get("ip", "").strip()
    if not ip:
        raise HTTPException(422, "Se requiere 'ip'")
    return blocklist_svc.add(ip, reason=(payload or {}).get("reason", "manual"))


@router.delete("/blocklist/{ip}", status_code=204, dependencies=ADMIN)
def unblock_ip(ip: str):
    """Quita una IP de la blocklist."""
    from app.services.deception import blocklist_svc
    if not blocklist_svc.remove(ip):
        raise HTTPException(404, "IP no encontrada en la blocklist")


@router.get("/iocs", dependencies=AUTH)
def list_iocs(limit: int = 5000):
    """Indicadores de compromiso (atacantes agregados)."""
    from app.services.deception import ioc_svc
    return ioc_svc.summarize(limit=limit)


@router.get("/iocs.csv", dependencies=AUTH, response_class=PlainTextResponse)
def iocs_csv(limit: int = 5000):
    """Exporta los IOCs a CSV."""
    from app.services.deception import ioc_svc
    return ioc_svc.to_csv(limit=limit)


@router.post("/sync-netbox", dependencies=ADMIN)
def sync_netbox():
    """Importa dispositivos de NetBox como señuelos (best-effort)."""
    return topology_svc.sync_from_netbox()


@router.get("/allowlist", dependencies=AUTH)
def list_allowlist():
    """Lista los orígenes confiables (no generan alertas)."""
    from app.services.deception import allowlist_svc
    return allowlist_svc.list_allowed()


@router.post("/allowlist", status_code=201, dependencies=ADMIN)
def add_allowlist(payload: dict):
    """Agrega una IP/CIDR a la allowlist."""
    from app.services.deception import allowlist_svc
    cidr = (payload or {}).get("cidr", "").strip()
    if not cidr:
        raise HTTPException(422, "Se requiere 'cidr'")
    try:
        return allowlist_svc.add(cidr, note=(payload or {}).get("note", ""))
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.delete("/allowlist/{cidr:path}", status_code=204, dependencies=ADMIN)
def remove_allowlist(cidr: str):
    """Quita una entrada de la allowlist."""
    from app.services.deception import allowlist_svc
    if not allowlist_svc.remove(cidr):
        raise HTTPException(404, "Entrada no encontrada")


# ── GeoIP / Mapa / Firewall ──────────────────────────────────


@router.get("/geo", dependencies=AUTH)
def geo_map(limit: int = 3000):
    """Puntos de ataque geolocalizados + objetivo (para el mapa global)."""
    from app.services.deception import geo_svc
    return geo_svc.attack_map(limit=limit)


@router.get("/geoip/{ip}", dependencies=AUTH)
def geoip_lookup(ip: str):
    """Geolocaliza una IP concreta (offline)."""
    from app.services.deception import geoip_svc
    return {"ip": ip, "available": geoip_svc.available(),
            "geo": geoip_svc.lookup(ip)}


@router.get("/firewall/status", dependencies=AUTH)
def firewall_status():
    """Estado del firewall y blocklist."""
    from app.services.deception import firewall_svc
    return firewall_svc.status()


@router.post("/firewall/block", dependencies=ADMIN)
def firewall_block(payload: dict):
    """Bloquea una IP en el firewall (dry-run salvo FIREWALL_ENABLED)."""
    from app.services.deception import firewall_svc
    ip = (payload or {}).get("ip", "").strip()
    if not ip:
        raise HTTPException(422, "Se requiere 'ip'")
    return firewall_svc.block(ip, reason=(payload or {}).get("reason", "firewall"))


@router.post("/firewall/unblock", dependencies=ADMIN)
def firewall_unblock(payload: dict):
    """Quita una IP de la blocklist."""
    from app.services.deception import firewall_svc
    ip = (payload or {}).get("ip", "").strip()
    if not ip:
        raise HTTPException(422, "Se requiere 'ip'")
    return firewall_svc.unblock(ip)
