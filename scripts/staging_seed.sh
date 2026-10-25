#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/staging_common.sh"
umask 077
mkdir -p "$STATE/config"
PYTHONPATH="$ROOT" "$PY" - <<'PY'
import json, os, secrets
from pathlib import Path
import yaml
from app.core.security import hash_password
root, state = Path(os.environ['ROOT']), Path(os.environ['STATE'])
credentials = state / 'credentials.json'
passwords = json.loads(credentials.read_text()) if credentials.exists() else {}
spec = json.loads((root / 'deploy/staging/users.json').read_text())
for user in spec['users']:
    password = passwords.setdefault(user['username'], secrets.token_urlsafe(24))
    user['password_hash'] = hash_password(password)
    user['plain_password'] = None
credentials.write_text(json.dumps(passwords))
# Preserve existing inventory and users during upgrades. Remove neither data nor volumes.
for name, data in [('users.yaml', spec), ('devices.yaml', json.loads((root / 'deploy/staging/inventory.json').read_text()))]:
    target = state / 'config' / name
    if not target.exists():
        target.write_text(yaml.safe_dump(data, sort_keys=False))
print('Seed local preparado; credenciales privadas en', credentials)
PY
if [[ "${1:-}" != --prepare ]]; then
  need limactl
  [[ "$(vm_status)" == Running ]] || fail 'VM apagada: ejecuta staging_up.sh primero.'
  sync_config
  compose restart
fi
