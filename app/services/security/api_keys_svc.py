"""NetPulse — API Keys Service.

Emisión y gestión de API keys persistidas en ``config/api_keys.json``.

Solo se almacena el hash SHA-256 de cada key, nunca la key en texto plano.
La key completa se devuelve una única vez en ``create_api_key()``.
"""

import hashlib
import hmac
import json
import secrets
import threading
import uuid
from datetime import datetime, timezone

from app.core.settings import CONFIG_DIR

API_KEYS_FILE = CONFIG_DIR / "api_keys.json"

_write_lock = threading.Lock()

VALID_ROLES = ("admin", "operator", "viewer")
VALID_KINDS = ("user", "collector")


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
    """Devuelve el registro sin el hash interno de la key.

    Los registros antiguos no tienen ``kind``: se interpretan como ``user``
    para no romper las claves ya emitidas.
    """
    public = {k: v for k, v in record.items() if k != "key_hash"}
    public.setdefault("kind", "user")
    public.setdefault("site_id", None)
    public.setdefault("collector_id", None)
    return public


def create_api_key(
    name: str,
    role: str = "viewer",
    *,
    kind: str = "user",
    site_id: str | None = None,
    collector_id: str | None = None,
) -> dict:
    """Crea una API key de usuario o de colector.

    Las claves de colector no tienen rol RBAC: solo pueden ingerir en
    ``/api/remote/ingest`` y consultar ``/api/remote/whoami``. Las de usuario
    mantienen el contrato anterior.
    """
    if kind not in VALID_KINDS:
        raise ValueError(f"Kind inválido: {kind}. Válidos: {', '.join(VALID_KINDS)}")
    if kind == "collector":
        site_id = (site_id or "").strip()
        collector_id = (collector_id or "").strip()
        if not site_id or not collector_id:
            raise ValueError("Una clave de colector exige site_id y collector_id")
        role = None
    else:
        if role not in VALID_ROLES:
            raise ValueError(f"Rol inválido: {role}. Válidos: {', '.join(VALID_ROLES)}")
        if site_id is not None or collector_id is not None:
            raise ValueError("site_id/collector_id solo se admiten en kind='collector'")

    raw_key = f"np_{secrets.token_hex(24)}"
    record = {
        "id": str(uuid.uuid4()),
        "name": name,
        "kind": kind,
        "role": role,
        "site_id": site_id,
        "collector_id": collector_id,
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
        "kind": record["kind"],
        "role": record["role"],
        "site_id": record["site_id"],
        "collector_id": record["collector_id"],
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
        stored = record.get("key_hash", "")
        if hmac.compare_digest(str(stored), digest) and not record.get("revoked", False):
            return _public(record)
    return None
