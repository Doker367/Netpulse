#!/usr/bin/env python
"""Rota la clave Fernet que cifra las credenciales del inventario.

Flujo: descifra cada valor cifrado de ``config/devices.yaml`` con la clave
actual, genera una clave nueva y re-cifra todo con ella. Antes de tocar
nada crea copias de seguridad de la clave y del inventario.

Ejecutar con la API DETENIDA, desde la raíz del repo:

    .venv/bin/python scripts/rotate_fernet.py

Al terminar:
1. Guarda la clave nueva (``config/.fernet_key``) en el gestor de secretos.
2. Verifica el arranque de la API y un facts/ping contra un equipo del lab.
3. Destruye la copia de la clave antigua (``config/.fernet_key.rotated-*``).
"""

import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402
from cryptography.fernet import Fernet, InvalidToken  # noqa: E402

from app.services.crypto_svc import KEY_FILE, DEVICES_FILE, _looks_encrypted  # noqa: E402

BACKUP_DIR = Path(__file__).resolve().parent.parent / "backups"


def _walk(node, path=()):
    """Yield (path, value) para cada cadena del documento YAML."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, path + (key,))
    elif isinstance(node, list):
        for idx, value in enumerate(node):
            yield from _walk(value, path + (idx,))
    elif isinstance(node, str):
        yield path, node


def _set(data, path, value):
    node = data
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value


def main() -> int:
    if not KEY_FILE.exists():
        print(f"❌ No existe {KEY_FILE}; nada que rotar.")
        return 1
    if not DEVICES_FILE.exists():
        print(f"❌ No existe {DEVICES_FILE}; nada que re-cifrar.")
        return 1

    old_fernet = Fernet(KEY_FILE.read_bytes().strip())
    data = yaml.safe_load(DEVICES_FILE.read_text(encoding="utf-8")) or {}

    encrypted = [(p, v) for p, v in _walk(data) if _looks_encrypted(v)]
    if not encrypted:
        print("ℹ️  No hay valores cifrados en devices.yaml; la clave no necesita rotación.")
        return 0

    # Descifrar TODO en memoria antes de tocar archivos: si algo falla,
    # no se modificó nada.
    plain = {}
    for path, token in encrypted:
        try:
            plain[path] = old_fernet.decrypt(token.encode())
        except InvalidToken:
            joined = ".".join(map(str, path))
            print(f"❌ No se pudo descifrar {joined}. Rotación abortada; nada cambió.")
            return 1

    ts = time.strftime("%Y%m%d-%H%M%S")
    BACKUP_DIR.mkdir(exist_ok=True)
    key_backup = KEY_FILE.with_name(f".fernet_key.rotated-{ts}")
    yaml_backup = BACKUP_DIR / f"devices.pre-rotate-{ts}.yaml"
    shutil.copy2(KEY_FILE, key_backup)
    shutil.copy2(DEVICES_FILE, yaml_backup)

    new_key = Fernet.generate_key()
    new_fernet = Fernet(new_key)
    KEY_FILE.write_bytes(new_key + b"\n")

    for path, value in plain.items():
        _set(data, path, new_fernet.encrypt(value).decode())
    DEVICES_FILE.write_text(
        yaml.dump(data, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )

    print(f"✅ Rotación completa: {len(plain)} valores re-cifrados.")
    print(f"   Clave anterior respaldada en: {key_backup}")
    print(f"   Inventario anterior en:        {yaml_backup}")
    print("   Siguiente paso: arranca la API y verifica un facts/ping,")
    print("   guarda la clave nueva en el gestor de secretos y")
    print("   destruye la copia de la clave antigua.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
