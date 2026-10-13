"""Tests para el módulo SFTP (/api/sftp)."""

from unittest.mock import MagicMock, patch
import stat
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.security import create_access_token


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def admin_token():
    return create_access_token({"sub": "admin", "role": "admin"})


@pytest.fixture
def viewer_token():
    return create_access_token({"sub": "viewer", "role": "viewer"})


def test_sftp_requires_auth(client):
    """Sin token debe retornar 401/403."""
    res = client.get("/api/sftp/dev1/list")
    assert res.status_code in (401, 403)


def test_sftp_viewer_forbidden(client, viewer_token):
    """Rol viewer no tiene permiso (requiere operator o admin)."""
    res = client.get("/api/sftp/dev1/list", headers={"Authorization": f"Bearer {viewer_token}"})
    assert res.status_code == 403


def test_sftp_device_not_found(client, admin_token):
    """Dispositivo inexistente retorna 404."""
    res = client.get("/api/sftp/non_existent_dev/list", headers={"Authorization": f"Bearer {admin_token}"})
    assert res.status_code == 404


def test_sftp_list_mocked(client, admin_token):
    """Prueba listado SFTP con mock de paramiko."""
    fake_device = {
        "id": "mock_switch",
        "hostname": "192.168.1.1",
        "port": 22,
        "credentials": {"username": "admin", "password": "secret"},
    }

    mock_attr_dir = MagicMock()
    mock_attr_dir.filename = "configs"
    mock_attr_dir.st_mode = stat.S_IFDIR | 0o755
    mock_attr_dir.st_size = 4096
    mock_attr_dir.st_mtime = 1700000000

    mock_attr_file = MagicMock()
    mock_attr_file.filename = "startup-config.cfg"
    mock_attr_file.st_mode = stat.S_IFREG | 0o644
    mock_attr_file.st_size = 1024
    mock_attr_file.st_mtime = 1700000100

    mock_sftp = MagicMock()
    mock_sftp.listdir_attr.return_value = [mock_attr_file, mock_attr_dir]

    mock_ssh = MagicMock()
    mock_ssh.open_sftp.return_value = mock_sftp

    with patch("app.services.napalm_svc._find_device", return_value=fake_device), \
         patch("paramiko.SSHClient", return_value=mock_ssh):
        res = client.get("/api/sftp/mock_switch/list?path=/", headers={"Authorization": f"Bearer {admin_token}"})
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert len(data["items"]) == 2
        # Las carpetas van primero
        assert data["items"][0]["name"] == "configs"
        assert data["items"][0]["is_dir"] is True
        assert data["items"][1]["name"] == "startup-config.cfg"
        assert data["items"][1]["is_dir"] is False


def test_sftp_upload_mocked(client, admin_token):
    """Prueba subida SFTP con mock de paramiko."""
    fake_device = {
        "id": "mock_switch",
        "hostname": "192.168.1.1",
        "port": 22,
        "credentials": {"username": "admin", "password": "secret"},
    }

    mock_sftp = MagicMock()
    mock_ssh = MagicMock()
    mock_ssh.open_sftp.return_value = mock_sftp

    with patch("app.services.napalm_svc._find_device", return_value=fake_device), \
         patch("paramiko.SSHClient", return_value=mock_ssh):
        res = client.post(
            "/api/sftp/mock_switch/upload?remote_dir=/configs&filename=test.cfg",
            headers={"Authorization": f"Bearer {admin_token}", "Content-Type": "application/octet-stream"},
            content=b"hostname switch01\n",
        )
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["remote_path"] == "/configs/test.cfg"
        assert mock_sftp.putfo.called


def test_sftp_download_mocked(client, admin_token):
    """Prueba descarga SFTP con mock de paramiko."""
    fake_device = {
        "id": "mock_switch",
        "hostname": "192.168.1.1",
        "port": 22,
        "credentials": {"username": "admin", "password": "secret"},
    }

    mock_attr = MagicMock()
    mock_attr.st_mode = stat.S_IFREG | 0o644
    mock_attr.st_size = 18

    mock_file = MagicMock()
    mock_file.read.side_effect = [b"hostname switch01\n", b""]
    mock_sftp = MagicMock()
    mock_sftp.stat.return_value = mock_attr
    mock_sftp.open.return_value.__enter__.return_value = mock_file

    mock_ssh = MagicMock()
    mock_ssh.open_sftp.return_value = mock_sftp

    with patch("app.services.napalm_svc._find_device", return_value=fake_device), \
         patch("paramiko.SSHClient", return_value=mock_ssh):
        res = client.get(
            "/api/sftp/mock_switch/download?remote_path=/configs/running.cfg",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 200
        assert res.content == b"hostname switch01\n"
        assert 'attachment; filename="running.cfg"' in res.headers.get("content-disposition", "")
