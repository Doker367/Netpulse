#!/usr/bin/env bash
# NetPulse — smoke test end-to-end de la red señuelo.
#
# Levanta la API con el motor de deception (red dev), genera un ataque
# simulado, verifica que se registren eventos/alertas y muestra un resumen.
# No necesita Docker ni root.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
UVICORN="$ROOT/.venv/bin/uvicorn"
PORT="${NETPULSE_PORT:-8082}"
LOG="/tmp/netpulse_smoke.log"

[ -x "$PY" ] || { echo "Falta el venv. Creá con: python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt"; exit 1; }

cleanup() {
  [ -n "${PID:-}" ] && kill "$PID" 2>/dev/null || true
  [ -n "${SMOKE_DATA:-}" ] && rm -rf "$SMOKE_DATA" 2>/dev/null || true
}
trap cleanup EXIT

echo "==> Iniciando NetPulse (deception) en :$PORT"
pkill -f "uvicorn app.main" 2>/dev/null || true
cd "$ROOT"
SMOKE_DATA="$(mktemp -d /tmp/netpulse_smoke.XXXXXX)"
NETPULSE_DECEPTION_ENABLED=true \
NETPULSE_DECEPTION_BIND_HOST=127.0.0.1 \
NETPULSE_DECEPTION_NETWORK_FILE=config/deception/network.dev.yaml \
NETPULSE_DECEPTION_DATA_DIR="$SMOKE_DATA" \
NETPULSE_DECEPTION_INGEST_TOKEN=demo-token \
NETPULSE_METRICS_ENABLED=false NETPULSE_SYSLOG_ENABLED=false \
"$UVICORN" app.main:app --host 127.0.0.1 --port "$PORT" > "$LOG" 2>&1 &
PID=$!

for _ in $(seq 1 40); do
  curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null && break
  sleep 0.5
done
curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null || { echo "La API no respondió. Log:"; tail -20 "$LOG"; exit 1; }

TOKEN=$("$PY" -c "from app.core.security import create_access_token; print(create_access_token({'sub':'smoke','role':'admin'}))")

echo "==> Estado del motor"
curl -s -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:$PORT/api/deception/engine/status" \
  | "$PY" -c "import json,sys; d=json.load(sys.stdin); print(f\"  listeners={d['count']} running={d['running']}\")"

echo "==> Generando ataque simulado"
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:$PORT/api/deception/simulate?count=5" >/dev/null
sleep 1

echo "==> Resumen"
curl -s -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:$PORT/api/deception/stats" \
  | "$PY" -c "
import json,sys
s=json.load(sys.stdin)
print('  eventos:', s['events_total'], '| atacantes:', s['attackers_unique'], '| dispositivos:', s['devices'])
print('  por tipo:', s['by_type'])
"

ALERTS=$(curl -s -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:$PORT/api/deception/alerts?limit=5" \
  | "$PY" -c "import json,sys; d=json.load(sys.stdin); print(len(d))")
echo "  alertas: $ALERTS"

echo "==> OK — smoke test completado"
