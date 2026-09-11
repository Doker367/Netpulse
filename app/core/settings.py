"""NetPulse — Core Settings."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = BASE_DIR / "config"
BACKUP_DIR = BASE_DIR / "backups"
BACKUP_DIR.mkdir(exist_ok=True)

DEVICES_FILE = CONFIG_DIR / "devices.yaml"

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
