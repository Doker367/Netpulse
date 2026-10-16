#!/usr/bin/env bash
# NetPulse — detiene todos los servicios de prueba (API, engines, Docker, Colima).
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> Deteniendo API y engines"
pkill -f "uvicorn app.main" 2>/dev/null && echo "  uvicorn detenido" || true
pkill -f "deception-engine/bin/engine" 2>/dev/null && echo "  engine Go detenido" || true

echo "==> Bajando contenedores"
docker compose -f docker-compose.deception.yml down 2>/dev/null || true
docker compose -f docker-compose.monitoring.yml down 2>/dev/null || true

if command -v colima >/dev/null 2>&1; then
  echo "==> Apagando Colima"
  colima stop 2>/dev/null || true
fi

echo "==> Todo detenido"
