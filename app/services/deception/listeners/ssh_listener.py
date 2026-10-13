"""Listener SSH señuelo (paramiko).

Servidor SSH falso que:
- usa una **host key persistente** (``config/deception/keys/``) para que el
  fingerprint no cambie entre reinicios (evita "Host key verification failed");
- **autentica de forma realista**: acepta credenciales débiles de la persona
  al primer intento y rechaza los primeros intentos incorrectos antes de
  aceptar (para capturar la sesión sin delatarse);
- ofrece una **shell falsa coherente con el vendor** (respuestas y errores).

Paramiko es bloqueante; el servidor corre en un thread dedicado y reporta
eventos con ``emit_sync``.
"""

import logging
import os
import socketserver
import threading
from typing import Optional

from app.core.settings import DECEPTION_SSH_HOST_KEY
from app.services.deception import personas, telemetry_svc

logger = logging.getLogger(__name__)

_host_key_lock = threading.Lock()
_host_key = None  # paramiko.RSAKey cacheado


def _get_host_key():
    """Carga la host key RSA persistente, generándola la primera vez."""
    global _host_key
    with _host_key_lock:
        if _host_key is not None:
            return _host_key
        import paramiko

        path = DECEPTION_SSH_HOST_KEY
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                _host_key = paramiko.RSAKey.from_private_key_file(str(path))
                return _host_key
            _host_key = paramiko.RSAKey.generate(2048)
            _host_key.write_private_key_file(str(path))
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            logger.info("[deception] host key SSH generada → %s", path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[deception] no se pudo persistir host key (%s); efímera", exc)
            _host_key = paramiko.RSAKey.generate(2048)
        return _host_key


class SshListener:
    """Servidor SSH señuelo gestionado desde un thread."""

    proto = "ssh"
    MAX_COMMANDS = 50
    ATTEMPTS_BEFORE_ACCEPT = 2

    def __init__(self, device_id: str, role: str, host: str, port: int,
                 banner: Optional[str] = None, proto: Optional[str] = None):
        self.device_id = device_id
        self.role = role
        self.host = host
        self.port = port
        self.banner = banner
        self.vendor = None
        if proto:
            self.proto = proto
        self._server: Optional[socketserver.ThreadingTCPServer] = None
        self._thread: Optional[threading.Thread] = None

    async def start(self) -> None:
        try:
            import paramiko  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "paramiko no está instalado; SSH señuelo deshabilitado"
            ) from exc

        _get_host_key()
        listener = self

        class _Handler(socketserver.BaseRequestHandler):
            def handle(inner_self):  # noqa: N805
                listener._handle_ssh(inner_self.request, inner_self.client_address)

        class _Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self._server = _Server((self.host, self.port), _Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, name=f"deception-ssh-{self.device_id}",
            daemon=True,
        )
        self._thread.start()
        logger.info("[deception] ssh %s:%s escuchando (%s)",
                    self.host, self.port, self.device_id)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    # ── Manejo en el thread ──────────────────────────────────

    def _handle_ssh(self, client_sock, addr) -> None:
        import paramiko

        src_ip, src_port = addr[0], addr[1]
        transport = paramiko.Transport(client_sock)
        try:
            banner = personas.banner_for(self.role, self.banner)
            # El identification string SSH no admite espacios.
            ident = banner.replace(" ", "-")
            transport.local_version = f"SSH-2.0-{ident}"
        except Exception:  # noqa: BLE001
            pass

        server = _SSHServer(self, src_ip, src_port)
        try:
            transport.add_server_key(_get_host_key())
            transport.start_server(server=server)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[deception] SSH handshake falló con %s: %s", src_ip, exc)
            transport.close()
            return

        telemetry_svc.emit_sync(self.device_id, src_ip, "ssh", "connect",
                                src_port=src_port)
        chan = transport.accept(20)
        if chan is None:
            transport.close()
            return

        prompt = personas.prompt_for(self.role)
        try:
            chan.send(f"\r\n{prompt}")
            for _ in range(self.MAX_COMMANDS):
                data = chan.recv(1024)
                if not data:
                    break
                command = data.decode("utf-8", errors="replace").strip()
                if not command:
                    chan.send(prompt)
                    continue
                telemetry_svc.emit_sync(
                    self.device_id, src_ip, "ssh", "command",
                    username=server.username,
                    detail={"service": "ssh", "command": command},
                )
                if command in ("exit", "quit", "logout"):
                    chan.send("Connection to host closed.\r\n")
                    break
                response = personas.shell_response(self.role, self.vendor, command)
                out = f"{response}\r\n{prompt}" if response else prompt
                chan.send(out)
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                chan.close()
            except Exception:  # noqa: BLE001
                pass
            transport.close()


class _SSHServer:
    """Implementación perezosa de paramiko.ServerInterface.

    Se construye en runtime para no importar paramiko al cargar el módulo.
    """

    def __new__(cls, listener, src_ip, src_port):
        import paramiko

        class _ServerInterface(paramiko.ServerInterface):
            def __init__(self):
                self.listener = listener
                self.src_ip = src_ip
                self.src_port = src_port
                self.username = ""
                self.failed_attempts = 0

            def check_auth_password(self, username, password):  # noqa: N802
                self.username = username
                creds = personas.credentials_for(self.listener.role)
                weak = (username, password) in creds
                if weak or self.failed_attempts >= self.listener.ATTEMPTS_BEFORE_ACCEPT:
                    telemetry_svc.emit_sync(
                        self.listener.device_id, self.src_ip, "ssh", "login_success",
                        username=username, success=True, src_port=self.src_port,
                        detail={"service": "ssh", "password": password, "weak": weak},
                    )
                    return paramiko.AUTH_SUCCESSFUL
                # Primeros intentos: rechazar (como un equipo real).
                self.failed_attempts += 1
                telemetry_svc.emit_sync(
                    self.listener.device_id, self.src_ip, "ssh", "login_attempt",
                    username=username, success=False, src_port=self.src_port,
                    detail={"service": "ssh", "password": password},
                )
                return paramiko.AUTH_FAILED

            def check_auth_publickey(self, username, key):  # noqa: N802
                return paramiko.AUTH_FAILED

            def get_allowed_auths(self, username):  # noqa: N802
                return "password"

            def check_channel_request(self, kind, chanid):  # noqa: N802
                return paramiko.OPEN_SUCCEEDED

            def check_channel_shell_request(self, channel):  # noqa: N802
                return True

            def check_channel_pty_request(self, *args, **kwargs):  # noqa: N802
                return True

        return _ServerInterface()
