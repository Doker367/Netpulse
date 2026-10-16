#!/usr/bin/env python3
"""Seed initial NOC users with operator-supplied passwords.

A clean clone ships `config/users.yaml` with unknown password hashes, so
nobody can log in. This script sets the initial passwords for `superadmin`
and `operator` (idempotent) and records the usernames in
`config/.initial_credentials` (git-ignored).

There are no default credentials in this repository: passwords MUST be
provided through the environment and are never written to disk or echoed.

Usage:
    NETPULSE_ADMIN_PASSWORD='...' NETPULSE_OPERATOR_PASSWORD='...' \
        python scripts/seed_users.py
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

MIN_PASSWORD_LENGTH = 12


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(
            f"Falta {name}: exporte una contraseña única antes de sembrar usuarios."
        )
    if len(value) < MIN_PASSWORD_LENGTH:
        raise SystemExit(f"{name} debe tener al menos {MIN_PASSWORD_LENGTH} caracteres.")
    return value


def main() -> int:
    admin_password = _required("NETPULSE_ADMIN_PASSWORD")
    operator_password = _required("NETPULSE_OPERATOR_PASSWORD")
    if admin_password == operator_password:
        raise SystemExit("Use contraseñas distintas para cada usuario.")

    specs = [
        {"username": "superadmin", "password": admin_password,
         "role": "admin", "name": "Super Admin"},
        {"username": "operator", "password": operator_password,
         "role": "operator", "name": "Operador"},
    ]

    data = {"users": []}
    if USERS_FILE.exists():
        data = yaml.safe_load(USERS_FILE.read_text(encoding="utf-8")) or {"users": []}
    users = data.setdefault("users", [])

    index = {u.get("username"): u for u in users}
    for spec in specs:
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
        "# Las contraseñas no se escriben aquí a propósito: use su gestor de secretos.\n"
        "admin    : superadmin\n"
        "operator : operator\n",
        encoding="utf-8",
    )

    print("Usuarios sembrados (las contraseñas no se muestran):")
    for spec in specs:
        print(f"  - {spec['username']:10} {spec['role']}")
    print(f"\nGuardado en {USERS_FILE} y {CREDS_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
