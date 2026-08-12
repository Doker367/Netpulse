"""NetPulse — TOTP 2FA Service.

Generación y verificación de códigos TOTP (RFC 6238) para autenticación
de dos factores.

- ``generate_totp_secret()``: genera un secreto base32 aleatorio (20 bytes).
- ``generate_qr_data_uri()``: produce un data URI con la URI ``otpauth://``
  lista para codificar en un QR (sin generar imagen real).
- ``verify_totp()``: valida un código de 6 dígitos contra el secreto.

Si ``pyotp`` está instalado se usa para la verificación; si no, se usa una
implementación mínima RFC 6238 (HMAC-SHA1, ventana de 30 s, 6 dígitos).
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
import urllib.parse

# Parámetros TOTP por defecto (RFC 6238)
TOTP_STEP = 30          # ventana de tiempo en segundos
TOTP_DIGITS = 6         # dígitos del código
TOTP_ALGORITHM = hashlib.sha1


def generate_totp_secret() -> str:
    """Genera un secreto TOTP aleatorio en base32.

    Returns:
        str: 20 bytes aleatorios codificados en base32 (sin padding).
    """
    raw = secrets.token_bytes(20)  # 160 bits de entropía
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def generate_qr_data_uri(username: str, secret: str, issuer: str = "NetPulse") -> str:
    """Genera un data URI con la URI ``otpauth://`` para el QR.

    No genera una imagen real: el data URI transporta el texto
    ``otpauth://totp/NetPulse:<user>?secret=...&issuer=NetPulse`` que
    cualquier lector QR / app de autenticación puede interpretar.

    Args:
        username: Nombre de usuario al que pertenece el secreto.
        secret: Secreto TOTP en base32.
        issuer: Emisor mostrado en la app de autenticación (default NetPulse).

    Returns:
        str: Data URI (``data:text/plain;charset=utf-8,...``).
    """
    label = f"{issuer}:{username}"
    otpauth_uri = (
        f"otpauth://totp/{urllib.parse.quote(label, safe=':')}"
        f"?secret={urllib.parse.quote(secret, safe='')}"
        f"&issuer={urllib.parse.quote(issuer, safe='')}"
    )
    # Data URI con el texto otpauth:// URL-encoded
    return "data:text/plain;charset=utf-8," + urllib.parse.quote(otpauth_uri, safe="")


# ── Implementación mínima RFC 6238 (fallback sin pyotp) ──────


def _base32_decode(secret: str) -> bytes:
    """Decodifica un secreto base32 (tolera falta de padding)."""
    secret = secret.strip().upper().replace(" ", "")
    padding = "=" * ((8 - len(secret) % 8) % 8)
    return base64.b32decode(secret + padding)


def _hotp(key: bytes, counter: int, digits: int = TOTP_DIGITS) -> str:
    """Genera un código HOTP (RFC 4226) para un contador dado."""
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, TOTP_ALGORITHM).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10**digits)).zfill(digits)


def _totp_at(secret: str, timestamp: int | None = None) -> str:
    """Calcula el código TOTP para un timestamp Unix dado (default: ahora)."""
    if timestamp is None:
        timestamp = int(time.time())
    counter = timestamp // TOTP_STEP
    return _hotp(_base32_decode(secret), counter)


def verify_totp(secret: str, code: str) -> bool:
    """Verifica un código TOTP contra el secreto.

    Usa ``pyotp`` si está instalado; en caso contrario cae a la
    implementación mínima RFC 6238. Se tolera ±1 paso (30 s) de
    desviación de reloj.

    Args:
        secret: Secreto TOTP en base32.
        code: Código de 6 dígitos a verificar.

    Returns:
        bool: True si el código es válido.
    """
    code = (code or "").strip()
    if not code:
        return False

    try:
        from pyotp import TOTP  # type: ignore

        return TOTP(secret).verify(code, valid_window=1)
    except ImportError:
        pass  # pyotp no instalado → fallback manual

    now = int(time.time())
    for delta_step in (0, -1, 1):
        if _totp_at(secret, now + delta_step * TOTP_STEP) == code:
            return True
    return False
