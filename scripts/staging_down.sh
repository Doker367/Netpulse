#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/staging_common.sh"
need limactl
if ! limactl list --format '{{.Name}}' | awk '$0 == "netpulse-staging" {found=1} END {exit !found}'; then
  echo 'VM no creada; nada que detener.'
  exit 0
fi
if [[ "$(vm_status)" != Running ]]; then
  echo 'VM ya apagada; volúmenes conservados.'
  exit 0
fi
status=0
if remote test -f "$REMOTE/override.json"; then
  compose stop || status=$?
fi
limactl stop "$VM"
echo 'VM apagada; contenedores, volúmenes y certificados conservados.'
exit "$status"
