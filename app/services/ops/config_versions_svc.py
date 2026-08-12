"""NetPulse — Historial de Versiones de Configuración.

Indexa los backups en backups/*.cfg (formato <device>_<YYYYMMDD_HHMMSS>.cfg),
permite leer una versión individual y comparar dos versiones
con unified diff (difflib).
"""

import difflib
import hashlib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.core.settings import BACKUP_DIR

logger = logging.getLogger(__name__)

_BACKUP_RE = re.compile(r"^(?P<device>.+?)_(?P<ts>\d{8}_\d{6})\.cfg$")


def _sha256(path: Path) -> str:
    """Hash sha256 del contenido de un archivo (lectura por chunks)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_path(filename: str) -> Path:
    """Devuelve un Path dentro de BACKUP_DIR (evita path traversal)."""
    return BACKUP_DIR / Path(filename).name


def index_backups() -> list[dict]:
    """Escanea backups/*.cfg y devuelve la lista ordenada (nuevos primero).

    Cada entrada: {device_id, filename, timestamp, size, sha256}.
    El timestamp se parsea del nombre <device>_<YYYYMMDD_HHMMSS>.cfg.
    Archivos que no matchean el patrón se incluyen al final (timestamp None).
    """
    entries = []
    for path in sorted(BACKUP_DIR.glob("*.cfg")):
        match = _BACKUP_RE.match(path.name)
        if match:
            ts = match.group("ts")
            timestamp = (
                datetime.strptime(ts, "%Y%m%d_%H%M%S")
                .replace(tzinfo=timezone.utc)
                .isoformat()
            )
            device_id = match.group("device")
        else:
            timestamp = None
            device_id = None
        entries.append({
            "device_id": device_id,
            "filename": path.name,
            "timestamp": timestamp,
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        })
    # Nuevos primero; los sin timestamp (None) al final
    entries.sort(key=lambda e: (e["timestamp"] is None, e["timestamp"] or ""), reverse=True)
    return entries


def get_version(device_id: str, filename: str) -> Optional[dict]:
    """Lee una versión de backup y devuelve su contenido + metadata.

    Devuelve None si el archivo no existe o no pertenece al dispositivo.
    """
    path = _safe_path(filename)
    if not path.exists() or not path.is_file():
        return None
    match = _BACKUP_RE.match(path.name)
    if match and match.group("device") != device_id:
        return None
    return {
        "device_id": device_id,
        "filename": path.name,
        "content": path.read_text(encoding="utf-8"),
        "size": path.stat().st_size,
        "sha256": _sha256(path),
    }


def diff_versions(device_id: str, file_a: str, file_b: str) -> Optional[str]:
    """Devuelve el unified diff entre dos versiones de backup.

    Devuelve None si alguna de las versiones no existe.
    """
    va = get_version(device_id, file_a)
    vb = get_version(device_id, file_b)
    if va is None or vb is None:
        return None
    diff = difflib.unified_diff(
        va["content"].splitlines(keepends=True),
        vb["content"].splitlines(keepends=True),
        fromfile=file_a,
        tofile=file_b,
    )
    return "".join(diff)
