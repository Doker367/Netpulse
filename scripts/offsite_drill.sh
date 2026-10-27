#!/usr/bin/env bash
# ============================================================
# offsite_drill.sh — prueba real del backup offsite (S3 + SFTP)
# ============================================================
# Levanta dos dependencias desechables en Docker (S3 compatible y SFTP),
# arranca la API de ESTE worktree en un puerto libre y demuestra, por HTTP:
#
#   backup local -> export -> objeto remoto verificado (tamaño/hash)
#   -> /status honesto -> verify -> restore a un directorio temporal
#   -> comparación SHA-256 con el original
#
# Después prueba los fallos: credenciales malas, bucket inexistente y
# endpoint caído. En todos ellos exige error claro (502 con causa), que el
# backup local siga intacto y que no haya un 500 genérico.
#
# S3: por defecto se usa VersityGW (implementación S3 real sobre POSIX)
# porque la imagen oficial de MinIO ya no se publica en Docker Hub. Si
# tienes MinIO disponible, exporta NP_DRILL_S3_IMAGE=minio/minio:latest y
# se arranca con su comando/healthcheck. El drill se niega a fingir PASS:
# si un contenedor o la API no arrancan, sale != 0.
#
# SFTP: atmoz/sftp. En Apple Silicon corre con emulación amd64.
#
# Uso:
#   NETPULSE_PYTHON=/ruta/.venv/bin/python scripts/offsite_drill.sh
#   scripts/offsite_drill.sh --keep     # conserva $TMP y logs
#   scripts/offsite_drill.sh --s3-only  # omite las fases SFTP
#   scripts/offsite_drill.sh --sftp-only
#
# Variables:
#   NETPULSE_PYTHON          Python con requirements del repo (obligatorio si
#                            no existe $ROOT/.venv/bin/python)
#   NP_DRILL_KEEP=1          no borrar el directorio temporal
#   NP_DRILL_S3_IMAGE        imagen S3 (default versity/versitygw:latest)
#   NP_DRILL_S3_PORT         puerto host S3 (default: libre)
#   NP_DRILL_SFTP_PORT       puerto host SFTP (default: libre)
#   NP_DRILL_API_PORT        puerto API de la primera fase (default: libre)
#   NP_DRILL_TIMEOUT=60      segundos máximos por espera/arranque
#
# Puertos: nunca se usan 9000/9001/2222 por defecto; se eligen libres y se
# publican solo en 127.0.0.1. El trap limpia contenedores, API y archivos
# del drill incluso con SIGINT/SIGTERM.
#
# Requisitos: Docker Engine/Desktop, curl, Python con boto3, paramiko,
# fastapi y uvicorn (el venv del repo).
# ============================================================

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PY=${NETPULSE_PYTHON:-$ROOT/.venv/bin/python}
S3_IMAGE=${NP_DRILL_S3_IMAGE:-versity/versitygw:latest}
WAIT_SECS=${NP_DRILL_TIMEOUT:-60}
KEEP=${NP_DRILL_KEEP:-0}
RUN_S3=1
RUN_SFTP=1
for arg in "$@"; do
    case "$arg" in
        --keep) KEEP=1 ;;
        --s3-only) RUN_SFTP=0 ;;
        --sftp-only) RUN_S3=0 ;;
        -h|--help) sed -n '2,58p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) printf 'opción desconocida: %s\n' "$arg" >&2; exit 2 ;;
    esac
done

TMP=""
S3_NAME="netpulse-offsite-drill-s3-$$"
SFTP_NAME="netpulse-offsite-drill-sftp-$$"
S3_PORT=""
SFTP_PORT=""
S3_BUCKET="netpulse-offsite-drill"
S3_PREFIX="netpulse"
S3_ACCESS="npdrill"
S3_SECRET="npdrill-secret-13o"
S3_FLAVOR="s3gw"
SFTP_USER="npdrill"
SFTP_PASS="npdrill-sftp-13o"
SFTP_DIR="netpulse-backups"
API_PID=""
API_PORT=""
API_LOG=""
TOKEN=""
JWT_SECRET="offsite-drill-$(date +%s)-$$"
PASS=0
FAIL=0
DRILL_FILES=()
SNAP_DIR=""
CONFIG_SNAPSHOTS=(devices.yaml .fernet_key scheduler_jobs.json api_keys.json maintenance_windows.json)

log()  { printf '[offsite] %s\n' "$*"; }
fail() { printf '[offsite] ERROR: %s\n' "$*" >&2; exit 1; }
pass() { PASS=$((PASS + 1)); printf 'PASS %s\n' "$*"; }
bad()  { FAIL=$((FAIL + 1)); printf 'FAIL %s\n' "$*"; }

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

json_expr() {
    "$PY" - "$1" "$2" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    d = json.load(fh)
allowed = {"d": d, "len": len, "all": all, "any": any, "str": str,
           "int": int, "float": float, "bool": bool, "isinstance": isinstance,
           "dict": dict, "list": list, "set": set, "sorted": sorted}
sys.exit(0 if eval(sys.argv[2], {"__builtins__": {}}, allowed) else 1)
PY
}

dump_result() {
    "$PY" - "$1" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception as exc:
    print(f"  (respuesta no JSON: {exc})")
    raise SystemExit
keep = {k: d.get(k) for k in ("status", "backend", "uploaded", "failed",
                              "restored", "local_intact", "manifest",
                              "error", "errors", "warnings", "detail") if k in d}
print("  " + json.dumps(keep, ensure_ascii=False)[:1200])
PY
}

expect() {
    local desc=$1 file=$2 expr=$3
    local code="?"
    [[ -f "$TMP/results/.last_code" ]] && code=$(cat "$TMP/results/.last_code")
    if json_expr "$file" "$expr" 2>/dev/null; then
        pass "$desc"
    else
        bad "$desc (HTTP $code)"
        dump_result "$file"
    fi
}

fail_case() {
    local desc=$1 file=$2 code=$3 expected_code=$4
    if [[ "$code" == "$expected_code" ]] && json_expr "$file" \
        "d.get('error',{}).get('code') not in (None,'partial_failure')"; then
        pass "$desc → HTTP $code, $("$PY" -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d.get("error",{}).get("code"))' "$file")"
    else
        bad "$desc → HTTP $code (esperado $expected_code con error clasificado)"
        dump_result "$file"
    fi
}

free_port() {
    "$PY" - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
}

pick_port() {
    local candidate
    while :; do
        candidate=$(free_port)
        case "$candidate" in 9000|9001|2222|80|443) continue ;; esac
        printf '%s' "$candidate"
        return
    done
}

cleanup() {
    rc=$?
    trap - EXIT INT TERM
    set +e
    stop_api
    if [[ -n "$S3_NAME" ]]; then docker rm -f "$S3_NAME" >/dev/null 2>&1; fi
    if [[ -n "$SFTP_NAME" ]]; then docker rm -f "$SFTP_NAME" >/dev/null 2>&1; fi
    if (( ${#DRILL_FILES[@]} > 0 )); then
        for path in "${DRILL_FILES[@]}"; do rm -f -- "$path"; done
    fi
    if [[ -n "$SNAP_DIR" && -d "$SNAP_DIR" ]]; then
        for name in "${CONFIG_SNAPSHOTS[@]}"; do
            if [[ -f "$SNAP_DIR/$name" ]]; then
                cp -p "$SNAP_DIR/$name" "$ROOT/config/$name"
            elif [[ -f "$SNAP_DIR/$name.absent" ]]; then
                rm -f -- "$ROOT/config/$name"
            fi
        done
    fi
    if [[ "${AUDIT_EXISTED:-1}" == "0" ]]; then rm -rf -- "$ROOT/audit"; fi
    if [[ "${BACKUPS_EXISTED:-1}" == "0" ]]; then rm -rf -- "$ROOT/backups"; fi
    if [[ -n "$TMP" && -d "$TMP" ]]; then
        if [[ "$KEEP" == "1" ]]; then
            printf '[offsite] KEEP=1: temporales en %s\n' "$TMP" >&2
        else
            rm -rf -- "$TMP"
        fi
    fi
    exit "$rc"
}
trap 'exit 130' INT TERM
trap cleanup EXIT

stop_api() {
    if [[ -n "$API_PID" ]] && kill -0 "$API_PID" >/dev/null 2>&1; then
        kill "$API_PID" >/dev/null 2>&1
        for _ in $(seq 1 20); do
            kill -0 "$API_PID" >/dev/null 2>&1 || break
            sleep 0.1
        done
        kill -9 "$API_PID" >/dev/null 2>&1 || true
    fi
    if [[ -n "$API_PID" ]]; then wait "$API_PID" >/dev/null 2>&1 || true; fi
    API_PID=""
}

port_free() {
    "$PY" - "$1" <<'PY'
import socket, sys
s = socket.socket()
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    raise SystemExit(1)
finally:
    s.close()
PY
}

# ── 0. Prerrequisitos ───────────────────────────────────────
for cmd in docker curl python3; do
    command -v "$cmd" >/dev/null 2>&1 || fail "falta $cmd en el PATH"
done
[[ -x "$PY" ]] || fail "no existe el Python $PY; exporta NETPULSE_PYTHON con el venv del repo"
"$PY" -c 'import boto3, paramiko, fastapi, uvicorn' >/dev/null 2>&1 \
    || fail "$PY no tiene boto3/paramiko/fastapi/uvicorn"
docker info >/dev/null 2>&1 || fail "Docker no responde (¿Docker Desktop/Colima arrancado?)"

if [[ "$S3_IMAGE" == *minio* ]]; then S3_FLAVOR=minio; fi

# macOS: Docker solo comparte $HOME; el volumen S3 vive ahí y el trap lo borra.
if [[ "$(uname -s)" == "Darwin" ]]; then
    TMP=$(mktemp -d "${HOME}/.netpulse-offsite-drill.XXXXXX")
else
    TMP=$(mktemp -d "${TMPDIR:-/tmp}/netpulse-offsite-drill.XXXXXX")
fi
SNAP_DIR="$TMP/config-snapshot"
mkdir -p "$SNAP_DIR" "$TMP/s3data" "$TMP/results"
for name in "${CONFIG_SNAPSHOTS[@]}"; do
    if [[ -f "$ROOT/config/$name" ]]; then
        cp -p "$ROOT/config/$name" "$SNAP_DIR/$name"
    else
        : > "$SNAP_DIR/$name.absent"
    fi
done
# La API siembra audit/ y backups/ al importar; se limpian si no existían.
AUDIT_EXISTED=0; [[ -d "$ROOT/audit" ]] && AUDIT_EXISTED=1
BACKUPS_EXISTED=0; [[ -d "$ROOT/backups" ]] && BACKUPS_EXISTED=1

S3_PORT=${NP_DRILL_S3_PORT:-$(pick_port)}
SFTP_PORT=${NP_DRILL_SFTP_PORT:-$(pick_port)}
while [[ "$SFTP_PORT" == "$S3_PORT" ]]; do SFTP_PORT=$(pick_port); done
port_free "$S3_PORT" || fail "puerto S3 $S3_PORT ocupado; elige otro con NP_DRILL_S3_PORT"
port_free "$SFTP_PORT" || fail "puerto SFTP $SFTP_PORT ocupado; elige otro con NP_DRILL_SFTP_PORT"

log "raíz=$ROOT  python=$PY"
log "S3=$S3_IMAGE (:$S3_PORT)  SFTP=atmoz/sftp (:$SFTP_PORT)  temporales=$TMP"

# ── 1. Dependencias ─────────────────────────────────────────
if [[ "$RUN_S3" == "1" ]]; then
    docker image inspect "$S3_IMAGE" >/dev/null 2>&1 || {
        log "descargando $S3_IMAGE"
        docker pull "$S3_IMAGE" >/dev/null || fail "no se pudo descargar $S3_IMAGE"
    }
fi
if [[ "$RUN_SFTP" == "1" ]]; then
    docker image inspect atmoz/sftp:latest >/dev/null 2>&1 || {
        log "descargando atmoz/sftp:latest"
        docker pull atmoz/sftp:latest >/dev/null || fail "no se pudo descargar atmoz/sftp"
    }
fi

start_s3() {
    docker rm -f "$S3_NAME" >/dev/null 2>&1 || true
    if [[ "$S3_FLAVOR" == "minio" ]]; then
        docker run -d --name "$S3_NAME" --label netpulse.offsite.drill=1 \
            -p "127.0.0.1:$S3_PORT:9000" \
            -e MINIO_ROOT_USER="$S3_ACCESS" -e MINIO_ROOT_PASSWORD="$S3_SECRET" \
            -v "$TMP/s3data:/data" "$S3_IMAGE" \
            server /data --address :9000 --console-address :9001 >/dev/null
    else
        docker run -d --name "$S3_NAME" --label netpulse.offsite.drill=1 \
            -p "127.0.0.1:$S3_PORT:7070" \
            -e ROOT_ACCESS_KEY="$S3_ACCESS" -e ROOT_SECRET_KEY="$S3_SECRET" \
            -v "$TMP/s3data:/data" "$S3_IMAGE" posix /data >/dev/null
    fi
    local ok=0
    for _ in $(seq 1 "$WAIT_SECS"); do
        if "$PY" - "$S3_PORT" "$S3_ACCESS" "$S3_SECRET" <<'PY' >/dev/null 2>&1
import boto3, sys
from botocore.config import Config
c = boto3.client("s3", endpoint_url=f"http://127.0.0.1:{sys.argv[1]}",
                 region_name="us-east-1", aws_access_key_id=sys.argv[2],
                 aws_secret_access_key=sys.argv[3],
                 config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 1}))
c.list_buckets()
PY
        then ok=1; break; fi
        sleep 1
    done
    if [[ "$ok" != "1" ]]; then
        docker logs --tail 40 "$S3_NAME" >&2 || true
        fail "S3 no arrancó en ${WAIT_SECS}s"
    fi
    "$PY" - "$S3_PORT" "$S3_ACCESS" "$S3_SECRET" "$S3_BUCKET" <<'PY' >/dev/null
import boto3, sys
from botocore.config import Config
c = boto3.client("s3", endpoint_url=f"http://127.0.0.1:{sys.argv[1]}",
                 region_name="us-east-1", aws_access_key_id=sys.argv[2],
                 aws_secret_access_key=sys.argv[3],
                 config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 1}))
try:
    c.create_bucket(Bucket=sys.argv[4])
except c.exceptions.BucketAlreadyOwnedByYou:
    pass
PY
    log "S3 listo: http://127.0.0.1:$S3_PORT bucket=$S3_BUCKET ($S3_FLAVOR)"
}

start_sftp() {
    docker rm -f "$SFTP_NAME" >/dev/null 2>&1 || true
    docker run -d --name "$SFTP_NAME" --label netpulse.offsite.drill=1 \
        -p "127.0.0.1:$SFTP_PORT:22" \
        -e "SFTP_USERS=$SFTP_USER:$SFTP_PASS:1001:1001:$SFTP_DIR" \
        atmoz/sftp:latest >/dev/null
    local ok=0
    for _ in $(seq 1 "$WAIT_SECS"); do
        if "$PY" - "$SFTP_PORT" "$SFTP_USER" "$SFTP_PASS" <<'PY' >/dev/null 2>&1
import paramiko, sys
t = paramiko.Transport(("127.0.0.1", int(sys.argv[1])))
t.banner_timeout = 5
t.connect(username=sys.argv[2], password=sys.argv[3])
t.close()
PY
        then ok=1; break; fi
        sleep 1
    done
    if [[ "$ok" != "1" ]]; then
        docker logs --tail 40 "$SFTP_NAME" >&2 || true
        fail "SFTP no arrancó en ${WAIT_SECS}s"
    fi
    log "SFTP listo: 127.0.0.1:$SFTP_PORT user=$SFTP_USER dir=$SFTP_DIR"
}

# ── 2. API real del worktree ────────────────────────────────
start_api() {
    local tag=$1; shift
    stop_api
    API_PORT=${NP_DRILL_API_PORT:-$(pick_port)}
    port_free "$API_PORT" || fail "puerto API $API_PORT ocupado"
    API_LOG="$TMP/api-$tag.log"
    (
        cd "$ROOT"
        # `exec`: el PID que guardamos debe ser uvicorn, no un bash padre que
        # al morir dejaría huérfana la API.
        exec env PYTHONPATH="$ROOT" NETPULSE_ENV=development \
            NETPULSE_JWT_SECRET="$JWT_SECRET" \
            NETPULSE_SYSLOG_ENABLED=false NETPULSE_METRICS_ENABLED=false \
            NETPULSE_AI_ENABLED=false NETPULSE_FIREWALL_ENABLED=false \
            NETPULSE_OFFSITE_ENABLED=true NETPULSE_OFFSITE_BACKEND="${BACKEND:-s3}" \
            NETPULSE_OFFSITE_CONNECT_TIMEOUT=5 NETPULSE_OFFSITE_READ_TIMEOUT=15 \
            NETPULSE_OFFSITE_SFTP_IO_TIMEOUT=15 \
            NETPULSE_OFFSITE_RESTORE_DIR="$TMP/results/restore-$tag" \
            "$@" "$PY" -m uvicorn app.main:app --host 127.0.0.1 \
            --port "$API_PORT" --log-level warning >"$API_LOG" 2>&1
    ) &
    API_PID=$!
    local code=000
    for _ in $(seq 1 "$WAIT_SECS"); do
        kill -0 "$API_PID" >/dev/null 2>&1 || break
        code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 \
            "http://127.0.0.1:$API_PORT/api/health" 2>/dev/null || true)
        [[ "$code" == "200" ]] && break
        sleep 0.5
    done
    if [[ "$code" != "200" ]]; then
        tail -40 "$API_LOG" >&2 || true
        fail "la API no arrancó en $API_PORT"
    fi
    TOKEN=$(cd "$ROOT" && env NETPULSE_JWT_SECRET="$JWT_SECRET" NETPULSE_ENV=development \
        "$PY" -c 'from app.core.security import create_access_token; print(create_access_token({"sub":"offsite-drill","role":"admin"}))')
    log "API lista: http://127.0.0.1:$API_PORT ($tag)"
}

api_call() {
    local name=$1 method=$2 path=$3
    shift 3
    LAST_CODE=$(curl -sS --max-time 90 -o "$TMP/results/$name.json" -w '%{http_code}' \
        -X "$method" -H "Authorization: Bearer $TOKEN" "$@" \
        "http://127.0.0.1:$API_PORT$path" 2>/dev/null || printf '000')
    printf '%s' "$LAST_CODE" > "$TMP/results/.last_code"
    printf '%s' "$LAST_CODE"
}

# ── 3. Backup local real (API de self-backup) + fixtures ────
prepare_local_backups() {
    mkdir -p "$ROOT/backups"
    local stamp
    stamp=$(date +%Y%m%d_%H%M%S)
    local cfg="$ROOT/backups/drill-core-01_${stamp}.cfg"
    local json="$ROOT/backups/drill-core-01_${stamp}.json"
    cat > "$cfg" <<'CFG'
hostname drill-core-01
!
interface GigabitEthernet0/1
 description uplink-offsite-drill
 ip address 192.0.2.10 255.255.255.0
!
end
CFG
    cat > "$json" <<'JSON'
{"device_id":"drill-core-01","hostname":"drill-core-01","vendor":"cisco",
 "facts":{"model":"C9300-48P","os_version":"17.9.4"},"source":"offsite-drill"}
JSON
    DRILL_FILES+=("$cfg" "$json")

    # Backup real del propio NetPulse, generado por la API (tar.gz).
    local code
    code=$(api_call selfbackup POST /api/ops/backup)
    if [[ "$code" == "201" ]]; then
        local self_path
        self_path=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["path"])' \
            "$TMP/results/selfbackup.json")
        if [[ -f "$self_path" ]]; then
            DRILL_FILES+=("$self_path")
            pass "POST /api/ops/backup → tar.gz local ($(basename "$self_path"))"
        else
            bad "POST /api/ops/backup devolvió una ruta inexistente"
        fi
    else
        bad "POST /api/ops/backup → HTTP $code"
    fi

    : > "$TMP/local.sha256"
    for path in "${DRILL_FILES[@]}"; do
        printf '%s  %s\n' "$(sha256_of "$path")" "$(basename "$path")" >> "$TMP/local.sha256"
    done
    log "backups locales del drill: ${#DRILL_FILES[@]}"
}

local_intact() {
    local hash name
    while read -r hash name; do
        [[ -f "$ROOT/backups/$name" ]] || { bad "backup local perdido: $name"; return 1; }
        [[ "$(sha256_of "$ROOT/backups/$name")" == "$hash" ]] || {
            bad "backup local modificado: $name"; return 1;
        }
    done < "$TMP/local.sha256"
    pass "backups locales intactos (SHA-256)"
}

# ── 4. Comprobación independiente del destino ───────────────
remote_check_s3() {
    "$PY" - "$S3_PORT" "$S3_ACCESS" "$S3_SECRET" "$S3_BUCKET" "$S3_PREFIX" \
        "$TMP/local.sha256" <<'PY'
import boto3, hashlib, sys
from botocore.config import Config
port, access, secret, bucket, prefix, listing = sys.argv[1:7]
c = boto3.client("s3", endpoint_url=f"http://127.0.0.1:{port}",
                 region_name="us-east-1", aws_access_key_id=access,
                 aws_secret_access_key=secret,
                 config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 1}))
ok = True
for line in open(listing, encoding="utf-8"):
    digest, name = line.split()
    key = f"{prefix}/{name}" if prefix else name
    head = c.head_object(Bucket=bucket, Key=key)
    body = c.get_object(Bucket=bucket, Key=key)["Body"].read()
    remote = hashlib.sha256(body).hexdigest()
    good = remote == digest and head["ContentLength"] == len(body)
    print(f"  S3 {'OK ' if good else 'BAD'} {key} size={head['ContentLength']} sha256={remote[:16]}…")
    ok = ok and good
manifest = c.get_object(Bucket=bucket, Key=f"{prefix}/_netpulse_manifest.json")["Body"].read()
print(f"  S3 manifiesto {len(manifest)} bytes")
raise SystemExit(0 if ok else 1)
PY
}

remote_check_sftp() {
    "$PY" - "$SFTP_PORT" "$SFTP_USER" "$SFTP_PASS" "$SFTP_DIR" "$TMP/local.sha256" <<'PY'
import hashlib, paramiko, sys
port, user, password, folder, listing = sys.argv[1:6]
t = paramiko.Transport(("127.0.0.1", int(port)))
t.banner_timeout = 5
t.connect(username=user, password=password)
s = paramiko.SFTPClient.from_transport(t)
ok = True
try:
    for line in open(listing, encoding="utf-8"):
        digest, name = line.split()
        path = f"{folder}/{name}"
        data = s.open(path, "rb").read()
        remote = hashlib.sha256(data).hexdigest()
        good = remote == digest
        print(f"  SFTP {'OK ' if good else 'BAD'} {path} size={len(data)} sha256={remote[:16]}…")
        ok = ok and good
    s.stat(f"{folder}/_netpulse_manifest.json")
    print("  SFTP manifiesto presente")
finally:
    s.close(); t.close()
raise SystemExit(0 if ok else 1)
PY
}

check_restore() {
    local name=$1
    local restored_dir
    restored_dir=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["dest"])' \
        "$TMP/results/$name.json")
    local hash file target
    local ok=1
    while read -r hash file; do
        target="$restored_dir/$file"
        if [[ ! -f "$target" ]]; then bad "restore sin $file"; ok=0; continue; fi
        if [[ "$(sha256_of "$target")" != "$hash" ]]; then
            bad "restore con hash distinto: $file"; ok=0; continue
        fi
        printf '  restore OK %s sha256=%s…\n' "$file" "${hash:0:16}"
    done < "$TMP/local.sha256"
    [[ "$ok" == "1" ]] && pass "contenido restaurado == original (SHA-256, ${#DRILL_FILES[@]} archivos)"
}

# ── 5. Fases de éxito ───────────────────────────────────────
run_success() {
    local backend=$1
    BACKEND=$backend
    log "===== fase $backend: export + verify + restore ====="
    if [[ "$backend" == "s3" ]]; then
        start_api "ok-s3" \
            NETPULSE_OFFSITE_S3_ENDPOINT="http://127.0.0.1:$S3_PORT" \
            NETPULSE_OFFSITE_S3_BUCKET="$S3_BUCKET" \
            NETPULSE_OFFSITE_S3_ACCESS_KEY="$S3_ACCESS" \
            NETPULSE_OFFSITE_S3_SECRET_KEY="$S3_SECRET" \
            NETPULSE_OFFSITE_S3_PREFIX="$S3_PREFIX" \
            NETPULSE_OFFSITE_S3_REGION=us-east-1
    else
        start_api "ok-sftp" \
            NETPULSE_OFFSITE_SFTP_HOST=127.0.0.1 \
            NETPULSE_OFFSITE_SFTP_PORT="$SFTP_PORT" \
            NETPULSE_OFFSITE_SFTP_USER="$SFTP_USER" \
            NETPULSE_OFFSITE_SFTP_PASSWORD="$SFTP_PASS" \
            NETPULSE_OFFSITE_SFTP_DIR="$SFTP_DIR"
    fi

    local code
    code=$(api_call status0 GET /api/config/offsite/status)
    expect "status inicial: configurado y sin export previo" "$TMP/results/status0.json" \
        "d.get('configured') is True and d.get('backend')=='$backend' and d.get('last_export') is None"

    code=$(api_call probe0 GET '/api/config/offsite/status?probe=true')
    expect "status?probe: destino alcanzable y vacío" "$TMP/results/probe0.json" \
        "d['remote']['reachable'] is True and d['remote']['files']==0 and d['remote']['error'] is None"

    code=$(api_call export POST /api/config/offsite/export)
    expect "export → todos subidos y verificados" "$TMP/results/export.json" \
        "d.get('status')=='ok' and d.get('uploaded')>=${#DRILL_FILES[@]} and d.get('failed')==0 and d.get('local_intact') is True and d.get('manifest')=='uploaded'"
    expect "export informa verificación por hash" "$TMP/results/export.json" \
        "all(r.get('verified') in ('md5','sha256') for r in d.get('results',[]))"
    local_intact

    if [[ "$backend" == "s3" ]]; then
        if remote_check_s3; then
            pass "S3: objetos existen y SHA-256 remoto == local (consulta directa)"
        else
            bad "S3: verificación directa del objeto falló"
        fi
    else
        if remote_check_sftp; then
            pass "SFTP: objetos existen y SHA-256 remoto == local (consulta directa)"
        else
            bad "SFTP: verificación directa del objeto falló"
        fi
    fi

    code=$(api_call probe1 GET '/api/config/offsite/status?probe=true')
    expect "status?probe: cuenta objetos, manifiesto y último export" "$TMP/results/probe1.json" \
        "d['remote']['reachable'] is True and d['remote']['files']>=${#DRILL_FILES[@]} and d['remote']['manifest'] is True and d['last_export']['status']=='ok'"

    code=$(api_call verify POST /api/config/offsite/verify)
    expect "verify → sin faltantes ni discrepancias" "$TMP/results/verify.json" \
        "d.get('status')=='ok' and d.get('missing')==[] and d.get('mismatched')==[] and d.get('manifest')=='present'"

    code=$(api_call restore POST /api/config/offsite/restore)
    expect "restore → HTTP 200 y archivos restaurados" "$TMP/results/restore.json" \
        "d.get('status')=='ok' and d.get('restored')>=${#DRILL_FILES[@]} and d.get('failed')==0"
    check_restore restore
    local_intact
    stop_api
}

# ── 6. Casos de fallo ──────────────────────────────────────
run_s3_failures() {
    log "===== fallos S3: credenciales / bucket / endpoint ====="
    local code

    BACKEND=s3
    start_api "bad-creds" \
        NETPULSE_OFFSITE_S3_ENDPOINT="http://127.0.0.1:$S3_PORT" \
        NETPULSE_OFFSITE_S3_BUCKET="$S3_BUCKET" \
        NETPULSE_OFFSITE_S3_ACCESS_KEY="$S3_ACCESS" \
        NETPULSE_OFFSITE_S3_SECRET_KEY="secreto-incorrecto" \
        NETPULSE_OFFSITE_S3_PREFIX="$S3_PREFIX" NETPULSE_OFFSITE_S3_REGION=us-east-1
    code=$(api_call badcreds_probe GET '/api/config/offsite/status?probe=true')
    expect "credenciales malas: probe informa auth_failed sin 500" "$TMP/results/badcreds_probe.json" \
        "d['remote']['reachable'] is False and d['remote']['error']['code']=='auth_failed'"
    code=$(api_call badcreds_export POST /api/config/offsite/export)
    fail_case "credenciales malas: export" "$TMP/results/badcreds_export.json" "$code" 502
    expect "credenciales malas: local_intact=true" "$TMP/results/badcreds_export.json" \
        "d.get('local_intact') is True and d.get('uploaded')==0"
    local_intact
    stop_api

    local missing_bucket="netpulse-no-existe-$$"
    start_api "missing-bucket" \
        NETPULSE_OFFSITE_S3_ENDPOINT="http://127.0.0.1:$S3_PORT" \
        NETPULSE_OFFSITE_S3_BUCKET="$missing_bucket" \
        NETPULSE_OFFSITE_S3_ACCESS_KEY="$S3_ACCESS" \
        NETPULSE_OFFSITE_S3_SECRET_KEY="$S3_SECRET" \
        NETPULSE_OFFSITE_S3_PREFIX="$S3_PREFIX" NETPULSE_OFFSITE_S3_REGION=us-east-1
    code=$(api_call missing_export POST /api/config/offsite/export)
    fail_case "bucket inexistente: export" "$TMP/results/missing_export.json" "$code" 502
    expect "bucket inexistente: causa bucket_missing y local intacto" "$TMP/results/missing_export.json" \
        "d.get('error',{}).get('code')=='bucket_missing' and d.get('local_intact') is True"
    local_intact
    stop_api

    local dead_port
    dead_port=$(pick_port)
    start_api "endpoint-down" \
        NETPULSE_OFFSITE_S3_ENDPOINT="http://127.0.0.1:$dead_port" \
        NETPULSE_OFFSITE_S3_BUCKET="$S3_BUCKET" \
        NETPULSE_OFFSITE_S3_ACCESS_KEY="$S3_ACCESS" \
        NETPULSE_OFFSITE_S3_SECRET_KEY="$S3_SECRET" \
        NETPULSE_OFFSITE_S3_PREFIX="$S3_PREFIX" NETPULSE_OFFSITE_S3_REGION=us-east-1
    local started ended
    started=$(date +%s)
    code=$(api_call down_export POST /api/config/offsite/export)
    ended=$(date +%s)
    fail_case "endpoint caído: export" "$TMP/results/down_export.json" "$code" 502
    expect "endpoint caído: causa endpoint_unreachable/timeout" "$TMP/results/down_export.json" \
        "d.get('error',{}).get('code') in ('endpoint_unreachable','timeout','dns_error')"
    if (( ended - started <= 20 )); then
        pass "endpoint caído: falla rápido ($((ended - started))s, sin cuelgue)"
    else
        bad "endpoint caído: tardó $((ended - started))s (timeout no efectivo)"
    fi
    local_intact
    stop_api
}

run_sftp_failures() {
    log "===== fallos SFTP: credenciales / endpoint ====="
    local code

    BACKEND=sftp
    start_api "sftp-bad-pass" \
        NETPULSE_OFFSITE_SFTP_HOST=127.0.0.1 \
        NETPULSE_OFFSITE_SFTP_PORT="$SFTP_PORT" \
        NETPULSE_OFFSITE_SFTP_USER="$SFTP_USER" \
        NETPULSE_OFFSITE_SFTP_PASSWORD="password-incorrecto" \
        NETPULSE_OFFSITE_SFTP_DIR="$SFTP_DIR"
    code=$(api_call sftp_bad_export POST /api/config/offsite/export)
    fail_case "SFTP contraseña mala: export" "$TMP/results/sftp_bad_export.json" "$code" 502
    expect "SFTP contraseña mala: auth_failed y local intacto" "$TMP/results/sftp_bad_export.json" \
        "d.get('error',{}).get('code')=='auth_failed' and d.get('local_intact') is True"
    local_intact
    stop_api

    local dead_port
    dead_port=$(pick_port)
    start_api "sftp-down" \
        NETPULSE_OFFSITE_SFTP_HOST=127.0.0.1 \
        NETPULSE_OFFSITE_SFTP_PORT="$dead_port" \
        NETPULSE_OFFSITE_SFTP_USER="$SFTP_USER" \
        NETPULSE_OFFSITE_SFTP_PASSWORD="$SFTP_PASS" \
        NETPULSE_OFFSITE_SFTP_DIR="$SFTP_DIR"
    code=$(api_call sftp_down_export POST /api/config/offsite/export)
    fail_case "SFTP endpoint caído: export" "$TMP/results/sftp_down_export.json" "$code" 502
    expect "SFTP endpoint caído: causa endpoint_unreachable/timeout" "$TMP/results/sftp_down_export.json" \
        "d.get('error',{}).get('code') in ('endpoint_unreachable','timeout')"
    local_intact
    stop_api
}

# ── 7. Ejecución ────────────────────────────────────────────
if [[ "$RUN_S3" == "1" ]]; then start_s3; fi
if [[ "$RUN_SFTP" == "1" ]]; then start_sftp; fi

# La API necesita los fixtures locales; se crean con la primera API arriba.
BACKEND=s3
if [[ "$RUN_S3" == "1" ]]; then
    start_api "prepare"
else
    BACKEND=sftp
    start_api "prepare" \
        NETPULSE_OFFSITE_SFTP_HOST=127.0.0.1 \
        NETPULSE_OFFSITE_SFTP_PORT="$SFTP_PORT" \
        NETPULSE_OFFSITE_SFTP_USER="$SFTP_USER" \
        NETPULSE_OFFSITE_SFTP_PASSWORD="$SFTP_PASS" \
        NETPULSE_OFFSITE_SFTP_DIR="$SFTP_DIR"
fi
prepare_local_backups
stop_api

if [[ "$RUN_S3" == "1" ]]; then
    run_success s3
    run_s3_failures
fi
if [[ "$RUN_SFTP" == "1" ]]; then
    run_success sftp
    run_sftp_failures
fi

printf '\n===== RESUMEN OFFSITE DRILL =====\n'
printf 'PASS=%d FAIL=%d\n' "$PASS" "$FAIL"
if (( FAIL > 0 )); then
    exit 1
fi
log "drill completo OK (S3: $S3_FLAVOR :$S3_PORT, SFTP :$SFTP_PORT)"
