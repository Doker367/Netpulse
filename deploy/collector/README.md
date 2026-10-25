# Colector remoto de NetPulse en Docker (un contenedor por site)

Este directorio empaqueta `python -m app.remote_collector` como imagen OCI y
ofrece un `docker compose` listo para un nodo Linux de site. El colector mide
latencia/disponibilidad **desde el site** y envía las muestras al NetPulse
central por HTTPS; si el enlace cae, sigue midiendo contra un buffer SQLite
local y sincroniza al recuperar.

Archivos:

| Archivo | Para qué |
|---|---|
| `Dockerfile` | Imagen `python:3.12-slim`, usuario uid/gid 10002, `iputils-ping`, solo `app/remote_collector` + `probe_svc`. |
| `docker-compose.yml` | `network_mode: host`, `CAP_NET_RAW`, rootfs de solo lectura, volumen del buffer, config/env montados, logs rotados. |
| `collector.yaml.example` | Config del site comentada; copiar a `/etc/netpulse/collector.yaml`. |
| `README.md` | Este documento. |

> La unit systemd sigue disponible en `deploy/netpulse-collector.service` y
> **no se modifica**. Es la alternativa para sites sin Docker; la comparación
> honesta está al final.

---

## 1. Qué hace la sonda (y por qué la imagen instala `ping`)

Verificado en el código, no supuesto:

- `app/services/collectors/probe_svc.py` usa **dos mecanismos**:
  - `ssh` y `telnet`: `socket.create_connection(host, port)`, es decir, TCP.
  - `snmp` (o `port: 161`, o `driver: snmp`): `_icmp_probe()` ejecuta el
    binario **`ping`** del sistema (`subprocess.run`). No hay sockets raw en
    Python.
- Por eso la imagen instala `iputils-ping` y el contenedor conserva
  **`CAP_NET_RAW`** (y ninguna otra capability): así `ping` funciona como
  uid 10002 sin setuid root, incluido el fallback de iputils a socket raw si
  el kernel no permite ICMP datagram para ese gid.
  Matiz verificado: en Docker con `net.ipv4.ping_group_range` abierto (valor
  habitual por defecto en contenedores) `ping` también funciona sin esa
  capability; se mantiene `CAP_NET_RAW` para hosts endurecidos o runtimes que
  restrinjan ICMP datagram, donde el fallback raw es imprescindible.
- Un fallo de `ping` (ausente, sin permiso) se reporta como
  `ok=false, reachable=null` («no se pudo medir»), nunca como «caído».
- La imagen copia también `app/services/collectors/probe_svc.py` y los
  `__init__.py` mínimos: el colector lo importa. No se copia el resto del
  backend central.

El colector **no escucha en ningún puerto**. Solo abre conexiones salientes:

| Destino | Protocolo | Uso |
|---|---|---|
| Central NetPulse | TCP 443 (HTTPS) | `whoami`, `ingest` |
| Equipos ssh/telnet | TCP 22 / 23 / puerto configurado | handshake TCP |
| Equipos snmp | ICMP echo | disponibilidad + RTT |

---

## 2. Requisitos del nodo

- Linux con Docker Engine + plugin Compose v2 (`docker compose`).
- Salida al central por HTTPS (443/tcp) y a los equipos del site (22/23/tcp,
  ICMP echo).
- Reloj sincronizado (NTP/chrony). El `ts` de cada muestra es la hora de
  **medición** del site; el central marca `clock_skew` si está desviado.
- `sudo` para preparar `/etc/netpulse` y arrancar el contenedor (el archivo
  de entorno con la clave es 0600 root).

No hace falta abrir puertos entrantes ni en el host ni en el firewall del site.

---

## 3. Crear la API key del colector (en el central)

La clave es de tipo `collector`, va ligada a un `site_id` + `collector_id` y
**no tiene rol RBAC**: solo puede ingerir y consultar su propio `whoami`.

Desde la UI (admin → Seguridad / API keys), o por API con un JWT de admin:

```bash
curl -sS -X POST https://noc.example.net/api/security/api-keys \
  -H "Authorization: Bearer $NETPULSE_ADMIN_JWT" \
  -H 'Content-Type: application/json' \
  -d '{"name":"collector-lima-01","kind":"collector",
       "site_id":"site-lima","collector_id":"collector-lima-01"}'
```

- El endpoint exige **rol admin** (JWT de usuario). Una API key no puede
  emitir otra API key.
- La respuesta incluye `"key": "np_..."` **una sola vez**. Guárdala en el
  gestor de secretos del site; no la pegues en tickets ni en el YAML.
- Revocación: `DELETE /api/security/api-keys/{id}` con JWT admin. El colector
  recibirá 401 en el siguiente sync, conservará las muestras y reintentará.
- `site_id` y `collector_id` del YAML deben coincidir **exactamente** con los
  de la clave; `--check` falla con un mensaje explícito si no.

---

## 4. Instalación en el site

### 4.1 Obtener el código e imagen

```bash
git clone https://github.com/netpulse-noc/netpulse-noc.git
cd netpulse-noc
git checkout <tag-o-commit-verificado>
docker compose -f deploy/collector/docker-compose.yml build
docker images netpulse-collector:0.1.0
```

El contexto de build es la **raíz del repo** (`../..` desde este directorio).
La imagen final parte de `python:3.12-slim` + `iputils-ping` + las dos
dependencias de `requirements-collector.txt` (`httpx`, `PyYAML`).

### 4.2 Preparar `/etc/netpulse`

El YAML **no** contiene secretos, pero se monta en un contenedor que corre
como uid 10002: debe poder leerlo. La clave va aparte.

```bash
sudo install -d -m 0755 -o root -g root /etc/netpulse
sudo install -m 0600 -o 10002 -g 10002 \
     deploy/collector/collector.yaml.example /etc/netpulse/collector.yaml
sudo install -m 0600 -o root -g root /dev/null /etc/netpulse/collector.env
```

Editar `/etc/netpulse/collector.yaml` (site/collector/central/devices reales) y
escribir la clave en el archivo de entorno:

```bash
sudo sh -c 'umask 077; printf "NETPULSE_COLLECTOR_API_KEY=%s\n" "np_..." \
  > /etc/netpulse/collector.env'
sudo chmod 0600 /etc/netpulse/collector.env
sudo chown root:root /etc/netpulse/collector.env
```

Permisos resultantes:

| Archivo | Modo | Dueño | Motivo |
|---|---|---|---|
| `/etc/netpulse/collector.yaml` | `0600` | `10002:10002` | lo lee el proceso sin privilegios dentro del contenedor |
| `/etc/netpulse/collector.env` | `0600` | `root:root` | lo lee `docker compose` (ejecutado con sudo) y lo inyecta como entorno |

> **Nota de seguridad:** `env_file` pasa la clave como variable de entorno del
> contenedor; cualquiera con acceso al daemon Docker puede verla con
> `docker inspect`. Si tu política lo prohíbe, usa la alternativa
> `central.api_key_file`: monta el archivo en solo-lectura (p. ej.
> `/etc/netpulse/collector.key`, `0600` uid 10002) y deja `api_key_env` sin
> valor. Requiere añadir ese bind mount al compose.

### 4.3 Preflight: `--check` ANTES de `up -d`

```bash
docker compose -f deploy/collector/docker-compose.yml run --rm collector \
  --config /etc/netpulse/collector.yaml --check
```

Debe devolver exit 0 y un JSON con `"ok": true`, `site_id` y `collector_id`
del `whoami`. Códigos de salida:

| Exit | Significado |
|---|---|
| 0 | config válida + clave válida + IDs coinciden |
| 1 | red/TLS/401/403 o IDs que no coinciden con la clave |
| 2 | YAML inválido, archivo ausente o falta la API key |

También puedes validar la configuración del compose sin arrancar nada:

```bash
docker compose -f deploy/collector/docker-compose.yml config
```

### 4.4 Arrancar

```bash
sudo docker compose -f deploy/collector/docker-compose.yml up -d
sudo docker compose -f deploy/collector/docker-compose.yml ps
sudo docker compose -f deploy/collector/docker-compose.yml logs -f --tail=50
```

Esperado en logs: `[probe] inicio ... devices=N interval_s=60`,
`[probe] cycle ... inserted=...` y `[sync] batch accepted=...`.
`docker compose ps` debe mostrar `healthy` tras el primer ciclo (start period
de 90 s).

Actualizar en el futuro:

```bash
git fetch && git checkout <nuevo-tag>
docker compose -f deploy/collector/docker-compose.yml build
docker compose -f deploy/collector/docker-compose.yml up -d
# rollback: volver al tag anterior, build y up -d; el volumen del buffer se conserva
```

---

## 5. Estado y diagnóstico

### 5.1 `--status` (estado local, sin red)

```bash
docker compose -f deploy/collector/docker-compose.yml exec collector \
  python -m app.remote_collector --config /etc/netpulse/collector.yaml --status
```

Devuelve `status.json` (junto al buffer) con `probe.last_cycle_at`, contadores
`reachable/unreachable/unverifiable`, `buffer.pending`, `sync.last_error`,
`consecutive_failures`, `next_attempt_at`, etc. El CLI añade
`state: fresh|stale` (`stale` si `updated_at` supera `2 × interval_s + 30 s`).

Salidas: `0` estado legible; `3` aún no hay `status.json` (el daemon no ha
completado el primer ciclo o el buffer no es accesible); `2` config inválida;
`1` error de lectura.

### 5.2 Healthcheck del contenedor

```bash
docker inspect --format '{{.State.Health.Status}}' netpulse-collector
docker inspect --format '{{json .State.Health}}' netpulse-collector | python3 -m json.tool
```

El healthcheck ejecuta el mismo `--status` cada 60 s con `start-period: 90 s`.
Durante el arranque, un exit 3 («todavía no hay estado») **no** cuenta como
fallo y no marca el contenedor unhealthy. Docker **no reinicia** contenedores
unhealthy por sí solo: el healthcheck es informativo. Si el proceso muere,
`restart: unless-stopped` lo reinicia.

### 5.3 Comprobar ICMP dentro del contenedor

```bash
docker compose -f deploy/collector/docker-compose.yml exec collector \
  ping -c 1 -W 1 127.0.0.1
```

Debe responder como uid 10002 (sin root). Para validar la ruta real a un
equipo SNMP del site:

```bash
docker compose -f deploy/collector/docker-compose.yml exec collector \
  ping -c 3 -W 2 10.10.0.1
```

Sin `CAP_NET_RAW` verías `ping: socket: Operation not permitted` en hosts
donde `net.ipv4.ping_group_range` no cubre el gid 10002 (el colector
reportaría entonces `sin permiso ICMP`: `ok=false`, no «caído»). En Docker
con el `ping_group_range` abierto por defecto, el ICMP datagram funciona
incluso sin la capability; no la quites si el host está endurecido.

### 5.4 Ver el site en el central

Con un JWT de viewer/admin:

```bash
curl -sS https://noc.example.net/api/remote/sites \
  -H "Authorization: Bearer $NETPULSE_ADMIN_JWT" | python3 -m json.tool
```

Debe aparecer el `site_id` con `contact: fresh` tras el primer `ingest`. Los
endpoints `/api/remote/sites/{site}/devices` y `.../history` muestran las
series. Una clave de colector **no** puede leer estos endpoints (solo
`whoami` e `ingest`).

---

## 6. Copia de seguridad y restauración del buffer

El buffer es la única fuente de las muestras aún no entregadas al central.
Vive en el volumen nombrado **`netpulse-collector-buffer`** (nombre fijo
declarado en el compose), montado en `/var/lib/netpulse-collector`.

### 6.1 Backup en frío (recomendado)

```bash
sudo docker compose -f deploy/collector/docker-compose.yml stop

sudo mkdir -p /var/backups/netpulse-collector
sudo docker run --rm \
  -v netpulse-collector-buffer:/data:ro \
  -v /var/backups/netpulse-collector:/backup \
  --entrypoint tar netpulse-collector:0.1.0 \
  czf /backup/collector-$(date +%F-%H%M).tar.gz -C /data .
```

Incluye `buffer.sqlite3`, su WAL/SHM y `status.json`. Con el contenedor parado
el SQLite es consistente. Vuelve a arrancar con `up -d`.

### 6.2 Backup en caliente (API de backup de SQLite)

```bash
docker compose -f deploy/collector/docker-compose.yml exec -T collector \
  python - <<'PY'
import sqlite3
src = sqlite3.connect("/var/lib/netpulse-collector/buffer.sqlite3")
dst = sqlite3.connect("/var/lib/netpulse-collector/buffer.backup.sqlite3")
src.backup(dst)
dst.close(); src.close()
PY
docker cp netpulse-collector:/var/lib/netpulse-collector/buffer.backup.sqlite3 \
  ./buffer-$(date +%F-%H%M).sqlite3
docker compose -f deploy/collector/docker-compose.yml exec -T collector \
  rm -f /var/lib/netpulse-collector/buffer.backup.sqlite3
```

No copies el `.sqlite3` «a pelo» con `docker cp` mientras el daemon escribe:
puedes llevarte una página a medias. El backup online de SQLite es seguro.

### 6.3 Restaurar

```bash
sudo docker compose -f deploy/collector/docker-compose.yml stop
sudo docker run --rm \
  -v netpulse-collector-buffer:/data \
  -v /var/backups/netpulse-collector:/backup:ro \
  --entrypoint sh netpulse-collector:0.1.0 -c \
  'rm -f /data/buffer.sqlite3 /data/buffer.sqlite3-wal /data/buffer.sqlite3-shm /data/status.json \
   && tar xzf /backup/collector-2026-10-25-1200.tar.gz -C /data \
   && chown -R 10002:10002 /data'
sudo docker compose -f deploy/collector/docker-compose.yml up -d
```

Tras restaurar, `--status` mostrará el `buffer_epoch` original. El central trata
un `buffer_epoch` distinto como buffer nuevo y no inventa continuidad; si
restauras una copia antigua, las muestras ya sincronizadas no se duplican
(idempotencia por `site, device, ts, metric`).

---

## 7. Qué pasa si el central cae o el enlace se corta

1. El hilo de sonda **sigue midiendo**; cada ciclo inserta en el buffer local.
2. El hilo de sync falla y aplica backoff exponencial con jitter (base 5 s,
   máximo 300 s por defecto). Sigue enviando heartbeats cuando toca.
3. El buffer retiene hasta **`max_age_hours: 72`** (y `max_rows: 500000`).
   Al llegar a la cota, descarta primero lo más viejo y lo declara:
   `dropped_total` y `gap_since` suben; el central muestra el hueco, no lo
   rellena.
4. Al volver el enlace, sincroniza por lotes ordenados (200 muestras por POST,
   máximo 500). El `ingest` es idempotente: un reenvío cuenta como duplicado,
   no como dato nuevo.
5. El buffer sobrevive reinicios y recreaciones del contenedor porque está en
   el volumen; **no** en `/tmp` ni en el rootfs `read_only`.

Dimensionamiento orientativo: 20 devices × 1 ciclo/60 s × ~2 filas ≈ 40
filas/min ≈ 57 600 filas/día; 72 h ≈ 173 000 filas, muy por debajo de
`max_rows: 500000`. Con más devices o cadencia más rápida, revisa ambos topes.

Si el disco del volumen baja de `min_free_mb` (200 MB), la sonda se pausa y lo
declara (`paused_reason: disk_low`); se reanuda con 50 MB de histéresis.

---

## 8. Detalles de seguridad del compose

| Medida | Efecto |
|---|---|
| `network_mode: host` | el contenedor usa la pila de red del host: ICMP y rutas/VPN/VLAN del site funcionan sin NAT ni publicar puertos |
| `cap_drop: ALL` + `cap_add: NET_RAW` | solo la capability que necesita `ping`; el proceso no es root |
| `read_only: true` + `tmpfs /tmp` | rootfs inmutable; único estado persistente en el volumen |
| `no-new-privileges` | impide ganar privilegios vía setuid |
| sin `ports:` | el colector no escucha; no hay superficie entrante |
| config `:ro` | el contenedor no puede alterar su propia config |
| logs `json-file` 10 MB × 3 | evita llenar el disco del site |
| `init: true` | recolecta zombies y reenvía señales |

`network_mode: host` reduce el aislamiento de red: el contenedor ve las mismas
interfaces que el host. Es el precio de sondear el site real. Si tu política
exige bridge, tendrás que garantizar rutas y NAT de retorno, y aun así ICMP y
descubrimiento L2 pueden no funcionar; no es la configuración recomendada.

---

## 9. Docker vs systemd (comparación honesta)

| Criterio | Docker (este directorio) | systemd (`deploy/netpulse-collector.service`) |
|---|---|---|
| Requisito | Docker Engine + Compose | Python 3.11+ y venv en el nodo |
| Aislamiento | proceso no-root, caps mínimas, rootfs ro | proceso no-root, `ProtectSystem=strict`, `PrivateTmp` |
| Red | `host` (sin aislamiento de red) | red del host por definición |
| ICMP | `iputils-ping` + `CAP_NET_RAW` | `ping` del host; `AmbientCapabilities=CAP_NET_RAW` solo si se sustituye |
| Actualización | imagen inmutable + `up -d`; rollback a tag anterior | `git pull` + reinstalar deps + `systemctl restart` |
| Estado | volumen nombrado; backup con `docker run` | `/var/lib/netpulse-collector` directo |
| Dependencias del host | solo Docker | Python, venv, `ping` |
| Superficie extra | daemon Docker (root) | ninguna |
| Arranque tras reboot | `restart: unless-stopped` + Docker habilitado | `systemctl enable` |
| Diagnóstico | `docker compose logs`, healthcheck | `journalctl -u netpulse-collector`, `--status` |

**Cuándo usar cada una:**

- **Site con Docker ya operado** (y equipo que lo mantiene): compose con
  `network_mode: host`. Ventaja: imagen reproducible y rollback trivial.
- **Site sin Docker, o donde no se quiere un daemon root extra**: systemd. Es
  la vía con menos piezas y la unit ya existe; no requiere cambios.
- **Nunca las dos a la vez en el mismo site**: duplicaría muestras y el
  contrato asume un colector activo por site (fase 1).

---

## 10. Problemas típicos

| Síntoma | Causa probable | Acción |
|---|---|---|
| `--check` exit 2 «falta la API key» | `env_file` ausente o clave vacía | crear `/etc/netpulse/collector.env` 0600 y reintentar |
| `--check` exit 1 «la clave pertenece a site=…» | IDs del YAML ≠ IDs de la key | corregir `site_id`/`collector_id` o emitir otra key |
| `401 API key inválida o revocada` en logs | key revocada/expirada | emitir key nueva y actualizar el env file; reiniciar |
| `ping: socket: Operation not permitted` | falta `CAP_NET_RAW` | no quitar `cap_add`; comprobar `docker inspect` |
| Contenedor `unhealthy` pero proceso vivo | aún no hay `status.json` o el buffer no abre | `logs` + `--status`; revisar permisos del volumen |
| `config inválida: ... claves desconocidas` | typo en el YAML | corregir; el colector falla cerrado a propósito |
| `up` falla «bind source path does not exist» | falta `/etc/netpulse/collector.yaml` | crearlo con el ejemplo (se usa `create_host_path: false` a propósito) |
| Muestras con `clock_skew` en el central | reloj del site desviado | habilitar NTP/chrony en el nodo |

---

## 11. Pendientes conocidos (no resueltos aquí)

- **TLS real con CA privada**: el compose no monta `ca_file` por defecto; si el
  central usa CA interna, añadir el bind mount y `central.ca_file` en el YAML.
  No se validó contra un central HTTPS real en esta corrida.
- **Rotación de API key sin downtime**: el flujo actual es emitir key nueva,
  editar el env file y reiniciar; no hay recarga en caliente de la clave.
- **`api_key_file`**: soportado por el CLI, pero este compose usa `env_file`
  por requisito. Ver la nota de seguridad de la sección 4.2.
- **Docker Desktop/macOS**: `network_mode: host` no equivale al host real y el
  central local se alcanza como `host.docker.internal`; no es un escenario de
  producción, solo de laboratorio.
- **Buffer en bind mount**: se usa un volumen nombrado. Si el site exige una
  ruta del host, sustituir por un bind y hacer `chown 10002:10002` en el host.
