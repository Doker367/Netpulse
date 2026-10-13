"""Listener HTTP/HTTPS señuelo.

Devuelve un panel de administración falso con la identidad del vendor y
registra peticiones y credenciales enviadas por formulario (POST).
Para HTTPS el motor lo trataría con TLS; en esta fase se atiende HTTP
plano (el TLS se añadirá en la fase de hardening).
"""

import urllib.parse
from typing import Optional

from app.services.deception import panels
from app.services.deception.listeners.base import BaseListener

MAX_HEADERS = 50


class HttpListener(BaseListener):
    proto = "http"

    async def handle(self, reader, writer, src_ip, src_port):
        request_line = await reader.readline()
        if not request_line:
            return
        try:
            method, path, _version = request_line.decode("latin-1").split(" ", 2)
        except ValueError:
            return

        headers = await self._read_headers(reader)
        body = await self._read_body(reader, headers)

        if path.split("?", 1)[0] == "/favicon.ico":
            await self._respond(writer, 404, "")
            return

        await self.emit(
            src_ip, "http_request", src_port=src_port,
            detail={"method": method, "path": path,
                    "user_agent": headers.get("user-agent", "")},
        )

        if method.upper() == "POST" and body:
            form = urllib.parse.parse_qs(body, keep_blank_values=True)
            username = (form.get("username") or form.get("user") or [""])[0]
            password = (form.get("password") or form.get("pass") or [""])[0]
            await self.emit(
                src_ip, "login_attempt", username=username, src_port=src_port,
                detail={"service": "http", "path": path, "password": password},
            )
            # Devuelve el panel como si hubiese autenticado (decepción).
            await self._respond(writer, 200, panels.dashboard_page(self.role, username or "admin", self.banner))
        elif path.split("?", 1)[0] in ("/", "/login", "/admin", "/index.html", "/cgi-bin/luci"):
            await self._respond(writer, 200, panels.login_page(self.role, self.banner))
        else:
            await self._respond(writer, 404, panels.not_found_page())

    async def _read_headers(self, reader) -> dict:
        headers: dict[str, str] = {}
        for _ in range(MAX_HEADERS):
            line = await reader.readline()
            if not line or line in (b"\r\n", b"\n"):
                break
            try:
                key, value = line.decode("latin-1").split(":", 1)
                headers[key.strip().lower()] = value.strip()
            except ValueError:
                continue
        return headers

    async def _read_body(self, reader, headers: dict) -> Optional[str]:
        try:
            length = int(headers.get("content-length", "0") or 0)
        except ValueError:
            return None
        if length <= 0 or length > 65536:
            return None
        raw = await reader.readexactly(length)
        return raw.decode("utf-8", errors="replace")

    async def _respond(self, writer, status: int, body: str) -> None:
        reasons = {200: "OK", 302: "Found", 404: "Not Found", 401: "Unauthorized"}
        reason = reasons.get(status, "OK")
        payload = body.encode("utf-8")
        head = (
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Server: {panels.server_header(self.role)}\r\n"
            f"Content-Length: {len(payload)}\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            "Connection: close\r\n\r\n"
        )
        await self.write(writer, head.encode("latin-1") + payload)
