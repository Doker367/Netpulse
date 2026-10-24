# Piloto en vivo del colector remoto — site-piloto

Fecha: 2026-10-23 (CDMX, UTC−06); rama `feat/colector-piloto`. **Prueba real en localhost, sin mocks**, con central `127.0.0.1:8199` y buffer/DB independientes bajo `runtime/piloto/` (ignorado por Git). No se tocó el backend central. Los horarios de las respuestas JSON son UTC. Credencial de colector revocada al finalizar la prueba; no se publica ninguna clave.

## Guía copiable para desplegar un nodo real

Requisitos: Linux con Python 3.11+, `ping` si se usa ICMP y salida HTTPS hacia el central; un único colector activo por site. En el nodo, desde una copia fijada del código, instalar en **un venv propio**, no en el del servidor central:

```bash
cd /opt/netpulse-collector
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-collector.txt
sudo install -d -o netpulse-collector -g netpulse-collector -m 0750 /var/lib/netpulse-collector
sudo install -d -m 0750 /etc/netpulse
# Basarse en docs/collector-config.example.yaml; poner site_id/collector_id exactos
# de la clave emitida por un admin, devices reales y buffer.path persistente.
# EN PRODUCCIÓN: central.base_url=https://..., verify_tls: true; ca_file si aplica.
# Nunca poner la API key en collector.yaml ni en comandos visibles en ps.
# Guardar NETPULSE_COLLECTOR_API_KEY=np_... en /etc/netpulse/collector.env
# con modo 0640 root:netpulse-collector (o api_key_file con modo 0600).
```

Un admin emite `POST /api/security/api-keys` con `{"name":"collector-site-01","kind":"collector","site_id":"site-id","collector_id":"collector-site-01"}` y custodia la clave devuelta una sola vez. Validación antes de activar el servicio, con el entorno cargado de forma segura:

```bash
cd /opt/netpulse-collector
# En sesión protegida con NETPULSE_COLLECTOR_API_KEY ya exportada:
.venv/bin/python -m app.remote_collector --config /etc/netpulse/collector.yaml --check
.venv/bin/python -m app.remote_collector --config /etc/netpulse/collector.yaml --dry-run
.venv/bin/python -m app.remote_collector --config /etc/netpulse/collector.yaml --once
# Instalación del ejemplo deploy/netpulse-collector.service (revisar rutas y usuario):
sudo install -m 0644 deploy/netpulse-collector.service /etc/systemd/system/netpulse-collector.service
sudo systemctl daemon-reload
sudo systemctl enable --now netpulse-collector
sudo systemctl status netpulse-collector
.venv/bin/python -m app.remote_collector --config /etc/netpulse/collector.yaml --status
```

**Validado localmente:** `--check`, `--dry-run`, `--once`, servicio continuo, estado y recuperación, utilizando el venv ya existente del servidor (httpx 0.28.1, PyYAML 6.0.3) sin instalar allí paquetes. La secuencia `python3 -m venv`/`pip install` para nodo nuevo es **instrucción de despliegue, no ejecutada en este piloto** (las dependencias ya existían). **No validados aquí:** systemd/Linux real, CA/TLS real, ICMP/ping en nodo real, permisos/almacenamiento del servicio en Linux, pérdida por saturación de buffer, reloj desviado, despliegue multi-site. `verify_tls: false` se usó **solo** para HTTP en localhost; en producción es obligatorio HTTPS y `verify_tls: true`.

## Preparación y resultados observados

Se leyó el contrato en `docs/handoffs/collectors-site.md`, `PRODUCT.md`, `docs/collector-config.example.yaml` y el código del CLI/config/sync antes de crear la configuración. La config local, ignorada, fue `runtime/piloto/collector.yaml`: `site-piloto`, `collector-piloto-01`, `http://127.0.0.1:8199`, TCP a `127.0.0.1:8198` (cerrado), `no-such-netpulse-pilot.invalid:22`, TCP a `127.0.0.1:8199`; sonda cada 10 s, sync cada 3 s, heartbeat cada 30 s, backoff base 1 s/máximo 12 s, buffer 5000 filas y 72 h. No hubo 429. La cadencia acelerada fue solo para obtener evidencias locales, no sugerencia para producción.

**Autenticación de test:** `tests/test_remote_auth.py` usa `create_access_token({"sub":"admin","role":"admin"})`; se repitió ese método contra la API de desarrollo localhost, sin login/credenciales de otro entorno. `/docs` devolvió 200. `POST /api/security/api-keys` devolvió 201, `kind=collector`, binding de ambos IDs, id `2b5daaf2-5dec-49f1-a9d7-91bfa92e194a`; clave guardada en `.env-piloto` con modo 0600, ignorada explícitamente en `.gitignore`. JWT administrativo y key-id se almacenaron en `runtime/piloto/` ignorado. No se muestra material secreto.

| Paso | Esperado | Resultado real | Incidencia |
|---|---|---|---|
| `--check` | whoami y coincidencia de IDs | Exit 0, HTTP 200, `site_id=site-piloto`, `collector_id=collector-piloto-01` | Ninguna. |
| `--dry-run` | Sondas reales, sin buffer ni HTTP de escritura | Exit 0; `tcp-central ok=true reachable=true latency_ms=0.8`; `tcp-refused ok=true reachable=false error="conexión rechazada"`; DNS `ok=false reachable=null latency_ms=null error="DNS: [Errno 8] ..."` | Ninguna; «no responde al sondeo» no significa «caído». |
| Primer `--once` | Ingesta 200 | Sonda 3 devices / 4 filas, HTTP **422** por `sync.last_success_at`; `sync_error` visible y `pending=4` | **BUG colector corregido**: incluía estado local no admitido por `SyncIn(extra="forbid")`. `--once` terminó exit 0 pese al error de sync, conforme a su contrato (solo falla si el ciclo/buffer falla). |
| `--status` tras `--once`, antes del daemon | Existencia de status si fue generado | Exit **3**, `sin status ... status.json` ausente | Semántica de CLI: `--once` no escribe `status.json`; documentado, no se afirma salud inexistente. |
| Segundo `--once` después del fix | Recupera muestras pendientes | HTTP 200, `accepted=8`, `duplicates=0`, `marked_synced=8`, `pending=0` (4 antiguas + 4 nuevas) | Ninguna tras el fix. |
| Servicio continuo + `--status` | Proceso con status fresco | PID 76214; `state=fresh`, `probe.devices=3`, `reachable=1`, `unreachable=1`, `unverifiable=1`; sync HTTP 200 | Status inicial mostró `pending=4` hasta siguiente sync (snapshot temporal, no pérdida). |
| GET `/api/remote/sites` | `site-piloto` fresco | HTTP 200, `contact=fresh`, devices `{total:3, reachable:1, unreachable:1, unverifiable:1, unmatched:3}` | Tres IDs piloto no están en inventario central; se muestran `inventory_match=false` sin crearlos. |
| GET `/api/remote/sites/site-piloto/devices` | Tres estados diferenciados | HTTP 200: DNS `ok=false,reachable=null,latency_ms=null`; puerto cerrado `ok=true,reachable=false,latency_ms=null`; central `ok=true,reachable=true,latency_ms=1.1` | Ninguna. |
| GET `/api/remote/sites/site-piloto/devices/tcp-central/history` | Puntos crudos | HTTP 200, `count=2`, puntos UTC `19:22:44.999709` (0.7 ms), `19:30:29.625505` (1.1 ms), `coverage.expected_samples=null` | Ninguna: no se infiere cobertura/SLA. |

## Escenarios de honestidad: evidencia real

1. **API central detenida, colector vivo.** Se envió SIGTERM al central PID 75951 y se comprobó `lsof -nP -iTCP:8199 -sTCP:LISTEN` vacío. El colector PID 76214 siguió midiendo; un status UTC `19:30:54.670538` informó `probe.last_cycle_at=19:30:54.657031`, `reachable=0, unreachable=2, unverifiable=1` (la propia central ahora también no responde), `buffer.pending=6`, `dropped_total=0`, `sync.last_error="error de red: [Errno 61] Connection refused"`, `consecutive_failures=3`, `next_attempt_at=19:30:59.942114`. Logs: errores a `19:30:46.747`, `19:30:48.073`, `19:30:51.369`, `19:30:59.959` UTC y nuevos ciclos a `19:30:44.641`, `19:30:54.663`, `19:31:04.679`. El jitter hace que los intervalos no sean exactamente 2×; los intentos tienen backoff exponencial acotado. Ninguna muestra se descartó.
2. **Reinicio y reenvío idempotente.** Central reiniciada con la **misma DB** (`runtime/piloto/central.db`), `/docs=200`. Log de sync UTC `19:31:11.825`: POST 200, `accepted=9,duplicates=0,marked_synced=9,marked_dead=0` (mediciones acumuladas durante el corte); status UTC `19:31:20.955525`: `pending=0,dropped_total=0,consecutive_failures=0,auth_state=ok,last_status_code=200`. Para probar el mismo conjunto otra vez, se tomaron cuatro filas **ya sincronizadas** del SQLite local y se reenviaron **sin mocks** a `/api/remote/ingest`, conservando sus tripletes: HTTP 200, `accepted=0,duplicates=4,rejected=0,out_of_order=3`. El `out_of_order=3` corresponde a muestras históricas válidas más viejas que el estado actual, no a inserciones nuevas; no hubo doble conteo. **Matiz:** la API reporta `buffer.pending` del payload previo al marcado local (p. ej., 4 aun cuando el `--once` devuelve `pending=0`), hasta un siguiente heartbeat/sync. PROPOSED: mostrar ese campo como «último valor declarado», con timestamp, y evitar presentarlo como saldo en tiempo real. No se cambió backend central.
3. **Revocación.** `DELETE /api/security/api-keys/2b5daaf2-5dec-49f1-a9d7-91bfa92e194a` con JWT admin → **204**; `GET /api/remote/whoami` con la clave revocada → **401** `"API key inválida o revocada"`. El servicio siguió vivo; log UTC `19:31:45.255` POST ingest **401**, `auth_error`; `--status` UTC `19:31:45.269249`: `auth_state=error`, `last_status_code=401`, `last_error="HTTP 401: API key inválida o revocada"`, `consecutive_failures=1`, `next_attempt_at=19:32:01.817288`, `buffer.pending=4`, `dropped_total=0`, último ciclo con los tres estados. El colector no borra filas en 401; **la central no puede saber de este fallo hasta una nueva autenticación exitosa**, así que su último `sync.auth_state=ok` no equivale a estado actual. PROPOSED en UI central: tratarlo como último reporte, mostrar edad de contacto y no inferir autorización vigente desde un snapshot pasado.

**Reloj:** manipulación de clock/TZ y `time_quality=skewed` no validados localmente (omitidos, no se cambiaron ajustes del host). **Rate limit:** no hubo respuestas 429 en esta cadencia; no se cambiaron límites backend.

## Fix y hallazgos pendientes

- **Aplicado, commit separado:** `fix(remote-collector): exclude local sync state from ingest payload`. Repro: crear clave real, ejecutar `--once` con buffer nuevo; `build_payload()` incluía `sync.last_success_at=None` y central devolvía 422 `extra_forbidden loc=['sync','last_success_at']`. `app/remote_collector/sync.py` ahora envía solo `last_error`, `last_error_at`, `consecutive_failures`, `auth_state`; prueba de regresión en `tests/test_remote_collector_sync.py`. No se alteraron modelos, routers, store ni API keys centrales.
- **PROPOSED (contrato/visualización central, no aplicado):** identificar `buffer.pending` como snapshot de envío anterior; no presentarlo como saldo local actual tras confirmación. Si se necesita cifra post-ack, negociar heartbeat posterior o campo distinguible de `pending_before_send`.
- **PROPOSED (operación/UI, no aplicado):** cuando se revoca la clave, el último `auth_state` central permanece en `ok` porque no acepta el 401 como ingesta; mostrar contacto/frescura y «último estado reportado», no pintar un estado de auth actual inventado. Evitar rotular TCP refused como «equipo caído».
- **Pendientes explícitos:** certificación TLS/CA real, nodo Linux y systemd, permisos ICMP, prueba de reloj desviado, corte superior a `max_age_hours`/capacidad de buffer y un flujo real de rotación de clave. Ninguno se presenta como validado.

## Verificación final

`PYTHONPATH=. /Users/doker/netpulse-noc/.venv/bin/pytest tests/ -q` → **exit 0, 253 tests pasados** (progreso final 100%; una advertencia `StarletteDeprecationWarning` sobre `httpx` en `fastapi/testclient.py`). El test de regresión específico `tests/test_remote_collector_sync.py -q` → 9 tests pasados. Se enviaron SIGTERM a colector PID 76214 y central PID 76277; ambos PIDs dejaron de existir; `lsof -nP -iTCP:8199 -sTCP:LISTEN` y `lsof -nP -iTCP:8198 -sTCP:LISTEN` quedaron vacíos. El `status.json` persistente después de terminar el daemon puede quedar `state=fresh` brevemente hasta superar umbral de staleness; **no** inferir proceso vivo únicamente de `--status`.
