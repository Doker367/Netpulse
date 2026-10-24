"""Tests del router SFTP: puerto SSH honesto y errores por causa.

Todo está mockeado (paramiko, sockets y dispositivos): no se abre ninguna
conexión real. La verificación contra el laboratorio Docker queda para la
tarjeta 6D.
"""

import errno
import socket
import stat
from unittest.mock import MagicMock, patch

import paramiko
import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.main import app


@pytest.fixture
def api():
    return TestClient(app)


@pytest.fixture
def headers():
    token = create_access_token({"sub": "admin", "role": "admin"})
    return {"Authorization": f"Bearer {token}"}


def _device(**overrides):
    """Dispositivo de inventario mínimo, ya con password descifrado."""
    device = {
        "id": "mock_switch",
        "hostname": "192.0.2.10",
        "protocol": "ssh",
        "driver": "ios",
        "port": 22,
        "credentials": {"username": "admin", "password": "secret"},
    }
    device.update(overrides)
    return device


def _ssh(sftp=None):
    ssh = MagicMock()
    if sftp is not None:
        ssh.open_sftp.return_value = sftp
    return ssh


def _attr(name, mode, size=0, mtime=0):
    attr = MagicMock()
    attr.filename = name
    attr.st_mode = mode
    attr.st_size = size
    attr.st_mtime = mtime
    return attr


def _request(api, headers, method, url, device, ssh, **kwargs):
    """Ejecuta la petición con el dispositivo y el cliente SSH mockeados."""
    with patch("app.services.napalm_svc._find_device", return_value=device), \
         patch("paramiko.SSHClient", return_value=ssh):
        return api.request(method, url, headers=headers, **kwargs)


# ── Resolución del puerto SSH ───────────────────────────────


def test_mikrotik_port_8728_is_api_not_ssh(api, headers):
    """RouterOS publica la API en 8728: SFTP debe caer al default 22."""
    device = _device(id="mikrotik-edge-01", driver="ros", port=8728)
    sftp = MagicMock()
    sftp.listdir_attr.return_value = []
    ssh = _ssh(sftp)

    res = _request(
        api, headers, "GET",
        "/api/sftp/mikrotik-edge-01/list?path=/", device, ssh,
    )

    assert res.status_code == 200
    assert ssh.connect.call_args.kwargs["port"] == 22
    assert ssh.connect.call_args.kwargs["look_for_keys"] is False
    assert ssh.connect.call_args.kwargs["allow_agent"] is False
    assert ssh.connect.call_args.kwargs["timeout"] == 10
    assert ssh.connect.call_args.kwargs["banner_timeout"] == 15
    assert ssh.connect.call_args.kwargs["auth_timeout"] == 15


def test_ros_api_ssl_port_8729_is_not_used(api, headers):
    device = _device(id="mikrotik-edge-01", driver="ros", port=8729)
    ssh = _ssh(MagicMock(listdir_attr=MagicMock(return_value=[])))

    res = _request(
        api, headers, "GET",
        "/api/sftp/mikrotik-edge-01/list?path=/", device, ssh,
    )

    assert res.status_code == 200
    assert ssh.connect.call_args.kwargs["port"] == 22


def test_cli_port_wins_over_api_port(api, headers):
    device = _device(
        id="mikrotik-edge-01", driver="ros", port=8728, cli_port=2225,
    )
    ssh = _ssh(MagicMock(listdir_attr=MagicMock(return_value=[])))

    res = _request(
        api, headers, "GET",
        "/api/sftp/mikrotik-edge-01/list?path=/", device, ssh,
    )

    assert res.status_code == 200
    assert ssh.connect.call_args.kwargs["port"] == 2225


def test_ssh_port_in_inventory_wins_over_api_port(api, headers):
    device = _device(driver="ros", port=8728, ssh_port=2225)
    ssh = _ssh(MagicMock(listdir_attr=MagicMock(return_value=[])))

    res = _request(api, headers, "GET", "/api/sftp/mock_switch/list?path=/", device, ssh)

    assert res.status_code == 200
    assert ssh.connect.call_args.kwargs["port"] == 2225


def test_missing_port_defaults_to_22(api, headers):
    device = _device(port=None)
    ssh = _ssh(MagicMock(listdir_attr=MagicMock(return_value=[])))

    res = _request(api, headers, "GET", "/api/sftp/mock_switch/list?path=/", device, ssh)

    assert res.status_code == 200
    assert ssh.connect.call_args.kwargs["port"] == 22


def test_api_port_failure_explains_inventory_mismatch(api, headers):
    """Si el inventario apunta a la API, el 502 lo dice explícitamente."""
    device = _device(id="mikrotik-edge-01", driver="ros", port=8728)
    ssh = _ssh()
    ssh.connect.side_effect = socket.gaierror(8, "nodename nor servname provided")

    res = _request(
        api, headers, "GET",
        "/api/sftp/mikrotik-edge-01/list?path=/", device, ssh,
    )

    assert res.status_code == 502
    detail = res.json()["detail"]
    assert "host no resuelto" in detail
    assert "puerto API 8728" in detail
    assert "se intentó el 22" in detail


# ── Errores de conexión clasificados ────────────────────────


def test_authentication_error_returns_401(api, headers):
    ssh = _ssh()
    ssh.connect.side_effect = paramiko.AuthenticationException("Authentication failed.")

    res = _request(api, headers, "GET", "/api/sftp/mock_switch/list?path=/", _device(), ssh)

    assert res.status_code == 401
    assert res.json()["detail"] == (
        "Autenticación SSH fallida en mock_switch: revisa credenciales"
    )


def test_connect_timeout_returns_504(api, headers):
    ssh = _ssh()
    ssh.connect.side_effect = socket.timeout("timed out")

    res = _request(api, headers, "GET", "/api/sftp/mock_switch/list?path=/", _device(), ssh)

    assert res.status_code == 504
    assert res.json()["detail"] == (
        "Tiempo de espera agotado conectando a mock_switch (192.0.2.10:22)"
    )


def test_connection_refused_returns_502(api, headers):
    ssh = _ssh()
    ssh.connect.side_effect = ConnectionRefusedError(
        errno.ECONNREFUSED, "Connection refused"
    )

    res = _request(api, headers, "GET", "/api/sftp/mock_switch/list?path=/", _device(), ssh)

    assert res.status_code == 502
    detail = res.json()["detail"]
    assert detail.startswith("No se pudo conectar a mock_switch (192.0.2.10:22): ")
    assert "conexión rechazada" in detail


def test_dns_error_returns_502(api, headers):
    ssh = _ssh()
    ssh.connect.side_effect = socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    res = _request(api, headers, "GET", "/api/sftp/mock_switch/list?path=/", _device(), ssh)

    assert res.status_code == 502
    assert "host no resuelto" in res.json()["detail"]


@pytest.mark.parametrize(
    "message",
    ["Channel closed", "Unable to open channel.", "subsystem request failed"],
)
def test_sftp_subsystem_unsupported_returns_501(api, headers, message):
    """FRR acepta SSH pero no ofrece subsistema SFTP."""
    ssh = _ssh()
    ssh.open_sftp.side_effect = paramiko.SSHException(message)

    res = _request(api, headers, "GET", "/api/sftp/frr_router/list?path=/", _device(id="frr_router"), ssh)

    assert res.status_code == 501
    assert res.json()["detail"] == (
        "SFTP no soportado por este equipo (frr_router); "
        "usa Terminal/Comandos para CLI"
    )


# ── Casos felices (cliente falso) ───────────────────────────


def test_list_happy_path(api, headers):
    sftp = MagicMock()
    sftp.listdir_attr.return_value = [
        _attr("running.cfg", stat.S_IFREG | 0o644, 1024, 1700000100),
        _attr("configs", stat.S_IFDIR | 0o755, 4096, 1700000000),
    ]
    ssh = _ssh(sftp)

    res = _request(api, headers, "GET", "/api/sftp/mock_switch/list?path=/", _device(), ssh)

    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["device_id"] == "mock_switch"
    assert body["path"] == "/"
    assert [item["name"] for item in body["items"]] == ["configs", "running.cfg"]
    assert body["items"][0]["is_dir"] is True
    assert body["items"][1]["is_dir"] is False


def test_empty_directory_is_success_with_empty_list(api, headers):
    """Un directorio vacío legítimo sí es 200 + items=[]."""
    sftp = MagicMock()
    sftp.listdir_attr.return_value = []
    ssh = _ssh(sftp)

    res = _request(api, headers, "GET", "/api/sftp/mock_switch/list?path=/", _device(), ssh)

    assert res.status_code == 200
    assert res.json() == {
        "success": True,
        "device_id": "mock_switch",
        "path": "/",
        "items": [],
    }


def test_upload_happy_path(api, headers):
    sftp = MagicMock()
    ssh = _ssh(sftp)

    res = _request(
        api, headers, "POST",
        "/api/sftp/mock_switch/upload?remote_dir=/configs&filename=test.cfg",
        _device(), ssh,
        content=b"hostname switch01\n",
    )

    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["remote_path"] == "/configs/test.cfg"
    assert body["size_bytes"] == len(b"hostname switch01\n")
    assert sftp.putfo.call_args.args[1] == "/configs/test.cfg"


def test_download_happy_path(api, headers):
    attr = _attr("running.cfg", stat.S_IFREG | 0o644, 18)
    remote_file = MagicMock()
    remote_file.read.side_effect = [b"hostname switch01\n", b""]
    sftp = MagicMock()
    sftp.stat.return_value = attr
    sftp.open.return_value.__enter__.return_value = remote_file
    ssh = _ssh(sftp)

    res = _request(
        api, headers, "GET",
        "/api/sftp/mock_switch/download?remote_path=/configs/running.cfg",
        _device(), ssh,
    )

    assert res.status_code == 200
    assert res.content == b"hostname switch01\n"
    assert 'attachment; filename="running.cfg"' in res.headers["content-disposition"]
    assert res.headers["content-length"] == "18"


# ── Fallos de operación: nunca un éxito vacío ───────────────


def test_list_failure_is_not_empty_success(api, headers):
    sftp = MagicMock()
    sftp.listdir_attr.side_effect = IOError("No such file")
    ssh = _ssh(sftp)

    res = _request(api, headers, "GET", "/api/sftp/mock_switch/list?path=/missing", _device(), ssh)

    assert res.status_code == 400
    body = res.json()
    assert "items" not in body
    assert body.get("success") is None
    assert "No se pudo acceder a la ruta '/missing'" in body["detail"]


def test_list_permission_denied_returns_403(api, headers):
    sftp = MagicMock()
    sftp.listdir_attr.side_effect = PermissionError(errno.EACCES, "Permission denied")
    ssh = _ssh(sftp)

    res = _request(api, headers, "GET", "/api/sftp/mock_switch/list?path=/root", _device(), ssh)

    assert res.status_code == 403
    assert res.json()["detail"] == (
        "Permiso denegado al leer /root en mock_switch"
    )


def test_upload_permission_denied_returns_403(api, headers):
    sftp = MagicMock()
    sftp.putfo.side_effect = PermissionError(errno.EACCES, "Permission denied")
    ssh = _ssh(sftp)

    res = _request(
        api, headers, "POST",
        "/api/sftp/mock_switch/upload?remote_dir=/configs&filename=test.cfg",
        _device(), ssh,
        content=b"x",
    )

    assert res.status_code == 403
    assert res.json()["detail"] == (
        "Permiso denegado al escribir /configs/test.cfg en mock_switch"
    )


def test_upload_connection_drop_returns_502(api, headers):
    sftp = MagicMock()
    sftp.putfo.side_effect = OSError(errno.ECONNRESET, "Connection reset by peer")
    ssh = _ssh(sftp)

    res = _request(
        api, headers, "POST",
        "/api/sftp/mock_switch/upload?remote_dir=/&filename=test.cfg",
        _device(), ssh,
        content=b"x",
    )

    assert res.status_code == 502
    assert "conexión interrumpida" in res.json()["detail"]


def test_download_missing_file_returns_404(api, headers):
    sftp = MagicMock()
    sftp.stat.side_effect = IOError("No such file")
    ssh = _ssh(sftp)

    res = _request(
        api, headers, "GET",
        "/api/sftp/mock_switch/download?remote_path=/nope.cfg",
        _device(), ssh,
    )

    assert res.status_code == 404
    assert res.json()["detail"].startswith("Archivo no encontrado en '/nope.cfg'")
    assert res.content != b""


def test_download_directory_returns_400(api, headers):
    sftp = MagicMock()
    sftp.stat.return_value = _attr("configs", stat.S_IFDIR | 0o755)
    ssh = _ssh(sftp)

    res = _request(
        api, headers, "GET",
        "/api/sftp/mock_switch/download?remote_path=/configs",
        _device(), ssh,
    )

    assert res.status_code == 400
    assert res.json()["detail"] == (
        "La ruta solicitada es un directorio, no un archivo."
    )


def test_device_not_found_still_404(api, headers):
    res = _request(
        api, headers, "GET", "/api/sftp/no_existe/list?path=/", None, _ssh(),
    )

    assert res.status_code == 404
    assert res.json()["detail"] == "Dispositivo 'no_existe' no encontrado."
