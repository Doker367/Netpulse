#!/usr/bin/env bash
# NetPulse — arranca la API con la red señuelo para pruebas manuales.
#
# - Siembra usuarios de prueba la primera vez (si no hay .initial_credentials).
# - Usa la topología dev (puertos altos en 127.0.0.1, sin root).
# - Corre en primer plano; Ctrl+C para salir.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ ! -f config/.initial_credentials ]; then
  echo "==> Sembrando usuarios de prueba"
  .venv/bin/python scripts/seed_users.py
fi

echo "==> Dashboard: http://localhost:${NETPULSE_PORT:-8082}"
echo "==> Login: ver config/.initial_credentials"
echo

NETPULSE_DECEPTION_ENABLED=true \
NETPULSE_DECEPTION_BIND_HOST="${NETPULSE_DECEPTION_BIND_HOST:-127.0.0.1}" \
NETPULSE_DECEPTION_NETWORK_FILE="${NETPULSE_DECEPTION_NETWORK_FILE:-config/deception/network.dev.yaml}" \
NETPULSE_DECEPTION_INGEST_TOKEN="${NETPULSE_DECEPTION_INGEST_TOKEN:-demo-token}" \
NETPULSE_METRICS_ENABLED=false \
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "${NETPULSE_PORT:-8082}"
