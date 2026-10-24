#!/usr/bin/env bash
# Smoke post-despliegue: lectura y sesiones reales contra API + lab FRR.
# NETPULSE_URL=http://127.0.0.1:8099 NETPULSE_SMOKE_USER=demo9 \
# NETPULSE_SMOKE_PASSWORD='...' bash scripts/smoke_full.sh
# Opcional: NETPULSE_COLLECTOR_API_KEY=np_... (clave existente, kind=collector).
set -euo pipefail

cd "$(dirname "$0")/.."
BASE="${NETPULSE_URL:-http://127.0.0.1:8099}"
BASE="${BASE%/}"
PY="${NETPULSE_PYTHON:-/Users/doker/netpulse-noc/.venv/bin/python}"
PASS=0
FAIL=0
TOTAL=0
TMP=$(mktemp -d)
trap 'rm -rf -- "$TMP"' EXIT

result() {
  TOTAL=$((TOTAL + 1))
  if [ "$1" = pass ]; then
    PASS=$((PASS + 1))
    printf 'PASS %s %s → %s %s\n' "$2" "$3" "$4" "${5:-}"
  else
    FAIL=$((FAIL + 1))
    printf 'FAIL %s %s → %s %s\n' "$2" "$3" "$4" "${5:-}"
  fi
}
skip() { printf 'SKIP %s %s → %s\n' "$1" "$2" "$3"; }
# Nunca enviar tokens, contraseñas ni cuerpos completos a stdout.
request() {
  local name=$1 method=$2 path=$3
  shift 3
  local status
  if status=$(curl --silent --show-error --max-time 45 -o "$TMP/$name" -w '%{http_code}' \
      -X "$method" "$@" "$BASE$path"); then
    printf '%s' "$status"
  else
    printf '000'
  fi
}
check() {
  local method=$1 path=$2 code=$3 valid=$4 detail=${5:-}
  if [ "$code" = 200 ] && [ "$valid" = yes ]; then
    result pass "$method" "$BASE$path" "$code" "$detail"
  else
    result fail "$method" "$BASE$path" "$code" "$detail"
  fi
}
# stdin: JSON; argumento: expresión booleana Python sobre el objeto `d`.
json_ok() {
  "$PY" -c 'import json,os,sys; d=json.load(sys.stdin); sys.exit(0 if eval(sys.argv[1], {"__builtins__": {}, "d": d, "os": os, "isinstance": isinstance, "dict": dict, "list": list, "str": str, "set": set, "len": len, "all": all, "bool": bool, "type": type, "int": int}) else 1)' "$1" < "$TMP/$2"
}
valid() {
  if json_ok "$1" "$2" 2>/dev/null; then printf yes; else printf no; fi
}

if ! command -v curl >/dev/null || ! "$PY" -c 'import json' >/dev/null 2>&1; then
  echo 'Faltan curl o Python: configura NETPULSE_PYTHON con el Python del entorno.' >&2
  exit 2
fi
if ! "$PY" -c 'import websockets' >/dev/null 2>&1; then
  printf 'Falta websockets en %s; instala con: %s -m pip install websockets\n' "$PY" "$PY" >&2
  exit 2
fi
: "${NETPULSE_SMOKE_USER:?Define NETPULSE_SMOKE_USER (admin del despliegue)}"
: "${NETPULSE_SMOKE_PASSWORD:?Define NETPULSE_SMOKE_PASSWORD (contraseña del admin)}"

printf 'NetPulse · smoke completo · %s\n' "$BASE"
code=$(request health GET /api/health)
check GET /api/health "$code" "$(valid 'isinstance(d,dict)' health)"

login_data=$("$PY" -c 'import json,os; print(json.dumps({"username":os.environ["NETPULSE_SMOKE_USER"],"password":os.environ["NETPULSE_SMOKE_PASSWORD"]}))')
code=$(request login POST /api/auth/login -H 'Content-Type: application/json' -d "$login_data")
TOKEN=''
if [ "$code" = 200 ] && [ "$(valid 'isinstance(d.get("access_token"),str) and len(d["access_token"])>0' login)" = yes ]; then
  TOKEN=$("$PY" -c 'import json,sys; print(json.load(sys.stdin)["access_token"])' < "$TMP/login")
  result pass POST "$BASE/api/auth/login" "$code" 'JWT recibido'
else
  result fail POST "$BASE/api/auth/login" "$code" 'JWT ausente o JSON inválido; pruebas protegidas no ejecutadas'
fi

if [ -n "$TOKEN" ]; then
  AUTH=(-H "Authorization: Bearer $TOKEN")
  code=$(request me GET /api/auth/me "${AUTH[@]}")
  check GET /api/auth/me "$code" "$(valid 'd.get("username")==os.environ["NETPULSE_SMOKE_USER"] and d.get("role")=="admin"' me)" 'se requiere admin'

  code=$(request refresh POST /api/auth/refresh "${AUTH[@]}")
  NEW_TOKEN=''
  if [ "$code" = 200 ] && [ "$(valid 'isinstance(d.get("access_token"),str) and len(d["access_token"])>0' refresh)" = yes ]; then
    NEW_TOKEN=$("$PY" -c 'import json,sys; print(json.load(sys.stdin)["access_token"])' < "$TMP/refresh")
    result pass POST "$BASE/api/auth/refresh" "$code" 'JWT renovado'
  else
    result fail POST "$BASE/api/auth/refresh" "$code" 'sin JWT renovado'
  fi
  if [ -n "$NEW_TOKEN" ]; then
    AUTH=(-H "Authorization: Bearer $NEW_TOKEN")
    code=$(request renewed_me GET /api/auth/me "${AUTH[@]}")
    check GET '/api/auth/me (JWT renovado)' "$code" "$(valid 'd.get("role")=="admin"' renewed_me)"
  else
    result fail GET "$BASE/api/auth/me (JWT renovado)" 'no ejecutado' 'refresh no entregó token'
  fi
  code=$(request invalid_me GET /api/auth/me -H 'Authorization: Bearer token.invalido')
  if [ "$code" = 401 ]; then result pass GET "$BASE/api/auth/me (JWT inválido)" "$code";
  else result fail GET "$BASE/api/auth/me (JWT inválido)" "$code" 'se esperaba 401'; fi

  code=$(request devices GET /api/devices "${AUTH[@]}")
  check GET /api/devices "$code" "$(valid 'isinstance(d,list) and {"cisco-core-01","juniper-edge-01","switch-access-01","mikrotik-edge-01"}.issubset({x.get("id") for x in d if isinstance(x,dict)})' devices)" '4 equipos esperados'
  code=$(request groups GET /api/groups "${AUTH[@]}")
  check GET /api/groups "$code" "$(valid 'isinstance(d,list)' groups)"

  code=$(request sftp GET '/api/sftp/cisco-core-01/list?path=/' "${AUTH[@]}")
  if [ "$code" = 501 ]; then
    result fail GET "$BASE/api/sftp/cisco-core-01/list?path=/" "$code" 'premisa incumplida: el lab FRR debe ofrecer SFTP; no aceptar items=[]'
  else
    check GET '/api/sftp/cisco-core-01/list?path=/' "$code" "$(valid 'd.get("success") is True and d.get("device_id")=="cisco-core-01" and d.get("path")=="/" and isinstance(d.get("items"),list) and len(d["items"])>0' sftp)" 'items no vacíos'
  fi

  # Una sola conexión comprueba los tres eventos. Ignora output de la shell,
  # pero nunca errores ni un cierre prematuro. Timeout global para no colgar CI.
  WS_URL="${BASE/http:/ws:}/ws/terminal/cisco-core-01?fmt=json"
  WS_URL="${WS_URL/https:/wss:}"
  export WS_URL
  WS_TOKEN=${NEW_TOKEN:-$TOKEN} "$PY" - "$TMP/ws" <<'PY' && ws_status=0 || ws_status=$?
import asyncio, json, os, sys
from urllib.parse import quote
import websockets

async def main():
    url = os.environ['WS_URL'] + '&token=' + quote(os.environ['WS_TOKEN'], safe='')
    async with websockets.connect(url, open_timeout=35, close_timeout=5) as ws:
        for expected in ('connected', 'pong', 'closed'):
            while True:
                event = json.loads(await asyncio.wait_for(ws.recv(), timeout=40))
                if event.get('type') == 'output':
                    continue
                if event.get('type') != expected:
                    raise RuntimeError('esperado %s, recibido %s' % (expected, event.get('type')))
                if expected == 'connected' and event.get('device') != 'cisco-core-01':
                    raise RuntimeError('connected de otro equipo')
                if expected == 'closed' and event.get('code') != 'operator':
                    raise RuntimeError('closed sin motivo operator')
                with open(sys.argv[1], 'a', encoding='utf8') as f:
                    f.write(expected + '\n')
                if expected == 'connected':
                    await ws.send(json.dumps({'type': 'ping'}))
                elif expected == 'pong':
                    await ws.send(json.dumps({'type': 'close'}))
                break

try:
    asyncio.run(asyncio.wait_for(main(), timeout=95))
except Exception as exc:
    print('Terminal WS: %s' % exc, file=sys.stderr)
    sys.exit(1)
PY
  for event in connected pong closed; do
    if [ "$ws_status" -eq 0 ] && [ -f "$TMP/ws" ] && "$PY" -c 'import sys;sys.exit(0 if sys.argv[1] in open(sys.argv[2]).read().splitlines() else 1)' "$event" "$TMP/ws"; then
      result pass WS "$WS_URL" "$event"
    else
      result fail WS "$WS_URL" "$event no recibido"
    fi
  done

  code=$(request sites GET /api/remote/sites "${AUTH[@]}")
  check GET /api/remote/sites "$code" "$(valid 'isinstance(d,dict) and isinstance(d.get("generated_at"),str) and isinstance(d.get("sites"),list)' sites)" 'lista vacía permitida'
  if [[ "${NETPULSE_COLLECTOR_API_KEY:-}" == np_* ]]; then
    code=$(request whoami GET /api/remote/whoami -H "Authorization: Bearer $NETPULSE_COLLECTOR_API_KEY")
    check GET /api/remote/whoami "$code" "$(valid 'isinstance(d.get("site_id"),str) and bool(d["site_id"]) and isinstance(d.get("collector_id"),str) and bool(d["collector_id"])' whoami)" 'clave de colector'
  elif [ -n "${NETPULSE_COLLECTOR_API_KEY:-}" ]; then
    result fail GET "$BASE/api/remote/whoami" 'no ejecutado' 'NETPULSE_COLLECTOR_API_KEY no empieza por np_'
  else
    skip GET "$BASE/api/remote/whoami" 'sin NETPULSE_COLLECTOR_API_KEY=np_... en el entorno'
  fi

  code=$(request retention GET /api/retention/inventory "${AUTH[@]}")
  check GET /api/retention/inventory "$code" "$(valid 'isinstance(d,dict) and all(isinstance(d.get(k),dict) for k in ("prometheus","audit","backups"))' retention)" '3 secciones'
  # null debe estar presente para campos sin medida; solo conteos observados
  # (backups.archivos/uso_bytes y audit.registros con DB) admiten cero.
  check GET '/api/retention/inventory (null≠0)' "$code" "$(valid 'isinstance(d,dict) and all(isinstance(d.get(k),dict) for k in ("prometheus","audit","backups")) and all(k in d["prometheus"] and (d["prometheus"][k] is None or isinstance(d["prometheus"][k],str)) for k in ("retention_time","retention_size")) and all(k in d["audit"] and (d["audit"][k] is None or isinstance(d["audit"][k],str)) for k in ("mas_viejo",)) and (d["audit"].get("registros") is None or type(d["audit"]["registros"]) is int) and all(k in d["backups"] and (d["backups"][k] is None or isinstance(d["backups"][k],str)) for k in ("mas_viejo","ruta")) and (d["prometheus"].get("configurado") or (d["prometheus"]["retention_time"] is None and d["prometheus"]["retention_size"] is None)) and (d["backups"].get("archivos") != 0 or (d["backups"]["mas_viejo"] is None and d["backups"]["ruta"] is None))' retention)" 'sin medición se serializa null'
fi

code=$(request dashboard GET /)
for marker in v-sites v-retention v-netbox term-container; do
  if [ "$code" = 200 ] && "$PY" -c 'import sys;sys.exit(0 if ("id=\""+sys.argv[1]+"\"") in open(sys.argv[2],encoding="utf8").read() else 1)' "$marker" "$TMP/dashboard"; then
    result pass GET "$BASE/" "$code" "id=$marker"
  else
    result fail GET "$BASE/" "$code" "falta id=$marker"
  fi
done

printf '\n%d/%d pruebas OK\n' "$PASS" "$TOTAL"
[ "$FAIL" -eq 0 ]
