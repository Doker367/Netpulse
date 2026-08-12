"""NetPulse — Pydantic Schemas.

Todos los modelos incluyen descripciones en español y ejemplos
para que Swagger UI los muestre automáticamente.
"""
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, IPvAnyAddress, Field, ConfigDict


# ── Device Inventory ────────────────────────────────────────

class DeviceType(str, Enum):
    """Tipo de dispositivo de red."""
    ROUTER = "router"
    SWITCH = "switch"
    FIREWALL = "firewall"
    SERVER = "server"


class DeviceDriver(str, Enum):
    """Driver NAPALM compatible para el dispositivo."""
    IOS = "ios"
    IOSXR = "iosxr"
    NXOS = "nxos"
    JUNOS = "junos"
    EOS = "eos"
    ROS = "ros"            # MikroTik
    FORTIOS = "fortios"
    LINUX = "linux"
    PROCURVE = "procurve"  # HP ProCurve (usa CLI Cisco-like con driver ios)
    COMWARE = "comware"    # HP/H3C Comware (usa CLI Cisco-like con driver ios)
    HPE = "hpe"            # HP Enterprise (fallback a ios)


class DeviceBase(BaseModel):
    """Modelo base para datos de dispositivo."""
    id: str = Field(
        ...,
        description="Identificador único del dispositivo (ej. 'cisco-core-01')",
        examples=["cisco-core-01"],
    )
    hostname: str = Field(
        ...,
        description="Dirección IP o FQDN del dispositivo",
        examples=["192.168.1.1", "router.mi-empresa.com"],
    )
    port: int = Field(
        default=22,
        ge=1,
        le=65535,
        description="Puerto SSH/API del dispositivo (22 para SSH, 8728 para MikroTik API)",
        examples=[22, 8728],
    )
    driver: DeviceDriver = Field(
        ...,
        description="Driver NAPALM a utilizar para comunicarse con el dispositivo",
        examples=["ios", "eos", "ros"],
    )
    username: str = Field(
        ...,
        description="Nombre de usuario para autenticación SSH/API",
        examples=["admin"],
    )
    type: DeviceType = Field(
        default=DeviceType.ROUTER,
        description="Tipo de dispositivo de red",
        examples=["router", "switch"],
    )
    group: Optional[str] = Field(
        default=None,
        description="Grupo al que pertenece el dispositivo (ej. 'Core', 'Access', 'Edge', 'Firewall', 'DMZ')",
        examples=["Core", "Access", "Edge"],
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Etiquetas para agrupar/filtrar dispositivos",
        examples=[["produccion", "core"], ["laboratorio"]],
    )
    description: str = Field(
        default="",
        description="Descripción libre del dispositivo",
        examples=["Router principal del datacenter"],
    )
    enable_password: Optional[str] = Field(
        default=None,
        description="Enable secret para Cisco/HP (si el dispositivo requiere enable mode)",
        examples=["enable123!"],
    )


class DeviceCreate(DeviceBase):
    """Datos necesarios para crear un dispositivo (incluye contraseña)."""
    password: str = Field(
        ...,
        description="Contraseña para autenticación SSH/API (se cifra antes de almacenar)",
        examples=["Micr0s0ft2024!"],
    )


class DeviceResponse(BaseModel):
    """Información de dispositivo SIN contraseña ni credenciales."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "cisco-core-01",
                "hostname": "192.168.1.1",
                "port": 22,
                "driver": "ios",
                "type": "router",
                "group": "Core",
                "tags": ["produccion", "core"],
                "description": "Router principal",
            }
        }
    )
    id: str = Field(..., description="Identificador único", examples=["cisco-core-01"])
    hostname: str = Field(..., description="IP o FQDN", examples=["192.168.1.1"])
    port: int = Field(default=22, description="Puerto de conexión", examples=[22])
    driver: DeviceDriver = Field(..., description="Driver NAPALM", examples=["ios"])
    type: DeviceType = Field(default=DeviceType.ROUTER, description="Tipo de dispositivo", examples=["router"])
    group: Optional[str] = Field(default=None, description="Grupo del dispositivo", examples=["Core"])
    tags: list[str] = Field(default_factory=list, description="Etiquetas", examples=[["produccion"]])
    description: str = Field(default="", description="Descripción", examples=["Router principal"])


class DeviceUpdate(BaseModel):
    """Campos actualizables de un dispositivo (todos opcionales)."""
    hostname: Optional[str] = Field(None, description="Nueva IP o FQDN", examples=["10.0.0.1"])
    port: Optional[int] = Field(None, description="Nuevo puerto", examples=[2222])
    driver: Optional[DeviceDriver] = Field(None, description="Nuevo driver NAPALM", examples=["eos"])
    username: Optional[str] = Field(None, description="Nuevo usuario", examples=["netadmin"])
    password: Optional[str] = Field(None, description="Nueva contraseña", examples=["NewPass123!"])
    type: Optional[DeviceType] = Field(None, description="Nuevo tipo", examples=["switch"])
    group: Optional[str] = Field(None, description="Nuevo grupo", examples=["Edge"])
    tags: Optional[list[str]] = Field(None, description="Nuevas etiquetas", examples=[["produccion"]])
    description: Optional[str] = Field(None, description="Nueva descripción", examples=["Actualizado"])


# ── Group Schemas ─────────────────────────────────────────────


class GroupCreate(BaseModel):
    """Datos para crear un nuevo grupo."""
    name: str = Field(
        ...,
        description="Nombre del grupo (ej. 'Core', 'Access', 'Edge', 'Firewall', 'DMZ')",
        examples=["Core", "Access", "Edge"],
    )
    description: str = Field(
        default="",
        description="Descripción opcional del grupo",
        examples=["Dispositivos del núcleo de la red"],
    )


class GroupResponse(BaseModel):
    """Información de un grupo con conteo de dispositivos."""
    name: str = Field(..., description="Nombre del grupo", examples=["Core"])
    description: str = Field(default="", description="Descripción del grupo", examples=["Dispositivos del núcleo"])
    device_count: int = Field(default=0, description="Cantidad de dispositivos en el grupo", examples=[3])


# ── NAPALM Operations ───────────────────────────────────────

class FactsResponse(BaseModel):
    """Facts del dispositivo obtenidos vía NAPALM."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "device_id": "cisco-core-01",
                "hostname": "core-router",
                "os_version": "15.2(4)M11",
                "model": "Cisco 7200",
                "vendor": "Cisco",
                "serial_number": "FTX0945W0MY",
                "uptime": 864000.0,
                "interface_count": 8,
            }
        }
    )
    device_id: str = Field(..., description="ID del dispositivo", examples=["cisco-core-01"])
    hostname: str = Field(..., description="Hostname configurado en el dispositivo", examples=["core-router"])
    os_version: str = Field(..., description="Versión del sistema operativo", examples=["15.2(4)M11"])
    model: str = Field(..., description="Modelo del hardware", examples=["Cisco 7200"])
    vendor: str = Field(..., description="Fabricante", examples=["Cisco"])
    serial_number: str = Field(..., description="Número de serie", examples=["FTX0945W0MY"])
    uptime: float = Field(..., description="Tiempo encendido en segundos", examples=[864000.0])
    interface_count: int = Field(..., description="Cantidad de interfaces detectadas", examples=[8])


class InterfaceInfo(BaseModel):
    """Información de una interfaz de red."""
    name: str = Field(..., description="Nombre de la interfaz", examples=["GigabitEthernet0/0"])
    is_up: bool = Field(..., description="¿Está operativa (up)?", examples=[True])
    is_enabled: bool = Field(..., description="¿Está habilitada?", examples=[True])
    description: str = Field(..., description="Descripción configurada", examples=["Enlace a sucursal"])
    mac_address: str = Field(..., description="Dirección MAC", examples=["00:1A:2B:3C:4D:5E"])
    speed: Optional[int] = Field(None, description="Velocidad en Mbps", examples=[1000])
    mtu: Optional[int] = Field(None, description="MTU configurada", examples=[1500])


class InterfacesResponse(BaseModel):
    """Listado de interfaces del dispositivo."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "device_id": "cisco-core-01",
                "interfaces": [
                    {
                        "name": "GigabitEthernet0/0",
                        "is_up": True,
                        "is_enabled": True,
                        "description": "Enlace WAN",
                        "mac_address": "00:1A:2B:3C:4D:5E",
                        "speed": 1000,
                        "mtu": 1500,
                    }
                ],
                "total": 1,
                "up_count": 1,
                "down_count": 0,
            }
        }
    )
    device_id: str = Field(..., description="ID del dispositivo", examples=["cisco-core-01"])
    interfaces: list[InterfaceInfo] = Field(..., description="Lista de interfaces")
    total: int = Field(..., description="Total de interfaces", examples=[8])
    up_count: int = Field(..., description="Interfaces operativas", examples=[6])
    down_count: int = Field(..., description="Interfaces inactivas", examples=[2])


class BgpPeer(BaseModel):
    """Información de un vecino BGP."""
    peer_ip: str = Field(..., description="IP del vecino BGP", examples=["10.0.0.2"])
    remote_as: int = Field(..., description="AS remoto del vecino", examples=[65002])
    state: str = Field(..., description="Estado de la sesión BGP", examples=["Established"])
    description: str = Field(..., description="Descripción del peer", examples=["Enlace a sucursal"])


class BgpResponse(BaseModel):
    """Estado de sesiones BGP del dispositivo."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "device_id": "cisco-core-01",
                "local_as": 65001,
                "peers": [
                    {
                        "peer_ip": "10.0.0.2",
                        "remote_as": 65002,
                        "state": "Established",
                        "description": "Sucursal Norte",
                    }
                ],
                "peer_count": 1,
            }
        }
    )
    device_id: str = Field(..., description="ID del dispositivo", examples=["cisco-core-01"])
    local_as: Optional[int] = Field(None, description="AS local del dispositivo", examples=[65001])
    peers: list[BgpPeer] = Field(default_factory=list, description="Lista de vecinos BGP")
    peer_count: int = Field(..., description="Cantidad de vecinos BGP", examples=[3])


class ConfigBackup(BaseModel):
    """Respaldo de configuración del dispositivo."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "device_id": "cisco-core-01",
                "timestamp": "2025-06-08T18:00:00Z",
                "running_config": "hostname core-router\n...",
                "startup_config": None,
                "size_bytes": 4521,
            }
        }
    )
    device_id: str = Field(..., description="ID del dispositivo", examples=["cisco-core-01"])
    timestamp: datetime = Field(..., description="Momento del backup")
    running_config: str = Field(..., description="Configuración en ejecución (running-config)")
    startup_config: Optional[str] = Field(None, description="Configuración de arranque (startup-config)")
    size_bytes: int = Field(..., description="Tamaño en bytes de la configuración", examples=[4521])


class ConfigDiffRequest(BaseModel):
    """Configuración candidata para comparar (dry-run)."""
    candidate_config: str = Field(
        ...,
        description="Configuración propuesta a comparar con la running actual",
        examples=["hostname nuevo-hostname\ninterface Gi0/1\ndescription Nueva descripcion\n"],
    )


class ConfigDeployRequest(BaseModel):
    """Configuración candidata para hacer deploy."""
    candidate_config: str = Field(
        ...,
        description="Configuración a desplegar en el dispositivo",
        examples=["hostname nuevo-hostname\ninterface Gi0/1\ndescription Nueva descripcion\n"],
    )


class ConfigDryRunResponse(BaseModel):
    """Respuesta de dry-run: muestra diff sin aplicar cambios."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "device_id": "cisco-core-01",
                "diff": "--- cisco-core-01-running\n+++ cisco-core-01-candidate\n@@ -1 +1 @@\n-hostname viejo\n+hostname nuevo\n",
            }
        }
    )
    device_id: str = Field(..., description="ID del dispositivo", examples=["cisco-core-01"])
    diff: str = Field(..., description="Diff unificado (cambios que se aplicarían)", examples=["--- running\n+++ candidate\n@@ -1 +1 @@\n-hostname viejo\n+hostname nuevo\n"])


class ConfigDeployResponse(BaseModel):
    """Respuesta de deploy completo."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "device_id": "cisco-core-01",
                "backup_file": "/backups/cisco-core-01_20250608_180000.cfg",
                "diff": "--- running\n+++ candidate\n@@ -1 +1 @@\n-hostname core\n+hostname nuevo\n",
                "committed": True,
                "error": None,
            }
        }
    )
    device_id: str = Field(..., description="ID del dispositivo", examples=["cisco-core-01"])
    backup_file: Optional[str] = Field(None, description="Archivo de backup pre-deploy", examples=["/backups/cisco-core-01_20250608_180000.cfg"])
    diff: str = Field("", description="Diff de los cambios aplicados")
    committed: bool = Field(False, description="¿Se aplicaron los cambios?", examples=[True])
    error: Optional[str] = Field(None, description="Error si ocurrió", examples=[None])


class ConfigRollbackResponse(BaseModel):
    """Respuesta de rollback."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "device_id": "cisco-core-01",
                "rolled_back": True,
                "error": None,
            }
        }
    )
    device_id: str = Field(..., description="ID del dispositivo", examples=["cisco-core-01"])
    rolled_back: bool = Field(..., description="¿Se revirtió exitosamente?", examples=[True])
    error: Optional[str] = Field(None, description="Mensaje de error si falló el rollback", examples=[None])


class ConfigDiff(BaseModel):
    """Resultado de diff entre running y candidate config."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "device_id": "cisco-core-01",
                "diff": "--- cisco-core-01-running\n+++ cisco-core-01-candidate\n@@ -1,3 +1,3 @@\n-hostname core-router\n+hostname nuevo-hostname\n",
            }
        }
    )
    device_id: str = Field(..., description="ID del dispositivo", examples=["cisco-core-01"])
    diff: str = Field(..., description="Diff unificado mostrando diferencias", examples=["--- running\n+++ candidate\n@@ -1 +1 @@\n-hostname viejo\n+hostname nuevo\n"])


class PingResult(BaseModel):
    """Resultado de ping desde el dispositivo."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "device_id": "cisco-core-01",
                "target": "8.8.8.8",
                "success": True,
                "probes_sent": 5,
                "probes_received": 5,
                "rtt_min": 14.5,
                "rtt_avg": 18.2,
                "rtt_max": 22.1,
            }
        }
    )
    device_id: str = Field(..., description="ID del dispositivo", examples=["cisco-core-01"])
    target: str = Field(..., description="Destino del ping", examples=["8.8.8.8"])
    success: bool = Field(..., description="¿Hubo respuesta?", examples=[True])
    probes_sent: int = Field(..., description="Paquetes enviados", examples=[5])
    probes_received: int = Field(..., description="Paquetes recibidos", examples=[5])
    rtt_min: float = Field(..., description="RTT mínimo en ms", examples=[14.5])
    rtt_avg: float = Field(..., description="RTT promedio en ms", examples=[18.2])
    rtt_max: float = Field(..., description="RTT máximo en ms", examples=[22.1])


class DeviceStatus(BaseModel):
    """Estado rápido de conectividad de un dispositivo."""
    device_id: str = Field(..., description="ID del dispositivo", examples=["cisco-core-01"])
    online: bool = Field(..., description="¿Responde el dispositivo?", examples=[True])
    driver: str = Field(..., description="Driver NAPALM usado", examples=["ios"])
    hostname: str = Field(..., description="IP o FQDN", examples=["192.168.1.1"])
    os_version: str = Field(..., description="Versión de SO detectada", examples=["15.2(4)M11"])


# ── Command Execution ─────────────────────────────────────────


class CommandRequest(BaseModel):
    """Solicitud para ejecutar comandos CLI en dispositivos."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "commands": ["show version", "show ip interface brief"],
                "device_id": "cisco-core-01",
            }
        }
    )
    commands: list[str] = Field(
        ...,
        description="Lista de comandos CLI a ejecutar (solo lectura/diagnóstico)",
        examples=[["show version", "show ip interface brief"]],
        min_length=1,
    )
    device_id: Optional[str] = Field(
        None,
        description="ID del dispositivo (requerido para /api/command/{device_id})",
        examples=["cisco-core-01"],
    )


class CommandOutput(BaseModel):
    """Salida de un comando individual."""
    command: str = Field(..., description="Comando ejecutado", examples=["show version"])
    output: str = Field(..., description="Salida raw del comando", examples=["Cisco IOS Software..."])
    error: Optional[str] = Field(None, description="Error si el comando falló", examples=[None])


class CommandResponse(BaseModel):
    """Respuesta de ejecución de comandos CLI."""
    device_id: str = Field(..., description="ID del dispositivo", examples=["cisco-core-01"])
    results: list[CommandOutput] = Field(..., description="Resultados de cada comando ejecutado")
    success: bool = Field(..., description="¿Todos los comandos se ejecutaron correctamente?", examples=[True])
    timestamp: datetime = Field(..., description="Momento de ejecución")


class BulkCommandRequest(BaseModel):
    """Solicitud para ejecutar comandos en múltiples dispositivos."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "device_ids": ["cisco-core-01", "arista-spine-01"],
                "commands": ["show version", "show ip route"],
            }
        }
    )
    device_ids: list[str] = Field(
        ...,
        description="Lista de IDs de dispositivos donde ejecutar los comandos",
        examples=[["cisco-core-01", "arista-spine-01"]],
        min_length=1,
    )
    commands: list[str] = Field(
        ...,
        description="Lista de comandos CLI a ejecutar en todos los dispositivos",
        examples=[["show version", "show ip route"]],
        min_length=1,
    )


class BulkCommandResponse(BaseModel):
    """Respuesta de ejecución masiva de comandos."""
    total: int = Field(..., description="Total de dispositivos consultados", examples=[2])
    success_count: int = Field(..., description="Dispositivos exitosos", examples=[1])
    failed_count: int = Field(..., description="Dispositivos con error", examples=[1])
    results: list[CommandResponse] = Field(default_factory=list, description="Resultados exitosos")
    errors: list[dict] = Field(default_factory=list, description="Errores agrupados por dispositivo")


class BulkResult(BaseModel):
    """Resultado de operación masiva en múltiples dispositivos."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "total": 5,
                "success": 4,
                "failed": 1,
                "results": [
                    {"device_id": "cisco-core-01", "data": {"hostname": "core-router", "os_version": "15.2"}}
                ],
                "errors": [
                    {"device_id": "switch-access-01", "error": "Error de conexión al dispositivo: switch-access-01"}
                ],
            }
        }
    )
    total: int = Field(..., description="Total de dispositivos consultados", examples=[5])
    success: int = Field(..., description="Dispositivos con respuesta exitosa", examples=[4])
    failed: int = Field(..., description="Dispositivos con error", examples=[1])
    results: list[dict] = Field(default_factory=list, description="Resultados exitosos")
    errors: list[dict] = Field(default_factory=list, description="Errores encontrados")


# ── Auth / RBAC ──────────────────────────────────────────────

class UserRole(str, Enum):
    """Roles del sistema RBAC."""
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"


class LoginRequest(BaseModel):
    """Credenciales de inicio de sesión."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "username": "admin",
                "password": "***REMOVED***",
            }
        }
    )
    username: str = Field(
        ...,
        description="Nombre de usuario",
        examples=["admin", "operador"],
    )
    password: str = Field(
        ...,
        description="Contraseña del usuario",
        examples=["***REMOVED***"],
    )


class TokenResponse(BaseModel):
    """Respuesta con token JWT al iniciar sesión."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "access_token": "eyJhbG...NiIs...",
                "token_type": "bearer",
            }
        }
    )
    access_token: str = Field(
        ...,
        description="Token JWT de acceso",
        examples=["eyJhbG...VCJ9..."],
    )
    token_type: str = Field(
        default="bearer",
        description="Tipo de token (siempre 'bearer')",
        examples=["bearer"],
    )


class UserInfo(BaseModel):
    """Información del usuario autenticado."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "username": "admin",
                "role": "admin",
            }
        }
    )
    username: str = Field(
        ...,
        description="Nombre de usuario autenticado",
        examples=["admin"],
    )
    role: UserRole = Field(
        ...,
        description="Rol RBAC del usuario",
        examples=["admin", "viewer"],
    )


# ── Admin User Management ────────────────────────────────────


class UserCreate(BaseModel):
    """Datos para crear un nuevo usuario del sistema."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "username": "nuevo_user",
                "password": "Pass123!",
                "role": "viewer",
                "name": "Nuevo Usuario",
            }
        }
    )
    username: str = Field(
        ...,
        min_length=3,
        max_length=64,
        description="Nombre de usuario único",
        examples=["nuevo_user"],
    )
    password: str = Field(
        ...,
        min_length=6,
        description="Contraseña del usuario (se almacena hasheada)",
        examples=["Pass123!"],
    )
    role: UserRole = Field(
        default=UserRole.VIEWER,
        description="Rol RBAC del usuario",
        examples=["viewer", "operator", "admin"],
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Nombre real o descriptivo del usuario",
        examples=["Nuevo Usuario"],
    )


class UserUpdate(BaseModel):
    """Campos actualizables de un usuario (todos opcionales)."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "password": "NuevaPass456!",
                "role": "operator",
                "name": "Nombre Actualizado",
            }
        }
    )
    password: Optional[str] = Field(
        None,
        min_length=6,
        description="Nueva contraseña (opcional)",
        examples=["NuevaPass456!"],
    )
    role: Optional[UserRole] = Field(
        None,
        description="Nuevo rol RBAC (opcional)",
        examples=["operator", "admin"],
    )
    name: Optional[str] = Field(
        None,
        min_length=1,
        max_length=128,
        description="Nuevo nombre descriptivo (opcional)",
        examples=["Nombre Actualizado"],
    )


class UserResponse(BaseModel):
    """Información de un usuario SIN contraseña."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "username": "admin",
                "role": "admin",
                "name": "Admin",
            }
        }
    )
    username: str = Field(
        ...,
        description="Nombre de usuario",
        examples=["admin"],
    )
    role: UserRole = Field(
        ...,
        description="Rol RBAC del usuario",
        examples=["admin", "viewer"],
    )
    name: str = Field(
        ...,
        description="Nombre real o descriptivo",
        examples=["Admin"],
    )


# ── Scheduler Schemas ─────────────────────────────────────────


class BackupScheduleRequest(BaseModel):
    """Solicitud para programar un backup único."""
    device_id: str = Field(
        ...,
        description="ID del dispositivo a respaldar",
        examples=["cisco-core-01"],
    )
    delay_minutes: int = Field(
        default=30,
        ge=1,
        description="Minutos de espera antes del backup",
        examples=[30],
    )


class RecurringBackupRequest(BaseModel):
    """Solicitud para programar backups recurrentes."""
    device_id: str = Field(
        ...,
        description="ID del dispositivo a respaldar",
        examples=["cisco-core-01"],
    )
    interval_minutes: int = Field(
        default=60,
        ge=5,
        description="Intervalo en minutos entre backups",
        examples=[60],
    )


class ScheduledJobResponse(BaseModel):
    """Información de un trabajo programado."""
    job_id: str = Field(..., description="ID único del trabajo", examples=["job-abc123"])
    device_id: str = Field(..., description="Dispositivo objetivo", examples=["cisco-core-01"])
    job_type: str = Field(..., description="Tipo: 'backup' o 'recurring'", examples=["backup"])
    delay_minutes: Optional[int] = Field(None, description="Minutos de espera (backup único)", examples=[30])
    interval_minutes: Optional[int] = Field(None, description="Intervalo en minutos (recurrente)", examples=[60])
    created_at: str = Field(..., description="Momento de creación ISO 8601", examples=["2026-06-11T17:00:00Z"])
    status: str = Field(default="scheduled", description="Estado del trabajo", examples=["scheduled", "running", "completed", "cancelled"])


# ── Compliance ────────────────────────────────────────────────


class ComplianceCheckRequest(BaseModel):
    """Solicitud para verificar compliance de uno o más dispositivos."""
    device_id: Optional[str] = Field(None, description="ID del dispositivo individual", examples=["cisco-core-01"])
    device_ids: Optional[list[str]] = Field(None, description="Lista de IDs de dispositivos", examples=[["cisco-core-01", "switch-access-01"]])


class ComplianceViolation(BaseModel):
    """Una línea que difiere entre running-config y baseline."""
    line: int = Field(..., description="Número de línea", examples=[10])
    expected: str = Field(..., description="Valor esperado según baseline", examples=["hostname core-router"])
    got: str = Field(..., description="Valor encontrado en running-config", examples=["hostname core-router-v2"])


class ComplianceCheckResponse(BaseModel):
    """Resultado de verificación de compliance para un dispositivo."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "device_id": "cisco-core-01",
                "compliant": False,
                "violations": [{"line": 10, "expected": "hostname core-router", "got": "hostname core-router-v2"}],
                "baseline_group": "lab",
            }
        }
    )
    device_id: str = Field(..., description="ID del dispositivo", examples=["cisco-core-01"])
    compliant: bool = Field(..., description="¿Cumple con el baseline?", examples=[False])
    violations: list[ComplianceViolation] = Field(default_factory=list, description="Lista de violaciones")
    baseline_group: Optional[str] = Field(None, description="Grupo baseline usado", examples=["lab"])


class ComplianceBaselineRequest(BaseModel):
    """Solicitud para establecer un baseline de configuración."""
    group: str = Field(..., description="Nombre del grupo (tag) de dispositivos", examples=["lab"])
    config: str = Field(..., description="Configuración baseline (texto plano)", examples=["hostname core-router\ninterface Gi0/0\n description WAN\n"])


class ComplianceBaselineResponse(BaseModel):
    """Respuesta al establecer o consultar un baseline."""
    group: str = Field(..., description="Grupo de dispositivos", examples=["lab"])
    config: str = Field(..., description="Configuración baseline almacenada", examples=["hostname core-router\n..."])
    size_bytes: int = Field(..., description="Tamaño en bytes", examples=[1024])


class ComplianceReportSummary(BaseModel):
    """Resumen del reporte de compliance."""
    total: int = Field(..., description="Total de dispositivos evaluados", examples=[5])
    compliant: int = Field(..., description="Dispositivos que cumplen", examples=[3])
    non_compliant: int = Field(..., description="Dispositivos que no cumplen", examples=[1])
    errors: int = Field(..., description="Dispositivos con error", examples=[1])


class ComplianceReportResponse(BaseModel):
    """Reporte completo de compliance."""
    summary: ComplianceReportSummary = Field(..., description="Resumen del reporte")
    devices: dict[str, ComplianceCheckResponse] = Field(..., description="Resultados por dispositivo")


# ── Webhooks / Notifications ──────────────────────────────────


class WebhookRegisterRequest(BaseModel):
    """Solicitud para registrar un webhook."""
    url: str = Field(..., description="URL del webhook (POST JSON)", examples=["https://hooks.example.com/netpulse"])
    events: list[str] = Field(
        ...,
        description="Eventos a los que suscribirse",
        examples=[["device_down", "device_up", "backup_complete", "compliance_violation"]],
    )


class WebhookResponse(BaseModel):
    """Datos de un webhook registrado."""
    id: str = Field(..., description="ID único del webhook", examples=["abc123-def456"])
    url: str = Field(..., description="URL del webhook", examples=["https://hooks.example.com/netpulse"])
    events: list[str] = Field(..., description="Eventos suscritos", examples=[["device_down"]])
    created_at: str = Field(..., description="Fecha de creación ISO8601", examples=["2025-06-11T18:00:00"])
    active: bool = Field(..., description="¿Está activo?", examples=[True])


# ── Bulk Config Deploy ────────────────────────────────────────


class BulkDeployRequest(BaseModel):
    """Solicitud para desplegar configuración en múltiples dispositivos."""
    device_ids: list[str] = Field(
        ...,
        description="Lista de IDs de dispositivos donde desplegar",
        examples=[["cisco-core-01", "switch-access-01"]],
        min_length=1,
    )
    candidate_config: str = Field(
        ...,
        description="Configuración a desplegar en todos los dispositivos",
        examples=["hostname nuevo-hostname\ninterface Gi0/1\ndescription Nueva descripcion\n"],
    )
    dry_run: bool = Field(
        default=False,
        description="Si es True, solo muestra diff sin aplicar cambios",
        examples=[True],
    )


class BulkDeployDeviceResult(BaseModel):
    """Resultado individual de deploy masivo."""
    device_id: str = Field(..., description="ID del dispositivo", examples=["cisco-core-01"])
    success: bool = Field(..., description="¿Fue exitoso?", examples=[True])
    diff: str = Field(default="", description="Diff de cambios")
    committed: bool = Field(default=False, description="¿Se aplicaron los cambios?", examples=[True])
    error: Optional[str] = Field(None, description="Error si ocurrió")


class BulkDeployResponse(BaseModel):
    """Respuesta de deploy masivo de configuración."""
    total: int = Field(..., description="Total de dispositivos", examples=[2])
    success: int = Field(..., description="Exitosos", examples=[2])
    failed: int = Field(..., description="Fallidos", examples=[0])
    results: list[BulkDeployDeviceResult] = Field(default_factory=list, description="Resultados individuales")


# ── Health ──────────────────────────────────────────────────

class HealthResponse(BaseModel):
    """Estado de salud del servidor API."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "healthy",
                "version": "1.0.0",
                "devices_configured": 5,
            }
        }
    )
    status: str = Field(
        default="healthy",
        description="Estado del servicio",
        examples=["healthy", "degraded"],
    )
    version: str = Field(
        default="1.0.0",
        description="Versión de la API",
        examples=["1.0.0"],
    )
    devices_configured: int = Field(
        ...,
        description="Cantidad de dispositivos en inventario",
        examples=[5],
    )
