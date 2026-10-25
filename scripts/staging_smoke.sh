#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/staging_common.sh"
need mkcert
[[ -f "$STATE/credentials.json" ]] || fail 'Falta seed: ejecuta staging_seed.sh.'
export NETPULSE_URL=https://noc.staging.local
export NETPULSE_SMOKE_USER=staging-admin
NETPULSE_SMOKE_PASSWORD=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["staging-admin"])' "$STATE/credentials.json")
export NETPULSE_SMOKE_PASSWORD
export NETPULSE_PYTHON="$PY"
export CURL_CA_BUNDLE="$(mkcert -CAROOT)/rootCA.pem"
export SSL_CERT_FILE="$CURL_CA_BUNDLE"
# No -k, no skipped failures, preserve the full smoke exit status.
exec bash "$ROOT/scripts/smoke_full.sh"
