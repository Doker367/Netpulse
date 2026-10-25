#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/staging_common.sh"
production # Deliberately before dependencies, sudo, certificates or VM creation.
for cmd in limactl rsync mkcert git curl; do need "$cmd"; done
[[ "$(uname -s)" == Darwin ]] || fail 'Este launcher requiere macOS con Lima VZ.'
umask 077
mkdir -p "$STATE/tls"
[[ -f "$STATE/production.env" ]] || fail "Crea $STATE/production.env con variables requeridas por el Compose real, SOLO valores nuevos de staging."
# Never silently rewrite the system resolver or ask sudo from inside this script.
if ! dscacheutil -q host -a name noc.staging.local | awk '/ip_address:/ {print $2}' | awk '$0 == "127.0.0.1" {found=1} END {exit !found}'; then
  fail 'Añade 127.0.0.1 noc.staging.local a /etc/hosts (ver docs/STAGING.md).'
fi
mkcert -install
if [[ ! -f "$STATE/tls/cert.pem" || ! -f "$STATE/tls/key.pem" ]] || ! openssl x509 -checkend 604800 -noout -in "$STATE/tls/cert.pem" >/dev/null; then
  mkcert -cert-file "$STATE/tls/cert.pem" -key-file "$STATE/tls/key.pem" noc.staging.local
fi
bash "$ROOT/scripts/staging_seed.sh" --prepare
if ! limactl list --format '{{.Name}}' | awk '$0 == "netpulse-staging" {found=1} END {exit !found}'; then
  limactl start --name="$VM" --tty=false "$ROOT/deploy/staging/lima.yaml"
elif [[ "$(vm_status)" != Running ]]; then
  limactl start --tty=false "$VM"
fi
remote mkdir -p "$REMOTE/repo" "$REMOTE/tls"
SSH_CONFIG=$(limactl list "$VM" --format '{{.SSHConfigFile}}')
# Only versioned source, never host secrets, venv, local DBs or inventories.
# No --delete: retain remote data. Upgrades of removed source files need fresh release trees.
git -C "$ROOT" ls-files -z | rsync -a --from0 --files-from=- \
  --exclude='.env*' --exclude='config/users.yaml' --exclude='config/devices.yaml' \
  --exclude='config/.*' --exclude='config/webhooks.json' --exclude='config/groups.yaml' \
  --exclude='data/***' --exclude='backups/***' --exclude='logs/***' \
  -e "ssh -F $SSH_CONFIG" "$ROOT/" "lima-$VM:$REMOTE/repo/"
rsync -a -e "ssh -F $SSH_CONFIG" "$STATE/tls/" "lima-$VM:$REMOTE/tls/"
rsync -a -e "ssh -F $SSH_CONFIG" "$STATE/production.env" "lima-$VM:$REMOTE/production.env"
sync_config
CADDY_TOKEN='{$DOMAIN}'
remote bash -c 'set -euo pipefail; cd /home/lima/netpulse-staging/repo; docker compose --project-name netpulse-staging --env-file ../production.env -f deploy/docker-compose.prod.yml --profile monitoring config --format json | STAGING_CADDY_HOST_TOKEN="$1" python3 deploy/staging/prepare.py' bash "${STAGING_CADDY_HOST_TOKEN:-$CADDY_TOKEN}"
compose config --quiet
compose up -d --build --wait --wait-timeout 180
curl --fail --silent --show-error --cacert "$(mkcert -CAROOT)/rootCA.pem" --retry 10 --retry-connrefused --retry-delay 2 https://noc.staging.local/api/health >/dev/null
printf 'URL: https://noc.staging.local\nCertificado: %s/tls/cert.pem\nCA: %s/rootCA.pem\n' "$STATE" "$(mkcert -CAROOT)"
