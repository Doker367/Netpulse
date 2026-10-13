"""NetPulse — Allowlist de origen confiable para deception.

IPs o rangos (CIDR) cuyo tráfico **no** debe generar alertas ni contar
como amenaza (p. ej. la red de gestión, escáneres de inventario propios).
Reduce falsos positivos. Persistida en ``deception/allowlist.json``.
"""

import ipaddress
import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from typing import Optional, Union

from app.core.settings import DECEPTION_DATA_DIR

logger = logging.getLogger(__name__)

ALLOWLIST_FILE = DECEPTION_DATA_DIR / "allowlist.json"
_lock = threading.Lock()


def _read() -> list[dict]:
    if not ALLOWLIST_FILE.exists():
        return []
    try:
        return json.loads(ALLOWLIST_FILE.read_text(encoding="utf-8")).get("allowed", [])
    except (json.JSONDecodeError, OSError):
        return []


def _write(entries: list[dict]) -> None:
    ALLOWLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(ALLOWLIST_FILE.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"allowed": entries}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, ALLOWLIST_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def list_allowed() -> list[dict]:
    """Lista las entradas de la allowlist."""
    with _lock:
        return list(_read())


def _as_network(value: str) -> Optional[Union[ipaddress.IPv4Network, ipaddress.IPv6Network]]:
    try:
        return ipaddress.ip_network(value, strict=False)
    except ValueError:
        return None


def is_allowed(ip: str) -> bool:
    """True si la IP cae dentro de alguna entrada de la allowlist."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    with _lock:
        for entry in _read():
            net = _as_network(entry.get("cidr", ""))
            if net and addr in net:
                return True
    return False


def add(cidr: str, note: str = "") -> dict:
    """Agrega una IP/CIDR a la allowlist."""
    cidr = (cidr or "").strip()
    if _as_network(cidr) is None:
        raise ValueError(f"CIDR/IP inválido: {cidr}")
    with _lock:
        entries = _read()
        for entry in entries:
            if entry["cidr"] == cidr:
                entry["note"] = note or entry.get("note", "")
                _write(entries)
                return entry
        entry = {"cidr": cidr, "note": note,
                 "added_at": datetime.now(timezone.utc).isoformat()}
        entries.append(entry)
        _write(entries)
        logger.info("[deception] allowlist + %s", cidr)
        return entry


def remove(cidr: str) -> bool:
    """Quita una entrada de la allowlist."""
    with _lock:
        entries = _read()
        filtered = [e for e in entries if e["cidr"] != cidr]
        if len(filtered) == len(entries):
            return False
        _write(filtered)
        return True
