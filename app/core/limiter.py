"""NetPulse — Rate Limiter compartido.

Definido en módulo propio para evitar imports circulares
entre app.main y los routers.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["100/minute"],  # 100 requests per minute per IP
)
