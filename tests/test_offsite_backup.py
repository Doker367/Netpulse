"""Backups offsite: exportación, verificación y restauración.

Todo se ejecuta contra un S3 y un SFTP en memoria (mocks): no se abre
ninguna conexión. El drill real contra contenedores está en
``scripts/offsite_drill.sh``. Los tests cubren los fallos que la tarjeta
13O encontró: bucket/credenciales/endpoint malos, objetos manipulados,
rutas de restauración y códigos HTTP claros (nunca un 500 genérico).
"""

import hashlib
import io
import stat
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import paramiko
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from fastapi.testclient import TestClient

from app.core import settings
from app.core.security import create_access_token
from app.main import app
from app.services import backup_offsite_svc as offsite


# ── Fakes ────────────────────────────────────────────────────


def _client_error(code: str, message: str, status: int) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": message},
         "ResponseMetadata": {"HTTPStatusCode": status}},
        "TestOperation",
    )


class _Body:
    def __init__(self, data: bytes):
        self._stream = io.BytesIO(data)

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)


class FakeS3:
    """S3 mínimo con ETag MD5, listado y errores tipados."""

    def __init__(self, *, buckets=("netpulse-drill",), auth_error: bool = False):
        self.buckets: dict[str, dict[str, bytes]] = {name: {} for name in buckets}
        self.auth_error = auth_error
        self.calls: list[str] = []

    def _check_auth(self):
        if self.auth_error:
            raise _client_error("InvalidAccessKeyId", "The access key is invalid", 403)

    def _bucket(self, name: str) -> dict[str, bytes]:
        self._check_auth()
        if name not in self.buckets:
            raise _client_error("NoSuchBucket", "The specified bucket does not exist", 404)
        return self.buckets[name]

    def upload_file(self, filename: str, bucket: str, key: str, **_kwargs):
        self.calls.append(f"upload:{key}")
        self._bucket(bucket)[key] = Path(filename).read_bytes()

    def put_object(self, Bucket, Key, Body, **_kwargs):
        self.calls.append(f"put:{Key}")
        data = Body if isinstance(Body, bytes) else Body.read()
        self._bucket(Bucket)[Key] = bytes(data)
        return {"ETag": f'"{hashlib.md5(bytes(data)).hexdigest()}"'}

    def head_object(self, Bucket, Key, **_kwargs):
        bucket = self._bucket(Bucket)
        if Key not in bucket:
            raise _client_error("NoSuchKey", "The specified key does not exist", 404)
        data = bucket[Key]
        return {
            "ContentLength": len(data),
            "ETag": f'"{hashlib.md5(data).hexdigest()}"',
        }

    def get_object(self, Bucket, Key, **_kwargs):
        bucket = self._bucket(Bucket)
        if Key not in bucket:
            raise _client_error("NoSuchKey", "The specified key does not exist", 404)
        return {"Body": _Body(bucket[Key])}

    def list_objects_v2(self, Bucket, Prefix="", **_kwargs):
        bucket = self._bucket(Bucket)
        contents = [
            {"Key": key, "Size": len(data), "ETag": f'"{hashlib.md5(data).hexdigest()}"',
             "LastModified": None}
            for key, data in sorted(bucket.items())
            if key.startswith(Prefix)
        ]
        return {"Contents": contents, "IsTruncated": False}


class FakeSFTP:
    """SFTP en memoria con archivos planos y stat/listdir_attr reales."""

    def __init__(self, root="/home/drill"):
        self.root = root
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = {root}

    def _dir(self, path: str):
        return SimpleNamespace(st_size=0, st_mode=stat.S_IFDIR | 0o755, st_mtime=0)

    def stat(self, path):
        if path in self.files:
            return SimpleNamespace(st_size=len(self.files[path]),
                                   st_mode=stat.S_IFREG | 0o644, st_mtime=0)
        if path in self.dirs:
            return self._dir(path)
        raise FileNotFoundError(path)

    def mkdir(self, path):
        self.dirs.add(path)

    def put(self, local, remote):
        self.files[remote] = Path(local).read_bytes()

    def get(self, remote, local):
        Path(local).write_bytes(self.files[remote])

    def listdir_attr(self, remote_dir):
        prefix = remote_dir.rstrip("/") + "/"
        out = []
        for path, data in self.files.items():
            if not path.startswith(prefix):
                continue
            name = path[len(prefix):]
            if "/" in name:
                continue
            out.append(SimpleNamespace(filename=name, st_size=len(data),
                                       st_mode=stat.S_IFREG | 0o644, st_mtime=0))
        return out

    @contextmanager
    def open(self, path, mode="rb"):
        if "r" in mode:
            if path not in self.files:
                raise FileNotFoundError(path)
            yield io.BytesIO(self.files[path])
        else:
            buffer = io.BytesIO()
            yield buffer
            self.files[path] = buffer.getvalue()

    def close(self):
        pass


@contextmanager
def _session(fake):
    yield fake


# ── Fixtures ─────────────────────────────────────────────────


@pytest.fixture
def backups(tmp_path, monkeypatch):
    folder = tmp_path / "backups"
    folder.mkdir()
    (folder / "core-1_20260101_120000.cfg").write_bytes(
        b"hostname core-1\ninterface Gi0/1\n ip address 10.0.0.1 255.255.255.0\n"
    )
    (folder / "core-1_20260101_120000.json").write_bytes(
        b'{"hostname":"core-1","model":"C9300"}\n'
    )
    (folder / "netpulse_self_20260101_120000.tar.gz").write_bytes(
        b"\x1f\x8b\x08\x00drill-self-backup"
    )
    (folder / "ignore.txt").write_text("no se publica", encoding="utf-8")
    monkeypatch.setattr(offsite, "BACKUP_DIR", folder)
    monkeypatch.setattr(offsite, "RESTORE_DIR", tmp_path / "restored")
    monkeypatch.setattr(offsite, "_last_export", None)
    return folder


def _s3_settings(monkeypatch, backend="s3"):
    monkeypatch.setattr(settings, "OFFSITE_ENABLED", True)
    monkeypatch.setattr(settings, "OFFSITE_BACKEND", backend)
    monkeypatch.setattr(settings, "OFFSITE_S3_BUCKET", "netpulse-drill")
    monkeypatch.setattr(settings, "OFFSITE_S3_ENDPOINT", "http://127.0.0.1:9000")
    monkeypatch.setattr(settings, "OFFSITE_S3_REGION", "us-east-1")
    monkeypatch.setattr(settings, "OFFSITE_S3_ACCESS_KEY", "drill")
    monkeypatch.setattr(settings, "OFFSITE_S3_SECRET_KEY", "drill-secret")
    monkeypatch.setattr(settings, "OFFSITE_S3_PREFIX", "netpulse")


def _sftp_settings(monkeypatch):
    monkeypatch.setattr(settings, "OFFSITE_ENABLED", True)
    monkeypatch.setattr(settings, "OFFSITE_BACKEND", "sftp")
    monkeypatch.setattr(settings, "OFFSITE_SFTP_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "OFFSITE_SFTP_PORT", 2222)
    monkeypatch.setattr(settings, "OFFSITE_SFTP_USER", "drill")
    monkeypatch.setattr(settings, "OFFSITE_SFTP_PASSWORD", "secret")
    monkeypatch.setattr(settings, "OFFSITE_SFTP_KEY", "")
    monkeypatch.setattr(settings, "OFFSITE_SFTP_DIR", "netpulse-backups")


@pytest.fixture
def s3(backups, monkeypatch):
    _s3_settings(monkeypatch)
    fake = FakeS3()
    monkeypatch.setattr(offsite, "_s3_client", lambda: fake)
    return fake


@pytest.fixture
def sftp(backups, monkeypatch):
    _sftp_settings(monkeypatch)
    fake = FakeSFTP()
    monkeypatch.setattr(offsite, "_sftp_session", lambda: _session(fake))
    return fake


def _admin() -> dict:
    return {"Authorization": f"Bearer {create_access_token({'sub': 'admin', 'role': 'admin'})}"}


# ── S3: flujo completo ───────────────────────────────────────


def test_s3_export_verify_restore_completo(s3, backups, tmp_path):
    result = offsite.export_all()
    assert result["status"] == "ok", result
    assert result["uploaded"] == 3
    assert result["failed"] == 0
    assert result["manifest"] == "uploaded"
    assert result["local_intact"] is True
    # Solo los artefactos de backup, nunca ignore.txt.
    assert {row["name"] for row in result["results"]} == {p.name for p in backups.iterdir()
                                                          if p.name != "ignore.txt"}
    assert all(row["verified"] in {"md5", "sha256"} for row in result["results"])
    manifest = s3.buckets["netpulse-drill"]["netpulse/_netpulse_manifest.json"]
    assert b'"sha256"' in manifest and b"netpulse_self" in manifest

    check = offsite.verify_all()
    assert check["status"] == "ok", check
    assert check["checked"] == 3 and check["manifest"] == "present"
    assert check["missing"] == [] and check["mismatched"] == []

    dest = tmp_path / "restore"
    restored = offsite.restore_all(dest_dir=dest)
    assert restored["status"] == "ok", restored
    assert restored["restored"] == 3
    for path in backups.iterdir():
        if path.name == "ignore.txt":
            continue
        target = dest / path.name
        assert target.is_file()
        assert hashlib.sha256(target.read_bytes()).hexdigest() == \
            hashlib.sha256(path.read_bytes()).hexdigest()
    # La restauración no toca los backups locales.
    assert all((backups / row["name"]).is_file() for row in result["results"])


def test_s3_status_probe_informa_objetos_reales(s3):
    offsite.export_all()
    status = offsite.status(probe=True)
    assert status["configured"] is True
    assert status["last_export"]["status"] == "ok"
    assert status["last_export"]["uploaded"] == 3
    assert status["remote"]["reachable"] is True
    assert status["remote"]["files"] == 3  # backups publicados (sin manifiesto)
    assert status["remote"]["manifest"] is True


# ── S3: fallos honestos ──────────────────────────────────────


def test_s3_bucket_inexistente_no_pierde_backups(backups, monkeypatch):
    _s3_settings(monkeypatch)
    fake = FakeS3(buckets=())  # ningún bucket creado
    monkeypatch.setattr(offsite, "_s3_client", lambda: fake)

    result = offsite.export_all()
    assert result["status"] == "error"
    assert result["uploaded"] == 0
    assert result["error"]["code"] == "bucket_missing"
    assert result["local_intact"] is True
    assert all((backups / name).is_file() for name in (
        "core-1_20260101_120000.cfg",
        "core-1_20260101_120000.json",
        "netpulse_self_20260101_120000.tar.gz",
    ))


def test_s3_credenciales_malas(backups, monkeypatch):
    _s3_settings(monkeypatch)
    fake = FakeS3(auth_error=True)
    monkeypatch.setattr(offsite, "_s3_client", lambda: fake)
    result = offsite.export_all()
    assert result["status"] == "error"
    assert result["error"]["code"] == "auth_failed"
    assert "credenciales" in result["errors"][0]
    assert result["local_intact"] is True


def test_s3_endpoint_caido(backups, monkeypatch):
    _s3_settings(monkeypatch)

    class Down(FakeS3):
        def _bucket(self, name):
            raise EndpointConnectionError(endpoint_url="http://127.0.0.1:1")

    monkeypatch.setattr(offsite, "_s3_client", lambda: Down(buckets=()))
    result = offsite.export_all()
    assert result["status"] == "error"
    assert result["error"]["code"] == "endpoint_unreachable"
    assert result["error"]["retriable"] is True


def test_s3_verify_detecta_objeto_borrado_y_manipulado(s3):
    assert offsite.export_all()["status"] == "ok"
    bucket = s3.buckets["netpulse-drill"]
    del bucket["netpulse/core-1_20260101_120000.json"]
    bucket["netpulse/core-1_20260101_120000.cfg"] = b"hostname alterado\n"

    check = offsite.verify_all()
    assert check["status"] == "incomplete"
    assert check["ok"] == 1
    assert "core-1_20260101_120000.json" in check["missing"]
    assert "core-1_20260101_120000.cfg" in check["mismatched"]


def test_s3_restore_rechaza_objeto_manipulado(s3, backups, tmp_path):
    assert offsite.export_all()["status"] == "ok"
    s3.buckets["netpulse-drill"]["netpulse/core-1_20260101_120000.cfg"] = b"tampered\n"
    dest = tmp_path / "restore"
    result = offsite.restore_all(dest_dir=dest)
    assert result["status"] == "partial"
    assert result["restored"] == 2
    assert "SHA-256" in result["errors"][0] or "tamaño" in result["errors"][0]
    assert not (dest / "core-1_20260101_120000.cfg").exists()
    assert not list(dest.glob(".*.part"))
    # El backup local sigue intacto.
    assert (backups / "core-1_20260101_120000.cfg").read_bytes().startswith(b"hostname core-1")


def test_s3_restore_respeta_overwrite_false(s3, tmp_path):
    assert offsite.export_all()["status"] == "ok"
    dest = tmp_path / "restore"
    dest.mkdir()
    (dest / "core-1_20260101_120000.cfg").write_bytes(b"contenido local distinto")
    result = offsite.restore_all(dest_dir=dest)
    assert result["status"] == "partial"
    assert result["restored"] == 2
    assert any("ya existe" in error for error in result["errors"])
    assert (dest / "core-1_20260101_120000.cfg").read_bytes() == b"contenido local distinto"

    again = offsite.restore_all(dest_dir=dest, overwrite=True)
    assert again["status"] == "ok"
    assert again["restored"] == 3
    assert (dest / "core-1_20260101_120000.cfg").read_bytes().startswith(b"hostname core-1")


# ── SFTP ─────────────────────────────────────────────────────


def test_sftp_export_verify_restore(sftp, backups, tmp_path):
    result = offsite.export_all()
    assert result["status"] == "ok", result
    assert result["uploaded"] == 3
    assert result["manifest"] == "uploaded"
    assert all(row["verified"] == "sha256" for row in result["results"])
    assert "netpulse-backups/core-1_20260101_120000.cfg" in sftp.files

    check = offsite.verify_all()
    assert check["status"] == "ok", check
    assert check["manifest"] == "present"

    dest = tmp_path / "restore-sftp"
    restored = offsite.restore_all(dest_dir=dest)
    assert restored["status"] == "ok", restored
    assert restored["restored"] == 3
    for name in ("core-1_20260101_120000.cfg", "core-1_20260101_120000.json",
                 "netpulse_self_20260101_120000.tar.gz"):
        assert (dest / name).read_bytes() == (backups / name).read_bytes()


def test_sftp_credenciales_malas(backups, monkeypatch):
    _sftp_settings(monkeypatch)

    @contextmanager
    def bad_session():
        raise paramiko.AuthenticationException("Authentication failed")
        yield  # pragma: no cover

    monkeypatch.setattr(offsite, "_sftp_session", bad_session)
    result = offsite.export_all()
    assert result["status"] == "error"
    assert result["error"]["code"] == "auth_failed"
    assert result["local_intact"] is True


def test_sftp_objeto_manipulado_no_se_restaura(sftp, backups, tmp_path):
    assert offsite.export_all()["status"] == "ok"
    sftp.files["netpulse-backups/core-1_20260101_120000.json"] = b'{"hostname":"evil"}'
    check = offsite.verify_all()
    assert check["status"] == "incomplete"
    assert "core-1_20260101_120000.json" in check["mismatched"]

    dest = tmp_path / "restore"
    result = offsite.restore_all(dest_dir=dest)
    assert result["status"] == "partial"
    assert not (dest / "core-1_20260101_120000.json").exists()


def test_sftp_makedirs_es_idempotente():
    fake = FakeSFTP(root="/home/drill")
    offsite._sftp_makedirs(fake, "/home/drill/netpulse/a/b")
    assert "/home/drill/netpulse" in fake.dirs
    assert "/home/drill/netpulse/a" in fake.dirs
    assert "/home/drill/netpulse/a/b" in fake.dirs
    offsite._sftp_makedirs(fake, "/home/drill/netpulse/a/b")  # no lanza


# ── Cliente S3: timeouts reales ──────────────────────────────


def test_s3_client_configura_timeouts_y_un_solo_reintento(monkeypatch):
    _s3_settings(monkeypatch)
    captured = {}

    def fake_client(name, **kwargs):
        captured.update(kwargs)
        return object()

    import boto3
    monkeypatch.setattr(boto3, "client", fake_client)
    offsite._s3_client()
    cfg = captured["config"]
    assert cfg.connect_timeout == offsite.CONNECT_TIMEOUT
    assert cfg.read_timeout == offsite.READ_TIMEOUT
    assert cfg.retries["max_attempts"] == 1
    assert cfg.s3 == {"addressing_style": "path"}


# ── API ──────────────────────────────────────────────────────


@pytest.fixture
def api():
    return TestClient(app)


def test_api_status_probe_y_export(api, s3):
    res = api.get("/api/config/offsite/status", params={"probe": "true"}, headers=_admin())
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["configured"] is True
    assert body["remote"]["reachable"] is True
    assert body["remote"]["files"] == 0

    res = api.post("/api/config/offsite/export", headers=_admin())
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "ok"
    assert res.json()["uploaded"] == 3

    res = api.get("/api/config/offsite/status", headers=_admin())
    assert res.json()["last_export"]["status"] == "ok"


def test_api_export_fallido_devuelve_502_con_causa(api, backups, monkeypatch):
    _s3_settings(monkeypatch)
    monkeypatch.setattr(offsite, "_s3_client", lambda: FakeS3(buckets=()))
    res = api.post("/api/config/offsite/export", headers=_admin())
    assert res.status_code == 502, res.text
    body = res.json()
    assert "bucket" in body["detail"].lower()
    assert body["error"]["code"] == "bucket_missing"
    assert body["local_intact"] is True
    assert (backups / "core-1_20260101_120000.cfg").is_file()


def test_api_restore_rechaza_path_traversal(api, s3):
    for dest in ("../evil", "/tmp/evil", "sub/../../evil"):
        res = api.post("/api/config/offsite/restore", params={"dest": dest},
                       headers=_admin())
        assert res.status_code == 400, (dest, res.text)


def test_api_restore_endpoint(api, s3, tmp_path, monkeypatch):
    assert offsite.export_all()["status"] == "ok"
    monkeypatch.setattr(offsite, "RESTORE_DIR", tmp_path / "api-restore")
    res = api.post("/api/config/offsite/restore", params={"dest": "run-1"},
                   headers=_admin())
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["restored"] == 3
    restored = Path(body["dest"])
    assert restored == (tmp_path / "api-restore" / "run-1")
    assert (restored / "core-1_20260101_120000.cfg").is_file()


def test_api_verify_y_restore_requieren_admin(api, s3):
    viewer = create_access_token({"sub": "viewer", "role": "viewer"})
    headers = {"Authorization": f"Bearer {viewer}"}
    assert api.post("/api/config/offsite/verify", headers=headers).status_code == 403
    assert api.post("/api/config/offsite/restore", headers=headers).status_code == 403


def test_api_verify_incompleto_es_200_honesto(api, s3):
    assert offsite.export_all()["status"] == "ok"
    del s3.buckets["netpulse-drill"]["netpulse/core-1_20260101_120000.cfg"]
    res = api.post("/api/config/offsite/verify", headers=_admin())
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "incomplete"
    assert res.json()["missing"] == ["core-1_20260101_120000.cfg"]


def test_s3_upload_wrapper_se_clasifica_por_codigo():
    """boto3 envuelve ClientError; el código debe sobrevivir a la envoltura."""
    from boto3.exceptions import S3UploadFailedError

    missing = offsite._classify(
        S3UploadFailedError("fallo (NoSuchBucket): The specified bucket does not exist"),
        "s3",
    )
    assert missing.code == "bucket_missing"
    auth = offsite._classify(
        S3UploadFailedError("fallo (SignatureDoesNotMatch): signature mismatch"),
        "s3",
    )
    assert auth.code == "auth_failed"


def test_sftp_makedirs_sin_permiso_no_se_traga_el_error():
    class ReadOnlyFS:
        def stat(self, path):
            raise FileNotFoundError(path)

        def mkdir(self, path):
            raise PermissionError(13, "Permission denied", path)

    with pytest.raises(offsite.OffsiteError) as excinfo:
        offsite._sftp_makedirs(ReadOnlyFS(), "/netpulse-backups")
    assert excinfo.value.code == "permission_denied"
