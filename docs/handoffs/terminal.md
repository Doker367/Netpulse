# Handoff — Terminal WebSocket: contrato JSON, heartbeat y cierre con causa

Contrato para la UI de la **tarjeta 7C**. Esta tarjeta es solo backend: el
frontend actual (`app/static/dashboard.html`) sigue usando el **modo legacy**
sin cambios. Aquí está el comportamiento exacto del WebSocket para migrarlo.

- Router: `app/routers/terminal.py`
- Endpoint: `WS /ws/terminal/{device_id}`
- Autenticación: query `?token=<JWT>` (rol `admin` u `operator`).
- El modo se elige **solo** con la query `fmt`:
  - `?fmt` ausente (o cualquier valor distinto de `json`) → **modo legacy**,
    texto plano, comportamiento actual de `main`.
  - `?fmt=json` → **modo JSON** con eventos, heartbeat e idle.

```
ws://<host>/ws/terminal/cisco-core-01?token=<JWT>&fmt=json
wss://<host>/ws/terminal/cisco-core-01?token=<JWT>&fmt=json   # tras TLS
```

---

## 1) Modo legacy (el que usa la UI actual)

Sin `fmt=json` el servidor habla exactamente como antes:

| Servidor envía | Cuándo |
|---|---|
| `Conectando a <host>:<port> como <user>...\r\n` | Al aceptar el WebSocket. |
| Chunks de texto del PTY (stdout y stderr) | Según llegan del equipo. |
| `⚠️ paramiko no disponible: ...\r\n` | Falta la librería en el servidor. |
| `⚠️ Dispositivo '<id>' no encontrado\r\n` | Id inexistente. |
| `⚠️ No se pudo conectar: <detalle>\r\n` | Fallo de SSH (el detalle puede incluir la nota de puerto API). |

Mensajes del cliente que entiende el modo legacy (sin cambios):

- Texto normal → se escribe tal cual en la shell.
- `__resize:<cols>,<rows>` → redimensiona el PTY.
- `__json:{"type":"resize","cols":80,"rows":24}` → redimensiona el PTY.

En legacy **no** hay eventos `connected`/`error`/`closed`, **no** hay
ping/pong y el cierre no lleva código semántico (solo el 4401 de auth). El
timeout de inactividad también aplica, pero cierra en silencio (sin evento).

> Importante: mientras 7C no esté mergeada, no añadir `fmt=json` a la UI
> actual. El modo por defecto se mantiene intacto a propósito.

---

## 2) Cierre de autenticación (ambos modos)

Si el token falta, es inválido o el rol no es `admin`/`operator`, el servidor
cierra **antes de aceptar** con:

```
close code = 4401
```

No hay `error` ni `closed`: el navegador solo ve el cierre 4401. La UI debe
tratarlo como «sesión caducada»: renovar token o volver al login, **sin**
reintentar en bucle.

---

## 3) Modo JSON: eventos del servidor

Cada mensaje del servidor es **un objeto JSON completo** (texto). No hay
mensajes parciales ni texto plano mezclado.

### 3.1 `connected`

Se emite **una vez**, cuando el SSH y el PTY ya están realmente abiertos (no
antes). Si la conexión falla, en su lugar llega `error` + `closed`.

```json
{
  "type": "connected",
  "device": "cisco-core-01",
  "host": "10.0.0.10",
  "port": 22,
  "user": "admin"
}
```

| Campo | Tipo | Notas |
|---|---|---|
| `device` | string | Id del inventario solicitado. |
| `host` | string | Hostname/IP usada para conectar. |
| `port` | int | Puerto SSH **resuelto** (ver §6), no necesariamente el `port` del inventario. |
| `user` | string | Usuario SSH. |

**Nunca incluye password** ni ningún otro secreto.

### 3.2 `output`

Salida del PTY (stdout y stderr), ya decodificada como UTF-8 con reemplazo de
bytes inválidos. El campo `data` puede contener `\r\n`, comillas, acentos o
emoji: el JSON los escapa y `JSON.parse` los restaura.

```json
{ "type": "output", "data": "RouterOS 7.15\r\n[admin@MikroTik] > " }
```

| Campo | Tipo | Notas |
|---|---|---|
| `data` | string | Chunk de salida. Puede ser vacío en teoría; la UI debe ignorar `data === ""`. |

La UI debe **concatenar** los chunks en el orden recibido; no asumir que un
comando termina en un chunk concreto (no hay delimitador de comando).

### 3.3 `error`

Fallo diagnosticado por el servidor. Siempre va seguido de `closed` (salvo
que el cliente ya se haya desconectado).

```json
{
  "type": "error",
  "code": "ssh_connect",
  "detail": "No se pudo conectar a mikrotik-edge-01 (10.0.0.20:22): ... El inventario apunta al puerto API 8728 del driver 'ros', que no ofrece SSH; se intentó el 22."
}
```

| `code` | Significado | `detail` (español, mostrable tal cual) |
|---|---|---|
| `auth` | Token inválido. **Reservado**: hoy el cierre 4401 ocurre antes del `accept`, así que no se emite como evento. | — |
| `device_not_found` | El id no existe en el inventario. | `Dispositivo '<id>' no encontrado.` |
| `ssh_connect` | No se pudo abrir el transporte SSH (DNS, timeout, conexión rechazada, red). | `No se pudo conectar a <device> (<host>:<port>): <causa>` o `Tiempo de espera agotado conectando a ...`. Puede terminar con la nota de puerto API (§6). |
| `ssh_auth` | El equipo rechazó usuario/contraseña. | `Autenticación SSH fallida en <device>: revisa las credenciales.` |
| `unsupported` | Falta paramiko en el servidor (o el equipo no permite abrir PTY). | `paramiko no disponible en el servidor: <causa>` |
| `internal` | Fallo no clasificable. | Texto corto; registrar para soporte. |

`detail` no contiene credenciales ni traceback.

### 3.4 `closed`

Último mensaje del servidor cuando la sesión termina de forma ordenada. La UI
debe **dejar de reintentar** según la tabla de §7.

```json
{ "type": "closed", "code": "idle", "reason": "timeout de inactividad" }
```

| `code` | `reason` literal | Cuándo |
|---|---|---|
| `eof` | `fin de sesión` | El equipo cerró el canal PTY (EOF) o el servidor termina la sesión. |
| `idle` | `timeout de inactividad` | Sin actividad durante `NETPULSE_TERM_IDLE_S` segundos (default 900). |
| `unreachable` | `equipo inalcanzable` | Falló la conexión (`ssh_connect` o `ssh_auth`); el `error` previo explica la causa. |
| `operator` | `cerrado por el operador` | El cliente envió `{"type":"close"}` (botón «Cerrar sesión»). |
| `device_not_found` | `equipo no encontrado` | Id inexistente; el `error` previo lo explica. |
| `internal` | `error interno` | Fallo interno del servidor. |

Si el cliente se desconecta por su cuenta (cierra la pestaña, `ws.close()`),
el servidor limpia la sesión SSH pero **no puede enviar** `closed` (el socket
ya no existe). En ese caso no hay evento: la UI simplemente descarta el
estado al cerrar.

---

## 4) Modo JSON: mensajes del cliente

Todo lo que envía la UI es texto WebSocket. El servidor distingue:

| Mensaje | Efecto |
|---|---|
| Texto plano (`"ls\n"`, una tecla suelta) | Se escribe tal cual en la shell. Es el caso normal de xterm.js. |
| `{"type":"input","data":"show version\n"}` | Igual que el texto plano, pero explícito. Útil para pegar texto. |
| `{"type":"ping"}` | Responde `{"type":"pong","ts":<epoch float>}`. |
| `{"type":"close"}` | Cierra la sesión: el servidor cierra SSH y envía `closed` con `code: "operator"`. |
| `{"type":"resize","cols":100,"rows":40}` | Redimensiona el PTY. |
| Cualquier otro objeto JSON | Se ignora (no se inyecta en la shell). |
| `__resize:<cols>,<rows>` / `__json:{...}` | Compatibilidad con el protocolo legacy; también funcionan aquí. |

Aviso: en modo JSON un objeto JSON escrito «a mano» en la shell se interpreta
como control y se ignora. El tecleo normal (carácter a carácter, texto plano)
no se ve afectado. Si 7C pega texto que empieza por `{`, debe enviarlo como
`{"type":"input","data":"..."}`.

---

## 5) Heartbeat e inactividad

- El servidor **no inicia** pings. La UI debe enviar `{"type":"ping"}` cada
  **20–30 s**; el `pong` incluye `ts` (epoch en segundos, float).
- El timeout de inactividad se configura con la variable de entorno
  `NETPULSE_TERM_IDLE_S` (segundos, default **900**). Valores no numéricos o
  `<= 0` vuelven al default.
- Cuenta como actividad: cualquier mensaje del cliente (incluido `ping`) y
  cualquier salida del equipo. Un `ping` mantiene viva la sesión.
- Al superarse, el servidor cierra el PTY y el SSH, y envía
  `{"type":"closed","code":"idle","reason":"timeout de inactividad"}`.
- Si no llega `pong` en ~2 intervalos de ping, la UI debe considerar la
  conexión muerta y reconectar con backoff (§7).

---

## 6) Resolución del puerto SSH (honesta)

El terminal ya no confía ciegamente en `port`. Orden:

1. `cli_port` del inventario.
2. `ssh_port` si existe en el inventario.
3. `port` **solo si** el protocolo es SSH y no es un puerto de API conocido.
4. Default `22`.

| Driver | Puertos de API descartados | SSH esperado |
|---|---|---|
| `ros` (RouterOS) | `8728`, `8729` | `22` (o `cli_port`; en el laboratorio el real es `2225`) |
| `eos` (Arista) | `443` (eAPI) | `22` |

Si el inventario apuntaba a un puerto API descartado, el `detail` del error
`ssh_connect` termina con:

```
 El inventario apunta al puerto API 8728 del driver 'ros', que no ofrece SSH; se intentó el 22.
```

La UI debe mostrar el `detail` completo: explica que el problema es del
inventario, no del equipo. El `connected.port` siempre muestra el puerto
realmente usado (nunca `8728` para un RouterOS con `port: 8728` sin
`cli_port`).

Para el laboratorio: añadir `cli_port: 2225` (o `ssh_port: 2225`) al MikroTik
en `config/devices.yaml`; eso está fuera de la zona de esta tarjeta.

---

## 7) Qué mostrar y cómo reconectar (UI 7C)

| Evento / cierre | UI |
|---|---|
| `connected` | Pintar cabecera `host:port (user)`, habilitar teclado, iniciar pings. |
| `output` | Escribir `data` en xterm.js en orden. |
| `error` `ssh_auth` | Banner **Credenciales SSH rechazadas** + `detail`. No reintentar en bucle; ofrecer «Revisar credenciales». |
| `error` `ssh_connect`/`closed` `unreachable` | Banner **No se pudo conectar** + `detail`. Si menciona «puerto API», añadir «El inventario apunta a la API, no a SSH». Reintento manual o backoff. |
| `error` `device_not_found` | Banner **Equipo no encontrado**; recargar inventario. Sin reintento. |
| `error` `unsupported` | Banner **Terminal no disponible en el servidor**; no reintentar. |
| `error` `internal` | Banner **Fallo interno**; registrar para soporte. |
| `closed` `eof` | «Sesión finalizada por el equipo.» Reconectar solo por acción del operador (el PTY murió). |
| `closed` `idle` | «Sesión cerrada por inactividad.» Reconectar al pulsar el operador. |
| `closed` `operator` | Cerrar el panel; no reconectar. |
| Cierre `4401` | «Sesión caducada»: renovar token / login. Sin reintento. |
| Cierre anómalo (sin `closed`) | Backoff con reconexión. |

Reconexión recomendada: backoff exponencial **1 s, 2 s, 4 s, 8 s, 16 s,
máximo 30 s**, con jitter; máximo ~5 intentos automáticos. No reconectar
automáticamente tras `operator`, `device_not_found`, `unsupported`, `ssh_auth`
ni 4401. Al reconectar, limpiar el buffer de xterm y pedir un prompt nuevo
(la sesión SSH anterior ya no existe).

---

## 8) Limpieza y garantías del backend

- `finally` cierra el canal PTY y el cliente SSH en **todas** las rutas:
  desconexión del cliente, idle, EOF remoto, fallo de conexión, excepción.
- Las tareas de bombeo (PTY→WS y WS→PTY) se cancelan y se esperan; no quedan
  tareas huérfanas. La limpieza está protegida con `anyio.CancelScope`
  (`shield=True`) para ejecutarse incluso si el servidor cancela la sesión.
- Logging con el logger `app.routers.terminal`: dispositivo, host, puerto y
  usuario; **nunca** password ni el JWT.

## 9) Pendientes explícitos

- **Verificación real en laboratorio** (SSH real, MikroTik/RouterOS e IOL):
  los tests usan `TestClient` y paramiko mockeado. No se abrió ninguna sesión
  real.
- **UI 7C**: este documento es el contrato; el frontend sigue en modo legacy.
- `_resolve_ssh_port` está duplicado a propósito en `terminal.py` (misma
  lógica que `app/routers/sftp.py`) para no acoplar routers. Si cambia el
  criterio, actualizar ambos.
