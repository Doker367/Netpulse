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


def netbox_auth_header(token: str | None = None) -> dict[str, str]:
    """Cabecera ``Authorization`` correcta para la API de NetBox.

    NetBox 4.x maneja dos formatos de token incompatibles entre sí:

    - Token legado v1: ``Authorization: Token <40 caracteres>``
    - Token v2 actual: ``Authorization: Bearer nbt_<key>.<token>``

    El segundo es el que genera NetBox al crear un token con peppers
    (``NETBOX_API_TOKEN_PEPPER_1``). Se elige por el prefijo ``nbt_``, de forma
    que ambos siguen funcionando sin reconfigurar nada.
    """
    raw = (NETBOX_TOKEN if token is None else token).strip()
    if not raw:
        return {}
    if raw.startswith("nbt_"):
        return {"Authorization": f"Bearer {raw}"}
    return {"Authorization": f"Token {raw}"}

# ── Loki / Syslog ────────────────────────────────────────────

LOKI_URL = os.getenv("NETPULSE_LOKI_URL", "http://localhost:3100").rstrip("/")
SYSLOG_HOST = os.getenv("NETPULSE_SYSLOG_HOST", "0.0.0.0")
SYSLOG_PORT = int(os.getenv("NETPULSE_SYSLOG_PORT", "514"))
SYSLOG_ENABLED = os.getenv("NETPULSE_SYSLOG_ENABLED", "false").lower() == "true"

# ── SNMP ─────────────────────────────────────────────────────

SNMP_COMMUNITY = os.getenv("NETPULSE_SNMP_COMMUNITY", "public")
SNMP_TIMEOUT = int(os.getenv("NETPULSE_SNMP_TIMEOUT", "5"))

# ── Métricas / Poller ────────────────────────────────────────

# Poller en segundo plano que recolecta recursos de dispositivos + host.
# Intervalo en segundos; 0 deshabilita el poller automático.
METRICS_ENABLED = os.getenv("NETPULSE_METRICS_ENABLED", "false").lower() == "true"
METRICS_INTERVAL = int(os.getenv("NETPULSE_METRICS_INTERVAL", "60"))

# ── Watchdog de sites remotos ────────────────────────────────
# Defaults de arranque; el watchdog relee el entorno en cada ciclo para que
# los cambios de NETPULSE_SITE_WATCHDOG_* no exijan reiniciar la API.
# Ver app/services/remote/watchdog_svc.py y docs/handoffs/collectors-site.md.
SITE_WATCHDOG_ENABLED_DEFAULT = (
    os.getenv("NETPULSE_SITE_WATCHDOG_ENABLED", "true").lower() == "true"
)


def _int_env(name: str, default: int) -> int:
    """Lee un entero de entorno sin romper el arranque si está malformado."""
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


SITE_WATCHDOG_INTERVAL_S_DEFAULT = _int_env("NETPULSE_SITE_WATCHDOG_INTERVAL_S", 60)
SITE_STALE_AFTER_S_DEFAULT = _int_env("NETPULSE_SITE_STALE_AFTER_S", 300)

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
