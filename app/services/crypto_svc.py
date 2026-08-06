"""NetPulse — Credential Encryption Service.

Uses Fernet (symmetric AES-128-CBC) for encrypting device passwords
at rest in config/devices.yaml. The Fernet key is stored in
config/.fernet_key and NEVER committed to version control.
"""

import base64
import logging
import os
from pathlib import Path

import yaml
from cryptography.fernet import Fernet, InvalidToken

from app.core.settings import CONFIG_DIR, DEVICES_FILE

logger = logging.getLogger(__name__)

KEY_FILE = CONFIG_DIR / ".fernet_key"

# ── Key Management ───────────────────────────────────────────


def _get_or_create_key() -> bytes:
    """Load Fernet key from disk or generate a new one."""
    if KEY_FILE.exists():
        return KEY_FILE.read_bytes().strip()

    logger.info("Generating new Fernet key → %s", KEY_FILE)
    key = Fernet.generate_key()
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_bytes(key + b"\n")
    # Restrict permissions (owner-only read/write)
    os.chmod(KEY_FILE, 0o600)
    return key


def _get_fernet() -> Fernet:
    """Return a ready-to-use Fernet instance."""
    return Fernet(_get_or_create_key())


# ── Public API ───────────────────────────────────────────────


def encrypt(text: str) -> str:
    """Encrypt a plaintext string, returning a base64 token."""
    if not text:
        # Never encrypt empty strings — treat as empty
        return ""
    return _get_fernet().encrypt(text.encode("utf-8")).decode("ascii")


def decrypt(encrypted: str) -> str:
    """Decrypt a Fernet token back to plaintext.

    Returns the token as-is if it looks like plaintext (backward
    compatibility with devices that haven't been encrypted yet).
    """
    if not encrypted:
        return ""
    try:
        return _get_fernet().decrypt(encrypted.encode("ascii")).decode("utf-8")
    except InvalidToken:
        # Already plaintext or invalid — return as-is
        logger.debug("Token is not a valid Fernet ciphertext, returning as-is")
        return encrypted
    except Exception as exc:
        logger.error("Decryption error: %s", exc)
        return encrypted


def encrypt_device_passwords() -> int:
    """Read devices.yaml and encrypt all credential passwords in-place.

    Returns the number of passwords encrypted.
    """
    if not DEVICES_FILE.exists():
        logger.warning("devices.yaml not found at %s", DEVICES_FILE)
        return 0

    with open(DEVICES_FILE) as fh:
        data = yaml.safe_load(fh)

    devices = data.get("devices", [])
    count = 0

    for dev in devices:
        creds = dev.get("credentials", {})
        password = creds.get("password", "")
        if password and not _looks_encrypted(password):
            creds["password"] = encrypt(password)
            count += 1
            logger.debug("Encrypted password for %s", dev.get("id", "?"))

    if count > 0:
        with open(DEVICES_FILE, "w") as fh:
            yaml.dump(data, fh, default_flow_style=False, allow_unicode=True)
        logger.info("Encrypted %d device password(s) in %s", count, DEVICES_FILE)

    return count


def decrypt_device_passwords() -> dict:
    """Read devices.yaml, decrypt all credential passwords, and return
    the full config dict with decrypted passwords ready for NAPALM use.

    The decrypted dict is NOT written back to disk — it is ephemeral
    and should be used only for the duration of a NAPALM session.
    """
    if not DEVICES_FILE.exists():
        return {"devices": []}

    with open(DEVICES_FILE) as fh:
        data = yaml.safe_load(fh)

    devices = data.get("devices", [])
    for dev in devices:
        creds = dev.get("credentials", {})
        password = creds.get("password", "")
        if password:
            creds["password"] = decrypt(password)

    return data


def _looks_encrypted(value: str) -> bool:
    """Heuristic: Fernet tokens start with 'gAAAAA' in base64."""
    return value.startswith("gAAAAA")
