"""NetPulse — Self Backup Service.

Crea un backup en ``backups/netpulse_self_<timestamp>.tar.gz`` con la
configuración del propio NetPulse: devices.yaml, users.yaml, webhooks.json,
scheduler_jobs.json y api_keys.json (solo los que existan).

Los archivos de secretos (``.fernet_key``, ``.netbox_token``) quedan
**excluidos** del backup a propósito.
"""

import tarfile
from datetime import datetime, timezone

from app.core.settings import BACKUP_DIR, CONFIG_DIR

# Whitelist de archivos a incluir (nunca secretos tipo .fernet_key/.netbox_token)
BACKUP_FILES = [
    "devices.yaml",
    "users.yaml",
    "webhooks.json",
    "scheduler_jobs.json",
    "api_keys.json",
]


def create_backup() -> dict:
    """Empaqueta la configuración de NetPulse en un tar.gz.

    Returns:
        dict: ``{path, size, created_at}`` con la ruta absoluta del
        backup, su tamaño en bytes y la fecha de creación (UTC ISO 8601).
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"netpulse_self_{timestamp}.tar.gz"
    # Evitar colisión si se crean dos backups en el mismo segundo
    counter = 2
    while backup_path.exists():
        backup_path = BACKUP_DIR / f"netpulse_self_{timestamp}_{counter}.tar.gz"
        counter += 1

    with tarfile.open(backup_path, "w:gz") as tar:
        for filename in BACKUP_FILES:
            source = CONFIG_DIR / filename
            if source.exists():
                tar.add(source, arcname=f"config/{filename}")

    return {
        "path": str(backup_path),
        "size": backup_path.stat().st_size,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
