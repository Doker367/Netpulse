"""Listener MySQL señuelo.

Envía el handshake de protocolo v10 con una versión falsa, captura el
usuario del paquete de autenticación y responde OK (login exitoso falso).
Registra consultas posteriores como comandos.
"""

import struct
from typing import Optional

from app.services.deception.listeners.base import BaseListener

SERVER_VERSION = b"5.5.43-0ubuntu0.14.04.1"
SALT = b"h0neyP0tS4lt9x2v"


def _packet(seq: int, payload: bytes) -> bytes:
    return struct.pack("<I", len(payload))[:3] + bytes([seq]) + payload


def _handshake() -> bytes:
    caps = 0x0000FFFF
    payload = b"\x0a" + SERVER_VERSION + b"\x00"
    payload += struct.pack("<I", 1337)          # thread id
    payload += SALT[:8] + b"\x00"               # auth-plugin-data-part-1
    payload += struct.pack("<H", caps & 0xFFFF)
    payload += b"\x21"                           # charset utf8_general_ci
    payload += struct.pack("<H", 0x0002)         # status flags
    payload += struct.pack("<H", (caps >> 16) & 0xFFFF)
    payload += b"\x00"                           # auth plugin data len
    payload += b"\x00" * 10
    payload += SALT[8:] + b"\x00"                # auth-plugin-data-part-2
    payload += b"mysql_native_password\x00"
    return _packet(0, payload)


def _ok(seq: int) -> bytes:
    return _packet(seq, b"\x00\x00\x00\x02\x00\x00\x00")


def _err(seq: int, code: int, msg: bytes) -> bytes:
    return _packet(seq, b"\xff" + struct.pack("<H", code) + b"#HY000" + msg)


async def _read_packet(reader) -> Optional[bytes]:
    header = await reader.readexactly(4)
    length = header[0] | (header[1] << 8) | (header[2] << 16)
    if length <= 0 or length > 1_000_000:
        return None
    return await reader.readexactly(length)


class MysqlListener(BaseListener):
    proto = "mysql"

    async def handle(self, reader, writer, src_ip, src_port):
        await self.write(writer, _handshake())

        response = await _read_packet(reader)
        if response is None:
            return

        username = _parse_username(response)
        await self.emit(src_ip, "login_attempt", username=username, src_port=src_port,
                        detail={"service": "mysql", "server": SERVER_VERSION.decode()})
        await self.write(writer, _ok(2))
        await self.emit(src_ip, "login_success", username=username, success=True,
                        src_port=src_port, detail={"service": "mysql"})

        for _ in range(20):
            packet = await _read_packet(reader)
            if packet is None:
                return
            if packet[:1] == b"\x03":  # COM_QUERY
                query = packet[1:].decode("utf-8", errors="replace")
                await self.emit(src_ip, "command", username=username, src_port=src_port,
                                detail={"service": "mysql", "command": query})
                await self.write(writer, _ok(1))
            elif packet[:1] == b"\x0e":  # COM_PING
                await self.write(writer, _ok(1))
            elif packet[:1] == b"\x01":  # COM_QUIT
                return
            else:
                await self.write(writer, _err(1, 1047, b"Unknown command"))


def _parse_username(payload: bytes) -> str:
    """Extrae el usuario del paquete HandshakeResponse41."""
    try:
        idx = 4 + 4 + 1 + 23  # capability, max_packet, charset, reserved
        end = payload.index(b"\x00", idx)
        return payload[idx:end].decode("utf-8", errors="replace")
    except (ValueError, IndexError):
        return "unknown"
