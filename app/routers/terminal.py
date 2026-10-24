"""NetPulse — Consola Web (WebSSH interactiva por WebSocket).

Endpoint ``/ws/terminal/{device_id}``: abre una sesión SSH interactiva
(PTY) contra el dispositivo y la puentea hacia el navegador (xterm.js).

Autenticación: el WebSocket no pasa por las dependencias HTTP, así que el
JWT se recibe como query param ``?token=`` y se valida con ``verify_token``.
Solo SSH directo (paramiko); dispositivos solo-SNMP no lo soportan.

Protocolo dual (retro-compatible):
- Sin ``?fmt`` (o con cualquier valor distinto de ``json``): modo **legacy**
  de texto plano, idéntico al que consume la UI actual (líneas "Conectando
  a ...", chunks del PTY y avisos).
- Con ``?fmt=json``: eventos JSON por mensaje con contrato documentado en
  ``docs/handoffs/terminal.md`` (connected / output / error / closed), más
  heartbeat ping/pong. La UI de la tarjeta 7C activa este modo.

Puerto SSH: ``_resolve_ssh_port`` duplica deliberadamente el criterio de
``app/routers/sftp.py`` (``cli_port`` > ``ssh_port`` > ``port`` solo si no
es un puerto de API del driver > 22). No se importa de sftp para no acoplar
routers; si el criterio cambia, actualizar ambos (ver sftp.py como
referencia).
"""

import asyncio
import json
import logging
import os
import socket
import time

import anyio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.security import verify_token
from app.services import napalm_svc

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Terminal"])

_ALLOWED_ROLES = {"admin", "operator"}
_READ_CHUNK = 4096

# ── Resolución de puerto SSH (duplicado mínimo de app/routers/sftp.py) ──

# Puertos de API conocidos por driver. Si el inventario usa uno de estos como
# ``port``, NO se intenta SSH ahí: se usa cli_port/ssh_port o el default 22.
_API_PORTS_BY_DRIVER: dict[str, frozenset[int]] = {
    "ros": frozenset({8728, 8729}),
    "eos": frozenset({443}),
}

_SSH_DEFAULT_PORT = 22
_CONNECT_TIMEOUT = 10
_BANNER_TIMEOUT = 15
_AUTH_TIMEOUT = 15
_IDLE_DEFAULT_S = 900.0


def _as_port(value) -> int | None:
    """Normaliza un valor de puerto; devuelve None si no es válido."""
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if 0 < port < 65536 else None


def _resolve_ssh_port(device: dict) -> tuple[int, int | None]:
    """Resuelve el puerto SSH real del dispositivo.

    Orden: ``cli_port`` > ``ssh_port`` > ``port`` (solo si no es un puerto
    de API del driver) > 22.

    Returns:
        ``(puerto_ssh, puerto_api_ignorado)``. El segundo valor no es None
        cuando el ``port`` del inventario apuntaba a una API y se descartó.
    """
    driver = str(device.get("driver") or "").lower()
    api_ports = _API_PORTS_BY_DRIVER.get(driver, frozenset())
    protocol = str(device.get("protocol") or "").lower()

    cli_port = _as_port(device.get("cli_port"))
    if cli_port:
        return cli_port, None

    ssh_port = _as_port(device.get("ssh_port"))
    if ssh_port:
        return ssh_port, None

    port = _as_port(device.get("port"))
    port_is_known_ssh = (
        port is not None
        and port not in api_ports
        and port not in (23, 161)  # telnet / SNMP no son SSH
        and protocol in ("", "ssh", "sftp")
    )
    if port_is_known_ssh:
        return port, None

    ignored_api_port = port if port in api_ports else None
    return _SSH_DEFAULT_PORT, ignored_api_port


def _api_port_note(driver: str, api_port: int, ssh_port: int) -> str:
    """Sufijo de error cuando el inventario apuntaba a un puerto API."""
    return (
        f" El inventario apunta al puerto API {api_port} del driver "
        f"'{driver}', que no ofrece SSH; se intentó el {ssh_port}."
    )


def _idle_timeout_s() -> float:
    """Timeout de inactividad desde ``NETPULSE_TERM_IDLE_S`` (default 900 s)."""
    raw = os.getenv("NETPULSE_TERM_IDLE_S", "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _IDLE_DEFAULT_S
    return value if value > 0 else _IDLE_DEFAULT_S


def _short_cause(exc: BaseException) -> str:
    """Causa corta y sin credenciales para mostrar al operador."""
    text = str(exc).strip() or exc.__class__.__name__
    return text[:200]


def _classify_connect_error(
    exc: BaseException,
    *,
    device_id: str,
    host: str,
    port: int,
    driver: str,
    ignored_api_port: int | None,
) -> tuple[str, str]:
    """Traduce un fallo de conexión SSH a ``(code, detail)`` honestos."""
    import paramiko

    note = ""
    if ignored_api_port is not None:
        note = _api_port_note(driver, ignored_api_port, port)

    if isinstance(exc, paramiko.AuthenticationException):
        return (
            "ssh_auth",
            f"Autenticación SSH fallida en {device_id}: revisa las credenciales.",
        )

    if isinstance(exc, (socket.timeout, TimeoutError)):
        return (
            "ssh_connect",
            f"Tiempo de espera agotado conectando a {device_id} ({host}:{port})."
            + note,
        )

    if isinstance(exc, socket.gaierror):
        return (
            "ssh_connect",
            f"No se pudo conectar a {device_id} ({host}:{port}): host no resuelto."
            + note,
        )

    return (
        "ssh_connect",
        f"No se pudo conectar a {device_id} ({host}:{port}): {_short_cause(exc)}"
        + note,
    )


def _closed_reason(code: str) -> str:
    """Motivo humano (español) del cierre, por código de ``closed``."""
    return {
        "eof": "fin de sesión",
        "idle": "timeout de inactividad",
        "unreachable": "equipo inalcanzable",
        "operator": "cerrado por el operador",
        "device_not_found": "equipo no encontrado",
        "internal": "error interno",
    }.get(code, "fin de sesión")


async def _send_event(websocket: WebSocket, payload: dict) -> None:
    """Envía un evento JSON escapado correctamente."""
    await websocket.send_text(json.dumps(payload, ensure_ascii=False))


@router.websocket("/ws/terminal/{device_id}")
async def terminal(websocket: WebSocket, device_id: str) -> None:
    """Puente WebSocket ⇄ shell SSH del dispositivo (texto legacy o JSON)."""
    token = websocket.query_params.get("token", "")
    payload = verify_token(token) if token else None
    if not payload or payload.get("role") not in _ALLOWED_ROLES:
        await websocket.close(code=4401)
        return

    json_mode = (websocket.query_params.get("fmt") or "").lower() == "json"

    try:
        import paramiko
    except Exception as exc:  # noqa: BLE001
        await websocket.accept()
        logger.error("[terminal] paramiko no disponible: %s", exc)
        if json_mode:
            await _send_event(
                websocket,
                {
                    "type": "error",
                    "code": "unsupported",
                    "detail": f"paramiko no disponible en el servidor: {_short_cause(exc)}",
                },
            )
            await _send_event(
                websocket,
                {"type": "closed", "code": "internal", "reason": _closed_reason("internal")},
            )
        else:
            await websocket.send_text(f"⚠️ paramiko no disponible: {exc}\r\n")
        await websocket.close()
        return

    device = napalm_svc._find_device(device_id)
    if not device:
        await websocket.accept()
        logger.warning("[terminal] dispositivo no encontrado: %s", device_id)
        if json_mode:
            await _send_event(
                websocket,
                {
                    "type": "error",
                    "code": "device_not_found",
                    "detail": f"Dispositivo '{device_id}' no encontrado.",
                },
            )
            await _send_event(
                websocket,
                {
                    "type": "closed",
                    "code": "device_not_found",
                    "reason": _closed_reason("device_not_found"),
                },
            )
        else:
            await websocket.send_text(f"⚠️ Dispositivo '{device_id}' no encontrado\r\n")
        await websocket.close()
        return

    creds = device.get("credentials", {}) or {}
    host = device.get("hostname", "") or device.get("ip", "") or ""
    port, ignored_api_port = _resolve_ssh_port(device)
    driver = str(device.get("driver") or "")
    username = creds.get("username") or "admin"
    password = creds.get("password", "") or ""

    await websocket.accept()
    if not json_mode:
        await websocket.send_text(f"Conectando a {host}:{port} como {username}...\r\n")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        await asyncio.to_thread(
            client.connect, host,
            port=port, username=username, password=password,
            timeout=_CONNECT_TIMEOUT, banner_timeout=_BANNER_TIMEOUT,
            auth_timeout=_AUTH_TIMEOUT,
            look_for_keys=False, allow_agent=False,
        )
        chan = client.invoke_shell(width=120, height=32)
        chan.settimeout(0.0)
        if json_mode:
            # ``connected`` solo cuando el SSH+PTY están realmente abiertos.
            # Sin password: el contrato prohíbe enviar credenciales al navegador.
            await _send_event(
                websocket,
                {
                    "type": "connected",
                    "device": device_id,
                    "host": host,
                    "port": port,
                    "user": username,
                },
            )
    except Exception as exc:  # noqa: BLE001
        code, detail = _classify_connect_error(
            exc,
            device_id=device_id,
            host=host,
            port=port,
            driver=driver,
            ignored_api_port=ignored_api_port,
        )
        logger.warning(
            "[terminal] fallo conectando %s (%s:%s, driver=%s, api_ignored=%s): %s",
            device_id, host, port, driver, ignored_api_port, exc,
        )
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass
        if json_mode:
            await _send_event(
                websocket, {"type": "error", "code": code, "detail": detail}
            )
            close_code = "unreachable" if code in ("ssh_connect", "ssh_auth") else "internal"
            await _send_event(
                websocket,
                {
                    "type": "closed",
                    "code": close_code,
                    "reason": _closed_reason(close_code),
                },
            )
        else:
            await websocket.send_text(f"⚠️ No se pudo conectar: {detail}\r\n")
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass
        return

    loop = asyncio.get_running_loop()
    # Estado compartido del cierre: los pumps/watchdog lo actualizan y el
    # cuerpo principal envía el evento ``closed`` una sola vez.
    state: dict = {
        "code": "eof",
        "activity": loop.time(),
        "disconnected": False,
    }

    async def _pump_device_to_ws() -> None:
        import socket as _socket
        while True:
            await asyncio.sleep(0.01)
            try:
                if chan.recv_ready():
                    data = chan.recv(_READ_CHUNK)
                    if not data:
                        break
                    state["activity"] = loop.time()
                    text = data.decode("utf-8", "replace")
                    if json_mode:
                        await _send_event(websocket, {"type": "output", "data": text})
                    else:
                        await websocket.send_text(text)
                elif getattr(chan, "closed", False):
                    break
                elif chan.recv_stderr_ready():
                    data = chan.recv_stderr(_READ_CHUNK)
                    if data:
                        state["activity"] = loop.time()
                        text = data.decode("utf-8", "replace")
                        if json_mode:
                            await _send_event(websocket, {"type": "output", "data": text})
                        else:
                            await websocket.send_text(text)
            except _socket.timeout:
                continue
            except WebSocketDisconnect:
                state["disconnected"] = True
                return
            except Exception as exc:  # noqa: BLE001
                logger.debug("[terminal] pump salida %s terminó: %s", device_id, exc)
                return
        state["code"] = "eof"

    async def _pump_ws_to_device() -> None:
        while True:
            try:
                message = await websocket.receive_text()
            except WebSocketDisconnect:
                state["disconnected"] = True
                state["code"] = "eof"
                return
            state["activity"] = loop.time()

            if json_mode:
                try:
                    obj = json.loads(message)
                except (TypeError, ValueError):
                    obj = None
                if isinstance(obj, dict):
                    kind = obj.get("type")
                    if kind == "ping":
                        await _send_event(
                            websocket, {"type": "pong", "ts": time.time()}
                        )
                        continue
                    if kind == "close":
                        state["code"] = "operator"
                        return
                    if kind == "resize":
                        try:
                            chan.resize_pty(
                                width=int(obj["cols"]), height=int(obj["rows"])
                            )
                        except (KeyError, TypeError, ValueError):
                            pass
                        continue
                    if kind == "input":
                        chan.send(str(obj.get("data", "")))
                        continue
                    # JSON desconocido: se ignora para no inyectarlo en la shell.
                    continue

            # Mensajes de control de tamaño (compatibles con el modo legacy).
            if message.startswith("__resize:"):
                try:
                    cols, rows = (int(x) for x in message[len("__resize:"):].split(","))
                    chan.resize_pty(width=cols, height=rows)
                except (ValueError, TypeError):
                    pass
                continue
            if message.startswith("__json:"):
                try:
                    obj = json.loads(message[len("__json:"):])
                    if obj.get("type") == "resize":
                        chan.resize_pty(width=int(obj["cols"]), height=int(obj["rows"]))
                except Exception:  # noqa: BLE001
                    pass
                continue
            try:
                chan.send(message)
            except Exception as exc:  # noqa: BLE001
                logger.debug("[terminal] envío al canal falló %s: %s", device_id, exc)
                return

    async def _idle_watchdog() -> None:
        while True:
            remaining = _idle_timeout_s() - (loop.time() - state["activity"])
            if remaining <= 0:
                state["code"] = "idle"
                return
            await asyncio.sleep(min(remaining, 0.25))

    pump_out = asyncio.create_task(_pump_device_to_ws())
    pump_in = asyncio.create_task(_pump_ws_to_device())
    idle_task = asyncio.create_task(_idle_watchdog())
    tasks = (pump_out, pump_in, idle_task)
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except asyncio.CancelledError:
        # Apagado del servidor o cancelación del cliente: limpiar y propagar.
        state["code"] = "eof"
        state["disconnected"] = True
        raise
    except Exception as exc:  # noqa: BLE001
        state["code"] = "internal"
        logger.debug("[terminal] sesión %s terminó: %s", device_id, exc)
    finally:
        # Shield: aunque el servidor cancele la tarea, la sesión SSH debe
        # cerrarse y las tareas PTY quedar canceladas (sin huérfanas).
        with anyio.CancelScope(shield=True):
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                chan.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
            logger.info(
                "[terminal] sesión %s cerrada (%s, host=%s:%s, user=%s)",
                device_id, state["code"], host, port, username,
            )

    with anyio.CancelScope(shield=True):
        if json_mode and not state.get("disconnected"):
            try:
                await _send_event(
                    websocket,
                    {
                        "type": "closed",
                        "code": state["code"],
                        "reason": _closed_reason(state["code"]),
                    },
                )
            except Exception:  # noqa: BLE001
                pass
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass


def _process_telnet_iac(data: bytes) -> tuple[bytes, bytes]:
    """Procesa y limpia comandos Telnet IAC (0xFF) y genera respuestas de negociación."""
    IAC = 255
    DO = 253
    DONT = 254
    WILL = 251
    WONT = 252

    clean = bytearray()
    reply = bytearray()
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        if b == IAC and i + 1 < n:
            cmd = data[i + 1]
            if cmd in (WILL, WONT, DO, DONT) and i + 2 < n:
                opt = data[i + 2]
                if cmd == WILL:
                    # Aceptar ECHO (1), SUPPRESS GO AHEAD (3), BINARY (0)
                    reply.extend([IAC, DO if opt in (0, 1, 3) else DONT, opt])
                elif cmd == DO:
                    reply.extend([IAC, WILL if opt in (0, 3) else WONT, opt])
                i += 3
                continue
            elif cmd == IAC:
                clean.append(IAC)
                i += 2
                continue
            elif cmd == 250:  # SB (subnegotiation) -> hasta SE (0xF0)
                se_idx = data.find(b"\xff\xf0", i + 2)
                if se_idx != -1:
                    i = se_idx + 2
                else:
                    break
                continue
            else:
                i += 2
                continue
        else:
            clean.append(b)
            i += 1
    return bytes(clean), bytes(reply)
