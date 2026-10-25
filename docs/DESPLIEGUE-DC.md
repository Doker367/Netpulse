# Despliegue en datacenter — runbook operativo

Fecha: 2026-10-25 · Versión: 1.0 · Aplica a: `main` (imagen Docker + colector por site)

Topología: **un nodo central** (API + TLS + monitoreo opcional) y **un colector por site**.
Los sites no exponen puertos entrantes: solo abren HTTPS saliente al central.

| Componente | Dónde | Puertos |
|---|---|---|
| API + dashboard + WS | central (Docker) | 8082 interno |
| Caddy (TLS) | central | 80/443 |
| Prometheus/Alertmanager/Loki | central (perfil `monitoring`) | internos |
| Colector | cada site (Docker o systemd) | ningún inbound |
| Equipos de red | site | 22/161/443 según driver |

## 1. Preflight (día 0)

- DNS `noc.empresa.com` → IP del central.
- Certificado: CA pública (Caddy automático) o CA interna + `ca_file` en el colector.
- NTP sincronizado en central y sites (`timedatectl`).
- SO: Ubuntu 22.04/24.04 o Debian 12; firewall cerrado salvo SSH admin y 443.
- Inventario real en `config/devices.yaml` (sin equipos del lab) con grupo y site.
- Usuarios reales en `config/users.yaml` con roles y scopes (`scope_group`, `scope_sites`).
- Secretos generados: `NETPULSE_JWT_SECRET`, contraseñas, clave Fernet. Nunca en git.

## 2. Preparar el host central

```bash
sudo scripts/prepare_host.sh --dry-run --domain noc.empresa.com   # revisar
sudo scripts/prepare_host.sh --domain noc.empresa.com             # aplicar
sudo scripts/prepare_host.sh --domain noc.empresa.com --apply-firewall
```

Crea usuario/grupo `netpulse` con **uid/gid 10001** (el mismo de la imagen), `/opt/netpulse`
y los directorios de estado `config/ runtime/ audit/ backups/ reports/` con permisos
predecibles. `--apply-firewall` solo escribe reglas; **no** activa ufw ni reinicia firewalld.

## 3. Central con Docker

```bash
cd /opt/netpulse
cp .env.example .env && chmod 600 .env
$EDITOR .env
```

`.env` mínimo de producción:

| Variable | Valor |
|---|---|
| `NETPULSE_ENV` | `production` (activa la guarda de JWT) |
| `NETPULSE_JWT_SECRET` | `openssl rand -hex 32` |
| `NETPULSE_HOST` | `0.0.0.0` (dentro del contenedor) |
| `NETPULSE_DOMAIN` | `noc.empresa.com` |
| `NETPULSE_METRICS_ENABLED` | `true` |
| `NETPULSE_ALERTMANAGER_URL` / `NETPULSE_PROMETHEUS_URL` | `http://alertmanager:9093` / `http://prometheus:9090` (perfil monitoring) |
| `NETPULSE_ALERTMANAGER_TOKEN` | token del NOC |

```bash
docker compose -f deploy/docker-compose.prod.yml up -d --build
docker compose -f deploy/docker-compose.prod.yml ps
curl -fsS https://$NETPULSE_DOMAIN/api/health
```

Estado persistente (binds): `config/`, `runtime/`, `audit/`, `backups/`;
`reports_data` es volumen nombrado. `metrics_history.json` vive en `config/`.

Permisos obligatorios en Linux: los cuatro directorios deben pertenecer a **uid 10001**
(`prepare_host.sh` lo hace). Si no, la API responde 500 al escribir auditoría.

### 3.1 TLS, WebSocket y logs

- `deploy/Caddyfile` obtiene certificado automático y hace proxy a `api:8082`.
- WebSocket `/ws/*` con timeout de 24 h (terminal). Con nginx haría falta
  `proxy_http_version 1.1`, `Upgrade`/`Connection` y `proxy_read_timeout 3600s`.
- **No registrar query strings de `/ws/`**: el token viaja en `?token=`.
  El Caddyfile desactiva el access log (nivel ERROR) y la app redacta `token=***`.

### 3.2 Perfil monitoring

```bash
docker compose -f deploy/docker-compose.prod.yml --profile monitoring up -d
```

- Prometheus consulta `api` por la red interna; Alertmanager recibe del NOC.
- **Ojo:** `config/alertmanager.yml.template` apunta a `127.0.0.1`. En Docker
  el webhook debe ser `http://api:8082/api/alerts/webhook`. Ajusta tu
  `config/alertmanager.yml` real (el template no se edita).
- No se publican 9090/9093/3100: acceso por túnel SSH o red de administración.

### 3.3 Alternativa sin Docker (systemd)

`deploy/netpulse-api.service`: `User=netpulse`, `EnvironmentFile=/opt/netpulse/.env`,
`ProtectSystem=strict` y `ReadWritePaths` para los directorios de estado.
Úsala en hosts donde no se permita Docker o para depurar el arranque.

## 4. Colector por site

### 4.1 Crear la API key
En la UI: **API Keys → nueva → tipo collector**. Se muestra una sola vez.
Guárdala en el site, nunca en este repo.

### 4.2 Docker (recomendado si el site tiene Docker)

```bash
sudo install -d -m 750 /etc/netpulse
sudo install -m 600 -o 10002 -g 10002 collector.yaml /etc/netpulse/collector.yaml
sudo install -m 600 collector.env /etc/netpulse/collector.env   # NETPULSE_COLLECTOR_API_KEY=np_...
sudo docker compose -f deploy/collector/docker-compose.yml run --rm collector --check
sudo docker compose -f deploy/collector/docker-compose.yml up -d
```

`network_mode: host` y `CAP_NET_RAW` son deliberados: el colector sondea equipos
del site y usa `ping` para ICMP. Detalle en `deploy/collector/README.md`.

### 4.3 systemd (site sin Docker)

Ver `docs/collector-config.example.yaml` y `deploy/netpulse-collector.service`.
Antes de `enable --now`, ejecuta siempre `--check`; si falla, no arranques.

### 4.4 Verificación
`GET /api/remote/sites` debe mostrar el site en `fresh` y los equipos con estado real.
Si el central cae, el colector bufferea (72 h por defecto) y drena sin duplicados.

## 5. Aceptación (checklist de 10)

| # | Prueba | Criterio |
|---|---|---|
| 1 | `scripts/smoke_full.sh` contra el central | 19/19 (SKIPs documentados) |
| 2 | `/api/remote/sites` | site `fresh`, equipos reales |
| 3 | Corte del central 10 min | buffer sube, drena, 0 duplicados, `gap_since` honesto |
| 4 | Terminal web a un equipo del site | "Conectado" y shell responde |
| 5 | SFTP | listado real, o 501 honesto si el equipo no tiene subsistema |
| 6 | RBAC con `operador-site` | 403 fuera de scope con detail literal en la UI |
| 7 | Silencio de alerta | silenciada en Alertmanager y visible en la UI |
| 8 | Reporte SLA de una semana | porcentaje solo con muestras; cobertura declarada |
| 9 | Retención | campos sin medir = `null`, nunca 0 |
| 10 | NetBox (si aplica) | creados/actualizados/saltados con motivo; nunca borra |

Cierre: 7 días de operación continua + `docs/piloto-dc.md` con mediciones y ajustes.

## 6. Actualización y rollback

Procedimiento (medido en el drill local, imagen del mismo Dockerfile):

```bash
git fetch --tags && git checkout deploy-YYYYMMDD
docker compose -f deploy/docker-compose.prod.yml up -d --build
curl -fsS https://$NETPULSE_DOMAIN/api/health
```

Rollback: `git checkout deploy-ANTERIOR` y repetir. Con los binds actuales,
los datos sobreviven a recrear el contenedor.

Tiempos reales del drill (`scripts/deploy_drill.sh`):

| Paso | Downtime | Healthy |
|---|---|---|
| Arranque inicial | — | 6.49 s |
| Upgrade v1→v2 | **2.71 s** | 6.87 s |
| Rollback v2→v1 | **2.86 s** | 7.00 s |
| Restore de estado | **2.39 s** | 6.54 s |

Antes de actualizar: `git tag deploy-$(date +%Y%m%d)` y backup (ver §7).

## 7. Backup, restore y claves

Backup consistente (API parada o SQLite en modo backup):

```bash
docker compose -f deploy/docker-compose.prod.yml stop api
tar czf netpulse-$(date +%F).tgz config runtime audit backups .env
docker compose -f deploy/docker-compose.prod.yml start api
```

- `.env` y la **clave Fernet** van en custodia separada: sin ella las credenciales
  de dispositivo son irrecuperables.
- Restore: parar API, extraer el tar, verificar permisos uid 10001, arrancar y
  volver a correr el smoke.
- Rotación Fernet: `scripts/rotate_fernet.py` (ver `docs/OPERACIONES.md`).
- Retención: activar la poda de 30 días y vigilar disco; la poda real aún no está
  medida a escala (pendiente de capacidad).

## 8. Riesgos y límites honestos

1. Sin HA: un central y un colector por site. Si el central cae, el NOC queda ciego.
2. `workers=1` y SQLite: decisión de diseño; no escalar workers sin migrar el almacén.
3. RBAC fase 2 pendiente (SFTP upload, facts, compliance, plantillas, NetBox).
4. Revocación de sesión: el JWT vive hasta expirar (no hay `token_version`).
5. Sensores DC (temperatura/PDU/UPS) no implementados: la UI muestra "sin sonda".
6. Proxy: sin WebSocket y sin redacción de query strings, el terminal falla o filtra el token.

## 9. Diagnóstico rápido

| Síntoma | Causa probable |
|---|---|
| 500 al login/auditar | permisos de `config/ audit/` (deben ser uid 10001) |
| Terminal se corta al minuto | proxy sin upgrade/read_timeout |
| `NETPULSE_ENV=production` no arranca | falta `NETPULSE_JWT_SECRET` |
| Colector `--check` 401 | API key revocada o de otro site |
| Site `stale` | colector caído o sin salida HTTPS al central |
| SFTP 501 | equipo real sin subsistema SFTP (no es un fallo del NOC) |

Referencias: `docs/DOCKER.md`, `docs/STAGING.md`, `docs/CI.md`,
`deploy/collector/README.md`, `docs/OPERACIONES.md`.
