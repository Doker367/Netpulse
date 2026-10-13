"""NetPulse — Blocklist de atacantes de deception.

Lista de IPs bloqueadas observadas en la red señuelo. Persistida en
``deception/blocklist.json``. Sirve para que el data plane (o el firewall
perimetral) aplique el bloqueo; NetPulse mantiene el registro y lo expone
en la API/dashboard.
"""

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from typing import Optional

from app.core.settings import DECEPTION_DATA_DIR

logger = logging.getLogger(__name__)

BLOCKLIST_FILE = DECEPTION_DATA_DIR / "blocklist.json"
_lock = threading.Lock()


def _read() -> list[dict]:
    if not BLOCKLIST_FILE.exists():
        return []
    try:
        data = json.loads(BLOCKLIST_FILE.read_text(encoding="utf-8"))
        return data.get("blocked", [])
    except (json.JSONDecodeError, OSError):
        return []


def _write(entries: list[dict]) -> None:
    BLOCKLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(BLOCKLIST_FILE.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"blocked": entries}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, BLOCKLIST_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def list_blocked() -> list[dict]:
    """Lista las IPs bloqueadas."""
    with _lock:
        return list(reversed(_read()))


def is_blocked(ip: str) -> bool:
    """True si la IP está en la blocklist."""
    with _lock:
        return any(e["ip"] == ip for e in _read())


def add(ip: str, reason: str = "") -> dict:
    """Agrega (o actualiza) una IP en la blocklist."""
    with _lock:
        entries = _read()
        for entry in entries:
            if entry["ip"] == ip:
                entry["reason"] = reason or entry.get("reason", "")
                entry["hits"] = entry.get("hits", 1) + 1
                entry["last_blocked"] = datetime.now(timezone.utc).isoformat()
                _write(entries)
                return entry
        entry = {
            "ip": ip,
            "reason": reason,
            "hits": 1,
            "blocked_at": datetime.now(timezone.utc).isoformat(),
        }
        entries.append(entry)
        _write(entries)
        logger.warning("[deception] IP bloqueada: %s (%s)", ip, reason or "manual")
        return entry


def remove(ip: str) -> bool:
    """Quita una IP de la blocklist."""
    with _lock:
        entries = _read()
        filtered = [e for e in entries if e["ip"] != ip]
        if len(filtered) == len(entries):
            return False
        _write(filtered)
        return True


def auto_block(alert: dict) -> Optional[dict]:
    """Bloquea la IP de una alerta (usado por el motor de alertas)."""
    ip = alert.get("src_ip")
    if not ip:
        return None
    return add(ip, reason=f"auto:{alert.get('rule', 'alert')}")
