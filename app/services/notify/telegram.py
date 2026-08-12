"""NetPulse — Notificaciones por Telegram.

Envía mensajes de texto a chats/grupos de Telegram usando la Bot API
(https://api.telegram.org/bot<token>/sendMessage) vía urllib.request.
"""

import json
import logging
import urllib.request

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
TIMEOUT = 10  # segundos


def send_telegram(bot_token: str, chat_id: str, message: str) -> bool:
    """Envía un mensaje de texto a un chat de Telegram.

    Args:
        bot_token: Token del bot (lo entrega @BotFather al crear el bot).
        chat_id: ID del chat, grupo o canal destino (puede ser negativo
            para grupos, ej. "-1001234567890").
        message: Texto a enviar (máx. 4096 caracteres en Telegram).

    Returns:
        True si Telegram respondió con status 200 OK; False en caso
        contrario (error de red, token inválido, etc.).
    """
    if not bot_token or not chat_id or not message:
        logger.warning("send_telegram: faltan argumentos (token, chat_id, message)")
        return False

    url = f"{API_BASE}/bot{bot_token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": message}).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            ok = resp.status == 200
            if ok:
                logger.info("Mensaje Telegram enviado a chat %s", chat_id)
            else:
                logger.warning("Telegram respondió status %d", resp.status)
            return ok
    except Exception as exc:
        logger.warning("Error enviando mensaje Telegram: %s", exc)
        return False
