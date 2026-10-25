#!/usr/bin/env bash
# ============================================================
# deploy_drill.sh — ensayo local del ciclo de vida de producción
# ============================================================
# Demuestra, con Docker y SIN secretos reales, el procedimiento que se
# aplicará en el datacenter:
#
#   build v1 -> arranque -> datos reales -> upgrade v2 -> rollback v1
#   -> backup -> pérdida simulada de estado -> restore -> limpieza
#
# Por qué `docker run` y no `docker compose`:
#   deploy/docker-compose.prod.yml lee `../.env` (el .env real del repo) y
#   publica 80/443 con Caddy. Para no tocar el repo ni exigir dominio/TLS,
#   el drill replica EXACTAMENTE los flags del servicio `api` (read-only,
#   tmpfs /tmp, binds de estado, env de producción, healthcheck de imagen)
#   con `docker run` sobre un directorio temporal. El compose real se valida
#   aparte con `docker compose ... config`.
#
# Qué NO prueba (se indica como SKIP en la salida):
#   - TLS/Let's Encrypt y WebSocket a través de Caddy (requiere dominio real;
#     en local se probaría con la CA interna, no es el camino de producción).
#   - Terminal SSH/SFTP contra equipos reales y RBAC multiusuario.
#
# Uso:
#   scripts/deploy_drill.sh            # limpia todo al terminar
#   scripts/deploy_drill.sh --keep     # conserva el directorio temporal
#
# Requisitos: Docker Engine + Compose v2, curl, python3, tar.
# No toca contenedores del laboratorio (netpulse-cisco, netpulse-switch...):
# solo opera sobre el contenedor `netpulse-drill-api` y las imágenes
# `netpulse-api:drill-v1` / `netpulse-api:drill-v2`.
# ============================================================

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
CONTAINER=netpulse-drill-api
IMAGE_V1=netpulse-api:drill-v1
IMAGE_V2=netpulse-api:drill-v2
KEEP=${KEEP:-0}
TMP=""
STATE=""
ENV_FILE=""
PORT=""
TOKEN=""
AUDIT_BASE=0
REMOTE_BASE=0
AUDIT_SHA=""
RUNTIME_SHA=""
FERNET_SHA=""
BUILD_S=""
TAG_S=""
START_V1_S=""
HEALTH_V1_S=""
UPGRADE_DOWNTIME_S=""
UPGRADE_HEALTH_S=""
ROLLBACK_DOWNTIME_S=""
ROLLBACK_HEALTH_S=""
BACKUP_S=""
RESTORE_DOWNTIME_S=""
RESTORE_HEALTH_S=""

for arg in "$@"; do
    case "$arg" in
        --keep) KEEP=1 ;;
        -h|--help)
            sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) printf 'opción desconocida: %s\n' "$arg" >&2; exit 2 ;;
    esac
done

log()  { printf '[drill] %s\n' "$*"; }
warn() { printf '[drill] AVISO: %s\n' "$*" >&2; }
fail() { printf '[drill] ERROR: %s\n' "$*" >&2; exit 1; }

now_ms() { python3 -c 'import time; print(int(time.time() * 1000))'; }
secs()   { awk -v ms="$1" 'BEGIN { printf "%.2f", ms / 1000 }'; }

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

cleanup() {
    rc=$?
    trap - EXIT INT TERM
    set +e
    if [[ $rc -ne 0 && -n "$CONTAINER" ]] && docker container inspect "$CONTAINER" >/dev/null 2>&1; then
        printf '[drill] ---- últimos logs de %s ----\n' "$CONTAINER" >&2
        docker logs --tail 80 "$CONTAINER" >&2
    fi
    if [[ -n "$CONTAINER" ]] && docker container inspect "$CONTAINER" >/dev/null 2>&1; then
        docker rm -f "$CONTAINER" >/dev/null 2>&1
    fi
    # Solo los tags del drill; nunca imágenes del lab.
    docker image rm -f "$IMAGE_V1" "$IMAGE_V2" >/dev/null 2>&1
    if [[ -n "$TMP" && -d "$TMP" ]]; then
        if [[ "$KEEP" == "1" ]]; then
            printf '[drill] KEEP=1: estado y logs en %s\n' "$TMP" >&2
        else
            rm -rf "$TMP"
        fi
    fi
    exit "$rc"
}
trap cleanup EXIT INT TERM

# ── 0. Prerrequisitos ───────────────────────────────────────
for cmd in docker curl python3 tar awk; do
    command -v "$cmd" >/dev/null 2>&1 || fail "falta $cmd en el PATH"
done
docker info >/dev/null 2>&1 || fail "Docker no responde (¿Docker Desktop/Colima arrancado?)"
docker compose version >/dev/null 2>&1 || fail "falta Docker Compose v2 (docker compose)"

if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
    label=$(docker inspect -f '{{index .Config.Labels "netpulse.drill"}}' "$CONTAINER" 2>/dev/null || true)
    [[ "$label" == "1" ]] || fail "existe un contenedor llamado $CONTAINER que no es del drill; revíselo a mano"
    log "eliminando contenedor drill de una ejecución anterior"
    docker rm -f "$CONTAINER" >/dev/null
fi
docker image rm -f "$IMAGE_V1" "$IMAGE_V2" >/dev/null 2>&1 || true

# En macOS, Colima/Docker Desktop solo comparte $HOME (virtiofs). Un
# directorio en /var/folders se vería como un directorio vacío root:root
# dentro del contenedor y el uid 10001 no podría escribir. Se usa $HOME,
# fuera del repo, y el trap lo borra igualmente.
if [[ "$(uname -s)" == "Darwin" ]]; then
    TMP=$(mktemp -d "${HOME}/.netpulse-drill.XXXXXX")
else
    TMP=$(mktemp -d "${TMPDIR:-/tmp}/netpulse-drill.XXXXXX")
fi
STATE="$TMP/state"
ENV_FILE="$TMP/drill.env"
mkdir -p "$STATE"/{config,runtime,audit,backups,reports}
# El drill corre como el usuario que lo lanza. En Linux sin root, el uid de
# la imagen (10001) no podría escribir en bind mounts propiedad del usuario;
# se usan permisos amplios SOLO en el directorio temporal. Producción usa
# chown 10001:10001 + 0750 (scripts/prepare_host.sh).
chmod 0755 "$TMP"
chmod 0777 "$STATE" "$STATE"/{config,runtime,audit,backups,reports}

RUN_USER_ARGS=()
if [[ "$(uname -s)" == "Linux" && "${EUID:-$(id -u)}" -ne 0 ]]; then
    RUN_USER_ARGS=(--user "$(id -u):$(id -g)")
    warn "Linux sin root: el drill ejecuta la API con su uid, no con 10001 (solo afecta al ensayo)"
fi

JWT_SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
ADMIN_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
ALERT_TOKEN=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
LAB_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')

cat > "$ENV_FILE" <<EOF
# .env temporal del drill — generado, sin secretos reales.
NETPULSE_JWT_SECRET=$JWT_SECRET
NETPULSE_DOMAIN=localhost
NETPULSE_ALERTMANAGER_TOKEN=$ALERT_TOKEN
NETPULSE_LAB_PASSWORD=$LAB_PASSWORD
NETPULSE_SNMP_COMMUNITY=drill-only
EOF
chmod 0600 "$ENV_FILE"

PORT=$(python3 - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
)
BASE="http://127.0.0.1:$PORT"

log "directorio temporal: $TMP"
log "API del drill en $BASE (solo 127.0.0.1; el compose real no publica la API)"
log "JWT de prueba: ${#JWT_SECRET} caracteres hex (no se imprime)"

# ── 1. Imágenes v1 y v2 (mismo Dockerfile) ─────────────────
log "construyendo $IMAGE_V1 desde Dockerfile (contexto $ROOT)"
t0=$(now_ms)
if ! docker build --tag "$IMAGE_V1" --file "$ROOT/Dockerfile" "$ROOT" >"$TMP/build.log" 2>&1; then
    tail -60 "$TMP/build.log" >&2
    fail "docker build falló (log: $TMP/build.log)"
fi
BUILD_S=$(secs $(( $(now_ms) - t0 )))
log "build v1 OK en ${BUILD_S}s"

t0=$(now_ms)
docker tag "$IMAGE_V1" "$IMAGE_V2"
TAG_S=$(secs $(( $(now_ms) - t0 )))
log "tag v2 OK en ${TAG_S}s (mismo contenido; simula la promoción de tag, no un cambio de código)"
log "digest v1=$(docker image inspect -f '{{.Id}}' "$IMAGE_V1" | cut -c1-19) v2=$(docker image inspect -f '{{.Id}}' "$IMAGE_V2" | cut -c1-19)"

# Guard de producción: sin NETPULSE_JWT_SECRET la app debe negarse a arrancar.
if docker run --rm -e NETPULSE_ENV=production --entrypoint python "$IMAGE_V1" \
        -c 'import app.core.security' >/dev/null 2>&1; then
    fail "la imagen arrancó en producción SIN NETPULSE_JWT_SECRET (guard de seguridad roto)"
fi
log "OK: NETPULSE_ENV=production sin JWT falla al importar (guard de security.py)"

# ── 2. Usuario de prueba en el estado temporal ─────────────
log "sembrando usuario drill-admin (bcrypt) en $STATE/config/users.yaml"
docker run --rm -i -e DRILL_ADMIN_PASSWORD="$ADMIN_PASSWORD" \
    -v "$STATE/config:/cfg" \
    ${RUN_USER_ARGS[@]+"${RUN_USER_ARGS[@]}"} \
    --entrypoint python "$IMAGE_V1" - <<'PY'
import os
import yaml
from app.core.security import hash_password

data = {
    "users": [{
        "username": "drill-admin",
        "name": "Drill Admin",
        "role": "admin",
        "password_hash": hash_password(os.environ["DRILL_ADMIN_PASSWORD"]),
        "plain_password": None,
    }]
}
with open("/cfg/users.yaml", "w", encoding="utf-8") as handle:
    yaml.safe_dump(data, handle, sort_keys=False)
PY

# ── 3. Arranque v1 ─────────────────────────────────────────
start_container() {
    local image=$1
    # docker stop deja el contenedor parado con el mismo nombre: se elimina
    # antes de recrearlo (el estado vive en los binds, no en el contenedor).
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    docker run -d --name "$CONTAINER" \
        --label netpulse.drill=1 \
        --read-only \
        --tmpfs /tmp:size=64m,mode=1777 \
        --init \
        -p "127.0.0.1:$PORT:8082" \
        --env-file "$ENV_FILE" \
        -e NETPULSE_ENV=production \
        -e NETPULSE_HOST=0.0.0.0 \
        -e NETPULSE_PORT=8082 \
        -e HOME=/tmp \
        -v "$STATE/config:/app/config" \
        -v "$STATE/runtime:/app/runtime" \
        -v "$STATE/audit:/app/audit" \
        -v "$STATE/backups:/app/backups" \
        -v "$STATE/reports:/app/reports" \
        ${RUN_USER_ARGS[@]+"${RUN_USER_ARGS[@]}"} \
        "$image" >/dev/null
}

wait_ready() {
    local deadline=$(( $(date +%s) + 60 ))
    while [[ $(date +%s) -lt $deadline ]]; do
        code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "$BASE/" 2>/dev/null || true)
        [[ "$code" == "200" ]] && return 0
        if [[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || echo false)" != "true" ]]; then
            return 1
        fi
        sleep 1
    done
    return 1
}

wait_health() {
    local deadline=$(( $(date +%s) + 120 ))
    local status
    while [[ $(date +%s) -lt $deadline ]]; do
        status=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
            "$CONTAINER" 2>/dev/null || echo missing)
        case "$status" in
            healthy) return 0 ;;
            unhealthy) return 1 ;;
        esac
        sleep 2
    done
    return 1
}

check_health() {
    local body code attempt
    for attempt in 1 2 3; do
        code=$(curl -s -o "$TMP/health.json" -w '%{http_code}' --max-time 5 "$BASE/api/health" || true)
        if [[ "$code" == "200" ]]; then
            body=$(cat "$TMP/health.json")
            log "  /api/health 200: $body"
            return 0
        fi
        # 429 = rate limit (10/min): la API está viva; se reintenta.
        sleep 6
    done
    return 1
}

login() {
    local attempt code
    for attempt in 1 2 3; do
        code=$(curl -sS -o "$TMP/login.json" -w '%{http_code}' -X POST "$BASE/api/auth/login" \
            -H 'Content-Type: application/json' \
            -d "{\"username\":\"drill-admin\",\"password\":\"$ADMIN_PASSWORD\"}" || true)
        if [[ "$code" == "200" ]]; then
            TOKEN=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["access_token"])' "$TMP/login.json")
            [[ -n "$TOKEN" ]] && return 0
        fi
        [[ "$code" == "429" ]] && { sleep 12; continue; }
        printf '[drill] login HTTP %s: %s\n' "$code" "$(cat "$TMP/login.json" 2>/dev/null)" >&2
        return 1
    done
    return 1
}

audit_total() {
    curl -fsS "$BASE/api/audit/stats" -H "Authorization: Bearer $TOKEN" \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)["total_events"])'
}

remote_total() {
    curl -fsS "$BASE/api/remote/sites" -H "Authorization: Bearer $TOKEN" \
        | python3 -c 'import json,sys; d=json.load(sys.stdin); print(sum(s["devices"]["total"] for s in d["sites"]))'
}

remote_history_count() {
    curl -fsS "$BASE/api/remote/sites/site-drill/devices/sw-drill-01/history?hours=24" \
        -H "Authorization: Bearer $TOKEN" \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)["count"])'
}

log "arrancando v1"
t0=$(now_ms)
start_container "$IMAGE_V1"
wait_ready || fail "v1 no respondió en 60s"
START_V1_S=$(secs $(( $(now_ms) - t0 )))
wait_health || fail "v1 no llegó a healthy"
HEALTH_V1_S=$(secs $(( $(now_ms) - t0 )))
log "v1 lista (HTTP) en ${START_V1_S}s; healthcheck healthy en ${HEALTH_V1_S}s"

check_health || fail "v1 /api/health no responde"
login || fail "login inicial falló"
log "login inicial OK (JWT de drill-admin)"

# ── 4. Datos reales en el estado ───────────────────────────
FIRST_AUDIT=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
    FIRST_AUDIT=$(audit_total)
    [[ "$FIRST_AUDIT" -ge 1 ]] && break
    sleep 0.5
done
[[ "$FIRST_AUDIT" -ge 1 ]] || fail "no se escribió ningún evento de auditoría"
log "auditoría: $FIRST_AUDIT evento(s) en audit/audit.db"

KEY_RESPONSE=$(curl -fsS -X POST "$BASE/api/security/api-keys" \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d '{"name":"drill-collector","kind":"collector","site_id":"site-drill","collector_id":"collector-drill-01"}')
COLLECTOR_KEY=$(printf '%s' "$KEY_RESPONSE" | python3 -c 'import json,sys; print(json.load(sys.stdin)["key"])')
[[ "$COLLECTOR_KEY" == np_* ]] || fail "no se creó la API key de colector"
log "API key de colector creada (kind=collector, site=site-drill)"

TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
INGEST=$(curl -fsS -X POST "$BASE/api/remote/ingest" \
    -H "Authorization: Bearer $COLLECTOR_KEY" -H 'Content-Type: application/json' \
    -d "{\"schema_version\":1,\"sent_at\":\"$TS\",\"buffer\":{\"buffer_epoch\":\"drill-epoch\",\"pending\":1},\"samples\":[{\"device_id\":\"sw-drill-01\",\"ts\":\"$TS\",\"metric\":\"reachable\",\"value_bool\":true,\"ok\":true,\"probe_kind\":\"tcp\"},{\"device_id\":\"sw-drill-01\",\"ts\":\"$TS\",\"metric\":\"latency_ms\",\"value_num\":1.23,\"ok\":true,\"probe_kind\":\"tcp\"}]}")
printf '%s\n' "$INGEST" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("accepted")==2, d; print("[drill] ingest OK: accepted=2")'

AUDIT_BASE=$(audit_total)
REMOTE_BASE=$(remote_total)
HISTORY_BASE=$(remote_history_count)
[[ "$REMOTE_BASE" -ge 1 ]] || fail "runtime/remote_collector.db no registró el site"
[[ "$HISTORY_BASE" -ge 1 ]] || fail "el histórico remoto no devolvió puntos"
log "estado inicial: audit=$AUDIT_BASE remote_devices=$REMOTE_BASE history_points=$HISTORY_BASE"

for path in audit/audit.db runtime/remote_collector.db config/api_keys.json config/users.yaml; do
    [[ -s "$STATE/$path" ]] || fail "no se escribió $path en el host"
done
log "OK: audit.db, remote_collector.db y config/ se escribieron en el host"

# ── 5. Upgrade v1 -> v2 ────────────────────────────────────
log "upgrade: parando v1 y arrancando v2 (mismo código, tag promovido)"
t0=$(now_ms)
docker stop -t 30 "$CONTAINER" >/dev/null
start_container "$IMAGE_V2"
wait_ready || fail "v2 no respondió"
UPGRADE_DOWNTIME_S=$(secs $(( $(now_ms) - t0 )))
wait_health || fail "v2 no llegó a healthy"
UPGRADE_HEALTH_S=$(secs $(( $(now_ms) - t0 )))

check_health || fail "v2 /api/health no responde"
login || fail "login tras upgrade falló (¿se perdió config/users.yaml?)"
AUDIT_UP=$(audit_total)
REMOTE_UP=$(remote_total)
HISTORY_UP=$(remote_history_count)
[[ "$AUDIT_UP" -ge "$AUDIT_BASE" ]] || fail "se perdió auditoría en el upgrade"
[[ "$REMOTE_UP" -eq "$REMOTE_BASE" ]] || fail "cambió el estado remoto en el upgrade"
[[ "$HISTORY_UP" -eq "$HISTORY_BASE" ]] || fail "se perdió el histórico en el upgrade"
log "upgrade OK: downtime=${UPGRADE_DOWNTIME_S}s (health a los ${UPGRADE_HEALTH_S}s), audit=$AUDIT_UP remote=$REMOTE_UP history=$HISTORY_UP"

# ── 6. Rollback v2 -> v1 ───────────────────────────────────
log "rollback: parando v2 y volviendo a v1"
t0=$(now_ms)
docker stop -t 30 "$CONTAINER" >/dev/null
start_container "$IMAGE_V1"
wait_ready || fail "v1 (rollback) no respondió"
ROLLBACK_DOWNTIME_S=$(secs $(( $(now_ms) - t0 )))
wait_health || fail "v1 (rollback) no llegó a healthy"
ROLLBACK_HEALTH_S=$(secs $(( $(now_ms) - t0 )))

check_health || fail "v1 (rollback) /api/health no responde"
login || fail "login tras rollback falló"
AUDIT_RB=$(audit_total)
REMOTE_RB=$(remote_total)
HISTORY_RB=$(remote_history_count)
[[ "$AUDIT_RB" -ge "$AUDIT_UP" ]] || fail "se perdió auditoría en el rollback"
[[ "$REMOTE_RB" -eq "$REMOTE_BASE" ]] || fail "cambió el estado remoto en el rollback"
[[ "$HISTORY_RB" -eq "$HISTORY_BASE" ]] || fail "se perdió el histórico en el rollback"
log "rollback OK: downtime=${ROLLBACK_DOWNTIME_S}s (health a los ${ROLLBACK_HEALTH_S}s), audit=$AUDIT_RB remote=$REMOTE_RB history=$HISTORY_RB"

# ── 7. Backup / pérdida simulada / restore ─────────────────
log "backup: parando la API para copiar SQLite consistente"
t0=$(now_ms)
docker stop -t 30 "$CONTAINER" >/dev/null
# El tar lo hace el usuario del host; en Linux los ficheros pueden ser del
# uid 10001. Se relajan permisos SOLO en el estado temporal del drill.
chmod -R a+rwX "$STATE" 2>/dev/null || true
AUDIT_SHA=$(sha256_of "$STATE/audit/audit.db")
RUNTIME_SHA=$(sha256_of "$STATE/runtime/remote_collector.db")
FERNET_SHA=$(sha256_of "$STATE/config/.fernet_key")
BACKUP_START=$(now_ms)
tar -czf "$TMP/state-backup.tgz" -C "$STATE" config runtime audit backups
BACKUP_S=$(secs $(( $(now_ms) - BACKUP_START )))
log "backup OK en ${BACKUP_S}s ($(du -h "$TMP/state-backup.tgz" | awk '{print $1}'))"

log "simulando pérdida total del estado (mover directorios)"
mkdir -p "$TMP/lost"
for dir in config runtime audit backups; do
    mv "$STATE/$dir" "$TMP/lost/$dir"
    mkdir -p "$STATE/$dir"
done

log "restaurando desde el backup"
RESTORE_START_MS=$(now_ms)
tar -xzf "$TMP/state-backup.tgz" -C "$STATE"
RESTORE_MS=$(( $(now_ms) - RESTORE_START_MS ))
chmod -R a+rwX "$STATE" 2>/dev/null || true

[[ "$(sha256_of "$STATE/audit/audit.db")" == "$AUDIT_SHA" ]] || fail "audit.db restaurado no coincide (sha256)"
[[ "$(sha256_of "$STATE/runtime/remote_collector.db")" == "$RUNTIME_SHA" ]] || fail "remote_collector.db restaurado no coincide (sha256)"
[[ "$(sha256_of "$STATE/config/.fernet_key")" == "$FERNET_SHA" ]] || fail ".fernet_key restaurada no coincide (sha256)"
log "hashes de audit.db, remote_collector.db y .fernet_key verificados tras restore"

t0=$(now_ms)
start_container "$IMAGE_V1"
wait_ready || fail "la API no arrancó tras el restore"
RESTORE_DOWNTIME_S=$(secs $(( $(now_ms) - t0 )))
wait_health || fail "la API restaurada no llegó a healthy"
RESTORE_HEALTH_S=$(secs $(( $(now_ms) - t0 )))
check_health || fail "/api/health no responde tras restore"
login || fail "login tras restore falló"
AUDIT_REST=$(audit_total)
REMOTE_REST=$(remote_total)
HISTORY_REST=$(remote_history_count)
[[ "$AUDIT_REST" -ge "$AUDIT_BASE" ]] || fail "se perdió auditoría tras restore"
[[ "$REMOTE_REST" -eq "$REMOTE_BASE" ]] || fail "se perdió el estado remoto tras restore"
[[ "$HISTORY_REST" -eq "$HISTORY_BASE" ]] || fail "se perdió el histórico tras restore"
log "restore OK: downtime=${RESTORE_DOWNTIME_S}s (health a los ${RESTORE_HEALTH_S}s, copia ${RESTORE_MS}ms), audit=$AUDIT_REST remote=$REMOTE_REST history=$HISTORY_REST"

# ── 8. Resumen ─────────────────────────────────────────────
cat <<EOF

================= RESULTADO DEL DRILL (tiempos reales) =================
build v1 (Dockerfile)                : ${BUILD_S}s
promoción de tag v1 -> v2            : ${TAG_S}s
arranque v1 -> HTTP listo            : ${START_V1_S}s
arranque v1 -> healthcheck healthy   : ${HEALTH_V1_S}s
upgrade v1->v2 downtime (stop+start) : ${UPGRADE_DOWNTIME_S}s (healthy: ${UPGRADE_HEALTH_S}s)
rollback v2->v1 downtime             : ${ROLLBACK_DOWNTIME_S}s (healthy: ${ROLLBACK_HEALTH_S}s)
backup en frío (tar)                 : ${BACKUP_S}s
restore + arranque (downtime)        : ${RESTORE_DOWNTIME_S}s (healthy: ${RESTORE_HEALTH_S}s, extracción: ${RESTORE_MS}ms)
datos verificados                    : audit=$AUDIT_REST eventos, remote_devices=$REMOTE_REST, history_points=$HISTORY_REST
========================================================================
SKIP honestos:
  - TLS/Let's Encrypt, HTTP/3 y WebSocket vía Caddy: requieren dominio y
    certificado reales; el drill usa HTTP en 127.0.0.1:$PORT.
  - Terminal SSH/SFTP y RBAC multiusuario contra equipos reales.
  - El compose de producción se valida con "docker compose config"; el
    drill reproduce los flags del servicio "api" con "docker run".
========================================================================
EOF

log "drill OK; la limpieza (contenedor, imágenes drill y temporal) se ejecuta ahora"
exit 0
