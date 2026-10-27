"""NetPulse — I/O bloqueante fuera del event loop, con tope de tiempo.

**Regla de la casa** (tarjetas 13B/13C): ningún handler ``async`` ejecuta
I/O síncrono en línea (paramiko, SQLite, YAML, SMTP, ``urllib.subprocess``…).
Un handler ``async`` comparte el event loop con el resto de peticiones y con
el WebSocket de terminal: una llamada de 2 s congela todo ese tiempo.

Este módulo centraliza el patrón ya usado en ``system.py``/``docker_stats_svc``:

* ``asyncio.to_thread`` → el trabajo corre en el **executor por defecto** del
  loop, que está acotado por asyncio (``min(32, cpu + 4)`` hilos). No se crean
  hilos sin límite.
* ``asyncio.wait`` → la espera tiene un tope duro. Si vence se lanza
  :class:`BlockingCallTimeout`, una excepción propia para no confundirla con
  los ``timeout`` que levantan las librerías dentro del hilo (``socket.timeout``
  es ``TimeoutError`` desde Python 3.10).

Límites conocidos (honestidad):

- ``wait_for`` cancela la **espera**, no el hilo. Una transferencia que ya
  arrancó sigue hasta que su propio socket timeout (paramiko) o su cliente
  HTTP la corten. Por eso todos los llamadores pasan topes holgados: el
  timeout es una red de seguridad, no el mecanismo normal de corte.
- Un ``executor`` propio (``run_blocking(..., executor=...)``) permite aislar
  trabajos largos (SFTP) del pool compartido, siempre con un número fijo de
  hilos.
"""

from __future__ import annotations

import asyncio
import functools
import os
from concurrent.futures import Executor
from typing import Any, Callable, TypeVar

T = TypeVar("T")


class BlockingCallTimeout(TimeoutError):
    """El trabajo bloqueante no terminó dentro del tope indicado.

    Es una subclase de ``TimeoutError`` para que un ``except TimeoutError``
    genérico siga funcionando, pero los routers la capturan explícitamente
    para devolver un ``detail`` propio (no el de la librería de turno).
    """

    def __init__(self, label: str, timeout: float):
        self.label = label
        self.timeout = timeout
        super().__init__(f"{label} superó {timeout:g} s")


def env_timeout(name: str, default: float) -> float:
    """Tope en segundos desde el entorno; valores inválidos o ``<= 0`` → default.

    Mismo criterio que ``NETPULSE_SFTP_CONNECT_TIMEOUT_S`` en
    ``app/routers/sftp.py`` y ``NETPULSE_DOCKER_STATS_TIMEOUT`` en
    ``docker_stats_svc``: una variable mal puesta nunca deja el tope en cero
    (que significaría "sin tope" o "falla siempre").
    """
    raw = os.getenv(name, "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


async def run_blocking(
    func: Callable[..., T],
    /,
    *args: Any,
    timeout: float | None = None,
    executor: Executor | None = None,
    label: str | None = None,
    cleanup_on_timeout: Callable[[Any], None] | None = None,
    **kwargs: Any,
) -> T:
    """Ejecuta ``func(*args, **kwargs)`` fuera del event loop.

    Args:
        func: función síncrona y bloqueante.
        timeout: segundos máximos de espera. ``None`` espera sin tope (solo
            para trabajos ya acotados internamente).
        executor: executor propio (acotado) para no ocupar el pool compartido.
        label: nombre para el mensaje de error (default: ``func.__name__``).
        cleanup_on_timeout: si el tope vence, se sigue esperando el resultado
            en segundo plano y se le pasa a este callback para liberar
            recursos que el hilo aún no había soltado (p. ej. la sesión SSH
            de una descarga). Sin él, el task se cancela y el resultado se
            descarta.

    Raises:
        BlockingCallTimeout: la espera superó ``timeout``.

    Las excepciones que levante ``func`` se propagan **sin envolver**: los
    routers siguen clasificando por causa (401/403/502/503/504) igual que antes.
    """
    if executor is None:
        coro = asyncio.to_thread(func, *args, **kwargs)
    else:
        loop = asyncio.get_running_loop()
        coro = loop.run_in_executor(executor, functools.partial(func, *args, **kwargs))

    if timeout is None:
        return await coro

    # ``asyncio.wait`` (y no ``wait_for``) para distinguir *nuestro* tope de un
    # ``TimeoutError``/``socket.timeout`` levantado por la propia ``func``:
    # en Python ≥3.10 ``socket.timeout`` ES ``TimeoutError``, así que un
    # ``except TimeoutError`` alrededor de ``wait_for`` convertiría un timeout
    # real del dispositivo en un "trabajo demasiado lento" y rompería la
    # clasificación de errores del router (401/403/502/504).
    task = asyncio.ensure_future(coro)
    done, _pending = await asyncio.wait({task}, timeout=timeout)
    if task in done:
        # ``result()`` propaga la excepción original sin envolver.
        return task.result()

    if cleanup_on_timeout is not None:
        # El hilo no se puede interrumpir: cuando termine, liberamos lo que
        # haya devuelto (si falló, no hay nada que liberar).
        def _late(done_task: asyncio.Task) -> None:
            if done_task.cancelled():
                return
            exc = done_task.exception()
            if exc is not None:
                return
            try:
                cleanup_on_timeout(done_task.result())
            except Exception:  # noqa: BLE001 — limpieza best-effort
                pass

        task.add_done_callback(_late)
    else:
        task.cancel()

    raise BlockingCallTimeout(label or getattr(func, "__name__", "trabajo"), timeout)
