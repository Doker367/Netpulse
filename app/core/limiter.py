"""NetPulse — Rate Limiter compartido.

Definido en módulo propio para evitar imports circulares
entre app.main y los routers.

La clave del límite es el cliente real: detrás de un proxy inverso
(Nginx/Traefik) la IP del socket es la del proxy, así que se prioriza
``X-Forwarded-For`` / ``X-Real-IP`` para no compartir el contador entre
todos los usuarios.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request


def client_key(request: Request) -> str:
    """Devuelve la IP real del cliente (respeta cabeceras de proxy)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return get_remote_address(request)


limiter = Limiter(
    key_func=client_key,
    default_limits=["100/minute"],  # 100 requests per minute per client
)
