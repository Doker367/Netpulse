#!/usr/bin/env python3
"""NetPulse — siembra usuarios de prueba con contraseñas conocidas.

Un clon limpio tiene `config/users.yaml` con hashes de contraseña
desconocidos, por lo que no se puede iniciar sesión. Este script fija
contraseñas conocidas para `superadmin` y `operator` (idempotente) y las
guarda también en `config/.initial_credentials` (ignorado por git).

Uso:
    python scripts/seed_users.py
    NETPULSE_ADMIN_PASSWORD=mipass python scripts/seed_users.py
"""

import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.security import hash_password  # noqa: E402

USERS_FILE = ROOT / "config" / "users.yaml"
CREDS_FILE = ROOT / "config" / ".initial_credentials"

ADMIN_PASSWORD = os.getenv("NETPULSE_ADMIN_PASSWORD", "netpulse-admin")
OPERATOR_PASSWORD = os.getenv("NETPULSE_OPERATOR_PASSWORD", "netpulse-operator")

USERS = [
    {"username": "superadmin", "password": ADMIN_PASSWORD, "role": "admin", "name": "Super Admin"},
    {"username": "operator", "password": OPERATOR_PASSWORD, "role": "operator", "name": "Operador"},
    {"username": "demo", "password": "deception123", "role": "admin", "name": "Demo"},
]


def main() -> int:
    data = {"users": []}
    if USERS_FILE.exists():
        data = yaml.safe_load(USERS_FILE.read_text(encoding="utf-8")) or {"users": []}
    users = data.setdefault("users", [])

    index = {u.get("username"): u for u in users}
    for spec in USERS:
        entry = index.get(spec["username"])
        if entry is None:
            entry = {"username": spec["username"]}
            users.append(entry)
        entry["role"] = spec["role"]
        entry["name"] = spec["name"]
        entry["password_hash"] = hash_password(spec["password"])
        entry["plain_password"] = None

    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    USERS_FILE.write_text(
        yaml.safe_dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    CREDS_FILE.write_text(
        "# Credenciales iniciales generadas por seed_users.py — NO COMMITEAR\n"
        f"dashboard admin   : superadmin / {ADMIN_PASSWORD}\n"
        f"dashboard operator: operator   / {OPERATOR_PASSWORD}\n"
        "dashboard demo    : demo       / deception123\n",
        encoding="utf-8",
    )

    print("Usuarios sembrados:")
    for spec in USERS:
        print(f"  - {spec['username']:10} {spec['role']:8} / {spec['password']}")
    print(f"\nGuardado en {USERS_FILE} y {CREDS_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
