# NetPulse — Rendimiento

Este documento recoge las intervenciones de rendimiento con cifras medidas
en el repo, no estimaciones. Cada anexo indica el banco de pruebas usado para
reproducirlas.

---

## Anexo — I/O bloqueante en handlers `async` (tarjeta 13B)

### El problema

Un handler `async` comparte el event loop con **todas** las peticiones y con
el WebSocket de terminal. Cualquier llamada síncrona lenta dentro de él
(`subprocess`, `psutil`, SQLite, SSH/paramiko, `urllib`) congela el resto
mientras dura. En el caso medido de `docker stats`, el comando puede tardar
**~2 s**: durante ese tiempo el dashboard no responde.

Medición base (`/api/metrics`, que recolectaba `psutil` en línea):

| métrica (4 rondas) | antes |
|---|---|
| latencia p95 del endpoint | **188,6 ms** |
| `GET /api/health` (canario) p95 bajo carga | **137,3 ms** |
| canario máximo | **190,1 ms** |
| p95 de 20 `GET /api/system/overview` concurrentes | **286,2 ms** |

El canario se lanza cada 25 ms: su latencia es, de facto, el tiempo que el
event loop estuvo secuestrado.

### Qué se corrigió

| Archivo | Antes | Ahora |
|---|---|---|
| `app/routers/metrics.py` | `host_metrics_svc.update_prometheus()` en línea (muestra de CPU de 100 ms + `psutil`) | `await update_prometheus_async()` → `to_thread` + timeout |
| `app/routers/system.py` `/host` y `/overview` | `def` síncrono (hilo del threadpool por request, 100 % de CPU por llamada) | `async` + `collect_async()` (`to_thread`) + caché 2 s single-flight |
| `app/routers/system.py` `/devices?refresh` y `/poll` | `poll_all()` síncrono | `to_thread` con tope de 120 s (`NETPULSE_SYSTEM_POLL_TIMEOUT`) |
| `app/routers/system.py` `/history` | SQLite en línea | `to_thread` con tope de 20 s |
| `app/routers/system.py` `/ping` | `subprocess.run` en handler | `to_thread` + `asyncio.wait_for` (cota dura) |
| `app/routers/monitoring.py` `/overview` | 4 sondeos HTTP + 5 PromQL **secuenciales** | `asyncio.gather` + `to_thread` en paralelo |
| `app/routers/monitoring.py` `/series` | `urlopen` en línea | `to_thread` |
| `app/services/host_metrics_svc.py` | `collect()` sin caché ni variante async | `collect_cached()`, `collect_async()`, `update_prometheus_async()`, `last_snapshot()` |

Nuevo servicio `app/services/docker_stats_svc.py`:

- `snapshot()` / `snapshot_async()` con **timeout** del comando y de la
  espera, **caché corta** y *single-flight* (N requests concurrentes lanzan
  un solo `docker stats`).
- Estado honesto si Docker no está: `{available: false, reason:
  "docker_no_disponible" | "timeout" | "error", detail, containers: {}}`,
  **HTTP 200**, nunca 500.
- `traffic()` / `traffic_async()` mantienen el contrato
  `{nombre: (rx, tx)}` que ya consumía el health de dispositivos, incluido el
  alias sin prefijo `netpulse-`.
- Nuevo endpoint `GET /api/system/docker?refresh=` (aditivo; no modifica
  ningún esquema existente).

Configuración (todo por entorno):

| Variable | Default | Efecto |
|---|---|---|
| `NETPULSE_DOCKER_STATS_TTL` | `8` | vida de la caché de un resultado correcto (s) |
| `NETPULSE_DOCKER_STATS_ERROR_TTL` | `3` | vida de la caché de un fallo (s) |
| `NETPULSE_DOCKER_STATS_TIMEOUT` | `2` | timeout de `docker stats` (s) |
| `NETPULSE_HOST_METRICS_CACHE_TTL` | `2` | caché de `collect()` para el dashboard (s); `0` la desactiva |
| `NETPULSE_HOST_METRICS_TIMEOUT` | `10` | tope de `collect_async()` (s) |
| `NETPULSE_SYSTEM_POLL_TIMEOUT` | `120` | tope de un poll forzado por HTTP (s) |

### Qué se midió después

Mismo banco, mismo host, 4 rondas:

| métrica | antes | después | mejora |
|---|---|---|---|
| canario p95 bajo carga | 137,3 ms | **25,5 ms** | −81 % |
| canario máximo | 190,1 ms | **27,5 ms** | −86 % |
| 20 × `/api/system/overview` p95 | 286,2 ms | **63,0 ms** | −78 % |
| `/api/metrics` p95 | 188,6 ms | 128,8 ms | −32 % (el trabajo sigue existiendo: 100 ms de muestra de CPU, ahora fuera del loop) |

`/api/system/host` tras el cambio: p95 **24,4 ms** con caché caliente, canario
máximo **18,8 ms** y 20 overviews concurrentes p95 **51,3 ms**.

Caso `docker stats` con un `docker` falso que tarda 2,0 s
(`--mode docker-demo`, mismo proceso, mismo comando):

| patrón | request p50 | bloqueo máximo del loop |
|---|---|---|
| `subprocess.run` dentro de `async` (antiguo) | 2026,0 ms | **2253,9 ms** |
| `await docker_stats_svc.snapshot_async()` | 2018,9 ms | **1,5 ms** |

Docker ausente en este host (Colima sin daemon): `GET /api/system/docker`
responde **200** en 19,8–58,2 ms con
`available: false, reason: "docker_no_disponible"` y el detalle del socket.

### Reproducir

```bash
# API real: lag del loop + 20 peticiones concurrentes al dashboard
PYTHONPATH=. .venv/bin/python scripts/perf_async_bench.py --mode live \
    --endpoint /api/metrics --rounds 4 --json /tmp/bench.json

# Comparativa del patrón docker (no requiere Docker real)
PYTHONPATH=. .venv/bin/python scripts/perf_async_bench.py --mode docker-demo \
    --rounds 2 --json /tmp/bench-docker.json
```

### Tests

`tests/test_system_async_io.py` (20 tests):

- Docker presente/ausente/daemon caído/timeout, parseo NetIO, alias corto.
- Caché (TTL configurable, TTL de error más corto, `refresh` la ignora).
- Endpoints `/api/system/docker`, `/host`, `/overview`, `/devices` con el
  contrato JSON intacto y 200 con Docker caído.
- Event loop libre con I/O lento simulado en `/host`, `/api/metrics` y
  `docker_stats_svc` (se mide el hueco máximo entre *ticks* de 20 ms).
- Caché single-flight de `collect_cached()` (8 hilos → 1 recolección).

Suite completa: **433 passed** (413 previos + 20 nuevos).

### Auditoría del resto del repo: qué quedó y por qué

Se revisaron todos los `async def` de `app/`. Quedan llamadas síncronas
dentro de handlers `async` **fuera de la zona de esta tarjeta** (la regla de
trabajo prohíbe tocar `devices.py`/`inventory_svc`, y estos otros módulos
pertenecen a sus propias tarjetas). Se documentan con su ubicación exacta
para que se corrijan con el mismo patrón:

| Ubicación | Bloqueo | Arreglo propuesto |
|---|---|---|
| `app/routers/devices.py:36-59` (`_get_device_traffic`) llamado desde `device_health` (`async`, línea 79; uso en línea 134) | `subprocess.check_output(["docker","stats"...], timeout=2.0)` → hasta **2 s** de loop congelado | Sustituir por `await docker_stats_svc.traffic_async()`; el servicio ya existe, cachea 8 s y devuelve estado honesto |
| `app/routers/sftp.py:483-560` (`_get_ssh_client_and_sftp`) llamado desde `upload_file_to_device` (`async`, línea 598; conexión en 610, `sftp.putfo` en 621) | conexión SSH + subida paramiko síncrona | `await asyncio.to_thread(...)` para conectar/subir con el cliente creado dentro del hilo |
| `app/routers/alerts.py:121-180` (`alertmanager_webhook`, `async`) | `maintenance_svc.active_windows()` (línea 137, disco/SQLite) y `notifications_svc.notify_event()` (línea 159; SMTP/Slack/`urllib`) en línea | `to_thread` para la carga de ventanas y `asyncio.create_task`/cola para el envío (o `to_thread` acotado) |
| `app/routers/remote.py:123` (`ingest`, `async`) | `ingest_svc.ingest()` escribe SQLite síncrono | `await asyncio.to_thread(ingest_svc.ingest, ...)` con timeout y 503 honesto |
| `app/routers/netbox.py:447` (`sync_from_netbox`, `async`) y `:618` (`push_to_netbox`, `async`) | lectura/escritura de `devices.yaml` (`yaml.safe_load/safe_dump`) e invalidación de caché en línea | `to_thread` para lectura y escritura; la red ya es `httpx.AsyncClient` |
| `app/main.py:146` (`health`, `async`) | `list_devices()` hace `stat` + posible lectura de YAML en el primer hit | `to_thread` o snapshot precalculado por el poller |

Lo que **no** es un problema:

- El poller (`metrics_poller`) y el watchdog de sites ya son hilos/tareas
  propias y usan `asyncio.to_thread` en el ciclo async
  (`watchdog_svc.py:540`).
- El WebSocket de terminal usa `asyncssh` (I/O async real).
- Los servicios NAPALM/Netmiko son síncronos por diseño y se invocan desde
  el poller o desde handlers `def` (threadpool), no desde `async`.
- `syslog_svc` usa un hilo *flusher* con cola acotada.

### Notas de comportamiento

- La caché de host (2 s) puede devolver métricas con hasta 2 s de antigüedad
  en `/host` y `/overview`. `/api/metrics` (scrape de Prometheus) usa
  `cached=False` para mantener valores frescos.
- Si `collect()` supera el timeout y hay un snapshot previo, se devuelve el
  previo (misma forma); si no lo hay, `/host` responde **503** con un mensaje
  claro, nunca un 500.
- No se usó `time.sleep` en ningún handler `async`; los hilos son el
  executor acotado de `asyncio.to_thread`.

---

# Rendimiento del inventario: health y escritura

Medición de la tarjeta **13A** sobre el camino HTTP real, con **500 equipos
sintéticos** en un `devices.yaml` temporal. No se tocó el inventario real ni
el esquema del YAML. Todas las cifras son de una máquina concreta (abajo) y
el montaje es una simulación: sirven para comparar antes/después y para fijar
órdenes de magnitud, no como promesa de red real.

## 1. Antes / después

| Ruta | Antes | Después | Objetivo |
|---|---:|---:|---:|
| `GET /api/devices/health` — primera llamada (frío) | 4293 ms | 881 ms | — |
| `GET /api/devices/health` — frío, p95 (8 tandas) | **2916 ms** | **811 ms** | ≤ 1500 ms |
| `GET /api/devices/health` — régimen estable, p95 (25 llamadas) | 5–6 ms* | **8.5 ms** | — |
| `PUT /api/devices/{id}` — p50 | 416 ms | **42.5 ms** | — |
| `PUT /api/devices/{id}` — p95 (50 updates) | **422 ms** | **72.3 ms** | ≤ 100 ms |
| `PUT /api/devices/{id}` — p99 | 423 ms | 74.6 ms | — |
| `GET /api/devices` (500) | 3.2 ms | 8.2 ms** | — |

\* El "antes" solo era rápido durante los 8 s de `_HEALTH_TTL`; al caducar
la caché volvía a sondear los 500 a la vez (2.9 s). El "después" mantiene el
estado fresco con refresco de fondo: 25 llamadas seguidas devolvieron los 500
equipos conocidos (100 online / 400 offline) sin ningún `null`.

\*\* Varía entre 3 y 9 ms según la caché de página del sistema; no se tocó el
camino de lectura (solo se añadió el cerrojo y el loader rápido de libyaml).

Estados en el peor caso frío: la primera respuesta trae 17 online, 0 offline
y 483 desconocidos; el resto lo va llenando el refresco de fondo en ~6-8 s.
**Ningún equipo se devuelve verde si su última medición superó el TTL.**

## 2. Montaje

| Elemento | Valor |
|---|---|
| Host | macOS 26.6.2, Apple M1, 16 GiB, SSD |
| Python | 3.12.14 |
| PyYAML | 6.0.3 con **libyaml** (`CSafeLoader`/`CSafeDumper`) |
| API | FastAPI 0.143.3 / Starlette 1.8.0, **un worker**, ASGI en proceso |
| Inventario | 500 equipos, YAML de 173 KiB, en `tempfile.mkdtemp()` |
| Mezcla de red | 100 con puerto abierto (servidor TCP local), 200 con puerto cerrado (`127.0.0.1:1`, refused rápido), 200 blackhole (`192.0.2.0/24`, TEST-NET-1, hasta timeout) |
| Orden | perfiles **intercalados** con semilla fija (13), para que el presupuesto incremental de health no caiga siempre en equipos vivos |
| Auth | JWT admin real; middleware de la app activo; rate limiter desactivado (el banco mide cómputo, no el límite de 100/min) |
| Lifespan | **no** se ejecuta (sin syslog/poller/watchdog) |

Reproducción:

```bash
PYTHONPATH=. .venv/bin/python scripts/perf_devices_bench.py \
    --devices 500 --put-iterations 50 --health-iterations 8 --warm-iterations 25
```

El script crea el YAML temporal, mide por HTTP con `httpx.ASGITransport`,
imprime p50/p95/p99 en JSON y comprueba al final que siguen los 500 equipos en
disco y que todas las entradas conservan `credentials.password`. La opción
`--profile` perfila 10 `update_device` con `cProfile`.

## 3. Diagnóstico del PUT (el coste real)

Perfilado de `inventory_svc.update_device` con 500 equipos (cifras estables
del microbanco, sin el sobrecoste de `cProfile`):

| Paso | Antes | Después |
|---|---:|---:|
| `_read()` dentro del PUT (caché) | 0.01 ms | 0.01 ms |
| `copy.deepcopy(_read())` | 3.5 ms | 3.5 ms |
| `_sanitize_for_yaml` (json round-trip) | 1.7 ms | 1.7 ms |
| `yaml.safe_dump` completo | **127 ms** | **32 ms** (CSafeDumper) |
| **Re-parseo tras escribir** (`get_device` → `_read` con caché invalidada) | **273 ms** | **0 ms** |
| Total servicio | ~405 ms | ~38 ms |

Las dos causas eran concretas:

1. `_write()` invalidaba la caché y `update_device()` terminaba con
   `get_device()`, que **volvía a leer y parsear el YAML entero** (273 ms).
   Ahora `_write()` cachea exactamente el objeto saneado que acaba de volcar,
   con la firma (mtime/tamaño) del archivo nuevo. La siguiente lectura es O(1).
   Si el archivo cambia por fuera, la firma no coincide y se reparsea como
   siempre; el YAML de disco sigue siendo la fuente de verdad.
2. El volcado completo es O(n) legítimo (no se puede escribir medio YAML sin
   romper la atomicidad), pero con `yaml.CSafeDumper` (libyaml) baja de 127 a
   32 ms produciendo **bytes idénticos** al dumper puro. Si el entorno no
   trae libyaml se usa `SafeDumper`: el PUT queda en ~130 ms p95, sigue siendo
   ~3× mejor que antes, pero no cumple el objetivo de 100 ms.

Se mantiene la escritura **síncrona** y **atómica** (`mkstemp` en el mismo
directorio → `fsync` implícito del cierre → `os.replace`). No se hizo async:
la llaman endpoints sync y también código async; envolverla en `to_thread`
solo trasladaría el coste y complicaría la semántica de errores. Un fallo a
mitad de volcado borra el temporal y deja intacto el `devices.yaml` anterior
(hay test explícito).

Además se añadió un `threading.RLock` que cubre la secuencia
leer-modificar-escribir de `add/update/delete`. Antes, dos PUT concurrentes
podían partir de la misma copia y perder el cambio del otro. Ahora se
serializan dentro del proceso (el archivo nunca queda corrupto en ningún
caso, porque `os.replace` ya era atómico). Entre workers el último `replace`
gana; ver límites.

Cambios de esquema: **ninguno**. Ni campos nuevos ni equipos perdidos
(comprobado en el banco y en tests).

## 4. Health: TTL por equipo + incremental + fondo

La implementación anterior hacía `asyncio.gather` de las 500 sondas con
timeout 2.5 s. Peor aún, la primera llamada ejecutaba `docker stats` con
timeout 2 s **en el hilo del event loop** para el tráfico rx/tx. De ahí los
3-6 s.

La nueva lógica vive en `app/services/device_health.py` y funciona en tres
capas:

* **TTL por equipo** (`NETPULSE_HEALTH_TTL`, 12 s). Un estado solo se sirve
  mientras su antigüedad sea ≤ TTL. Pasado el TTL sin sonda nueva:
  `online: null`, `stale: true`, `checked_at` con la última medición. Nunca
  el último verde.
* **Sondeo incremental por petición**: como máximo
  `NETPULSE_HEALTH_INLINE_BUDGET` equipos caducados (24), esperando como
  máximo `NETPULSE_HEALTH_INLINE_DEADLINE` (0.8 s). Si no terminan, se
  cancelan y se devuelven desconocidos. La latencia de health deja de
  depender del tamaño del inventario.
* **Refresco de fondo**: un task por event loop sondea los caducados en
  tandas de `NETPULSE_HEALTH_CONCURRENCY` (96) y **recalcula caducados entre
  tandas**. Se adelanta al 80 % del TTL (9.6 s) para que ningún equipo llegue
  a caducar en régimen estable. Hay un set de "en vuelo" para no contar dos
  veces el mismo intento si coinciden petición y fondo.

### Estados honestos

| Resultado de la sonda | Estado devuelto | `online` |
|---|---|---|
| TCP/ICMP responde | verde, con latencia y `checked_at` | `true` |
| 1 fallo consecutivo | desconocido (ni verde ni rojo) | `null` |
| 2 fallos consecutivos | offline | `false` |
| DNS, host vacío, sin permiso ICMP | desconocido (no medible) | `null` |
| Estado más viejo que el TTL | desconocido + `stale: true` | `null` |

Contrato: se mantienen `online`, `latency_ms`, `rx`, `tx` y el modo `simple`.
Se añaden dos campos opcionales y retro-compatibles: `checked_at` (ISO-8601
UTC o `null`) y `stale` (bool). En `simple=true` un equipo desconocido se
representa como `false`: el formato solo admite bool y se elige el lado
conservador, **nunca un falso verde**. La UI mantiene tres estados
(verde/rojo/neutro) y no pinta verde un `null`.

`rx`/`tx` son informativos (docker stats) y no influyen en `online`. Su
lectura se movió al task de fondo (`asyncio.to_thread`, timeout 0.8 s, reintento
30 s si falla); antes podía bloquear la primera respuesta hasta 2 s.

### Resultado en régimen estable

Con 500 equipos (100 vivos, 400 caídos), tras dejar trabajar al refresco:

* 25 llamadas seguidas: p95 **8.5 ms**, 100 online / 400 offline / 0 `null`.
* Edad máxima observada de una medición: **10.8 s** < TTL de 12 s.
* En frío, tras `expire_all()`, p95 **812 ms** y los equipos no remedidos
  salen `null`, no verdes.

## 5. Configuración

| Variable | Default | Efecto |
|---|---:|---|
| `NETPULSE_HEALTH_TTL` | 12 s | antigüedad máxima de un estado servido |
| `NETPULSE_HEALTH_TIMEOUT` | 1.0 s | timeout de cada sonda TCP/ICMP |
| `NETPULSE_HEALTH_CONCURRENCY` | 96 | sondas simultáneas del fondo |
| `NETPULSE_HEALTH_INLINE_BUDGET` | 24 | equipos sondeados por petición |
| `NETPULSE_HEALTH_INLINE_DEADLINE` | 0.8 s | espera máxima de la petición |
| `NETPULSE_HEALTH_REFRESH_INTERVAL` | 1.0 s | pausa entre pasadas del fondo |
| `NETPULSE_HEALTH_UNKNOWN_RETRY` | 5 s | reintento de un equipo no medible |
| `NETPULSE_HEALTH_BACKGROUND` | `true` | `false` desactiva el task (tests) |

Si se sube el inventario muy por encima de 500, conviene subir TTL y
concurrencia a la vez. La concurrencia compite con los fds del proceso: en
macOS el límite soft por defecto (256) admite 96 de fondo + 24 inline, pero no
mucho más; en Linux/Docker el límite habitual es 1024+.

## 6. Límites y lo que no se midió

* **Simulación**, no red real: 100 % de los destinos son locales/TEST-NET.
  No hay latencia WAN, pérdida intermitente, AAA lento ni firewalls con
  inspección. Los timeouts dominan el peor caso y sí están representados.
* **Un worker**. Con varios workers, cada uno mantiene su propio estado y su
  propio refresco: el coste de sondas se multiplica por worker. Para 500
  equipos desplegar 4 workers equivale a ~4× sondas. El health seguiría siendo
  honesto (cada worker responde con su medición), pero conviene reducir
  presupuesto/concurrencia o fijar un worker para esta ruta.
* **Procesos concurrentes** contra el mismo `devices.yaml`: el cerrojo es
  intra-proceso. La escritura sigue siendo atómica, pero dos workers pueden
  pisarse una actualización (last-write-wins). No se perdió ningún equipo en
  las pruebas de un worker; para multi-worker haría falta un lock de archivo o
  un almacén único.
* **HTTPS/proxy real** no medido (el banco va por ASGI en proceso). El coste
  añadido de TLS es del servidor, no de estas rutas.
* **DNS**: las 500 entradas del banco son IPs. Muchos FQDN harían que
  `getaddrinfo` (threadpool del loop) sea el cuello; el deadline acota la
  respuesta, pero la cobertura del fondo bajaría.
* **ICMP**: los 500 del banco son SSH/TCP. Los equipos SNMP usan `ping`; sin
  permiso ICMP se devuelven desconocidos (nunca offline). No se midió una
  flota SNMP grande.
* `docker stats` puede no existir o tardar; su fallo no afecta a `online`.
* No se midió arranque en frío del **proceso** (imports), ni memoria con
  500/5000 equipos, ni la UI completa.

## 7. Archivos tocados

* `app/services/device_health.py` (nuevo): estado/TTL, sondas, fondo, tráfico.
* `app/routers/devices.py`: `/health` delega en el servicio; contrato ampliado
  con `checked_at`/`stale`.
* `app/services/inventory_svc.py`: caché tras escritura, libyaml, cerrojo
  RMW; escritura atómica y esquema intactos.
* `app/static/js/views/{dashboard,devices}.js`: `online:null` → estado neutro
  (no rojo, no verde).
* `scripts/perf_devices_bench.py` (nuevo): banco reproducible antes/después.
* `tests/test_devices_perf.py` (nuevo): 12 tests de salud honesta, presupuesto,
  contrato, credenciales, atomicidad y concurrencia.

---

## Anexo — Últimos bloqueos del event loop (tarjeta 13C)

13B sacó `psutil`, `subprocess`, SQLite y `docker stats` de los handlers
`async`. Quedaban seis puntos con el mismo patrón. Ninguno cambia contrato,
código de respuesta ni semántica: solo **dónde** corre el trabajo.

### Qué se corrigió

| Archivo | Antes (en línea en `async`) | Ahora |
|---|---|---|
| `app/routers/sftp.py` upload | `paramiko` connect + `putfo` dentro del handler | `run_blocking` en un **executor propio de 4 hilos** (`NETPULSE_SFTP_WORKERS`) con tope `connect + transfer` |
| `app/routers/sftp.py` list/download | `def` síncrono (threadpool) y `stat` en el handler | `async` + `run_blocking`; el streaming sigue iterándose en el threadpool de Starlette |
| `app/routers/alerts.py` webhook | `maintenance_svc.active_windows()` (JSON) y `notify_event()` (webhooks HTTP, Telegram, Slack, SMTP) en línea | dos `await run_blocking` con topes de 5 s / 60 s; un fallo o timeout no cambia la respuesta |
| `app/routers/remote.py` ingest | `ingest_svc.ingest()` (SQLite + inventario) en línea | `run_blocking` con tope de 20 s; la idempotencia sigue en la clave única y la transacción |
| `app/routers/netbox.py` sync/push | leer y escribir `devices.yaml` (PyYAML) en línea | `run_blocking` con tope de 10 s; escritura atómica intacta |
| `app/main.py` `/api/health` | `len(list_devices())` en cada request (parseo en cache miss) | `to_thread` + caché de 5 s *single-flight* + valor previo servido al instante + calentado en el arranque |

El HTTP a NetBox **no** se movió a un hilo a propósito: usa
`httpx.AsyncClient`, que ya es asíncrono. Moverlo sería un paso atrás.

Nuevo helper `app/utils/async_io.py` (`run_blocking` + `env_timeout`):

- `asyncio.to_thread` (executor acotado por asyncio) o un `Executor` propio.
- Tope con `asyncio.wait` **y no** `wait_for`/`except TimeoutError`: en Python
  ≥3.10 `socket.timeout` **es** `TimeoutError`, así que `wait_for` habría
  convertido un timeout real del equipo en "trabajo demasiado lento" y roto
  la clasificación 401/403/502/504. Si el tope vence se lanza
  `BlockingCallTimeout`; las excepciones de la función se propagan sin
  envolver.

### Configuración (todo por entorno)

| Variable | Default | Efecto |
|---|---|---|
| `NETPULSE_SFTP_CONNECT_TIMEOUT_S` | `30` | kex/SSH (ya existía, sin cambios) |
| `NETPULSE_SFTP_TRANSFER_TIMEOUT_S` | `120` | trabajo tras el kex; el tope duro es `connect + transfer` |
| `NETPULSE_SFTP_WORKERS` | `4` | hilos del executor SFTP (fijo, no crece con la carga) |
| `NETPULSE_ALERT_WINDOWS_TIMEOUT_S` | `5` | leer ventanas de mantenimiento |
| `NETPULSE_ALERT_NOTIFY_TIMEOUT_S` | `60` | una notificación completa (todos sus canales) |
| `NETPULSE_REMOTE_INGEST_TIMEOUT_S` | `20` | una ingesta |
| `NETPULSE_INVENTORY_IO_TIMEOUT_S` | `10` | leer/escribir `devices.yaml` desde NetBox |
| `NETPULSE_HEALTH_COUNT_TTL_S` | `5` | vida de la caché del contador de health |
| `NETPULSE_HEALTH_COUNT_TIMEOUT_S` | `2` | tope de la lectura del inventario para health |

### Qué se midió después

Método: `scripts/perf_async_bench.py --mode handlers-demo`. Monta los routers
reales en una app en memoria, sustituye **la red** por esperas de 300 ms
(`--slow-ms`) y ejecuta el **mismo** trabajo con el patrón anterior (en línea)
y con el actual. El hueco máximo del event loop se mide con un latido de 20 ms.
El escenario de health usa un `devices.yaml` real de **500 equipos**
(`--devices 500`) y fuerza un *cache miss* en cada ronda.

| escenario (7 rondas) | patrón | request p50 | bloqueo máximo del loop |
|---|---|---|---|
| subida SFTP (putfo 300 ms) | en línea | 305,3 ms | **296,4 ms** |
| subida SFTP (putfo 300 ms) | `run_blocking` | 306,9 ms | **13,4 ms** (rondas típicas 1–3 ms) |
| webhook Alertmanager | en línea | 307,0 ms | **300,1 ms** |
| webhook Alertmanager | `run_blocking` | 311,4 ms | **8,6 ms** |
| `/api/health` cache miss (500 equipos) | en línea | 74,4 ms | **73,9 ms** |
| `/api/health` cache miss (500 equipos) | `to_thread` + caché | 60,3 ms | **47,0 ms** (techo del GIL, ver límites) |
| `/api/health` caché caliente | — | 0,8 ms | **1,7 ms** |

API real (`--mode health`: uvicorn, 50 peticiones secuenciales, inventario de
laboratorio de 5 equipos):

| métrica | valor |
|---|---|
| `GET /api/health` p50 | **2,6 ms** |
| p95 | **7,3 ms** |
| máximo | **10,6 ms** |
| códigos | solo `200` |

La subida SFTP tarda lo mismo que antes (el trabajo sigue existiendo): lo que
cambia es que ya no se hace en el loop. Las 20 peticiones concurrentes al
dashboard y el WebSocket de terminal dejan de esperar 300 ms.

### Límites conocidos (honestidad)

- **Cancelar la espera no mata el hilo.** Si vence el tope, se responde 504 y
  la transferencia sigue hasta que su socket timeout de paramiko la corta. Por
  eso los topes son holgados: son una red de seguridad, no el corte normal.
- **El executor SFTP es propio y fijo** (4 hilos): una transferencia larga no
  consume el pool compartido de health/auditoría, pero 4 subidas simultáneas
  hacen cola la quinta. Es deliberado (no hilos sin límite).
- **El parseo YAML es CPU-bound y retiene el GIL.** libyaml (`CSafeLoader`) es
  una llamada C: con 500 equipos tarda ~50–75 ms y, aunque corra en un hilo,
  el loop puede quedarse ~45 ms esperando el GIL. Un hilo no arregla trabajo
  CPU-bound; lo que sí lo arregla es no hacerlo en régimen normal: health
  cachea 5 s, calienta el contador en el arranque y sirve el valor anterior
  mientras refresca. Con 5000 equipos el parseo ronda 600 ms y el hueco del
  GIL sería mayor: haría falta un subproceso o un inventario incremental.
- **`/api/health` puede dar un `devices_configured` de hasta 5 s de antigüedad**
  (`NETPULSE_HEALTH_COUNT_TTL_S`). Es el intercambio por responder en ~1 ms;
  el número real se refresca en segundo plano.
- **Las notificaciones se envían en serie**, igual que antes: N alertas ×
  notificación es tiempo aditivo (no bloquea el loop, pero el webhook tarda).
  Si una notificación supera su tope, se registra y se sigue; el hilo puede
  terminar enviándola igualmente (mejor duplicar que silenciar).
- **Timeout de escritura de `devices.yaml`**: la escritura es atómica y nunca
  deja el YAML a medias, pero si el tope vence el 504 lo dice explícitamente:
  no se puede confirmar si llegó a aplicarse.
- **`requires_scope` sigue leyendo el inventario en el loop** antes de abrir
  SSH (una entrada cacheada: stat + O(1)). Moverlo a un hilo cambiaría el
  orden de errores (RBAC antes de consumir el cuerpo) y pertenece a auth, fuera
  del alcance de esta tarjeta.
- **NetBox push es secuencial**: cada POST espera al anterior. No bloquea el
  loop, pero la latencia es la suma. No se paralelizó para no cambiar el orden
  de creación ni el recuento de errores.

### Reproducir

```bash
# SFTP upload + webhook + health con inventario real de 500 equipos
PYTHONPATH=. .venv/bin/python scripts/perf_async_bench.py --mode handlers-demo \
    --rounds 7 --devices 500 --json /tmp/bench-13c.json

# Health real contra uvicorn (50 muestras)
PYTHONPATH=. .venv/bin/python scripts/perf_async_bench.py --mode health \
    --rounds 5 --json /tmp/bench-health.json

# Suites
PYTHONPATH=. .venv/bin/pytest tests/ -q
```

### Tests

`tests/test_event_loop_13c.py` (22 tests): SFTP listar/subir/descargar
(incluye timeout 504 y EACCES 403), webhook (lento, fallo, ventanas lentas,
supresión en mantenimiento), ingesta (lenta, 503 del store, timeout 504),
NetBox (lectura/escritura de YAML y timeout), health (lectura lenta, caché
caliente <100 ms, inventario roto) y el helper (`run_blocking` no enmascara
`socket.timeout` y libera la sesión SSH que llega tarde).

Suite completa: **468 passed** (446 previos + 22 nuevos).
