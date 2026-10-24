"""NetPulse — Security Middleware.

Adds security headers and removes Server header from responses.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Injects security headers into every response and strips Server."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response: Response = await call_next(request)

        headers = response.headers

        # Prevent MIME-type sniffing
        headers.setdefault("X-Content-Type-Options", "nosniff")

        # Prevent clickjacking
        headers.setdefault("X-Frame-Options", "DENY")

        # XSS filter
        headers.setdefault("X-XSS-Protection", "1; mode=block")

        # HSTS (1 year, include subdomains)
        headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )

        # Referrer policy
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")

        # Permissions policy
        headers.setdefault("Permissions-Policy", "geolocation=(), camera=(), microphone=()")

        # Content-Security-Policy (allow inline styles/scripts for dashboard)
        if request.url.path.startswith("/api/"):
            headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' https://fonts.gstatic.com https://fonts.googleapis.com; connect-src 'self'",
            )
        else:
            headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' 'unsafe-inline' https://fonts.googleapis.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; img-src 'self' data:; font-src 'self' https://fonts.gstatic.com https://fonts.googleapis.com; connect-src 'self'; frame-src 'self' http://localhost:3000 http://127.0.0.1:3000",
            )

        # Remove Server header (uvicorn adds it)
        if "server" in headers:
            del headers["server"]

        # No cachear el HTML/JS del dashboard (evita servir JS viejo tras
        # una actualización y que el usuario tenga que forzar recarga).
        if request.url.path in ("/", "/legacy", "/favicon.svg"):
            headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            headers["Pragma"] = "no-cache"
            headers["Expires"] = "0"

        return response
