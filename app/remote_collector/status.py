"""Estado local del colector (``status.json``) sin secretos."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from app.remote_collector.timeutil import parse_iso, utcnow


def status_path(buffer_path: str | Path) -> Path:
    return Path(buffer_path).parent / "status.json"


def write_status(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, target)


def read_status(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("status.json no contiene un objeto")
    return data


def is_stale(payload: dict[str, Any], interval_s: int, now: datetime | None = None) -> bool:
    updated = payload.get("updated_at")
    if not isinstance(updated, str):
        return True
    try:
        age = ((now or utcnow()) - parse_iso(updated)).total_seconds()
    except (ValueError, TypeError):
        return True
    return age > (2 * max(1, interval_s) + 30)
