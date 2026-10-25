"""Validate the real Compose contract; generate only staging mount/TLS overrides."""
import json
import os
import re
import sys
from pathlib import Path

base = Path('/home/lima/netpulse-staging')
repo = base / 'repo'
config = json.load(sys.stdin)
overrides = {}
config_services = 0
caddy_services = 0
for name, service in config['services'].items():
    volumes = []
    for mount in service.get('volumes', []):
        source = Path(mount.get('source', ''))
        target = mount['target']
        if mount['type'] == 'bind' and source == repo / 'config':
            config_services += 1
        if target == '/etc/caddy/Caddyfile':
            if mount['type'] != 'bind' or not source.is_file():
                sys.exit('staging: Caddyfile de producción debe ser bind mount existente')
            text = source.read_text()
            token = os.environ.get('STAGING_CADDY_HOST_TOKEN', '{$DOMAIN}')
            # Fail closed rather than replace a global block, upstream or multiple sites.
            pattern = r'(?m)^' + re.escape(token) + r'\s*\{\s*$'
            if len(re.findall(pattern, text)) != 1 or re.search(r'(?m)^\s*tls\s', text):
                sys.exit('staging: se requiere un único bloque HOST { sin directiva tls; configura STAGING_CADDY_HOST_TOKEN con el host literal de producción')
            text = re.sub(pattern, 'noc.staging.local {\n    tls /staging-tls/cert.pem /staging-tls/key.pem', text)
            generated = base / 'Caddyfile'
            generated.write_text(text)
            volumes.extend([
                {'type': 'bind', 'source': str(generated), 'target': target, 'read_only': True},
                {'type': 'bind', 'source': str(base / 'tls'), 'target': '/staging-tls', 'read_only': True},
            ])
            caddy_services += 1
    if volumes:
        overrides[name] = {'volumes': volumes}
if config_services == 0 or caddy_services != 1:
    sys.exit('staging: contrato incompatible: requiere bind del directorio repo/config y un Caddyfile montado en /etc/caddy/Caddyfile; revisar docs/STAGING.md')
(base / 'override.json').write_text(json.dumps({'services': overrides}, indent=2))
