"""NetPulse — Prometheus Metrics Middleware (raw ASGI).

ASGI middleware that records request count and duration for every HTTP request.
Skips /api/metrics, /docs, /redoc, /openapi.json paths.

Must be added as the first (outermost) middleware to capture all requests
before any filtering/auth middleware runs.
"""

import time

from app.services.metrics_svc import (
    netpulse_request_duration_seconds,
    netpulse_requests_total,
)

SKIP_PREFIXES = (
    "/api/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
)


class MetricsMiddleware:
    """Raw ASGI middleware that records Prometheus metrics for every HTTP request."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # Only intercept HTTP requests
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "/")
        method = scope.get("method", "GET")

        # Skip metrics/docs endpoints to avoid polluting metrics
        for prefix in SKIP_PREFIXES:
            if path.startswith(prefix):
                return await self.app(scope, receive, send)

        start = time.monotonic()
        status_code = 500

        # ── Wrap send() to capture the response status ─────────
        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            status_code = 500
            raise
        finally:
            duration = time.monotonic() - start

            # Record metrics
            netpulse_requests_total.labels(
                method=method,
                path=path,
                status=str(status_code),
            ).inc()

            netpulse_request_duration_seconds.labels(
                method=method,
                path=path,
            ).observe(duration)
