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
