"""Redacción de secretos en logs — tokens en query strings.

El WebSocket de terminal autentica con ``?token=<JWT>`` (ver
``app/routers/terminal.py``). Uvicorn registra la URL al aceptar el upgrade
(``uvicorn.error``), incluso con ``--no-access-log``, así que el JWT quedaría
en claro en los logs. Este módulo instala una redacción mínima:

    token=<cualquier cosa>  ->  token=***

Se aplica a todos los ``LogRecord`` (factory global), a los loggers de
uvicorn y a los handlers del root existentes; es idempotente y no toca el
resto del formato/mensaje. No sustituye a no loguear query strings en el
proxy: ver ``docs/handoffs/terminal.md`` §10.
"""

from __future__ import annotations

import logging
import re

REDACTED = "***"

# ``token=`` seguido del valor, sin tragarse ``&`` (otro query param) ni
# espacios/comillas (fin de la URL en un request line). Se aceptan escapes
# percent-encoded (``%2E``) pero NO placeholders de formato (``token=%s``),
# para no romper ``record.msg % record.args``. Case-insensitive por si algún
# proxy normaliza el nombre del parámetro.
_TOKEN_RE = re.compile(r"(?i)\btoken=(?:[^&\s\"'%]|%[0-9A-Fa-f]{2})+")

_FACTORY_FLAG = "_netpulse_token_redaction_factory"
_TARGET_LOGGERS = ("uvicorn", "uvicorn.access", "uvicorn.error", "app")


def redact_tokens(text: str) -> str:
    """Devuelve ``text`` con los valores de ``token=`` sustituidos por ***."""
    if not text:
        return text

    def _replace(match: re.Match) -> str:
        name = match.group(0).split("=", 1)[0]
        return f"{name}={REDACTED}"

    return _TOKEN_RE.sub(_replace, text)


def _redact_args(value):
    """Redacta strings dentro de ``record.args`` (tuple, dict o escalar)."""
    if isinstance(value, str):
        return redact_tokens(value)
    if isinstance(value, tuple):
        return tuple(_redact_args(v) for v in value)
    if isinstance(value, list):
        return [_redact_args(v) for v in value]
    if isinstance(value, dict):
        return {k: _redact_args(v) for k, v in value.items()}
    return value


def _redact_record(record: logging.LogRecord) -> logging.LogRecord:
    """Redacta ``msg`` y ``args`` de un registro (in place)."""
    if isinstance(record.msg, str):
        record.msg = redact_tokens(record.msg)
    if record.args:
        record.args = _redact_args(record.args)
    return record


class RedactTokensFilter(logging.Filter):
    """Filtro que redacta tokens y deja pasar siempre el registro."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        _redact_record(record)
        return True


def _install_record_factory() -> None:
    """Encadena la factory global para cubrir handlers creados después."""
    if getattr(logging, _FACTORY_FLAG, False):
        return

    previous = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        return _redact_record(previous(*args, **kwargs))

    logging.setLogRecordFactory(factory)
    setattr(logging, _FACTORY_FLAG, True)


def install_log_redaction() -> None:
    """Instala la redacción de tokens; seguro de llamar varias veces."""
    _install_record_factory()

    flt = RedactTokensFilter()
    targets = [logging.getLogger()]  # root (registros emitidos por root)
    targets += [logging.getLogger(name) for name in _TARGET_LOGGERS]
    for log in targets:
        if not any(isinstance(f, RedactTokensFilter) for f in log.filters):
            log.addFilter(flt)
    # Handlers del root ya existentes (p. ej. basicConfig de uvicorn o del
    # despliegue): sus filtros sí se aplican a los registros propagados.
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, RedactTokensFilter) for f in handler.filters):
            handler.addFilter(flt)
