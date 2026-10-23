"""NetPulse — GeoIP Service.

Resuelve IPs públicas a coordenadas geográficas para el mapa de topología
y la visualización de traceroutes. Usa la base MaxMind GeoLite2-City si
está presente; en su caso también la GeoLite2-Country.

La base NO viene incluida (licencia de MaxMind) — descárgala desde
https://dev.maxmind.com/geoip/geolite2-free-geolocation-databases y colócala
en ``config/GeoLite2-City.mmdb`` o configura ``NETPULSE_GEOIP_DB``.

Si no hay base disponible, lookup() devuelve None (el mapa funciona igual,
solo sin geolocalización) — nunca lanza excepciones a los callers.
"""

import ipaddress
import logging
import os
import threading
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

# Ubicaciones candidatas para la base GeoLite (primera que exista gana):
#   env NETPULSE_GEOIP_DB > ./config/ > ~  /.netpulse/
_GEO_CONFIG_DIR = Path(os.getenv("NETPULSE_CONFIG_DIR", "config"))


def _candidate_paths() -> list[Path]:
    env = os.getenv("NETPULSE_GEOIP_DB", "")
    paths: list[Path] = [Path(env)] if env else []
    paths.append(_GEO_CONFIG_DIR / "GeoLite2-City.mmdb")
    home = Path.home() / ".netpulse" / "GeoLite2-City.mmdb"
    paths.append(home)
    return paths


def db_path() -> Path | None:
    """Ruta efectiva de la base GeoLite2-City, o None si no existe."""
    for p in _candidate_paths():
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    return None


@lru_cache(maxsize=1)
def _available() -> str | None:
    """Devuelve la ruta (como str) de la base activa, o None."""
    p = db_path()
    if p is None:
        logger.info("[geoip] Sin base GeoLite2 (mira docs: config/GeoLite2-City.mmdb)")
        return None
    logger.info("[geoip] Base GeoLite2 activa: %s", p)
    return str(p)


def is_available() -> bool:
    """True si hay una base GeoIP cargable."""
    return _available() is not None


def is_public_ip(value: str) -> bool:
    """True si value es una IP pública enrutable (GeoIP aplicable)."""
    try:
        addr = ipaddress.ip_address(str(value).strip())
    except ValueError:
        return False
    return addr.is_global


class _GeoliteReader:
    """Reader de geoip2 abierto lazy y compartido (thread-safe al abrir)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reader = None
        self._failed = False

    def get(self):
        if self._reader or self._failed:
            return self._reader
        with self._lock:
            if self._reader or self._failed:
                return self._reader
            path = _available()
            if not path:
                self._failed = True
                return None
            try:
                import geoip2.database

                self._reader = geoip2.database.Reader(path)
            except Exception:  # noqa: BLE001 — geoip2 ausente o base corrupta
                logger.info("[geoip] No pude abrir la base GeoLite2 en %s", path)
                self._failed = True
        return self._reader


_READER = _GeoliteReader()


def _geolite_lookup(ip: str) -> dict | None:
    reader = _READER.get()
    if reader is None:
        return None
    try:
        city = reader.city(ip)
    except Exception:  # noqa: BLE001 — no-match / base corrupta
        return None

    loc = getattr(city, "location", None)
    lat = getattr(loc, "latitude", None) if loc else None
    lon = getattr(loc, "longitude", None) if loc else None
    if not lat or not lon:
        return None
    country = getattr(getattr(city, "country", None), "name", None) or ""
    city_name = getattr(getattr(city, "city", None), "name", None) or ""
    return {
        "lat": float(lat),
        "lon": float(lon),
        "city": city_name,
        "country": country,
    }


def lookup(value: str) -> dict | None:
    """Geo-localiza una IP (o hostname que resuelva a IP pública).

    Returns:
        {"lat": float, "lon": float, "city": str, "country": str} o None
        si es IP privada, desconocida, o no hay base GeoLite.
    """
    if not value:
        return None
    node = str(value).strip()
    if not node:
        return None
    try:
        ipaddress.ip_address(node)  # solo IPs: hosts privados/lan no geolocalizan
    except ValueError:
        return None
    if not is_public_ip(node):
        return None
    return _geolite_lookup(node)


def lookup_many(values: list[str]) -> dict[str, dict | None]:
    """Geo-localiza muchas IPs con cache."""
    out: dict[str, dict | None] = {}
    for v in values:
        if v in out:
            continue
        out[v] = lookup(v)
    return out
