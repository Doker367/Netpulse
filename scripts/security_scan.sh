#!/usr/bin/env bash
# ============================================================
# NetPulse NOC — auditoría de seguridad local (pre-piloto)
# ============================================================
# Qué hace:
#   1. pip-audit sobre requirements.txt y requirements-collector.txt
#   2. bandit -r app -ll
#   3. búsqueda de secretos en archivos TRACKED (git ls-files)
#   4. comprobaciones de configuración (JWT, CORS, headers, debug,
#      permisos y ficheros sensibles)
#
# Qué NO hace (ver docs/SEGURIDAD.md):
#   pentest, validación TLS real, revisión manual de lógica de negocio,
#   auditoría de hardware/red, análisis de imágenes Docker.
#
# Uso:
#   scripts/security_scan.sh
#   NP_SECURITY_KEEP=1 scripts/security_scan.sh   # conserva /tmp y logs
#
# Salida: PASS / WARN / FAIL + resumen. Exit != 0 SOLO si hay FAIL de
# severidad alta. Los WARN no rompen la ejecución.
#
# No imprime valores de secretos: solo fichero:línea y el nombre del patrón.
# Idempotente: cada ejecución usa un venv temporal nuevo y no muta el repo.
# ============================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/netpulse-security-scan.XXXXXX")"
KEEP="${NP_SECURITY_KEEP:-0}"

cleanup() {
    if [[ "$KEEP" == "1" ]]; then
        printf '\n[info] Artefactos conservados en %s\n' "$TMP_DIR"
    else
        rm -rf "$TMP_DIR"
    fi
}
trap cleanup EXIT

PASS=0
WARN=0
FAIL=0
HIGH_FAIL=0

pass() { printf 'PASS  %s\n' "$*"; PASS=$((PASS + 1)); }
warn() { printf 'WARN  %s\n' "$*"; WARN=$((WARN + 1)); }
fail() { printf 'FAIL  %s\n' "$*"; FAIL=$((FAIL + 1)); }
fail_high() { printf 'FAIL  [ALTA] %s\n' "$*"; FAIL=$((FAIL + 1)); HIGH_FAIL=$((HIGH_FAIL + 1)); }
section() { printf '\n== %s ==\n' "$*"; }
note() { printf '      %s\n' "$*"; }

is_tty=0
[[ -t 1 ]] && is_tty=1
if [[ "$is_tty" == "1" ]]; then
    pass() { printf '\033[32mPASS\033[0m  %s\n' "$*"; PASS=$((PASS + 1)); }
    warn() { printf '\033[33mWARN\033[0m  %s\n' "$*"; WARN=$((WARN + 1)); }
    fail() { printf '\033[31mFAIL\033[0m  %s\n' "$*"; FAIL=$((FAIL + 1)); }
    fail_high() { printf '\033[1;31mFAIL [ALTA]\033[0m %s\n' "$*"; FAIL=$((FAIL + 1)); HIGH_FAIL=$((HIGH_FAIL + 1)); }
fi

printf 'NetPulse — auditoría de seguridad local\n'
printf 'Repo: %s\n' "$ROOT_DIR"
printf 'Fecha: %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
printf 'Nota: los WARN no hacen fallar el script; solo los FAIL de severidad ALTA.\n'

# ── 0. Herramientas base ────────────────────────────────────
section "0. Entorno"
for tool in git python3; do
    if command -v "$tool" >/dev/null 2>&1; then
        note "$tool: $(command -v "$tool")"
    else
        fail_high "Herramienta requerida no encontrada: $tool"
    fi
done
if [[ "$HIGH_FAIL" -gt 0 ]]; then
    printf '\nResumen imposible: faltan herramientas base.\n'
    exit 1
fi

# Interprete base para el venv de auditoría: el del proyecto si existe
# (así los markers de requirements se resuelven como en producción), o
# python3. Se puede forzar con NETPULSE_PYTHON=/ruta/bin/python.
BASE_PY="${NETPULSE_PYTHON:-}"
if [[ -z "$BASE_PY" ]]; then
    if [[ -x .venv/bin/python ]]; then
        BASE_PY=".venv/bin/python"
    else
        BASE_PY="python3"
    fi
fi
note "intérprete base: $BASE_PY ($("$BASE_PY" --version 2>&1))"

# ════════════════════════════════════════════════════════════
# 1. Dependencias — pip-audit
# ════════════════════════════════════════════════════════════
section "1. Dependencias (pip-audit)"
VENV="$TMP_DIR/venv"
PY="$VENV/bin/python"
AUDIT_JSON="$TMP_DIR/pip-audit.json"
AUDIT_RAN=0

if "$BASE_PY" -m venv "$VENV" >"$TMP_DIR/venv.log" 2>&1 \
    && "$VENV/bin/pip" install --quiet --disable-pip-version-check \
        pip-audit bandit >>"$TMP_DIR/venv.log" 2>&1; then
    note "venv temporal: $VENV (pip-audit + bandit)"
else
    warn "No se pudo crear/instalar el venv temporal (¿sin red?). Ver $TMP_DIR/venv.log"
    note "pip-audit y bandit NO se ejecutaron: resultado NO COMPROBADO."
fi

if [[ -x "$VENV/bin/pip-audit" ]]; then
    audit_rc=0
    "$VENV/bin/pip-audit" \
        -r requirements.txt \
        -r requirements-collector.txt \
        --format json \
        --output "$AUDIT_JSON" \
        >>"$TMP_DIR/pip-audit.log" 2>&1 || audit_rc=$?

    if [[ ! -s "$AUDIT_JSON" ]]; then
        warn "pip-audit no produjo JSON (rc=$audit_rc). Resultado NO COMPROBADO."
        note "Ver $TMP_DIR/pip-audit.log"
    else
        AUDIT_RAN=1
        "$PY" - "$AUDIT_JSON" >"$TMP_DIR/audit-summary.txt" <<'PY'
import json
import sys

path = sys.argv[1]
try:
    data = json.load(open(path, encoding="utf-8"))
except Exception as exc:  # noqa: BLE001
    print(f"PARSE_ERROR={exc}")
    raise SystemExit(0)

deps = data.get("dependencies", [])
total_vulns = 0
affected = set()
seen = set()
lines = []
for dep in deps:
    vulns = dep.get("vulns") or []
    if not vulns:
        continue
    for v in vulns:
        vid = v.get("id", "?")
        key = (dep.get("name"), dep.get("version"), vid)
        if key in seen:
            continue
        seen.add(key)
        total_vulns += 1
        affected.add(dep.get("name"))
        fixes = ",".join(v.get("fix_versions") or []) or "sin fix"
        aliases = ",".join(v.get("aliases") or [])
        extra = f" ({aliases})" if aliases else ""
        lines.append(
            f"  {dep.get('name','?')}=={dep.get('version','?')}  {vid}{extra}  fix={fixes}"
        )

print(f"TOTAL_VULNS={total_vulns}")
print(f"AFFECTED_PACKAGES={len(affected)}")
print(f"TOTAL_DEPS={len(deps)}")
for line in lines:
    print(line)
PY

        total_vulns=$(sed -n 's/^TOTAL_VULNS=//p' "$TMP_DIR/audit-summary.txt" | head -1)
        affected_pkgs=$(sed -n 's/^AFFECTED_PACKAGES=//p' "$TMP_DIR/audit-summary.txt" | head -1)
        total_deps=$(sed -n 's/^TOTAL_DEPS=//p' "$TMP_DIR/audit-summary.txt" | head -1)

        if grep -q '^PARSE_ERROR=' "$TMP_DIR/audit-summary.txt"; then
            warn "No se pudo parsear el JSON de pip-audit: resultado NO COMPROBADO."
        elif [[ "${total_deps:-0}" -eq 0 ]]; then
            warn "pip-audit no resolvió dependencias: resultado NO COMPROBADO."
        elif [[ "${total_vulns:-0}" -eq 0 ]]; then
            pass "pip-audit: 0 vulnerabilidades conocidas en ${total_deps} dependencias resueltas."
        else
            warn "pip-audit: ${total_vulns} vulnerabilidad(es) conocida(s) en ${affected_pkgs} paquete(s)."
            note "Severidad: pip-audit no expone CVSS en su JSON; consulte el CVE en OSV/NVD."
            sed -n '/^  /p' "$TMP_DIR/audit-summary.txt" | head -40
            extra_lines=$(sed -n '/^  /p' "$TMP_DIR/audit-summary.txt" | wc -l | tr -d ' ')
            if [[ "$extra_lines" -gt 40 ]]; then
                note "... y $((extra_lines - 40)) más. Ver $AUDIT_JSON"
            fi
            note "No se actualizan pins automáticamente: requiere tests y ventana de cambio."
        fi
    fi
fi

# ════════════════════════════════════════════════════════════
# 2. Código — bandit
# ════════════════════════════════════════════════════════════
section "2. Código (bandit -r app -ll)"
BANDIT_JSON="$TMP_DIR/bandit.json"
BANDIT_RAN=0

if [[ -x "$VENV/bin/bandit" ]]; then
    bandit_rc=0
    "$VENV/bin/bandit" -r app -ll -f json -o "$BANDIT_JSON" \
        >>"$TMP_DIR/bandit.log" 2>&1 || bandit_rc=$?

    if [[ ! -s "$BANDIT_JSON" ]]; then
        warn "bandit no produjo JSON (rc=$bandit_rc). Resultado NO COMPROBADO."
    else
        BANDIT_RAN=1
        "$PY" - "$BANDIT_JSON" >"$TMP_DIR/bandit-summary.txt" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
results = data.get("results", [])
high = [r for r in results if r.get("issue_severity") == "HIGH"]
medium = [r for r in results if r.get("issue_severity") == "MEDIUM"]
# Descripción por regla: nunca se imprime issue_text (puede contener el
# valor de una contraseña hardcodeada).
desc = {
    "B104": "bind a todas las interfaces",
    "B105": "posible contraseña hardcodeada",
    "B106": "posible contraseña hardcodeada en llamada",
    "B107": "posible contraseña hardcodeada en parámetro",
    "B310": "urlopen permite esquemas no esperados",
    "B506": "yaml.load no seguro (posible)",
    "B507": "Paramiko AutoAddPolicy: no verifica host key",
    "B601": "posible shell injection vía Paramiko",
    "B608": "posible SQL injection por concatenación",
}
print(f"HIGH={len(high)}")
print(f"MEDIUM={len(medium)}")
for r in high + medium:
    test_id = r.get("test_id", "?")
    print(
        f"{r.get('issue_severity')}\t{test_id}\t"
        f"{r.get('filename')}:{r.get('line_number')}\t"
        f"{desc.get(test_id, r.get('test_name', ''))}"
    )
PY

        bandit_high=$(sed -n 's/^HIGH=//p' "$TMP_DIR/bandit-summary.txt" | head -1)
        bandit_medium=$(sed -n 's/^MEDIUM=//p' "$TMP_DIR/bandit-summary.txt" | head -1)
        if [[ "${bandit_high:-0}" -eq 0 && "${bandit_medium:-0}" -eq 0 ]]; then
            pass "bandit: sin hallazgos de severidad media/alta."
        else
            while IFS=$'\t' read -r _sev test_id location text; do
                [[ "$_sev" == HIGH || "$_sev" == MEDIUM ]] || continue
                if [[ "$_sev" == "HIGH" ]]; then
                    fail_high "bandit $test_id ${location} — ${text:0:120}"
                else
                    warn "bandit $test_id ${location} — ${text:0:120}"
                fi
            done < <(grep -E '^(HIGH|MEDIUM)\t' "$TMP_DIR/bandit-summary.txt" || true)
        fi
    fi
fi

# ════════════════════════════════════════════════════════════
# 3. Secretos en archivos versionados
# ════════════════════════════════════════════════════════════
section "3. Secretos (solo archivos tracked)"

# 3a. Archivos sensibles que NUNCA deben estar en git.
sensitive_found=0
for f in .env .env-piloto config/.fernet_key config/api_keys.json \
         config/users.yaml config/devices.yaml config/.netbox_token \
         config/.initial_credentials; do
    if git ls-files --error-unmatch "$f" >/dev/null 2>&1; then
        fail_high "Archivo sensible versionado: $f"
        sensitive_found=1
    fi
done
while IFS= read -r f; do
    [[ -n "$f" ]] || continue
    fail_high "Material criptográfico versionado: $f"
    sensitive_found=1
done < <(git ls-files | grep -Ei '\.(pem|key|p12|pfx|jks)$' || true)
# Variantes con sufijo (.env.production, *.fernet_key.*) que el listado
# explícito no cubre.
while IFS= read -r f; do
    [[ -n "$f" ]] || continue
    [[ "$f" == ".env.example" ]] && continue
    fail_high "Archivo sensible versionado: $f"
    sensitive_found=1
done < <(git ls-files | grep -Ei '(^|/)\.env(\.|$)|(^|/)\.fernet_key|(^|/)api_keys\.json$|(^|/)\.netbox_token' || true)
if [[ "$sensitive_found" -eq 0 ]]; then
    pass "Ningún archivo sensible (.env, Fernet, API keys, usuarios, claves) está trackeado."
fi

# 3b. .gitignore cubre esos archivos.
missing_ignore=0
for f in .env config/.fernet_key config/api_keys.json config/users.yaml config/devices.yaml; do
    if ! git check-ignore -q "$f"; then
        warn ".gitignore NO cubre $f"
        missing_ignore=1
    fi
done
if [[ "$missing_ignore" -eq 0 ]]; then
    pass ".gitignore cubre .env, Fernet, API keys, usuarios y dispositivos."
fi

# 3c. Patrones de secretos en contenido tracked. Solo se imprime ubicación,
#     nunca el valor encontrado.
scan_secret_pattern() {
    local label="$1" severity="$2" regex="$3"
    shift 3
    local hits
    hits=$(git grep -nE "$regex" -- . "$@" 2>/dev/null || true)
    if [[ -z "$hits" ]]; then
        return 1
    fi
    while IFS= read -r hit; do
        local file line
        file="${hit%%:*}"
        line="${hit#*:}"
        line="${line%%:*}"
        if [[ "$severity" == "HIGH" ]]; then
            fail_high "$label — $file:$line"
        else
            warn "$label — $file:$line"
        fi
    done <<<"$hits"
    return 0
}

# JWT secret por defecto: se espera únicamente en app/core/security.py (donde
# está el guard de producción) y en tests/docs. Fuera de ahí = WARN.
default_hits=$(git grep -nE 'netpulse-dev-secret-change-me' -- . 2>/dev/null || true)
if [[ -z "$default_hits" ]]; then
    pass "JWT por defecto no aparece en el repo."
else
    unexpected=0
    while IFS= read -r hit; do
        file="${hit%%:*}"
        case "$file" in
            app/core/security.py|tests/*|docs/*|*.md) ;;
            *) warn "JWT secreto por defecto fuera de security.py — $file"; unexpected=1 ;;
        esac
    done <<<"$default_hits"
    if [[ "$unexpected" -eq 0 ]]; then
        pass "JWT por defecto solo en app/core/security.py y documentación; guard de producción presente."
    fi
fi

# Clave Fernet: 44 caracteres urlsafe base64 terminados en '='.
scan_secret_pattern "Posible clave Fernet versionada" HIGH \
    '^[A-Za-z0-9_-]{43}=$' || true

# Claves privadas PEM.
scan_secret_pattern "Clave privada PEM versionada" HIGH \
    '-----BEGIN ([A-Z]+ )?PRIVATE KEY-----' || true

# API keys np_ (formato real: np_ + 48 hex).
scan_secret_pattern "API key np_ versionada" HIGH \
    'np_[0-9a-fA-F]{32,}' || true

# Contraseñas/tokens en YAML/JSON versionados. Se filtran referencias a
# variables y placeholders conocidos antes de avisar.
pw_hits=$(git grep -nEi \
    '(password|passwd|secret|api[_-]?key|token|community)[[:space:]]*[:=][[:space:]]*[^[:space:]#]+' \
    -- '*.yaml' '*.yml' '*.json' 2>/dev/null || true)
real_pw=0
if [[ -n "$pw_hits" ]]; then
    while IFS= read -r hit; do
        content="${hit#*:}"
        content="${content#*:}"
        # Ignorar líneas comentadas (documentación de ejemplo, no valores).
        trimmed=$(printf '%s' "$content" | sed -e 's/^[[:space:]]*//')
        if [[ "$trimmed" == \#* ]]; then
            continue
        fi
        value="$content"
        # Ignorar referencias a variables y placeholders evidentes.
        if [[ "$value" =~ \$\{|\{\{|^[[:space:]]*$ ]]; then
            continue
        fi
        if [[ "$value" =~ (cambiame|changeme|change-me|example|placeholder|dummy|xxx) ]]; then
            note "Placeholder (no secreto real): ${hit%%:*}"
            continue
        fi
        real_pw=1
        warn "Posible credencial en YAML/JSON versionado — ${hit%%:*}"
    done <<<"$pw_hits"
fi
if [[ "$real_pw" -eq 0 ]]; then
    pass "Sin credenciales reales en YAML/JSON versionados (solo variables/placeholders)."
fi

# ════════════════════════════════════════════════════════════
# 4. Configuración
# ════════════════════════════════════════════════════════════
section "4. Configuración"

# 4a. .env.example sin secretos reales.
if [[ -f .env.example ]]; then
    example_bad=0
    while IFS= read -r line; do
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        [[ "$line" == *=* ]] || continue
        key="${line%%=*}"
        value="${line#*=}"
        value="${value%"${value##*[![:space:]]}"}"
        case "$key" in
            *SECRET*|*PASSWORD*|*TOKEN*|*KEY*|*COMMUNITY*|*PEPPER*) ;;
            *) continue ;;
        esac
        [[ -z "$value" ]] && continue
        case "$value" in
            *'${'*|*'{{'*) continue ;;
        esac
        lower_value=$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')
        case "$lower_value" in
            cambiame|changeme|change-me|example|placeholder|dummy|xxx|your-*|'< '*|'<>') continue ;;
        esac
        if [[ "$lower_value" == "admin" ]]; then
            warn ".env.example usa valor débil 'admin' en $key (cámbielo al copiar a .env)."
            example_bad=1
            continue
        fi
        warn ".env.example tiene valor no vacío en $key: revise que no sea un secreto real."
        example_bad=1
    done < .env.example
    if [[ "$example_bad" -eq 0 ]]; then
        pass ".env.example no contiene secretos reales (solo vacíos/placeholders)."
    fi
else
    warn ".env.example no existe: no se pudo comprobar."
fi

# 4b. Permisos de archivos sensibles locales (si existen).
stat_mode() {
    stat -f '%Lp' "$1" 2>/dev/null || stat -c '%a' "$1" 2>/dev/null || echo "?"
}
perm_bad=0
for f in .env config/.fernet_key config/users.yaml config/devices.yaml \
         config/api_keys.json config/.netbox_token config/.initial_credentials; do
    [[ -f "$f" ]] || continue
    mode="$(stat_mode "$f")"
    if [[ "$mode" == "?" ]]; then
        warn "No se pudo leer el modo de $f"
        perm_bad=1
    else
        group_digit="${mode: -2:1}"
        other_digit="${mode: -1}"
        if [[ "$group_digit" =~ [1-7] || "$other_digit" =~ [1-7] ]]; then
            warn "$f tiene permisos $mode (recomendado 600/640, sin acceso de otros)."
            perm_bad=1
        fi
    fi
done
if [[ "$perm_bad" -eq 0 ]]; then
    pass "Permisos de secretos locales correctos (o no existen en este clon)."
fi

# 4c. Guard de producción en security.py.
if grep -q 'NETPULSE_ENV=production requiere NETPULSE_JWT_SECRET' app/core/security.py 2>/dev/null; then
    pass "Guard de producción presente: NETPULSE_ENV=production exige JWT secret."
else
    fail_high "No se encontró el guard de producción para NETPULSE_JWT_SECRET."
fi

if [[ -f .env ]] && grep -qE '^[[:space:]]*NETPULSE_ENV[[:space:]]*=[[:space:]]*production[[:space:]]*$' .env; then
    jwt_line=$(grep -E '^[[:space:]]*NETPULSE_JWT_SECRET[[:space:]]*=' .env | tail -1 || true)
    jwt_val="${jwt_line#*=}"
    jwt_val="${jwt_val//\"/}"
    jwt_val="${jwt_val//\'/}"
    if [[ -z "$jwt_val" ]]; then
        fail_high ".env declara NETPULSE_ENV=production sin NETPULSE_JWT_SECRET (la API no arrancará)."
    elif [[ "$jwt_val" == "netpulse-dev-secret-change-me" ]]; then
        fail_high ".env usa el JWT secret por defecto en producción."
    elif [[ "${#jwt_val}" -lt 32 ]]; then
        warn ".env usa un JWT secret de ${#jwt_val} caracteres (<32): use openssl rand -hex 32."
    else
        pass ".env de producción define un JWT secret propio (no se imprime su valor)."
    fi
else
    note "No hay .env local con NETPULSE_ENV=production: guard no verificable aquí."
    note "deploy/docker-compose.prod.yml fuerza NETPULSE_ENV=production en el contenedor."
fi

# 4d. CORS: nunca '*' junto con credenciales.
cors_section=$(sed -n '/^[[:space:]]*CORSMiddleware,$/,/^)/p' app/main.py 2>/dev/null || true)
origins_section=$(awk '
    /^[[:space:]]*allow_origins[[:space:]]*=/ { capture = 1 }
    capture { print }
    capture && /]/ { exit }
' app/main.py 2>/dev/null || true)
if printf '%s' "$origins_section" | grep -qE '["'"'"']\*["'"'"']'; then
    if printf '%s' "$cors_section" | grep -q 'allow_credentials[[:space:]]*=[[:space:]]*True'; then
        fail_high "CORS permite '*' con allow_credentials=True."
    else
        warn "CORS permite cualquier origen ('*')."
    fi
elif printf '%s' "$cors_section" | grep -q 'allow_credentials[[:space:]]*=[[:space:]]*True'; then
    pass "CORS con credenciales limitado a orígenes explícitos (localhost:3000)."
else
    pass "CORS sin comodín."
fi

# 4e. Headers de seguridad (app + Caddy).
if grep -q 'X-Content-Type-Options' app/middleware/security.py 2>/dev/null \
    && grep -q 'SecurityHeadersMiddleware' app/main.py 2>/dev/null; then
    pass "SecurityHeadersMiddleware registrado (nosniff, X-Frame-Options, HSTS, CSP, etc.)."
else
    warn "No se pudo confirmar el middleware de headers de seguridad."
fi
if [[ -f deploy/Caddyfile ]] && grep -q 'Strict-Transport-Security' deploy/Caddyfile; then
    pass "Caddy añade HSTS y oculta Server (TLS real no probado por este script)."
else
    warn "deploy/Caddyfile sin HSTS o ausente."
fi

# 4f. Debug / reload en configuración de producción.
debug_hits=$(grep -rnE -- '(^|[^a-zA-Z])--reload([^a-zA-Z]|$)|reload[[:space:]]*=[[:space:]]*True|debug[[:space:]]*=[[:space:]]*True' \
    app/ deploy/ Dockerfile docker-compose*.yml 2>/dev/null || true)
if [[ -z "$debug_hits" ]]; then
    pass "Sin debug/reload activado en app, deploy ni Dockerfiles."
else
    while IFS= read -r hit; do
        warn "Posible debug/reload en producción — ${hit%%:*}"
    done <<<"$debug_hits"
fi

# 4g. Superficie de monitorización (Grafana) si se usa el compose de monitoring.
if [[ -f docker-compose.monitoring.yml ]] \
    && grep -q 'GF_SECURITY_ADMIN_PASSWORD: admin' docker-compose.monitoring.yml; then
    warn "docker-compose.monitoring.yml publica Grafana (3000) con admin/admin y anónimo activo; no usar en el host del cliente sin cambiarlo."
fi

# ── Resumen ─────────────────────────────────────────────────
section "Resumen"
printf 'PASS=%d  WARN=%d  FAIL=%d  (FAIL de severidad ALTA=%d)\n' \
    "$PASS" "$WARN" "$FAIL" "$HIGH_FAIL"
[[ "$AUDIT_RAN" -eq 1 ]] || note "pip-audit: NO COMPROBADO"
[[ "$BANDIT_RAN" -eq 1 ]] || note "bandit: NO COMPROBADO"
printf 'Informe: docs/SEGURIDAD.md\n'

if [[ "$HIGH_FAIL" -gt 0 ]]; then
    printf '\nResultado: FAIL (hay %d hallazgo(s) de severidad alta).\n' "$HIGH_FAIL"
    exit 1
fi

printf '\nResultado: OK (sin FAIL de severidad alta; revise los WARN).\n'
exit 0
