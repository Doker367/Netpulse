# Terminal WebSocket — verificación en vivo (tarjeta 7E)

Fecha: 2026-10-24, America/Mexico_City. Rama `feat/terminal-verif`, base `2deacf1`.
**Resultado: 5 de los 8 casos pasados; 1 fallido, 1 parcial y 1 NO validado.** Cierre explícito del operador adicional: pasado. No se modificó código de la aplicación.

## Prerrequisitos y alcance real

`git log --oneline -3` al comenzar:

```text
2deacf1 backend: websocket terminal con eventos json, heartbeat y cierre con causa
a5314d5 feat(terminal): eventos json, heartbeat y cierre con causa + contrato
4fe65fb ui: panel sftp con estados honestos y progreso
```

`main` local apunta a `2deacf1`; `git merge-base --is-ancestor a5314d5 main` devuelve 0. El [router](<app/routers/terminal.py>) contiene `fmt` y eventos JSON; existe el [contrato](<docs/handoffs/terminal.md>). Se leyó [PRODUCT.md](<PRODUCT.md>). Sin merges, push ni cambios fuera del worktree salvo herramientas/evidencia temporal en `/tmp`, solicitada por la tarjeta.

Diferencia de entorno: los archivos locales `config/users.yaml` y `config/devices.yaml` **no existían** en este worktree. Se registró su ausencia inicial, se crearon temporalmente y se restauró la ausencia en `finally`. No había bytes originales que respaldar. No se leyó ni modificó la configuración del repo principal. Inventario temporal explícito, no mocks: Cisco `127.0.0.1:2222`, driver `ios`, usuario/clave de laboratorio `admin/admin`; MikroTik `127.0.0.1`, driver `ros`, `port: 8728`, `cli_port: 2225`, usuario `admin`, contraseña vacía (valores del inicializador del laboratorio). Por tanto **no se valida el inventario privado del repo principal ni sus credenciales**.

Se creó `demo7`, admin, con contraseña aleatoria y `app.core.security.hash_password`; login HTTP real y JWT obtenido de la API, no fabricado. Cliente `websockets 17.2`, servidor `uvicorn 0.54.0`, `paramiko 5.0.0`. Todo WS se realizó por TCP contra uvicorn, sin TestClient ni sustituciones de SSH. Se repitió la corrida para esperar el prompt y la respuesta completa de FRR; la evidencia versionada corresponde a la segunda corrida.

## Casos: esperado / real / evidencia

| Caso | Esperado | Real | Evidencia |
|---|---|---|---|
| 1. Login + legacy | Login → JWT; línea Conectando; `\n` → salida real FRR | **PASADO.** HTTP 200; `Conectando a 127.0.0.1:2222 como admin...`; banner `FRRouting (version 8.4_git)`; tras newline vuelve `cisco-core-01#` | [Transcripción:1–5](<docs/handoffs/terminal-verif-evidence/results.jsonl#L1-L5>) |
| 2. JSON + heartbeat | `connected`, `output`; ping → pong con ts | **PASADO.** `connected.port=2222`; `show version` devuelve `FRRouting 8.4_git (cisco-core-01)`; `pong.ts=1792863284.231436` | [Transcripción:6–10](<docs/handoffs/terminal-verif-evidence/results.jsonl#L6-L10>) |
| 3. Equipo inexistente | `error.device_not_found` y `closed.reason` | **PASADO.** `code=device_not_found` en ambos; reason `equipo no encontrado` | [Transcripción:12–13](<docs/handoffs/terminal-verif-evidence/results.jsonl#L12-L13>) |
| 4. Token inválido | Cierre WS 4401 | **FALLIDO respecto al contrato.** Handshake rechazado con HTTP 403; `websockets.exceptions.InvalidStatus`; ningún frame close 4401 | [Transcripción:14](<docs/handoffs/terminal-verif-evidence/results.jsonl#L14>), [API:18–19](<docs/handoffs/terminal-verif-evidence/api-8097.txt#L18-L19>) |
| 5. MikroTik SSH | `connected.port=2225`, nunca 8728 | **PARCIAL.** Se negocia banner `ROSSSH` por 2225, pero no hay `connected`. Error real `ssh_connect`: `No se pudo conectar a mikrotik-edge-01 (127.0.0.1:2225): No existing session`; después `closed/unreachable`, reason `equipo inalcanzable`. No se puede atribuir con certeza a credenciales | [Transcripción:15–16](<docs/handoffs/terminal-verif-evidence/results.jsonl#L15-L16>), [API:20–24](<docs/handoffs/terminal-verif-evidence/api-8097.txt#L20-L24>) |
| 6. Corte abrupto | Liberar SSH, sin traceback ni hilos Paramiko residuales; API responde | **PASADO en sesión Cisco establecida.** `ws.transport.abort()` sin close frame; log `cerrada (eof)`; snapshot durante tiene `paramiko.transport.Transport`, un segundo después no queda ninguno. Nueva conexión al WS recibe respuesta. Sin tracebacks en los dos logs | [Transcripción:17–20](<docs/handoffs/terminal-verif-evidence/results.jsonl#L17-L20>), [API:25–34](<docs/handoffs/terminal-verif-evidence/api-8097.txt#L25-L34>) |
| 7. UI + reconexión | Comando y banner tras corte de API en navegador | **NO validado.** UI 7C no está integrada en esta base: aún crea WS legacy y marca Desconectado al cerrar. No hay herramienta de navegador disponible en esta sesión; no se afirma verificación visual | [UI:7327–7397](<app/static/dashboard.html#L7327-L7397>) |
| 8. Idle | Instancia 8098 con `NETPULSE_TERM_IDLE_S=3`; cierre con causa | **PASADO.** `closed`, `code=idle`, `reason=timeout de inactividad`; 3.466 s desde apertura del WS (incluye SSH y banner), sin enviar ping ni input | [Transcripción:21–26](<docs/handoffs/terminal-verif-evidence/results.jsonl#L21-L26>), [API idle:9](<docs/handoffs/terminal-verif-evidence/api-8098.txt#L9>) |
| Adicional: cierre operador | `{"type":"close"}` → closed con causa | **PASADO.** `closed/operator`, `cerrado por el operador`; log confirma cierre de sesión | [Transcripción:11](<docs/handoffs/terminal-verif-evidence/results.jsonl#L11>), [API:14](<docs/handoffs/terminal-verif-evidence/api-8097.txt#L14>) |

## Método reproducible e instrumentación

Intérprete: `/Users/doker/netpulse-noc/.venv/bin/python`, `PYTHONPATH=.` desde este worktree. Para arranque sin observador:

```bash
PYTHONPATH=. /Users/doker/netpulse-noc/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8097
NETPULSE_TERM_IDLE_S=3 PYTHONPATH=. /Users/doker/netpulse-noc/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8098
```

La corrida usó el mismo entrypoint de uvicorn mediante `runpy.run_module('uvicorn', run_name='__main__')`, añadiendo solamente observabilidad: handler SIGUSR1 que imprime `(t.name, type(t).__module__, type(t).__name__)` para `threading.enumerate()`, logging INFO y filtro que oculta el JWT. No se alteró el router, Paramiko, el protocolo ni sus métodos. Collectors métricos/syslog deshabilitados para aislar los transportes de terminal. Antes, durante y un segundo después del aborto se envió SIGUSR1 al PID servidor. El primer snapshot conserva momentáneamente un transporte del intento MikroTik; no se oculta: el snapshot final no conserva ninguno.

Secuencia del cliente (timeout de recepción 25 s, `ping_interval=None`):

1. `POST /api/auth/login` con JSON `username=demo7` y la contraseña temporal; conservar `access_token` sin registrarlo.
2. `websockets.connect('ws://127.0.0.1:8097/ws/terminal/cisco-core-01?token='+token)`; recibir hasta prompt FRR, enviar `\n`, recibir nuevo prompt.
3. Reabrir con `&fmt=json`; esperar `connected` y prompt, enviar `{"type":"input","data":"show version\n"}` y `{"type":"ping"}`; recibir hasta pong y prompt posterior al comando. Enviar `{"type":"close"}` y esperar `closed`.
4. Cambiar device a `inexistente-7e`; recibir error y closed. Cambiar token a `invalido`: observar rechazo HTTP, no sesión WS establecida.
5. Cambiar device a `mikrotik-edge-01`; esperar error/closed o connected. No sustituir el error por éxito.
6. Abrir Cisco JSON, esperar connected, ejecutar `ws.transport.abort(); await ws.wait_closed()`; observar hilos y volver a consultar WS.
7. Abrir Cisco en 8098 sin actividad de cliente y esperar el evento idle.

Runner temporal: [script de verificación](</tmp/np-w7v-terminal-verif.py>). Logs de la corrida final: [API 8097](</tmp/np-w7v-terminal-evidence-v2/api-8097.log>), [API 8098](</tmp/np-w7v-terminal-evidence-v2/api-8098.log>). Las copias versionadas anteriores evitan depender de la persistencia de `/tmp`.

## Bugs y fixes propuestos (no aplicados)

### Auth: 4401 no llega por la red

Repro mínimo con servidor activo, no necesita usuario válido:

```python
import asyncio, websockets
async def main():
    async with websockets.connect(
        'ws://127.0.0.1:8097/ws/terminal/cisco-core-01?token=invalido&fmt=json'
    ) as ws:
        await ws.recv()
asyncio.run(main())
# InvalidStatus: server rejected WebSocket connection: HTTP 403
```

Causa: el [router:192–196](<app/routers/terminal.py#L192-L196>) invoca `close(4401)` antes de `accept()`. Uvicorn rechaza el upgrade con HTTP 403; no hay canal WS donde transmitir el código. Fix propuesto para conservar el contrato 4401: aceptar el WS y cerrarlo inmediatamente con 4401, sin buscar inventario ni abrir SSH; añadir regresión con uvicorn real. Alternativa de producto: mantener rechazo HTTP y corregir contrato/UI, sin prometer que el navegador distinguirá auth por 4401.

### MikroTik: sesión SSH no establecida

Repro: inventario y URL descritos arriba, `cli_port=2225`, `admin` con contraseña vacía. Se alcanza `ROSSSH`, pero Paramiko devuelve `No existing session`. **No es prueba de credenciales rechazadas** (`ssh_auth` no fue emitido). Propuesta: comprobar credenciales y negociación SSH del lab con su responsable, repetir contra 2225 y capturar diagnóstico Paramiko sanitizado; cambiar código solamente si se aísla un fallo del backend. Pendiente validar `connected.port`, output y cierre de una sesión RouterOS exitosa.

### Registro de JWT en uvicorn

La primera corrida mostró que uvicorn registra la URL WS con `?token=...` incluso usando `--no-access-log`; no basta la garantía del logger del router. La segunda corrida añadió un filtro de observación para ocultarlo, sin cambiar la app. Fix propuesto: filtro de redacción en configuración de logging de despliegue y regresión que compruebe ausencia de JWT en todos los loggers. No publicar logs crudos de autenticación.

## Limpieza, suite y pendientes

- Servidores iniciales PIDs 12947/12980 y repetición 12998/13015 terminados con SIGTERM y esperados por el runner; logs confirman shutdown completo. No quedó proceso creado por la verificación.
- Comprobación posterior: `lsof -nP -iTCP:8097` y `lsof -nP -iTCP:8098` sin salida (exit 1 = sin conexiones); `kill -0` falla para los cuatro PIDs.
- Usuario temporal eliminado; configuración restaurada a su estado inicial ausente. No hubo modificación de código de app ni del repo principal.
- Suite: **301 tests pasados**, exit 0, con `PYTHONPATH=. /Users/doker/netpulse-noc/.venv/bin/pytest tests/ -q`. La salida doble-quiet omite el resumen numérico: hay 301 puntos y ningún fallo/skip. Una advertencia de deprecación de Starlette/httpx; no afecta al resultado. [Salida completa](<docs/handoffs/terminal-verif-evidence/pytest.txt>).
- **Pendientes explícitos:** resolver discrepancia 403/4401; verificar SSH exitoso y `connected.port` de MikroTik con configuración real autorizada; integrar UI 7C y validar banner de reconexión en navegador; aplicar redacción de JWT al logging real. El corte durante la negociación SSH, agotamiento por muchas sesiones y reconexiones repetidas no forman parte de esta evidencia.
