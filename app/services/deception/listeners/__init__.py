"""Listeners nativos de la red señuelo (Fase B).

Cada clase atiende un protocolo, registra la interacción del atacante
vía ``telemetry_svc`` y responde con una persona falsa. El motor
(``engine_svc``) instancia uno por cada servicio del dispositivo.
"""

from app.services.deception.listeners.base import BaseListener, read_line
from app.services.deception.listeners.telnet_listener import TelnetListener
from app.services.deception.listeners.http_listener import HttpListener
from app.services.deception.listeners.banner_listener import BannerListener
from app.services.deception.listeners.udp_listener import UdpListener
from app.services.deception.listeners.ssh_listener import SshListener
from app.services.deception.listeners.ftp_listener import FtpListener
from app.services.deception.listeners.mysql_listener import MysqlListener

# Mapa protocolo → clase de listener
_LISTENERS = {
    "telnet": TelnetListener,
    "http": HttpListener,
    "https": HttpListener,
    "rtsp": BannerListener,
    "smb": BannerListener,
    "snmp": UdpListener,
    "ssh": SshListener,
    "ftp": FtpListener,
    "mysql": MysqlListener,
}

# Protocolos cuyo listener es UDP (el resto TCP)
UDP_PROTOCOLS = {"snmp"}


def build_listener(device_id: str, role: str, host: str, port: int,
                   proto: str, banner: str | None = None):
    """Crea el listener nativo adecuado para un protocolo.

    Returns:
        Instancia de listener, o None si el protocolo no está soportado.
    """
    klass = _LISTENERS.get((proto or "").lower())
    if klass is None:
        return None
    return klass(device_id=device_id, role=role, host=host, port=port,
                 banner=banner, proto=(proto or "").lower())


__all__ = [
    "BaseListener",
    "read_line",
    "TelnetListener",
    "HttpListener",
    "BannerListener",
    "UdpListener",
    "SshListener",
    "FtpListener",
    "MysqlListener",
    "build_listener",
    "UDP_PROTOCOLS",
]
