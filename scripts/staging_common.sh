#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
VM=netpulse-staging
STATE="${HOME}/.local/state/netpulse-staging"
REMOTE=/home/lima/netpulse-staging
PY="${NETPULSE_PYTHON:-/Users/doker/netpulse-noc/.venv/bin/python}"
export ROOT STATE
fail() { printf 'staging: %s\n' "$*" >&2; exit 2; }
need() { command -v "$1" >/dev/null || fail "Falta $1; consulta docs/STAGING.md"; }
production() {
  [[ -f "$ROOT/deploy/docker-compose.prod.yml" ]] || fail 'Falta deploy/docker-compose.prod.yml; integra el stack de producción antes de staging (no se crea un sustituto).'
}
vm_status() { limactl list "$VM" --format '{{.Status}}'; }
remote() { limactl shell "$VM" -- "$@"; }
compose() {
  remote bash -c 'cd /home/lima/netpulse-staging/repo && docker compose --project-name netpulse-staging --env-file ../production.env -f deploy/docker-compose.prod.yml -f ../override.json --profile monitoring "$@"' bash "$@"
}
sync_config() {
  local ssh_config
  ssh_config=$(limactl list "$VM" --format '{{.SSHConfigFile}}')
  rsync -a --ignore-existing -e "ssh -F $ssh_config" "$STATE/config/" "lima-$VM:$REMOTE/repo/config/"
}
