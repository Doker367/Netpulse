#!/usr/bin/env bash
# NetPulse — detiene todos los servicios de prueba (API, stacks Docker, Colima).
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> Deteniendo API"
pkill -f "uvicorn app.main" 2>/dev/null && echo "  uvicorn detenido" || true

echo "==> Bajando stacks Docker"
for compose in docker-compose.ai.yml docker-compose.netbox.yml \
               docker-compose.monitoring.yml docker-compose.lab.yml; do
  if [ -f "$compose" ]; then
    docker compose -f "$compose" down 2>/dev/null && echo "  $compose abajo" || true
  fi
done

if command -v colima >/dev/null 2>&1; then
  echo "==> Apagando Colima"
  colima stop 2>/dev/null || true
fi

echo "==> Todo detenido"
