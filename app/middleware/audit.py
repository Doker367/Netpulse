"""NetPulse — Audit Middleware (raw ASGI).

Auto-logs every API request (method, path, query params, status code, client IP).
Uses raw ASGI middleware instead of BaseHTTPMiddleware for maximum reliability
with streaming responses, background tasks, and other middleware interactions.

Skips /api/health and /docs paths.
"""

import uuid

SKIP_PREFIXES = (
    "/api/health",
    "/docs",
    "/redoc",
    "/openapi.json",
)


class AuditMiddleware:
    """Raw ASGI middleware that logs every HTTP request as an immutable audit event.

    Added as the outermost middleware so it captures ALL incoming requests
    and outgoing responses regardless of what other middleware does.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # Only intercept HTTP requests (skip WebSocket, lifespan, etc.)
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        # ── Parse request from ASGI scope ──────────────────────
        path = scope.get("path", "/")
        method = scope.get("method", "GET")
        query_string = scope.get("query_string", b"").decode("utf-8", errors="replace")

        # Skip health / docs endpoints
        for prefix in SKIP_PREFIXES:
            if path.startswith(prefix):
                return await self.app(scope, receive, send)

        session_id = str(uuid.uuid4())
        status_code = 500

        # ── Wrap send() to capture the response status ─────────
        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
            success = 200 <= status_code < 400
        except Exception:
            status_code = 500
            success = False
            raise
        finally:
            # ── Extract client IP ───────────────────────────
            client_ip = _get_client_ip(scope)

            details = f"{method} {path}"
            if query_string:
                details += f"?{query_string}"

            # Escribir fuera del event loop (SQLite en executor, sin bloquear)
            _log_async(
                action=f"api_{method.lower()}",
                username="anonymous",
                details=details,
                ip_address=client_ip,
                success=success,
                session_id=session_id,
                path=path,
                method=method,
                response_status=status_code,
            )


def _log_async(**kwargs) -> None:
    """Delega log_event a un executor (fire-and-forget, sin bloquear)."""
    try:
        import asyncio
        from functools import partial

        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, partial(_safe_log, **kwargs))
    except Exception:
        pass


def _safe_log(**kwargs) -> None:
    try:
        from app.services.audit_svc import log_event
        log_event(**kwargs)
    except Exception:
        pass


def _get_client_ip(scope: dict) -> str:
    """Extract client IP from ASGI scope, respecting proxy headers."""
    # Parse headers from scope (they are tuples of (b"name", b"value"))
    headers = {}
    for key, value in scope.get("headers", []):
        headers[key.decode("latin-1").lower()] = value.decode("latin-1")

    # X-Forwarded-For (behind reverse proxy)
    forwarded = headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()

    # X-Real-IP
    real_ip = headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()

    # Direct client from scope
    client = scope.get("client")
    if client:
        return client[0]

    return "unknown"
