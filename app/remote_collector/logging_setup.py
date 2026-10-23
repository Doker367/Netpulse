"""Configuración de logging del colector (stdout → journald/systemd)."""

from __future__ import annotations

import logging
import sys


def setup_logging(level: str = "INFO") -> None:
    numeric = getattr(logging, str(level).upper(), logging.INFO)
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=numeric,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
            stream=sys.stdout,
        )
    else:
        root.setLevel(numeric)
