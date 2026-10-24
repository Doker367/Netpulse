# Handoff — NetBox: estado previo y resumen de sync legible

Contrato para la UI del panel NetBox. Esta tarjeta es **solo backend**: no se
modificó `app/static/dashboard.html`. Aquí está el comportamiento exacto de los
dos endpoints que la UI debe usar y cómo presentar el resultado al operador.

- Router: `app/routers/netbox.py`
- Prefijo: `/api/netbox`
- Autenticación: `Authorization: Bearer <JWT>`
- RBAC: **solo admin**. `operator`/`viewer` → `403` con
  `{"detail": "Acceso denegado — se requiere rol 'admin' o superior"}`.
- Errores: siempre `HTTPException`, cuerpo `{"detail": "<texto en español>"}`,
  **sin traceback**. El `detail` se puede mostrar tal cual.

---

## 1) `GET /api/netbox/status` — checklist antes de sincronizar

No contacta con NetBox: responde al instante y sirve para que la UI sepa si
merece la pena enseñar el botón de sincronizar. Un NetBox sin configurar **no
es un error de este endpoint**: es `200` con `configurado: false`.

### Respuesta `200`

```json
{
  "configurado": true,
  "url_enmascarada": "http://netbox.lab:8000",
  "tiene_token": true,
  "alcance": "Dispositivos de NetBox → devices.yaml. Campos: id local 'netbox-<id>', hostname (primary_ip sin máscara), driver (fabricante/modelo), type (rol), tags (rol y site), description (comments), netbox_id, netbox_name y netbox_url. Solo se importan dispositivos con primary_ip.",
  "no_hace": [
    "no borra dispositivos del inventario local (los equipos que no están en NetBox se conservan)",
    "no sobrescribe credenciales locales (username, password, enable_password)",
    "no cambia el id local de un equipo existente (solo lo asigna al crear)",
    "no sobrescribe el puerto de un equipo ya existente (solo fija 22 al crear)",
    "no escribe ni modifica nada en NetBox (para eso existe POST /api/netbox/sync/push)",
    "no importa sites, racks, VLANs, prefijos ni direcciones IP (solo dispositivos)"
  ],
  "sync_endpoint": "/api/netbox/sync",
  "mensaje": ""
}
```

| Campo | Tipo | Notas |
|---|---|---|
| `configurado` | bool | `true` solo si hay URL **y** token no vacíos. |
| `url_enmascarada` | string | URL configurada; si llevaba `usuario:contraseña@`, se sustituye por `***@`. |
| `tiene_token` | bool | Hay token en `NETBOX_TOKEN` o en `config/.netbox_token`. Nunca se devuelve el token. |
| `alcance` | string | Qué se importa y con qué campos. Cambia si cambia el backend: no hardcodear en la UI. |
| `no_hace` | array[string] | Lista literal de garantías. Mostrarla como viñetas en «¿Qué es NetBox?». |
| `sync_endpoint` | string | Ruta a la que hay que hacer `POST`. |
| `mensaje` | string | `""` si está configurado; si no, el mismo texto que devolvería el sync como `detail`. |

Variables reales del repo: `NETBOX_URL` (por defecto `http://localhost:8000`) y
`NETBOX_TOKEN` (o el archivo `config/.netbox_token`). Si el token está vacío,
`configurado` es `false` aunque la URL tenga valor.

---

## 2) `POST /api/netbox/sync` — importar y resumir

Importa dispositivos de NetBox a `config/devices.yaml`. **NetBox manda** en los
campos importados; el resto del inventario local no se toca.

### Respuesta `200` (contrato estable)

```json
{
  "status": "ok",
  "solicitados": 3,
  "creados": ["netbox-100", "netbox-200"],
  "actualizados": ["core-1"],
  "sin_cambios": 0,
  "saltados": [],
  "errores": [],
  "inventario_total": 3,
  "netbox_total": 3
}
```

Ejemplo con problemas (siempre `200`, nunca listas vacías como excusa):

```json
{
  "status": "partial",
  "solicitados": 3,
  "creados": [],
  "actualizados": [],
  "sin_cambios": 0,
  "saltados": [
    {
      "device_id": "netbox-10",
      "motivo": "sin primary_ip en NetBox: no hay dirección de gestión para monitorizar"
    }
  ],
  "errores": [
    {
      "device_id": "netbox-?",
      "detalle": "datos incompletos en NetBox: 'id'"
    }
  ],
  "inventario_total": 1,
  "netbox_total": 3
}
```

| Campo | Tipo | Significado |
|---|---|---|
| `status` | `"ok"` \| `"partial"` | `partial` si `saltados` o `errores` tienen algo. Un fallo global **no** es `partial`: es un `4xx/5xx` con `detail`. |
| `solicitados` | int | Dispositivos devueltos por NetBox en esta sync. |
| `creados` | array[string] | `device_id` locales nuevos, ordenados. |
| `actualizados` | array[string] | `device_id` locales cuyo contenido cambió, ordenados. |
| `sin_cambios` | int | Equipos que ya estaban idénticos; no se reescribieron. |
| `saltados` | array[obj] | `{device_id, motivo}` ordenados por `device_id`. |
| `errores` | array[obj] | `{device_id, detalle}` ordenados por `device_id`. |
| `inventario_total` | int | Equipos en `devices.yaml` **después** de la sync (incluye los locales que NetBox no conoce). |
| `netbox_total` | int | Equipos leídos de NetBox. Hoy coincide con `solicitados` porque no hay filtros. |

Invariante útil para la UI:

```
len(creados) + len(actualizados) + sin_cambios
  + len(saltados) + len(errores) == solicitados
```

**Orden determinista:** las tres listas de `device_id` y los objetos de
`saltados`/`errores` van ordenados por `device_id`, aunque NetBox los devuelva
desordenados. Dos syncs con los mismos datos producen el mismo JSON.

### Campos heredados (compatibilidad con el panel actual)

Se mantienen para no romper `dashboard.html`:

| Campo | Equivale a |
|---|---|
| `created` | `len(creados)` |
| `updated` | `len(actualizados)` (no incluye `sin_cambios`) |
| `skipped` | `len(saltados)` (no incluye `errores`) |

La UI nueva debe usar los campos del contrato y dejar de depender de estos.

### Cuándo se escribe el archivo

- Solo se escribe si hay algo que crear o actualizar. Una sync con todo
  `sin_cambios` no toca el archivo (no cambia su mtime).
- La escritura es **atómica**: tmp en el mismo directorio + `os.replace`. Si
  falla, `devices.yaml` queda exactamente como estaba.
- Se invalida la caché de inventario tras escribir, así el panel ve los
  equipos nuevos sin reiniciar.

---

## Errores del sync (`detail` literal)

| Código | `detail` | Causa real |
|---|---|---|
| `503` | `NetBox no configurado: define NETBOX_URL y NETBOX_TOKEN (o crea config/.netbox_token)` | Falta URL o token. |
| `503` | `NetBox no disponible: conexión rechazada` | Conexión TCP rechazada. |
| `503` | `NetBox no disponible: tiempo de espera agotado` | Timeout de conexión/lectura. |
| `503` | `NetBox no disponible: host no resuelto` | DNS. |
| `503` | `NetBox no disponible: host no alcanzable` | Red/host inalcanzable. |
| `503` | `NetBox no disponible: error 502 de NetBox` | NetBox (o su proxy) devolvió 5xx. |
| `503` | `NetBox no disponible: error TLS` | Certificado/SSL. |
| `500` | `No se pudo leer el inventario local: <causa>` | `devices.yaml` corrupto, ilegible o no es un objeto/lista. |
| `500` | `No se pudo escribir el inventario local: <causa>. No se aplicó ningún cambio.` | Fallo de disco/permisos; escritura atómica, nada a medias. |
| `401/403` | `Error de NetBox: <texto de NetBox>` | Token inválido o sin permisos (4xx se conserva). |
| `403` | `Acceso denegado — se requiere rol 'admin' o superior` | Usuario no admin. |

Regla de oro para la UI: **un fallo nunca se pinta como «0 equipos»**. Si
`!res.ok`, leer `detail` y mostrar el error; no asumir listas vacías.

---

## Qué se importa y qué NO se toca

| Campo local | ¿Se importa? | Origen / regla |
|---|---|---|
| `id` | Solo al crear | `netbox-<id de NetBox>`. En equipos existentes **se conserva** el id local. |
| `hostname` | Sí | `primary_ip.address` sin máscara. Sin `primary_ip` el equipo **se salta** (no se inventa `127.0.0.1`). |
| `port` | Solo al crear | `22`. En equipos existentes se conserva el puerto actual. |
| `driver` | Sí | Fabricante/modelo: cisco→`ios`, arista→`eos`, juniper→`junos`, mikrotik→`ros`, fortinet→`fortios`, linux→`linux`; resto `ios`. |
| `type` | Sí | Rol: contiene switch/firewall/server; resto `router`. |
| `tags` | Sí | `["netbox", <slug o nombre del rol>, <slug o nombre del site>]`. |
| `description` | Sí | `comments` de NetBox; `""` si está vacío. |
| `netbox_id` | Sí | Id numérico de NetBox. |
| `netbox_name` | Sí | Nombre en NetBox (o `netbox-<id>` si no tiene). |
| `netbox_url` | Sí | Enlace directo al dispositivo en NetBox. |
| `credentials` | **No** | Usuario/contraseña locales intactos. |
| `enable_password`, `username`, `password` | **No** | Intactos. |
| `cli_port`, `ssh_port` y demás campos locales | **No** | Intactos. |
| `group`, `snmp`, `site` local, etc. | **No** | Intactos. |
| `options` y otras claves del YAML | **No** | Se conservan al reescribir. |
| Equipos locales ausentes en NetBox | **No se borran** | Se quedan como están. |
| NetBox (sites, VLANs, IPAM, comentarios) | **No se escribe** | La sync es de solo lectura sobre NetBox. |

Cómo se empareja un dispositivo de NetBox con uno local:

1. Por `netbox_name` igual al `id` local (equipos dados de alta a mano).
2. Por `netbox-<id>` (equipos creados por una sync anterior).

---

## Textos sugeridos para el panel

### «¿Qué es NetBox?»

> **NetBox es el inventario central de la red** (DCIM/IPAM): qué equipos
> existen, dónde están y cuál es su IP de gestión.
>
> NetPulse puede leer esos equipos y traerlos al inventario local para
> monitorizarlos. La sincronización **solo lee de NetBox**:
>
> - no borra equipos locales;
> - no cambia tus credenciales, puertos ni el id de un equipo existente;
> - no importa sites, VLANs, prefijos ni direcciones IP.
>
> Si un equipo de NetBox no tiene `primary_ip`, se omite y se explica por qué:
> sin dirección de gestión no se puede monitorizar.

Mostrar `alcance` y `no_hace` desde `/api/netbox/status` en vez de copiar este
texto a mano, para que no se desincronice del backend.

### Antes de sincronizar

`configurado: true`:

> NetBox configurado en `{url_enmascarada}`. Se importarán los dispositivos al
> inventario local; los equipos que solo existen en NetPulse se conservan.

`configurado: false`:

> Mostrar `mensaje` tal cual (empieza por «NetBox no configurado:…») y el botón
> de sync deshabilitado. No es un fallo del panel: falta configuración.

### Después de sincronizar (`status: ok`)

Titular:

> Sincronización completada: **{n} creados · {m} actualizados · {k} sin cambios**

Detalle plegable:

> Inventario local: **{inventario_total}** equipos · NetBox: **{netbox_total}** equipos.

### Después de sincronizar (`status: partial`)

Titular en ámbar, nunca en verde:

> Sincronización parcial: **{len(saltados) + len(errores)}** equipos no se importaron.

Lista:

- **Saltados:** `device_id` — `motivo` (p. ej. «sin primary_ip en NetBox…»).
- **Errores:** `device_id` — `detalle`.

Acción sugerida: «Corrige esos equipos en NetBox y vuelve a sincronizar. El
resto ya está en el inventario local».

### Nunca hacer

1. Pintar verde/«Sincronizado» si el `fetch` no devolvió `2xx`.
2. Sustituir un error por «0 creados, 0 actualizados».
3. Mostrar listas de `device_id` sin ordenar (el backend ya las ordena; no
   reordenar por otro criterio).
4. Presentar `sin_cambios` como fallo: es el caso normal de una segunda sync.

---

## Pendientes explícitos

- **Frontend sin tocar.** Este documento es el contrato; el panel actual sigue
  leyendo `created`/`updated`/`skipped` y no llama a `/api/netbox/status`.
- **Sin verificación contra un NetBox real.** Los tests mockean
  `httpx.AsyncClient`; no se abrió ninguna conexión ni se ejecutó
  `docker-compose.netbox.yml`.
- **`POST /api/netbox/sync/push` no se tocó.** Sigue enviando `device_type`,
  `role` y `site` a `null` (NetBox suele rechazarlo) y devolviendo
  `{created, skipped, errors}`. Queda para otra tarjeta.
- **Equipos sin `primary_ip`:** cambio de comportamiento respecto al sync
  anterior (que inventaba `127.0.0.1`). Ahora se saltan con motivo; hasta que
  tengan IP de gestión en NetBox no se monitorizan.
- **Renombrado de un equipo local emparejado por nombre:** si NetBox cambia el
  nombre de un equipo que se emparejó por `netbox_name == id local` (y no por
  `netbox-<id>`), se crea una entrada nueva y la antigua se conserva (nunca se
  borra). Revisar duplicados tras renombrados.
- **Syncs concurrentes:** no hay bloqueo entre dos `POST /sync` simultáneos.
  La escritura es atómica, pero la última gana.
- **`netbox_total` == `solicitados`** mientras la sync no acepte filtros.
