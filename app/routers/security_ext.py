"""NetPulse — Security Extras Router.

2FA/TOTP y API keys para automatización.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.middleware.auth import JWTBearer
from app.middleware.rbac import get_current_user, requires_role
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
def create_key(payload: dict):
    name = payload.get("name", "unnamed")
    role = payload.get("role", "viewer")
    return api_keys_svc.create_api_key(name, role)


@router.delete("/api-keys/{key_id}", dependencies=ADMIN, status_code=204)
def revoke_key(key_id: str):
    if not api_keys_svc.revoke_api_key(key_id):
        raise HTTPException(404, "API key no encontrada")
