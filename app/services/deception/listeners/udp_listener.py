"""Listener UDP señuelo (SNMP).

Recibe datagramas SNMP y responde con un PDU GetResponse válido (MIB
reducida: system group) coherente con la persona del dispositivo, vía
``snmp_agent``. Así ``nmap``/``snmpwalk`` ven un equipo real.
"""

import asyncio
import logging
from typing import Optional

from app.services.deception import personas, snmp_agent, telemetry_svc, topology_svc

logger = logging.getLogger(__name__)


class _SnmpProtocol(asyncio.DatagramProtocol):
    def __init__(self, listener: "UdpListener"):
        self.listener = listener

    def datagram_received(self, data: bytes, addr) -> None:
        asyncio.create_task(self.listener.on_datagram(data, addr))

    def error_received(self, exc) -> None:  # noqa: N802
        logger.debug("[deception] SNMP error: %s", exc)


class UdpListener:
    proto = "snmp"

    def __init__(self, device_id: str, role: str, host: str, port: int,
                 banner: Optional[str] = None, proto: Optional[str] = None):
        self.device_id = device_id
        self.role = role
        self.host = host
        self.port = port
        self.banner = banner
        if proto:
            self.proto = proto
        self._transport: Optional[asyncio.DatagramTransport] = None

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._transport, _proto = await loop.create_datagram_endpoint(
            lambda: _SnmpProtocol(self),
            local_addr=(self.host, self.port),
        )
        sock = self._transport.get_extra_info("socket")
        if sock is not None:
            self.port = sock.getsockname()[1]
        logger.info("[deception] snmp %s:%s escuchando (UDP, %s)",
                    self.host, self.port, self.device_id)

    async def stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    async def on_datagram(self, data: bytes, addr) -> None:
        src_ip, src_port = addr[0], addr[1]
        await telemetry_svc.emit(
            self.device_id, src_ip, self.proto, "snmp_query",
            src_port=src_port,
            detail={"service": "snmp", "bytes": len(data)},
        )
        # Responder con un GetResponse coherente con la persona.
        try:
            device = topology_svc.get_device(self.device_id) or {}
            persona = device.get("persona") or {}
            identity = personas.snmp_identity(
                self.role, persona.get("vendor"), persona.get("hostname")
            )
            response = snmp_agent.handle_datagram(data, identity)
            if response and self._transport is not None:
                self._transport.sendto(response, addr)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[deception] SNMP respuesta falló: %s", exc)
