"""NetPulse — límite de concurrencia para operaciones de red.

Evita que N operaciones simultáneas (NAPALM/netmiko) saturen los
equipos o el threadpool HTTP. El límite es configurable con
NETPULSE_MAX_DEVICE_CONCURRENCY (default 8).

Se aplica con el decorador ``limited`` a las funciones que abren
sesiones de red (las primitivas reales de transporte), de forma que
las operaciones que llaman a varias de ellas no dupliquen el cupo.
"""

import functools
import os
import threading

_DEFAULT_MAX = 8


def _max_concurrent() -> int:
    try:
        value = int(os.getenv("NETPULSE_MAX_DEVICE_CONCURRENCY", str(_DEFAULT_MAX)))
        return max(1, value)
    except (TypeError, ValueError):
        return _DEFAULT_MAX


_semaphore = threading.BoundedSemaphore(_max_concurrent())


def limited(func):
    """Envoltorio: ejecuta ``func`` bajo el semáforo global de dispositivos."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with _semaphore:
            return func(*args, **kwargs)
    return wrapper


def reset_for_tests():
    """(tests) recrea el semáforo con el límite actual del entorno."""
    global _semaphore
    _semaphore = threading.BoundedSemaphore(_max_concurrent())
