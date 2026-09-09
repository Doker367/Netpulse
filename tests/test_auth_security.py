"""Tests de seguridad (bcrypt/JWT) y del flujo de autenticación (login + RBAC).

El login se prueba pocas veces para no disparar el rate limiter (5/min por IP).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.core.security import hash_password, verify_password, create_access_token
from app.main import app


# ── bcrypt directo ────────────────────────────────────────────────────────

def test_hash_y_verify():
    h = hash_password("MiP@ssword1")
    assert h.startswith("$2b$")
    assert verify_password("MiP@ssword1", h) is True
    assert verify_password("otra-clave", h) is False


def test_verify_hash_vacio():
    assert verify_password("x", "") is False
    assert verify_password("", "hash") is False
    assert verify_password("x", None) is False


def test_verify_hash_invalido():
    assert verify_password("x", "no-es-bcrypt") is False


def test_password_larga_truncada_consistente():
    # bcrypt usa 72 bytes; hash/verify deben comportarse igual para largas
    pwd = "A" * 200
    h = hash_password(pwd)
    assert verify_password(pwd, h) is True


# ── JWT ──────────────────────────────────────────────────────────────────

def test_jwt_roundtrip():
    token = create_access_token({"sub": "tester", "role": "admin"})
    from app.core.security import verify_token
    payload = verify_token(token)
    assert payload["sub"] == "tester"
    assert payload["role"] == "admin"


# ── API: login + /me (fixture por módulo, logins limitados) ──────────────

@pytest.fixture(scope="module")
def client():
    # Sin contexto: no se ejecuta el lifespan (evita sembrar devices reales / syslog)
    return TestClient(app)


def test_login_ok_me(client):
    r = client.post("/api/auth/login", json={"username": "superadmin", "password": "***REMOVED***"})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["username"] == "superadmin"
    assert me.json()["role"] == "admin"


def test_login_operador_ok(client):
    r = client.post("/api/auth/login", json={"username": "operator", "password": "***REMOVED***"})
    assert r.status_code == 200, r.text


def test_login_invalido(client):
    r = client.post("/api/auth/login", json={"username": "superadmin", "password": "incorrecta"})
    assert r.status_code == 401


def test_me_sin_token(client):
    r = client.get("/api/auth/me")
    assert r.status_code in (401, 403)


# ── RBAC ─────────────────────────────────────────────────────────────────

def test_rbac_operator_no_puede_crear(client):
    token = create_access_token({"sub": "operator", "role": "operator"})
    payload = {
        "id": "rbac-test",
        "hostname": "10.0.0.99",
        "port": 23,
        "driver": "procurve",
        "protocol": "telnet",
    }
    r = client.post("/api/devices", json=payload,
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
