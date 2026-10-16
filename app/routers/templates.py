"""NetPulse — Config Templates Router.

Endpoints para listar, renderizar, y aplicar templates de configuración
pre-armados en dispositivos multi-driver (Cisco, Juniper, Arista).

Todos los endpoints requieren autenticación JWT y rol admin.
Las operaciones de deploy requieren dry-run previo con confirmación explícita.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.middleware.auth import JWTBearer
from app.middleware.rbac import requires_role, get_current_user
from app.services import templates_svc

router = APIRouter(
    prefix="/api/templates",
    tags=["Templates"],
    dependencies=[Depends(JWTBearer()), Depends(requires_role("admin"))],
)


# ── Schemas ───────────────────────────────────────────────────


class TemplateSummary(BaseModel):
    """Resumen de un template disponible."""
    name: str = Field(..., description="Nombre del template (ej. 'vlan')", examples=["vlan"])
    display_name: str = Field(..., description="Nombre descriptivo en español", examples=["Crear VLAN"])
    description: str = Field(..., description="Descripción de lo que hace el template", examples=["Crea una VLAN en un switch Cisco/Juniper/Arista"])
    parameters: list[str] = Field(default_factory=list, description="Parámetros requeridos", examples=[["vlan_id", "vlan_name"]])


class TemplateDetail(BaseModel):
    """Detalle completo de un template con snippets por driver."""
    name: str = Field(..., description="Nombre del template", examples=["vlan"])
    display_name: str = Field(..., description="Nombre descriptivo", examples=["Crear VLAN"])
    description: str = Field(..., description="Descripción del template", examples=["Crea una VLAN en un switch"])
    parameters: list[str] = Field(default_factory=list, description="Parámetros requeridos", examples=[["vlan_id", "vlan_name"]])
    drivers: dict[str, str] = Field(default_factory=dict, description="Snippets Jinja2 por driver", examples=[{"ios": "vlan {{vlan_id}}\n name {{vlan_name}}", "eos": "vlan {{vlan_id}}\n name {{vlan_name}}"}])


class TemplateRenderRequest(BaseModel):
    """Solicitud para renderizar un template."""
    driver: str = Field(..., description="Driver NAPALM destino (ios, junos, eos)", examples=["ios"])
    params: dict = Field(..., description="Parámetros del template", examples=[{"vlan_id": 100, "vlan_name": "IoT"}])


class TemplateRenderResponse(BaseModel):
    """Respuesta del renderizado de un template."""
    template: str = Field(..., description="Nombre del template", examples=["vlan"])
    driver: str = Field(..., description="Driver usado", examples=["ios"])
    rendered_config: str = Field(..., description="Configuración renderizada", examples=["vlan 100\n name IoT"])


class TemplateApplyRequest(BaseModel):
    """Solicitud para aplicar un template a un dispositivo."""
    device_id: str = Field(..., description="ID del dispositivo destino", examples=["cisco-core-01"])
    params: dict = Field(..., description="Parámetros del template", examples=[{"vlan_id": 100, "vlan_name": "IoT"}])


class TemplateApplyResponse(BaseModel):
    """Respuesta del dry-run de un template (pendiente confirmación)."""
    device_id: str = Field(..., description="ID del dispositivo", examples=["cisco-core-01"])
    template: str = Field(..., description="Nombre del template aplicado", examples=["vlan"])
    driver: str = Field(..., description="Driver del dispositivo", examples=["ios"])
    diff: str = Field(default="", description="Diff unificado mostrando los cambios propuestos")
    dry_run_ok: bool = Field(..., description="¿El dry-run fue exitoso?", examples=[True])
    error: str | None = Field(None, description="Error si ocurrió")
    session_token: str | None = Field(None, description="Token para confirmar el commit", examples=["abc123-def456"])


class TemplateConfirmRequest(BaseModel):
    """Solicitud para confirmar y committear un template."""
    session_token: str = Field(..., description="Token de sesión obtenido del dry-run", examples=["abc123-def456"])


class TemplateConfirmResponse(BaseModel):
    """Respuesta del commit de un template."""
    device_id: str = Field(..., description="ID del dispositivo", examples=["cisco-core-01"])
    template: str = Field(..., description="Nombre del template", examples=["vlan"])
    committed: bool = Field(..., description="¿Se aplicaron los cambios?", examples=[True])
    diff: str = Field(default="", description="Diff de los cambios aplicados")
    error: str | None = Field(None, description="Error si ocurrió")


# ── Endpoints ─────────────────────────────────────────────────


@router.get("", response_model=list[TemplateSummary])
def list_templates():
    """Lista todos los templates de configuración disponibles.

    Retorna nombre, descripción y parámetros requeridos de cada template.
    """
    templates = templates_svc.list_templates()
    return [
        TemplateSummary(
            name=t["name"],
            display_name=t["display_name"],
            description=t["description"],
            parameters=t["parameters"],
        )
        for t in templates
    ]


@router.get("/{name}", response_model=TemplateDetail)
def get_template_detail(name: str):
    """Obtiene el detalle completo de un template, incluyendo snippets por driver.

    Útil para que el frontend muestre qué parámetros se necesitan
    y qué comandos se generarán para cada plataforma.
    """
    tmpl = templates_svc.get_template(name)
    if not tmpl:
        raise HTTPException(
            status_code=404,
            detail=f"Template '{name}' no encontrado. Templates disponibles: {[t['name'] for t in templates_svc.list_templates()]}",
        )
    return TemplateDetail(
        name=tmpl["name"],
        display_name=tmpl["display_name"],
        description=tmpl["description"],
        parameters=tmpl["parameters"],
        drivers=tmpl.get("drivers", {}),
    )


@router.post("/{name}/render", response_model=TemplateRenderResponse)
def render_template(name: str, body: TemplateRenderRequest):
    """Renderiza un template para un driver específico con los parámetros dados.

    No modifica ningún dispositivo. Solo genera la configuración resultante
    para que el operador la revise antes de aplicarla.
    """
    rendered = templates_svc.render_template(name, body.driver, body.params)
    if rendered is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"No se pudo renderizar template '{name}' para driver '{body.driver}'. "
                f"Verifique que el template existe, el driver es soportado, "
                f"y todos los parámetros requeridos están presentes."
            ),
        )
    return TemplateRenderResponse(
        template=name,
        driver=body.driver,
        rendered_config=rendered,
    )


@router.post("/{name}/apply", response_model=TemplateApplyResponse)
def apply_template(name: str, body: TemplateApplyRequest, request: Request):
    """Aplica un template a un dispositivo (dry-run primero).

    Flujo:
    1. Obtiene el driver del dispositivo
    2. Renderiza el template para ese driver
    3. Ejecuta dry-run comparando contra la running config
    4. Retorna el diff para que el operador lo revise

    **IMPORTANTE**: Este endpoint NO modifica la configuración del dispositivo.
    Solo muestra qué cambios se aplicarían. Para confirmar, use el endpoint
    POST /api/templates/{name}/apply/confirm con el session_token retornado.
    """
    user = get_current_user(request)
    username = user.get("username", "anonymous")

    result = templates_svc.apply_template(
        device_id=body.device_id,
        template_name=name,
        params=body.params,
        username=username,
    )

    if result.get("error") and not result.get("dry_run_ok"):
        raise HTTPException(status_code=400, detail=result["error"])

    return TemplateApplyResponse(
        device_id=result["device_id"],
        template=result["template"],
        driver=result.get("driver", ""),
        diff=result.get("diff", ""),
        dry_run_ok=result.get("dry_run_ok", False),
        error=result.get("error"),
        session_token=result.get("session_token"),
    )


@router.post("/{name}/apply/confirm", response_model=TemplateConfirmResponse)
def confirm_template_apply(name: str, body: TemplateConfirmRequest, request: Request):
    """Confirma y committea un template previamente validado con dry-run.

    Debe llamarse DESPUÉS de revisar el diff retornado por
    POST /api/templates/{name}/apply. El session_token tiene
    una validez limitada y es de un solo uso.

    Flujo:
    1. Recupera la sesión pendiente
    2. Ejecuta deploy_config (backup + commit)
    3. Si falla, auto-rollback
    4. Auditoría automática
    """
    user = get_current_user(request)
    username = user.get("username", "anonymous")

    result = templates_svc.confirm_apply(
        session_token=body.session_token,
        username=username,
    )

    if result.get("error") and not result.get("committed"):
        raise HTTPException(status_code=400, detail=result["error"])

    return TemplateConfirmResponse(
        device_id=result.get("device_id", ""),
        template=result.get("template", name),
        committed=result.get("committed", False),
        diff=result.get("diff", ""),
        error=result.get("error"),
    )
