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


@router.websocket("/ws/vm/{vm_id}")
async def vm_console(websocket: WebSocket, vm_id: str) -> None:
    """Consola serie de una VM señuelo (QEMU).

    Filtra secuencias Telnet IAC para evitar caracteres extraños () y
    permite interacción serie fluida.
    """
    token = websocket.query_params.get("token", "")
    payload = verify_token(token) if token else None
    if not payload or payload.get("role") not in _ALLOWED_ROLES:
        await websocket.close(code=4401)
        return

    from app.services.deception import vm_svc

    await websocket.accept()
    vm = vm_svc.get_vm(vm_id)
    if not vm or not vm.get("running") or not vm.get("serial_port"):
        await websocket.send_text(
            "⚠️ La VM no está arrancada. Arráncala primero para abrir la consola serie.\r\n"
        )
        await websocket.close()
        return

    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", int(vm["serial_port"]))
    except Exception as exc:  # noqa: BLE001
        await websocket.send_text(f"⚠️ No se pudo abrir la consola serie: {exc}\r\n")
        await websocket.close()
        return

    async def _tcp_to_ws() -> None:
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                clean_text, reply = _process_telnet_iac(data)
                if reply:
                    writer.write(reply)
                    await writer.drain()
                if clean_text:
                    await websocket.send_text(clean_text.decode("utf-8", "ignore"))
        except Exception:
            pass

    async def _ws_to_tcp() -> None:
        try:
            while True:
                message = await websocket.receive_text()
                # Normalizar retorno de carro para consola serie si es necesario
                writer.write(message.encode("utf-8", "replace"))
                await writer.drain()
        except Exception:
            pass

    tcp_out = asyncio.create_task(_tcp_to_ws())
    tcp_in = asyncio.create_task(_ws_to_tcp())
    try:
        await asyncio.wait({tcp_out, tcp_in}, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("[terminal] consola VM %s terminó: %s", vm_id, exc)
    finally:
        for task in (tcp_out, tcp_in):
            task.cancel()
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass


@router.websocket("/ws/vm/{vm_id}/vnc")
async def vm_vnc(websocket: WebSocket, vm_id: str) -> None:
    """Proxy WebSocket binario <-> VNC RFB (puerto 5900+ de QEMU).

    Permite mostrar la pantalla gráfica y de texto real de cualquier VM
    (Alpine, Windows, Linux) directamente en el navegador con noVNC.
    """
    token = websocket.query_params.get("token", "")
    payload = verify_token(token) if token else None
    if not payload or payload.get("role") not in _ALLOWED_ROLES:
        await websocket.close(code=4401)
        return

    from app.services.deception import vm_svc

    vm = vm_svc.get_vm(vm_id)
    if not vm or not vm.get("running") or not vm.get("vnc_port"):
        await websocket.close(code=4404)
        return

    # Soporte para subprotocolo binary de noVNC
    subprotocol = None
    requested_protocols = websocket.headers.get("sec-websocket-protocol", "")
    if "binary" in requested_protocols:
        subprotocol = "binary"
    await websocket.accept(subprotocol=subprotocol)

    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", int(vm["vnc_port"]))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[vnc] no se pudo conectar a VNC de %s: %s", vm_id, exc)
        await websocket.close()
        return

    async def _ws_to_tcp() -> None:
        try:
            while True:
                msg = await websocket.receive()
                if "bytes" in msg and msg["bytes"]:
                    writer.write(msg["bytes"])
                    await writer.drain()
                elif "text" in msg and msg["text"]:
                    writer.write(msg["text"].encode("latin1"))
                    await writer.drain()
                elif msg.get("type") == "websocket.disconnect":
                    break
        except Exception:
            pass

    async def _tcp_to_ws() -> None:
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                await websocket.send_bytes(data)
        except Exception:
            pass

    t1 = asyncio.create_task(_ws_to_tcp())
    t2 = asyncio.create_task(_tcp_to_ws())
    try:
        await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
    except Exception:
        pass
    finally:
        for t in (t1, t2):
            t.cancel()
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
