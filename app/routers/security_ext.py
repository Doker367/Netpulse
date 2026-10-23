"""NetPulse — Security Extras Router.

2FA/TOTP y API keys para automatización.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.middleware.auth import JWTBearer
from app.middleware.rbac import get_current_user, requires_role
from app.services import audit_svc
from app.services.security import totp_svc, api_keys_svc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/security", tags=["Security"])

AUTH = [Depends(JWTBearer())]
ADMIN = [Depends(JWTBearer()), Depends(requires_role("admin"))]


# ── 2FA / TOTP ───────────────────────────────────────────────

@router.post("/2fa/setup", dependencies=AUTH)
def setup_2fa(user: dict = Depends(get_current_user)):
    """Genera un secreto TOTP y el data URI para la app de autenticación."""
    secret = totp_svc.generate_totp_secret()
    uri = totp_svc.generate_qr_data_uri(user["sub"], secret)
    return {"secret": secret, "qr_data_uri": uri}


@router.post("/2fa/verify", dependencies=AUTH)
def verify_2fa(payload: dict, user: dict = Depends(get_current_user)):
    """Verifica un código TOTP contra un secreto."""
    secret = payload.get("secret", "")
    code = payload.get("code", "")
    if not secret or not code:
        raise HTTPException(422, "secret y code son requeridos")
    return {"valid": totp_svc.verify_totp(secret, code)}


# ── API Keys ─────────────────────────────────────────────────

@router.get("/api-keys", dependencies=ADMIN)
def list_keys():
    return api_keys_svc.list_api_keys()


@router.post("/api-keys", dependencies=ADMIN, status_code=201)
def create_key(payload: dict, request: Request):
    """Crea una clave de usuario (contrato anterior) o de colector.

    Las claves de colector exigen ``site_id`` y ``collector_id`` y no admiten
    ``role``: solo pueden ingerir/consultar su propio site.
    """
    name = payload.get("name", "unnamed")
    kind = payload.get("kind", "user")
    if kind not in ("user", "collector"):
        raise HTTPException(422, "kind debe ser 'user' o 'collector'")
    username = get_current_user(request).get("sub", "anonymous")
    try:
        if kind == "collector":
            if payload.get("role") is not None:
                raise ValueError("Una clave de colector no admite role")
            result = api_keys_svc.create_api_key(
                name,
                kind="collector",
                site_id=payload.get("site_id"),
                collector_id=payload.get("collector_id"),
            )
        else:
            role = payload.get("role", "viewer")
            result = api_keys_svc.create_api_key(name, role)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit_svc.log_event(
        action="collector_key_create" if result.get("kind") == "collector" else "api_key_create",
        username=username,
        details=(
            f"key_id={result.get('id')} name={result.get('name')} "
            f"kind={result.get('kind')} site={result.get('site_id')} "
            f"collector={result.get('collector_id')}"
        ),
        ip_address=request.client.host if request.client else None,
        path="/api/security/api-keys",
        method="POST",
    )
    return result


@router.delete("/api-keys/{key_id}", dependencies=ADMIN, status_code=204)
def revoke_key(key_id: str, request: Request):
    previous = next(
        (key for key in api_keys_svc.list_api_keys() if key.get("id") == key_id),
        None,
    )
    if not api_keys_svc.revoke_api_key(key_id):
        raise HTTPException(404, "API key no encontrada")
    kind = (previous or {}).get("kind", "user")
    audit_svc.log_event(
        action="collector_key_revoke" if kind == "collector" else "api_key_revoke",
        username=get_current_user(request).get("sub", "anonymous"),
        details=(
            f"key_id={key_id} kind={kind} "
            f"site={(previous or {}).get('site_id')} "
            f"collector={(previous or {}).get('collector_id')}"
        ),
        ip_address=request.client.host if request.client else None,
        path=f"/api/security/api-keys/{key_id}",
        method="DELETE",
    )
