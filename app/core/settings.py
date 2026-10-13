"""NetPulse — Core Settings."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = BASE_DIR / "config"
BACKUP_DIR = BASE_DIR / "backups"
BACKUP_DIR.mkdir(exist_ok=True)


def _load_dotenv() -> None:
    """Carga .env si existe, sin sobrescribir variables ya definidas.

    Así NETPULSE_LAB_PASSWORD, NETBOX_TOKEN, etc. se aplican al arrancar
    la API sin necesidad de exportarlas manualmente.
    """
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


_load_dotenv()


DEVICES_FILE = CONFIG_DIR / "devices.yaml"

# ── Deception / Honeypot ─────────────────────────────────────
# Red señuelo con dispositivos simulados. Deshabilitada por defecto:
# no se abre ningún puerto si NETPULSE_DECEPTION_ENABLED no es "true".
DECEPTION_ENABLED = os.getenv("NETPULSE_DECEPTION_ENABLED", "false").lower() == "true"
DECEPTION_DIR = CONFIG_DIR / "deception"
DECEPTION_NETWORK_FILE = Path(
    os.getenv("NETPULSE_DECEPTION_NETWORK_FILE", str(DECEPTION_DIR / "network.yaml"))
)
DECEPTION_DATA_DIR = Path(
    os.getenv("NETPULSE_DECEPTION_DATA_DIR", str(BASE_DIR / "deception"))
)
DECEPTION_DATA_DIR.mkdir(exist_ok=True)
DECEPTION_DB = DECEPTION_DATA_DIR / "deception.db"
# Claves persistentes del motor (host keys SSH estables entre reinicios).
DECEPTION_KEYS_DIR = DECEPTION_DIR / "keys"
DECEPTION_SSH_HOST_KEY = Path(
    os.getenv("NETPULSE_DECEPTION_SSH_HOST_KEY",
              str(DECEPTION_KEYS_DIR / "ssh_host_rsa_key"))
)
# Interfaz del bridge de deception (nunca la de gestión); usada por el
# motor en modo paquete. Solo se acepta si NO coincide con la de gestión.
DECEPTION_IFACE = os.getenv("NETPULSE_DECEPTION_IFACE", "deception0")
# Token compartido con el data plane (Go/engine) para el endpoint de ingest.
# Si está vacío, /api/deception/ingest exige JWT de administrador.
DECEPTION_INGEST_TOKEN = os.getenv("NETPULSE_DECEPTION_INGEST_TOKEN", "")

# Host de bind del motor nativo. Vacío = usar la IP de cada dispositivo
# señuelo (comportamiento normal en la red aislada). En dev se puede
# forzar a "127.0.0.1" para pruebas locales.
DECEPTION_BIND_HOST = os.getenv("NETPULSE_DECEPTION_BIND_HOST", "")

# Logs de honeypots reales (Fase C). El tailer los lee y normaliza.
DECEPTION_COWRIE_LOG = os.getenv(
    "NETPULSE_DECEPTION_COWRIE_LOG",
    str(DECEPTION_DATA_DIR / "logs" / "cowrie" / "cowrie.json"),
)
DECEPTION_COWRIE_DEVICE = os.getenv("NETPULSE_DECEPTION_COWRIE_DEVICE", "srv-files-01")
DECEPTION_OPENCANARY_LOG = os.getenv(
    "NETPULSE_DECEPTION_OPENCANARY_LOG",
    str(DECEPTION_DATA_DIR / "logs" / "opencanary" / "opencanary.log"),
)
DECEPTION_OPENCANARY_DEVICE = os.getenv(
    "NETPULSE_DECEPTION_OPENCANARY_DEVICE", "sw-access-01"
)

# Alertas de deception: se disparan ante eventos de alto valor.
DECEPTION_ALERTS_ENABLED = os.getenv(
    "NETPULSE_DECEPTION_ALERTS_ENABLED", "true"
).lower() == "true"
# Segundos de silencio por (dispositivo, atacante, regla) para no saturar.
DECEPTION_ALERT_COOLDOWN = int(os.getenv("NETPULSE_DECEPTION_ALERT_COOLDOWN", "60"))
# Umbral de intentos de login desde una misma IP para alertar fuerza bruta.
DECEPTION_BRUTE_FORCE_THRESHOLD = int(
    os.getenv("NETPULSE_DECEPTION_BRUTE_FORCE_THRESHOLD", "5")
)
DECEPTION_BRUTE_FORCE_WINDOW = int(
    os.getenv("NETPULSE_DECEPTION_BRUTE_FORCE_WINDOW", "60")
)
# Bloqueo automático de la IP atacante ante alertas de severidad critical.
DECEPTION_AUTOBLOCK_CRITICAL = os.getenv(
    "NETPULSE_DECEPTION_AUTOBLOCK_CRITICAL", "false"
).lower() == "true"

# ── Reducción de falsos positivos ────────────────────────────
# Ignorar eventos marcados como simulados (botón "Simular ataque").
DECEPTION_ALERT_IGNORE_SIMULATED = os.getenv(
    "NETPULSE_DECEPTION_ALERT_IGNORE_SIMULATED", "true"
).lower() == "true"
# Ignorar origen loopback (127.0.0.0/8, ::1): pruebas internas, no ataques.
DECEPTION_ALERT_IGNORE_LOOPBACK = os.getenv(
    "NETPULSE_DECEPTION_ALERT_IGNORE_LOOPBACK", "true"
).lower() == "true"
# Ignorar rangos privados (RFC1918) como origen: red interna de confianza.
DECEPTION_ALERT_IGNORE_PRIVATE = os.getenv(
    "NETPULSE_DECEPTION_ALERT_IGNORE_PRIVATE", "false"
).lower() == "true"

# ── GeoIP / Mapa global ──────────────────────────────────────
# Base de datos MaxMind (.mmdb). Si está vacío se autodetecta la que
# incluye el paquete `maxminddb-geolite2` (offline, sin servicios).
GEOIP_DB = os.getenv("NETPULSE_GEOIP_DB", "")
# Ubicación del centro de datos / red señuelo (punto verde del mapa).
DECEPTION_DATACENTER_LAT = float(os.getenv("NETPULSE_DATACENTER_LAT", "0"))
DECEPTION_DATACENTER_LON = float(os.getenv("NETPULSE_DATACENTER_LON", "0"))
DECEPTION_DATACENTER_NAME = os.getenv("NETPULSE_DATACENTER_NAME", "Data Center")

# ── Firewall ─────────────────────────────────────────────────
# Aplica reglas de bloqueo reales (requiere privilegios). Deshabilitado
# por defecto: solo devuelve los comandos a ejecutar (dry-run).
FIREWALL_ENABLED = os.getenv("NETPULSE_FIREWALL_ENABLED", "false").lower() == "true"

# ── Capa de decisiones IA (Laya — alternativa libre y multilingüe a Jev) ──
# Motor "System One": recibe un `state` + preguntas tipadas (noul/choice/
# score) y devuelve respuestas con probabilidad y confianza. Se consume vía
# HTTP contra `laya-serve` (docker-compose.ai.yml). Deshabilitado por
# defecto: si está off, todo cae de vuelta a la lógica por reglas (fail-open).
AI_ENABLED = os.getenv("NETPULSE_AI_ENABLED", "false").lower() == "true"
AI_BACKEND = os.getenv("NETPULSE_AI_BACKEND", "laya").lower()
AI_BASE_URL = os.getenv("NETPULSE_AI_BASE_URL", "http://localhost:8090").rstrip("/")
AI_MODEL = os.getenv("NETPULSE_AI_MODEL", "multilingual")
# Solo se actúa sobre una decisión si su confianza alcanza este umbral.
AI_CONFIDENCE_FLOOR = float(os.getenv("NETPULSE_AI_CONFIDENCE_FLOOR", "0.90"))
AI_TIMEOUT = float(os.getenv("NETPULSE_AI_TIMEOUT", "8"))
# Caché de decisiones idénticas (state+questions) para no repetir llamadas.
AI_CACHE_TTL = int(os.getenv("NETPULSE_AI_CACHE_TTL", "300"))
# Contrato estricto de respuesta tipo Jev (descarta campos extra).
AI_JEV_STRICT = os.getenv("NETPULSE_AI_JEV_STRICT", "true").lower() == "true"

# ── Falsos positivos de deception con IA (Fase 2) ─────────────
# Enriquecer/decidir alertas de deception con el modelo.
DECEPTION_AI_FP_ENABLED = os.getenv(
    "NETPULSE_DECEPTION_AI_FP_ENABLED", "true"
).lower() == "true"
# Permitir que la IA silencie (no notifique) una alerta cuando la clasifica
# como benigna con confianza >= DECEPTION_AI_FP_MIN_CONFIDENCE.
DECEPTION_AI_FP_SUPPRESS = os.getenv(
    "NETPULSE_DECEPTION_AI_FP_SUPPRESS", "true"
).lower() == "true"
DECEPTION_AI_FP_MIN_CONFIDENCE = float(
    os.getenv("NETPULSE_DECEPTION_AI_FP_MIN_CONFIDENCE", str(AI_CONFIDENCE_FLOOR))
)

# ── Alertmanager webhook (auth de servicio) ──────────────────
# Token compartido para que Alertmanager (que no envía JWT) autentique
# POST /api/alerts/webhook. Vacío = exigir JWT admin (comportamiento previo).
ALERTMANAGER_TOKEN = os.getenv("NETPULSE_ALERTMANAGER_TOKEN", "")

# ── Backup offsite (S3/MinIO o SFTP) ─────────────────────────
OFFSITE_ENABLED = os.getenv("NETPULSE_OFFSITE_ENABLED", "false").lower() == "true"
OFFSITE_BACKEND = os.getenv("NETPULSE_OFFSITE_BACKEND", "sftp").lower()  # sftp|s3
# SFTP
OFFSITE_SFTP_HOST = os.getenv("NETPULSE_OFFSITE_SFTP_HOST", "")
OFFSITE_SFTP_PORT = int(os.getenv("NETPULSE_OFFSITE_SFTP_PORT", "22"))
OFFSITE_SFTP_USER = os.getenv("NETPULSE_OFFSITE_SFTP_USER", "")
OFFSITE_SFTP_PASSWORD = os.getenv("NETPULSE_OFFSITE_SFTP_PASSWORD", "")
OFFSITE_SFTP_KEY = os.getenv("NETPULSE_OFFSITE_SFTP_KEY", "")  # ruta a clave privada
OFFSITE_SFTP_DIR = os.getenv("NETPULSE_OFFSITE_SFTP_DIR", "netpulse-backups")
# S3 / MinIO
OFFSITE_S3_ENDPOINT = os.getenv("NETPULSE_OFFSITE_S3_ENDPOINT", "")  # MinIO: http://host:9000
OFFSITE_S3_BUCKET = os.getenv("NETPULSE_OFFSITE_S3_BUCKET", "")
OFFSITE_S3_REGION = os.getenv("NETPULSE_OFFSITE_S3_REGION", "us-east-1")
OFFSITE_S3_ACCESS_KEY = os.getenv("NETPULSE_OFFSITE_S3_ACCESS_KEY", "")
OFFSITE_S3_SECRET_KEY = os.getenv("NETPULSE_OFFSITE_S3_SECRET_KEY", "")
OFFSITE_S3_PREFIX = os.getenv("NETPULSE_OFFSITE_S3_PREFIX", "netpulse")

# ── Firewalls (FortiOS) ──────────────────────────────────────
FORTIOS_VERIFY = os.getenv("NETPULSE_FORTIOS_VERIFY", "false").lower() == "true"
FORTIOS_TIMEOUT = float(os.getenv("NETPULSE_FORTIOS_TIMEOUT", "10"))




API_TITLE = "NetPulse API"
API_VERSION = "1.0.0"
API_HOST = os.getenv("NETPULSE_HOST", "0.0.0.0")
API_PORT = int(os.getenv("NETPULSE_PORT", "8082"))

NAPALM_TIMEOUT = int(os.getenv("NAPALM_TIMEOUT", "60"))

# ── TLS / HTTPS ──────────────────────────────────────────────

# Para HTTPS: NETPULSE_SSL_CERTFILE y NETPULSE_SSL_KEYFILE apuntando
# a los archivos del certificado. Si no se definen, la API corre HTTP.
SSL_CERTFILE = os.getenv("NETPULSE_SSL_CERTFILE", "")
SSL_KEYFILE = os.getenv("NETPULSE_SSL_KEYFILE", "")

# ── NetBox Integration ──────────────────────────────────────

NETBOX_URL = os.getenv("NETBOX_URL", "http://localhost:8000").rstrip("/")


def _load_netbox_token() -> str:
    """Carga el token de NetBox desde variable de entorno o archivo."""
    token = os.getenv("NETBOX_TOKEN", "")
    if token:
        return token
    token_file = CONFIG_DIR / ".netbox_token"
    if token_file.exists():
        return token_file.read_text(encoding="utf-8").strip()
    return ""


NETBOX_TOKEN = _load_netbox_token()

# ── Loki / Syslog ────────────────────────────────────────────

LOKI_URL = os.getenv("NETPULSE_LOKI_URL", "http://localhost:3100").rstrip("/")
SYSLOG_HOST = os.getenv("NETPULSE_SYSLOG_HOST", "0.0.0.0")
SYSLOG_PORT = int(os.getenv("NETPULSE_SYSLOG_PORT", "514"))
SYSLOG_ENABLED = os.getenv("NETPULSE_SYSLOG_ENABLED", "true").lower() == "true"

# ── SNMP ─────────────────────────────────────────────────────

SNMP_COMMUNITY = os.getenv("NETPULSE_SNMP_COMMUNITY", "public")
SNMP_TIMEOUT = int(os.getenv("NETPULSE_SNMP_TIMEOUT", "5"))

# ── Métricas / Poller ────────────────────────────────────────

# Poller en segundo plano que recolecta recursos de dispositivos + host.
# Intervalo en segundos; 0 deshabilita el poller automático.
METRICS_ENABLED = os.getenv("NETPULSE_METRICS_ENABLED", "true").lower() == "true"
METRICS_INTERVAL = int(os.getenv("NETPULSE_METRICS_INTERVAL", "60"))

# ── Notificaciones (Telegram / Email / Slack) ───────────────

TELEGRAM_BOT_TOKEN = os.getenv("NETPULSE_TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("NETPULSE_TELEGRAM_CHAT_ID", "")

SLACK_WEBHOOK_URL = os.getenv("NETPULSE_SLACK_WEBHOOK_URL", "")
SLACK_CHANNEL = os.getenv("NETPULSE_SLACK_CHANNEL", "")

SMTP_HOST = os.getenv("NETPULSE_SMTP_HOST", "")
SMTP_PORT = int(os.getenv("NETPULSE_SMTP_PORT", "25"))
SMTP_USERNAME = os.getenv("NETPULSE_SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("NETPULSE_SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("NETPULSE_SMTP_FROM", "netpulse@localhost")
SMTP_TO = os.getenv("NETPULSE_SMTP_TO", "")

# ── Reportes ─────────────────────────────────────────────────

REPORT_DIR = BASE_DIR / "reports"
REPORT_DIR.mkdir(exist_ok=True)
