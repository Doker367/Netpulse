# Despliegue con Docker (API + Caddy)

Guía del stack de **producción** de NetPulse NOC. La API corre en un
contenedor sin privilegios y Caddy termina TLS y publica 80/443. El
colector remoto de sedes **no** va en este stack: se despliega aparte
(`deploy/netpulse-collector.service` o su propio contenedor).

Archivos implicados:

| Archivo | Uso |
|---|---|
| `Dockerfile` | Imagen multi-stage `python:3.12-slim`, usuario uid 10001 |
| `.dockerignore` | Contexto mínimo, sin secretos ni estado |
| `deploy/docker-compose.prod.yml` | API + Caddy + perfil `monitoring` |
| `deploy/Caddyfile` | TLS automático, reverse proxy, WebSocket |
| `deploy/netpulse-api.service` | Opción B: systemd sin Docker |
| `docs/DOCKER.md` | Este documento |

---

## 1. Requisitos

- Docker Engine 24+ con Compose v2 (`docker compose`, no `docker-compose`).
- 2 vCPU y 2 GB de RAM libres (la API sola usa ~200-400 MB).
- Puertos **80/tcp**, **443/tcp** y opcionalmente **443/udp** (HTTP/3)
  accesibles desde Internet si se usa un dominio público.
- Un dominio con DNS `A`/`AAAA` apuntando al servidor (para Let's Encrypt).
- En Linux, permisos para que el uid **10001** escriba en los bind mounts
  (ver §4).

La imagen se construye con `requirements.txt` (versiones pineadas) y
wheels; la imagen final no contiene `gcc`, `make` ni cabeceras.

---

## 2. Variables de entorno (`.env`)

Compose lee `../.env` (la raíz del repo). No se versiona: cópielo de
`.env.example` y complete los secretos.

```bash
cd /ruta/netpulse-noc
cp .env.example .env
chmod 600 .env
```

Mínimo imprescindible para este stack:

```dotenv
# La API escucha en todas las interfaces DENTRO del contenedor.
NETPULSE_HOST=0.0.0.0
NETPULSE_PORT=8082

# Obligatorio: sin él la API falla al arrancar con NETPULSE_ENV=production.
# Generar: python -c "import secrets; print(secrets.token_hex(32))"
NETPULSE_JWT_SECRET=<64 hex>

# Dominio público. Sin valor usa localhost + CA interna de Caddy (pruebas).
NETPULSE_DOMAIN=noc.example.com

# Usuarios iniciales (scripts/seed_users.py), contraseñas >= 12 caracteres.
NETPULSE_ADMIN_PASSWORD=<...>
NETPULSE_OPERATOR_PASSWORD=<...>
```

El compose fija además `NETPULSE_ENV=production` y `HOME=/tmp`. El resto
de variables (NetBox, SNMP, notificaciones, offsite, etc.) están
documentadas en `.env.example`; se pasan tal cual al contenedor.

> **Secretos**: `.env`, `config/users.yaml`, `config/devices.yaml`,
> `config/.fernet_key`, `config/alertmanager.yml` y los `.db` quedan
> fuera de la imagen por `.dockerignore`. No los añada al repositorio.

### Usuarios y credenciales

Un clon limpio no trae `config/users.yaml` utilizable ni
`config/devices.yaml`. Antes del primer arranque:

```bash
# Crea/actualiza config/users.yaml con superadmin y operator.
NETPULSE_ADMIN_PASSWORD='...' NETPULSE_OPERATOR_PASSWORD='...' \
    .venv/bin/python scripts/seed_users.py

# Opcional: inventario inicial (o use la UI/API de descubrimiento).
# La clave Fernet se genera sola al guardar el primer dispositivo.
```

Si restaura un `devices.yaml` con contraseñas cifradas, restaure también
`config/.fernet_key`; sin esa clave las credenciales no se descifran.

---

## 3. Arranque

Desde la raíz del repo:

```bash
# 1) Validar el compose (no arranca nada)
docker compose -f deploy/docker-compose.prod.yml config

# 2) Construir la imagen y levantar API + Caddy
docker compose -f deploy/docker-compose.prod.yml up -d --build

# 3) Estado (api debe aparecer "healthy")
docker compose -f deploy/docker-compose.prod.yml ps

# 4) Logs
docker compose -f deploy/docker-compose.prod.yml logs -f api
docker compose -f deploy/docker-compose.prod.yml logs -f caddy
```

Caddy espera a que la API esté `healthy` (`depends_on.condition:
service_healthy`) y luego obtiene el certificado. La API **no publica
puertos al host**: solo es accesible por la red interna como `api:8082`.

Comandos útiles:

```bash
docker compose -f deploy/docker-compose.prod.yml restart api
docker compose -f deploy/docker-compose.prod.yml down          # conserva volúmenes
docker compose -f deploy/docker-compose.prod.yml down -v       # BORRA volúmenes (¡cuidado!)
```

### Actualizar

```bash
git pull
docker compose -f deploy/docker-compose.prod.yml build --pull api
docker compose -f deploy/docker-compose.prod.yml up -d
```

El healthcheck de la imagen consulta `http://127.0.0.1:8082/api/health`
con `python` + `urllib` (la imagen no incluye `curl`). Configuración:
cada 30 s, timeout 5 s, 5 reintentos, `start_period` 30 s.

```bash
docker inspect --format '{{.State.Health.Status}}' netpulse-api
docker inspect --format '{{json .State.Health}}' netpulse-api | python3 -m json.tool
```

---

## 4. Volúmenes y estado persistente

La raíz del contenedor es **de solo lectura** (`read_only: true`); solo
`/tmp` (tmpfs) y los montajes siguientes son escribibles:

| Host | Contenedor | Contenido |
|---|---|---|
| `./config` | `/app/config` | `users.yaml`, `devices.yaml`, `.fernet_key`, plantillas, programación, `metrics_history.json` |
| `./runtime` | `/app/runtime` | `remote_collector.db` (si `NETPULSE_REMOTE_DB` no apunta a otro sitio) |
| `./audit` | `/app/audit` | `audit.db` (SQLite + WAL) |
| `./backups` | `/app/backups` | backups de configuración de dispositivos |
| volumen `reports_data` | `/app/reports` | PDF/CSV generados por reportes |

**`metrics_history.json`**: la aplicación lo escribe en `config/`
(`app/services/metrics_history_svc.py`), así que queda persistido por el
bind `./config`. Se decidió **no** montar un fichero dedicado porque
Docker crearía un directorio si el fichero no existiera (rompiendo el
arranque) y **no** usar un volumen aparte porque no puede montarse sobre
un subpath de un bind ya existente. Con `NETPULSE_METRICS_ENABLED=false`
no se genera; si lo activa, estará en `config/metrics_history.json`.

### Permisos (Linux)

El proceso corre como **uid/gid 10001**. En Docker Desktop/Colima los
bind mounts suelen ser permisivos; en Linux hay que preparar las carpetas:

```bash
mkdir -p config runtime audit backups
sudo chown -R 10001:10001 config runtime audit backups
# El .env se lee como root por Compose; no necesita ser legible por 10001.
```

Con SELinux en enforcing, añada el relabel a los binds (`:Z`) o aplique
`chcon -Rt container_file_t config runtime audit backups`.

### Backup

Con la API parada para obtener una copia consistente de SQLite:

```bash
docker compose -f deploy/docker-compose.prod.yml stop api

tar czf "netpulse-backup-$(date +%F).tgz" \
    config runtime audit backups

# Volumen de reportes
docker run --rm \
  -v netpulse-prod_reports_data:/data:ro \
  -v "$PWD":/out \
  alpine:3.20 tar czf /out/reports-$(date +%F).tgz -C /data .

docker compose -f deploy/docker-compose.prod.yml start api
```

Copie fuera del servidor el `.tgz` (y la clave `.fernet_key`, sin la cual
los backups de dispositivos no sirven). `caddy_data` contiene la clave
privada TLS y los certificados; solo hace falta respaldarlo si quiere
evitar reemitirlos (Let's Encrypt tiene límites de emisión).

> El stack de monitoreo usa volúmenes nombrados
> (`netpulse-prod_prometheus_data`, `_alertmanager_data`, `_loki_data`):
> son series temporales descartables, no forman parte del backup crítico.

---

## 5. TLS, Caddy y logs

`deploy/Caddyfile` usa `{$NETPULSE_DOMAIN:localhost}`:

- Dominio público → certificado Let's Encrypt automático (necesita 80/443).
- `localhost` → CA interna de Caddy. Para probar sin `-k`, exporte la raíz:
  ```bash
  docker cp netpulse-caddy:/data/caddy/pki/authorities/local/root.crt /tmp/caddy-root.crt
  curl --cacert /tmp/caddy-root.crt https://localhost/api/health
  ```
  Caddy intenta instalar esa CA en el trust store del contenedor y falla
  (esperado, contenedor read-only); el certificado se emite igualmente.

Cabeceras añadidas: `Strict-Transport-Security`, `X-Content-Type-Options`,
`Referrer-Policy`; se elimina `Server`. La app añade además CSP,
`X-Frame-Options`, etc.

WebSocket: `reverse_proxy` negocia el upgrade; `/ws/*` tiene timeouts de
24 h para el terminal interactivo.

### Por qué no hay access log de Caddy

Los JWT del terminal viajan en la query string (`/ws/terminal/...?token=`)
y también se usan en descargas. Un access log normal (nivel INFO) escribiría
esas URIs en disco. El `log` de Caddy se configura a nivel **ERROR**: se
conservan errores de TLS/proxy, pero **no** se registra ninguna URI. La
propia API redacta `token=***` en sus logs (ver `app/core/logging_redaction.py`).

No cambie ese nivel a INFO sin antes mover el token fuera de la query
string o filtrar `/ws/`.

---

## 6. Perfil opcional: monitoring

Reutiliza los YAML de `config/` montados en solo lectura y volúmenes
nombrados. Versiones **fijadas** (equivalentes a
`docker-compose.monitoring.yml`, que usa `:latest`; aquí no se usa
`latest` en producción):

| Servicio | Imagen |
|---|---|
| Prometheus | `prom/prometheus:v3.15.1` |
| Alertmanager | `prom/alertmanager:v0.34.1` |
| Loki | `grafana/loki:3.7.9` |
| Promtail | `grafana/promtail:3.6.11` |

```bash
# Alertmanager necesita su config real (el template tiene un placeholder).
cp config/alertmanager.yml.template config/alertmanager.yml
# Edite config/alertmanager.yml:
#   url: http://api:8082/api/alerts/webhook
#   credentials: <mismo valor que NETPULSE_ALERTMANAGER_TOKEN>
chmod 600 config/alertmanager.yml

docker compose -f deploy/docker-compose.prod.yml --profile monitoring up -d
```

Detalles:

- **Scrape de la API**: `config/prometheus.yml` apunta a
  `host.docker.internal:8082` porque la API no publica puertos. El servicio
  `api` tiene el alias de red `host.docker.internal`, de modo que Prometheus
  resuelve ese nombre al contenedor `api` sin editar el YAML. Verificado:
  el target `netpulse-api` queda `up`.
- **Alertmanager**: el webhook debe apuntar a `http://api:8082/...` (no a
  `127.0.0.1`, que dentro del contenedor es el propio Alertmanager).
- **Grafana no se incluye** en este compose. Si ya usa
  `docker-compose.monitoring.yml`, mantenga su Grafana y añada Prometheus/
  Loki como datasources; el dashboard embebe Grafana por URL.
- Ningún servicio de monitoreo publica puertos al host. Para consultarlos:
  ```bash
  docker compose -f deploy/docker-compose.prod.yml exec prometheus \
      wget -qO- http://localhost:9090/-/healthy
  # o un túnel SSH al 9090/9093/3100 del host de Docker
  ```

---

## 7. Limitaciones (honestas)

- **Un solo worker** (`--workers 1`), deliberado: SQLite (`audit.db`,
  `remote_collector.db`) y el poller de métricas viven en el proceso.
  Varios workers duplicarían escritores y recolecciones. No hay HA ni
  escalado horizontal de la API tal como está.
- **SQLite**: un único host y un único proceso escritor. No ponga el
  `config/`, `audit/` ni `runtime/` en NFS/SMB.
- **TLS**: lo termina Caddy. No active `NETPULSE_SSL_CERTFILE` dentro del
  contenedor; el puerto 8082 es HTTP plano en la red interna.
- **Colector remoto**: va aparte (sedes). La API solo expone
  `/api/remote/*`; no lo incluya en este compose.
- **syslog**: `NETPULSE_SYSLOG_ENABLED=true` requiere publicar el puerto
  UDP configurado (por defecto 5514) y añadirlo al compose; no viene
  publicado por defecto.
- **Ping/traceroute** desde el servidor funcionan porque la imagen incluye
  `iputils-ping` y `traceroute`; el contenedor necesita `CAP_NET_RAW`
  (Docker lo da por defecto; no ejecute con `cap_drop: ALL` sin revisarlo).
- **Monitoring**: Prometheus/Loki no publican puertos; para usarlos desde
  fuera, túnel SSH o añada un override local (no lo commitee).
- La imagen pesa ~230 MB comprimidos (~775 MB en disco local con capas);
  la mayor parte son dependencias de red (napalm, nornir, netmiko, boto3).
- En Apple Silicon/ARM se construye nativa; en un servidor amd64 conviene
  construir allí o usar `buildx` (este repo no fija plataforma).

---

## 8. Opción B: systemd sin Docker

Para hosts donde no se permite Docker. Instale la app en `/opt/netpulse`,
cree el usuario `netpulse`, su venv y los directorios de estado:

```bash
sudo useradd --system --home /opt/netpulse --shell /usr/sbin/nologin netpulse
sudo rsync -a --exclude .venv --exclude .git ./ /opt/netpulse/
sudo python3.12 -m venv /opt/netpulse/.venv
sudo /opt/netpulse/.venv/bin/pip install -r /opt/netpulse/requirements.txt
sudo install -d -o netpulse -g netpulse \
    /opt/netpulse/{config,runtime,audit,backups,reports}
sudo install -d -m 0750 /etc/netpulse
sudo install -m 0640 -o root -g netpulse .env /etc/netpulse/netpulse.env
sudo cp deploy/netpulse-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now netpulse-api
```

El unit aplica `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`,
`NoNewPrivileges` y `ReadWritePaths` solo para
`config/runtime/audit/backups/reports`. Escucha en `0.0.0.0:8082`: ponga
Caddy/nginx delante para TLS (o cambie a `--host 127.0.0.1` si el proxy es
local). No hay healthcheck de Docker; use `systemctl status` o
`curl http://127.0.0.1:8082/api/health`.

---

## 9. Diagnóstico rápido

```bash
# ¿La API responde por dentro?
docker compose -f deploy/docker-compose.prod.yml exec api \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8082/api/health').read().decode())"

# ¿Caddy obtuvo certificado?
docker compose -f deploy/docker-compose.prod.yml logs caddy | grep -i certificate

# ¿El contenedor es realmente no-root y read-only?
docker inspect --format '{{.Config.User}}' netpulse-api
docker exec netpulse-api touch /app/x   # debe fallar: Read-only file system

# ¿Escribe auditoría en el host?
ls -l audit/audit.db
sqlite3 audit/audit.db 'select count(*) from audit;'
```

Si la API no arranca, revise en este orden: `.env` ausente o sin
`NETPULSE_JWT_SECRET`; permisos de `config/` para uid 10001; puertos
80/443 ya ocupados; y `docker compose ... logs api`.
