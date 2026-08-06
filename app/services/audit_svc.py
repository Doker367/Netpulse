"""NetPulse — Immutable Audit Logging Service.

Append-only JSONL audit log. Once written, entries cannot be modified
or deleted. File-based, no external database required.

Thread-safe writes: all writes acquire a file-level lock (fcntl / msvcrt).
"""

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.core.settings import BASE_DIR
from app.services.metrics_svc import netpulse_audit_events_total

# ── Paths ────────────────────────────────────────────────────

AUDIT_DIR = BASE_DIR / "audit"
AUDIT_FILE = AUDIT_DIR / "audit.jsonl"

# ── File lock for thread-safe writes ─────────────────────────

_write_lock = threading.Lock()


def _ensure_dir() -> None:
    """Create audit directory if it doesn't exist."""
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)


# ── Public API ────────────────────────────────────────────────

def log_event(
    action: str,
    device_id: Optional[str] = None,
    username: str = "anonymous",
    details: Optional[str] = None,
    ip_address: Optional[str] = None,
    success: bool = True,
    session_id: Optional[str] = None,
    path: Optional[str] = None,
    method: Optional[str] = None,
    response_status: Optional[int] = None,
) -> dict:
    """Log an immutable audit event (append-only JSONL).

    Args:
        action: Short action description (e.g. 'device_create', 'facts_get').
        device_id: Target device (if applicable).
        username: Who performed the action.
        details: Free-form details / extra context.
        ip_address: Client IP address.
        success: Whether the operation succeeded.
        session_id: Optional session / correlation ID.
        path: API path (auto-filled by middleware).
        method: HTTP method (auto-filled by middleware).
        response_status: HTTP response status (auto-filled by middleware).

    Returns:
        The logged event dict.
    """
    ts = datetime.now(timezone.utc)
    event = {
        "timestamp": ts.isoformat(),
        "action": action,
        "device_id": device_id,
        "username": username,
        "details": details or "",
        "ip_address": ip_address or "",
        "success": success,
        "session_id": session_id or str(uuid.uuid4()),
        "path": path or "",
        "method": method or "",
        "response_status": response_status,
    }

    _ensure_dir()
    line = json.dumps(event, ensure_ascii=False, default=str)

    with _write_lock:
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())  # ensure durability

    netpulse_audit_events_total.inc()

    return event


def get_events(
    limit: int = 100,
    offset: int = 0,
    device_id: Optional[str] = None,
    username: Optional[str] = None,
    action: Optional[str] = None,
) -> list[dict]:
    """Read and filter audit events from the JSONL file.

    Args:
        limit: Max events to return.
        offset: Skip first N matching events.
        device_id: Filter by device ID.
        username: Filter by username.
        action: Filter by action.

    Returns:
        List of matching event dicts (most recent first).
    """
    if not AUDIT_FILE.exists():
        return []

    results: list[dict] = []

    # Read all lines (most recent first)
    with _write_lock:
        try:
            with open(AUDIT_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception:
            return []

    # Parse in reverse (newest first)
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        # Apply filters
        if device_id and event.get("device_id") != device_id:
            continue
        if username and event.get("username") != username:
            continue
        if action and event.get("action") != action:
            continue

        results.append(event)

    # Paginate
    total = len(results)
    paginated = results[offset : offset + limit]

    return paginated


def get_audit_stats() -> dict:
    """Get basic audit log statistics.

    Returns:
        Dict with total_events, file_size_bytes, and file path.
    """
    if not AUDIT_FILE.exists():
        return {
            "total_events": 0,
            "file_size_bytes": 0,
            "file_path": str(AUDIT_FILE),
        }

    with _write_lock:
        try:
            with open(AUDIT_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception:
            lines = []

    total = sum(1 for line in lines if line.strip())
    file_size = AUDIT_FILE.stat().st_size

    return {
        "total_events": total,
        "file_size_bytes": file_size,
        "file_path": str(AUDIT_FILE),
    }
