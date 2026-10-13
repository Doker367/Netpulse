"""NetPulse — GeoIP offline (MaxMind GeoLite2).

Resuelve país/ciudad/coordenadas de una IP usando una base MaxMind local
(.mmdb). Por defecto autodetecta la incluida en el paquete
``maxminddb-geolite2`` (sin servicios externos). Si no hay base, el
servicio queda deshabilitado y devuelve None.
"""

import glob
import ipaddress
import logging
import os
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_reader = None
_lock = threading.Lock()
_tried = False


def _find_db() -> Optional[str]:
    from app.core.settings import CONFIG_DIR, GEOIP_DB

    if GEOIP_DB and os.path.exists(GEOIP_DB):
        return GEOIP_DB
    for candidate in (
        CONFIG_DIR / "GeoLite2-City.mmdb",
        CONFIG_DIR / "geoip" / "GeoLite2-City.mmdb",
    ):
        if candidate.exists():
            return str(candidate)
    try:
        import geoip2  # type: ignore

        sp = os.path.dirname(os.path.dirname(geoip2.__file__))
        found = glob.glob(os.path.join(sp, "**", "*.mmdb"), recursive=True)
        if found:
            return found[0]
    except Exception:  # noqa: BLE001
        pass
    return None


def _get_reader():
    global _reader, _tried
    if _reader is not None or _tried:
        return _reader
    with _lock:
        if _reader is not None or _tried:
            return _reader
        _tried = True
        db = _find_db()
        if not db:
            logger.info("[geoip] sin base .mmdb; GeoIP deshabilitado")
            return None
        try:
            import geoip2.database  # type: ignore

            _reader = geoip2.database.Reader(db)
            logger.info("[geoip] base cargada: %s", db)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[geoip] no se pudo abrir la base: %s", exc)
    return _reader


def available() -> bool:
    """True si hay una base GeoIP cargada."""
    return _get_reader() is not None


def lookup(ip: str) -> Optional[dict]:
    """Geolocaliza una IP pública.

    Returns:
        ``{country_code, country, city, lat, lon}`` o None si es privada,
        inválida o no está en la base.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if addr.is_private or addr.is_loopback or addr.is_reserved or addr.is_link_local:
        return None
    if addr.is_multicast or addr.is_unspecified:
        return None

    reader = _get_reader()
    if reader is None:
        return None

    for method in ("city", "country"):
        try:
            resp = getattr(reader, method)(ip)
        except Exception:  # noqa: BLE001
            continue
        country = getattr(resp, "country", None)
        if country is None or not country.iso_code:
            continue
        result = {
            "country_code": country.iso_code,
            "country": country.name or country.iso_code,
            "subdivision": None,
            "subdivision_code": None,
            "city": None,
            "lat": None,
            "lon": None,
        }
        try:
            subdivisions = getattr(resp, "subdivisions", None)
            most = getattr(subdivisions, "most_specific", None) if subdivisions else None
            if most is not None:
                result["subdivision"] = most.name
                result["subdivision_code"] = most.iso_code
        except Exception:  # noqa: BLE001
            pass
        city = getattr(resp, "city", None)
        if city is not None:
            result["city"] = city.name
        loc = getattr(resp, "location", None)
        if loc is not None:
            result["lat"] = loc.latitude
            result["lon"] = loc.longitude
        return result
    return None
