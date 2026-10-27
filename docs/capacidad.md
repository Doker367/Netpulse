# Capacidad del almacén SQLite de colectores remotos

Medición hecha el 2026-10-24/25 sobre el camino **real de producción**:
`POST /api/remote/ingest` con API keys `kind=collector` (el mismo endpoint que
usa `app/remote_collector/sync.py`). No se insertó nada por la puerta de atrás
ni se tocó el inventario.

> **Los datos son una simulación.** Todos los `device_id` llevan el prefijo
> `sim-` y no representan equipos reales. Las cifras de esta página son
> medidas del almacén, no del inventario del NOC.

## 1. Qué se midió y qué no

Se midió:

- Latencia HTTP de ingesta (round-trip en el cliente) por tanda: p50/p95/p99.
- Filas aceptadas por tick y proyección a filas/día con sonda de 60 s.
- Tamaño real del archivo SQLite y bytes por fila (tabla + índices).
- Tiempo de las consultas SQL exactas que usan la vista **Sites** y el
  histórico del panel (20 iteraciones por consulta, `sqlite3` de Python).
- Para la tanda de 500: tiempo de las rutas HTTP del panel
  (`/api/remote/sites`, dispositivos e histórico) con el mismo JWT de admin.
- Errores, duplicados y rechazos del contrato de ingesta.

No se midió (ver §7): comportamiento a 30/90 días acumulados, poda diaria,
varios procesos API simultáneos, varios colectores concurrentes, red real/TLS,
discos lentos ni ventanas de backup.

## 2. Montaje

| Elemento | Valor |
|---|---|
| Host | macOS 26.6.2, Apple M1, 16 GiB RAM, SSD (79 % ocupado) |
| Python | 3.12.14 |
| SQLite | 3.53.4 (WAL, `synchronous=NORMAL`, `busy_timeout=5000`) |
| API | FastAPI 0.143.3 / Uvicorn 0.54.0, **un solo worker**, `127.0.0.1` |
| DB | `NETPULSE_REMOTE_DB=/tmp/np-w10b-cap/remote-<tanda>.db` (fuera del repo) |
| Colectores | 5 sites × 1 colector cada uno, claves temporales `kind=collector` |
| Métricas de fondo | `NETPULSE_METRICS_ENABLED=true`, `NETPULSE_METRICS_INTERVAL=0` |
| Rate limit | 90 peticiones/min (el endpoint admite 120/min por IP) |

`NETPULSE_METRICS_ENABLED=true` se pidió explícitamente; el poller de fondo se
desactivó con intervalo `0` para que su sondeo no contaminara la medición. La
ingesta, el middleware de métricas y la auditoría sí estuvieron activos.

Cada tanda arrancó con DB nueva, 5 claves de colector creadas por
`POST /api/security/api-keys` (usuario admin temporal, borrado al terminar) y
el generador `scripts/load_generator.py`. Las claves y la DB quedaron en
`/tmp/np-w10b-cap/`; el repo no contiene datos de la prueba.

### Ritmo: replay acelerado, no sondeo real

El generador mantiene el reloj sintético de los `ts` a **60 s por tick** (la
sonda de producción) pero comprime el envío: cada tick de pared dura 3.33 s.
Por eso las tres tandas corren a **18× el ritmo de producción** y 300 s de
prueba equivalen a **90 minutos** de datos sintéticos.

| Tanda | Dispositivos | Sites | Muestras/tick | Tick de pared | Ritmo medido | Ritmo de producción | Factor |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | 100 | 5 | 300 | 3.33 s | 90 muestras/s | 5 muestras/s | 18× |
| 2 | 300 | 5 | 900 | 3.33 s | 270 muestras/s | 15 muestras/s | 18× |
| 3 | 500 | 5 | 1 500 | 3.33 s | 450 muestras/s | 25 muestras/s | 18× |

Cada tanda: 90 ticks, 450 peticiones (5 sites × 1 lote de 300 muestras por
tick), 0 duplicados, 0 rechazos, 0 errores HTTP, 0 respuestas 429.
Semillas `1100`, `2200`, `3300` (deterministas). El modelo de datos está en el
docstring del generador: disponibilidad con 0.5 % de caídas, latencia
log-normal por equipo y pérdida ocasional.

## 3. Ingesta medida

Latencia round-trip del cliente al `POST /api/remote/ingest` (450 peticiones
por tanda):

| Tanda | Filas aceptadas | p50 | p95 | p99 | máx | Errores |
|---|---:|---:|---:|---:|---:|---:|
| 100 devices | 26 920 | 8.40 ms | 12.24 ms | 15.28 ms | 43.02 ms | 0 |
| 300 devices | 80 720 | 15.20 ms | 32.33 ms | 58.39 ms | 81.31 ms | 0 |
| 500 devices | 134 620 | 22.35 ms | 38.49 ms | 46.06 ms | 84.72 ms | 0 |

Cada petición de la tanda de 500 lleva 300 muestras; la latencia incluye
validación Pydantic, lectura del inventario, `BEGIN IMMEDIATE`, inserción
idempotente, actualización de `remote_device_state` y respuesta JSON. A 18×
producción, el peor p99 es 46 ms: el límite práctico de esta medición no fue
SQLite sino el presupuesto de 90 peticiones/min del rate limiter.

## 4. Almacenamiento

| Tanda | Filas en DB tras 90 min sintéticos | Tamaño DB | Bytes/fila | Filas/día proyectadas | MB/día proyectados |
|---|---:|---:|---:|---:|---:|
| 100 | 26 920 | 10.35 MiB | 403.2 | 430 720 | 173.7 MB |
| 300 | 80 720 | 31.20 MiB | 405.3 | 1 291 520 | 523.4 MB |
| 500 | 134 620 | 52.16 MiB | 406.3 | 2 153 920 | 875.1 MB |

- `Bytes/fila` = tamaño real del archivo (incluye WAL/SHM al cierre) ÷ filas,
  medido con los índices ya creados. Es estable (403–406 B) entre 27 k y 135 k
  filas.
- `Filas/día` = filas realmente aceptadas por tick × 1 440 ticks/día. Es una
  **proyección desde el conteo medido por tick**, con la merma real por
  disponibilidad (≈0.3 %). No se ejecutaron 24 h.
- `MB/día` = filas/día × bytes/fila medidos.

Proyección acumulada (solo si se mantiene el ritmo y **sin** poda):

| Tanda | 30 días | 90 días |
|---|---:|---:|
| 100 | 12.9 M filas / 5.2 GB | 38.8 M filas / 15.6 GB |
| 300 | 38.7 M filas / 15.7 GB | 116.2 M filas / 47.1 GB |
| 500 | 64.6 M filas / 26.2 GB | 193.9 M filas / 78.8 GB |

La retención por defecto es **30 días** (`NETPULSE_REMOTE_RETENTION_DAYS=30`),
así que el estado estacionario real es la columna de 30 días. La de 90 días
solo aplica si se sube la retención.

## 5. Consultas del panel (medidas en la DB real)

La vista *Monitoring/overview* del panel (`/api/monitoring/overview`) consulta
Prometheus y no toca esta DB. La vista que sí lee el almacén SQLite es
**Sites** (`/api/remote/sites`, dispositivos e histórico); son exactamente las
consultas cronometradas aquí.

SQL copiado de `app/services/remote/query_svc.py`; 20 iteraciones por consulta,
cronometradas con `time.perf_counter()` en `sqlite3` (equivalente a `.timer`).
Se usó `scripts/db_capacity_probe.py`.

| Consulta | 100 devices | 300 devices | 500 devices |
|---|---:|---:|---:|
| `SELECT * FROM remote_collector ...` (lista de sites) | 0.016 / 0.022 ms | 0.018 / 0.035 ms | 0.017 / 0.020 ms |
| `remote_device_state GROUP BY site_id` (contadores) | 0.031 / 0.032 ms | 0.090 / 0.132 ms | 0.139 / 0.142 ms |
| `remote_device_state WHERE site_id=?` (dispositivos) | 0.033 / 0.037 ms | 0.103 / 0.122 ms | 0.167 / 0.171 ms |
| Histórico 6 h de un equipo (índice `site,device,ts`) | 0.316 / 0.356 ms | 0.320 / 0.344 ms | 0.320 / 0.333 ms |

Formato: **p50 / p95** en milisegundos. El histórico devuelve 270 puntos
(90 ticks × 3 métricas) en todas las tandas; usa el índice y no escanea la
tabla.

Rutas HTTP del panel con la DB de 500 (20 peticiones cada una, JWT admin):

| Ruta | Cuerpo | p50 | p95 |
|---|---:|---:|---:|
| `GET /api/remote/sites` | 4.1 KB | 2.72 ms | 5.47 ms |
| `GET /api/remote/sites/site-1/devices` | 43.2 KB | 5.75 ms | 6.91 ms |
| `GET .../sim-site-1-0001/history?hours=6` | 16.1 KB | 4.30 ms | 6.94 ms |

## 6. Lectura honesta: ¿cuándo duele?

1. **La latencia de ingesta no es el problema a 500 devices.** Incluso a 18×
   producción (450 muestras/s) el p99 es 46 ms y no hubo 429, timeouts ni
   rechazos. La máquina iba holgada; el techo medido fue el rate limiter.
2. **El coste que crece es el archivo, no las consultas del panel.** La vista
   Sites y la lista de dispositivos leen `remote_device_state` (una fila por
   equipo); el histórico usa el índice y devuelve solo la ventana pedida. A
   500 devices siguen por debajo de 0.35 ms en SQL y 7 ms por HTTP. Con 30
   días de retención no hay motivo medido para que empeoren.
3. **El primer dolor será la poda y el disco.** 500 devices generan 875 MB/día
   y ~26 GB a 30 días. `prune_samples()` borra en lotes de 10 000 con
   `DELETE`, lo que genera páginas libres y WAL; no se midió. En un disco
   compartido o con backups, 26 GB y 2.15 M borrados/día es lo que hay que
   vigilar, no el `SELECT` del panel.
4. **El lock de escritura es por proceso.** `store.py` serializa con
   `_WRITE_LOCK` y asume un solo worker Uvicorn. Con varios workers o varios
   colectores que entren a la vez, SQLite sigue funcionando por WAL +
   `busy_timeout=5s`, pero la cola no es justa. Esto **no se midió** y es el
   riesgo arquitectónico real antes que el volumen.
5. **PostgreSQL: recomendación preliminar, no implementar aún.** Con los
   números medidos, SQLite es suficiente para ≤500 devices y 30 días en un
   solo nodo. Postgres pasaría a justificarse si se cumple alguno de estos
   umbrales: >1 000 devices, retención >60–90 días (a 500 devices, 90 días son
   ~79 GB), varios workers/instancias de API, varios colectores concurrentes
   con escritura sostenida, o requisitos de HA/backup en caliente. Antes de
   migrar conviene medir poda + escrituras concurrentes; puede que el cuello
   sea la poda y no el motor.

## 7. No medible en esta ronda

| Aspecto | Motivo |
|---|---|
| Consultas a 30/90 días acumulados | Requiere 64.6 M / 193.9 M filas reales; aquí la DB mayor tiene 134 620. Los tamaños a 30/90 días son proyección, no medición. |
| Poda diaria y `VACUUM`/fragmentación | La prueba dura 5 min; la poda corre cada hora y no llegó a ejecutarse con volumen. **Medido después en §9** (tarjeta 12E): poda de régimen, poda atrasada de 7 días y páginas libres. `VACUUM` sigue sin medirse. |
| Varios colectores concurrentes | El generador envía 5 peticiones secuenciales por tick desde un cliente. No se probó el lock con N escritores simultáneos. **Medido después en §9**: 1 y 2 procesos escritores concurrentes con la poda en marcha. |
| Varios workers Uvicorn | Se usó un solo proceso, que es el supuesto documentado de `store.py`. |
| Red real, TLS y latencia WAN | Todo fue loopback; no incluye RTT ni TLS de los sites. |
| Discos lentos / NFS / contenedor | Solo el SSD local del host de prueba. |
| Backup/restore de 26 GB | Fuera de alcance. |
| CPU y memoria del servidor durante la prueba | No se instrumentó; solo la latencia y los errores del cliente. |

## 8. Reproducción

```bash
# 1. API con DB y claves temporales (nunca la DB del repo)
export NETPULSE_METRICS_ENABLED=true NETPULSE_METRICS_INTERVAL=0
export NETPULSE_REMOTE_DB=/tmp/np-w10b-cap/remote-500.db
uvicorn app.main:app --host 127.0.0.1 --port 54416

# 2. Claves de colector (una por site) vía admin
#    /tmp/np-w10b-cap/keys.json = {"site-1": {"collector_id": "...", "api_key": "np_..."}, ...}

# 3. Carga
PYTHONPATH=. python scripts/load_generator.py \
  --url http://127.0.0.1:54416 --keys-file /tmp/np-w10b-cap/keys.json \
  --devices 500 --sites 5 --rate 450 --duration 300 --probe-interval 60 \
  --seed 3300 --max-requests-per-minute 90 \
  --report-json /tmp/np-w10b-cap/run-500/generator.json

# 4. Consultas del panel sobre la DB resultante
python scripts/db_capacity_probe.py /tmp/np-w10b-cap/remote-500.db --iterations 20
```

`scripts/load_generator.py --help` deja claro que es una simulación y que los
`device_id` llevan prefijo `sim-`. Para producción no debe usarse contra el
inventario real.

## 9. Poda y concurrencia (medido)

Medición de la **tarjeta 12E** (2026-10-25), con `scripts/capacity_prune_bench.py`
sobre una DB **temporal** (`tempfile.mkdtemp()`, nunca la del repo). Usa el
esquema real, los PRAGMA de producción (`WAL`, `synchronous=NORMAL`,
`busy_timeout=5000`) y la **función real** `store.prune_samples`, envuelta en
`store.transaction()` (`BEGIN IMMEDIATE` + commit) igual que
`ingest_svc._maybe_prune`.

> **Los datos son sintéticos** (`bench-dev-*`, reloj fijo). Las inserciones usan
> el mismo SQL `INSERT … ON CONFLICT DO NOTHING` que la ingesta y lotes de 300
> filas con conexión corta, pero **no** pasan por HTTP/Pydantic/JWT. Los
> tiempos de contención no son deterministas; el volumen y los datos sí.

### 9.1 Montaje y tamaño de la prueba

| Elemento | Valor |
|---|---|
| Host | macOS 26.6.2, MacBook Pro (Apple M1, `MacBookPro17,1`), 16 GiB RAM, SSD (`Solid State: Yes`), 68 % ocupado |
| Python / SQLite | 3.12.14 / 3.53.4 |
| Volumen sembrado | 40 equipos × 30 días × 3 métricas × 1 440 sondas/día = **5 184 000 filas** |
| Reloj sintético | 2026-09-01 → 2026-10-01 (30 días), sonda cada 60 s |
| Retención usada | 30 días (`NETPULSE_REMOTE_RETENTION_DAYS=30`) |
| Poda medida | corte a +7 días del final → borra los **primeros 7 días** (1 209 600 filas, 23.3 %) |
| Semilla | 1205 |

No se sembraron 500 equipos × 30 días (~64.8 M filas, ~25 GB) porque la siembra
sola habría superado el presupuesto de 10 min de la tarjeta. Se usó el mismo
volumen **por equipo y día** que producción (4 320 filas/equipo/día, medido en
§3) y se redujo el número de equipos; las cifras a 500 equipos de §9.4 son
**extrapolación lineal**, marcada como tal.

### 9.2 Resultados medidos

Siembra (5 184 000 filas, una transacción por 5 000 filas):

| Métrica | Valor |
|---|---:|
| Tiempo de siembra | 46.8–47.1 s |
| Filas/s | 110 000–110 726 |
| Tamaño final (DB+WAL+SHM, con `wal_checkpoint(TRUNCATE)`) | 1 928.69 MiB (1.88 GiB) |
| Bytes por fila (con los 2 índices) | 390.1 B |

Poda (cada escenario sobre una **copia** de la misma DB, mismo corte):

| Escenario | Filas borradas | Poda | Filas/s de poda | Escritores | Filas/s escritor(es) | `database is locked` | Error de poda |
|---|---:|---:|---:|---:|---:|---:|---|
| Régimen (+1 h de retención) | 7 200 | **0.15 s** | 48 540 | 0 | — | 0 | ninguno |
| Atrasada 7 días, sola | 1 209 600 | **7.38 s** | 163 984 | 0 | — | 0 | ninguno |
| Atrasada 7 días + **1** escritor | 1 209 600 | **7.63 s** | 158 434 | 812 100 filas / 25 s | 32 479 | **1** (lote perdido, reintentado) | ninguno |
| Atrasada 7 días + **2** escritores | 1 209 600 | **7.63 s** | 158 598 | 1 172 400 filas / 25 s | 46 882 (23 750 + 23 132) | **2** (uno por escritor) | ninguno |

Efecto en disco (lo que de verdad importa tras un `DELETE` masivo):

| Escenario | Tamaño antes | Con WAL tras la poda | Tras checkpoint | `page_count` | Páginas libres |
|---|---:|---:|---:|---:|---:|
| Poda sola | 1 928.69 MiB | 1 928.69 MiB | 1 928.69 MiB | 493 744 → 493 744 | 0 → 115 091 (**23.3 %**) |
| + 1 escritor | 1 938.65 MiB | 1 938.72 MiB | 1 938.72 MiB | 496 287 → 496 313 | 0 → 92 143 (18.6 %) |
| + 2 escritores | 1 946.62 MiB | 2 502.94 MiB | 1 945.23 MiB | 497 699 → 497 699 | 0 → 88 266 (17.7 %) |

- **El archivo no encoge.** `prune_samples` borra filas pero no hace `VACUUM`:
  el tamaño quedó idéntico y el espacio pasó a la lista de páginas libres
  (23.3 % con la poda de 7 días). Ese hueco se **reutiliza** en inserciones
  posteriores; el disco solo se recupera con `VACUUM` (no medido, y no
  recomendable cada hora).
- **El WAL crece con escritores activos.** En el escenario de 2 escritores el
  WAL llegó a +556 MiB mientras la poda (7.6 s) y los dos escritores se
  solapaban; el checkpoint posterior lo devolvió a 1 945 MiB. Hay que prever
  ese espacio extra, no solo el tamaño de la DB.

### 9.3 Lectura honesta

1. **La poda de régimen es barata.** 7 200 filas en 0.15 s a este volumen. Con
   la poda cada hora (`NETPULSE_REMOTE_PRUNE_INTERVAL_S=3600`), en régimen solo
   se borra 1 h de datos: en esta máquina, por debajo de 1 s para 40 equipos.
2. **El riesgo no es el throughput, es la duración del lock.** `prune_samples`
   borra en lotes de 10 000 **dentro de una sola transacción** (`BEGIN
   IMMEDIATE` … commit en `store.transaction()`): mientras poda, nadie más
   escribe. Con una poda atrasada de 7.6 s y `busy_timeout=5000`, cada escritor
   perdió **exactamente un lote** con `database is locked` (y lo reintentó). No
   hubo tormenta de errores ni corrupción: 1 y 2 escritores siguieron
   escribiendo a 32 k y 47 k filas/s agregadas. Si se acumulan días de poda
   atrasada **mientras** hay ingesta, habrá lotes rechazados; la mitigación es
   trocear el commit (o podar fuera de la ventana de ingesta), no cambiar de
   motor.
3. **Dos escritores no hunden SQLite.** WAL + `busy_timeout` serializan las
   escrituras y el agregado incluso subió (46.9 k filas/s con 2 procesos frente
   a 32.5 k con 1). El techo medido aquí es el coste por transacción del banco
   de pruebas (conexión nueva + lote de 300), no el motor.
4. **Nada de esto invalida la recomendación de Postgres de §6**, pero tampoco
   la adelanta: sigue sin haber un motivo medido para migrar a ≤500 equipos.

### 9.4 Proyección a 500 equipos (extrapolación, no medición)

Con 4 320 filas/equipo/día y 390.1 B/fila:

| Concepto | 500 equipos, 30 días | Cómo se obtiene |
|---|---:|---|
| Filas retenidas | 64.8 M | 500 × 4 320 × 30 (proyección de §4) |
| Tamaño | ~25.2 GB | 64.8 M × 390.1 B (bytes/fila medidos aquí) |
| Poda de régimen (1 h) | ~90 000 filas ≈ **0.5–0.6 s** | 90 k ÷ 160 k filas/s medidos |
| Poda atrasada de 7 días | ~15.1 M filas ≈ **95 s** | 15.1 M ÷ 160 k filas/s, lineal |

Las dos últimas filas son **cuentas**, no cronómetros: no se ejecutaron 65 M de
filas ni una poda de 15 M. La extrapolación es razonable porque el borrado usa
el índice `ts` y el coste medido fue lineal entre 7 200 y 1 209 600 filas, pero
no está verificada a esa escala.

### 9.5 ¿Sigue siendo NO Postgres para ≤500?

**Sí, con los números en la mano.** Para ≤500 equipos, 30 días, un solo nodo y
un solo worker Uvicorn, SQLite cumple: ingestión con p99 < 50 ms (§3), consultas
del panel < 7 ms (§5), poda de régimen < 1 s (proyección) y escritores
concurrentes sin errores sostenidos. Los dos riesgos reales son operativos, no
de motor: (a) la poda atrasada mantiene el lock de escritura varios segundos y
puede costar lotes si coincide con ingesta; (b) el archivo no se encoge sin
`VACUUM`.

Postgres pasaría a justificarse si se cumple **alguno** de estos umbrales:

- más de **1 000–1 500 equipos** (o retención > 60–90 días: a 500 equipos,
  90 días son ~76 GB y la poda atrasada se acerca al minuto);
- **más de un worker/instancia** de API escribiendo, o varios colectores
  concurrentes con escritura sostenida (el `_WRITE_LOCK` de `store.py` es por
  proceso);
- necesidad de **HA, backups en caliente o réplica de lectura**;
- podas atrasadas frecuentes (> 5 s) solapadas con ingesta, si trocear el
  commit no basta.

Mientras eso no ocurra, migrar añade un servicio, backups y credenciales sin
resolver ningún cuello medido.

### 9.6 Límites de esta medición

- Inserciones por SQL directo, **no** por `POST /api/remote/ingest` (sin
  Pydantic, JWT, métricas ni rate limiter).
- Un solo host y un solo SSD local; nada de NFS, contenedores con I/O limitado
  ni discos lentos.
- No se midió `VACUUM`, ni backup/restore de 1.9 GB, ni el crecimiento del WAL
  durante días.
- 40 equipos, no 500: el volumen por equipo/día sí es el de producción.
- La contención exacta (1 o 2 locks) varía entre ejecuciones; el orden de
  magnitud y el mecanismo (lock de escritura durante toda la poda) sí se
  reproducen.

### 9.7 Reproducción

```bash
# DB temporal, 40 equipos × 30 días, poda de 7 días, 1 y 2 escritores
PYTHONPATH=. python scripts/capacity_prune_bench.py --json /tmp/bench.json

# Otra escala (siempre fuera del repo; el script aborta si la ruta cae dentro)
PYTHONPATH=. python scripts/capacity_prune_bench.py --devices 80 --keep
```

`--keep` conserva el directorio temporal para inspeccionarlo; sin él se borra
todo al terminar. El script imprime las tablas y, con `--json`, el detalle por
escenario (incluidos los resultados de cada proceso escritor).
