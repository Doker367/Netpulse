"""NetPulse — Router SFTP para transferencia de archivos con equipos de red.

Permite explorar el sistema de archivos remoto del dispositivo,
subir archivos (firmwares, configs, scripts) y descargarlos.

Notas de conexión:
- El inventario puede declarar un ``port`` que NO sea SSH (p. ej. RouterOS
  publica la API en 8728/8729 y eAPI en 443). ``_resolve_ssh_port()`` evita
  usar esos puertos y cae al puerto SSH del inventario o a 22.
- Las credenciales vienen de ``napalm_svc._find_device()``, que ya descifra
  los passwords Fernet del inventario. Aquí no se vuelve a descifrar.
- Cada fallo se traduce a un ``detail`` literal en español para que la UI
  pueda explicar la causa; nunca se devuelve una lista vacía como éxito.
- Listar y descargar son lectura y siguen siendo globales (decisión de
  producto de RBAC fase 1). Subir es una mutación y valida el grupo real del
  inventario antes de abrir SSH.
- ``SSHClient.connect(timeout=...)`` no es solo el timeout TCP: también limita
  el *kex* SSH. RouterOS del laboratorio tarda 8–26 s en negociarlo, así que
  el default es 30 s (``NETPULSE_SFTP_CONNECT_TIMEOUT_S``). Un kex incompleto
  se clasifica como negociación fallida, **nunca** como credenciales.
"""

import errno
import io
import logging
import os
import socket
import stat

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.middleware.auth import JWTBearer
from app.middleware.rbac import requires_role, requires_scope
from app.services import napalm_svc

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/sftp",
    tags=["SFTP"],
    dependencies=[Depends(JWTBearer()), Depends(requires_role("operator"))],
)

# Puertos de API conocidos por driver. Si el inventario usa uno de estos como
# ``port``, NO se intenta SSH ahí: se usa cli_port/ssh_port o el default 22.
# - ros: 8728/8729 = API RouterOS (plain/SSL). El SSH de RouterOS es 22/2225.
# - eos: 443 = eAPI HTTPS de Arista. El SSH es 22.
_API_PORTS_BY_DRIVER: dict[str, frozenset[int]] = {
    "ros": frozenset({8728, 8729}),
    "eos": frozenset({443}),
}

_SSH_DEFAULT_PORT = 22
# ``timeout`` de ``SSHClient.connect`` = espera del kex SSH completo.
# RouterOS emulado tarda 8–26 s en DH-GEX (mismo diagnóstico que la terminal,
# ``docs/handoffs/terminal.md`` §9); con 10 s paramiko abortaba el kex y
# devolvía el engañoso "No existing session". 30 s cubre el laboratorio.
_CONNECT_TIMEOUT_S = 30.0
_CONNECT_TIMEOUT_ENV = "NETPULSE_SFTP_CONNECT_TIMEOUT_S"
_BANNER_TIMEOUT = 30
_AUTH_TIMEOUT = 30

_PERMISSION_MARKERS = (
    "permission denied",
    "access denied",
    "errno 13",
    "eacces",
    "eperm",
)

# Mensajes con los que paramiko describe un kex/banner que no terminó. No
# indican credenciales rechazadas: la sesión ni siquiera llegó a autenticar.
_KEX_INCOMPLETE_MARKERS = (
    "no existing session",
    "unable to complete handshake",
    "handshake failed",
    "error reading ssh protocol banner",
    "kex",
    "key exchange",
    "negotiation",
)


def _connect_timeout_s() -> float:
    """Timeout de conexión/kex desde ``NETPULSE_SFTP_CONNECT_TIMEOUT_S``.

    Default 30 s. Valores no numéricos o ``<= 0`` vuelven al default (mismo
    criterio que ``app/routers/terminal.py``).
    """
    raw = os.getenv(_CONNECT_TIMEOUT_ENV, "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _CONNECT_TIMEOUT_S
    return value if value > 0 else _CONNECT_TIMEOUT_S


# ── Resolución de puerto SSH ────────────────────────────────


def _as_port(value) -> int | None:
    """Normaliza un valor de puerto; devuelve None si no es válido."""
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if 0 < port < 65536 else None


def _resolve_ssh_port(device: dict) -> tuple[int, int | None]:
    """Resuelve el puerto SSH real del dispositivo.

    Orden documentado:
      1. ``cli_port`` (canal CLI explícito del inventario).
      2. ``ssh_port`` si el inventario lo declarara (campo opcional).
      3. ``port`` SOLO si el protocolo es SSH y no es un puerto de API
         conocido del driver (``_API_PORTS_BY_DRIVER``).
      4. Default 22.

    Returns:
        ``(puerto_ssh, puerto_api_ignorado)``. El segundo valor no es None
        cuando el ``port`` del inventario apuntaba a una API y se descartó;
        se usa para explicarlo en el ``detail`` si la conexión falla.
    """
    driver = str(device.get("driver") or "").lower()
    api_ports = _API_PORTS_BY_DRIVER.get(driver, frozenset())
    protocol = str(device.get("protocol") or "").lower()

    cli_port = _as_port(device.get("cli_port"))
    if cli_port:
        return cli_port, None

    ssh_port = _as_port(device.get("ssh_port"))
    if ssh_port:
        return ssh_port, None

    port = _as_port(device.get("port"))
    port_is_known_ssh = (
        port is not None
        and port not in api_ports
        and port not in (23, 161)  # telnet / SNMP no son SSH
        and protocol in ("", "ssh", "sftp")
    )
    if port_is_known_ssh:
        return port, None

    ignored_api_port = port if port in api_ports else None
    return _SSH_DEFAULT_PORT, ignored_api_port


def _api_port_note(driver: str, api_port: int, ssh_port: int) -> str:
    """Sufijo de error cuando el inventario apuntaba a un puerto API."""
    return (
        f" El inventario apunta al puerto API {api_port} del driver "
        f"'{driver}', que no ofrece SSH; se intentó el {ssh_port}."
    )


# ── Clasificación de errores ────────────────────────────────


def _short_cause(exc: BaseException) -> str:
    """Causa corta y legible (sin traceback) para el ``detail``."""
    if isinstance(exc, socket.gaierror):
        return "host no resuelto"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(exc, PermissionError):
        return "permiso denegado"

    code = getattr(exc, "errno", None)
    if code == errno.ECONNREFUSED:
        return "conexión rechazada"
    if code in (errno.ECONNRESET, errno.ECONNABORTED, errno.EPIPE):
        return "conexión interrumpida"
    if code == errno.EHOSTUNREACH:
        return "host no alcanzable"
    if code == errno.ENETUNREACH:
        return "red no alcanzable"
    if code == errno.ETIMEDOUT:
        return "timeout"

    text = str(exc).strip()
    if not text:
        return exc.__class__.__name__
    first_line = text.splitlines()[0].strip()
    lowered = first_line.lower()
    if "connection refused" in lowered:
        return "conexión rechazada"
    if "no route to host" in lowered:
        return "host no alcanzable"
    if "network is unreachable" in lowered:
        return "red no alcanzable"
    if "timed out" in lowered or "timeout" in lowered:
        return "timeout"
    return first_line[:200]


def _is_kex_incomplete(exc: BaseException) -> bool:
    """True si paramiko no completó el kex/banner (no es un fallo de auth).

    RouterOS del laboratorio devuelve ``No existing session`` cuando el kex
    excede el timeout; también se cubre ``Unable to complete handshake`` y
    errores de negociación/banner. Autenticación real llega como
    ``AuthenticationException`` y se clasifica antes (401).
    """
    try:
        import paramiko
    except ImportError:
        return False
    if not isinstance(exc, paramiko.SSHException):
        return False
    message = str(exc).lower()
    return any(marker in message for marker in _KEX_INCOMPLETE_MARKERS)


def _kex_error(
    *, device_id: str, host: str, port: int, note: str = ""
) -> HTTPException:
    """502 honesto cuando el kex SSH no termina dentro del timeout."""
    return HTTPException(
        status_code=502,
        detail=(
            f"Negociación SSH incompleta con {device_id} ({host}:{port}): "
            f"el equipo tardó más que el timeout ({_connect_timeout_s():g} s). "
            f"Revisa el equipo o sube {_CONNECT_TIMEOUT_ENV}."
        )
        + note,
    )


def _is_sftp_unsupported(exc: BaseException) -> bool:
    """True si paramiko falla al pedir el subsistema SFTP (equipo sin SFTP)."""
    try:
        import paramiko
    except ImportError:
        return False
    if not isinstance(exc, paramiko.SSHException):
        return False
    message = str(exc).lower()
    markers = (
        "subsystem request failed",
        "channel closed",
        "unable to open channel",
        "failed to open channel",
        "channel open failure",
    )
    return any(marker in message for marker in markers)


def _is_permission_error(exc: BaseException) -> bool:
    """Detecta EACCES/EPERM aunque paramiko lo entregue como IOError genérico."""
    if isinstance(exc, PermissionError):
        return True
    if isinstance(exc, OSError) and exc.errno in (errno.EACCES, errno.EPERM):
        return True
    message = str(exc).lower()
    return any(marker in message for marker in _PERMISSION_MARKERS)


_NETWORK_ERRNOS = frozenset({
    errno.ECONNREFUSED,
    errno.ECONNRESET,
    errno.ECONNABORTED,
    errno.EPIPE,
    errno.ETIMEDOUT,
    errno.EHOSTUNREACH,
    errno.ENETUNREACH,
})


def _is_network_error(exc: BaseException) -> bool:
    """True si el fallo es de transporte (no de ruta/permisos)."""
    try:
        import paramiko
    except ImportError:
        return False
    if isinstance(exc, (paramiko.SSHException, socket.gaierror)):
        return True
    return isinstance(exc, OSError) and exc.errno in _NETWORK_ERRNOS


def _connection_error(
    exc: BaseException,
    *,
    device_id: str,
    host: str,
    port: int,
    driver: str,
    ignored_api_port: int | None,
) -> HTTPException:
    """Traduce un fallo de conexión/apertura SFTP a una HTTPException honesta."""
    import paramiko

    note = ""
    if ignored_api_port is not None:
        note = _api_port_note(driver, ignored_api_port, port)

    if isinstance(exc, paramiko.AuthenticationException):
        # Literal exacto: la UI muestra el banner de credenciales.
        return HTTPException(
            status_code=401,
            detail=f"Autenticación SSH fallida en {device_id}: revisa credenciales",
        )

    if isinstance(exc, (socket.timeout, TimeoutError)):
        return HTTPException(
            status_code=504,
            detail=(
                f"Tiempo de espera agotado conectando a {device_id} "
                f"({host}:{port})"
            ) + note,
        )

    if isinstance(exc, socket.gaierror):
        return HTTPException(
            status_code=502,
            detail=(
                f"No se pudo conectar a {device_id} ({host}:{port}): "
                f"host no resuelto"
            ) + note,
        )

    if _is_kex_incomplete(exc):
        # "No existing session" / "Unable to complete handshake": el kex no
        # terminó a tiempo. NO es credenciales rechazadas; decirlo claro.
        return _kex_error(
            device_id=device_id, host=host, port=port, note=note
        )

    if _is_sftp_unsupported(exc):
        # FRR/otros equipos pueden aceptar SSH pero no ofrecer subsistema SFTP.
        return HTTPException(
            status_code=501,
            detail=(
                f"SFTP no soportado por este equipo ({device_id}); "
                f"usa Terminal/Comandos para CLI"
            ),
        )

    if isinstance(exc, paramiko.SSHException):
        lowered = str(exc).lower()
        if "timed out" in lowered or "timeout" in lowered:
            return HTTPException(
                status_code=504,
                detail=(
                    f"Tiempo de espera agotado conectando a {device_id} "
                    f"({host}:{port})"
                ) + note,
            )
        return HTTPException(
            status_code=502,
            detail=(
                f"No se pudo conectar a {device_id} ({host}:{port}): "
                f"{_short_cause(exc)}"
            ) + note,
        )

    # ConnectionRefusedError, NoValidConnectionsError, OSError de red, etc.
    return HTTPException(
        status_code=502,
        detail=(
            f"No se pudo conectar a {device_id} ({host}:{port}): "
            f"{_short_cause(exc)}"
        ) + note,
    )


def _read_error(
    exc: BaseException, *, device_id: str, remote_path: str
) -> HTTPException:
    """Traduce fallos al listar/leer una ruta remota."""
    if _is_permission_error(exc):
        return HTTPException(
            status_code=403,
            detail=f"Permiso denegado al leer {remote_path} en {device_id}",
        )
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return HTTPException(
            status_code=504,
            detail=(
                f"Tiempo de espera agotado leyendo {remote_path} "
                f"en {device_id}"
            ),
        )
    if _is_network_error(exc):
        return HTTPException(
            status_code=502,
            detail=(
                f"Se perdió la conexión SFTP con {device_id} al leer "
                f"{remote_path}: {_short_cause(exc)}"
            ),
        )
    return HTTPException(
        status_code=400,
        detail=f"No se pudo acceder a la ruta '{remote_path}': {_short_cause(exc)}",
    )


def _write_error(
    exc: BaseException, *, device_id: str, remote_path: str
) -> HTTPException:
    """Traduce fallos de subida (putfo)."""
    import paramiko

    if _is_permission_error(exc):
        # Literal exacto pedido por el contrato de UI.
        return HTTPException(
            status_code=403,
            detail=f"Permiso denegado al escribir {remote_path} en {device_id}",
        )
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return HTTPException(
            status_code=504,
            detail=f"Tiempo de espera agotado durante la subida a {device_id}",
        )
    if isinstance(
        exc,
        (ConnectionRefusedError, socket.gaierror, OSError, paramiko.SSHException),
    ):
        return HTTPException(
            status_code=502,
            detail=(
                f"Se perdió la conexión SFTP con {device_id} al escribir "
                f"{remote_path}: {_short_cause(exc)}"
            ),
        )
    return HTTPException(
        status_code=500,
        detail=f"Error durante la subida SFTP: {_short_cause(exc)}",
    )


def _download_error(
    exc: BaseException, *, device_id: str, remote_path: str
) -> HTTPException:
    """Traduce fallos al inspeccionar el archivo a descargar."""
    if _is_permission_error(exc):
        return HTTPException(
            status_code=403,
            detail=f"Permiso denegado al leer {remote_path} en {device_id}",
        )
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return HTTPException(
            status_code=504,
            detail=(
                f"Tiempo de espera agotado leyendo {remote_path} "
                f"en {device_id}"
            ),
        )
    if _is_network_error(exc):
        return HTTPException(
            status_code=502,
            detail=(
                f"Se perdió la conexión SFTP con {device_id} al leer "
                f"{remote_path}: {_short_cause(exc)}"
            ),
        )
    return HTTPException(
        status_code=404,
        detail=f"Archivo no encontrado en '{remote_path}': {_short_cause(exc)}",
    )


def _safe_close(sftp=None, client=None) -> None:
    """Cierra sesión y cliente ignorando errores de cierre."""
    if sftp is not None:
        try:
            sftp.close()
        except Exception:  # noqa: BLE001 — cierre best-effort
            pass
    if client is not None:
        try:
            client.close()
        except Exception:  # noqa: BLE001 — cierre best-effort
            pass


# ── Conexión ────────────────────────────────────────────────


def _get_ssh_client_and_sftp(device_id: str):
    """Crea una sesión SSH y cliente SFTP contra el dispositivo indicado.

    Las credenciales ya vienen descifradas desde ``napalm_svc._find_device``.
    """
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
    port, ignored_api_port = _resolve_ssh_port(device)
    driver = str(device.get("driver") or "")
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
            timeout=_connect_timeout_s(),
            banner_timeout=_BANNER_TIMEOUT,
            auth_timeout=_AUTH_TIMEOUT,
            # El inventario solo guarda password: no buscamos llaves ni agente.
            look_for_keys=False,
            allow_agent=False,
        )
        sftp = client.open_sftp()
        return client, sftp
    except Exception as exc:  # noqa: BLE001 — se clasifica por causa abajo
        _safe_close(client=client)
        logger.error(
            "[sftp] Error conectando a %s (%s:%s, driver=%s, api_ignored=%s): %s",
            device_id,
            host,
            port,
            driver,
            ignored_api_port,
            exc,
        )
        raise _connection_error(
            exc,
            device_id=device_id,
            host=host,
            port=port,
            driver=driver,
            ignored_api_port=ignored_api_port,
        ) from exc


# ── Endpoints ───────────────────────────────────────────────


@router.get("/{device_id}/list")
def list_remote_directory(device_id: str, path: str = Query("/", description="Ruta remota a listar")):
    """Lista el contenido de un directorio remoto en el dispositivo.

    Un fallo de conexión/lectura devuelve 4xx/5xx con ``detail``; un
    directorio vacío legítimo devuelve 200 con ``items: []``.
    """
    client, sftp = _get_ssh_client_and_sftp(device_id)
    try:
        remote_path = path.strip() or "/"
        try:
            file_attrs = sftp.listdir_attr(remote_path)
        except Exception as exc:  # noqa: BLE001 — se clasifica por causa
            logger.warning(
                "[sftp] Fallo listando %s en %s: %s", remote_path, device_id, exc
            )
            raise _read_error(
                exc, device_id=device_id, remote_path=remote_path
            ) from exc

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
        _safe_close(sftp, client)


@router.post("/{device_id}/upload")
async def upload_file_to_device(
    device_id: str,
    request: Request,
    filename: str = Query(..., description="Nombre del archivo a guardar"),
    remote_dir: str = Query("/", description="Directorio remoto de destino"),
):
    """Sube un archivo desde la máquina local al dispositivo de red vía SFTP.

    Es una mutación: un token con scope debe pertenecer al grupo real del
    equipo antes de que se abra la sesión SSH.
    """
    requires_scope(device_id=device_id, request=request)
    client, sftp = _get_ssh_client_and_sftp(device_id)
    try:
        clean_name = os.path.basename(filename or "uploaded_file")
        clean_dir = remote_dir.rstrip("/")
        dest_path = f"{clean_dir}/{clean_name}" if clean_dir else f"/{clean_name}"

        # Leer contenido enviado directamente en el cuerpo HTTP
        content = await request.body()
        file_obj = io.BytesIO(content)

        try:
            sftp.putfo(file_obj, dest_path)
        except Exception as exc:  # noqa: BLE001 — se clasifica por causa
            logger.warning(
                "[sftp] Fallo subiendo %s a %s (%s): %s",
                clean_name,
                device_id,
                dest_path,
                exc,
            )
            raise _write_error(
                exc, device_id=device_id, remote_path=dest_path
            ) from exc

        size_bytes = len(content)

        logger.info("[sftp] Archivo %s (%d bytes) subido exitosamente a %s en %s", clean_name, size_bytes, dest_path, device_id)
        return {
            "success": True,
            "message": f"Archivo '{clean_name}' subido exitosamente.",
            "remote_path": dest_path,
            "size_bytes": size_bytes,
        }
    finally:
        _safe_close(sftp, client)


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
        try:
            attr = sftp.stat(remote_path)
        except Exception as exc:  # noqa: BLE001 — se clasifica por causa
            logger.warning(
                "[sftp] Fallo inspeccionando %s en %s: %s",
                remote_path,
                device_id,
                exc,
            )
            raise _download_error(
                exc, device_id=device_id, remote_path=remote_path
            ) from exc

        if stat.S_ISDIR(attr.st_mode):
            raise HTTPException(status_code=400, detail="La ruta solicitada es un directorio, no un archivo.")
    except Exception:
        _safe_close(sftp, client)
        raise

    def iterfile():
        try:
            with sftp.open(remote_path, "rb") as remote_f:
                while chunk := remote_f.read(65536):
                    yield chunk
        finally:
            _safe_close(sftp, client)

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Length": str(attr.st_size),
    }
    return StreamingResponse(iterfile(), media_type="application/octet-stream", headers=headers)
