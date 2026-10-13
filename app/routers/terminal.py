"""NetPulse — Consola Web (WebSSH interactiva por WebSocket).

Endpoint ``/ws/terminal/{device_id}``: abre una sesión SSH interactiva
(PTY) contra el dispositivo y la puentéa hacia el navegador (xterm.js).

Autenticación: el WebSocket no pasa por las dependencias HTTP, así que el
JWT se recibe como query param ``?token=`` y se valida con ``verify_token``.
Solo SSH directo (paramiko); dispositivos solo-SNMP no lo soportan.
"""

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.security import verify_token
from app.services import napalm_svc

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Terminal"])

_ALLOWED_ROLES = {"admin", "operator"}
_READ_CHUNK = 4096


@router.websocket("/ws/terminal/{device_id}")
async def terminal(websocket: WebSocket, device_id: str) -> None:
    """Puente WebSocket ⇄ shell SSH del dispositivo."""
    token = websocket.query_params.get("token", "")
    payload = verify_token(token) if token else None
    if not payload or payload.get("role") not in _ALLOWED_ROLES:
        await websocket.close(code=4401)
        return

    try:
        import paramiko
    except Exception as exc:  # noqa: BLE001
        await websocket.accept()
        await websocket.send_text(f"⚠️ paramiko no disponible: {exc}\r\n")
        await websocket.close()
        return

    device = napalm_svc._find_device(device_id)
    if not device:
        await websocket.accept()
        await websocket.send_text(f"⚠️ Dispositivo '{device_id}' no encontrado\r\n")
        await websocket.close()
        return

    creds = device.get("credentials", {}) or {}
    host = device.get("hostname", "")
    port = int(device.get("cli_port") or device.get("port") or 22)
    username = creds.get("username") or "admin"
    password = creds.get("password", "") or ""

    await websocket.accept()
    await websocket.send_text(f"Conectando a {host}:{port} como {username}...\r\n")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        await asyncio.to_thread(
            client.connect, host,
            port=port, username=username, password=password,
            timeout=10, banner_timeout=15, auth_timeout=15,
            look_for_keys=False, allow_agent=False,
        )
        chan = client.invoke_shell(width=120, height=32)
        chan.settimeout(0.0)
    except Exception as exc:  # noqa: BLE001
        await websocket.send_text(f"⚠️ No se pudo conectar: {exc}\r\n")
        await websocket.close()
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass
        return

    async def _pump_device_to_ws() -> None:
        import socket as _socket
        while True:
            await asyncio.sleep(0.01)
            try:
                if chan.recv_ready():
                    data = chan.recv(_READ_CHUNK)
                    if not data:
                        break
                    await websocket.send_text(data.decode("utf-8", "replace"))
                elif chan.closed:
                    break
                elif chan.recv_stderr_ready():
                    data = chan.recv_stderr(_READ_CHUNK)
                    if data:
                        await websocket.send_text(data.decode("utf-8", "replace"))
            except _socket.timeout:
                continue
            except Exception:  # noqa: BLE001
                break

    async def _pump_ws_to_device() -> None:
        while True:
            message = await websocket.receive_text()
            # Mensaje de control de tamaño (enviado por xterm.js)
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
            chan.send(message)

    pump_out = asyncio.create_task(_pump_device_to_ws())
    pump_in = asyncio.create_task(_pump_ws_to_device())
    try:
        await asyncio.wait({pump_out, pump_in}, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("[terminal] sesión %s terminó: %s", device_id, exc)
    finally:
        for task in (pump_out, pump_in):
            task.cancel()
        try:
            chan.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass
