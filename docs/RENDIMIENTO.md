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
