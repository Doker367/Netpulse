"""NetPulse — Mensajes de error en español.

Traducción de errores de NAPALM y del sistema a español
para que la API devuelva mensajes amigables al usuario.
"""

from typing import Optional

# ── Categorías de error ─────────────────────────────────────

# Mapeo de tipos de error a mensajes en español
_ERROR_MESSAGES: dict[str, str] = {
    "connection_error": "Error de conexión al dispositivo",
    "auth_error": "Credenciales inválidas",
    "timeout": "Tiempo de espera agotado",
    "command_error": "Comando no soportado por el dispositivo",
    "driver_error": "Dispositivo no encontrado o driver no soportado",
    "unknown": "Error desconocido al comunicarse con el dispositivo",
}


def map_napalm_error(error_type: Optional[str], device_id: str) -> str:
    """Traduce un tipo de error NAPALM a un mensaje en español.

    Args:
        error_type: Categoría del error (connection_error, auth_error, ...)
        device_id: ID del dispositivo para contextualizar el mensaje

    Returns:
        Mensaje de error en español incluyendo el device_id
    """
    base = _ERROR_MESSAGES.get(error_type or "unknown", _ERROR_MESSAGES["unknown"])
    return f"{base}: {device_id}"


# Constantes de mensajes de error reutilizables
DEVICE_NOT_FOUND = "Dispositivo no encontrado"
UNAUTHORIZED = "No autorizado — token requerido"
FORBIDDEN = "Acceso denegado — rol insuficiente"
INVALID_TOKEN = "Token inválido o expirado"
INVALID_CREDENTIALS = "Credenciales inválidas"
CONFLICT = "El dispositivo ya existe"
INTERNAL_ERROR = "Error interno del servidor"
