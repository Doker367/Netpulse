"""NetPulse — Mapa global de ataques.

Agrega los eventos de deception por IP de origen, geolocaliza las IPs
públicas (GeoIP offline) y devuelve los puntos para el mapa, junto con la
ubicación del centro de datos (punto objetivo en verde).
"""

from app.core.settings import (
    DECEPTION_DATACENTER_LAT,
    DECEPTION_DATACENTER_LON,
    DECEPTION_DATACENTER_NAME,
)
from app.services.deception import geoip_svc, store_svc

# Fuentes de ataque de demostración por país (IPs públicas reconocidas por
# GeoLite2). Se usan para simular ataques desde distintas geografías.
ATTACK_SOURCES: dict[str, list[str]] = {
    "US": ["8.8.8.8", "4.2.2.1", "208.67.222.222"],
    "DE": ["185.220.101.4", "85.214.132.117"],
    "GB": ["81.2.69.142", "51.140.0.1"],
    "FR": ["89.92.0.1", "212.27.48.10"],
    "JP": ["202.32.0.1"],
    "BR": ["200.147.0.1", "201.7.184.1"],
    "IN": ["202.56.0.1", "49.44.0.1"],
    "CN": ["114.114.114.114", "223.5.5.5"],
    "RU": ["77.88.8.8", "5.255.255.5"],
    "AU": ["1.1.1.1"],
    "CA": ["24.48.0.1", "99.224.0.1"],
    "MX": ["201.148.0.1", "189.203.0.1"],
    "NL": ["83.82.0.1", "145.100.0.1"],
    "SG": ["165.21.0.1", "203.116.0.1"],
    "ZA": ["41.0.0.1", "196.25.0.1"],
    "KR": ["168.126.63.1", "121.78.0.1"],
}


def source_ips(country: str | None = None) -> list[tuple[str, str]]:
    """Lista de (ip, country_code) para simular ataques.

    Si se indica ``country`` (ISO-2) se restringe a ese país.
    """
    if country:
        cc = country.upper()
        if cc in ATTACK_SOURCES:
            return [(ip, cc) for ip in ATTACK_SOURCES[cc]]
        return []
    return [(ip, cc) for cc, ips in ATTACK_SOURCES.items() for ip in ips]


def attack_map(limit: int = 3000) -> dict:
    """Puntos de ataque geolocalizados + objetivo (datacenter)."""
    events = store_svc.get_events(limit=limit)
    aggr: dict[str, dict] = {}

    for e in events:
        ip = e.get("src_ip")
        if not ip:
            continue
        if (e.get("detail") or {}).get("simulated"):
            continue
        a = aggr.setdefault(ip, {"count": 0, "devices": set(), "last": None})
        a["count"] += 1
        if e.get("device_id"):
            a["devices"].add(e["device_id"])
        ts = e.get("timestamp")
        if ts and (a["last"] is None or ts > a["last"]):
            a["last"] = ts

    points = []
    for ip, a in aggr.items():
        geo = geoip_svc.lookup(ip)
        if not geo or geo.get("lat") is None:
            continue
        points.append({
            "ip": ip,
            "count": a["count"],
            "devices": len(a["devices"]),
            "last_seen": a["last"],
            "country_code": geo.get("country_code"),
            "country": geo.get("country"),
            "subdivision": geo.get("subdivision"),
            "city": geo.get("city"),
            "lat": geo["lat"],
            "lon": geo["lon"],
        })

    points.sort(key=lambda p: p["count"], reverse=True)
    return {
        "geoip": geoip_svc.available(),
        "target": {
            "lat": DECEPTION_DATACENTER_LAT,
            "lon": DECEPTION_DATACENTER_LON,
            "name": DECEPTION_DATACENTER_NAME,
        },
        "points": points,
        "total": sum(p["count"] for p in points),
    }
