"""NetPulse — Seguridad: JWT + Hashing de contraseñas.

Provee:
- create_access_token: genera un token JWT con claims y expiración.
- verify_token: valida y decodifica un token JWT.
- hash_password / verify_password: hashing bcrypt via passlib.
"""

import os
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

# ── Configuración ─────────────────────────────────────────────

# Clave secreta para firmar JWTs (¡cambiar en producción!)
JWT_SECRET = os.getenv("NETPULSE_JWT_SECRET", "netpulse-dev-secret-change-me")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE = 30  # minutos

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


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


# ── Hashing de contraseñas ────────────────────────────────────

def hash_password(password: str) -> str:
    """Hashea una contraseña con bcrypt.

    Args:
        password: Contraseña en texto plano.

    Returns:
        Hash bcrypt de la contraseña.
    """
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifica una contraseña contra su hash bcrypt.

    Args:
        plain_password: Contraseña en texto plano.
        hashed_password: Hash bcrypt almacenado.

    Returns:
        True si coinciden, False en caso contrario.
    """
    return pwd_context.verify(plain_password, hashed_password)
