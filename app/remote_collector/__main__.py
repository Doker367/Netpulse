"""Entrypoint: ``python -m app.remote_collector``."""

from __future__ import annotations

import sys

from app.remote_collector.cli import main

if __name__ == "__main__":
    sys.exit(main())
