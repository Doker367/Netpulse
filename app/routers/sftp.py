"""NetPulse — Router SFTP para transferencia de archivos con equipos de red.

Permite explorar el sistema de archivos remoto del dispositivo,
subir archivos (firmwares, configs, scripts) y descargarlos.
"""

import io
import logging
import os
import stat

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.middleware.auth import JWTBearer
from app.middleware.rbac import requires_role
from app.services import napalm_svc

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/sftp",
    tags=["SFTP"],
    dependencies=[Depends(JWTBearer()), Depends(requires_role("operator"))],
)


def _get_ssh_client_and_sftp(device_id: str):
    """Crea una sesión SSH y cliente SFTP contra el dispositivo indicado."""
    try:
        import paramiko
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Librería paramiko no disponible en el servidor: {exc}",
        ) from exc

    device = napalm_svc._find_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail=f"Dispositivo '{device_id}' no encontrado.")

    creds = device.get("credentials", {}) or {}
    host = device.get("hostname") or device.get("ip") or ""
    port = int(device.get("cli_port") or device.get("port") or 22)
    username = creds.get("username") or "admin"
    password = creds.get("password") or ""

    if not host:
        raise HTTPException(status_code=400, detail="El dispositivo no tiene hostname ni IP configurado.")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            host,
            port=port,
            username=username,
            password=password,
            timeout=10,
            banner_timeout=15,
            auth_timeout=15,
            look_for_keys=False,
            allow_agent=False,
        )
        sftp = client.open_sftp()
        return client, sftp
    except Exception as exc:
        try:
            client.close()
        except Exception:
            pass
        logger.error("[sftp] Error conectando a %s (%s:%s): %s", device_id, host, port, exc)
        raise HTTPException(
            status_code=502,
            detail=f"Error conectando por SFTP al equipo ({host}:{port}): {exc}",
        ) from exc


@router.get("/{device_id}/list")
def list_remote_directory(device_id: str, path: str = Query("/", description="Ruta remota a listar")):
    """Lista el contenido de un directorio remoto en el dispositivo."""
    client, sftp = _get_ssh_client_and_sftp(device_id)
    try:
        # Si el path está vacío o relativo, normalizar a /
        remote_path = path.strip() or "/"
        try:
            # Obtener atributos de los archivos
            file_attrs = sftp.listdir_attr(remote_path)
        except IOError as io_err:
            raise HTTPException(
                status_code=400,
                detail=f"No se pudo acceder a la ruta '{remote_path}': {io_err}",
            ) from io_err

        items = []
        for attr in file_attrs:
            is_dir = stat.S_ISDIR(attr.st_mode) if attr.st_mode is not None else False
            is_link = stat.S_ISLNK(attr.st_mode) if attr.st_mode is not None else False
            items.append({
                "name": attr.filename,
                "is_dir": is_dir,
                "is_link": is_link,
                "size": attr.st_size or 0,
                "mtime": attr.st_mtime or 0,
                "permissions": oct(attr.st_mode)[-4:] if attr.st_mode is not None else "",
            })

        # Ordenar: primero carpetas alfabéticamente, luego archivos alfabéticamente
        items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))

        return {
            "success": True,
            "device_id": device_id,
            "path": remote_path,
            "items": items,
        }
    finally:
        try:
            sftp.close()
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass


@router.post("/{device_id}/upload")
async def upload_file_to_device(
    device_id: str,
    request: Request,
    filename: str = Query(..., description="Nombre del archivo a guardar"),
    remote_dir: str = Query("/", description="Directorio remoto de destino"),
):
    """Sube un archivo desde la máquina local al dispositivo de red vía SFTP."""
    client, sftp = _get_ssh_client_and_sftp(device_id)
    try:
        clean_name = os.path.basename(filename or "uploaded_file")
        clean_dir = remote_dir.rstrip("/")
        dest_path = f"{clean_dir}/{clean_name}" if clean_dir else f"/{clean_name}"

        # Leer contenido enviado directamente en el cuerpo HTTP
        content = await request.body()
        file_obj = io.BytesIO(content)

        sftp.putfo(file_obj, dest_path)
        size_bytes = len(content)

        logger.info("[sftp] Archivo %s (%d bytes) subido exitosamente a %s en %s", clean_name, size_bytes, dest_path, device_id)
        return {
            "success": True,
            "message": f"Archivo '{clean_name}' subido exitosamente.",
            "remote_path": dest_path,
            "size_bytes": size_bytes,
        }
    except Exception as exc:
        logger.error("[sftp] Error subiendo archivo a %s: %s", device_id, exc)
        raise HTTPException(
            status_code=500,
            detail=f"Error durante la subida SFTP: {exc}",
        ) from exc
    finally:
        try:
            sftp.close()
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass


@router.get("/{device_id}/download")
def download_file_from_device(
    device_id: str,
    remote_path: str = Query(..., description="Ruta completa del archivo remoto a descargar"),
):
    """Descarga un archivo desde el dispositivo de red vía SFTP."""
    client, sftp = _get_ssh_client_and_sftp(device_id)
    filename = os.path.basename(remote_path) or "download"

    try:
        # Verificar que el archivo existe y no sea un directorio
        attr = sftp.stat(remote_path)
        if stat.S_ISDIR(attr.st_mode):
            sftp.close()
            client.close()
            raise HTTPException(status_code=400, detail="La ruta solicitada es un directorio, no un archivo.")
    except IOError as io_err:
        sftp.close()
        client.close()
        raise HTTPException(status_code=404, detail=f"Archivo no encontrado en '{remote_path}': {io_err}") from io_err

    def iterfile():
        try:
            with sftp.open(remote_path, "rb") as remote_f:
                while chunk := remote_f.read(65536):
                    yield chunk
        finally:
            try:
                sftp.close()
            except Exception:
                pass
            try:
                client.close()
            except Exception:
                pass

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Length": str(attr.st_size),
    }
    return StreamingResponse(iterfile(), media_type="application/octet-stream", headers=headers)
