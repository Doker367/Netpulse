#!/usr/bin/env bash
# Pruebas en vivo de la Ola 1 (API + módulos integrados).
#
# Uso:
#   make smoke                 # rápido (~2 s)
#   SCAN=1 make smoke          # incluye un escaneo LLDP real (~30 s)
#   bash scripts/smoke_ola1.sh # equivalente
#
# Requiere la API corriendo (make run) y un .env con NETPULSE_OPERATOR_PASSWORD.

set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
BASE="${NETPULSE_URL:-http://127.0.0.1:8082}"
PY=".venv/bin/python"
PASS=0
FAIL=0

ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; PASS=$((PASS + 1)); }
bad()  { printf "  \033[31m✗\033[0m %s\n" "$1"; FAIL=$((FAIL + 1)); }
info() { printf "      %s\n" "$1"; }

[ -f .env ] || { echo "Falta .env en $(pwd)"; exit 1; }
set -a
# shellcheck disable=SC1091
. ./.env
set +a
: "${NETPULSE_OPERATOR_PASSWORD:?Falta NETPULSE_OPERATOR_PASSWORD en .env}"

echo "NetPulse · smoke Ola 1 · $BASE"
echo

# 1. Salud de la API
code=$(curl -s -o /tmp/np_smoke_health.json -w '%{http_code}' "$BASE/api/health")
if [ "$code" = "200" ]; then
  ok "GET /api/health → 200"
  info "$("$PY" -c "import json;d=json.load(open('/tmp/np_smoke_health.json'));print('version',d.get('version'),'·',d.get('devices_configured'),'equipos')" 2>/dev/null)"
else
  bad "GET /api/health → $code (¿está la API arriba?)"
fi

# 2. Login real
code=$(curl -s -o /tmp/np_smoke_login.json -w '%{http_code}' -X POST "$BASE/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"operator\",\"password\":\"$NETPULSE_OPERATOR_PASSWORD\"}")
TOK=$("$PY" -c "import json;print(json.load(open('/tmp/np_smoke_login.json')).get('access_token',''))" 2>/dev/null)
if [ "$code" = "200" ] && [ -n "$TOK" ]; then
  ok "POST /api/auth/login (operator) → 200 · JWT recibido"
else
  bad "POST /api/auth/login (operator) → $code · sin token, se omiten las pruebas autenticadas"
fi

if [ -n "$TOK" ]; then
  AUTH=(-H "Authorization: Bearer $TOK")

  # 3. Grupos (módulo dormido, ahora visible)
  code=$(curl -s -o /tmp/np_smoke_groups.json -w '%{http_code}' "${AUTH[@]}" "$BASE/api/groups")
  if [ "$code" = "200" ]; then
    ok "GET /api/groups → 200"
    info "$("$PY" -c "import json;g=json.load(open('/tmp/np_smoke_groups.json'));print(', '.join('%s=%s' % (x['name'], x.get('device_count', 0)) for x in g))" 2>/dev/null)"
  else
    bad "GET /api/groups → $code"
  fi

  # 4. NetBox (solo admin: 403 es el comportamiento correcto para operator)
  code=$(curl -s -o /dev/null -w '%{http_code}' "${AUTH[@]}" "$BASE/api/netbox/sites")
  if [ "$code" = "200" ]; then
    ok "GET /api/netbox/sites → 200 (usuario con rol admin)"
  elif [ "$code" = "403" ]; then
    ok "GET /api/netbox/sites → 403 (operator: requiere admin, correcto)"
  else
    bad "GET /api/netbox/sites → $code"
  fi

  # 5. Discovery (contrato de la Ola 1)
  code=$(curl -s -o /tmp/np_smoke_cand.json -w '%{http_code}' "${AUTH[@]}" "$BASE/api/discovery/candidates")
  if [ "$code" = "200" ]; then
    ok "GET /api/discovery/candidates → 200"
    info "$("$PY" -c "import json;d=json.load(open('/tmp/np_smoke_cand.json'));r=d.get('resumen',{});print('ultimo scan:', d.get('timestamp') or 'ninguno', '· candidatos:', r.get('candidatos',0))" 2>/dev/null)"
  else
    bad "GET /api/discovery/candidates → $code"
  fi

  if [ "${SCAN:-0}" = "1" ]; then
    echo "  … ejecutando scan LLDP real (hasta 30 s)"
    code=$(curl -s --max-time 60 -o /tmp/np_smoke_scan.json -w '%{http_code}' -X POST \
      "${AUTH[@]}" -H 'Content-Type: application/json' \
      -d '{"metodo":"lldp"}' "$BASE/api/discovery/scan")
    if [ "$code" = "200" ]; then
      ok "POST /api/discovery/scan → 200"
      info "$("$PY" -c "import json;d=json.load(open('/tmp/np_smoke_scan.json'));r=d.get('resumen',{});print('origenes ok:',r.get('origenes_ok'),'/',r.get('origenes'),'· vecinos:',r.get('vecinos'),'· candidatos:',r.get('candidatos'))" 2>/dev/null)"
      "$PY" -c "import json;d=json.load(open('/tmp/np_smoke_scan.json'));[print('      ·',o['id'],'→',o['estado'],('· '+o['motivo'][:70]) if o.get('motivo') else '') for o in d.get('origenes',[])]" 2>/dev/null
    else
      bad "POST /api/discovery/scan → $code"
    fi
  fi

  # 6. Reportes SLA (última hora)
  read -r DESDE HASTA < <("$PY" -c "
from datetime import datetime, timezone, timedelta
n = datetime.now(timezone.utc)
print((n - timedelta(hours=1)).isoformat().replace('+00:00','Z'), n.isoformat().replace('+00:00','Z'))")
  code=$(curl -s -o /tmp/np_smoke_report.json -w '%{http_code}' -X POST "${AUTH[@]}" \
    -H 'Content-Type: application/json' \
    -d "{\"desde\":\"$DESDE\",\"hasta\":\"$HASTA\"}" "$BASE/api/reports/availability")
  if [ "$code" = "200" ]; then
    ok "POST /api/reports/availability → 200"
    info "$("$PY" -c "import json;d=json.load(open('/tmp/np_smoke_report.json'));print('estado:',d.get('estado'),'· recoleccion:',d.get('recoleccion_activa'),'· equipos:',len(d.get('equipos',[])),'· excluidos:',len(d.get('excluidos',[])))" 2>/dev/null)"
  else
    bad "POST /api/reports/availability → $code"
  fi

  # 7. Rutas nuevas registradas (si faltara un include_router daría 404)
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${AUTH[@]}" \
    -H 'Content-Type: application/json' -d '{}' "$BASE/api/topology/traceroute")
  if [ "$code" != "404" ]; then
    ok "POST /api/topology/traceroute → $code (ruta registrada; 422 = body incompleto esperado)"
  else
    bad "POST /api/topology/traceroute → 404 (ruta no registrada)"
  fi
fi

# 8. Panel servido con las vistas nuevas
html=$(curl -s "$BASE/")
for marker in 'id="v-groups"' 'id="v-netbox"' 'nav_groups' 'nav_netbox'; do
  case "$html" in
    *"$marker"*) ok "panel contiene $marker" ;;
    *)           bad "panel NO contiene $marker" ;;
  esac
done

echo
if [ "$FAIL" -eq 0 ]; then
  printf "\033[32m%s pruebas OK\033[0m\n" "$PASS"
  exit 0
fi
printf "\033[31m%d fallidas\033[0m · %d OK\n" "$FAIL" "$PASS"
exit 1
