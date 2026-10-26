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
