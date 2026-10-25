#!/usr/bin/env bash
# ============================================================
# prepare_host.sh — prepara un host Linux para NetPulse NOC.
# ============================================================
# Idempotente. Pensado para el servidor CENTRAL del datacenter, tanto si
# la API corre con Docker como si corre con systemd (opción B).
#
#   sudo scripts/prepare_host.sh --domain noc.example.com
#   sudo scripts/prepare_host.sh --domain noc.example.com --apply-firewall
#   scripts/prepare_host.sh --dry-run --domain noc.example.com
#
# Qué hace:
#   - crea grupo/usuario de sistema netpulse con uid/gid 10001 (el mismo
#     uid de la imagen Docker, para que los bind mounts sean predecibles);
#   - crea /opt/netpulse y los directorios de estado
#     config/ runtime/ audit/ backups/ reports/ con dueño 10001:10001,
#     modo 0750 (y ficheros sensibles a 0600);
#   - prepara /etc/netpulse (0750 root:netpulse), donde vive el env file
#     de systemd (0640 root:netpulse);
#   - comprueba Docker/Compose, DNS, NTP, espacio y SELinux;
#   - IMPRIME las reglas de firewall sugeridas. Solo las aplica con
#     --apply-firewall (nunca activa ufw ni reinicia firewalld).
#
# No escribe secretos, no crea usuarios de la aplicación y no arranca
# ningún servicio.
# ============================================================

set -euo pipefail

ROOT_DIR=/opt/netpulse
ETC_DIR=/etc/netpulse
SERVICE_USER=netpulse
SERVICE_GROUP=netpulse
SERVICE_UID=10001
SERVICE_GID=10001
SSH_PORT=22
DOMAIN=""
DRY_RUN=0
FIREWALL_MODE=print   # print | apply | none
SELINUX_RELABEL=0

STATE_DIRS=(config runtime audit backups reports)

usage() {
    cat <<'EOF'
Uso: prepare_host.sh [opciones]

  --domain FQDN        Dominio público del NOC (para comprobar DNS).
  --ssh-port N         Puerto SSH que se conserva en el firewall (defecto: 22).
  --root DIR           Raíz de la instalación (defecto: /opt/netpulse).
  --uid N              UID/GID del usuario de servicio (defecto: 10001).
  --apply-firewall     Aplica las reglas con ufw/firewalld si están instalados.
  --no-firewall        No imprime ni aplica reglas de firewall.
  --selinux-relabel    Aplica chcon -Rt container_file_t a los directorios de estado.
  --dry-run            Muestra los comandos sin ejecutarlos (no requiere root).
  -h, --help           Esta ayuda.

El script es idempotente: se puede ejecutar varias veces sin efectos extra.
EOF
}

log()  { printf '[prepare-host] %s\n' "$*"; }
warn() { printf '[prepare-host] AVISO: %s\n' "$*" >&2; }
fail() { printf '[prepare-host] ERROR: %s\n' "$*" >&2; exit 2; }

run() {
    if [[ $DRY_RUN -eq 1 ]]; then
        printf '  + %s\n' "$*"
    else
        "$@"
    fi
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --domain) DOMAIN="${2:-}"; shift 2 ;;
        --ssh-port) SSH_PORT="${2:-}"; shift 2 ;;
        --root) ROOT_DIR="${2:-}"; shift 2 ;;
        --uid) SERVICE_UID="${2:-}"; SERVICE_GID="$SERVICE_UID"; shift 2 ;;
        --apply-firewall) FIREWALL_MODE=apply; shift ;;
        --no-firewall) FIREWALL_MODE=none; shift ;;
        --selinux-relabel) SELINUX_RELABEL=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) fail "opción desconocida: $1 (use --help)" ;;
    esac
done

[[ "$SSH_PORT" =~ ^[0-9]+$ ]] || fail "--ssh-port debe ser numérico"
[[ "$SERVICE_UID" =~ ^[0-9]+$ ]] || fail "--uid debe ser numérico"
[[ "$ROOT_DIR" = /* ]] || fail "--root debe ser una ruta absoluta"
if [[ $DRY_RUN -eq 0 ]]; then
    [[ "$(uname -s)" == "Linux" ]] || fail "este script prepara hosts Linux; use --dry-run para inspeccionarlo"
    [[ "${EUID:-$(id -u)}" -eq 0 ]] || fail "ejecute con sudo (o use --dry-run)"
fi

log "raíz=$ROOT_DIR uid=$SERVICE_UID usuario=$SERVICE_USER dry_run=$DRY_RUN"

# ── 1. Usuario y grupo de sistema ───────────────────────────
if getent group "$SERVICE_GROUP" >/dev/null 2>&1; then
    existing_gid=$(getent group "$SERVICE_GROUP" | cut -d: -f3)
    if [[ "$existing_gid" != "$SERVICE_GID" ]]; then
        fail "el grupo $SERVICE_GROUP ya existe con gid=$existing_gid (se esperaba $SERVICE_GID)"
    fi
    log "grupo $SERVICE_GROUP ya existe (gid=$existing_gid)"
else
    log "creando grupo $SERVICE_GROUP (gid=$SERVICE_GID)"
    run groupadd --system --gid "$SERVICE_GID" "$SERVICE_GROUP"
fi

if getent passwd "$SERVICE_USER" >/dev/null 2>&1; then
    existing_uid=$(id -u "$SERVICE_USER")
    existing_gid_user=$(id -g "$SERVICE_USER")
    if [[ "$existing_uid" != "$SERVICE_UID" || "$existing_gid_user" != "$SERVICE_GID" ]]; then
        fail "el usuario $SERVICE_USER existe con uid/gid=$existing_uid:$existing_gid_user (se esperaba $SERVICE_UID:$SERVICE_GID)"
    fi
    log "usuario $SERVICE_USER ya existe (uid=$existing_uid gid=$existing_gid_user)"
else
    if getent passwd "$SERVICE_UID" >/dev/null 2>&1; then
        fail "el uid $SERVICE_UID ya pertenece a $(getent passwd "$SERVICE_UID" | cut -d: -f1); elija otro con --uid"
    fi
    log "creando usuario $SERVICE_USER (uid=$SERVICE_UID, nologin)"
    run useradd --system --uid "$SERVICE_UID" --gid "$SERVICE_GID" \
        --home-dir "$ROOT_DIR" --no-create-home \
        --shell /usr/sbin/nologin --comment "NetPulse NOC" "$SERVICE_USER"
fi

# ── 2. Directorios de instalación y estado ──────────────────
log "creando $ROOT_DIR y directorios de estado"
run install -d -m 0755 -o root -g root "$ROOT_DIR"
for dir in "${STATE_DIRS[@]}"; do
    run install -d -m 0750 -o "$SERVICE_UID" -g "$SERVICE_GID" "$ROOT_DIR/$dir"
done

if [[ $DRY_RUN -eq 0 ]]; then
    # Los ficheros que crea la app deben ser privados del servicio. Se
    # excluyen .git y el código para no tocar el checkout.
    for dir in "${STATE_DIRS[@]}"; do
        find "$ROOT_DIR/$dir" -type f \
            \( -name '*.yaml' -o -name '*.yml' -o -name '*.json' \
               -o -name '*.db' -o -name '*.db-wal' -o -name '*.db-shm' \
               -o -name '.fernet_key*' -o -name '.netbox_token*' \) \
            -exec chmod 0600 {} +
    done
    # El .env del compose (si existe) no lo lee el contenedor: solo Compose.
    if [[ -f "$ROOT_DIR/.env" ]]; then
        chown root:root "$ROOT_DIR/.env"
        chmod 0600 "$ROOT_DIR/.env"
    fi
fi

# ── 3. /etc/netpulse (env files) ────────────────────────────
log "preparando $ETC_DIR"
run install -d -m 0750 -o root -g "$SERVICE_GROUP" "$ETC_DIR"
if [[ -f "$ETC_DIR/netpulse.env" && $DRY_RUN -eq 0 ]]; then
    chown root:"$SERVICE_GROUP" "$ETC_DIR/netpulse.env"
    chmod 0640 "$ETC_DIR/netpulse.env"
fi
if [[ -f "$ETC_DIR/collector.env" && $DRY_RUN -eq 0 ]]; then
    chown root:root "$ETC_DIR/collector.env"
    chmod 0600 "$ETC_DIR/collector.env"
fi

# ── 4. Comprobaciones de preflight (informativas) ───────────
log "comprobaciones del host"

if command -v docker >/dev/null 2>&1; then
    log "docker: $(docker --version 2>/dev/null || echo 'no ejecutable')"
    if docker compose version >/dev/null 2>&1; then
        log "compose: $(docker compose version --short 2>/dev/null || echo 'v2')"
    else
        warn "Docker Compose v2 no disponible; el stack de producción lo necesita"
    fi
else
    warn "Docker no instalado (válido solo para la opción systemd)"
fi

if command -v timedatectl >/dev/null 2>&1; then
    ntp_state=$(timedatectl show -p NTPSynchronized --value 2>/dev/null || echo unknown)
    if [[ "$ntp_state" == "yes" ]]; then
        log "NTP: sincronizado"
    else
        warn "NTP no confirmado (timedatectl NTPSynchronized=$ntp_state); active chrony/systemd-timesyncd"
    fi
else
    warn "timedatectl no disponible; verifique chrony/NTP manualmente"
fi

if [[ -n "$DOMAIN" ]]; then
    if getent ahosts "$DOMAIN" >/dev/null 2>&1; then
        log "DNS: $DOMAIN resuelve ($(getent ahosts "$DOMAIN" | awk 'NR==1{print $1}'))"
    else
        warn "DNS: $DOMAIN NO resuelve en este host"
    fi
fi

free_kb=$(df -Pk "$(dirname "$ROOT_DIR")" | awk 'NR==2{print $4}')
if [[ -n "$free_kb" && "$free_kb" -lt 5242880 ]]; then
    warn "quedan menos de 5 GiB libres en $ROOT_DIR (${free_kb} KiB)"
fi

selinux_state=none
if command -v getenforce >/dev/null 2>&1; then
    selinux_state=$(getenforce 2>/dev/null || echo unknown)
fi
if [[ "$selinux_state" == "Enforcing" ]]; then
    log "SELinux: Enforcing"
    log "  alternativa recomendada: añada :Z a los binds del compose (config/runtime/audit/backups)"
    log "  o etiquete una vez: chcon -Rt container_file_t $ROOT_DIR/{config,runtime,audit,backups}"
    if [[ $SELINUX_RELABEL -eq 1 ]]; then
        run chcon -Rt container_file_t "$ROOT_DIR/config" "$ROOT_DIR/runtime" \
            "$ROOT_DIR/audit" "$ROOT_DIR/backups"
    fi
elif [[ "$selinux_state" != "none" ]]; then
    log "SELinux: $selinux_state (no requiere relabel)"
fi

# ── 5. Firewall sugerido ────────────────────────────────────
print_firewall() {
    cat <<EOF

Reglas sugeridas (el central solo necesita 80/443; 8082 NUNCA al exterior):
  # ufw
  ufw allow ${SSH_PORT}/tcp
  ufw allow 80/tcp
  ufw allow 443/tcp
  ufw allow 443/udp
  # ufw deny 8082/tcp   # la API vive en la red interna de Docker

  # firewalld
  firewall-cmd --permanent --add-service=http
  firewall-cmd --permanent --add-service=https
  firewall-cmd --permanent --add-port=443/udp
  firewall-cmd --permanent --add-port=${SSH_PORT}/tcp
  firewall-cmd --reload

Si /api/metrics debe scrapearse desde otro host, permita 8082 solo desde
la IP de Prometheus (o mejor: use el perfil monitoring del compose, que
no publica puertos).
EOF
}

if [[ "$FIREWALL_MODE" == "print" ]]; then
    print_firewall
elif [[ "$FIREWALL_MODE" == "apply" ]]; then
    if command -v ufw >/dev/null 2>&1; then
        log "aplicando reglas ufw (no se activa ufw)"
        run ufw allow "${SSH_PORT}/tcp"
        run ufw allow 80/tcp
        run ufw allow 443/tcp
        run ufw allow 443/udp
    elif command -v firewall-cmd >/dev/null 2>&1; then
        log "aplicando reglas firewalld"
        run firewall-cmd --permanent --add-service=http
        run firewall-cmd --permanent --add-service=https
        run firewall-cmd --permanent --add-port=443/udp
        run firewall-cmd --permanent --add-port="${SSH_PORT}/tcp"
        run firewall-cmd --reload
    else
        warn "no hay ufw ni firewalld; aplique las reglas manualmente"
        print_firewall
    fi
fi

# ── 6. Resumen ──────────────────────────────────────────────
cat <<EOF

[prepare-host] listo.
  usuario/grupo : $SERVICE_USER ($SERVICE_UID:$SERVICE_GID)
  instalación   : $ROOT_DIR
  estado        : ${STATE_DIRS[*]} (dueño $SERVICE_UID:$SERVICE_GID, 0750)
  env systemd   : $ETC_DIR/netpulse.env (0640 root:$SERVICE_GROUP)

Siguientes pasos (runbook: docs/DESPLIEGUE-DC.md):
  1) Copie el repo en $ROOT_DIR (o haga git clone) y fije el tag verificado.
  2) Cree $ROOT_DIR/.env desde .env.example (0600, sin commitear).
  3) Siembre usuarios (scripts/seed_users.py) y arranque el stack.
  4) Compruebe: docker compose -f deploy/docker-compose.prod.yml ps
EOF
