"""NetPulse — Notificaciones por correo electrónico (SMTP).

Dos variantes de envío:

- ``send_email_smtp``: SMTP con STARTTLS (típico de proveedores como
  Gmail, Outlook, etc. en el puerto 587).
- ``send_email_plain``: SMTP sin TLS, pensado para servidores locales
  (ej. Postfix en localhost:25 dentro del mismo entorno).
"""

import logging
import smtplib
from email.message import EmailMessage
from typing import Union

logger = logging.getLogger(__name__)

TIMEOUT = 10  # segundos

# Acepta un destinatario suelto o una lista
Addresses = Union[str, list[str], tuple[str, ...]]


def _to_list(to_addrs: Addresses) -> list[str]:
    """Normaliza destinatarios a lista de strings."""
    if isinstance(to_addrs, str):
        return [to_addrs]
    return list(to_addrs)


def _build_message(from_addr: str, to_addrs: Addresses, subject: str, body: str) -> EmailMessage:
    """Construye un EmailMessage de texto plano."""
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = ", ".join(_to_list(to_addrs))
    msg["Subject"] = subject
    msg.set_content(body)
    return msg


def send_email_smtp(
    smtp_host: str,
    smtp_port: int,
    username: str,
    password: str,
    from_addr: str,
    to_addrs: Addresses,
    subject: str,
    body: str,
) -> bool:
    """Envía un correo vía SMTP con STARTTLS.

    Args:
        smtp_host: Host del servidor SMTP (ej. "smtp.gmail.com").
        smtp_port: Puerto (típicamente 587 para STARTTLS).
        username: Usuario de autenticación (si es vacío, no se hace login).
        password: Password de autenticación.
        from_addr: Dirección remitente.
        to_addrs: Destinatario(s) — string o lista de strings.
        subject: Asunto del correo.
        body: Cuerpo del mensaje (texto plano).

    Returns:
        True si el servidor aceptó el correo; False en caso de error.
    """
    msg = _build_message(from_addr, to_addrs, subject, body)
    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=TIMEOUT) as server:
            server.starttls()
            if username:
                server.login(username, password)
            server.sendmail(from_addr, _to_list(to_addrs), msg.as_string())
        logger.info(
            "Correo enviado vía %s:%d a %s", smtp_host, smtp_port, to_addrs
        )
        return True
    except Exception as exc:
        logger.warning(
            "Error enviando correo vía %s:%d: %s", smtp_host, smtp_port, exc
        )
        return False


def send_email_plain(
    smtp_host: str,
    smtp_port: int,
    from_addr: str,
    to_addrs: Addresses,
    subject: str,
    body: str,
    username: str = "",
    password: str = "",
) -> bool:
    """Envía un correo sin TLS (para SMTP local, ej. localhost:25).

    Args:
        smtp_host: Host del servidor SMTP local.
        smtp_port: Puerto (típicamente 25).
        from_addr: Dirección remitente.
        to_addrs: Destinatario(s) — string o lista de strings.
        subject: Asunto del correo.
        body: Cuerpo del mensaje (texto plano).
        username: Opcional — usuario si el servidor local exige auth.
        password: Opcional — password si el servidor local exige auth.

    Returns:
        True si el servidor aceptó el correo; False en caso de error.
    """
    msg = _build_message(from_addr, to_addrs, subject, body)
    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=TIMEOUT) as server:
            if username:
                server.login(username, password)
            server.sendmail(from_addr, _to_list(to_addrs), msg.as_string())
        logger.info(
            "Correo enviado (sin TLS) vía %s:%d a %s", smtp_host, smtp_port, to_addrs
        )
        return True
    except Exception as exc:
        logger.warning(
            "Error enviando correo (sin TLS) vía %s:%d: %s", smtp_host, smtp_port, exc
        )
        return False
