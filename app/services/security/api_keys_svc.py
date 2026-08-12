"""NetPulse — API Keys Service.

Emisión y gestión de API keys persistidas en ``config/api_keys.json``.

Solo se almacena el hash SHA-256 de cada key, nunca la key en texto plano.
La key completa se devuelve una única vez en ``create_api_key()``.
"""

import hashlib
import json
import secrets
import threading
import uuid
from datetime import datetime, timezone

from app.core.settings import CONFIG_DIR

API_KEYS_FILE = CONFIG_DIR / "api_keys.json"

_write_lock = threading.Lock()

VALID_ROLES = ("admin", "operator", "viewer")


def _read() -> list[dict]:
    """Lee la lista de API keys desde el archivo JSON."""
    if not API_KEYS_FILE.exists():
        return []
    try:
        with open(API_KEYS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _write(keys: list[dict]) -> None:
    """Persiste la lista de API keys en el archivo JSON."""
    API_KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(API_KEYS_FILE, "w", encoding="utf-8") as f:
        json.dump(keys, f, indent=2, ensure_ascii=False)


def _hash_key(key: str) -> str:
    """Calcula el hash SHA-256 de una API key."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _public(record: dict) -> dict:
    """Devuelve el registro sin el hash interno de la key."""
    return {k: v for k, v in record.items() if k != "key_hash"}


def create_api_key(name: str, role: str = "viewer") -> dict:
    """Crea una nueva API key.

    Args:
        name: Nombre descriptivo de la key (p. ej. "CI pipeline").
        role: Rol asociado: admin, operator o viewer (default viewer).

    Returns:
        dict: Contiene la key completa (``key``, solo visible ahora),
        junto con id, name, role y created_at.
    """
    if role not in VALID_ROLES:
        raise ValueError(f"Rol inválido: {role}. Válidos: {', '.join(VALID_ROLES)}")

    raw_key = f"np_{secrets.token_hex(24)}"
    record = {
        "id": str(uuid.uuid4()),
        "name": name,
        "role": role,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "key_hash": _hash_key(raw_key),
        "revoked": False,
    }

    with _write_lock:
        keys = _read()
        keys.append(record)
        _write(keys)

    return {
        "key": raw_key,  # única vez que se expone en texto plano
        "id": record["id"],
        "name": record["name"],
        "role": record["role"],
        "created_at": record["created_at"],
    }


def list_api_keys() -> list[dict]:
    """Lista todas las API keys (sin las keys en texto plano ni sus hashes)."""
    with _write_lock:
        keys = _read()
    return [_public(k) for k in keys]


def revoke_api_key(key_id: str) -> bool:
    """Revoca una API key por su id (borrado lógico).

    Args:
        key_id: ID de la key a revocar.

    Returns:
        bool: True si se encontró y revocó; False si no existe.
    """
    with _write_lock:
        keys = _read()
        for record in keys:
            if record.get("id") == key_id:
                record["revoked"] = True
                record["revoked_at"] = datetime.now(timezone.utc).isoformat()
                _write(keys)
                return True
    return False


def validate_api_key(key: str) -> dict | None:
    """Valida una API key y devuelve sus datos si es activa.

    Hashea la key recibida y la busca en el almacén.

    Args:
        key: API key en texto plano (``np_...``).

    Returns:
        dict | None: Datos públicos de la key si existe y no está
        revocada; None en caso contrario.
    """
    if not key:
        return None
    digest = _hash_key(key)

    with _write_lock:
        keys = _read()

    for record in keys:
        if record.get("key_hash") == digest and not record.get("revoked", False):
            return _public(record)
    return None
