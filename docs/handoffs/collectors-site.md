# Colector distribuido por site — FASE 1 (arquitectura)

Rama: `feat/colector-site` · Base: `main@505afe4` · Fecha: 2026-10-23
Estado: **propuesta de arquitectura, pendiente de visto bueno del orquestador**.
Esta corrida **no escribe código de producción**. Zona tocada: solo este documento.

---

## 0. Resumen ejecutivo

Un proceso autónomo (`python -m app.remote_collector`) corre en un nodo del site
remoto, mide **latencia y disponibilidad** de los dispositivos de ese site y las
empuja al NetPulse central por HTTPS con una API key de servicio. Si el enlace al
centro cae, el colector sigue midiendo y guarda en un buffer SQLite local; al
volver el enlace sincroniza por lotes idempotentes y declara explícitamente el
hueco. El centro guarda esas muestras en un almacén **separado** del poller
central, con la fuente (`site_id`/`collector_id`) en cada fila, y las expone en
endpoints nuevos de solo lectura. El panel actual no se toca en esta ola.

Decisiones clave:

| # | Decisión | Alternativa rechazada | Motivo |
|---|---|---|---|
| D1 | El colector remoto es un **proceso CLI sin API propia** | sidecar con HTTP local | menos superficie, menos puertos, systemd lo gestiona |
| D2 | Buffer local **SQLite WAL** con `pending/synced/dead` | JSONL o memoria | transacciones, idempotencia, poda acotada, sobrevive reinicios |
| D3 | Al llenarse el buffer se **descarta lo más viejo** y se declara el hueco | dejar de medir | el estado actual es lo accionable; el hueco se reporta, no se inventa |
| D4 | El centro guarda en **DB SQLite separada** (`runtime/remote_collector.db`) | reutilizar `metrics_history_svc` o `audit.db` | no mezclar vantages ni fuentes; no contaminar auditoría |
| D5 | **Coexistencia explícita** con el poller central, sin alimentarlo | inyectar muestras remotas en el poller | el poller mide desde el centro; mezclar vantages produce falsos «caído» |
| D6 | Auth server-to-server con **API key `np_` de tipo `collector`**, ligada a `site_id` + `collector_id` | JWT de usuario o clave admin | mínimo privilegio, revocable, no legible por el resto de la API |
| D7 | El `ts` del colector es la hora de la **medición**; `received_at` del centro es la hora de **llegada** | corregir relojes en el servidor | no se reescribe el dato; se marca `clock_skew` |
| D8 | Un colector activo por site en fase 1 | HA / varios colectores | evita duplicados e intercalados; el modelo queda site-scoped para crecer |

---

## 1. Objetivos y alcance de la FASE 1

### 1.1 Objetivo

Que el NOC central pueda responder, con datos reales y procedencia explícita:

- ¿El site remoto está en contacto? ¿desde cuándo no?
- ¿Qué dispositivos de ese site responden al sondeo y con qué latencia?
- Si un dispositivo no responde, ¿es que no responde o que no se pudo medir?
- Si hubo un corte de enlace, ¿qué hueco de datos existe y cuánto se perdió?

### 1.2 Alcance

- Un proceso CLI en el nodo del site, sin colecta en el central.
- Métricas por dispositivo: `reachable`, `latency_ms` y, solo para ICMP,
  `packet_loss_pct` de una sonda.
- Buffer local durable y sincronización por lotes al centro.
- Almacén central nuevo, endpoints de lectura y contrato de UI.
- Empaque systemd (primario) y compose (alternativo).

### 1.3 Fuera de alcance (fase 1)

Sin escritura de configuraciones, sin SSH/CLI a los equipos, sin alertas
generadas en el site, sin SNMP de recursos (CPU/RAM), sin syslog, sin HA, sin
más de un site validado. El detalle está en la sección 11.

---

## 2. Contexto verificado en el código (no inventado)

Estos hechos condicionan el diseño:

1. **Poller central**: `app/services/collectors/metrics_poller.py`
   - `_icmp_ping()` (líneas 51-73) y `_probe_latency()` (76-102): ICMP con
     `subprocess ping` para SNMP; TCP connect para SSH/API.
   - `collect_device()` (140-194) y `poll_all()` (197-231) recorren **todo** el
     inventario y publican snapshot en memoria + gauges Prometheus.
   - `snapshot()` (268-278) alimenta `GET /api/system/devices` y
     `/api/system/overview`.
   - El poller no tiene concepto de site ni de fuente: mide desde el central.
2. **Histórico nativo**: `app/services/metrics_history_svc.py` es un `deque`
   de 1440 muestras persistido en JSON (`config/metrics_history.json`), con
   forma fija `{ts, host, devices}` y **sin** campo de fuente/colector. No es
   apto para muestras remotas (fuente distinta, semántica distinta, escritura
   no transaccional).
3. **Ping manual**: `app/routers/system.py:23-78` ejecuta `ping` desde el
   servidor y devuelve `success`, `rtt_*`, `packet_loss`; es una medición
   puntual del central, no del site.
4. **API keys**: `app/services/security/api_keys_svc.py` guarda solo SHA-256 en
   `config/api_keys.json`, con roles `admin|operator|viewer`; la clave completa
   se muestra una vez. `app/middleware/auth.py:44-59` acepta `Authorization:
   Bearer np_...` y la mapea a `request.state.user` con `sub=apikey:<name>`.
5. **RBAC**: `app/middleware/rbac.py:13-17`, jerarquía `admin > operator >
   viewer`; el 403 usa el texto `"Acceso denegado — se requiere rol '<rol>' o
   superior"`.
6. **Inventario**: `app/services/inventory_svc.py` lee `config/devices.yaml`
   cacheado por mtime; `list_devices()` no expone credenciales. No existe
   `site_id` en el dispositivo.
7. **Salud de dispositivos del panel**: `app/routers/devices.py:78-157` tiene un
   endpoint público `/api/devices/health` con caché de 8 s e histéresis de 2
   fallos, y `_probe_device()` (160-189) con otra implementación de ICMP/TCP.
   Es un fast-path de UI; **no** se reutiliza en el colector para no acoplar
   semánticas.
8. **Stack de monitorización**: `docker-compose.monitoring.yml` levanta
   Prometheus, Alertmanager, Loki, Promtail y Grafana. La API NetPulse corre
   aparte con uvicorn (Makefile: `127.0.0.1:8082`). El colector remoto **no**
   necesita este stack.
9. **Patrones de honestidad/RBAC/audit**:
   - `app/routers/retention.py` + `app/services/retention_svc.py`: `null` =
     «sin medir», `estado:"no disponible: <causa>"`, `503` cuando no se puede
     medir, y nunca un cero inferido.
   - `app/routers/alerts_admin.py`: `viewer` lee, `operator` escribe, `503`
     cuando el upstream no responde, `audit_svc.log_event()` en cada escritura.
   - `app/middleware/audit.py`: registra **todas** las peticiones HTTP
     (excepto `/api/health`, `/docs`, `/redoc`, `/openapi.json`) con
     `username=apikey:<name>` cuando corresponde.
10. **Rate limit global**: `app/core/limiter.py`, 100/min por IP de cliente.
11. **Persistencia de runtime**: `.gitignore` ya ignora `/runtime/`, `*.db`,
    `*.db-wal`, `*.db-shm`. El almacén central puede vivir ahí sin riesgo de
    commit.

---

## 3. Arquitectura general

```
┌────────────────────────── SITE REMOTO ──────────────────────────┐
│                                                                  │
│  systemd: netpulse-collector                                     │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ python -m app.remote_collector --config collector.yaml     │  │
│  │                                                            │  │
│  │  probe loop (cada 60 s)          sync loop (cada 30 s)     │  │
│  │  ┌─────────────────────┐         ┌──────────────────────┐  │  │
│  │  │ inventario YAML     │         │ pending batch (200)  │  │  │
│  │  │ ICMP / TCP          │         │ POST /api/remote/... │  │  │
│  │  └─────────┬───────────┘         └──────────┬───────────┘  │  │
│  │            │ INSERT (WAL)                    │ HTTPS+key    │  │
│  │            ▼                                 ▼              │  │
│  │  ┌──────────────────────────────────────────────────────┐  │  │
│  │  │ buffer.sqlite3  (pending / synced / dead, acotado)   │  │  │
│  │  └──────────────────────────────────────────────────────┘  │  │
│  │  status.json (observabilidad local, sin secretos)          │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
└──────────────────────────────┬───────────────────────────────────┘
                               │ Internet / WAN / VPN (TLS)
                               ▼
┌────────────────────────── CENTRO (NetPulse) ────────────────────┐
│  FastAPI  /api/remote/ingest  (CollectorAuth: np_ kind=collector)│
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ runtime/remote_collector.db (SQLite WAL)                   │  │
│  │  remote_collector  ← heartbeat, buffer, skew               │  │
│  │  remote_sample     ← append-only, UNIQUE(site,dev,ts,met)  │  │
│  │  remote_device_state ← último estado por (site, device)    │  │
│  └────────────────────────────────────────────────────────────┘  │
│  GET /api/remote/sites...  (viewer+)  →  panel (otra ola)        │
│                                                                  │
│  Poller central (metrics_poller) sigue midiendo SOLO desde aquí. │
│  Las dos fuentes no se suman ni se promedian.                    │
└──────────────────────────────────────────────────────────────────┘
```

Separación de responsabilidades:

- El colector **mide y almacena**; no decide alertas ni escribe inventario.
- El centro **valida, deduplica, almacena y expone**; no empuja configuración.
- La UI **lee** y muestra la fuente; no mezcla números de distinto origen.

---

## 4. Proceso remoto

### 4.1 Entrypoint y CLI

```bash
python -m app.remote_collector --config /etc/netpulse/collector.yaml
```

Subcomandos/flags (misma entrada, sin dependencias extra):

| Flag | Comportamiento | Código de salida |
|---|---|---|
| `--config PATH` | obligatorio; YAML validado en arranque | 2 si no existe o es inválido |
| `--check` | valida config, clave y (opcional) `GET /api/remote/whoami`; no mide | 0 ok, 1 error de clave/red |
| `--once` | un ciclo de sonda + un intento de sync; termina | 0 si el ciclo se ejecutó (aunque haya devices no respondan), 1 si falló el buffer |
| `--status` | imprime `status.json` y termina; si está viejo añade `"state":"stale"` | 0 si existe; 3 si no existe |
| `--dry-run` | imprime resultados de una sonda en JSON; no escribe buffer ni hace HTTP | 0 |
| `--log-level` | override de `log.level` | — |

`--check` y `--dry-run` son la vía de validación en el site sin arrancar el
servicio. Ninguno imprime la API key (solo `key_name` y `site_id`).

El proceso normal:

- hilo principal: valida config, abre buffer, arranca hilos y espera `SIGTERM`/`SIGINT`;
- hilo `probe`: ciclo periódico, escritura transaccional en SQLite;
- hilo `sync`: lotes HTTP, backoff, heartbeat;
- al recibir señal: deja de programar ciclos, espera como máximo 10 s a que
  termine el ciclo en curso, hace checkpoint del WAL y sale con 0.

Un error fatal de buffer (DB corrupta, disco sin espacio para abrir WAL) se
registra y **termina el proceso con 1** para que systemd lo reinicie; nunca se
sigue midiendo en memoria como si se estuviera guardando.

### 4.2 Configuración YAML (contrato exacto)

```yaml
schema_version: 1

site_id: site-lima                 # inmutable; debe coincidir con la clave
collector_id: collector-lima-01    # inmutable; debe coincidir con la clave

central:
  base_url: https://noc.example.net
  # La clave NUNCA va en este YAML. Preferencia: variable de entorno.
  api_key_env: NETPULSE_COLLECTOR_API_KEY
  api_key_file: null               # alternativa: ruta 0600, una línea
  verify_tls: true                 # false prohibido en producción
  ca_file: null                    # CA interna si aplica
  timeout_s: 10

probe:
  interval_s: 60                   # 10..3600
  timeout_ms: 1500                 # 100..10000
  workers: 8                       # 1..32
  icmp_count: 1                    # fijo en fase 1; se documenta como 1 sonda

buffer:
  path: /var/lib/netpulse-collector/buffer.sqlite3
  max_rows: 500000                 # cota dura de filas
  max_age_hours: 72                # cota de antigüedad de lo no sincronizado
  synced_retention_hours: 6        # copia local de lo ya sincronizado
  min_free_mb: 200                 # por debajo: se pausa la sonda

sync:
  interval_s: 30                   # cadencia del bucle de sync
  heartbeat_s: 60                  # POST vacío si no hay muestras
  batch_max_samples: 200           # <= 500 (máximo del servidor)
  backoff_base_s: 5
  backoff_max_s: 300

# Una de las dos formas, nunca ambas:
devices:
  - id: sw-access-01
    hostname: 10.10.0.11
    port: 22
    protocol: ssh                  # ssh|telnet|snmp
    driver: ios                    # opcional, solo informativo/log
  - id: fw-edge-01
    hostname: 10.10.0.1
    port: 161
    protocol: snmp

inventory_file: null               # o ruta a un YAML con la misma clave `devices`
```

Reglas de validación (fallo de arranque, mensaje claro, exit 2):

- `schema_version == 1`; claves desconocidas en el YAML son **error** (evita
  typos silenciosos que dejarían equipos sin monitorizar).
- `site_id`/`collector_id`: `^[a-z0-9][a-z0-9._-]{0,63}$`.
- `devices` e `inventory_file` son mutuamente excluyentes; al menos uno debe
  aportar ≥ 1 dispositivo. IDs únicos, `hostname` no vacío, `port` 1..65535,
  `protocol` en el enum.
- `interval_s` 10..3600; `workers` 1..32; `batch_max_samples` 1..500.
- `max_rows` >= 1000; `max_age_hours` >= 1; `synced_retention_hours` >= 1;
  `min_free_mb` >= 10.
- La ruta del buffer debe ser escribible por el usuario del servicio; si no,
  **no** se cae a `/tmp` (se perdería el buffer al reiniciar).
- La API key se resuelve de `api_key_env` o `api_key_file`; si falta, exit 2.
  Nunca se escribe en logs ni en `status.json`.

`inventory_file` es un YAML **sin credenciales**, con la misma forma de
`devices:` (id, hostname, port, protocol, driver). Puede exportarlo el NOC a
mano o generarlo otro proceso; el colector no necesita usuario/contraseña
porque solo hace ICMP/TCP.

### 4.3 Inventario: decisión de fase 1

**Decidimos lista explícita en el YAML del site (o `inventory_file` local) sobre
auto-discovery contra el centro**, porque:

- el inventario central no tiene `site_id` (verificado en
  `inventory_svc.list_devices()`), así que un endpoint de inventario por site
  exigiría cambiar el modelo central antes de tiempo;
- evita que el colector dependa del centro para arrancar (si el enlace está
  caído, debe seguir midiendo);
- la lista es pequeña y auditable; el operador ve exactamente qué se mide.

Un `GET /api/remote/inventory` queda como evolución posterior (sección 12),
no como dependencia de fase 1.

### 4.4 Lógica de sonda: una sola primitiva compartida

**Decidimos extraer la primitiva a `app/services/collectors/probe_svc.py`
(stdlib puro) y que la usen tanto `metrics_poller` como el colector**, sobre
duplicar la lógica. El contrato:

```python
@dataclass(frozen=True)
class ProbeResult:
    ok: bool                     # la sonda se ejecutó y su resultado es interpretable
    reachable: bool | None       # True/False; None si ok=False
    latency_ms: float | None
    packet_loss_pct: float | None  # solo ICMP; None en TCP
    probe_kind: str              # "icmp" | "tcp"
    error: str | None            # texto corto, <= 200 chars
```

Reglas semánticas (clave para la honestidad):

| Caso | `ok` | `reachable` | `latency_ms` | `error` |
|---|---|---|---|---|
| ICMP responde | true | true | rtt | null |
| ICMP no responde / timeout | true | false | null | `"sin respuesta ICMP"` / `"timeout ICMP"` |
| `ping` no existe / sin permiso | false | null | null | `"ping no disponible"` / `"sin permiso ICMP"` |
| TCP connect OK | true | true | rtt | null |
| TCP refused | true | false | null | `"conexión rechazada"` |
| TCP timeout | true | false | null | `"timeout TCP"` |
| DNS inválido | false | null | null | `"DNS: <detalle>"` |
| Host vacío / config inválida | false | null | null | `"host vacío"` |

`ok=true, reachable=false` significa **medimos y no respondió**; no prueba
absoluta de que el equipo esté caído (puede fallar el path desde el site o el
propio colector). La UI debe decir «no responde al sondeo», no «caído».
`ok=false` significa **no se pudo medir** y jamás se cuenta como offline ni
como latencia 0.

`latency_ms` es el RTT de **la sonda** (ICMP o `connect` TCP), no la latencia
de gestión del equipo ni la de un flujo de datos. `packet_loss_pct` solo existe
para ICMP y corresponde a **una** sonda (0.0 o 100.0). No es una tasa agregada;
la UI no debe rotularlo como «pérdida media».

Compatibilidad con el poller central: `metrics_poller._probe_latency()` se
convierte en un adaptador que conserva su contrato actual
`(latency_ms, loss_pct)` (para TCP, `loss=0` si conecta y `100` si no), de modo
que `/api/system/devices` no cambia de comportamiento en PR-1. La semántica
rica (`ok`, `probe_kind`, error) es la que viaja al centro desde el colector.

`app/routers/devices.py:_probe_device()` (async, con histéresis) **no se toca**
en fase 1; se documenta como deuda: hay dos implementaciones de sonda de UI y
una de colecta. Unificarlas exige decidir si la histéresis aplica al colector
(no, porque el colector debe reportar cada medición, no un estado suavizado).

### 4.5 Ciclo de sonda

Por cada ciclo:

1. Lee la lista de dispositivos ya validada (en memoria; el YAML se recarga
   solo al arrancar o con `SIGHUP`, que valida y aplica en caliente; si el
   nuevo YAML es inválido, conserva el anterior y registra el error).
2. Ejecuta sondas en un `ThreadPoolExecutor(workers)` con timeout acotado.
3. Un solo `INSERT` transaccional por ciclo con todas las filas y un
   `probe_batch` (inicio/fin, nº devices, nº errores).
4. Actualiza `status.json` de forma atómica (tmp + rename).
5. Cada N ciclos (10) o si `pending > 80% max_rows`, ejecuta la poda (6.3).

Un equipo lento no bloquea al resto: el timeout es por sonda y el pool es
acotado. El ciclo nunca se solapa consigo mismo (el hilo espera al anterior).

---

## 5. Buffer local (SQLite)

### 5.1 Esquema exacto

**Decidimos SQLite WAL sobre JSONL o memoria porque** el buffer necesita
transacciones (insertar el ciclo completo o nada), una clave única local,
poda acotada y supervivencia a reinicios; un JSONL reescrito en cada ciclo
corrompe o pierde datos y no permite marcar filas individuales.

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS probe_batch (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,          -- UTC ISO-8601, µs
    finished_at   TEXT NOT NULL,
    device_count  INTEGER NOT NULL,
    error_count   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sample (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id        INTEGER NOT NULL,     -- referencia lógica, sin FK (la poda no cascadea)
    ts              TEXT NOT NULL,        -- hora de MEDICIÓN del colector, UTC
    device_id       TEXT NOT NULL,
    metric          TEXT NOT NULL
                    CHECK (metric IN ('reachable','latency_ms','packet_loss_pct')),
    value_num       REAL,                 -- latency_ms / packet_loss_pct
    value_bool      INTEGER               -- reachable: 0/1/NULL
                    CHECK (value_bool IN (0,1) OR value_bool IS NULL),
    ok              INTEGER NOT NULL
                    CHECK (ok IN (0,1)),
    probe_kind      TEXT NOT NULL
                    CHECK (probe_kind IN ('icmp','tcp')),
    error           TEXT,                 -- <= 200 chars, solo si ok=0 o reachable=0
    sync_state      TEXT NOT NULL DEFAULT 'pending'
                    CHECK (sync_state IN ('pending','synced','dead')),
    sync_attempts   INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    synced_at       TEXT,
    dead_reason     TEXT,
    UNIQUE (device_id, ts, metric)
);

CREATE INDEX IF NOT EXISTS idx_sample_pending
    ON sample (sync_state, id);
CREATE INDEX IF NOT EXISTS idx_sample_ts
    ON sample (ts);

CREATE TABLE IF NOT EXISTS collector_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

Claves de `collector_state`:

| Clave | Contenido |
|---|---|
| `schema_version` | `1` |
| `buffer_epoch` | UUID generado al crear la DB; cambia si se borra/recrea |
| `created_at` | UTC ISO-8601 |
| `dropped_total` | contador monótono de muestras descartadas por política |
| `dropped_last_at` | última vez que se descartó |
| `gap_since` | `ts` de la muestra más vieja descartada; **no se borra solo** |
| `last_success_at` | último sync 200 |
| `last_error` / `last_error_at` | último error de sync (texto corto) |
| `paused_reason` | `null` o `disk_low` |

Cada sonda genera **1 fila `reachable` siempre** y, si corresponde, filas
`latency_ms` y `packet_loss_pct` con el **mismo `ts`**:

- `ok=true, reachable=true`: `reachable=1`; `latency_ms` si se midió;
  `packet_loss_pct` solo si `probe_kind=icmp` (0.0).
- `ok=true, reachable=false`: `reachable=0`; `packet_loss_pct=100.0` solo si
  ICMP; sin `latency_ms`.
- `ok=false`: `reachable=NULL`, `ok=0`, `error` obligatorio; sin filas de
  latencia/loss.

La clave única local `(device_id, ts, metric)` evita duplicar si un ciclo se
reintenta. El `ts` se genera con `datetime.now(timezone.utc)` con microsegundos.

### 5.2 Qué se guarda y qué no

Se guarda: `ts`, `device_id`, `metric`, valor numérico o booleano, `ok`,
`probe_kind`, `error`, estado de sync e intentos. No se guarda: credenciales,
configuraciones, payloads de equipos, ni la API key. El buffer no es una copia
del inventario; solo mediciones.

### 5.3 Política de crecimiento (decisión y costo)

**Decidimos descartar lo más viejo cuando se agota la cota, y declarar el hueco
(`dropped_total`, `gap_since`, `buffer_epoch`) sobre dejar de medir.**

Costo explícito:

- durante un corte largo se pierde historia antigua del site;
- se conserva el estado **reciente**, que es lo accionable cuando vuelve el
  enlace;
- el centro nunca recibe una serie continua falsa: recibe el conteo de
  descartes y el instante del hueco.

La alternativa «dejar de medir al llenar» preservaría una historia completa
pero dejaría al site ciego justo cuando más importa; se rechaza.

Orden de poda (transacción `BEGIN IMMEDIATE`, cada 10 ciclos o al 80 %):

1. Borrar `synced` con `ts < now - synced_retention_hours` (6 h por defecto).
2. Borrar `probe_batch` con `finished_at < now - max_age_hours`.
3. Borrar `pending`/`dead` con `ts < now - max_age_hours` aunque se esté por
   debajo de `max_rows`; sumar a `dropped_total` y fijar `gap_since` (el más
   antiguo que ya hubiera) al `ts` más viejo borrado. Es la cota de antigüedad
   de lo no sincronizado.
4. Si `COUNT(*) > max_rows`: borrar primero `synced`; si aún sobra, borrar las
   `pending`/`dead` más viejas hasta `max_rows`, sumar a `dropped_total`, fijar
   `gap_since` al `ts` más viejo borrado (si ya existía, conservar el más
   antiguo) y registrar `dropped_last_at`.
5. `PRAGMA wal_checkpoint(PASSIVE)` tras la poda (no bloqueante).

Guardas:

- Antes de cada ciclo, `shutil.disk_usage(buffer_dir).free`; si
  `< min_free_mb`, se pone `paused_reason="disk_low"` y **se pausa la sonda**
  (el sync sigue). Se reanuda cuando `free >= min_free_mb + 50 MB`.
- Si la DB no se puede abrir/escribir: log crítico y exit 1 (no se simula
  buffer).
- La poda **nunca** borra filas `pending` si aún caben en `max_rows` y no
  superan `max_age_hours`.

Dimensionamiento (estimación, marcar como tal): hasta 3 filas por device y
ciclo (2 en TCP, 3 en ICMP). Con 100 devices y 60 s:
`100 × 3 × 1440 ≈ 432 000 filas/día`; a ~100-150 B por fila con índices,
~45-65 MB/día. `max_rows=500 000` se alcanza en ~28 h a ese ritmo (no en 72 h);
`max_age_hours=72` solo cabe con ~38 devices o menos. La cota efectiva es la
que ocurra primero. Con 20 devices, ~13 MB/día y 72 h ≈ 260 000 filas (~35 MB).
Son estimaciones de diseño; PR-1 debe medirlo en el test de capacidad.

### 5.4 `status.json` (observabilidad local)

Se escribe atómicamente en el mismo directorio del buffer, sin secretos:

```json
{
  "schema_version": 1,
  "site_id": "site-lima",
  "collector_id": "collector-lima-01",
  "pid": 1234,
  "started_at": "2026-10-23T17:00:00.000000+00:00",
  "updated_at": "2026-10-23T18:00:00.000000+00:00",
  "probe": {
    "interval_s": 60,
    "last_cycle_at": "2026-10-23T17:59:30.000000+00:00",
    "last_cycle_duration_ms": 420,
    "devices": 12,
    "reachable": 10,
    "unreachable": 1,
    "unverifiable": 1
  },
  "buffer": {
    "path": "/var/lib/netpulse-collector/buffer.sqlite3",
    "buffer_epoch": "5b0c...",
    "pending": 120,
    "oldest_pending_ts": "2026-10-23T17:40:00.000000+00:00",
    "dropped_total": 4,
    "gap_since": "2026-10-23T17:40:00.000000+00:00",
    "paused_reason": null,
    "free_mb": 8421
  },
  "sync": {
    "last_success_at": "2026-10-23T17:58:00.000000+00:00",
    "last_error": null,
    "last_error_at": null,
    "consecutive_failures": 0,
    "next_attempt_at": "2026-10-23T17:58:30.000000+00:00",
    "auth_state": "ok"
  },
  "central": {
    "base_url": "https://noc.example.net",
    "last_status_code": 200
  }
}
```

`--status` no convierte un archivo ausente en «sano»: si no existe, exit 3 y
mensaje `"sin status: el colector no ha arrancado o el buffer no es accesible"`.
Si existe pero `updated_at` es más viejo que `2 × interval_s + 30 s`, imprime
`"state":"stale"` además del contenido; no lo interpreta como activo.

---

## 6. Sincronización con el centro

### 6.1 Auth server-to-server

**Decidimos extender las API keys existentes sobre crear una tabla de tokens
de colector separada porque** ya hay hash, revocación, listado admin y
auditoría probados; duplicar el almacén añadiría otra pieza que rotar y
olvidar. La extensión queda **fuera de los roles de usuario**:

- La clave de colector se guarda en el mismo `config/api_keys.json` con:
  `kind: "collector"`, `site_id`, `collector_id`, `role: null` (no es un rol
  RBAC), `revoked`, `key_hash` (sin cambios).
- `POST /api/security/api-keys` admite `kind`, `site_id`, `collector_id`; para
  `kind="collector"` exige ambos IDs y **rechaza** `role` explícito (422) para
  no crear una clave con doble semántica. Sigue siendo solo `admin`.
- `GET /api/security/api-keys` devuelve `kind`, `site_id`, `collector_id`
  (nunca el hash ni la clave).
- `JWTBearer` rechaza con 403 cualquier clave `kind="collector"` en rutas
  normales; así una clave robada **no puede leer** el resto de la API.
- El router de ingesta usa una dependencia propia `CollectorAuth`, no
  `JWTBearer`:
  1. exige `Authorization: Bearer np_...`;
  2. `api_keys_svc.validate_api_key()`;
  3. exige `kind=="collector"`, `site_id` y `collector_id` no vacíos;
  4. deja `request.state.collector = {name, site_id, collector_id, key_id}`
     y, para que el middleware de auditoría registre `apikey:<name>`, fija
     también `request.state.user = {"sub": "apikey:<name>", "role": None,
     "api_key_id": ...}` sin conceder RBAC;
  5. si la clave está revocada o no existe: 401; si es de usuario: 403.

`site_id` y `collector_id` **no viajan en el body**: se derivan de la clave.
Así un colector comprometido no puede escribir en nombre de otro site. El
colector verifica al arrancar con `GET /api/remote/whoami` que la clave
corresponde a su `site_id`/`collector_id` configurados.

Contrato de creación (admin):

```http
POST /api/security/api-keys

{"name":"collector-lima-01","kind":"collector",
 "site_id":"site-lima","collector_id":"collector-lima-01"}

201 Created

{"key":"np_...","id":"...","name":"collector-lima-01",
 "kind":"collector","site_id":"site-lima","collector_id":"collector-lima-01",
 "created_at":"2026-10-23T17:00:00.000000+00:00"}
```

La clave completa se devuelve **una sola vez**. El registro en
`config/api_keys.json` queda con `role:null`, `kind:"collector"`, `site_id`,
`collector_id`, `key_hash` y `revoked:false`. Las claves de usuario siguen
funcionando con el payload anterior `{"name","role"}` sin cambios; los
registros antiguos sin `kind` se interpretan como `kind:"user"` (retrocompat).

Rotación: crear la clave nueva, actualizar `collector.env`, `systemctl restart
netpulse-collector`, verificar con `--check`, y solo entonces revocar la vieja.
`SIGHUP` recarga inventario/intervalos, pero **no** la clave (vive en el
entorno del proceso); para rotar hay que reiniciar.

Endurecimiento incluido en PR-2: comparar hashes con `hmac.compare_digest` en
`api_keys_svc.validate_api_key()` (hoy usa `==`), sin cambiar el formato.

### 6.2 Contrato HTTP de ingesta

```
POST /api/remote/ingest
Authorization: Bearer np_...
Content-Type: application/json
```

Body exacto:

```json
{
  "schema_version": 1,
  "collector_version": "0.1.0",
  "probe_interval_s": 60,
  "sync_batch_id": "c0a8012e-7f3b-4a1e-9d6a-2b7f0e1c9a44",
  "sent_at": "2026-10-23T18:00:00.000000+00:00",
  "buffer": {
    "buffer_epoch": "5b0c9e2a-1f4d-4c3b-8a6e-7d2f1c0b9a88",
    "pending": 120,
    "oldest_pending_ts": "2026-10-23T17:40:00.000000+00:00",
    "dropped_total": 4,
    "gap_since": "2026-10-23T17:40:00.000000+00:00",
    "paused_reason": null
  },
  "sync": {
    "last_error": "HTTP 503",
    "last_error_at": "2026-10-23T17:55:00.000000+00:00",
    "consecutive_failures": 0,
    "auth_state": "ok"
  },
  "samples": [
    {
      "device_id": "sw-access-01",
      "ts": "2026-10-23T17:59:30.123456+00:00",
      "metric": "reachable",
      "value_bool": true,
      "value_num": null,
      "ok": true,
      "probe_kind": "icmp",
      "error": null
    },
    {
      "device_id": "sw-access-01",
      "ts": "2026-10-23T17:59:30.123456+00:00",
      "metric": "latency_ms",
      "value_bool": null,
      "value_num": 1.84,
      "ok": true,
      "probe_kind": "icmp",
      "error": null
    },
    {
      "device_id": "sw-access-01",
      "ts": "2026-10-23T17:59:30.123456+00:00",
      "metric": "packet_loss_pct",
      "value_bool": null,
      "value_num": 0.0,
      "ok": true,
      "probe_kind": "icmp",
      "error": null
    }
  ]
}
```

`sync` es informativo y lo reporta el colector: `last_error`/`last_error_at`
conservan el último error aunque el intento actual ya haya funcionado;
`consecutive_failures=0` indica que está resuelto. `auth_state` es
`ok|error|unknown`. El servidor no inventa estos campos: si el colector no los
manda (versión antigua), quedan `NULL`.

Validación por muestra:

| Campo | Regla |
|---|---|
| `device_id` | `^[A-Za-z0-9._:-]{1,128}$` |
| `ts` | ISO-8601 **con offset**; naive → rechazo; se normaliza a UTC `+00:00` |
| `metric` | `reachable` \| `latency_ms` \| `packet_loss_pct` |
| `reachable` | exige `value_bool` booleano y `value_num=null` |
| `latency_ms` | exige `value_num >= 0`; `value_bool=null`; no se acepta si `ok=false` ni si el `reachable` del mismo `ts` es false |
| `packet_loss_pct` | exige `value_num` 0..100, `probe_kind="icmp"`; puede ser 100 con `reachable=false` |
| `ok=false` | exige `metric="reachable"`, `value_bool=null`, `error` no vacío; no se envían filas de latencia/loss |
| `ok=true` | `error=null` si `reachable=true`; `error` no vacío si `reachable=false` |
| `probe_kind` | `icmp` \| `tcp` |
| `error` | ≤ 200 caracteres |

Límites: `samples` ≤ 500 (`413` si más) y `Content-Length` ≤ 1 MiB (`413`). El
colector envía 200 por defecto. Un lote con muestras inválidas **no se rechaza
entero**: las válidas se insertan, las inválidas se cuentan y se devuelven para
que el colector las marque `dead` (si no, bloquearían el buffer para siempre).

Respuesta 200:

```json
{
  "status": "ok",
  "site_id": "site-lima",
  "collector_id": "collector-lima-01",
  "server_time": "2026-10-23T18:00:01.120000+00:00",
  "accepted": 118,
  "duplicates": 2,
  "rejected": 0,
  "out_of_order": 0,
  "clock_skew_s": 1.2,
  "rejected_samples": []
}
```

- `status`: `"ok"` si `rejected==0`; `"partial"` si hay rechazos.
- `rejected == len(rejected_samples)` siempre.
- `accepted`: filas nuevas insertadas.
- `duplicates`: ya existían por `(site_id, device_id, ts, metric)`.
- `rejected_samples`: **todas** las muestras rechazadas (máximo 500, el tamaño
  del lote), con `{"device_id","ts","metric","reason"}`. El colector las marca
  `dead` por triplete, no por índice. Si el servidor devuelve `rejected>0` pero
  la lista no cubre todas, el colector no debe adivinar: deja el resto
  `pending` y lo reporta como error de contrato (no las borra).
- `out_of_order`: muestras válidas cuyo `ts` es más viejo que el estado ya
  guardado para ese device; se almacenan en `remote_sample`, pero **no**
  actualizan `remote_device_state`. Se reportan para diagnóstico de reloj.
- `clock_skew_s = received_at - sent_at` (ambos UTC). No se corrige nada.
  `sent_at` se regenera **inmediatamente antes de cada intento HTTP**, no es la
  hora de creación del lote; si no, un reintento después de 10 min parecería
  un reloj desviado. `sync_batch_id` identifica el conjunto lógico y puede repetirse
  entre reintentos; **no** se usa para idempotencia (la clave única son las
  muestras).

Errores:

| Código | Cuándo | `detail` |
|---|---|---|
| 401 | sin clave / inválida / revocada | `"API key de colector requerida"` / `"API key inválida o revocada"` |
| 403 | clave de usuario o site/collector no coinciden | `"La clave no es de tipo collector"` / `"La clave no corresponde a este colector"` |
| 413 | lote o cuerpo demasiado grande | `"Lote demasiado grande: máximo 500 muestras y 1 MiB"` |
| 422 | schema/versión/campo inválido | detalle Pydantic o `"schema_version no soportada: <n>"` |
| 429 | rate limit | slowapi |
| 503 | DB central no disponible | `"Almacén remoto no disponible: <causa>"` |

En `503`/`5xx`/timeout el colector **no borra nada**: las filas siguen
`pending` y se reintentan. La idempotencia la garantiza la clave única del
servidor, no la memoria del colector.

### 6.3 Idempotencia exacta

Clave única central:

```sql
UNIQUE (site_id, device_id, ts, metric)
```

Se incluye `site_id` aunque el `device_id` sea único hoy, porque:
(a) dos sites podrían usar el mismo ID (p. ej. `sw-access-01`);
(b) el reintento de un lote debe ser idempotente incluso si cambia el
`collector_id` (rotación/reemplazo del nodo).

Inserción por muestra: `INSERT ... ON CONFLICT DO NOTHING`; `rowcount=1`
cuenta `accepted`, `rowcount=0` cuenta `duplicates`. La primera escritura gana:
si llega el mismo `(site, device, ts, metric)` con otro valor, se conserva el
original y se cuenta como duplicado (no se sobrescribe). Todo el lote válido se
inserta en **una transacción** (`BEGIN IMMEDIATE`); si falla la DB, se revierte
completo y se devuelve 503.

### 6.4 Reintentos y backoff

```
delay = min(backoff_max_s, backoff_base_s * 2**consecutive_failures)
        * uniform(0.5, 1.5)
```

- Base 5 s, máximo 300 s, jitter para no sincronizar todos los sites a la vez.
- Éxito: `consecutive_failures=0`, `next_attempt = now + interval_s`.
- `401/403`: `auth_state="error"`, backoff máximo; **no** se descartan datos.
- `413`: reduce `batch_max_samples` a la mitad (mínimo 25) y reintenta una vez
  de inmediato; el cambio es en memoria.
- `422`: error de contrato/versión; backoff máximo y log crítico; no se
  descartan datos (un `schema_version` incompatible requiere intervención).
- `5xx`/timeout/error de red: backoff normal.
- `200` con `rejected>0`: marca esos tripletes `dead` con `dead_reason`, los
  excluye de futuros lotes y sigue.
- Heartbeat: si no hay `pending`, se envía un POST con `samples: []` cada
  `heartbeat_s` (60 s) mientras el centro esté disponible. Es lo que permite
  al centro distinguir «site en contacto sin novedades» de «site sin
  contacto».

El sync nunca borra filas antes de recibir confirmación. Si el proceso muere
entre el POST y el marcado local, el siguiente lote reenvía y el servidor
responde `duplicates`; no hay doble conteo.

### 6.5 Relojes

- `ts` de la muestra = hora del colector en el momento de la sonda. Es la
  línea temporal de la medición.
- `sent_at` = hora del colector al construir el lote.
- `received_at` = hora del servidor al recibir; se guarda por fila.
- `clock_skew_s = received_at - sent_at` por lote; se guarda en
  `remote_collector.clock_skew_s`.
- Si `|skew| > 300 s`, `time_quality="skewed"`; si no, `"ok"`. No se ajusta
  ningún `ts` ni se descartan muestras por skew.
- La frescura de contacto se calcula con `received_at` (reloj del servidor),
  nunca con `ts` del colector. Así un reloj mal configurado no hace que un site
  activo parezca muerto.
- Un `ts` que retrocede se detecta por `out_of_order`; el estado no retrocede.

---

## 7. Modelo de datos central

### 7.1 Ubicación y migración

- DB: `runtime/remote_collector.db` (ignorada por git; `runtime/` ya está en
  `.gitignore`). Override: `NETPULSE_REMOTE_DB`. **Decidimos una DB SQLite
  separada sobre reutilizar `audit.db` o `metrics_history_svc` porque** la
  auditoría es inmutable y de otro dominio, y el histórico nativo no tiene
  fuente ni transacciones; mezclarlos impediría distinguir procedencia y
  arriesgaría datos de auditoría.
- SQLite WAL, `synchronous=NORMAL`, `busy_timeout=5000`, `foreign_keys=ON`.
- Sin Alembic: `CREATE TABLE IF NOT EXISTS` + `PRAGMA user_version`.
  `SCHEMA_VERSION = 1`; si la DB tiene `user_version > 1`, el servicio falla
  con un error claro en vez de operar con un esquema desconocido.
- Lock de escritura en proceso (`threading.Lock`), mismo patrón que
  `audit_svc`. Conexión corta por operación. Se asume **un proceso uvicorn**
  (el despliegue actual del Makefile); con varios workers, SQLite sigue
  funcionando con WAL/`busy_timeout`, pero la poda y el lock de proceso deben
  revisarse. Para N sites, Postgres.
- Si la DB no existe todavía: `GET /api/remote/sites` devuelve `{"sites": []}`
  (ausencia de contacto es un estado válido). Si existe pero no se puede leer:
  `503 "Almacén remoto no disponible: <causa>"`.

### 7.2 DDL exacto

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS remote_collector (
    site_id              TEXT NOT NULL,
    collector_id         TEXT NOT NULL,
    collector_version    TEXT,
    probe_interval_s     INTEGER,
    buffer_epoch         TEXT,
    buffer_epoch_changed_at TEXT,
    first_seen_at        TEXT NOT NULL,
    last_seen_at         TEXT NOT NULL,
    last_sent_at         TEXT,
    clock_skew_s         REAL,
    time_quality         TEXT NOT NULL DEFAULT 'unknown'
                         CHECK (time_quality IN ('ok','skewed','unknown')),
    buffer_pending       INTEGER,
    buffer_oldest_ts     TEXT,
    dropped_total        INTEGER,
    dropped_total_max    INTEGER,
    gap_since            TEXT,
    paused_reason        TEXT,
    last_batch_accepted  INTEGER,
    last_batch_duplicates INTEGER,
    last_batch_rejected  INTEGER,
    last_sync_error      TEXT,
    last_sync_error_at   TEXT,
    sync_failures        INTEGER,
    auth_state           TEXT CHECK (auth_state IN ('ok','error','unknown') OR auth_state IS NULL),
    last_remote_addr     TEXT,
    PRIMARY KEY (site_id, collector_id)
);

CREATE TABLE IF NOT EXISTS remote_sample (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id       TEXT NOT NULL,
    collector_id  TEXT NOT NULL,
    device_id     TEXT NOT NULL,
    metric        TEXT NOT NULL
                  CHECK (metric IN ('reachable','latency_ms','packet_loss_pct')),
    ts            TEXT NOT NULL,          -- normalizado UTC +00:00
    received_at   TEXT NOT NULL,          -- reloj del servidor
    sync_batch_id TEXT,
    value_num     REAL,
    value_bool    INTEGER CHECK (value_bool IN (0,1) OR value_bool IS NULL),
    ok            INTEGER NOT NULL CHECK (ok IN (0,1)),
    probe_kind    TEXT NOT NULL CHECK (probe_kind IN ('icmp','tcp')),
    error         TEXT,
    UNIQUE (site_id, device_id, ts, metric)
);

CREATE INDEX IF NOT EXISTS idx_remote_sample_device_ts
    ON remote_sample (site_id, device_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_remote_sample_ts
    ON remote_sample (ts);

CREATE TABLE IF NOT EXISTS remote_device_state (
    site_id          TEXT NOT NULL,
    device_id        TEXT NOT NULL,
    collector_id     TEXT NOT NULL,
    last_ts          TEXT NOT NULL,       -- hora de medición del colector
    last_received_at TEXT NOT NULL,       -- hora de llegada al centro
    reachable        INTEGER CHECK (reachable IN (0,1) OR reachable IS NULL),
    latency_ms       REAL,
    packet_loss_pct  REAL,
    probe_kind       TEXT,
    ok               INTEGER NOT NULL CHECK (ok IN (0,1)),
    error            TEXT,
    inventory_match  INTEGER CHECK (inventory_match IN (0,1) OR inventory_match IS NULL),
    inventory_error  TEXT,
    PRIMARY KEY (site_id, device_id)
);

CREATE TABLE IF NOT EXISTS remote_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

Notas de semántica:

- `remote_device_state.reachable = NULL` es «no verificable», no «offline».
  `inventory_match = NULL` es «no se pudo comprobar el inventario».
- `remote_sample` es append-only; no se actualiza. La retención la poda.
- `remote_collector.dropped_total` guarda el último valor reportado;
  `dropped_total_max` conserva el máximo visto **dentro del epoch actual**.
  Ambos son `NULL` si el colector no los reportó (no se asume 0). Si
  `buffer_epoch` cambia, se actualiza `buffer_epoch_changed_at`, se resetea
  `dropped_total_max` al valor entrante y se interpreta como buffer nuevo, no
  como un contador que «mejora».
- `error` nunca se inventa: si la sonda fue OK y respondió, es `null`.

### 7.3 Actualización de `remote_device_state`

Por cada grupo `(device_id, ts)` del lote válido:

1. Se insertan todas las filas en `remote_sample` (idempotente).
2. Se construye el estado combinado: `reachable` de la fila `reachable`;
   `latency_ms` y `packet_loss_pct` de las filas del **mismo `ts`**; si falta
   una, el campo queda `null`. `ok` y `error` de la fila `reachable`. Si el
   grupo no tiene fila `reachable` válida (fue rechazada), **no** se actualiza
   el estado; las filas válidas se guardan y el grupo se reporta como parcial.
3. `inventory_match` se calcula una vez por lote con
   `inventory_svc.inventory_health()` y un `{id: device}` construido desde
   `inventory_svc.list_devices()` (una sola lectura por lote, no una por
   muestra).
   `inventory_health()` es una adición mínima de PR-2 (ver 12/PR-2): devuelve
   `(disponible, error)`. Si el YAML no existe o no se puede parsear, el
   estado queda `inventory_match=NULL` + `inventory_error`; si el inventario
   está sano pero el `device_id` no aparece, `inventory_match=0`.
4. Los grupos se procesan en orden ascendente de `ts`; solo se hace upsert si
   `ts > last_ts` del estado existente. Si `ts` es menor, no se toca el estado
   y se cuenta `out_of_order` (la muestra queda igualmente almacenada). Si es
   igual, no se cuenta como out-of-order pero tampoco se reescribe el estado.

Esto evita que un lote reintentado o un reloj que retrocede pise el estado
actual con datos viejos.

### 7.4 Retención central

- `NETPULSE_REMOTE_RETENTION_DAYS` (default 30) y
  `NETPULSE_REMOTE_PRUNE_INTERVAL_S` (default 3600).
- Poda: borrar `remote_sample` con `ts < now - retention_days` en lotes de
  10 000, como máximo una vez por intervalo, en transacción aparte del ingesta.
  Si falla, el ingesta no falla; se registra y se reintenta en la siguiente
  ventana.
- `remote_device_state` y `remote_collector` **no** se podan (son estado, no
  serie).
- `GET /api/remote/sites/{site_id}` expone `retention_days` y
  `oldest_sample_ts` (con `MIN(ts)` indexado); no expone un total costoso.
- La política es del servidor, no editable por UI en fase 1. Si el operador
  necesita cambiarla, se hace por variable de entorno y reinicio del servicio
  central.

Estimación: 100 devices/60 s ≈ 432 k filas/día; 30 días ≈ 13 M filas. Es
grande para SQLite. En fase 1 (1 site, decenas de devices) es manejable; para
N sites se debe medir y probablemente migrar a Postgres (ver riesgos).

---

## 8. Endpoints centrales propuestos

Prefijo nuevo `/api/remote`, tags `["Remote Collectors"]`. No se modifica
ningún endpoint existente.

### 8.1 `POST /api/remote/ingest`

- Auth: `CollectorAuth` (API key `kind=collector`), sin JWT.
- Rate limit: `120/minute` por IP en la ruta; si slowapi combina además el
  default global de 100/min, el efectivo es 100/min. El colector normal hace
  ≤ 2/min, así que el margen es amplio; con N sites detrás de una misma IP
  habrá que subir el límite por collector, no por IP.
- Body/respuesta: sección 6.2.
- Efectos: inserta muestras, actualiza `remote_device_state`,
  upsert `remote_collector`, programa poda; **no** toca inventario ni audit
  más allá del evento HTTP del middleware.

### 8.2 `GET /api/remote/whoami`

- Auth: `CollectorAuth`.
- 200:

```json
{
  "site_id": "site-lima",
  "collector_id": "collector-lima-01",
  "key_name": "collector-lima-01",
  "key_id": "1b2c3d4e-...",
  "server_time": "2026-10-23T18:00:00.000000+00:00"
}
```

- Uso: `--check` del colector verifica que la clave y el YAML coinciden.

### 8.3 `GET /api/remote/sites`

- Auth: `JWTBearer` + `requires_role("viewer")`.
- Sin query. 200:

```json
{
  "generated_at": "2026-10-23T18:00:00.000000+00:00",
  "sites": [
    {
      "site_id": "site-lima",
      "contact": "fresh",
      "last_seen_at": "2026-10-23T17:59:50.000000+00:00",
      "last_contact_age_s": 10,
      "collectors": [
        {
          "collector_id": "collector-lima-01",
          "version": "0.1.0",
          "contact": "fresh",
          "stale_after_s": 300,
          "first_seen_at": "2026-10-23T17:00:00.000000+00:00",
          "last_seen_at": "2026-10-23T17:59:50.000000+00:00",
          "last_contact_age_s": 10,
          "time_quality": "ok",
          "clock_skew_s": 1.2,
          "buffer": {
            "pending": 120,
            "oldest_pending_ts": "2026-10-23T17:40:00.000000+00:00",
            "dropped_total": 4,
            "dropped_total_max": 4,
            "gap_since": "2026-10-23T17:40:00.000000+00:00",
            "paused_reason": null,
            "buffer_epoch": "5b0c..."
          },
          "last_batch": {
            "accepted": 118,
            "duplicates": 2,
            "rejected": 0
          },
          "sync": {
            "last_error": null,
            "last_error_at": null,
            "consecutive_failures": 0,
            "auth_state": "ok"
          }
        }
      ],
      "devices": {
        "total": 12,
        "reachable": 10,
        "unreachable": 1,
        "unverifiable": 1,
        "unmatched": 0
      }
    }
  ]
}
```

Reglas:

- Un site **solo aparece después de su primer ingest**. Un site que nunca
  contactó no se inventa; no se puede afirmar que exista. `last_seen_at` se
  actualiza con un `200` (incluido heartbeat vacío); un `422`/`403` no cuenta
  como contacto útil, aunque el middleware lo audite.
- `stale_after_s = max(3 * probe_interval_s, 300)`. Si falta el intervalo,
  `300`.
- `contact = "fresh"` si `now - last_seen_at <= stale_after_s`; si no,
  `"stale"`. No hay estado `"down"` a nivel de site.
- `site.contact` toma el **mejor** collector (el de `last_seen_at` más
  reciente). En fase 1 hay uno; con varios, los demás se listan con su propio
  estado.
- `unverifiable` = devices con `ok=0`. `unreachable` = `ok=1 AND reachable=0`.
  `reachable` = `ok=1 AND reachable=1`. `unmatched` = `inventory_match=0`.
- `sync` son datos reportados por el colector; `null` significa «no reportado»
  (versión antigua o primer lote), no «sin errores». `consecutive_failures=0`
  con `last_error` no nulo significa «hubo un error y ya se resolvió».
- Si no hay muestras todavía: `devices` con ceros **comprobados** y
  `last_seen_at` del heartbeat; no es «sin medir» porque el heartbeat sí se
  midió.

### 8.4 `GET /api/remote/sites/{site_id}`

- Auth: viewer+.
- 404 si el site nunca contactó:
  `{"detail":"Site sin contacto registrado: <site_id>"}`.
- 200: el objeto del site de 8.3 más:

```json
{
  "retention_days": 30,
  "oldest_sample_ts": "2026-10-23T17:00:00.000000+00:00",
  "buffer_epoch_changed_at": null
}
```

### 8.5 `GET /api/remote/sites/{site_id}/devices`

- Auth: viewer+.
- Query: `include_unmatched: bool = true`.
- 404 si el site no existe.
- 200:

```json
{
  "site_id": "site-lima",
  "generated_at": "2026-10-23T18:00:00.000000+00:00",
  "contact": "fresh",
  "last_seen_at": "2026-10-23T17:59:50.000000+00:00",
  "stale_after_s": 300,
  "devices": [
    {
      "device_id": "sw-access-01",
      "source": {
        "kind": "remote_collector",
        "site_id": "site-lima",
        "collector_id": "collector-lima-01"
      },
      "inventory_match": true,
      "inventory_error": null,
      "freshness": {
        "state": "fresh",
        "current": true,
        "last_sample_ts": "2026-10-23T17:59:30.123456+00:00",
        "last_received_at": "2026-10-23T17:59:31.000000+00:00",
        "age_s": 29
      },
      "ok": true,
      "reachable": true,
      "latency_ms": 1.84,
      "packet_loss_pct": 0.0,
      "probe_kind": "icmp",
      "error": null
    }
  ]
}
```

Reglas de honestidad:

- `freshness.current = (contact == "fresh") AND
  (now - last_received_at <= stale_after_s)`. La edad usa `last_received_at`
  (reloj del servidor), no `last_ts`.
- Si `current=false`, `reachable`/`latency_ms` son **último estado conocido**,
  no estado actual; la UI debe mostrarlo como histórico y no pintarlo verde.
- `ok=false` implica `reachable=null`; no se traduce a `false` ni a `0 ms`.
- `inventory_match=false` no oculta la fila; se marca «no inventariado».
- `inventory_match=null` se marca «inventario no verificable» con
  `inventory_error`.

### 8.6 `GET /api/remote/sites/{site_id}/devices/{device_id}/history`

- Auth: viewer+.
- Query: `hours: float = 6`, rango `0.1..168`.
- 404 si no hay site o no hay **ninguna** muestra histórica de ese device.
  Si hay muestras pero ninguna dentro de `hours`, `200` con `count:0`,
  `first_ts:null`, `last_ts:null` y `points:[]` (ventana vacía comprobada, no
  error).
- `points` va ordenado por `ts` ascendente; no se agrupa ni se promedia.
- Límite duro: 20 000 puntos; si la ventana produce más, `422`
  `"ventana demasiado grande: <n> puntos > 20000; reduzca hours"`. No se
  trunca en silencio ni se interpola.
- 200:

```json
{
  "site_id": "site-lima",
  "device_id": "sw-access-01",
  "hours": 6,
  "source": {
    "kind": "remote_collector",
    "site_id": "site-lima",
    "collector_id": "collector-lima-01"
  },
  "count": 2,
  "first_ts": "2026-10-23T17:59:30.123456+00:00",
  "last_ts": "2026-10-23T18:00:30.123456+00:00",
  "points": [
    {
      "ts": "2026-10-23T17:59:30.123456+00:00",
      "collector_id": "collector-lima-01",
      "ok": true,
      "reachable": true,
      "latency_ms": 1.84,
      "packet_loss_pct": 0.0,
      "probe_kind": "icmp",
      "error": null
    },
    {
      "ts": "2026-10-23T18:00:30.123456+00:00",
      "collector_id": "collector-lima-01",
      "ok": true,
      "reachable": false,
      "latency_ms": null,
      "packet_loss_pct": 100.0,
      "probe_kind": "icmp",
      "error": "sin respuesta ICMP"
    }
  ],
  "coverage": {
    "buffer_gap_since": null,
    "dropped_samples_reported": 0,
    "expected_samples": null
  }
}
```

Cada punto lleva su `collector_id` porque una ventana puede cruzar un reemplazo
de nodo; `source.collector_id` es solo el último que reportó ese device. No se
reescribe la procedencia histórica.

`coverage.expected_samples` es `null` a propósito: el servidor no conoce la
cadencia exacta de cada device (el colector puede haber cambiado de intervalo,
haber estado pausado o haber perdido muestras). No se calcula un porcentaje de
disponibilidad a partir de datos incompletos.

### 8.7 RBAC y errores transversales

| Endpoint | viewer | operator | admin | collector key |
|---|---|---|---|---|
| `POST /ingest` | — | — | — | sí (site propio) |
| `GET /whoami` | — | — | — | sí |
| `GET /sites` | sí | sí | sí | no (403) |
| `GET /sites/{id}` | sí | sí | sí | no (403) |
| `GET .../devices` | sí | sí | sí | no (403) |
| `GET .../history` | sí | sí | sí | no (403) |

- 401 sin token: `"Token de autorización requerido"` (JWT) o
  `"API key de colector requerida"` (ingesta).
- 403 de rol: el texto existente de `requires_role`.
- 503 solo cuando la DB existe y no se puede leer; un site inexistente es 404.
- El middleware de auditoría registra cada lectura y cada ingesta; no se
  añaden eventos por muestra.

---

## 9. Integración con lo existente y propuesta de UI (no implementar)

### 9.1 Coexistencia con el poller central (decisión)

**Decidimos no alimentar `metrics_poller` ni `metrics_history_svc` con datos
remotos.** Razones:

1. El poller central mide desde el centro; si un device del site no es
   alcanzable desde el centro, marcarlo `offline` sería un falso positivo de
   vantage. Inyectar muestras del site en el mismo snapshot mezcla dos rutas
   de red y hace imposible saber qué se midió desde dónde.
2. `metrics_history_svc` tiene forma fija `{ts, host, devices}` y un deque de
   1440 muestras; no tiene `source`, `ok`, `collector_id` ni transacciones. Un
   site con buffer retrasado rompería la semántica de «muestra actual».
3. La UI necesita distinguir fuente y frescura; eso exige campos que el modelo
   actual no tiene.

Por tanto: el poller central sigue igual para los dispositivos que el centro
alcanza; el colector remoto alimenta su propio almacén; el panel muestra dos
fuentes separadas y **nunca** suma ni promedia una con otra. Si un mismo
`device_id` aparece en ambas, se muestran dos filas con su fuente.

### 9.2 Qué se reutiliza

- Primitiva de sonda (`probe_svc`) — una sola implementación para poller y
  colector.
- Inventario (`inventory_svc.get_device`) solo para calcular
  `inventory_match`; no se escribe.
- API keys (`api_keys_svc`) y RBAC existentes, con la extensión `kind`.
- Auditoría (`audit_svc` + middleware) para las peticiones; no se duplica.
- Patrón de honestidad de `retention_svc` (`null` = sin medir, `503` = no
  disponible) y de `alerts_admin` (`viewer` lectura, `audit` en escritura).

### 9.3 Propuesta de vista «Sitios» (otra ola, solo contrato)

- Sidebar: **INFRAESTRUCTURA → Sitios** (`#v-sites`).
- Tarjetas por site:
  - contacto: `fresh` verde con «último contacto hace X s»; `stale` gris con
    «sin contacto desde hace X min»;
  - buffer: si `pending > 0`, banda ámbar «N muestras pendientes desde
    <oldest_pending_ts>»; si `dropped_total > 0`, banda gris «se descartaron N
    muestras; hueco desde <gap_since>»;
  - `time_quality="skewed"`: aviso «reloj del colector desviado <skew> s»;
  - `paused_reason="disk_low"`: banda gris «sonda pausada: disco bajo»; los
    estados de device pasan a «sin dato actual» en cuanto envejecen;
  - conteos: reachable / unreachable / unverifiable / unmatched.
- Tabla de dispositivos: `Equipo | Fuente | Estado | Latencia de sonda (ms) | Última muestra | Inventario`.
  - `current && ok && reachable` → «Responde» (verde).
  - `current && ok && !reachable` → «No responde al sondeo» (rojo) + error.
  - `current && !ok` → «No verificable» (gris) + error.
  - `!current` → «Sin dato actual» (gris) con «último estado conocido: …».
  - `inventory_match=false` → badge «No inventariado»; `null` → «Inventario
    no verificable».
- Histórico: puntos crudos; los fallos se dibujan como `reachable=false` y los
  huecos como huecos, **sin interpolar**. Nunca una línea continua inventada.
- No hay acciones de escritura: la vista es solo lectura. No hay botón de
  «purgar» ni de «forzar sync» en fase 1.
- Texto de ayuda: «Datos medidos desde el nodo del site; no comparables
  directamente con el sondeo del centro».

Regla de oro de la UI: un site `stale` **nunca** pinta verde; un `ok=false`
**nunca** pinta rojo de caída; un `null` **nunca** se muestra como `0`.

---

## 10. Empaque y despliegue

### 10.1 Requisitos del nodo remoto

- Linux (systemd). Python 3.11+.
- Salida HTTPS hacia `central.base_url` (443 o el puerto configurado).
- ICMP: se usa el binario `ping` del sistema (no se abre socket raw). Si el
  binario no existe, las sondas ICMP quedan `ok=false` con
  `error="ping no disponible"`; los devices SSH/TCP siguen funcionando.
- Dependencias mínimas (`requirements-collector.txt`):
  `httpx==0.28.1` y `PyYAML==6.0.2` (fijar a la versión auditada; hoy PyYAML
  entra transitivamente con napalm, pero el colector debe declararla
  explícita). SQLite, `subprocess`, `socket` y `logging` son stdlib.
- El paquete `app.remote_collector` no importa napalm/netmiko/psutil; solo
  stdlib + httpx + PyYAML. `app/services/collectors/probe_svc.py` también es
  stdlib puro.

### 10.2 systemd (primario)

`/etc/systemd/system/netpulse-collector.service`:

```ini
[Unit]
Description=NetPulse remote collector (site)
Documentation=https://github.com/<org>/netpulse-noc/docs/handoffs/collectors-site.md
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=netpulse-collector
Group=netpulse-collector
WorkingDirectory=/opt/netpulse-collector
EnvironmentFile=/etc/netpulse/collector.env
ExecStart=/opt/netpulse-collector/.venv/bin/python -m app.remote_collector --config /etc/netpulse/collector.yaml
Restart=on-failure
RestartSec=5s
RestartPreventExitStatus=2
TimeoutStopSec=20s

StateDirectory=netpulse-collector
StateDirectoryMode=0750

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/netpulse-collector

# Solo si se sustituye el binario ping por ICMP raw:
# AmbientCapabilities=CAP_NET_RAW
# CapabilityBoundingSet=CAP_NET_RAW

[Install]
WantedBy=multi-user.target
```

`/etc/netpulse/collector.env` (modo `0640`, propietario
`root:netpulse-collector`; systemd lo lee como root y el servicio puede leerlo
para `--check` manual):

```bash
NETPULSE_COLLECTOR_API_KEY=np_...
# Alternativa: NETPULSE_COLLECTOR_API_KEY_FILE=/etc/netpulse/collector.key
```

La API key no se pasa por argumento de línea de comandos (visible en `ps`);
solo por entorno o archivo. El YAML puede ser `0640 root:netpulse-collector`.

Instalación:

```bash
# En el nodo del site, con el repo en un tag fijado
useradd --system --home /var/lib/netpulse-collector --shell /usr/sbin/nologin netpulse-collector
install -d -o netpulse-collector -g netpulse-collector -m 0750 /var/lib/netpulse-collector
python3 -m venv /opt/netpulse-collector/.venv
/opt/netpulse-collector/.venv/bin/pip install -r requirements-collector.txt
/opt/netpulse-collector/.venv/bin/python -m app.remote_collector --check \
  --config /etc/netpulse/collector.yaml
systemctl enable --now netpulse-collector
```

No se instala el stack de monitorización en el site. No se usa
`network_mode: host` ni `privileged`.

### 10.3 docker compose (alternativo)

```yaml
services:
  netpulse-collector:
    build:
      context: .
      dockerfile: docker/collector/Dockerfile
    container_name: netpulse-collector
    command: ["python", "-m", "app.remote_collector", "--config", "/etc/netpulse/collector.yaml"]
    env_file: /etc/netpulse/collector.env
    volumes:
      - /etc/netpulse/collector.yaml:/etc/netpulse/collector.yaml:ro
      - netpulse-collector-state:/var/lib/netpulse-collector
    cap_add:
      - NET_RAW          # solo si se usa ICMP dentro del contenedor
    restart: unless-stopped
    # Sin network_mode: host (prohibido por DEPLOYMENT.md)

volumes:
  netpulse-collector-state:
```

El `Dockerfile` debe instalar `iputils-ping` y `ca-certificates`, copiar solo
`app/` y `requirements-collector.txt`, y ejecutar como usuario no root. Si se
opta por systemd, este fragmento no se usa.

### 10.4 Variables de entorno

| Variable | Dónde | Obligatoria | Uso |
|---|---|---|---|
| `NETPULSE_COLLECTOR_API_KEY` | nodo site | sí (o `_FILE`) | clave `np_` de colector |
| `NETPULSE_COLLECTOR_API_KEY_FILE` | nodo site | alternativa | ruta 0600 con la clave |
| `NETPULSE_COLLECTOR_CONFIG` | nodo site | no | ruta por defecto si no se pasa `--config` |
| `NETPULSE_REMOTE_DB` | centro | no | ruta de la DB central |
| `NETPULSE_REMOTE_RETENTION_DAYS` | centro | no | default 30 |
| `NETPULSE_REMOTE_PRUNE_INTERVAL_S` | centro | no | default 3600 |

`settings.py` no necesita cambios obligatorios: el módulo remoto lee sus
variables con `os.getenv` (patrón ya usado por `metrics_history_svc`). Si se
prefiere centralizarlas, PR-2 puede añadir un bloque pequeño, pero no es
requisito.

### 10.5 Logs

- Salida estándar/error → journald. Sin archivo de log propio por defecto.
- Formato de una línea, con campos clave=valor (o JSON si se configura):
  - arranque: `site=... collector=... interval_s=... buffer=... central=...`
    (sin clave);
  - ciclo: `probe_cycle devices=12 reachable=10 unreachable=1 unverifiable=1
    duration_ms=420`;
  - sync: `sync_batch batch=... pending=120 sent=120 accepted=118
    duplicates=2 rejected=0 http=200 latency_ms=85`;
  - error: `sync_error status=503 attempt=3 next_in_s=40 error="..."`;
  - auth: `auth_error status=401` (sin clave);
  - poda: `buffer_prune deleted_synced=... dropped_unsynced=...`;
  - pausa: `probe_paused reason=disk_low free_mb=...`.
- Nivel `INFO` por defecto; `DEBUG` puede incluir device_id/resultado, pero
  **nunca** la API key ni credenciales (el colector no las tiene).
- `status.json` es la fuente de verdad local para `--status` y para un futuro
  exporter de métricas; no se inventa salud si el archivo falta.

---

## 11. Riesgos y límites

| Riesgo | Impacto | Mitigación / decisión |
|---|---|---|
| Reloj del site desviado | Timeline incorrecta; estado puede no actualizarse | `clock_skew_s` + `time_quality`; frescura por `received_at`; NTP es responsabilidad del nodo; nunca se corrige en silencio |
| Robo de API key | Un atacante puede inyectar latencia/disponibilidad falsa del site | Clave solo-ingesta, ligada a site+collector, TLS, revocación, auditoría por `apikey:<name>`, rate limit. Residual: no hay prueba criptográfica de origen más allá de la clave |
| Buffer lleno / disco lleno | Pérdida de historia o pausa de sonda | Cotas `max_rows`/`max_age_hours`/`min_free_mb`; contadores `dropped_total`/`gap_since`; pausa honesta `disk_low`; nunca se simula |
| Centro caído mucho tiempo | Se pierde lo más viejo al superar la cota | Es una decisión explícita; el centro recibe `dropped_total`/`gap_since`; no se rellena |
| SQLite central con un solo writer | Ingestiones concurrentes de N sites pueden esperar | Transacciones cortas, WAL, `busy_timeout`; fase 1 un site; a escala, Postgres |
| Crecimiento de `remote_sample` | Disco central | Retención 30 días + poda por lotes; estimar con datos reales; no contar filas en cada request |
| Dos colectores en el mismo site | Series intercaladas y duplicados solo si coincide `ts` | Convención: un collector activo por site; clave por collector; la UI muestra `collector_id`; HA fuera de fase 1 |
| Device no inventariado | Datos sin contexto de hostname | `inventory_match=false/error`; no se auto-crea inventario; UI lo marca |
| Device retirado del site | Su última fila queda `stale` para siempre | Se muestra «sin dato actual»; no hay tombstone ni borrado automático en fase 1 |
| Falso negativo de sonda | Un equipo sano parece no responder | Se etiqueta «no responde al sondeo», no «caído»; `ok` distingue fallo de medición; timeout configurable |
| ICMP sin permisos en contenedor | ICMP no verificable | Binario `ping` o `CAP_NET_RAW`; TCP para SSH; `ok=false` con causa, sin inventar |
| TLS interceptado / CA interna | Sync falla | `ca_file`; `verify_tls` por defecto `true`; `false` prohibido en producción |
| Duplicados por reintento | Doble conteo | `UNIQUE(site_id, device_id, ts, metric)` + `ON CONFLICT DO NOTHING`; respuesta `duplicates` |
| `ts` retrocede | Estado viejo pisa al nuevo | Solo se actualiza si `ts > last_ts`; `out_of_order` reportado |
| Reinicio del buffer | Contadores parecen «mejorar» | `buffer_epoch`; `buffer_epoch_changed_at`; `dropped_total_max` |
| Auditoría con más volumen | Crecimiento de `audit.db` | 1 request/min ≈ 1440 eventos/día; aceptable; no se auditan muestras individuales |
| Escalado a N sites | Filas/día lineales; rate limit por IP | Modelo site-scoped; claves por collector; medir y migrar a Postgres si hace falta; rate limit se ajusta por sitio en el futuro |

Límites explícitos de fase 1: un site validado, sin HA, sin alertas locales,
sin config push, sin SNMP de recursos, sin UI implementada.

---

## 12. Plan de implementación por PRs

### PR-1 — Servicio colector + config + buffer (sin tocar el centro)

**Objetivo:** un proceso que mide, bufferiza y sincroniza contra un mock/central
existente, con tests deterministas.

Zona de archivos:

```
app/remote_collector/__init__.py
app/remote_collector/__main__.py
app/remote_collector/cli.py
app/remote_collector/config.py
app/remote_collector/buffer.py
app/remote_collector/sync.py
app/remote_collector/status.py
app/remote_collector/logging_setup.py
app/services/collectors/probe_svc.py          # primitiva stdlib compartida
app/services/collectors/metrics_poller.py     # adaptador mínimo, sin cambio de contrato
requirements-collector.txt
deploy/netpulse-collector.service
docs/collector-config.example.yaml
tests/test_remote_collector_config.py
tests/test_remote_collector_buffer.py
tests/test_remote_collector_probe.py
tests/test_remote_collector_sync.py
tests/test_remote_collector_cli.py
tests/test_probe_svc_compat.py
```

Detalle:

- `config.py`: carga/valida YAML, resuelve clave por env/archivo, errores con
  exit 2, recarga en `SIGHUP` conservando la config previa si falla.
- `buffer.py`: DDL, inserciones transaccionales, `peek_pending`, marcado
  `synced`/`dead`, poda, `collector_state`, `status_summary`.
- `probe_svc.py`: `ProbeResult` y `probe()`; ICMP/TCP; sin imports pesados.
- `sync.py`: cliente httpx con `MockTransport` inyectable, lotes, backoff con
  jitter inyectable (`random.uniform` monkeypatch), heartbeat, manejo de
  401/403/413/422/5xx, reenvío idempotente.
- `cli.py`: `--check`, `--once`, `--status`, `--dry-run`, señales, exit codes.
- `metrics_poller.py`: usa `probe_svc` y conserva `(latency, loss)` legacy;
  test de regresión.
- **No** se crean routers, tablas centrales ni endpoints.

Tests (pytest, sin red real, sin servidores):

- config válida/errónea, claves desconocidas, XOR devices/inventory_file,
  clave ausente, no fuga de la clave en `repr`/logs;
- buffer: esquema, unique local, orden pending, poda por edad/rows, contadores
  `dropped_total`/`gap_since`, pausa por disco (mock `shutil.disk_usage`),
  WAL, reinicio;
- probe: mock `subprocess.run` (ICMP ok/timeout/sin ping/sin permiso) y
  `socket` (TCP ok/refused/timeout/DNS); mapeo `ok/reachable/error`;
- sync: lote aceptado, duplicados, rechazos parciales, 413 reduce batch, 401
  auth error, 5xx backoff, heartbeat vacío, crash antes de marcar → reenvío;
- CLI: `--check`, `--once`, `--status` sin archivo, `--dry-run`;
- compatibilidad del poller central.

Verificación: `make test`, `make lint`, y un `--dry-run` contra un YAML de
ejemplo. Sin servidores.

### PR-2 — Modelo y endpoints centrales + auth de colector

**Objetivo:** recibir, almacenar y exponer datos remotos con RBAC y
honestidad; el colector de PR-1 ya puede apuntar al centro real.

Zona de archivos:

```
app/services/remote/__init__.py
app/services/remote/store.py          # DDL, migración, lock, poda
app/services/remote/ingest_svc.py     # validación/transacción/estado
app/services/remote/query_svc.py      # sites/devices/history
app/routers/remote.py                 # /api/remote/*
app/models/remote_schemas.py          # Pydantic request/response
app/main.py                           # solo la línea de include_router
app/services/security/api_keys_svc.py # kind/site/collector_id + compare_digest
app/middleware/auth.py                # rechazo de claves collector en rutas normales
app/routers/security_ext.py           # creación de clave collector + audit
app/services/inventory_svc.py         # inventory_health(): (disponible, error)
tests/test_remote_ingest.py
tests/test_remote_query.py
tests/test_remote_auth.py
tests/test_remote_retention.py
tests/test_remote_schema.py
```

Detalle:

- `store.py`: `SCHEMA_VERSION=1`, `user_version`, DDL de sección 7.2, ruta
  `runtime/remote_collector.db` o `NETPULSE_REMOTE_DB`, poda.
- `ingest_svc.py`: `CollectorAuth` (o dependencia en `remote.py`), validación
  por muestra, transacción, `ON CONFLICT DO NOTHING`, `remote_device_state`
  solo si `ts` más nuevo, `inventory_match`, `clock_skew`, `buffer_epoch`.
- `query_svc.py`: cálculo de `contact`, `stale_after_s`, `freshness.current`,
  conteos, history con límite de 20 000 puntos, `source` explícito.
- `remote.py`: endpoints de sección 8, rate limit en ingest, errores exactos.
- `api_keys_svc.py`: campos nuevos retrocompatibles; `create_api_key` acepta
  `kind/site_id/collector_id`; `validate_api_key` devuelve esos campos;
  `hmac.compare_digest`.
- `auth.py`: si `kind=="collector"` → 403 en `JWTBearer`.
- `security_ext.py`: validación de payload, auditoría de creación/revocación
  (`collector_key_create`, `collector_key_revoke`) sin material de clave.
- `inventory_svc.py`: `inventory_health()` devuelve `(disponible, error)` sin
  cambiar el comportamiento de `list_devices()`/`get_device()`; `_read()`
  registra el último error de parseo en una variable de módulo. Es lo que
  permite distinguir «device no inventariado» de «inventario ilegible».
- `main.py`: `include_router(remote.router)`. `main.py`, `auth.py` y
  `security_ext.py` son archivos compartidos: los cambios son aditivos y el
  merge se resuelve a mano, como en las olas anteriores.

Tests:

- auth: clave collector válida; clave de usuario 403; clave revocada 401;
  clave de otro site 403; collector no puede leer `/api/remote/sites` ni
  `/api/devices`; creación solo admin; audit sin clave;
- ingest: lote válido; reintento idempotente; rechazo parcial; `413`; schema
  version; `ts` naive; `ok=false` con `reachable=null`; `out_of_order`; estado
  no retrocede; `inventory_match` true/false/null; heartbeat vacío;
- query: sites vacío (DB ausente), site 404, contacto fresh/stale con reloj
  congelado, devices `current=false` con último estado, conteos, unmatched;
- history: ventana, límite 20 000, puntos con `ok=false`, sin interpolación;
- retención: poda por antigüedad, no toca estado/collector, idempotente;
- esquema: creación idempotente, `user_version` futuro falla claro, DB
  corrupta → 503.

Verificación: `make test`, `make lint`; prueba manual con `curl` de un lote y
consulta de los endpoints (sin UI).

### PR-3 — UI «Sitios» (otra ola; **no** en esta rama)

**Objetivo:** exponer la fuente y los estados honestos en el panel.

Zona de archivos: `app/static/dashboard.html` (y CSS/JS propios si el panel los
separa). Cero cambios de backend.

Detalle:

- `#v-sites`, item de sidebar, i18n `sites.*`.
- Fetch a `GET /api/remote/sites`, `.../devices`, `.../history`; guards de rol
  (viewer ve todo; no hay acciones).
- Badges de fuente (`site:<id>`), banners de contacto/buffer/gap/skew, tabla
  con los cuatro estados de 9.3, histórico sin interpolación.
- Estado vacío: «Ningún site ha contactado todavía» (no una tabla con guiones
  que parezca medida).
- Tests: `node --check` del JS extraído, `tests/test_dashboard_routes.py`
  sigue verde, revisión visual con datos reales de un site.

Orden: PR-1 y PR-2 pueden ir en la misma rama/PRs separados; PR-3 solo después
de que el contrato de PR-2 esté commiteado y verificado con un colector real o
un mock que respete este documento.

---

## 13. Qué NO hace la fase 1 (explícito)

- **No** escribe configuraciones ni ejecuta comandos en los equipos. No hay
  SSH/CLI/SNMP-set. Solo lectura de estado por ICMP/TCP.
- **No** hace config push ni recibe órdenes del centro. El flujo es
  site → centro, nunca centro → site.
- **No** genera alertas en el site ni habla con Alertmanager. Solo latencia y
  disponibilidad. Las alertas (p. ej. «site sin contacto») son una ola
  posterior y deben usar estos datos, no inventarlos.
- **No** recolecta CPU/RAM/disco, syslog, tráfico, interfaces ni vecinos LLDP.
- **No** tiene HA ni varios colectores por site validados. Un site, un
  colector activo.
- **No** implementa UI en esta corrida. La sección 9.3 es contrato para otra
  ola.
- **No** auto-descubre dispositivos ni hace scan de red. El inventario es
  explícito (YAML o archivo exportado).
- **No** crea dispositivos en el inventario central. Los desconocidos se
  marcan `inventory_match=false`.
- **No** alimenta Prometheus, Grafana ni el histórico nativo con datos
  remotos. El almacén es separado y la fuente es explícita.
- **No** usa mTLS. La autenticación es API key sobre TLS; mTLS queda como
  evolución.
- **No** corrige relojes ni normaliza `ts` del colector a la hora del centro.
- **No** rellena huecos, interpola ni calcula SLA con datos incompletos.

---

## 14. Incertidumbres y decisiones que necesitan visto bueno

1. **Auth**: extender `api_keys_svc` con `kind="collector"` + `site_id` +
   `collector_id` (recomendado) frente a una tabla de tokens de colector
   separada. La extensión reutiliza hash/revocación/auditoría; la tabla
   separada aísla más pero duplica gestión.
2. **Almacén central**: SQLite separado en `runtime/remote_collector.db`
   (recomendado para fase 1) frente a Postgres. SQLite basta para un site;
   N sites exige medir y probablemente migrar.
3. **Rutas**: `/api/remote/*` (recomendado) frente a colgar los sitios de
   `/api/system/*`. La ruta nueva evita mezclar con el poller central.
4. **Retención central**: 30 días por defecto. ¿Es aceptable para el NOC o se
   prefiere 90? A 100 devices/60 s, 30 días ≈ 13 M filas; 90 días ≈ 39 M.
5. **Inventario**: lista/YAML local en fase 1 (recomendado) frente a
   `GET /api/remote/inventory` contra el centro. El inventario central no
   tiene `site_id`; añadirlo es un cambio de modelo aparte.
6. **Sites antes del primer contacto**: solo aparecen tras el primer ingest
   (recomendado, honesto) frente a una lista de sites esperados en config
   central. La lista esperada permitiría mostrar «nunca contactó», pero
   inventaría entidades que el centro no ha visto.
7. **`packet_loss_pct`**: se mantiene con semántica de **una** sonda ICMP
   (0/100). ¿Se prefiere eliminarlo del contrato y quedarse solo con
   `reachable` + `latency_ms`? Recomendado mantenerlo, bien rotulado.
8. **Un collector por site**: convención de fase 1. ¿Se necesita HA desde el
   inicio? Si sí, hay que definir líder/lease y deduplicación por `ts`, que
   este documento no resuelve.
9. **UI**: PR-3 no empieza hasta que el contrato de PR-2 esté commiteado y
   validado con un colector real o un mock que respete los schemas de este
   documento.

---

## 15. Anexo: matriz de honestidad (resumen para UI y tests)

| Situación real | Fila local | Centro | Lo que se muestra | Lo que **no** se hace |
|---|---|---|---|---|
| Sonda responde | `reachable=1`, `latency` | `remote_device_state` fresco | «Responde», latencia | — |
| Sonda no responde | `reachable=0`, `ok=1`, `error` | estado fresco, `reachable=0` | «No responde al sondeo» | No decir «caído» con certeza |
| No se pudo medir | `reachable=NULL`, `ok=0`, `error` | `reachable=NULL` | «No verificable» | No contar como offline ni `0 ms` |
| Centro caído | `pending` crece | Sin datos nuevos | Al volver: `pending`/`gap_since` | No rellenar el hueco |
| Colector caído | Sin filas nuevas | `last_seen_at` viejo | «Sin contacto desde X»; último estado como histórico | No pintar verde ni latencia actual |
| Reloj desviado | `ts` del colector | `clock_skew_s` | Aviso de desviación | No corregir en silencio |
| Device no inventariado | `device_id` | `inventory_match=0` | Badge «No inventariado» | No crear inventario |
| Buffer lleno | descarta lo viejo | `dropped_total`/`gap_since` | «Se descartaron N; hueco desde X» | No inventar continuidad |
| Buffer recreado | nuevo `buffer_epoch` | epoch distinto | «Buffer reiniciado» | No comparar contadores como si fueran continuos |

---

## 16. Desviaciones de implementación

Se anotan aquí los cambios respecto al diseño, sin reescribir las secciones
anteriores.

### PR-1 — colector, buffer y sync

- **Capacidad real medida** (test `tests/test_remote_collector_capacity.py`):
  100 devices × 1 día a 60 s con sonda ICMP = **432 000 filas**, **93.0 MB**
  tras `wal_checkpoint(TRUNCATE)`, en **11.2 s** (~38 600 filas/s) en el
  entorno de tests. La estimación de la sección 5.3 (45-65 MB/día) se quedó
  corta: el valor medido ronda **~90 MB/día** para ese escenario, y
  `max_rows=500 000` equivaldría a ~108 MB. No cambia la política de poda;
  solo se corrige la cifra de dimensionamiento.
- `status.json` incluye `buffer.dead` (filas rechazadas localmente) además de
  los campos del diseño; es informativo y no viaja en el body de ingesta.
- `sync.next_attempt_at` se calcula como hora futura tras aplicar el backoff,
  no como la hora del último intento.
- `SIGHUP` recarga `devices`/`probe`; cambios de `buffer`, `central`, `site_id`
  o `collector_id` se registran y **requieren reinicio** (la clave y el cliente
  HTTP viven en el proceso). El diseño no exigía más para fase 1.
- El adaptador de `metrics_poller` usa `probe_svc` y conserva el contrato
  `(latency_ms, loss_pct)`; la sonda ICMP/TCP real se prueba con mocks, sin
  red.
- `PyYAML` se declara explícitamente en `requirements-collector.txt`
  (`6.0.2`); antes solo entraba como dependencia transitiva de napalm.
