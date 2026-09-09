"""NetPulse — Config Templates Service.

Gestión de templates pre-armados para operaciones comunes de datacenter.
Los templates son archivos YAML con snippets Jinja2 multi-driver (ios, junos, eos).
"""

import logging
from pathlib import Path
from typing import Optional

import yaml
from jinja2 import Template, TemplateError

from app.core.settings import CONFIG_DIR
from app.services import napalm_svc
from app.services.audit_svc import log_event

logger = logging.getLogger(__name__)

TEMPLATES_DIR = CONFIG_DIR / "templates"

# ── Cache de templates (poco volátiles, se leen en cada request) ──

_template_cache: dict[str, dict] = {}


def _list_template_files() -> list[Path]:
    """Lista todos los archivos YAML en config/templates/."""
    if not TEMPLATES_DIR.exists():
        return []
    return sorted(TEMPLATES_DIR.glob("*.yaml"))


def _load_template_file(path: Path) -> dict:
    """Carga un template desde disco."""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _template_name_from_path(path: Path) -> str:
    """Extrae el nombre del template desde el nombre de archivo (sin .yaml)."""
    return path.stem


# ── Public API ──────────────────────────────────────────────────


def list_templates() -> list[dict]:
    """Lista todos los templates disponibles (nombre y descripción).

    Returns:
        Lista de dicts con keys: name, description, parameters, file.
    """
    templates = []
    for f in _list_template_files():
        try:
            tmpl = _load_template_file(f)
            name = _template_name_from_path(f)
            templates.append({
                "name": name,
                "display_name": tmpl.get("name", name),
                "description": tmpl.get("description", ""),
                "parameters": tmpl.get("parameters", []),
                "file": f.name,
            })
        except Exception as e:
            logger.warning("Error cargando template %s: %s", f.name, e)
    return templates


def get_template(name: str) -> Optional[dict]:
    """Obtiene un template completo por nombre.

    Args:
        name: Nombre del template (sin extensión, ej. 'vlan').

    Returns:
        Dict con name, display_name, description, parameters, y claves por driver
        (ios, junos, eos) con los snippets Jinja2 correspondientes.
        None si no existe.
    """
    path = TEMPLATES_DIR / f"{name}.yaml"
    if not path.exists():
        return None
    try:
        tmpl = _load_template_file(path)
        drivers = {}
        for key in tmpl:
            if key in ("ios", "junos", "eos", "nxos", "iosxr"):
                drivers[key] = tmpl[key]
        return {
            "name": name,
            "display_name": tmpl.get("name", name),
            "description": tmpl.get("description", ""),
            "parameters": tmpl.get("parameters", []),
            "drivers": drivers,
        }
    except Exception as e:
        logger.error("Error cargando template %s: %s", name, e)
        return None


def render_template(name: str, driver: str, params: dict) -> Optional[str]:
    """Renderiza un template para un driver específico con los parámetros dados.

    Args:
        name: Nombre del template (ej. 'vlan').
        driver: Driver NAPALM destino (ios, junos, eos).
        params: Diccionario con los valores de los parámetros requeridos.

    Returns:
        Configuración renderizada como string, o None si falla.
    """
    tmpl = get_template(name)
    if not tmpl:
        logger.warning("Template no encontrado: %s", name)
        return None

    snippet = tmpl.get("drivers", {}).get(driver)
    if not snippet:
        logger.warning(
            "Template '%s' no tiene snippet para driver '%s'. Disponibles: %s",
            name, driver, list(tmpl.get("drivers", {}).keys()),
        )
        return None

    # Validar parámetros requeridos
    required = set(tmpl.get("parameters", []))
    provided = set(params.keys())
    missing = required - provided
    if missing:
        logger.warning(
            "Template '%s': parámetros faltantes: %s", name, missing,
        )
        return None

    try:
        template = Template(snippet)
        rendered = template.render(**params)
        return rendered.strip()
    except TemplateError as e:
        logger.error("Error renderizando template '%s': %s", name, e)
        return None


def apply_template(
    device_id: str,
    template_name: str,
    params: dict,
    username: str = "anonymous",
) -> dict:
    """Ciclo completo de deploy con template: render + dry-run + (pendiente commit).

    Flujo:
    1. Obtiene el driver del dispositivo
    2. Renderiza el template para ese driver
    3. Obtiene la running config actual
    4. Ejecuta dry-run (compara template contra running)
    5. Retorna el diff para revisión del operador (NO commitea)

    Args:
        device_id: ID del dispositivo destino.
        template_name: Nombre del template a aplicar.
        params: Parámetros para el template.
        username: Usuario que ejecuta la acción (para auditoría).

    Returns:
        Dict con: device_id, template, driver, diff, dry_run_ok, session_token.
        session_token se usa luego para confirmar el commit.
    """
    result = {
        "device_id": device_id,
        "template": template_name,
        "driver": None,
        "diff": "",
        "dry_run_ok": False,
        "error": None,
        "session_token": None,  # para confirmar después
    }

    # 1. Obtener dispositivo
    device = napalm_svc._find_device(device_id)
    if not device:
        result["error"] = f"Dispositivo '{device_id}' no encontrado"
        log_event(
            action="template_apply",
            device_id=device_id,
            username=username,
            details=f"Template '{template_name}' — ERROR: dispositivo no encontrado",
            success=False,
        )
        return result

    driver = device.get("driver", "ios")
    result["driver"] = driver

    # 2. Renderizar template
    candidate = render_template(template_name, driver, params)
    if candidate is None:
        result["error"] = (
            f"No se pudo renderizar template '{template_name}' "
            f"para driver '{driver}'"
        )
        log_event(
            action="template_apply",
            device_id=device_id,
            username=username,
            details=(
                f"Template '{template_name}' params={params} "
                f"— ERROR: render falló para driver '{driver}'"
            ),
            success=False,
        )
        return result

    # 3. Dry-run (compare)
    compare_result = napalm_svc.compare_config(device_id, candidate)
    if not compare_result.success:
        result["error"] = compare_result.error or "Error en dry-run"
        result["diff"] = compare_result.data or ""
        log_event(
            action="template_apply",
            device_id=device_id,
            username=username,
            details=(
                f"Template '{template_name}' params={params} "
                f"— dry-run FALLÓ: {result['error']}"
            ),
            success=False,
        )
        return result

    result["diff"] = compare_result.data or ""
    result["dry_run_ok"] = True

    # Generar token de sesión simple (para confirmación)
    import uuid
    session_token = str(uuid.uuid4())
    result["session_token"] = session_token

    # Guardar en caché de sesiones pendientes
    _pending_sessions[session_token] = {
        "device_id": device_id,
        "template_name": template_name,
        "driver": driver,
        "candidate": candidate,
        "params": params,
        "username": username,
        "diff": result["diff"],
    }

    log_event(
        action="template_apply",
        device_id=device_id,
        username=username,
        details=(
            f"Template '{template_name}' params={params} "
            f"— dry-run OK, esperando confirmación"
        ),
        success=True,
    )

    return result


# ── Sesiones pendientes de confirmación ────────────────────────

_pending_sessions: dict[str, dict] = {}


def confirm_apply(session_token: str, username: str = "anonymous") -> dict:
    """Confirma y commitea un template previamente validado con dry-run.

    Args:
        session_token: Token de sesión obtenido de apply_template().
        username: Usuario que confirma (para auditoría).

    Returns:
        Dict con: device_id, template, committed, diff, error.
    """
    result = {
        "device_id": None,
        "template": None,
        "committed": False,
        "diff": "",
        "error": None,
    }

    session = _pending_sessions.pop(session_token, None)
    if not session:
        result["error"] = "Sesión no encontrada o ya expirada. Vuelva a ejecutar apply_template()."
        log_event(
            action="template_confirm",
            username=username,
            details=f"ERROR: sesión '{session_token}' no encontrada",
            success=False,
        )
        return result

    device_id = session["device_id"]
    template_name = session["template_name"]
    candidate = session["candidate"]
    params = session["params"]

    result["device_id"] = device_id
    result["template"] = template_name
    result["diff"] = session["diff"]

    # Deploy (commit real)
    deploy_result = napalm_svc.deploy_config(device_id, candidate)
    result["committed"] = deploy_result.get("committed", False)
    result["diff"] = deploy_result.get("diff", session["diff"])

    if deploy_result.get("error"):
        result["error"] = deploy_result["error"]
        log_event(
            action="template_confirm",
            device_id=device_id,
            username=username,
            details=(
                f"Template '{template_name}' params={params} "
                f"— COMMIT FALLÓ: {deploy_result['error']}"
            ),
            success=False,
        )
    elif result["committed"]:
        log_event(
            action="template_confirm",
            device_id=device_id,
            username=username,
            details=(
                f"Template '{template_name}' params={params} "
                f"— COMMIT OK"
            ),
            success=True,
        )
    else:
        log_event(
            action="template_confirm",
            device_id=device_id,
            username=username,
            details=(
                f"Template '{template_name}' params={params} "
                f"— sin cambios que aplicar"
            ),
            success=True,
        )

    return result
