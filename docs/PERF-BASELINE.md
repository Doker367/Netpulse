# Línea base de rendimiento

Este banco mide rutas HTTP reales de NetPulse con Uvicorn y deja un JSON apto
para comparar el mismo escenario después de cada merge. La línea base incluida
corresponde al commit `d865c0ebc5d4d8121241c27d9e21f1246d6549fe`
(`d865c0e`) y a 500 equipos.

## Ejecución reproducible

Desde la raíz del worktree:

```bash
PYTHONPATH=. /Users/doker/netpulse-noc/.venv/bin/python \
  scripts/perf_bench.py \
  --devices 500 \
  --out docs/perf/baseline.json \
  --label baseline-d865c0e
```

Sin `--base-url`, el script:

1. reserva un puerto cerrado y genera `config/devices.yaml` con `N` equipos
   `127.0.0.1:<puerto-cerrado>`;
2. genera un usuario administrador aleatorio temporal;
3. respalda cualquier configuración local, la base de auditoría y el historial
   de métricas que ya existan;
4. arranca Uvicorn en puertos libres del loopback;
5. detiene todos los procesos y restaura o elimina los archivos temporales,
   también si recibe una interrupción.

No toca el repo principal. Las escrituras `PUT` se hacen sobre el inventario
temporal y reenvían la descripción actual, por lo que ejercitan la escritura
real sin introducir un cambio lógico.

Para medir una instancia ya levantada:

```bash
NETPULSE_SMOKE_USER=admin NETPULSE_SMOKE_PASSWORD='…' \
PYTHONPATH=. /Users/doker/netpulse-noc/.venv/bin/python \
  scripts/perf_bench.py --base-url http://127.0.0.1:8082 \
  --devices 500 --out docs/perf/despues.json --label despues-13A
```

En modo `--base-url` el script no cambia equipos para forzar `docker stats` y
no puede garantizar que la primera consulta de health tenga la caché fría; el
JSON lo marca explícitamente. Se requiere un usuario `admin` porque se mide
`PUT /api/devices/{id}`.

## Qué mide

Los tiempos son de extremo a extremo desde el cliente local y se expresan en
milisegundos. Los percentiles usan *nearest rank*.

- `health_simple_cold` y `health_full_cold`: cinco muestras; cada muestra usa
  un proceso Uvicorn nuevo para garantizar caché fría sin esperar el TTL.
- `health_simple_hot` y `health_full_hot`: un warm-up y 20 repeticiones contra
  la caché caliente de `GET /api/devices/health`, con y sin `simple=true`.
- `devices_list`: un warm-up y 20 `GET /api/devices` autenticados.
- `device_put_10`: un warm-up y una escritura real sobre cada uno de 10
  equipos; se reporta tiempo por request.
- `event_loop_health_during_docker_stats`: fuerza una entrada online temporal
  para que el health completo ejecute `docker stats`; mientras corre, lanza 20
  `GET /api/health` concurrentes y mide su latencia. Usa direcciones RFC 2544
  distintas en `X-Forwarded-For` para que el rate limit no convierta la mitad
  de la muestra en HTTP 429. Si el daemon Docker no está disponible, queda
  `SKIP`, nunca se sintetiza un resultado.
- `dashboard` y `core_js`: un warm-up y 20 lecturas de `/` y
  `/static/js/core.js` para detectar degradación del frontend estático.

Cada métrica conserva `n`, p50, p95, p99, mínimo, máximo y códigos HTTP en el
JSON. Un fallo de medición queda como `ERROR`; una dependencia ausente que la
tarjeta permite omitir queda como `SKIP`.

## Línea base real (`d865c0e`)

Medida el 26 de octubre de 2026 en un MacBook Pro (`MacBookPro17,1`, Apple M1,
16 GiB), macOS 26.6.2 arm64 y Python 3.12.14. El artefacto completo es
`docs/perf/baseline.json`.

| Métrica | Estado | n | p50 ms | p95 ms | p99 ms |
|---|---:|---:|---:|---:|---:|
| health simple, fría | OK | 5 | 18.698 | 21.092 | 21.092 |
| health simple, caliente | OK | 20 | 2.310 | 2.740 | 3.289 |
| health completo, fría | OK | 5 | 21.808 | 23.676 | 23.676 |
| health completo, caliente | OK | 20 | 4.597 | 5.172 | 5.370 |
| `GET /api/devices` | OK | 20 | 3.642 | 4.482 | 4.493 |
| `PUT` sobre 10 equipos | OK | 10 | 315.536 | 323.309 | 323.309 |
| `GET /` | OK | 20 | 1.969 | 2.393 | 2.447 |
| `GET /static/js/core.js` | OK | 20 | 1.843 | 2.112 | 3.106 |
| health concurrente durante `docker stats` | SKIP | 0 | — | — | — |

El check de event loop no se validó en esta corrida: la CLI Docker estaba
instalada, pero el daemon de Colima no estaba disponible. El JSON conserva el
error real de `docker info`. Los demás requests contabilizados devolvieron HTTP
200.

El cuello dominante observado es la reescritura de inventario en cada `PUT`:
p95 **323.309 ms** con 500 equipos. El health frío completo quedó en p95
**23.676 ms** y, con caché caliente, en **5.172 ms**.

## Comparar después de 13A/13B

Conserve `docs/perf/baseline.json` y escriba el run nuevo en otro archivo:

```bash
PYTHONPATH=. /Users/doker/netpulse-noc/.venv/bin/python \
  scripts/perf_bench.py \
  --devices 500 \
  --label despues-13A-13B \
  --out docs/perf/despues-13A-13B.json \
  --compare docs/perf/baseline.json
```

La comparación empareja métricas por nombre y usa p95. Una métrica queda
marcada como regresión cuando su p95 es **más de 30 % peor** que la referencia;
el proceso termina con código 1. Un `SKIP`/`ERROR`, una métrica ausente o un
`N` distinto se informa y no se convierte en un número inventado. Sin
regresiones el código es 0; errores de medición producen código 2.

Para atribuir una mejora, ejecute el mismo comando, `N` y máquina con el sistema
lo más ocioso posible. Compare primero tras 13A y repita tras 13B, guardando un
JSON distinto para cada commit.
