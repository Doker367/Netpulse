"""NetPulse — Seguridad: JWT + Hashing de contraseñas.

Provee:
- create_access_token: genera un token JWT con claims y expiración.
- verify_token: valida y decodifica un token JWT.
- hash_password / verify_password: hashing bcrypt directo (sin passlib,
  que está abandonado). Compatible con hashes $2a$/$2b$ ya existentes.
"""

import os
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

# ── Configuración ─────────────────────────────────────────────

# Clave secreta para firmar JWTs.
# En producción DEBE definirse NETPULSE_JWT_SECRET como variable de entorno.
JWT_SECRET = os.getenv("NETPULSE_JWT_SECRET", "netpulse-dev-secret-change-me")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE = int(os.getenv("NETPULSE_TOKEN_EXPIRE", "30"))  # minutos

# Si estamos en modo producción (NETPULSE_ENV=production) y el secret es
# el default, fallar en lugar de arriesgar tokens predecibles.
_IS_PRODUCTION = os.getenv("NETPULSE_ENV", "development").lower() == "production"
if _IS_PRODUCTION and JWT_SECRET == "netpulse-dev-secret-change-me":
    raise RuntimeError(
        "NETPULSE_ENV=production requiere NETPULSE_JWT_SECRET configurado. "
        "Genera uno con: python -c \"import secrets; print(secrets.token_hex(32))\""
    )

# bcrypt solo usa los primeros 72 bytes; imitar el truncado histórico de passlib
_BCRYPT_MAX_BYTES = 72


def _bcrypt_bytes(password: str) -> bytes:
    data = password.encode("utf-8")
    if len(data) > _BCRYPT_MAX_BYTES:
        data = data[:_BCRYPT_MAX_BYTES]
    return data


# ── Funciones de token JWT ────────────────────────────────────

def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """Genera un token JWT con los datos y expiración indicados.

    Args:
        data: Diccionario con claims a incluir (ej. {"sub": username, "role": ...}).
        expires_delta: Tiempo de expiración opcional; por defecto 30 minutos.

    Returns:
        Token JWT como string.
    """
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> dict | None:
    """Decodifica y valida un token JWT.

    Args:
        token: Token JWT en formato string.

    Returns:
        Diccionario con los claims del token, o None si es inválido/expirado.
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except JWTError:
        return None


# ── Hashing de contraseñas (bcrypt directo) ───────────────────

def hash_password(password: str) -> str:
    """Hashea una contraseña con bcrypt.

    Args:
        password: Contraseña en texto plano.

    Returns:
        Hash bcrypt ($2b$) de la contraseña.
    """
    if not password:
        return ""
    return bcrypt.hashpw(_bcrypt_bytes(password), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(plain_password: str, hashed_password: str | None) -> bool:
    """Verifica una contraseña contra su hash bcrypt.

    Args:
        plain_password: Contraseña en texto plano.
        hashed_password: Hash bcrypt almacenado.

    Returns:
        True si coinciden, False en caso contrario.
    """
    if not plain_password or not hashed_password:
        return False
    try:
        return bcrypt.checkpw(_bcrypt_bytes(plain_password), hashed_password.encode("ascii"))
    except ValueError:
        # Hash no válido (no es bcrypt) — nunca dar una pista al usuario
        return False
