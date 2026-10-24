# Handoff — SFTP: puerto SSH real y errores por causa

Contrato para la UI (`app/static/dashboard.html`, ventana de UI). Esta tarjeta
es **solo backend**: no se tocó el frontend. Aquí está el comportamiento exacto
de los tres endpoints y cómo debe explicarse cada fallo.

- Router: `app/routers/sftp.py`
- Prefijo: `/api/sftp`
- Autenticación: `Authorization: Bearer <JWT>`.
- RBAC: rol mínimo **operator** (`admin` incluido). `viewer` → `403`.
- Errores: siempre `HTTPException`, cuerpo `{"detail": "<texto en español>"}`,
  **sin traceback**. Los códigos 200 conservan `success: true`.

## Resolución del puerto SSH (importante para el laboratorio)

El backend ya no confía ciegamente en `port`. Orden:

1. `cli_port` del inventario (canal CLI explícito).
2. `ssh_port` si el inventario lo declarara (campo opcional, no está en el
   `devices.yaml` actual).
3. `port` **solo si** el protocolo es SSH y no es un puerto de API conocido.
4. Default `22`.

Puertos de API que se descartan para SSH (`_API_PORTS_BY_DRIVER`):

| Driver | Puertos de API | Consecuencia |
|---|---|---|
| `ros` (RouterOS) | `8728`, `8729` | `mikrotik-edge-01` tiene `port: 8728` (API). SFTP intenta `22` salvo que el inventario declare `cli_port`/`ssh_port`. |
| `eos` (Arista) | `443` | `arista-core-01` tiene `port: 443` (eAPI). SFTP intenta `22`. |

En el laboratorio el SSH real de `mikrotik-edge-01` es `2225`, pero **el
inventario no lo declara** (`cli_port: null`). No se editó `devices.yaml` en
esta tarjeta: para la verificación real de 6D hay que añadir allí
`cli_port: 2225` (o `ssh_port: 2225`) al MikroTik. Mientras tanto, el error
502/504 incluye una nota que explica que el inventario apuntaba al puerto API.

Si `port` apuntaba a un puerto API y la conexión falla, el `detail` de 502/504
termina con:

```
 El inventario apunta al puerto API 8728 del driver 'ros', que no ofrece SSH; se intentó el 22.
```

La UI debe mostrar el `detail` completo (o su texto en tooltip); no lo recorte.

Las credenciales se leen de `napalm_svc._find_device()`, que ya descifra los
passwords Fernet del inventario. El backend usa solo password:
`look_for_keys=false`, `allow_agent=false`, `timeout=30` (ver §«Timeout de
conexión y kex»), `banner_timeout=30`, `auth_timeout=30`.

---

## Timeout de conexión y kex SSH

`SSHClient.connect(timeout=...)` de paramiko **no es solo el timeout TCP**:
también es el tiempo máximo que se espera el *kex* (intercambio de claves)
SSH. El default de SFTP es **30 s** y se puede cambiar con
`NETPULSE_SFTP_CONNECT_TIMEOUT_S`. Valores no numéricos o `<= 0` vuelven al
default de 30 s.

Motivo (hallazgos 6D/6E): el RouterOS del laboratorio (`127.0.0.1:2225`)
negocia el banner `SSH-2.0-ROSSSH` y tarda **8–26 s** en completar
`diffie-hellman-group-exchange-sha256` porque paramiko no anuncia el
`curve25519-sha256` que RouterOS espera. Con el timeout anterior de 10 s el kex
se agotaba a mitad y paramiko devolvía el engañoso `No existing session`.
Medición real con el default nuevo: **23.5 s** y listado `200` (ver
«Verificación en vivo 6E»).

Un kex/banner incompleto **no son credenciales rechazadas**. Se clasifica como
`502` con un `detail` de negociación (tabla común). `401` se reserva para
`paramiko.AuthenticationException` real. Marcadores reconocidos:
`No existing session`, `Unable to complete handshake`, `Error reading SSH
protocol banner`, `kex`, `key exchange`, `negotiation`.

El mismo criterio que `app/routers/terminal.py` (`NETPULSE_TERM_CONNECT_TIMEOUT_S`,
30 s), pero la variable de SFTP es independiente: subir una no cambia la otra.

---


## 1) `GET /api/sftp/{device_id}/list?path=/`

Lista un directorio remoto.

### Respuesta `200`

```json
{
  "success": true,
  "device_id": "cisco-core-01",
  "path": "/",
  "items": [
    {
      "name": "configs",
      "is_dir": true,
      "is_link": false,
      "size": 4096,
      "mtime": 1700000000,
      "permissions": "0755"
    },
    {
      "name": "running.cfg",
      "is_dir": false,
      "is_link": false,
      "size": 1024,
      "mtime": 1700000100,
      "permissions": "0644"
    }
  ]
}
```

| Campo | Tipo | Notas |
|---|---|---|
| `items` | array | Carpetas primero, luego archivos; ambos alfabéticos. |
| `is_dir` / `is_link` | bool | Calculados de `st_mode`. |
| `size` | int | Bytes; `0` si el equipo no lo informa. |
| `mtime` | number | Epoch seconds; `0` si no se informa. |
| `permissions` | string | Octal de 4 dígitos (`0755`), `""` si no se informa. |

**Un directorio vacío legítimo es `200` con `items: []`.** Eso NO es un error.
Un fallo de conexión o de lectura **nunca** devuelve `200` ni `items: []`:
devuelve uno de los códigos de la tabla y el cuerpo solo tiene `detail`.

### Errores propios

| Código | `detail` literal |
|---|---|
| `400` | `No se pudo acceder a la ruta '<path>': <causa>` |
| `403` | `Permiso denegado al leer <path> en <device>` |
| `404` | `Dispositivo '<device_id>' no encontrado.` |
| `504` | `Tiempo de espera agotado leyendo <path> en <device>` (lectura) |

Los errores de conexión (401/501/502/504) están en la tabla común.

---

## 2) `POST /api/sftp/{device_id}/upload?filename=<nombre>&remote_dir=<dir>`

Sube un archivo. El cuerpo es **binario crudo** (`Content-Type:
application/octet-stream`), no `multipart/form-data`. El backend construye la
ruta como `<remote_dir>/<basename(filename)>`.

### Respuesta `200`

```json
{
  "success": true,
  "message": "Archivo 'firmware.bin' subido exitosamente.",
  "remote_path": "/firmware/firmware.bin",
  "size_bytes": 1048576
}
```

### Errores propios

| Código | `detail` literal |
|---|---|
| `403` | `Permiso denegado al escribir <remote_path> en <device>` |
| `500` | `Error durante la subida SFTP: <causa>` (fallo no clasificable) |
| `502` | `Se perdió la conexión SFTP con <device> al escribir <remote_path>: <causa>` |
| `504` | `Tiempo de espera agotado durante la subida a <device>` |
| `404` | `Dispositivo '<device_id>' no encontrado.` |

`<causa>` es corta: `conexión rechazada`, `conexión interrumpida`, `host no
resuelto`, `timeout`, etc.

---

## 3) `GET /api/sftp/{device_id}/download?remote_path=<ruta>`

Devuelve el archivo como `application/octet-stream`, con
`Content-Disposition: attachment; filename="<basename>"` y `Content-Length`.

### Errores propios

| Código | `detail` literal |
|---|---|
| `400` | `La ruta solicitada es un directorio, no un archivo.` |
| `403` | `Permiso denegado al leer <remote_path> en <device>` |
| `404` | `Archivo no encontrado en '<remote_path>': <causa>` |
| `504` | `Tiempo de espera agotado leyendo <remote_path> en <device>` |
| `404` | `Dispositivo '<device_id>' no encontrado.` |

Si `fetch` recibe `200`, el cuerpo es el archivo. Cualquier otro código es
JSON con `detail`: no guardes el blob ni muestres el archivo como descargado.

---

## Tabla común de errores de conexión SFTP

Aplica a `list`, `upload` y `download` (la conexión se abre antes de la
operación).

| Código | `detail` literal | Causa real |
|---|---|---|
| `401` | `Autenticación SSH fallida en <device>: revisa credenciales` | `paramiko.AuthenticationException` (rechazo real de usuario/contraseña). |
| `501` | `SFTP no soportado por este equipo (<device>); usa Terminal/Comandos para CLI` | SSH acepta la sesión pero no hay subsistema SFTP (`Channel closed`, `subsystem request failed`, etc.). Medido: los FRR del laboratorio actual **sí** lo publican y devuelven `200`; el 501 aplica a equipos sin `Subsystem sftp`. |
| `502` | `Negociación SSH incompleta con <device> (<host>:<port>): el equipo tardó más que el timeout (<n> s). Revisa el equipo o sube NETPULSE_SFTP_CONNECT_TIMEOUT_S.` | **Kex/banner incompleto** (`No existing session`, `Unable to complete handshake`, `Error reading SSH protocol banner`). No es credenciales. `<n>` es el timeout efectivo (env o 30). |
| `502` | `No se pudo conectar a <device> (<host>:<port>): <causa>` | DNS, conexión rechazada, red/host inalcanzable, `SSHException` no relacionada con kex ni con el subsistema SFTP |
| `504` | `Tiempo de espera agotado conectando a <device> (<host>:<port>)` | timeout de TCP/banner/auth reportado como `socket.timeout`/`TimeoutError` |
| `500` | `Librería paramiko no disponible en el servidor: <causa>` | instalación incompleta |
| `400` | `El dispositivo no tiene hostname ni IP configurado.` | inventario incompleto |
| `404` | `Dispositivo '<device_id>' no encontrado.` | id inexistente |

`<causa>` en 502 es corta y en español: `host no resuelto`, `conexión
rechazada`, `conexión interrumpida`, `host no alcanzable`, `red no alcanzable`,
`timeout`. En 502/504 puede añadirse la nota de puerto API descrita arriba.

---

## Qué banner mostrar en la UI (por causa)

No usar un toast genérico «Error SFTP». Mostrar un bloque persistente en el
panel SFTP con título, explicación y acción:

| Código | Título del banner | Texto / acción |
|---|---|---|
| `401` | **Credenciales SSH rechazadas** | «El equipo rechazó el usuario/contraseña del inventario.» Acción: revisar credenciales; no reintentar en bucle. |
| `504` | **Sin respuesta SSH** | Mostrar `<host>:<port>` del `detail`. «El equipo no respondió; puede ser firewall, puerto incorrecto o equipo apagado.» Si el `detail` menciona «puerto API», mostrar además: «El inventario apunta a la API, no a SSH.» |
| `502` (negociación) | **Negociación SSH incompleta** | Si el `detail` empieza por «Negociación SSH incompleta», mostrar el timeout y la acción: reintentar en unos segundos o subir `NETPULSE_SFTP_CONNECT_TIMEOUT_S` en el servidor. **No** pedir credenciales ni borrar el inventario. |
| `502` | **No se pudo conectar** | Mostrar la causa del `detail`. Si es `host no resuelto`, revisar DNS/IP; si es `conexión rechazada`, revisar puerto/servicio SSH. |
| `501` | **SFTP no soportado por este equipo** | «Este equipo ofrece CLI por SSH pero no subsistema SFTP (habitual en FRR).» Acción primaria: **Abrir Terminal/Comandos**. No ofrecer reintentar SFTP. |
| `403` (upload) | **Permiso denegado al escribir** | Mostrar `<remote_path>`. «El usuario SSH no puede escribir en esa ruta.» Sugerir otra carpeta (p. ej. `/tmp` o la de configuración permitida). |
| `403` (list/download) | **Permiso denegado al leer** | Mostrar la ruta. No mostrar «carpeta vacía». |
| `404` (download) | **Archivo no encontrado** | Mostrar la ruta; refrescar el listado. |
| `404` (device) | **Equipo no encontrado** | El inventario cambió; recargar equipos. |
| `400` (path) | **Ruta no accesible** | Mostrar la ruta y la causa; volver al directorio anterior. |
| `500` | **Fallo interno de SFTP** | Mostrar la causa; registrar para soporte. |

Buenas prácticas obligatorias:

1. **Un fallo nunca debe verse como tabla vacía.** Solo mostrar «Carpeta
   vacía» cuando `res.ok === true` y `data.items.length === 0`. Un `catch` no
   debe asignar `items = []`.
2. Al cambiar de dispositivo o de carpeta, limpiar el estado anterior para no
   dejar visible el listado de otro equipo.
3. No mostrar `success`/«subido» si el `fetch` no devolvió `2xx`.
4. Para descargas, si `!res.ok`, leer `res.json().detail` y mostrar el banner;
   no crear el enlace de descarga.
5. El `detail` ya viene en español y sin traceback: se puede mostrar tal cual.

### Esquema de decisión

```js
if (!res.ok) {
  const { detail } = await res.json();
  const title = res.status === 502 && detail.startsWith('Negociación SSH incompleta')
    ? 'Negociación SSH incompleta'
    : {
        401: 'Credenciales SSH rechazadas',
        501: 'SFTP no soportado por este equipo',
        502: 'No se pudo conectar',
        504: 'Sin respuesta SSH',
        403: 'Permiso denegado',
        404: 'No encontrado',
      }[res.status] || 'Error SFTP';
  showSftpBanner({ title }, detail);
  return; // nunca pintar items
}
```

---

## Verificación en vivo 6E (lab real)

Fecha: 2026-10-24. Uvicorn desde este worktree en `127.0.0.1:8102`, usuario
temporal `demo6e` (admin) e inventario temporal local con
`mikrotik-edge-01` (`port: 8728`, `cli_port: 2225`, `admin`/vacío) y
`cisco-core-01` (`127.0.0.1:2222`, `admin`/`admin`). No se leyó el inventario
del repositorio principal. Procesos, usuario e inventario temporales se
retiraron al terminar; `lsof` confirmó el puerto 8102 libre y
`config/users.yaml`/`config/devices.yaml` volvieron a no existir.

| Caso | Esperado | Real |
|---|---|---|
| `GET /api/sftp/mikrotik-edge-01/list?path=/` con default 30 s | Intentar `2225`, esperar el kex; 200 o 502 de negociación | **PASA.** HTTP `200` en **23.46 s**; `success: true`, ruta `/`, 1 elemento real (`skins`). El kex RouterOS cabe en 30 s. |
| Mismo caso con `NETPULSE_SFTP_CONNECT_TIMEOUT_S=5` (forzado para probar la clasificación) | 502 de negociación, nunca `No existing session` crudo | **PASA.** HTTP `502` en `5.10 s`; traceback interno de paramiko: `No existing session`; respuesta: `Negociación SSH incompleta con mikrotik-edge-01 (127.0.0.1:2225): el equipo tardó más que el timeout (5 s). Revisa el equipo o sube NETPULSE_SFTP_CONNECT_TIMEOUT_S.` |
| `GET /api/sftp/cisco-core-01/list?path=/` | 200 con listado real (FRR sí tiene SFTP) | **PASA.** HTTP `200` en `0.23 s`, 18 entradas reales (`bin`, `dev`, `etc`, `home`, …). |

Lectura: con 30 s el caso MikroTik ya **no** falla por kex. Si un equipo tarda
más de 30 s, la UI recibe el 502 de negociación y puede sugerir subir la
variable; no se presenta como credenciales. La variable se validó en vivo con
`5`, no solo en tests mockeados.

---

## Pendientes explícitos

- **Verificación real:** hecha en 6D (comportamiento general) y 6E (timeout de
  kex y error honesto) contra el laboratorio activo; ver §«Verificación en
  vivo 6E». Los tests de `tests/test_sftp.py` siguen siendo mockeados salvo
  esa verificación manual documentada.
- **MikroTik del laboratorio:** su SSH está en `2225` pero el inventario del
  repositorio principal no lo declara. Para que SFTP lo use, añadir
  `cli_port: 2225` o `ssh_port: 2225` en `config/devices.yaml` (fuera de la
  zona de esta tarjeta). En la verificación 6E se usó un inventario temporal.
- **FRR (cisco/switch del laboratorio):** el laboratorio actual **sí** ofrece
  `Subsystem sftp` y devuelve `200` con listado real; no se debe asumir `501`
  por driver. El `501` se reserva a equipos que aceptan SSH pero no publican
  el subsistema SFTP.
- **Valor de timeout en producción:** el default de 30 s cubre el RouterOS
  medido (8–26 s). Si el parque incluye equipos más lentos, ajustar
  `NETPULSE_SFTP_CONNECT_TIMEOUT_S`; no hay validación en vivo por encima de
  30 s.
- El frontend sigue siendo de la ventana de UI: este documento es el contrato,
  no una implementación.
