"""NetPulse — Exportación y restauración offsite de backups.

Sube los backups locales (``backups/*.cfg``, ``*.json`` y los
``netpulse_self_*.tar.gz``) a un destino remoto (**SFTP** con paramiko o
**S3/MinIO** con boto3) y permite verificar y restaurar lo publicado.

Contrato resumido (detalle en ``docs/handoffs/backups-offsite.md``):

* Los objetos se guardan planos bajo ``NETPULSE_OFFSITE_S3_PREFIX`` (S3)
  o ``NETPULSE_OFFSITE_SFTP_DIR`` (SFTP), conservando el nombre local.
* Tras cada subida se comprueba el objeto remoto (tamaño y, cuando el
  backend lo expone, MD5/ETag o SHA-256). El resultado queda en
  ``export_all()["results"]``.
* Se publica un manifiesto ``_netpulse_manifest.json`` con tamaño y
  SHA-256 de cada archivo, para poder verificar la restauración incluso
  si el backup local ya no existe.
* ``restore_all()`` descarga a un directorio distinto de ``BACKUP_DIR``,
  verifica tamaño/SHA-256 y solo entonces reemplaza el archivo final.
  Nunca borra ni sobrescribe backups locales fuera de ese directorio.

El servicio está deshabilitado por defecto (``NETPULSE_OFFSITE_ENABLED``).
Sin destino configurado devuelve un estado informativo, no un fallo.
Los errores remotos se clasifican (credenciales, endpoint caído, bucket
inexistente, timeout...) para que la API responda con una causa clara en
lugar de un 500 genérico.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import socket
import stat
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.core import settings
from app.core.settings import BACKUP_DIR

logger = logging.getLogger(__name__)

MANIFEST_NAME = "_netpulse_manifest.json"
MANIFEST_VERSION = 1

# Tiempos de espera (segundos). Se leen de entorno para poder ajustarlos sin
# tocar código; los valores por defecto evitan cuelgues de minutos cuando el
# endpoint está caído.
CONNECT_TIMEOUT = float(os.getenv("NETPULSE_OFFSITE_CONNECT_TIMEOUT", "10"))
READ_TIMEOUT = float(os.getenv("NETPULSE_OFFSITE_READ_TIMEOUT", "60"))
SFTP_IO_TIMEOUT = float(os.getenv("NETPULSE_OFFSITE_SFTP_IO_TIMEOUT", "60"))
# Tope para calcular SHA-256 remoto por SFTP (no re-descargar backups enormes).
VERIFY_HASH_MAX_BYTES = int(
    os.getenv("NETPULSE_OFFSITE_VERIFY_HASH_MAX_BYTES", str(16 * 1024 * 1024))
)
# Destino de restauración. Deliberadamente fuera de BACKUP_DIR para no pisar
# los backups locales. El router solo permite subdirectorios relativos.
RESTORE_DIR = Path(
    os.getenv("NETPULSE_OFFSITE_RESTORE_DIR", str(BACKUP_DIR / "restored"))
)

# Códigos que inhabilitan el destino completo: no tiene sentido seguir con
# el resto de archivos ni publicar un manifiesto parcial como si fuese bueno.
FATAL_CODES = {
    "auth_failed",
    "endpoint_unreachable",
    "timeout",
    "dns_error",
    "bucket_missing",
    "permission_denied",
    "host_key_mismatch",
}

# Último resultado de exportación, para que /status informe de algo real.
_last_export: Optional[dict] = None


# ── Errores clasificados ─────────────────────────────────────


class OffsiteError(Exception):
    """Fallo remoto con una causa estable para la API."""

    def __init__(self, code: str, message: str, retriable: bool = False):
        self.code = code
        self.message = message
        self.retriable = retriable
        super().__init__(message)

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "retriable": self.retriable}


class OffsiteIntegrityError(OffsiteError):
    """El objeto remoto no coincide con el local."""

    def __init__(self, message: str):
        super().__init__("integrity_mismatch", message, retriable=False)


def _short(text: Any, limit: int = 300) -> str:
    value = str(text or "").strip().replace("\n", " ")
    return value[:limit]


def _classify(exc: BaseException, backend: str) -> OffsiteError:
    """Traduce excepciones de boto3/paramiko a códigos estables."""
    if isinstance(exc, OffsiteError):
        return exc

    # boto3 envuelve los fallos de upload_file en S3UploadFailedError sin
    # encadenar la ClientError: el código viaja dentro del mensaje.
    try:
        from boto3.exceptions import S3UploadFailedError
    except Exception:  # pragma: no cover
        S3UploadFailedError = ()  # type: ignore[assignment]
    if S3UploadFailedError and isinstance(exc, S3UploadFailedError):
        text = str(exc)
        match = re.search(r"\(([A-Za-z0-9_.-]+)\)", text)
        code = match.group(1) if match else ""
        lowered = text.lower()
        if code == "NoSuchBucket" or "bucket does not exist" in lowered:
            return OffsiteError("bucket_missing", f"bucket inexistente: {_short(text)}")
        if code in {
            "InvalidAccessKeyId", "SignatureDoesNotMatch", "AccessDenied",
            "InvalidToken", "ExpiredToken", "RequestTimeTooSkewed",
        } or "signature" in lowered or "access denied" in lowered:
            return OffsiteError("auth_failed", f"credenciales rechazadas: {_short(text)}")
        return OffsiteError("remote_error", _short(text))

    # boto3 / botocore
    try:
        from botocore.exceptions import (
            ClientError,
            ConnectTimeoutError,
            ConnectionClosedError,
            EndpointConnectionError,
            ProxyConnectionError,
            ReadTimeoutError,
        )
    except Exception:  # pragma: no cover - boto3 siempre está instalado
        ClientError = ()  # type: ignore[assignment]

    if ClientError and isinstance(exc, ClientError):
        response = getattr(exc, "response", {}) or {}
        err = response.get("Error", {}) or {}
        code = str(err.get("Code", ""))
        msg = _short(err.get("Message", ""))
        http_status = int(
            (response.get("ResponseMetadata", {}) or {}).get("HTTPStatusCode") or 0
        )
        lowered = f"{code} {msg}".lower()
        if code in {"NoSuchBucket"} or (code in {"404", "NoSuchBucket"} and "bucket" in lowered):
            return OffsiteError("bucket_missing", f"bucket inexistente: {msg or code}")
        if code in {"NoSuchKey", "NotFound"} or (code == "404" and "bucket" not in lowered):
            return OffsiteError("not_found", f"objeto no encontrado: {msg or code}")
        if code in {
            "InvalidAccessKeyId",
            "SignatureDoesNotMatch",
            "AccessDenied",
            "InvalidToken",
            "ExpiredToken",
            "TokenRefreshRequired",
            "RequestTimeTooSkewed",
        } or http_status in {401, 403}:
            return OffsiteError(
                "auth_failed",
                f"credenciales rechazadas: {msg or code or http_status}",
            )
        return OffsiteError("remote_error", f"{code or 'S3'}: {msg}" if msg else (code or str(exc)))

    if backend == "s3":
        name = type(exc).__name__
        if isinstance(exc, (ConnectTimeoutError, ReadTimeoutError, TimeoutError, socket.timeout)):
            return OffsiteError("timeout", f"timeout hablando con S3: {_short(exc)}", True)
        if isinstance(exc, (EndpointConnectionError, ConnectionClosedError, ProxyConnectionError)):
            return OffsiteError("endpoint_unreachable", f"endpoint S3 inaccesible: {_short(exc)}", True)
        if isinstance(exc, socket.gaierror):
            return OffsiteError("dns_error", f"no se resuelve el endpoint S3: {_short(exc)}", True)
        if isinstance(exc, ConnectionRefusedError):
            return OffsiteError("endpoint_unreachable", "conexión rechazada por el endpoint S3", True)
        if isinstance(exc, OSError) and exc.errno in {61, 111, 113}:
            return OffsiteError("endpoint_unreachable", f"endpoint S3 inaccesible: {_short(exc)}", True)
        return OffsiteError("remote_error", f"S3 {name}: {_short(exc)}")

    # paramiko / socket
    try:
        import paramiko
    except Exception:  # pragma: no cover - paramiko está en requirements
        paramiko = None  # type: ignore[assignment]

    if paramiko is not None:
        if isinstance(exc, paramiko.AuthenticationException):
            return OffsiteError("auth_failed", "credenciales SFTP rechazadas")
        if isinstance(exc, paramiko.BadHostKeyException):
            return OffsiteError("host_key_mismatch", "la clave del host SFTP no coincide")
        if isinstance(exc, (socket.timeout, TimeoutError)):
            return OffsiteError("timeout", "timeout hablando con SFTP", True)
        if isinstance(exc, socket.gaierror):
            return OffsiteError("dns_error", f"no se resuelve el host SFTP: {_short(exc)}", True)
        if isinstance(exc, (ConnectionRefusedError, OSError)) and getattr(exc, "errno", None) in {61, 111, 113}:
            return OffsiteError("endpoint_unreachable", "conexión rechazada por el SFTP", True)
        if isinstance(exc, paramiko.SSHException):
            return OffsiteError(
                "endpoint_unreachable",
                f"no se pudo establecer la sesión SFTP: {_short(exc)}",
                True,
            )
    if isinstance(exc, FileNotFoundError):
        return OffsiteError("not_found", _short(exc))
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return OffsiteError("timeout", _short(exc), True)
    if isinstance(exc, socket.gaierror):
        return OffsiteError("dns_error", _short(exc), True)
    if isinstance(exc, OSError) and getattr(exc, "errno", None) in {61, 111, 113}:
        return OffsiteError("endpoint_unreachable", f"endpoint SFTP inaccesible: {_short(exc)}", True)
    return OffsiteError("remote_error", f"{type(exc).__name__}: {_short(exc)}")


def http_status_for(result: dict) -> int:
    """Código HTTP honesto para un resultado de operación offsite."""
    status = result.get("status")
    error = result.get("error") or {}
    code = error.get("code")
    if status == "error":
        return 404 if code == "not_found" else 502
    if status == "empty":
        return 404
    return 200


# ── Estado ───────────────────────────────────────────────────


def is_enabled() -> bool:
    """True si el offsite está activo y tiene destino configurado."""
    if not settings.OFFSITE_ENABLED:
        return False
    if settings.OFFSITE_BACKEND == "s3":
        return bool(settings.OFFSITE_S3_BUCKET)
    if settings.OFFSITE_BACKEND == "sftp":
        return bool(settings.OFFSITE_SFTP_HOST)
    return False


def status(probe: bool = False) -> dict:
    """Estado del destino offsite (sin mostrar secretos).

    ``probe=True`` hace una comprobación real de conectividad y lista los
    objetos remotos; el default no toca la red (respuesta rápida y
    determinista).
    """
    backend = settings.OFFSITE_BACKEND
    out = {
        "enabled": settings.OFFSITE_ENABLED,
        "configured": is_enabled(),
        "backend": backend,
        "last_export": _last_export,
        "restore_dir": str(RESTORE_DIR),
    }
    if backend == "s3":
        out.update({
            "bucket": settings.OFFSITE_S3_BUCKET,
            "endpoint": settings.OFFSITE_S3_ENDPOINT or "AWS",
            "prefix": settings.OFFSITE_S3_PREFIX,
        })
    else:
        out.update({
            "host": settings.OFFSITE_SFTP_HOST,
            "port": settings.OFFSITE_SFTP_PORT,
            "dir": settings.OFFSITE_SFTP_DIR,
            "auth": "key" if settings.OFFSITE_SFTP_KEY else (
                "password" if settings.OFFSITE_SFTP_PASSWORD else "none"
            ),
        })
    if probe:
        out["remote"] = remote_status()
    return out


def remote_status() -> dict:
    """Comprueba el destino y cuenta objetos. Nunca lanza."""
    if not is_enabled():
        return {"reachable": False, "configured": False, "files": 0,
                "error": {"code": "not_configured",
                          "message": "offsite sin destino configurado",
                          "retriable": False}}
    try:
        if settings.OFFSITE_BACKEND == "s3":
            client = _s3_client()
            files = _remote_list_s3(client)
            manifest, manifest_warning = _read_manifest_s3(client)
        else:
            with _sftp_session() as sftp:
                files = _sftp_remote_list(sftp)
                manifest, manifest_warning = _sftp_read_manifest(sftp)
        return {
            "reachable": True,
            "configured": True,
            "files": len(files),
            "manifest": manifest is not None,
            "warnings": [manifest_warning] if manifest_warning else [],
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - se devuelve la causa clasificada
        error = _classify(exc, settings.OFFSITE_BACKEND)
        logger.warning("[offsite] probe %s: %s", error.code, error.message)
        return {"reachable": False, "configured": True, "files": None,
                "manifest": None, "error": error.as_dict()}


# ── Archivos locales ─────────────────────────────────────────


def _is_backup_name(name: str) -> bool:
    """Nombre de artefacto que el offsite publica (nunca el manifiesto)."""
    if name == MANIFEST_NAME or name.startswith("."):
        return False
    if name.endswith((".cfg", ".json")):
        return True
    return name.startswith("netpulse_self_") and name.endswith(".tar.gz")


def _local_files(device_id: Optional[str] = None) -> list[Path]:
    """Backups locales a exportar (todos o los de un dispositivo)."""
    try:
        candidates = [p for p in BACKUP_DIR.iterdir() if p.is_file() and not p.is_symlink()]
    except OSError:
        return []
    files = [p for p in candidates if _is_backup_name(p.name)]
    if device_id:
        prefix = _device_prefix(device_id)
        files = [f for f in files if f.name.startswith(prefix)]
    return sorted(files)


def _device_prefix(device_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in device_id)
    return f"{safe}_"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _md5_hex(value: str) -> Optional[str]:
    """MD5 de un ETag simple; ``None`` para multipart/SSE."""
    etag = (value or "").strip('"')
    if len(etag) == 32 and "-" not in etag and all(c in "0123456789abcdefABCDEF" for c in etag):
        return etag.lower()
    return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── S3 / MinIO ───────────────────────────────────────────────


def _s3_client():
    import boto3
    from botocore.config import Config

    cfg = Config(
        connect_timeout=CONNECT_TIMEOUT,
        read_timeout=READ_TIMEOUT,
        retries={"max_attempts": 1, "mode": "standard"},
        s3={"addressing_style": "path"} if settings.OFFSITE_S3_ENDPOINT else None,
    )
    return boto3.client(
        "s3",
        endpoint_url=settings.OFFSITE_S3_ENDPOINT or None,
        region_name=settings.OFFSITE_S3_REGION,
        aws_access_key_id=settings.OFFSITE_S3_ACCESS_KEY or None,
        aws_secret_access_key=settings.OFFSITE_S3_SECRET_KEY or None,
        config=cfg,
    )


def _s3_key(name: str) -> str:
    prefix = settings.OFFSITE_S3_PREFIX.strip("/")
    return f"{prefix}/{name}" if prefix else name


def _remote_list_s3(client) -> dict[str, dict]:
    bucket = settings.OFFSITE_S3_BUCKET
    prefix = settings.OFFSITE_S3_PREFIX.strip("/")
    kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": f"{prefix}/" if prefix else ""}
    out: dict[str, dict] = {}
    while True:
        response = client.list_objects_v2(**kwargs)
        for item in response.get("Contents", []) or []:
            key = item.get("Key", "")
            name = key.rsplit("/", 1)[-1]
            if not _is_backup_name(name):
                continue
            out[name] = {
                "name": name,
                "key": key,
                "path": key,
                "size": int(item.get("Size", 0)),
                "etag": item.get("ETag"),
                "mtime": item.get("LastModified"),
            }
        if not response.get("IsTruncated"):
            break
        kwargs["ContinuationToken"] = response.get("NextContinuationToken")
    return out


def _s3_download(client, key: str, target: Path) -> None:
    response = client.get_object(Bucket=settings.OFFSITE_S3_BUCKET, Key=key)
    body = response["Body"]
    with target.open("wb") as fh:
        while True:
            chunk = body.read(1024 * 1024)
            if not chunk:
                break
            fh.write(chunk)


def _read_manifest_s3(client) -> tuple[Optional[dict], Optional[str]]:
    key = _s3_key(MANIFEST_NAME)
    try:
        response = client.get_object(Bucket=settings.OFFSITE_S3_BUCKET, Key=key)
        raw = response["Body"].read()
    except Exception as exc:  # noqa: BLE001 - manifiesto opcional
        error = _classify(exc, "s3")
        if error.code == "not_found":
            return None, None
        return None, f"manifiesto ilegible: {error.message}"
    return _parse_manifest(raw)


# ── SFTP ─────────────────────────────────────────────────────


@contextmanager
def _sftp_session():
    """Sesión SFTP con timeout de conexión real (no cuelga si el host no responde)."""
    import paramiko

    sock = socket.create_connection(
        (settings.OFFSITE_SFTP_HOST, settings.OFFSITE_SFTP_PORT),
        timeout=CONNECT_TIMEOUT,
    )
    transport = paramiko.Transport(sock)
    transport.banner_timeout = CONNECT_TIMEOUT
    transport.auth_timeout = CONNECT_TIMEOUT
    sftp = None
    try:
        if settings.OFFSITE_SFTP_KEY:
            key = _load_private_key(settings.OFFSITE_SFTP_KEY)
            transport.connect(username=settings.OFFSITE_SFTP_USER, pkey=key)
        else:
            transport.connect(
                username=settings.OFFSITE_SFTP_USER,
                password=settings.OFFSITE_SFTP_PASSWORD,
            )
        sftp = paramiko.SFTPClient.from_transport(transport)
        sftp.get_channel().settimeout(SFTP_IO_TIMEOUT)
        remote_dir = settings.OFFSITE_SFTP_DIR
        _sftp_makedirs(sftp, remote_dir)
        yield sftp
    finally:
        if sftp is not None:
            try:
                sftp.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            transport.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            sock.close()
        except Exception:  # noqa: BLE001
            pass


def _load_private_key(path: str):
    import paramiko

    for cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
        try:
            return cls.from_private_key_file(path)
        except Exception:  # noqa: BLE001
            continue
    raise OffsiteError("auth_failed", f"clave privada no soportada: {path}")


def _sftp_join(name: str) -> str:
    remote_dir = settings.OFFSITE_SFTP_DIR.rstrip("/") or "."
    return f"{remote_dir}/{name}" if remote_dir != "." else name


def _sftp_makedirs(sftp, remote_dir: str) -> None:
    parts = [p for p in remote_dir.split("/") if p]
    cur = "" if not remote_dir.startswith("/") else "/"
    for part in parts:
        cur = f"{cur}{part}" if cur.endswith("/") or cur == "" else f"{cur}/{part}"
        try:
            sftp.stat(cur)
        except OSError:
            try:
                sftp.mkdir(cur)
            except OSError as exc:
                # Otro proceso pudo crearlo entre stat y mkdir.
                try:
                    sftp.stat(cur)
                except OSError:
                    raise OffsiteError(
                        "permission_denied",
                        f"no se puede crear el directorio remoto {cur}: {_short(exc)}",
                    ) from exc
        if not cur.endswith("/"):
            cur += "/"


def _sftp_remote_list(sftp) -> dict[str, dict]:
    remote_dir = settings.OFFSITE_SFTP_DIR
    try:
        attrs = sftp.listdir_attr(remote_dir)
    except OSError:
        return {}
    out: dict[str, dict] = {}
    for attr in attrs:
        name = attr.filename
        if not _is_backup_name(name):
            continue
        if not stat.S_ISREG(attr.st_mode or 0):
            continue
        out[name] = {
            "name": name,
            "key": name,
            "path": _sftp_join(name),
            "size": int(attr.st_size),
            "etag": None,
            "mtime": getattr(attr, "st_mtime", None),
        }
    return out


def _sftp_read_manifest(sftp) -> tuple[Optional[dict], Optional[str]]:
    path = _sftp_join(MANIFEST_NAME)
    try:
        with sftp.open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return None, None
    return _parse_manifest(raw)


def _sftp_sha256(sftp, path: str, size: int) -> Optional[str]:
    if size > VERIFY_HASH_MAX_BYTES:
        return None
    digest = hashlib.sha256()
    with sftp.open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ── Manifiesto ───────────────────────────────────────────────


def _parse_manifest(raw: Any) -> tuple[Optional[dict], Optional[str]]:
    try:
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict) or not isinstance(data.get("files"), list):
            return None, "manifiesto con formato inválido"
        entries = {
            item.get("name"): item
            for item in data["files"]
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        return {"version": data.get("version", MANIFEST_VERSION),
                "generated_at": data.get("generated_at"),
                "files": entries}, None
    except (ValueError, TypeError) as exc:
        return None, f"manifiesto ilegible: {_short(exc)}"


def _build_manifest(entries: dict[str, dict]) -> bytes:
    body = {
        "version": MANIFEST_VERSION,
        "generated_at": _now(),
        "files": sorted(entries.values(), key=lambda item: item["name"]),
    }
    return json.dumps(body, indent=2, sort_keys=True).encode("utf-8")


def _upload_manifest_s3(client, entries: dict[str, dict]) -> None:
    client.put_object(
        Bucket=settings.OFFSITE_S3_BUCKET,
        Key=_s3_key(MANIFEST_NAME),
        Body=_build_manifest(entries),
        ContentType="application/json",
    )


def _upload_manifest_sftp(sftp, entries: dict[str, dict]) -> None:
    body = _build_manifest(entries)
    with sftp.open(_sftp_join(MANIFEST_NAME), "wb") as fh:
        fh.write(body)


# ── Exportación ──────────────────────────────────────────────


def _upload_s3_one(client, path: Path, expected: dict) -> dict:
    key = _s3_key(path.name)
    client.upload_file(str(path), settings.OFFSITE_S3_BUCKET, key)
    head = client.head_object(Bucket=settings.OFFSITE_S3_BUCKET, Key=key)
    remote_size = int(head.get("ContentLength", -1))
    etag = head.get("ETag")
    if remote_size != expected["size"]:
        raise OffsiteIntegrityError(
            f"{path.name}: tamaño remoto {remote_size} != local {expected['size']}"
        )
    remote_md5 = _md5_hex(etag or "")
    if remote_md5 and remote_md5 != expected["md5"]:
        raise OffsiteIntegrityError(
            f"{path.name}: MD5 remoto {remote_md5} != local {expected['md5']}"
        )
    return {
        "name": path.name,
        "remote_key": key,
        "size": remote_size,
        "sha256": expected["sha256"],
        "etag": etag,
        "verified": "md5" if remote_md5 else "size",
    }


def _upload_sftp_one(sftp, path: Path, expected: dict) -> dict:
    remote_path = _sftp_join(path.name)
    sftp.put(str(path), remote_path)
    attr = sftp.stat(remote_path)
    remote_size = int(attr.st_size)
    if remote_size != expected["size"]:
        raise OffsiteIntegrityError(
            f"{path.name}: tamaño remoto {remote_size} != local {expected['size']}"
        )
    remote_sha = _sftp_sha256(sftp, remote_path, remote_size)
    if remote_sha and remote_sha != expected["sha256"]:
        raise OffsiteIntegrityError(f"{path.name}: SHA-256 remoto != local")
    return {
        "name": path.name,
        "remote_key": remote_path,
        "size": remote_size,
        "sha256": expected["sha256"],
        "etag": None,
        "verified": "sha256" if remote_sha else "size",
    }


def export_all(device_id: Optional[str] = None) -> dict:
    """Sube los backups locales al destino offsite y verifica cada objeto.

    Returns:
        ``{status, backend, uploaded, failed, errors, warnings, files,
        results, manifest, local_intact, ...}``. ``status`` es ``ok``,
        ``partial``, ``error`` o ``skipped``.
    """
    global _last_export
    backend = settings.OFFSITE_BACKEND

    if not settings.OFFSITE_ENABLED:
        return _finish({"backend": backend, "status": "skipped", "uploaded": 0,
                        "failed": 0, "errors": [], "warnings": [],
                        "skipped": "offsite deshabilitado"})
    if not is_enabled():
        return _finish({"backend": backend, "status": "skipped", "uploaded": 0,
                        "failed": 0, "errors": [], "warnings": [],
                        "skipped": "offsite sin destino configurado"})

    files = _local_files(device_id)
    if not files:
        return _finish({"backend": backend, "status": "ok", "uploaded": 0,
                        "failed": 0, "errors": [], "warnings": [], "files": [],
                        "results": [], "skipped": "sin backups"})

    results: list[dict] = []
    errors: list[str] = []
    warnings: list[str] = []
    fatal: Optional[OffsiteError] = None
    manifest_state = "skipped"
    expected_by_name: dict[str, dict] = {}

    try:
        if backend == "s3":
            client = _s3_client()
            existing, manifest_warning = _read_manifest_s3(client)
            if manifest_warning:
                warnings.append(manifest_warning)
            entries = dict((existing or {}).get("files", {}))
            for path in files:
                expected = _expected(path)
                expected_by_name[path.name] = expected
                try:
                    info = _upload_s3_one(client, path, expected)
                    results.append(info)
                    entries[path.name] = _manifest_entry(info)
                except Exception as exc:  # noqa: BLE001 - se clasifica por archivo
                    error = _classify(exc, backend)
                    errors.append(f"{path.name}: {error.message}")
                    if error.code in FATAL_CODES:
                        fatal = error
                        break
            if results:
                try:
                    _upload_manifest_s3(client, entries)
                    manifest_state = "uploaded"
                except Exception as exc:  # noqa: BLE001
                    error = _classify(exc, backend)
                    warnings.append(f"manifiesto: {error.message}")
                    manifest_state = "failed"
        elif backend == "sftp":
            with _sftp_session() as sftp:
                existing, manifest_warning = _sftp_read_manifest(sftp)
                if manifest_warning:
                    warnings.append(manifest_warning)
                entries = dict((existing or {}).get("files", {}))
                for path in files:
                    expected = _expected(path)
                    expected_by_name[path.name] = expected
                    try:
                        info = _upload_sftp_one(sftp, path, expected)
                        results.append(info)
                        entries[path.name] = _manifest_entry(info)
                    except Exception as exc:  # noqa: BLE001
                        error = _classify(exc, backend)
                        errors.append(f"{path.name}: {error.message}")
                        if error.code in FATAL_CODES:
                            fatal = error
                            break
                if results:
                    try:
                        _upload_manifest_sftp(sftp, entries)
                        manifest_state = "uploaded"
                    except Exception as exc:  # noqa: BLE001
                        error = _classify(exc, backend)
                        warnings.append(f"manifiesto: {error.message}")
                        manifest_state = "failed"
        else:
            fatal = OffsiteError("not_configured", f"backend offsite desconocido: {backend}")
    except Exception as exc:  # noqa: BLE001 - error de conexión/global
        fatal = _classify(exc, backend)
        errors.append(fatal.message)

    local_intact = _locals_intact(files, expected_by_name)
    if (fatal is not None or errors) and not results:
        result_status = "error"
    elif errors or warnings or fatal is not None:
        result_status = "partial"
    else:
        result_status = "ok"

    result = {
        "backend": backend,
        "status": result_status,
        "uploaded": len(results),
        "failed": len(errors),
        "errors": errors[:20],
        "warnings": warnings[:20],
        "files": [item.get("remote_key") or item["name"] for item in results][:50],
        "results": results,
        "manifest": manifest_state,
        "local_intact": local_intact,
    }
    if fatal is not None:
        result["error"] = fatal.as_dict()
    elif errors:
        result["error"] = {"code": "partial_failure",
                           "message": "algunos backups no se publicaron",
                           "retriable": True}
    return _finish(result)


def _expected(path: Path) -> dict:
    return {
        "size": path.stat().st_size,
        "sha256": _sha256_file(path),
        "md5": hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest(),
        "mtime": path.stat().st_mtime,
    }


def _manifest_entry(info: dict) -> dict:
    return {
        "name": info["name"],
        "size": info["size"],
        "sha256": info["sha256"],
        "uploaded_at": _now(),
    }


def _locals_intact(files: list[Path], expected_by_name: dict[str, dict]) -> bool:
    """Comprueba que la exportación no tocó (ni perdió) ningún local."""
    for path in files:
        try:
            if not path.is_file():
                return False
            expected = expected_by_name.get(path.name)
            if expected is None:
                continue
            if path.stat().st_size != expected["size"]:
                return False
            if _sha256_file(path) != expected["sha256"]:
                return False
        except OSError:
            return False
    return True


def _finish(result: dict) -> dict:
    """Publica el último resultado (sin listas gigantes) en /status."""
    global _last_export
    _last_export = {
        "at": _now(),
        "backend": result.get("backend"),
        "status": result.get("status"),
        "uploaded": result.get("uploaded", 0),
        "failed": result.get("failed", 0),
        "manifest": result.get("manifest"),
        "local_intact": result.get("local_intact"),
    }
    return result


# ── Verificación ─────────────────────────────────────────────


def verify_all(device_id: Optional[str] = None) -> dict:
    """Compara los backups locales con lo publicado (existencia/tamaño/hash)."""
    backend = settings.OFFSITE_BACKEND
    if not settings.OFFSITE_ENABLED:
        return {"backend": backend, "status": "skipped", "checked": 0,
                "missing": [], "mismatched": [], "results": [],
                "error": {"code": "disabled", "message": "offsite deshabilitado",
                          "retriable": False}}
    if not is_enabled():
        return {"backend": backend, "status": "skipped", "checked": 0,
                "missing": [], "mismatched": [], "results": [],
                "error": {"code": "not_configured",
                          "message": "offsite sin destino configurado", "retriable": False}}

    files = _local_files(device_id)
    if not files:
        return {"backend": backend, "status": "ok", "checked": 0, "missing": [],
                "mismatched": [], "results": [], "error": None,
                "skipped": "sin backups"}

    missing: list[str] = []
    mismatched: list[str] = []
    results: list[dict] = []
    error: Optional[OffsiteError] = None
    manifest_missing = False
    try:
        if backend == "s3":
            client = _s3_client()
            remote = _remote_list_s3(client)
            manifest, manifest_warning = _read_manifest_s3(client)
            for path in files:
                expected = _expected(path)
                info = remote.get(path.name)
                row = _verify_row(path, expected, info, remote_md5_key="etag")
                if manifest is not None:
                    me = manifest["files"].get(path.name)
                    if me and me.get("sha256") and me["sha256"] != expected["sha256"]:
                        row["local_changed"] = True
                        row["ok"] = False
                    elif me is None:
                        row["manifest_missing"] = True
                        manifest_missing = True
                results.append(row)
                _tally(row, missing, mismatched)
            manifest_missing = manifest_missing or manifest is None
        elif backend == "sftp":
            with _sftp_session() as sftp:
                remote = _sftp_remote_list(sftp)
                manifest, manifest_warning = _sftp_read_manifest(sftp)
                for path in files:
                    expected = _expected(path)
                    info = remote.get(path.name)
                    row = _verify_row(path, expected, info, remote_md5_key=None)
                    if info is not None:
                        try:
                            remote_sha = _sftp_sha256(sftp, info["path"], info["size"])
                        except Exception as exc:  # noqa: BLE001
                            remote_sha = None
                            row["warning"] = _classify(exc, "sftp").message
                        if remote_sha and remote_sha != expected["sha256"]:
                            row["ok"] = False
                            row["remote_sha256"] = remote_sha
                    results.append(row)
                    _tally(row, missing, mismatched)
                manifest_missing = manifest is None
        else:
            raise OffsiteError("not_configured", f"backend offsite desconocido: {backend}")
    except Exception as exc:  # noqa: BLE001
        error = _classify(exc, backend)
        return {"backend": backend, "status": "error", "checked": 0,
                "missing": [], "mismatched": [], "results": [],
                "error": error.as_dict()}

    return {
        "backend": backend,
        "status": "ok" if not missing and not mismatched else "incomplete",
        "checked": len(files),
        "ok": len(files) - len(missing) - len(mismatched),
        "missing": missing,
        "mismatched": mismatched,
        "manifest": "missing" if manifest_missing else "present",
        "warnings": [manifest_warning] if manifest_warning else [],
        "results": results,
        "error": None,
    }


def _verify_row(path: Path, expected: dict, info: Optional[dict],
                remote_md5_key: Optional[str]) -> dict:
    row = {
        "name": path.name,
        "local_size": expected["size"],
        "local_sha256": expected["sha256"],
        "remote_size": (info or {}).get("size"),
        "remote_key": (info or {}).get("key"),
        "ok": False,
    }
    if info is None:
        row["reason"] = "missing"
        return row
    if info["size"] != expected["size"]:
        row["reason"] = "size"
        return row
    if remote_md5_key:
        remote_md5 = _md5_hex(info.get(remote_md5_key) or "")
        if remote_md5 and remote_md5 != expected["md5"]:
            row["reason"] = "md5"
            row["remote_md5"] = remote_md5
            return row
        row["verified"] = "md5" if remote_md5 else "size"
    else:
        row["verified"] = "size"
    row["ok"] = True
    return row


def _tally(row: dict, missing: list[str], mismatched: list[str]) -> None:
    if row["ok"]:
        return
    if row.get("reason") == "missing":
        missing.append(row["name"])
    else:
        mismatched.append(row["name"])


# ── Restauración ─────────────────────────────────────────────


def restore_all(device_id: Optional[str] = None,
                dest_dir: Optional[Path | str] = None,
                overwrite: bool = False) -> dict:
    """Restaura los objetos remotos en ``dest_dir`` (por defecto ``RESTORE_DIR``).

    Descarga a ``<destino>/.<nombre>.part``, verifica tamaño y SHA-256 contra
    el manifiesto (o contra el backup local si existe) y solo entonces
    publica el archivo con ``os.replace``. Nunca toca ``BACKUP_DIR`` salvo
    que el llamador apunte explícitamente ahí.
    """
    backend = settings.OFFSITE_BACKEND
    dest = Path(dest_dir) if dest_dir is not None else RESTORE_DIR

    if not settings.OFFSITE_ENABLED:
        return {"backend": backend, "status": "skipped", "restored": 0, "failed": 0,
                "dest": str(dest), "files": [], "errors": [],
                "error": {"code": "disabled", "message": "offsite deshabilitado",
                          "retriable": False}}
    if not is_enabled():
        return {"backend": backend, "status": "skipped", "restored": 0, "failed": 0,
                "dest": str(dest), "files": [], "errors": [],
                "error": {"code": "not_configured",
                          "message": "offsite sin destino configurado", "retriable": False}}

    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"backend": backend, "status": "error", "restored": 0, "failed": 0,
                "dest": str(dest), "files": [], "errors": [_short(exc)],
                "error": {"code": "local_io", "message": _short(exc), "retriable": False}}

    restored: list[dict] = []
    errors: list[str] = []
    fatal: Optional[OffsiteError] = None
    manifest_state = "missing"
    prefix = _device_prefix(device_id) if device_id else None

    try:
        if backend == "s3":
            client = _s3_client()
            remote = _remote_list_s3(client)
            manifest, manifest_warning = _read_manifest_s3(client)
        elif backend == "sftp":
            with _sftp_session() as sftp:
                return _restore_with_sftp(
                    sftp, dest, prefix, overwrite, backend
                )
        else:
            fatal = OffsiteError("not_configured", f"backend offsite desconocido: {backend}")
            remote, manifest, manifest_warning = {}, None, None
    except Exception as exc:  # noqa: BLE001
        fatal = _classify(exc, backend)
        return {"backend": backend, "status": "error", "restored": 0, "failed": 0,
                "dest": str(dest), "files": [], "errors": [fatal.message],
                "error": fatal.as_dict()}

    if fatal is not None:
        return {"backend": backend, "status": "error", "restored": 0, "failed": 0,
                "dest": str(dest), "files": [], "errors": [fatal.message],
                "error": fatal.as_dict()}
    if manifest is not None:
        manifest_state = "present"
    selected = [
        name for name in remote
        if name != MANIFEST_NAME and (prefix is None or name.startswith(prefix))
    ]
    selected.sort()
    if not selected:
        return {"backend": backend, "status": "empty", "restored": 0, "failed": 0,
                "dest": str(dest), "files": [], "errors": [],
                "manifest": manifest_state,
                "error": {"code": "not_found",
                          "message": "no hay backups remotos" + (f" para {device_id}" if device_id else ""),
                          "retriable": False}}

    for name in selected:
        info = remote[name]
        try:
            row = _restore_s3_one(client, info, dest, manifest, overwrite)
            restored.append(row)
        except Exception as exc:  # noqa: BLE001
            error = _classify(exc, backend)
            errors.append(f"{name}: {error.message}")

    status = "ok" if not errors else ("partial" if restored else "error")
    result = {"backend": backend, "status": status,
              "restored": len(restored), "failed": len(errors),
              "dest": str(dest), "files": restored, "errors": errors[:20],
              "manifest": manifest_state,
              "warnings": [manifest_warning] if manifest_warning else [],
              "error": None}
    if errors and not restored:
        result["error"] = {"code": "restore_failed",
                           "message": errors[0], "retriable": True}
    return result


def _restore_s3_one(client, info: dict, dest: Path, manifest: Optional[dict],
                    overwrite: bool) -> dict:
    name = info["name"]
    target = dest / name
    part = dest / f".{name}.part"
    expected = (manifest or {}).get("files", {}).get(name)
    try:
        _s3_download(client, info["key"], part)
        row = _verify_downloaded(part, info["size"], expected, target, overwrite)
        os.replace(part, target)
        row["dest"] = str(target)
        return row
    finally:
        if part.exists():
            try:
                part.unlink()
            except OSError:
                pass


def _restore_with_sftp(sftp, dest: Path, prefix: Optional[str], overwrite: bool,
                       backend: str) -> dict:
    remote = _sftp_remote_list(sftp)
    manifest, manifest_warning = _sftp_read_manifest(sftp)
    selected = [
        name for name in remote
        if name != MANIFEST_NAME and (prefix is None or name.startswith(prefix))
    ]
    selected.sort()
    if not selected:
        return {"backend": backend, "status": "empty", "restored": 0, "failed": 0,
                "dest": str(dest), "files": [], "errors": [],
                "manifest": "present" if manifest else "missing",
                "error": {"code": "not_found",
                          "message": "no hay backups remotos", "retriable": False}}

    restored: list[dict] = []
    errors: list[str] = []
    for name in selected:
        info = remote[name]
        part = dest / f".{name}.part"
        try:
            sftp.get(info["path"], str(part))
            expected = (manifest or {}).get("files", {}).get(name)
            row = _verify_downloaded(part, info["size"], expected, dest / name, overwrite)
            os.replace(part, dest / name)
            row["dest"] = str(dest / name)
            restored.append(row)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {_classify(exc, backend).message}")
        finally:
            if part.exists():
                try:
                    part.unlink()
                except OSError:
                    pass

    status = "ok" if not errors else ("partial" if restored else "error")
    result = {"backend": backend, "status": status,
              "restored": len(restored), "failed": len(errors),
              "dest": str(dest), "files": restored, "errors": errors[:20],
              "manifest": "present" if manifest else "missing",
              "warnings": [manifest_warning] if manifest_warning else [],
              "error": None}
    if errors and not restored:
        result["error"] = {"code": "restore_failed",
                           "message": errors[0], "retriable": True}
    return result


def _verify_downloaded(part: Path, remote_size: int, expected: Optional[dict],
                       target: Path, overwrite: bool) -> dict:
    size = part.stat().st_size
    if size != remote_size:
        raise OffsiteIntegrityError(
            f"{target.name}: descarga incompleta ({size} != {remote_size})"
        )
    sha = _sha256_file(part)
    if expected and expected.get("size") is not None and int(expected["size"]) != size:
        raise OffsiteIntegrityError(f"{target.name}: tamaño no coincide con el manifiesto")
    if expected and expected.get("sha256") and expected["sha256"] != sha:
        raise OffsiteIntegrityError(f"{target.name}: SHA-256 no coincide con el manifiesto")
    if target.exists():
        if _sha256_file(target) != sha and not overwrite:
            raise OffsiteIntegrityError(
                f"{target.name}: ya existe con otro contenido (usar overwrite)"
            )
    return {
        "name": target.name,
        "size": size,
        "sha256": sha,
        "verified": "sha256" if expected and expected.get("sha256") else "size",
    }


__all__ = [
    "MANIFEST_NAME",
    "OffsiteError",
    "export_all",
    "http_status_for",
    "is_enabled",
    "restore_all",
    "status",
    "verify_all",
]
