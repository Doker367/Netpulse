"""Parser de eventos de Cowrie (SSH/Telnet medium-interaction).

Cowrie emite un JSON por línea con un campo ``eventid``. Este módulo
traduce los eventos relevantes al contrato de deception de NetPulse.
"""

from typing import Optional

# eventid de Cowrie → tipo de evento NetPulse
_EVENT_MAP = {
    "cowrie.session.connect": "connect",
    "cowrie.login.failed": "login_attempt",
    "cowrie.login.success": "login_success",
    "cowrie.command.input": "command",
    "cowrie.session.file_download": "file_download",
}


def parse(raw: dict, default_device: str = "") -> Optional[dict]:
    """Traduce un evento Cowrie a kwargs de ``telemetry_svc.emit``.

    Args:
        raw: Diccionario JSON de una línea de ``cowrie.json``.
        default_device: device_id a usar (Cowrie no conoce nuestros ids).

    Returns:
        Dict de kwargs, o None si el evento no es relevante.
    """
    eventid = raw.get("eventid", "")
    etype = _EVENT_MAP.get(eventid)
    if etype is None:
        return None

    detail: dict = {"sensor": "cowrie", "eventid": eventid}
    if raw.get("input"):
        detail["command"] = raw["input"]
    if raw.get("password"):
        detail["password"] = raw["password"]
    if raw.get("url"):
        detail["url"] = raw["url"]
    if raw.get("session"):
        detail["session"] = raw["session"]

    return {
        "device_id": default_device or raw.get("sensor") or "cowrie",
        "src_ip": raw.get("src_ip", "0.0.0.0"),
        "src_port": raw.get("src_port"),
        "proto": "ssh",
        "event_type": etype,
        "detail": detail,
        "username": raw.get("username"),
        "success": eventid == "cowrie.login.success",
        "ts": raw.get("timestamp"),
    }
