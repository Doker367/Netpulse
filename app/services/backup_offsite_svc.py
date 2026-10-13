"""NetPulse — Exportación offsite de backups.

Sube los backups locales (``backups/*.cfg``/``*.json``) a un destino
remoto: **SFTP** (paramiko) o **S3/MinIO** (boto3). Deshabilitado por
defecto (``NETPULSE_OFFSITE_ENABLED``); sin config activa, ``export_all``
devuelve un estado informativo y no falla.
"""

import logging
from pathlib import Path
from typing import Optional

from app.core import settings
from app.core.settings import BACKUP_DIR

logger = logging.getLogger(__name__)


def is_enabled() -> bool:
    """True si el offsite está activo y tiene destino configurado."""
    if not settings.OFFSITE_ENABLED:
        return False
    if settings.OFFSITE_BACKEND == "s3":
        return bool(settings.OFFSITE_S3_BUCKET)
    return bool(settings.OFFSITE_SFTP_HOST)


def status() -> dict:
    """Estado del destino offsite (sin mostrar secretos)."""
    backend = settings.OFFSITE_BACKEND
    out = {
        "enabled": settings.OFFSITE_ENABLED,
        "configured": is_enabled(),
        "backend": backend,
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
            "auth": "key" if settings.OFFSITE_SFTP_KEY else ("password" if settings.OFFSITE_SFTP_PASSWORD else "none"),
        })
    return out


def _local_files(device_id: Optional[str] = None) -> list[Path]:
    """Backups locales a exportar (todos o los de un dispositivo)."""
    files = list(BACKUP_DIR.glob("*.cfg")) + list(BACKUP_DIR.glob("*.json"))
    if device_id:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in device_id)
        files = [f for f in files if f.name.startswith(f"{safe}_")]
    return sorted(files)


def export_all(device_id: Optional[str] = None) -> dict:
    """Sube los backups locales al destino offsite.

    Returns:
        ``{backend, uploaded, failed, errors, files}``.
    """
    if not settings.OFFSITE_ENABLED:
        return {"backend": settings.OFFSITE_BACKEND, "uploaded": 0, "failed": 0,
                "errors": [], "skipped": "offsite deshabilitado"}
    backend = settings.OFFSITE_BACKEND
    files = _local_files(device_id)
    if not files:
        return {"backend": backend, "uploaded": 0, "failed": 0, "errors": [],
                "files": [], "skipped": "sin backups"}

    uploaded: list[str] = []
    errors: list[str] = []
    try:
        if backend == "s3":
            _export_s3(files, uploaded, errors)
        else:
            _export_sftp(files, uploaded, errors)
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc))
    return {
        "backend": backend,
        "uploaded": len(uploaded),
        "failed": len(errors),
        "errors": errors[:20],
        "files": uploaded[:50],
    }


# ── SFTP ─────────────────────────────────────────────────────


def _export_sftp(files: list[Path], uploaded: list[str], errors: list[str]) -> None:
    import paramiko

    transport = paramiko.Transport((settings.OFFSITE_SFTP_HOST, settings.OFFSITE_SFTP_PORT))
    try:
        if settings.OFFSITE_SFTP_KEY:
            key = _load_private_key(settings.OFFSITE_SFTP_KEY)
            transport.connect(username=settings.OFFSITE_SFTP_USER, pkey=key)
        else:
            transport.connect(username=settings.OFFSITE_SFTP_USER,
                              password=settings.OFFSITE_SFTP_PASSWORD)
        sftp = paramiko.SFTPClient.from_transport(transport)
        remote_dir = settings.OFFSITE_SFTP_DIR
        _sftp_makedirs(sftp, remote_dir)
        for f in files:
            try:
                sftp.put(str(f), f"{remote_dir}/{f.name}")
                uploaded.append(f.name)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{f.name}: {exc}")
        sftp.close()
    finally:
        transport.close()


def _load_private_key(path: str):
    import paramiko

    for cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
        try:
            return cls.from_private_key_file(path)
        except Exception:  # noqa: BLE001
            continue
    raise ValueError(f"clave privada no soportada: {path}")


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
            except OSError:
                pass
        if not cur.endswith("/"):
            cur += "/"


# ── S3 / MinIO ───────────────────────────────────────────────


def _export_s3(files: list[Path], uploaded: list[str], errors: list[str]) -> None:
    import boto3
    from botocore.config import Config

    client = boto3.client(
        "s3",
        endpoint_url=settings.OFFSITE_S3_ENDPOINT or None,
        region_name=settings.OFFSITE_S3_REGION,
        aws_access_key_id=settings.OFFSITE_S3_ACCESS_KEY or None,
        aws_secret_access_key=settings.OFFSITE_S3_SECRET_KEY or None,
        config=Config(s3={"addressing_style": "path"} if settings.OFFSITE_S3_ENDPOINT else None),
    )
    bucket = settings.OFFSITE_S3_BUCKET
    prefix = settings.OFFSITE_S3_PREFIX.strip("/")
    for f in files:
        key = f"{prefix}/{f.name}" if prefix else f.name
        try:
            client.upload_file(str(f), bucket, key)
            uploaded.append(key)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{f.name}: {exc}")
