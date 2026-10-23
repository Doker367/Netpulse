# Handoff — Motor de administración de alertas (silencios + reglas)

Lo consume el agente de **UI** (`netpulse-ui`, `app/static/dashboard.html`). Esta
ola es **solo backend**: aquí va el contrato exacto de la API y un mock del panel
para que la UI se conecte sin adivinar. No se tocó ningún archivo de frontend.

- Rama backend: `feat/alerts-admin` (`../netpulse-alerts`, worktree).
- Servicio: `app/services/alerts_admin_svc.py`
- Router: `app/routers/alerts_admin.py`
- Prefijo: `/api/alerts/admin`
- Autenticación: `Authorization: Bearer <JWT>` en los cuatro endpoints.

## Fuentes de datos (leer antes de conectar)

- **Silencios** → API **v2** de Alertmanager (`GET/POST /api/v2/silences`,
  `DELETE /api/v2/silence/{id}`).
- **Reglas** → API **v1** de Prometheus (`GET /api/v1/rules`). Alertmanager **no
  tiene** endpoint de reglas: las reglas de alerta viven en Prometheus
  (`config/prometheus-rules.yml`).
- URLs por defecto (compatibles con `docker-compose.monitoring.yml`):
  `http://localhost:9093` (Alertmanager) y `http://localhost:9090` (Prometheus).
  Sobrescribibles con `NETPULSE_ALERTMANAGER_URL` y `NETPULSE_PROMETHEUS_URL`.
  **Nota**: no existe variable central en `app/core/settings.py` para estas URLs;
  se leen por entorno en el servicio. La UI no necesita conocerlas.

## RBAC

| Endpoint | Rol mínimo |
|---|---|
| `GET /api/alerts/admin/silences` | `viewer` |
| `GET /api/alerts/admin/rules` | `viewer` |
| `POST /api/alerts/admin/silences` | `operator` |
| `DELETE /api/alerts/admin/silences/{id}` | `operator` |

`admin` incluye a `operator`. Sin token → `401`. Rol insuficiente → `403`
`{"detail": "Acceso denegado — se requiere rol 'operator' o superior"}`.

---

## 1) `GET /api/alerts/admin/silences`

Silencios **activos ahora** (los expirados se filtran). La UI nunca debe mostrar
silencios vencidos.

### Respuesta `200`

```json
{
  "status": "success",
  "total": 1,
  "silences": [
    {
      "id": "3f9a...uuid",
      "matchers": [{"name": "device_id", "value": "cisco-core-01", "isRegex": false}],
      "startsAt": "2026-10-23T09:00:00.000000Z",
      "endsAt": "2026-10-23T11:00:00.000000Z",
      "createdBy": "operator",
      "comment": "mantenimiento",
      "expires_in": 7200
    }
  ]
}
```

| Campo | Tipo | Notas |
|---|---|---|
| `id` | string | UUID del silencio en Alertmanager |
| `matchers` | array | `{name, value, isRegex}` |
| `startsAt` / `endsAt` | string | RFC3339 UTC (`Z`) |
| `createdBy` | string | `sub` del JWT que lo creó |
| `comment` | string | Puede ser `""` |
| `expires_in` | int | **Segundos restantes** hasta `endsAt` (≥ 0). Cálculo directo; la UI debe formatearlo (`2h`, `45m`, etc.) |

### Errores

| Código | `detail` literal | Texto UI a mostrar |
|---|---|---|
| `503` | `"Alertmanager no disponible: {causa}"` | "Alertmanager no disponible — no verificable" (con `{causa}` en tooltip/detalle) |
| `401` | (de JWTBearer) | "Sesión expirada" |
| `403` | (de RBAC) | "Sin permisos" |

`{causa}` es corta: `conexión rechazada`, `timeout`, `host no resuelto`,
`HTTP 500: <msg>`.

---

## 2) `POST /api/alerts/admin/silences`

Crea un silencio. `createdBy` se toma del JWT (no lo manda la UI).
`startsAt = ahora`, `endsAt = ahora + duration`.

### Request

```json
{ "device_id": "cisco-core-01", "duration": "2h", "comment": "mantenimiento" }
```

o con `group`:

```json
{ "group": "Core", "duration": "8h", "comment": "ventana de cambios" }
```

o con matchers explícitos:

```json
{ "matchers": [{"name": "alertname", "value": "DeviceDown", "isRegex": false}], "duration": "24h" }
```

| Campo | Tipo | Obligatorio | Notas |
|---|---|---|---|
| `matchers` | `[{name, value, isRegex}]` | no* | Alternativa explícita |
| `device_id` | string | no* | Equivale a `{name:"device_id", value, isRegex:false}` |
| `group` | string | no* | Equivale a `{name:"group", value, isRegex:false}` |
| `duration` | string | **sí** | `"2h"` \| `"8h"` \| `"24h"` \| `"7d"` |
| `comment` | string | no | Motivo (recomendado) |

\* Al menos **uno** de `matchers`, `device_id`, `group`. Si se pasan `device_id`
y `group` juntos (sin `matchers`), se generan los dos matchers.

### Respuesta `201`

```json
{
  "status": "created",
  "id": "3f9a...uuid",
  "startsAt": "2026-10-23T09:00:00.000000Z",
  "endsAt": "2026-10-23T11:00:00.000000Z",
  "createdBy": "operator",
  "comment": "mantenimiento"
}
```

### Errores

| Código | `detail` literal | Texto UI |
|---|---|---|
| `422` | `"Al menos un matcher es requerido (usa matchers, device_id o group)"` | "Indica qué silenciar (dispositivo o grupo)" |
| `422` | `"Duración inválida: usa una de 2h, 8h, 24h, 7d"` | "Duración no permitida" |
| `503` | `"Alertmanager no disponible: {causa}"` | "Alertmanager no disponible — no se pudo crear" |
| `403` | (de RBAC) | "Necesitas rol operator" |

---

## 3) `DELETE /api/alerts/admin/silences/{id}`

Cancela (expira) un silencio. Registra la acción en el audit trail
(`action="silence_delete"`).

### Respuesta `200`

```json
{ "status": "deleted", "id": "3f9a...uuid" }
```

### Errores

| Código | `detail` literal | Texto UI |
|---|---|---|
| `404` | `"Silencio no encontrado: {causa}"` | "El silencio ya no existe (quizá venció)" |
| `503` | `"Alertmanager no disponible: {causa}"` | "Alertmanager no disponible — no se pudo cancelar" |
| `403` | (de RBAC) | "Necesitas rol operator" |

---

## 4) `GET /api/alerts/admin/rules`

**Solo lectura** en esta ola. Reglas de alerta desde Prometheus.

### Respuesta `200`

```json
{
  "status": "success",
  "total": 13,
  "rules": [
    {
      "name": "DeviceDown",
      "type": "alerting",
      "expr": "netpulse_devices_up == 0",
      "for": 300,
      "severity": "critical",
      "labels": {"severity": "critical", "team": "netops"},
      "group": "netpulse-devices"
    }
  ]
}
```

| Campo | Tipo | Notas |
|---|---|---|
| `name` | string | Nombre de la regla (ej. `DeviceDown`) |
| `type` | string | `alerting` o `recording` |
| `expr` | string | Expresión PromQL |
| `for` | int | Segundos del `for` (0 si no hay cláusula) |
| `severity` | string | `labels.severity` (`""` si no existe) |
| `labels` | object | Etiquetas completas |
| `group` | string | Grupo de reglas de Prometheus |

### Errores

| Código | `detail` literal | Texto UI |
|---|---|---|
| `503` | `"Prometheus no disponible: {causa}"` | "Prometheus no disponible — reglas no verificables" |
| `401`/`403` | (de auth/RBAC) | "Sesión expirada" / "Sin permisos" |

---

## Mock sugerido del panel de silencios (para la UI, sin tocar frontend)

**Tabla de silencios activos** (datos de `GET /silences`):

```
┌ Silencios activos (2)                              [ + Nuevo silencio ]
├──────────────┬────────────┬────────────┬────────────┬──────────────┬─────────
│ Alcance      │ Creado por │ Vence en   │ Comentario │              │
├──────────────┼────────────┼────────────┼────────────┼──────────────┼─────────
│ device_id=cisco-core-01 │ operator │ 1h 45m │ mantenimiento │ [Cancelar]
│ group=Core   │ admin      │ 6d 2h     │ ventana      │ [Cancelar]
└──────────────┴────────────┴────────────┴────────────┴──────────────┴─────────
```

- Columna "Vence en" formatea `expires_in` (segundos → `Xm`/`Xh`/`Xd`).
- "Alcance" renderiza los `matchers` como `name=value` unidos por comas.
- "Cancelar" muestra un **panel inline de confirmación** (no `confirm()` nativo):

```
┌ ¿Cancelar este silencio? ──────────────────────────────┐
│  device_id=cisco-core-01 · vence en 1h 45m              │
│  [ Cancelar ] [ Volver ]                                │
└─────────────────────────────────────────────────────────┘
```

- Al cancelar → `DELETE /silences/{id}`. `200` → quitar fila + toast "Silencio
  cancelado". `404` → recargar lista (ya no existe). `503` → toast de estado
  honesto (ver tablas de arriba).

**Formulario "Nuevo silencio"** (inline):

```
┌ Nuevo silencio ────────────────────────────────────────┐
│  Alcance:  (•) Dispositivo  ( ) Grupo  ( ) Matchers    │
│   device_id: [________________]                        │
│  Duración:  [ 2h ▾ ]  (2h / 8h / 24h / 7d)             │
│  Comentario: [________________________________]        │
│                                          [ Crear ]     │
└─────────────────────────────────────────────────────────┘
```

- "Matchers" usa inputs dinámicos `name` / `value` / `isRegex`.
- `201` → cierra panel, refresca lista, toast "Silencio creado".
- `422` → resaltar el campo del error (`matcher` vacío o `duration` inválida).
- `503` → toast "Alertmanager no disponible — no se pudo crear".

**Reglas** (pestaña aparte): tabla de solo lectura con `name`, `expr`, `for`
(humanizado: `300` → `5m`), `severity` (badge de color), `labels`. Sin botones
de edición en esta ola. `503` → "Prometheus no disponible — reglas no
verificables".

---

## GEN-2 (documentado, NO implementado): escritura de reglas

La escritura/edición de reglas queda **fuera de esta ola** a propósito.

### Endpoint propuesto

```
PUT /api/alerts/admin/rules/{group}/{name}
```

Request:

```json
{
  "expr": "netpulse_devices_up == 0",
  "for": "5m",
  "severity": "critical",
  "labels": {"team": "netops"},
  "summary": "Dispositivo caído",
  "description": "..."
}
```

### Por qué no va en esta ola

1. **Las reglas no son un API de escritura de Alertmanager ni de Prometheus.**
   Prometheus solo lee reglas desde `rule_files` (ficheros YAML en disco,
   `config/prometheus-rules.yml`). Editarlas exige reescribir el fichero y
   recargar Prometheus (`POST /-/reload`, protegido con `--web.enable-lifecycle`),
   que hoy **no** está habilitado en `docker-compose.monitoring.yml`.
2. **Riesgo de validación de `expr`**: un PromQL inválido rompe la evaluación de
   todo el grupo de reglas (no solo una). Se necesita validar sintaxis contra
   Prometheus (`/api/v1/query` de prueba) y un diff/rollback atómico.
3. **Riesgo operativo**: escribir reglas es mutación de la config de
   observabilidad; un error silencia alertas críticas. Requiere audit trail,
   bloqueo de concurrencia y revisión. Es material de una ola propia.

### Validación de `expr` recomendada (cuando se implemente)

- Parseo sintáctico con `promql-parser` (o similar) + validación semántica
  ejecutando `GET /api/v1/query?query=<expr>` contra Prometheus en dry-run.
- Rechazar `expr` que ya esté en uso o que cambie de tipo (vector/instant) sin
  querer.
- Guardar la versión anterior para rollback y loguear `rule_update` en audit.

### Riesgos a mitigar

- Fichero YAML corrupto → Prometheus no arranca tras reload.
- Escrituras concurrentes desde dos operadores → último gana (sin versionado).
- Permisos: exigir `admin` (no `operator`) para mutar reglas.
