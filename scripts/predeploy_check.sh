#!/usr/bin/env bash
# NetPulse NOC — validación honesta previa al despliegue.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

usage() {
  cat <<'EOF'
Uso:
  scripts/predeploy_check.sh --url URL [opciones]

Opciones:
  --url URL                 Central NetPulse (obligatorio; http o https)
  --domain DOMINIO          Dominio para comprobar DNS, TCP 443 y TLS
  --expect-ip IP            IP que debe devolver DNS (requiere --domain)
  --collector-config RUTA   Ejecuta el --check real del colector
  --compose RUTA            Compose de producción para Docker y logs
  --json                    Añade un resumen JSON a la salida legible
  -h, --help                Muestra esta ayuda

Credenciales opcionales para la prueba WebSocket y /api/remote/sites:
  NETPULSE_SMOKE_USER, NETPULSE_SMOKE_PASSWORD
  NETPULSE_SMOKE_DEVICE (si falta, usa el primer equipo visible)
EOF
}

BASE_URL=""
DOMAIN=""
EXPECT_IP=""
COLLECTOR_CONFIG=""
COMPOSE_FILE=""
JSON_OUTPUT=0

while (($#)); do
  case "$1" in
    --url)
      (($# >= 2)) || { echo "Falta el valor de --url" >&2; exit 2; }
      BASE_URL=$2; shift 2 ;;
    --domain)
      (($# >= 2)) || { echo "Falta el valor de --domain" >&2; exit 2; }
      DOMAIN=$2; shift 2 ;;
    --expect-ip)
      (($# >= 2)) || { echo "Falta el valor de --expect-ip" >&2; exit 2; }
      EXPECT_IP=$2; shift 2 ;;
    --collector-config)
      (($# >= 2)) || { echo "Falta el valor de --collector-config" >&2; exit 2; }
      COLLECTOR_CONFIG=$2; shift 2 ;;
    --compose)
      (($# >= 2)) || { echo "Falta el valor de --compose" >&2; exit 2; }
      COMPOSE_FILE=$2; shift 2 ;;
    --json) JSON_OUTPUT=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Opción desconocida: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[ -n "$BASE_URL" ] || { echo "--url es obligatorio" >&2; usage >&2; exit 2; }
[ -z "$EXPECT_IP" ] || [ -n "$DOMAIN" ] || {
  echo "--expect-ip requiere --domain" >&2
  exit 2
}

for tool in curl python3 openssl; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "Falta la dependencia obligatoria: $tool" >&2
    exit 2
  }
done

URL_INFO=$(python3 - "$BASE_URL" <<'PY'
import sys
from urllib.parse import urlsplit
u = urlsplit(sys.argv[1])
if u.scheme not in {"http", "https"} or not u.hostname or u.username or u.password:
    raise SystemExit(2)
try:
    port = u.port or (443 if u.scheme == "https" else 80)
except ValueError:
    raise SystemExit(2)
print(u.scheme, u.hostname, port, sep="\t")
PY
) || { echo "--url debe ser una URL http(s) válida y sin credenciales" >&2; exit 2; }
IFS=$'\t' read -r URL_SCHEME URL_HOST URL_PORT <<<"$URL_INFO"
BASE_URL=${BASE_URL%/}

if [ -n "$DOMAIN" ]; then
  python3 - "$DOMAIN" <<'PY' >/dev/null || {
import sys
name = sys.argv[1]
if not name or "/" in name or ":" in name or any(c.isspace() for c in name):
    raise SystemExit(1)
PY
    echo "--domain debe ser un hostname sin esquema, puerto ni ruta" >&2
    exit 2
  }
fi
if [ -n "$EXPECT_IP" ]; then
  python3 - "$EXPECT_IP" <<'PY' >/dev/null || {
import ipaddress, sys
ipaddress.ip_address(sys.argv[1])
PY
    echo "--expect-ip no es una dirección IP válida" >&2
    exit 2
  }
fi

TMP=$(mktemp -d "${TMPDIR:-/tmp}/netpulse-predeploy.XXXXXX")
trap 'rm -rf -- "$TMP"' EXIT
mkdir -p "$TMP/results"
PASS=0
FAIL=0
SKIP=0
TOKEN_FILE="$TMP/token"

record() {
  local id=$1 name=$2 status=$3 detail=$4
  case "$status" in
    PASS) PASS=$((PASS + 1)) ;;
    FAIL) FAIL=$((FAIL + 1)) ;;
    SKIP) SKIP=$((SKIP + 1)) ;;
    *) echo "Estado interno inválido: $status" >&2; exit 2 ;;
  esac
  printf '%s' "$status" >"$TMP/results/$id.status"
  printf '%s' "$name" >"$TMP/results/$id.name"
  printf '%s' "$detail" >"$TMP/results/$id.detail"
  printf '%-4s %2d. %-24s %s\n' "$status" "$id" "$name" "$detail"
}

# Guarda cuerpo, cabeceras y código sin mostrar respuestas ni secretos.
http_get() {
  local label=$1 url=$2
  shift 2
  local code
  if code=$(curl --silent --show-error --location --max-time 30 \
      --dump-header "$TMP/$label.headers" --output "$TMP/$label.body" \
      --write-out '%{http_code}' "$@" "$url" 2>"$TMP/$label.error"); then
    printf '%s' "$code" >"$TMP/$label.code"
  else
    printf '000' >"$TMP/$label.code"
  fi
}

login_if_configured() {
  [ -s "$TOKEN_FILE" ] && return 0
  [ -n "${NETPULSE_SMOKE_USER:-}" ] && [ -n "${NETPULSE_SMOKE_PASSWORD:-}" ] || return 2
  local payload code
  payload=$(python3 - <<'PY'
import json, os
print(json.dumps({"username": os.environ["NETPULSE_SMOKE_USER"],
                  "password": os.environ["NETPULSE_SMOKE_PASSWORD"]}))
PY
)
  if code=$(curl --silent --show-error --max-time 30 --output "$TMP/login.body" \
      --write-out '%{http_code}' -H 'Content-Type: application/json' \
      --data "$payload" "$BASE_URL/api/auth/login" 2>"$TMP/login.error"); then
    :
  else
    code=000
  fi
  [ "$code" = 200 ] || return 1
  python3 - "$TMP/login.body" "$TOKEN_FILE" <<'PY'
import json, os, sys
try:
    token = json.load(open(sys.argv[1], encoding="utf-8")).get("access_token")
except (OSError, ValueError, AttributeError):
    token = None
if not isinstance(token, str) or not token:
    raise SystemExit(1)
fd = os.open(sys.argv[2], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    f.write(token)
PY
}

aggregate_status() {
  # Uso: aggregate_status fail_count skip_count
  if [ "$1" -gt 0 ]; then printf FAIL
  elif [ "$2" -gt 0 ]; then printf SKIP
  else printf PASS
  fi
}

join_details() {
  local separator="" item
  for item in "$@"; do
    printf '%s%s' "$separator" "$item"
    separator='; '
  done
}

printf 'NetPulse NOC · validación pre-despliegue\n'
printf 'Central: %s\n\n' "$BASE_URL"

# 1) DNS
if [ -z "$DOMAIN" ]; then
  record 1 "DNS" SKIP "sin --domain; no se comprobó resolución ni IP esperada"
else
  if DNS_ADDRESSES=$(python3 - "$DOMAIN" <<'PY'
import socket, sys
try:
    addresses = sorted({x[4][0] for x in socket.getaddrinfo(sys.argv[1], None)})
except OSError:
    raise SystemExit(1)
if not addresses:
    raise SystemExit(1)
print(", ".join(addresses))
PY
  ); then
    if [ -n "$EXPECT_IP" ]; then
      if python3 - "$DNS_ADDRESSES" "$EXPECT_IP" <<'PY'
import ipaddress, sys
found = {ipaddress.ip_address(x.strip()) for x in sys.argv[1].split(",")}
raise SystemExit(0 if ipaddress.ip_address(sys.argv[2]) in found else 1)
PY
      then
        record 1 "DNS" PASS "$DOMAIN resuelve a $DNS_ADDRESSES; incluye la IP esperada"
      else
        record 1 "DNS" FAIL "$DOMAIN resuelve a $DNS_ADDRESSES; no incluye --expect-ip"
      fi
    else
      record 1 "DNS" PASS "$DOMAIN resuelve a $DNS_ADDRESSES; IP esperada no fijada"
    fi
  else
    record 1 "DNS" FAIL "$DOMAIN no resolvió mediante el resolver del host"
  fi
fi

# 2) TCP 443 y redirección HTTP→HTTPS
c2_fail=0; c2_skip=0; c2_parts=()
TCP_HOST=${DOMAIN:-$URL_HOST}
if python3 - "$TCP_HOST" <<'PY'
import socket, sys
try:
    with socket.create_connection((sys.argv[1], 443), timeout=7):
        pass
except OSError:
    raise SystemExit(1)
PY
then c2_parts+=("TCP 443 abierto en $TCP_HOST"); else c2_parts+=("TCP 443 cerrado/no alcanzable en $TCP_HOST"); c2_fail=$((c2_fail + 1)); fi
if [ -n "$DOMAIN" ]; then
  REDIRECT_URL="http://$DOMAIN/"
elif [ "$URL_SCHEME" = http ]; then
  REDIRECT_URL="$BASE_URL/"
else
  REDIRECT_URL=$(python3 - "$URL_HOST" <<'PY'
import sys
host = f"[{sys.argv[1]}]" if ":" in sys.argv[1] else sys.argv[1]
print(f"http://{host}/")
PY
)
fi
if redirect_meta=$(curl --silent --show-error --max-time 15 --output /dev/null \
    --write-out '%{http_code}\t%{redirect_url}' "$REDIRECT_URL" 2>"$TMP/redirect.error"); then
  IFS=$'\t' read -r redirect_code redirect_location <<<"$redirect_meta"
else
  redirect_code=000; redirect_location=""
fi
case "$redirect_code" in
  301|302|307|308)
    if [[ "$redirect_location" == https://* ]]; then
      c2_parts+=("HTTP $redirect_code redirige a HTTPS")
    else
      c2_parts+=("HTTP $redirect_code sin destino HTTPS"); c2_fail=$((c2_fail + 1))
    fi ;;
  *) c2_parts+=("HTTP→HTTPS ausente (código $redirect_code)"); c2_fail=$((c2_fail + 1)) ;;
esac
c2_detail=$(join_details "${c2_parts[@]}")
record 2 "Red y redirección" "$(aggregate_status "$c2_fail" "$c2_skip")" "$c2_detail"

# 3) TLS
if [ -z "$DOMAIN" ]; then
  record 3 "TLS" SKIP "sin --domain; no se abrió una sesión TLS"
else
  if openssl s_client -connect "$DOMAIN:443" -servername "$DOMAIN" \
      -verify_hostname "$DOMAIN" -verify_return_error -showcerts </dev/null \
      >"$TMP/tls.txt" 2>"$TMP/tls.error"; then
    if openssl x509 -in "$TMP/tls.txt" -noout -issuer -startdate -enddate >"$TMP/cert.txt" 2>/dev/null &&
       openssl x509 -in "$TMP/tls.txt" -noout -checkend 0 >/dev/null 2>&1; then
      TLS_INFO=$(python3 - "$TMP/cert.txt" <<'PY'
from datetime import datetime, timezone
import math, sys
lines = open(sys.argv[1], encoding="utf-8", errors="replace").read().splitlines()
values = dict(line.split("=", 1) for line in lines if "=" in line)
end = datetime.strptime(values["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
days = max(0, math.floor((end - datetime.now(timezone.utc)).total_seconds() / 86400))
issuer = values.get("issuer", "desconocido").replace("\t", " ")
print(days, issuer, values.get("notBefore", "?"), values.get("notAfter", "?"), sep="\t")
PY
      ) || TLS_INFO=""
      if [ -n "$TLS_INFO" ]; then
        IFS=$'\t' read -r tls_days tls_issuer tls_start tls_end <<<"$TLS_INFO"
        tls_note="cadena, hostname y fechas válidos; emisor: $tls_issuer; vigencia: $tls_start — $tls_end; restan ${tls_days} días"
        if [ "$tls_days" -lt 15 ]; then tls_note="$tls_note; AVISO: menos de 15 días"; fi
        record 3 "TLS" PASS "$tls_note"
      else
        record 3 "TLS" FAIL "la sesión abrió, pero no se pudieron interpretar emisor/fechas"
      fi
    else
      record 3 "TLS" FAIL "certificado ausente, ilegible o vencido"
    fi
  else
    record 3 "TLS" FAIL "falló cadena, hostname o conexión TLS (verificación estricta)"
  fi
fi

# 4) Cabeceras de seguridad
http_get headers "$BASE_URL/"
headers_code=$(<"$TMP/headers.code")
if [ "$headers_code" = 200 ]; then
  if HEADER_RESULT=$(python3 - "$TMP/headers.headers" <<'PY'
import sys
raw = open(sys.argv[1], encoding="iso-8859-1").read()
# curl con redirects puede guardar varios bloques: inspeccionar el último.
blocks = [b for b in raw.replace("\r\n", "\n").split("\n\n") if b.startswith("HTTP/")]
lines = blocks[-1].splitlines()[1:] if blocks else []
headers = {}
for line in lines:
    if ":" in line:
        k, v = line.split(":", 1)
        headers[k.strip().lower()] = v.strip()
required = ["strict-transport-security", "x-content-type-options", "referrer-policy"]
missing = [x for x in required if not headers.get(x)]
print(", ".join(missing))
raise SystemExit(1 if missing else 0)
PY
  ); then
    record 4 "Cabeceras HTTP" PASS "HSTS, X-Content-Type-Options y Referrer-Policy presentes"
  else
    record 4 "Cabeceras HTTP" FAIL "faltan: ${HEADER_RESULT:-cabeceras requeridas}"
  fi
else
  record 4 "Cabeceras HTTP" FAIL "GET / devolvió HTTP $headers_code; no se validaron cabeceras"
fi

# 5) Salud, portada y JavaScript
c5_fail=0; c5_parts=()
for spec in "health|/api/health" "root|/" "core|/static/js/core.js"; do
  label=${spec%%|*}; path=${spec#*|}
  http_get "asset_$label" "$BASE_URL$path"
  code=$(<"$TMP/asset_$label.code")
  if [ "$code" = 200 ]; then
    c5_parts+=("$path=200")
  else
    c5_parts+=("$path=$code"); c5_fail=$((c5_fail + 1))
  fi
done
core_size=$(python3 - "$TMP/asset_core.body" <<'PY'
import os, sys
print(os.path.getsize(sys.argv[1]) if os.path.exists(sys.argv[1]) else 0)
PY
)
if [ "$core_size" -gt 10240 ]; then
  c5_parts+=("core.js=${core_size} B (>10 KiB)")
else
  c5_parts+=("core.js=${core_size} B (debe ser >10 KiB)"); c5_fail=$((c5_fail + 1))
fi
c5_detail=$(join_details "${c5_parts[@]}")
if [ "$c5_fail" -eq 0 ]; then record 5 "Aplicación HTTP" PASS "$c5_detail"; else record 5 "Aplicación HTTP" FAIL "$c5_detail"; fi

# 6) Login y terminal WebSocket real
if [ -z "${NETPULSE_SMOKE_USER:-}" ] || [ -z "${NETPULSE_SMOKE_PASSWORD:-}" ]; then
  record 6 "Terminal WebSocket" SKIP "faltan NETPULSE_SMOKE_USER/PASSWORD; no se generó JWT"
else
  if login_if_configured; then login_rc=0; else login_rc=$?; fi
  if [ "$login_rc" -ne 0 ]; then
    record 6 "Terminal WebSocket" FAIL "login no entregó un JWT válido (HTTP/red/credenciales)"
  else
    token=$(<"$TOKEN_FILE")
    device=${NETPULSE_SMOKE_DEVICE:-}
    if [ -z "$device" ]; then
      http_get ws_devices "$BASE_URL/api/devices" -H "Authorization: Bearer $token"
      if [ "$(<"$TMP/ws_devices.code")" = 200 ]; then
        device=$(python3 - "$TMP/ws_devices.body" <<'PY' || true
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError):
    data = []
for item in data if isinstance(data, list) else []:
    if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]:
        print(item["id"])
        break
PY
)
      fi
    fi
    if [ -z "$device" ]; then
      record 6 "Terminal WebSocket" SKIP "JWT válido, pero no hay NETPULSE_SMOKE_DEVICE ni equipo visible"
    else
      if NETPULSE_WS_TOKEN="$token" NETPULSE_WS_DEVICE="$device" \
          python3 - "$BASE_URL" "$TMP/ws.result" <<'PY'
import base64, hashlib, json, os, secrets, socket, ssl, struct, sys, time
from urllib.parse import quote, urlsplit

base, result_path = sys.argv[1:]
device = os.environ["NETPULSE_WS_DEVICE"]
token = os.environ["NETPULSE_WS_TOKEN"]
u = urlsplit(base)
tls = u.scheme == "https"
host = u.hostname
port = u.port or (443 if tls else 80)
base_path = u.path.rstrip("/")
target = f"{base_path}/ws/terminal/{quote(device, safe='')}?fmt=json&token={quote(token, safe='')}"
raw = socket.create_connection((host, port), timeout=15)
if tls:
    raw = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
raw.settimeout(35)
key = base64.b64encode(secrets.token_bytes(16)).decode()
display_host = f"[{host}]" if ":" in host else host
host_header = display_host if port in ({443} if tls else {80}) else f"{display_host}:{port}"
request = (f"GET {target} HTTP/1.1\r\nHost: {host_header}\r\nUpgrade: websocket\r\n"
           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
           "Sec-WebSocket-Version: 13\r\n\r\n")
raw.sendall(request.encode("ascii"))
header = b""
while b"\r\n\r\n" not in header and len(header) < 65536:
    chunk = raw.recv(4096)
    if not chunk:
        break
    header += chunk
head, sep, pending = header.partition(b"\r\n\r\n")
if not sep or not head.startswith(b"HTTP/1.1 101"):
    raise RuntimeError("el servidor no aceptó el upgrade WebSocket")
headers = {}
for line in head.split(b"\r\n")[1:]:
    if b":" in line:
        k, v = line.split(b":", 1)
        headers[k.strip().lower()] = v.strip()
expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
if headers.get(b"sec-websocket-accept") != expected:
    raise RuntimeError("Sec-WebSocket-Accept inválido")

buf = bytearray(pending)
def exact(n):
    while len(buf) < n:
        chunk = raw.recv(max(4096, n - len(buf)))
        if not chunk:
            raise EOFError("cierre TCP antes del frame WebSocket")
        buf.extend(chunk)
    out = bytes(buf[:n]); del buf[:n]
    return out

def recv_frame():
    first, second = exact(2)
    opcode = first & 0x0f
    length = second & 0x7f
    if length == 126:
        length = struct.unpack("!H", exact(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", exact(8))[0]
    mask = exact(4) if second & 0x80 else None
    payload = exact(length)
    if mask:
        payload = bytes(x ^ mask[i % 4] for i, x in enumerate(payload))
    return opcode, payload

def send_frame(opcode, payload=b""):
    mask = secrets.token_bytes(4)
    encoded = bytes(x ^ mask[i % 4] for i, x in enumerate(payload))
    n = len(payload)
    prefix = bytes([0x80 | opcode])
    if n < 126:
        prefix += bytes([0x80 | n])
    elif n < 65536:
        prefix += bytes([0x80 | 126]) + struct.pack("!H", n)
    else:
        prefix += bytes([0x80 | 127]) + struct.pack("!Q", n)
    raw.sendall(prefix + mask + encoded)

def send_json(obj):
    send_frame(1, json.dumps(obj, separators=(",", ":")).encode())

connected = closed_event = close_frame = False
deadline = time.monotonic() + 90
while time.monotonic() < deadline:
    opcode, payload = recv_frame()
    if opcode == 9:
        send_frame(10, payload)
        continue
    if opcode == 8:
        close_code = struct.unpack("!H", payload[:2])[0] if len(payload) >= 2 else 1005
        close_frame = close_code == 1000
        send_frame(8, payload)
        break
    if opcode != 1:
        continue
    event = json.loads(payload.decode("utf-8"))
    kind = event.get("type")
    if kind == "error":
        raise RuntimeError("el terminal devolvió un evento error")
    if kind == "connected":
        if event.get("device") != device:
            raise RuntimeError("connected corresponde a otro equipo")
        connected = True
        send_json({"type": "close"})
    elif kind == "closed":
        if event.get("code") != "operator":
            raise RuntimeError("cierre de aplicación no fue solicitado por operador")
        closed_event = True
if not (connected and closed_event and close_frame):
    raise RuntimeError("faltó connected, closed(operator) o close frame")
with open(result_path, "w", encoding="utf-8") as f:
    f.write("connected + closed(operator) + close frame 1000/normal")
raw.close()
PY
      then
        record 6 "Terminal WebSocket" PASS "equipo $device: $(<"$TMP/ws.result")"
      else
        record 6 "Terminal WebSocket" FAIL "equipo $device: no hubo connected y cierre limpio (sin exponer JWT)"
      fi
    fi
  fi
fi

# 7) Estado y permisos del contenedor API
if [ -z "$COMPOSE_FILE" ]; then
  record 7 "Contenedor y permisos" SKIP "sin --compose; Docker no fue inspeccionado"
elif [ ! -f "$COMPOSE_FILE" ]; then
  record 7 "Contenedor y permisos" FAIL "el archivo --compose no existe o no es regular"
elif ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  record 7 "Contenedor y permisos" SKIP "Docker/Compose no está disponible o el daemon no responde"
else
  cid=$(docker compose -f "$COMPOSE_FILE" ps -q api 2>"$TMP/compose_ps.error" || true)
  if [ -z "$cid" ]; then
    record 7 "Contenedor y permisos" FAIL "Compose no reporta un contenedor api en ejecución"
  else
    if docker inspect "$cid" >"$TMP/api.inspect.json" 2>"$TMP/api_inspect.error" &&
       docker exec -i "$cid" python - <<'PY' >"$TMP/api.writecheck" 2>/dev/null
import os, tempfile
paths = ["/app/config", "/app/runtime", "/app/audit", "/app/backups"]
for path in paths:
    with tempfile.NamedTemporaryFile(prefix=".predeploy-", dir=path):
        pass
print("ok")
PY
    then write_ok=1; else write_ok=0; fi
    if INSPECT_RESULT=$(python3 - "$TMP/api.inspect.json" "$write_ok" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))[0]
except (OSError, ValueError, IndexError, TypeError):
    raise SystemExit(1)
problems = []
health = d.get("State", {}).get("Health", {}).get("Status")
if health != "healthy": problems.append(f"health={health or 'ausente'}")
user = str(d.get("Config", {}).get("User") or "")
if user.split(":", 1)[0] != "10001": problems.append(f"usuario={user or 'root/ausente'}")
if d.get("HostConfig", {}).get("ReadonlyRootfs") is not True: problems.append("rootfs no es read-only")
mounts = {m.get("Destination"): m for m in d.get("Mounts", [])}
for path in ("/app/config", "/app/runtime", "/app/audit", "/app/backups"):
    m = mounts.get(path, {})
    if m.get("Type") != "bind" or m.get("RW") is not True:
        problems.append(f"{path} no es bind RW")
if sys.argv[2] != "1": problems.append("falló escritura temporal en binds")
if problems:
    print("; ".join(problems)); raise SystemExit(1)
print("healthy; uid 10001; rootfs RO; config/runtime/audit/backups son binds RW y aceptan escritura")
PY
    ); then
      record 7 "Contenedor y permisos" PASS "$INSPECT_RESULT"
    else
      record 7 "Contenedor y permisos" FAIL "${INSPECT_RESULT:-no se pudo inspeccionar o escribir en los binds}"
    fi
  fi
fi

# 8) Logs del proxy sin revelar coincidencias
if [ -z "$COMPOSE_FILE" ]; then
  record 8 "Logs del proxy" SKIP "sin --compose; no hay fuente de logs Caddy/nginx"
elif [ ! -f "$COMPOSE_FILE" ]; then
  record 8 "Logs del proxy" SKIP "--compose inválido; no se leyeron logs"
elif ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  record 8 "Logs del proxy" SKIP "Docker/Compose no está disponible o el daemon no responde"
else
  proxy_services=()
  while IFS= read -r service; do
    case "$service" in *caddy*|*Caddy*|*nginx*|*Nginx*|*proxy*|*Proxy*) proxy_services+=("$service") ;; esac
  done < <(docker compose -f "$COMPOSE_FILE" config --services 2>/dev/null || true)
  if [ "${#proxy_services[@]}" -eq 0 ]; then
    record 8 "Logs del proxy" FAIL "Compose no contiene un servicio Caddy/nginx/proxy identificable"
  else
    : >"$TMP/proxy.logs"
    logs_ok=1
    for service in "${proxy_services[@]}"; do
      if ! docker compose -f "$COMPOSE_FILE" logs --no-color --tail 1000 "$service" \
          >>"$TMP/proxy.logs" 2>"$TMP/proxy_logs.error"; then logs_ok=0; fi
    done
    if [ "$logs_ok" -ne 1 ]; then
      record 8 "Logs del proxy" FAIL "no se pudieron leer los últimos logs del proxy"
    elif [ ! -s "$TMP/proxy.logs" ]; then
      record 8 "Logs del proxy" SKIP "el proxy no entregó líneas de log; no hay evidencia que buscar"
    elif match_count=$(python3 - "$TMP/proxy.logs" <<'PY'
import sys
raw = open(sys.argv[1], "rb").read().lower()
print(raw.count(b"token="))
raise SystemExit(1 if b"token=" in raw else 0)
PY
    ); then
      record 8 "Logs del proxy" PASS "últimas 1000 líneas por servicio: ninguna contiene token="
    else
      record 8 "Logs del proxy" FAIL "se detectaron $match_count coincidencias de token=; valores ocultos"
    fi
  fi
fi

# 9) Colector: --check real + site fresh
if [ -z "$COLLECTOR_CONFIG" ]; then
  record 9 "Colector remoto" SKIP "sin --collector-config; no se ejecutó --check ni se consultó el site"
elif [ ! -r "$COLLECTOR_CONFIG" ]; then
  record 9 "Colector remoto" FAIL "la configuración del colector no existe o no es legible"
else
  if python3 -m app.remote_collector --config "$COLLECTOR_CONFIG" --check \
      >"$TMP/collector_check.out" 2>"$TMP/collector_check.error"; then collector_ok=1; else collector_ok=0; fi
  if [ "$collector_ok" -ne 1 ]; then
    record 9 "Colector remoto" FAIL "el --check real del colector falló; salida sensible omitida"
  else
    site_id=$(python3 - "$TMP/collector_check.out" <<'PY' || true
import json, sys
text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
try:
    data = json.loads(text)
except ValueError:
    data = {}
site = data.get("site_id") if isinstance(data, dict) else None
if isinstance(site, str) and site:
    print(site)
PY
)
    if [ -z "$site_id" ]; then
      record 9 "Colector remoto" FAIL "--check terminó 0, pero no declaró site_id en JSON"
    else
      if login_if_configured; then collector_login_rc=0; else collector_login_rc=$?; fi
      if [ "$collector_login_rc" -eq 2 ]; then
        record 9 "Colector remoto" SKIP "--check OK para $site_id; faltan credenciales smoke para verificar /api/remote/sites"
      elif [ "$collector_login_rc" -ne 0 ]; then
        record 9 "Colector remoto" FAIL "--check OK para $site_id; login falló y no se pudo verificar frescura"
      else
        token=$(<"$TOKEN_FILE")
        http_get collector_sites "$BASE_URL/api/remote/sites" -H "Authorization: Bearer $token"
        sites_code=$(<"$TMP/collector_sites.code")
        if [ "$sites_code" = 200 ] && python3 - "$TMP/collector_sites.body" "$site_id" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError):
    raise SystemExit(1)
for site in data.get("sites", []) if isinstance(data, dict) else []:
    if isinstance(site, dict) and site.get("site_id") == sys.argv[2] and site.get("contact") == "fresh":
        raise SystemExit(0)
raise SystemExit(1)
PY
        then
          record 9 "Colector remoto" PASS "--check OK; site $site_id aparece fresh en /api/remote/sites"
        else
          record 9 "Colector remoto" FAIL "--check OK; site $site_id ausente/stale o endpoint HTTP $sites_code"
        fi
      fi
    fi
  fi
fi

# 10) Operación: NTP, disco, SQLite y backup
c10_fail=0; c10_skip=0; c10_parts=()
if ! command -v timedatectl >/dev/null 2>&1; then
  c10_parts+=("NTP SKIP: timedatectl no disponible"); c10_skip=$((c10_skip + 1))
elif ntp_sync=$(timedatectl show -p NTPSynchronized --value 2>/dev/null); then
  if [ "$ntp_sync" != yes ]; then
    c10_parts+=("NTP FAIL: NTPSynchronized=$ntp_sync"); c10_fail=$((c10_fail + 1))
  elif ntp_offset=$(timedatectl timesync-status --no-pager 2>/dev/null | python3 -c '
import re, sys
text=sys.stdin.read()
m=re.search(r"Offset:\s*([+-]?[0-9.]+)\s*(ns|us|µs|ms|s|min|h)\b", text)
if not m: raise SystemExit(1)
factors={"ns":1e-9,"us":1e-6,"µs":1e-6,"ms":1e-3,"s":1,"min":60,"h":3600}
print(abs(float(m.group(1))*factors[m.group(2)]))
'); then
    if python3 - "$ntp_offset" <<'PY'
import sys
raise SystemExit(0 if float(sys.argv[1]) < 5 else 1)
PY
    then c10_parts+=("NTP PASS: skew ${ntp_offset}s (<5s)")
    else c10_parts+=("NTP FAIL: skew ${ntp_offset}s (>=5s)"); c10_fail=$((c10_fail + 1)); fi
  else
    c10_parts+=("NTP SKIP: sincronizado, pero timedatectl no expuso offset"); c10_skip=$((c10_skip + 1))
  fi
else
  c10_parts+=("NTP SKIP: timedatectl no opera en este host"); c10_skip=$((c10_skip + 1))
fi

free_pct=$(python3 - "$ROOT" <<'PY'
import shutil, sys
u = shutil.disk_usage(sys.argv[1])
print(round(u.free * 100 / u.total, 1))
PY
)
if python3 - "$free_pct" <<'PY'
import sys
raise SystemExit(0 if float(sys.argv[1]) > 20 else 1)
PY
then c10_parts+=("disco PASS: ${free_pct}% libre")
else c10_parts+=("disco FAIL: ${free_pct}% libre (debe ser >20%)"); c10_fail=$((c10_fail + 1)); fi

sqlite_found=0
for db in "$ROOT/audit/audit.db" "$ROOT/runtime/remote_collector.db"; do
  [ -e "$db" ] || continue
  sqlite_found=$((sqlite_found + 1))
  db_name=${db#"$ROOT/"}
  if python3 - "$db" <<'PY'
import sqlite3, sys
try:
    conn = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True, timeout=10)
    result = conn.execute("PRAGMA integrity_check").fetchone()
    conn.close()
except (OSError, sqlite3.Error):
    raise SystemExit(1)
raise SystemExit(0 if result and result[0] == "ok" else 1)
PY
  then c10_parts+=("$db_name PASS: integrity_check=ok")
  else c10_parts+=("$db_name FAIL: integrity_check no fue ok"); c10_fail=$((c10_fail + 1)); fi
done
if [ "$sqlite_found" -eq 0 ]; then
  c10_parts+=("SQLite SKIP: bases audit/remote no accesibles o ausentes"); c10_skip=$((c10_skip + 1))
fi

if [ ! -d "$ROOT/backups" ] || [ ! -r "$ROOT/backups" ]; then
  c10_parts+=("backup SKIP: directorio no accesible o ausente"); c10_skip=$((c10_skip + 1))
else
  if backup_age=$(python3 - "$ROOT/backups" <<'PY'
import pathlib, sys, time
suffixes = (".cfg", ".json", ".tgz", ".tar.gz", ".zip", ".sqlite", ".sqlite3", ".db", ".bak")
files = [p for p in pathlib.Path(sys.argv[1]).iterdir()
         if p.is_file() and not p.name.startswith(".") and p.name.lower().endswith(suffixes)]
if not files:
    raise SystemExit(2)
latest = max(p.stat().st_mtime for p in files)
print(round((time.time() - latest) / 86400, 1))
PY
  ); then
    if python3 - "$backup_age" <<'PY'
import sys
raise SystemExit(0 if float(sys.argv[1]) < 7 else 1)
PY
    then c10_parts+=("backup PASS: último hace ${backup_age} días")
    else c10_parts+=("backup FAIL: último hace ${backup_age} días (debe ser <7)"); c10_fail=$((c10_fail + 1)); fi
  else
    backup_rc=$?
    if [ "$backup_rc" -eq 2 ]; then
      c10_parts+=("backup FAIL: directorio accesible sin artefactos de backup reconocidos"); c10_fail=$((c10_fail + 1))
    else
      c10_parts+=("backup FAIL: no se pudo determinar antigüedad"); c10_fail=$((c10_fail + 1))
    fi
  fi
fi
c10_detail=$(join_details "${c10_parts[@]}")
record 10 "Operación" "$(aggregate_status "$c10_fail" "$c10_skip")" "$c10_detail"

printf '\nResumen: PASS=%d FAIL=%d SKIP=%d TOTAL=10\n' "$PASS" "$FAIL" "$SKIP"
if [ "$JSON_OUTPUT" -eq 1 ]; then
  python3 - "$TMP/results" "$PASS" "$FAIL" "$SKIP" "$BASE_URL" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
checks = []
for i in range(1, 11):
    checks.append({
        "id": i,
        "name": (root / f"{i}.name").read_text(encoding="utf-8"),
        "status": (root / f"{i}.status").read_text(encoding="utf-8"),
        "detail": (root / f"{i}.detail").read_text(encoding="utf-8"),
    })
print(json.dumps({
    "url": sys.argv[5],
    "summary": {"pass": int(sys.argv[2]), "fail": int(sys.argv[3]),
                "skip": int(sys.argv[4]), "total": 10},
    "checks": checks,
}, ensure_ascii=False, separators=(",", ":")))
PY
fi

[ "$FAIL" -eq 0 ]
