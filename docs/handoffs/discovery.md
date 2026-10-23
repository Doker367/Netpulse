# Handoff — Auto-discovery (Ola 3, UI)

Contrato del backend de descubrimiento de vecinos. Lo consume la ventana A
(`../netpulse-ui`, `app/static/dashboard.html`). Esta tarjeta **no** toca el
frontend: el wizard se implementa en Ola 3 con este documento.

- Rama backend: `feat/discovery` (`../netpulse-discovery`)
- Servicio: `app/services/discovery_svc.py`
- Router: `app/routers/discovery.py`
- Prefijo: `/api/discovery`
- Autenticación: `Authorization: Bearer <JWT>` en los tres endpoints.

## RBAC

| Endpoint | Rol mínimo |
|---|---|
| `POST /api/discovery/scan` | `viewer` |
| `GET /api/discovery/candidates` | `viewer` |
| `POST /api/discovery/import` | `operator` |

`admin` incluye a `operator`. Sin token: `401`. Con rol insuficiente: `403`
con `{"detail": "Acceso denegado — se requiere rol 'operator' o superior"}`.

El escaneo es de lectura (igual que `GET /api/topology/map`) y puede tardar;
la importación es escritura y exige `operator`.

---

## 1) `POST /api/discovery/scan`

Lanza un escaneo y guarda el resultado como «último escaneo» en memoria.

### Request

```json
{
  "source_ids": ["cisco-core-01", "juniper-edge-01"],
  "metodo": "lldp"
}
```

| Campo | Tipo | Obligatorio | Valores |
|---|---|---|---|
| `source_ids` | `string[]` | no | IDs de `GET /api/devices`. Si se omite o es `null`, escanea **todos**. `[]` no escanea ninguno. |
| `metodo` | `string` | no | `"lldp"` (default) o `"cdp"` |

- LLDP reutiliza `napalm_svc.get_lldp_neighbors()` (incluye fallback
  `lldpcli show neighbors` por SSH).
- CDP ejecuta `show cdp neighbors detail` solo en drivers que lo hablan
  (`ios`, `nxos`, `iosxr`, `eos`). En el resto devuelve `sin_soporte` por
  equipo, no un error global.
- Orígenes sin usuario/contraseña se omiten con `sin_credenciales`.
- Orígenes solo SNMP se omiten con `sin_soporte`.
- Presupuesto global: 25 s (`NETPULSE_DISCOVERY_TIMEOUT`). Lo que no responda
  se marca `error` / `timeout` y el resto del escaneo continúa.

### Response `200`

```json
{
  "timestamp": "2026-10-22T23:40:12.123456Z",
  "metodo": "lldp",
  "origenes": [
    {
      "id": "cisco-core-01",
      "hostname": "192.0.2.10",
      "driver": "ios",
      "metodo": "lldp",
      "estado": "ok",
      "vecinos": 2,
      "candidatos": 1,
      "omitidos": [
        {
          "nombre": "vecino-sin-dns",
          "motivo": "Sin IP de gestión: el vecino no reportó dirección y el nombre no resuelve."
        }
      ]
    },
    {
      "id": "juniper-edge-01",
      "hostname": "192.0.2.11",
      "driver": "junos",
      "metodo": "lldp",
      "estado": "error",
      "vecinos": 0,
      "candidatos": 0,
      "omitidos": [],
      "motivo": "Timeout de conexión con el equipo.",
      "error_type": "timeout"
    }
  ],
  "candidatos": [
    {
      "ip": "192.0.2.50",
      "origen": "cisco-core-01",
      "origenes": ["cisco-core-01", "switch-edge-02"],
      "estado": "propuesto",
      "en_inventario": false,
      "nombre_sugerido": "juniper-edge-02",
      "plataforma_sugerida": "junos",
      "interfaz_local": "Gi0/1",
      "interfaz_remota": "Gi0/2"
    }
  ],
  "resumen": {
    "origenes": 2,
    "origenes_ok": 1,
    "origenes_con_error": 1,
    "vecinos": 2,
    "candidatos": 1,
    "omitidos": 1
  },
  "mensaje": null
}
```

### Semántica de `origenes`

`origenes` incluye **todas** las fuentes solicitadas, también las que fallaron.

| `estado` | Significado | Texto sugerido en UI |
|---|---|---|
| `ok` | Consulta correcta con ≥ 1 vecino | «Escaneado» |
| `sin_vecinos` | Consulta correcta, 0 vecinos | «Sin vecinos» |
| `sin_soporte` | Driver o modo de acceso no soporta el método | «Sin soporte para LLDP/CDP» |
| `sin_credenciales` | El equipo no tiene usuario ni contraseña | «Sin credenciales» |
| `error` | Fallo de conexión, timeout o driver | «No verificable» (gris/rojo, nunca verde) |

`motivo` (string) y `error_type` solo aparecen cuando hay problema.
`error_type` posibles: `timeout`, `auth_error`, `connection_error`,
`command_error`, `driver_error`, `not_found`, `internal_error`, `unknown`.

`omitidos` son vecinos vistos pero **no propuestos** porque no se pudo obtener
su IP de gestión. No son errores del origen; se muestran como nota informativa.
Nunca se inventa una IP.

### Semántica de `candidatos`

| Campo | Tipo | Notas |
|---|---|---|
| `ip` | `string` | IP de gestión normalizada. **Clave de deduplicación.** |
| `origen` | `string` | Primer equipo que lo vio (ID del inventario). |
| `origenes` | `string[]` | Todos los equipos que vieron el mismo vecino (dedup por IP). |
| `estado` | `string` | **Siempre `"propuesto"`.** Nunca `"importado"`. |
| `en_inventario` | `bool` | `true` si esa IP (o su nombre) ya está en el inventario. |
| `nombre_sugerido` | `string?` | Puede faltar si el vecino no reportó nombre. |
| `plataforma_sugerida` | `string?` | Driver NAPALM (`junos`, `ios`, …). Puede faltar: no hay evidencia suficiente. |
| `interfaz_local` | `string?` | Puerto del origen que ve al vecino. |
| `interfaz_remota` | `string?` | Puerto del vecino reportado por el protocolo. |

`plataforma_sugerida` es una **sugerencia**, no una certeza: sale del
inventario, del dato `Platform` de CDP o de una heurística por nombre. La UI
debe mostrarla como sugerencia y permitir corregirla.

`mensaje` explica en español por qué no hay candidatos (`null` cuando sí hay).

### Errores HTTP del scan

| Código | Cuándo | Cuerpo |
|---|---|---|
| `401` | Sin token o token inválido | `{"detail": "..."}` |
| `403` | Rol menor que `viewer` | `{"detail": "Acceso denegado…"}` |
| `422` | `metodo` distinto de `lldp`/`cdp`, o tipos inválidos | Error de validación de FastAPI |
| `503` | Fallo global (p. ej. inventario ilegible) | `{"detail": {"error": "No se pudo completar el escaneo.", "motivo": "..."}}` |

Un equipo caído **nunca** produce 500 ni 502: aparece en `origenes[].estado`.

---

## 2) `GET /api/discovery/candidates`

Devuelve el último escaneo guardado en memoria, con su timestamp. No vuelve a
consultar equipos.

### Response `200` con escaneo

Es exactamente el mismo objeto que devolvió `POST /scan` (incluye `timestamp`,
`metodo`, `origenes`, `candidatos`, `resumen`, `mensaje`).

### Response `200` sin escaneo previo

```json
{
  "timestamp": null,
  "metodo": null,
  "origenes": [],
  "candidatos": [],
  "resumen": {
    "origenes": 0,
    "origenes_ok": 0,
    "origenes_con_error": 0,
    "vecinos": 0,
    "candidatos": 0,
    "omitidos": 0
  },
  "mensaje": "Sin escaneo registrado. Ejecuta POST /api/discovery/scan."
}
```

Esto es **sin datos**, no un error: la UI muestra el estado vacío en gris, no
un toast rojo. El último escaneo vive en memoria y se pierde al reiniciar la
API; tras reiniciar, `timestamp` vuelve a `null`.

Errores: `401` / `403`.

---

## 3) `POST /api/discovery/import`

Registra en el inventario IPs del **último escaneo**. Usa
`inventory_svc.add_device()`, la misma función de registro que
`POST /api/devices` (no hay lógica duplicada).

### Request

```json
{
  "candidates": ["192.0.2.50", "192.0.2.51"],
  "username": "netops",
  "password": "••••••••",
  "group": "Access",
  "tipo": "switch",
  "driver": "junos"
}
```

| Campo | Tipo | Obligatorio | Notas |
|---|---|---|---|
| `candidates` | `string[]` | **sí** | IPs del último escaneo. Se deduplican; mínimo 1. |
| `username` | `string` | no | Usuario SSH para **todos** los importados. Default `""`. |
| `password` | `string` | no | Contraseña SSH para todos. Se cifra en el inventario. Default `""`. |
| `group` | `string?` | no | Grupo destino. `null` = sin grupo. |
| `tipo` | `string?` | no | `router` (default), `switch`, `firewall`, `server`. |
| `driver` | `string?` | no | Fuerza el driver para todos; sustituye a `plataforma_sugerida`. |

**Credenciales:** si el wizard no envía `username`/`password`, el equipo queda
registrado sin credenciales y no podrá ser sondeado hasta editarlo. La UI debe
pedirlas en el paso de resumen (se pueden reutilizar las del origen, pero el
usuario decide; el backend no las copia solo).

**Driver:** si no se envía `driver` y el candidato no trae
`plataforma_sugerida`, la importación falla con
«Plataforma no identificada…». No se adivina un driver.

### Response `200`

```json
{
  "timestamp": "2026-10-22T23:42:00.000000Z",
  "creados": [
    {
      "ip": "192.0.2.50",
      "id": "juniper-edge-02",
      "driver": "junos",
      "nombre_sugerido": "juniper-edge-02"
    }
  ],
  "ya_existian": [
    {
      "ip": "192.0.2.51",
      "id": "core-01",
      "motivo": "Ya existe en el inventario como 'core-01'."
    }
  ],
  "fallidos": [
    {
      "ip": "192.0.2.52",
      "motivo": "Plataforma no identificada. Indica el driver manualmente para importar."
    }
  ],
  "resumen": {
    "solicitados": 3,
    "creados": 1,
    "ya_existian": 1,
    "fallidos": 1
  }
}
```

Reglas aplicadas por candidato (nunca aborta el lote):

1. IP inválida → `fallidos` «Dirección IP inválida.».
2. IP ya registrada por `hostname` → `ya_existian` (no se vuelve a crear).
3. IP fuera del último escaneo → `fallidos` «No está en el último escaneo…».
4. Sin driver (ni forzado ni sugerido) → `fallidos` «Plataforma no identificada…».
5. `id` = slug de `nombre_sugerido`; si colisiona se añade `-2`, `-3`, …
6. El equipo se crea con `hostname = ip`, `port = 22`, `protocol = "ssh"`,
   `tags = ["discovery"]` y `description = "Descubierto vía LLDP|CDP desde <origen>"`.
7. Cualquier excepción de registro → `fallidos` con el motivo; HTTP sigue en 200.

**El último escaneo no se modifica.** Los candidatos siguen en
`estado: "propuesto"` después de importar y un segundo import de la misma IP
responde `ya_existian`. Para refrescar la tabla, la UI debe volver a escanear.

### Errores HTTP del import

| Código | Cuándo | Cuerpo |
|---|---|---|
| `401` | Sin token o inválido | `{"detail": "..."}` |
| `403` | Rol menor que `operator` | `{"detail": "Acceso denegado…"}` |
| `422` | `candidates` vacío, `tipo`/`driver` fuera del enum | Validación de FastAPI |

Los fallos por candidato van dentro del `200`, en `fallidos`.

---

## Wizard sugerido (Ola 3)

Cinco pasos dentro del panel HTML; sin fetch fuera de este contrato.

1. **Orígenes** — `GET /api/devices` (viewer). Lista con checkbox por equipo
   (id, hostname, driver, grupo). Selector de método: **LLDP** (default) /
   **CDP** (aviso: solo Cisco/Arista). Botón «Escanear ahora».
2. **Scan** — `POST /api/discovery/scan` con los IDs marcados y el método.
   Es síncrono y puede tardar hasta ~25 s: deshabilitar el botón y mostrar
   «Escaneando… no cierres esta vista». Un `503` es fallo global; un equipo
   caído no lo es.
3. **Candidatos** — tabla con checkbox: IP, nombre sugerido, plataforma
   sugerida (con etiqueta «sugerida»), origen(es), interfaces, y badge
   «Ya en inventario» cuando `en_inventario = true`. Las filas con
   `en_inventario` se muestran deshabilitadas (no seleccionables). Si
   `plataforma_sugerida` falta, badge ámbar «Plataforma sin identificar» y
   el resumen pedirá driver manual. Mostrar `origenes[].estado` de cada
   origen y sus `motivo`; los `omitidos` van como nota, no como error.
   Si `candidatos` está vacío: usar `mensaje` tal cual.
4. **Resumen** — seleccionados, credenciales SSH comunes (usuario/contraseña),
   grupo opcional, tipo y driver manual opcional (obligatorio si algún
   seleccionado no tiene plataforma). Aviso honesto: sin credenciales el
   equipo se registra pero no se podrá sondear.
5. **Importar** — `POST /api/discovery/import`. Renderizar los tres grupos:
   creados (verde), ya existían (gris/ámbar), fallidos (rojo con `motivo`).
   Texto de cierre: «N de M importados». Ofrecer «Escanear de nuevo», no
   recargar candidatos automáticamente (seguirían en `propuesto`).

### Textos de estado (usar literalmente)

- `ok` → «Escaneado»
- `sin_vecinos` → «Sin vecinos»
- `sin_soporte` → «Sin soporte para LLDP/CDP»
- `sin_credenciales` → «Sin credenciales»
- `error` → «No verificable»
- candidato → «Propuesto» (nunca «Importado»)
- `en_inventario: true` → «Ya en inventario»
- sin escaneo → «Sin escaneo registrado»
- sin resultados → el `mensaje` del backend

### Lo que la UI no debe asumir

- No hay WebSocket ni polling: el escaneo termina en la misma respuesta.
- No hay paginación; el inventario del laboratorio es pequeño.
- `timestamp` es ISO-8601 UTC con sufijo `Z`.
- El último escaneo se pierde al reiniciar la API.
- `plataforma_sugerida` puede ser incorrecta o faltar; es editable/override.

---

## Verificación de esta tarjeta

```bash
cd ../netpulse-discovery
.venv/bin/python -m pytest tests/ -q      # 126 tests OK
.venv/bin/python -m ruff check app tests  # All checks passed
```

Tests de `tests/test_discovery.py`: scan feliz, dedup de vecino repetido,
vecino sin IP resoluble, equipo solo SNMP, driver sin CDP, error/timeout por
equipo, parseo CDP, import de equipo existente, import creado, plataforma
desconocida, IP fuera del escaneo y RBAC de import.
