"""NetPulse — Deception Schemas.

Modelos de la red señuelo: segmentos (VLANs), dispositivos simulados
(personas) y los eventos que generan los atacantes. Estos modelos son
la representación validada de ``config/deception/network.yaml`` y del
contrato JSON que usa el data plane (motor nativo o engine en Go).
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class DeceptionRole(str, Enum):
    """Rol del dispositivo simulado."""

    PC = "pc"
    SERVER = "server"
    CAMERA = "camera"
    SWITCH = "switch"
    FIREWALL = "firewall"
    ROUTER = "router"


class DeceptionEngine(str, Enum):
    """Backend que atiende un servicio señuelo."""

    NATIVE = "native"          # listener asyncio del propio NetPulse
    COWRIE = "cowrie"          # honeypot SSH/Telnet (medium-interaction)
    OPENCANARY = "opencanary"  # honeypot multi-protocolo ligero
    DIONAEA = "dionaea"        # captura de malware (SMB/HTTP/FTP)
    GO = "go"                  # data plane en Go (deception-engine)


class DeceptionEventType(str, Enum):
    """Tipo de evento observado (normalizado entre todos los motores)."""

    CONNECT = "connect"
    LOGIN_ATTEMPT = "login_attempt"
    LOGIN_SUCCESS = "login_success"
    COMMAND = "command"
    FILE_DOWNLOAD = "file_download"
    PORT_SCAN = "port_scan"
    SNMP_QUERY = "snmp_query"
    HTTP_REQUEST = "http_request"
    RTSP_REQUEST = "rtsp_request"
    OTHER = "other"


class DeceptionService(BaseModel):
    """Servicio expuesto por un dispositivo señuelo."""

    port: int = Field(..., ge=1, le=65535, description="Puerto TCP/UDP", examples=[22])
    proto: str = Field(..., description="Protocolo (ssh, telnet, http, snmp, rtsp, smb)",
                       examples=["ssh"])
    engine: DeceptionEngine = Field(
        default=DeceptionEngine.NATIVE,
        description="Backend que atiende el servicio",
    )
    banner: Optional[str] = Field(
        default=None, description="Banner/personalidad devuelta al cliente"
    )


class DeceptionPersona(BaseModel):
    """Identidad falsa del dispositivo (vendor/OS/MAC/hostname)."""

    vendor: str = Field(default="generic", description="Fabricante simulado", examples=["hikvision"])
    os: str = Field(default="linux", description="Sistema operativo simulado", examples=["linux"])
    hostname: str = Field(default="device", description="Hostname simulado", examples=["cam-entrada"])
    mac: Optional[str] = Field(default=None, description="MAC simulada", examples=["00:12:5a:00:00:11"])


class DeceptionDevice(BaseModel):
    """Dispositivo virtual de la red señuelo."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., description="Identificador único", examples=["cam-entrada-01"])
    role: DeceptionRole = Field(..., description="Rol del dispositivo")
    segment: str = Field(..., description="Segmento/VLAN al que pertenece", examples=["vlan-camaras"])
    ip: str = Field(..., description="IP simulada", examples=["10.30.0.11"])
    persona: DeceptionPersona = Field(default_factory=DeceptionPersona)
    services: list[DeceptionService] = Field(default_factory=list)
    enabled: bool = Field(default=True, description="Si el motor debe levantarlo")
    tags: list[str] = Field(default_factory=list)
    description: str = Field(default="")


class DeceptionSegment(BaseModel):
    """Segmento/VLAN de la red señuelo."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., description="Identificador del segmento", examples=["vlan-camaras"])
    vlan_id: Optional[int] = Field(default=None, ge=1, le=4094, description="VLAN 802.1Q")
    cidr: str = Field(..., description="Subred del segmento", examples=["10.30.0.0/24"])
    gateway: Optional[str] = Field(default=None, description="Gateway simulado")
    isolated: bool = Field(default=True, description="Sin salida a otras redes")
    description: str = Field(default="")


class DeceptionNetwork(BaseModel):
    """Documento raíz de ``network.yaml``."""

    segments: list[DeceptionSegment] = Field(default_factory=list)
    devices: list[DeceptionDevice] = Field(default_factory=list)


# ── Contrato de telemetría (data plane → NetPulse) ───────────


class DeceptionEventIn(BaseModel):
    """Evento normalizado enviado por un motor (nativo/Go/Cowrie...)."""

    model_config = ConfigDict(extra="ignore")

    device_id: str = Field(..., description="Dispositivo señuelo que recibió la interacción")
    src_ip: str = Field(..., description="IP del atacante", examples=["203.0.113.9"])
    src_port: Optional[int] = Field(default=None, ge=0, le=65535)
    proto: str = Field(default="tcp", description="Protocolo de la interacción")
    type: DeceptionEventType = Field(default=DeceptionEventType.OTHER, description="Tipo de evento")
    detail: dict = Field(default_factory=dict, description="Datos específicos del evento")
    username: Optional[str] = Field(default=None, description="Usuario probado, si aplica")
    success: bool = Field(default=False, description="Si la acción tuvo éxito")
    ts: Optional[str] = Field(default=None, description="Timestamp ISO-8601 (UTC)")


class DeceptionEventOut(BaseModel):
    """Evento de deception tal como se almacena/consulta."""

    id: int
    timestamp: str
    device_id: str
    src_ip: str
    src_port: Optional[int] = None
    proto: str
    type: str
    username: Optional[str] = None
    success: bool
    detail: dict = Field(default_factory=dict)


class DeceptionSession(BaseModel):
    """Sesión agregada por (dispositivo, atacante)."""

    device_id: str
    src_ip: str
    proto: str
    started_at: str
    last_seen: str
    event_count: int
    command_count: int
    success: bool


class DeceptionStats(BaseModel):
    """Resumen para el dashboard."""

    events_total: int
    attackers_unique: int
    devices_configured: int
    devices_enabled: int
    segments: int
    by_type: dict[str, int] = Field(default_factory=dict)
    top_devices: list[dict] = Field(default_factory=list)
    top_attackers: list[dict] = Field(default_factory=list)
