"""Tests de redacción de tokens en logs (hallazgo 7E → tarjeta 7F)."""

import io
import logging

import pytest

from app.core.logging_redaction import (
    REDACTED,
    install_log_redaction,
    redact_tokens,
)

SECRET = "eyJhbGciOiJIUzI1NiJ9.FIRMA.SECRETO"


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.stream = io.StringIO()

    def emit(self, record):
        self.stream.write(self.format(record) + "\n")


@pytest.fixture(autouse=True)
def _installed():
    install_log_redaction()
    yield


def _capture_on(logger: logging.Logger) -> tuple[Capture, int]:
    handler = Capture()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    old_level = logger.level
    logger.setLevel(logging.INFO)
    return handler, old_level


def test_redact_tokens_conserva_otros_query_params():
    url = f"ws://127.0.0.1:8082/ws/terminal/dev1?token={SECRET}&fmt=json"
    redacted = redact_tokens(url)
    assert SECRET not in redacted
    assert "token=***" in redacted
    assert "fmt=json" in redacted
    assert redacted.startswith("ws://127.0.0.1:8082/ws/terminal/dev1?")


def test_redact_tokens_case_insensitive_y_sin_token():
    assert "TOKEN=***&x=1" in redact_tokens(f"TOKEN={SECRET}&x=1")
    assert redact_tokens("sin secretos") == "sin secretos"


def test_uvicorn_error_no_filtra_token(caplog):
    logger = logging.getLogger("uvicorn.error")
    handler, old_level = _capture_on(logger)
    try:
        logger.info(
            '%s - "WebSocket %s" [accepted]',
            "127.0.0.1:5555",
            f"/ws/terminal/dev1?token={SECRET}&fmt=json",
        )
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)

    output = handler.stream.getvalue()
    assert SECRET not in output
    assert "token=***" in output
    assert "WebSocket /ws/terminal/dev1?token=" in output


def test_uvicorn_access_no_filtra_token():
    logger = logging.getLogger("uvicorn.access")
    handler, old_level = _capture_on(logger)
    try:
        logger.info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1:5555",
            "GET",
            f"/ws/terminal/dev1?token={SECRET}&fmt=json",
            "1.1",
            101,
        )
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)

    output = handler.stream.getvalue()
    assert SECRET not in output
    assert "token=***" in output
    assert '101' in output


def test_root_propagado_no_filtra_token():
    """Un handler del root creado después de instalar también está cubierto."""
    root = logging.getLogger()
    handler, old_level = _capture_on(root)
    try:
        logging.getLogger("app.test.redaction").info(
            "abriendo ws con %s", f"/ws/terminal/dev1?token={SECRET}&fmt=json"
        )
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)

    output = handler.stream.getvalue()
    assert SECRET not in output
    assert f"token={REDACTED}" in output


def test_placeholder_de_formato_no_se_rompe():
    """``token=%s`` debe sobrevivir: se redacta el argumento, no el formato."""
    logger = logging.getLogger("app.test.redaction_format")
    handler, old_level = _capture_on(logger)
    try:
        logger.info(
            "abriendo ws con token=%s", f"/ws/terminal/dev1?token={SECRET}"
        )
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)

    output = handler.stream.getvalue()
    assert SECRET not in output
    assert "token=***" in output
