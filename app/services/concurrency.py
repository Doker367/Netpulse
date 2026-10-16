"""NetPulse — límite de concurrencia para operaciones de red.

Evita que N operaciones simultáneas (NAPALM/netmiko) saturen los equipos
o el threadpool HTTP. A diferencia del límite global anterior, ahora el
cupo es **por dispositivo** (``NETPULSE_PER_DEVICE_CONCURRENCY``, default
2): un switch lento ya no congela los slots de los demás equipos.

Se aplica con el decorador ``limited`` a las funciones que abren sesiones
de red (sus primitivas reales de transporte). El primer argumento de esas
funciones es el dict del dispositivo; si no lo es, se cae a un semáforo
global de respaldo (``NETPULSE_MAX_DEVICE_CONCURRENCY``, default 16).
"""

import functools
import os
import threading

_DEFAULT_GLOBAL = 16
_DEFAULT_PER_DEVICE = 2


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _per_device_permits() -> int:
    return _int_env("NETPULSE_PER_DEVICE_CONCURRENCY", _DEFAULT_PER_DEVICE)


_global_sem = threading.BoundedSemaphore(_int_env("NETPULSE_MAX_DEVICE_CONCURRENCY", _DEFAULT_GLOBAL))
_sem_lock = threading.Lock()
_device_sems: dict[str, threading.BoundedSemaphore] = {}


def _sem_for(device_id: str) -> threading.BoundedSemaphore:
    """Semáforo (creado a demanda) para un dispositivo."""
    with _sem_lock:
        sem = _device_sems.get(device_id)
        if sem is None:
            sem = threading.BoundedSemaphore(_per_device_permits())
            _device_sems[device_id] = sem
        return sem


def limited(func):
    """Ejecuta ``func`` bajo el semáforo del dispositivo (o el global)."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        device_id = None
        if args and isinstance(args[0], dict):
            device_id = args[0].get("id")
        sem = _sem_for(device_id) if device_id else _global_sem
        with sem:
            return func(*args, **kwargs)
    return wrapper


def reset_for_tests():
    """(tests) recrea los semáforos con los límites actuales del entorno."""
    global _global_sem, _device_sems
    with _sem_lock:
        _device_sems = {}
    _global_sem = threading.BoundedSemaphore(
        _int_env("NETPULSE_MAX_DEVICE_CONCURRENCY", _DEFAULT_GLOBAL)
    )
