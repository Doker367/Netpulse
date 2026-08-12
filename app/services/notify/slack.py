"""NetPulse — Canal de notificación Slack (Incoming Webhook).

Envía mensajes a un canal de Slack vía webhook URL.
"""

import logging
import urllib.request

logger = logging.getLogger(__name__)

# Timeout corto para no bloquear el flujo de notificaciones
TIMEOUT = 10  # segundos


def send_slack(webhook_url: str, message: str, channel: str = "") -> bool:
    """Envía un mensaje de texto a Slack vía Incoming Webhook.

    Args:
        webhook_url: URL del webhook de Slack
            (https://hooks.slack.com/services/...).
        message: Texto del mensaje.
        channel: Canal opcional (ej. "#netops"); si se omite, usa el
            canal configurado en el webhook.

    Returns:
        True si Slack respondió 200, False en caso contrario.
        Nunca lanza excepciones (fallo elegante).
    """
    payload: dict = {"text": message}
    if channel:
        payload["channel"] = channel

    body = __import__("json").dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            ok = resp.status == 200
            logger.debug("Slack webhook: %s (status=%d)", "OK" if ok else "falló", resp.status)
            return ok
    except Exception as e:
        logger.warning("Error enviando a Slack: %s", e)
        return False


def send_slack_attachment(webhook_url: str, title: str, text: str, color: str = "danger") -> bool:
    """Envía un mensaje con attachment (formato de alerta).

    Args:
        webhook_url: URL del webhook de Slack.
        title: Título del attachment.
        text: Cuerpo del mensaje.
        color: Color del borde (good, warning, danger).

    Returns:
        True si Slack respondió 200.
    """
    payload = {
        "attachments": [
            {
                "color": color,
                "title": title,
                "text": text,
            }
        ]
    }

    body = __import__("json").dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            ok = resp.status == 200
            logger.debug("Slack attachment: %s (status=%d)", "OK" if ok else "falló", resp.status)
            return ok
    except Exception as e:
        logger.warning("Error enviando attachment a Slack: %s", e)
        return False
