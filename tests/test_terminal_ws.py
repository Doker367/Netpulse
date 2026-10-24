"""Tests del WebSocket de Terminal: modo legacy, contrato JSON, heartbeat e idle."""

from unittest.mock import patch

import paramiko
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.security import create_access_token
from app.main import app
from app.routers import terminal as terminal_router


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def admin_token():
    return create_access_token({"sub": "admin", "role": "admin"})


def _device(**overrides) -> dict:
    device = {
        "id": "dev1",
        "hostname": "10.0.0.1",
        "port": 22,
        "protocol": "ssh",
        "driver": "ios",
        "credentials": {"username": "admin", "password": "secreto"},
    }
    device.update(overrides)
    return device


class FakeChannel:
    """Canal paramiko falso: entrega chunks y registra lo enviado."""

    def __init__(self, output: list[bytes] | None = None):
        self._output = list(output or [])
        self.sent: list[str] = []
        self.resizes: list[tuple[int, int]] = []
        self.closed = False

    def settimeout(self, _timeout) -> None:
        pass

    def recv_ready(self) -> bool:
        return bool(self._output)

    def recv(self, _n: int) -> bytes:
        return self._output.pop(0) if self._output else b""

    def recv_stderr_ready(self) -> bool:
        return False

    def recv_stderr(self, _n: int) -> bytes:
        return b""

    def send(self, data: str) -> None:
        self.sent.append(data)

    def resize_pty(self, width: int, height: int) -> None:
        self.resizes.append((width, height))

    def close(self) -> None:
        self.closed = True


class FakeSSHClient:
    """SSHClient falso; opcionalmente falla en ``connect``."""

    def __init__(self, channel: FakeChannel | None = None, connect_exc: Exception | None = None):
        self.channel = channel or FakeChannel()
        self.connect_exc = connect_exc
        self.connect_kwargs: dict = {}
        self.closed = False

    def set_missing_host_key_policy(self, _policy) -> None:
        pass

    def connect(self, _host, **kwargs) -> None:
        self.connect_kwargs = kwargs
        if self.connect_exc:
            raise self.connect_exc

    def invoke_shell(self, **_kwargs) -> FakeChannel:
        return self.channel

    def close(self) -> None:
        self.closed = True


def _ws_url(token: str, *, fmt: str | None = None) -> str:
    url = f"/ws/terminal/dev1?token={token}"
    if fmt:
        url += f"&fmt={fmt}"
    return url


def _connect(client: TestClient, token: str, *, fmt: str | None = None):
    return client.websocket_connect(_ws_url(token, fmt=fmt))


# ── Modo legacy (regresión UI actual) ───────────────────────


def test_legacy_envia_linea_conectando(client, admin_token):
    fake = FakeSSHClient()
    with patch("app.services.napalm_svc._find_device", return_value=_device()), \
         patch("paramiko.SSHClient", return_value=fake):
        with _connect(client, admin_token) as ws:
            line = ws.receive_text()
            assert line == "Conectando a 10.0.0.1:22 como admin...\r\n"
            assert fake.connect_kwargs["port"] == 22
            assert fake.connect_kwargs["timeout"] == 30.0
        assert fake.closed is True


def test_legacy_sin_fmt_envia_output_plano(client, admin_token):
    chan = FakeChannel([b"hola\r\n"])
    fake = FakeSSHClient(channel=chan)
    with patch("app.services.napalm_svc._find_device", return_value=_device()), \
         patch("paramiko.SSHClient", return_value=fake):
        with _connect(client, admin_token) as ws:
            assert ws.receive_text().startswith("Conectando a")
            assert ws.receive_text() == "hola\r\n"
            ws.send_text("show version\n")
    assert chan.sent == ["show version\n"]


# ── Autenticación y errores ─────────────────────────────────


def test_token_invalido_cierra_4401(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with _connect(client, "token-invalido", fmt="json") as ws:
            ws.receive_json()
    assert exc.value.code == 4401


def test_auth_rechazada_acepta_antes_de_cerrar_4401():
    """Regresión 7F: el 4401 solo llega si el upgrade se acepta primero."""

    class FakeWebSocket:
        def __init__(self, token):
            self.query_params = {"token": token}
            self.calls = []

        async def accept(self):
            self.calls.append("accept")

        async def close(self, code=1000):
            self.calls.append(("close", code))

    ws = FakeWebSocket("token-invalido")
    with patch("app.services.napalm_svc._find_device") as find_device, \
         patch("paramiko.SSHClient") as ssh_client:
        import asyncio

        asyncio.run(terminal_router.terminal(ws, "dev1"))

    assert ws.calls == ["accept", ("close", 4401)]
    find_device.assert_not_called()
    ssh_client.assert_not_called()


def test_rol_no_permitido_acepta_y_cierra_4401(client):
    token = create_access_token({"sub": "viewer", "role": "viewer"})
    with pytest.raises(WebSocketDisconnect) as exc:
        with _connect(client, token, fmt="json") as ws:
            ws.receive_json()
    assert exc.value.code == 4401


def test_device_inexistente_json(client, admin_token):
    with patch("app.services.napalm_svc._find_device", return_value=None):
        with _connect(client, admin_token, fmt="json") as ws:
            error = ws.receive_json()
            closed = ws.receive_json()
    assert error["type"] == "error"
    assert error["code"] == "device_not_found"
    assert "dev1" in error["detail"]
    assert closed == {
        "type": "closed",
        "code": "device_not_found",
        "reason": "equipo no encontrado",
    }


def test_device_inexistente_legacy(client, admin_token):
    with patch("app.services.napalm_svc._find_device", return_value=None):
        with _connect(client, admin_token) as ws:
            assert "no encontrado" in ws.receive_text()


# ── Modo JSON feliz: connected + output + ping/pong + closed ──


def test_json_connected_output_ping_y_cierre(client, admin_token):
    chan = FakeChannel(['hola "mundo"\r\n'.encode()])
    fake = FakeSSHClient(channel=chan)
    with patch("app.services.napalm_svc._find_device", return_value=_device()), \
         patch("paramiko.SSHClient", return_value=fake):
        with _connect(client, admin_token, fmt="json") as ws:
            connected = ws.receive_json()
            assert connected == {
                "type": "connected",
                "device": "dev1",
                "host": "10.0.0.1",
                "port": 22,
                "user": "admin",
            }
            assert "password" not in connected

            ws.send_json({"type": "ping"})
            seen_pong = False
            seen_output = False
            for _ in range(6):
                event = ws.receive_json()
                if event["type"] == "pong":
                    assert isinstance(event["ts"], (int, float))
                    seen_pong = True
                elif event["type"] == "output":
                    assert event["data"] == 'hola "mundo"\r\n'
                    seen_output = True
                if seen_pong and seen_output:
                    break
            assert seen_pong and seen_output

            ws.send_json({"type": "close"})
            closed = ws.receive_json()
            assert closed == {
                "type": "closed",
                "code": "operator",
                "reason": "cerrado por el operador",
            }
    assert chan.closed is True
    assert fake.closed is True


def test_json_reenvia_input_escapado(client, admin_token):
    chan = FakeChannel()
    fake = FakeSSHClient(channel=chan)
    with patch("app.services.napalm_svc._find_device", return_value=_device()), \
         patch("paramiko.SSHClient", return_value=fake):
        with _connect(client, admin_token, fmt="json") as ws:
            ws.receive_json()
            ws.send_text("ls -l\n")
            ws.send_json({"type": "resize", "cols": 100, "rows": 40})
            ws.send_json({"type": "control_no_soportado"})
            ws.send_json({"type": "close"})
            assert ws.receive_json()["type"] == "closed"
    assert chan.sent == ["ls -l\n"]
    assert chan.resizes == [(100, 40)]


# ── Idle timeout ────────────────────────────────────────────


def test_idle_cierra_con_codigo_idle(client, admin_token, monkeypatch):
    monkeypatch.setenv("NETPULSE_TERM_IDLE_S", "1")
    with patch("app.services.napalm_svc._find_device", return_value=_device()), \
         patch("paramiko.SSHClient", return_value=FakeSSHClient()):
        with _connect(client, admin_token, fmt="json") as ws:
            assert ws.receive_json()["type"] == "connected"
            closed = ws.receive_json()
    assert closed == {
        "type": "closed",
        "code": "idle",
        "reason": "timeout de inactividad",
    }


def test_idle_timeout_por_defecto_900(monkeypatch):
    monkeypatch.delenv("NETPULSE_TERM_IDLE_S", raising=False)
    assert terminal_router._idle_timeout_s() == 900.0
    monkeypatch.setenv("NETPULSE_TERM_IDLE_S", "basura")
    assert terminal_router._idle_timeout_s() == 900.0
    monkeypatch.setenv("NETPULSE_TERM_IDLE_S", "2.5")
    assert terminal_router._idle_timeout_s() == 2.5


def test_connect_timeout_por_defecto_y_env(monkeypatch):
    monkeypatch.delenv("NETPULSE_TERM_CONNECT_TIMEOUT_S", raising=False)
    assert terminal_router._connect_timeout_s() == 30.0
    monkeypatch.setenv("NETPULSE_TERM_CONNECT_TIMEOUT_S", "basura")
    assert terminal_router._connect_timeout_s() == 30.0
    monkeypatch.setenv("NETPULSE_TERM_CONNECT_TIMEOUT_S", "0")
    assert terminal_router._connect_timeout_s() == 30.0
    monkeypatch.setenv("NETPULSE_TERM_CONNECT_TIMEOUT_S", "45")
    assert terminal_router._connect_timeout_s() == 45.0


def test_kex_incompleto_no_se_reporta_como_auth():
    code, detail = terminal_router._classify_connect_error(
        paramiko.SSHException("No existing session"),
        device_id="mikrotik-edge-01",
        host="10.0.0.20",
        port=2225,
        driver="ros",
        ignored_api_port=None,
    )
    assert code == "ssh_connect"
    assert "negociación SSH" in detail
    assert "credenciales" not in detail


# ── Puerto SSH honesto ──────────────────────────────────────


def test_resolve_puerto_descarta_api_ros():
    device = _device(driver="ros", port=8728, protocol="")
    assert terminal_router._resolve_ssh_port(device) == (22, 8728)
    assert terminal_router._resolve_ssh_port(_device(driver="ros", port=8728, cli_port=2225)) == (2225, None)
    assert terminal_router._resolve_ssh_port(_device(port=2222)) == (2222, None)


def test_json_connected_usa_puerto_22_no_api(client, admin_token):
    fake = FakeSSHClient()
    device = _device(driver="ros", port=8728, protocol="")
    with patch("app.services.napalm_svc._find_device", return_value=device), \
         patch("paramiko.SSHClient", return_value=fake):
        with _connect(client, admin_token, fmt="json") as ws:
            connected = ws.receive_json()
            assert connected["port"] == 22
            ws.send_json({"type": "close"})
            ws.receive_json()
    assert fake.connect_kwargs["port"] == 22


def test_error_ssh_connect_menciona_puerto_api(client, admin_token):
    fake = FakeSSHClient(connect_exc=OSError("network unreachable"))
    device = _device(driver="ros", port=8728, protocol="")
    with patch("app.services.napalm_svc._find_device", return_value=device), \
         patch("paramiko.SSHClient", return_value=fake):
        with _connect(client, admin_token, fmt="json") as ws:
            error = ws.receive_json()
            closed = ws.receive_json()
    assert error["type"] == "error"
    assert error["code"] == "ssh_connect"
    assert "8728" in error["detail"]
    assert "API" in error["detail"]
    assert "se intentó el 22" in error["detail"]
    assert closed == {
        "type": "closed",
        "code": "unreachable",
        "reason": "equipo inalcanzable",
    }


def test_error_ssh_auth(client, admin_token):
    fake = FakeSSHClient(connect_exc=paramiko.AuthenticationException("bad password"))
    with patch("app.services.napalm_svc._find_device", return_value=_device()), \
         patch("paramiko.SSHClient", return_value=fake):
        with _connect(client, admin_token, fmt="json") as ws:
            error = ws.receive_json()
            closed = ws.receive_json()
    assert error["code"] == "ssh_auth"
    assert "credenciales" in error["detail"]
    assert "secreto" not in error["detail"]
    assert closed["code"] == "unreachable"


def test_paramiko_no_disponible_json(client, admin_token):
    real_import = __import__

    def _fake_import(name, *args, **kwargs):
        if name == "paramiko":
            raise ImportError("sin paramiko")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_fake_import), \
         patch("app.services.napalm_svc._find_device", return_value=_device()):
        with _connect(client, admin_token, fmt="json") as ws:
            error = ws.receive_json()
            closed = ws.receive_json()
    assert error["code"] == "unsupported"
    assert closed["code"] == "internal"


def test_servidor_cierra_canal_si_el_cliente_se_va(client, admin_token):
    chan = FakeChannel()
    fake = FakeSSHClient(channel=chan)
    with patch("app.services.napalm_svc._find_device", return_value=_device()), \
         patch("paramiko.SSHClient", return_value=fake):
        with _connect(client, admin_token, fmt="json") as ws:
            assert ws.receive_json()["type"] == "connected"
    assert chan.closed is True
    assert fake.closed is True
