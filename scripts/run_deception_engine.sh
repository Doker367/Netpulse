#!/usr/bin/env bash
# NetPulse — compila (si hace falta) y ejecuta el data plane en Go.
#
# Variables (opcionales):
#   NETPULSE_DECEPTION_CONFIG       ruta a network.yaml (def: network.dev.yaml)
#   NETPULSE_DECEPTION_INGEST_URL   endpoint de ingest
#   NETPULSE_DECEPTION_INGEST_TOKEN token X-Deception-Token
#   NETPULSE_DECEPTION_BIND_HOST    host de bind (vacío = IP del dispositivo)
#   DECEPTION_ENGINE_PACKET=1       habilita modo paquete (-tags packet, root)
#   NETPULSE_DECEPTION_IFACE        interfaz para modo paquete
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENGINE_DIR="$ROOT/deception-engine"
CONFIG="${NETPULSE_DECEPTION_CONFIG:-$ROOT/config/deception/network.dev.yaml}"
INGEST="${NETPULSE_DECEPTION_INGEST_URL:-http://127.0.0.1:8082/api/deception/ingest}"
TOKEN="${NETPULSE_DECEPTION_INGEST_TOKEN:-}"
BIND="${NETPULSE_DECEPTION_BIND_HOST:-}"

cd "$ENGINE_DIR"
if [ "${DECEPTION_ENGINE_PACKET:-0}" = "1" ]; then
  BIN="bin/engine-packet"
  TAGS="-tags packet"
else
  BIN="bin/engine"
  TAGS=""
fi

if [ ! -x "$BIN" ]; then
  echo "[run_deception_engine] compilando ($TAGS)..."
  # shellcheck disable=SC2086
  go build $TAGS -o "$BIN" ./cmd/engine
fi

ARGS=(-config "$CONFIG" -ingest "$INGEST" -token "$TOKEN" -bind "$BIND")
if [ "${DECEPTION_ENGINE_PACKET:-0}" = "1" ]; then
  ARGS+=(-packet -iface "${NETPULSE_DECEPTION_IFACE:-}")
fi

echo "[run_deception_engine] ejecutando $BIN -config $CONFIG"
exec "./$BIN" "${ARGS[@]}"
