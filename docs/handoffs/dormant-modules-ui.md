# Handoff — A · UI módulos dormidos (Grupos, NetBox, Compliance por rol)

Rama: `feat/dormant-modules-ui` · Zona: `app/static/dashboard.html` (única) · Fecha: 2026-10-22

## Contexto corregido

La tarjeta original pedía crear Compliance. **No era cierto**: `#v-compliance`,
`loadCompliance()`, `checkCompliance()`, el ítem de sidebar y las claves i18n ya
existían. El trabajo real fue: crear **Grupos** (dormido, cero fetch previo) y
**NetBox** (dormido, 0 apariciones de «netbox»), y hacer **honesto** el flujo de
Compliance con roles y estados vacíos.

## Contratos usados (verificados en el código backend, no inventados)

| Endpoint | Roles | Respuesta / estados |
|---|---|---|
| `GET /api/groups` | viewer+ | `200 [{name, description, device_count}]` |
| `POST /api/groups` | admin | `201 GroupResponse` · `409` duplicado · `403` no admin |
| `GET /api/groups/{g}/devices` | viewer+ | lista · `404` si vacío o inexistente |
| `GET /api/netbox/sites` | admin | lista de sites · `503` NetBox caído · `403` no admin |
| `POST /api/netbox/sync` | admin | `{created, updated, skipped}` — no hay «conflictos» en el backend, no se inventan |
| `POST /api/compliance/check` (con `device_ids`) | admin | `{total, compliant, non_compliant, results: {id: {compliant, violations}}}` |
| `POST /api/compliance/baseline` / `GET .../baselines` | admin | baseline por grupo · `baselines` → lista de nombres de grupo |
| `GET /api/compliance/report` | admin | `{summary: {total, compliant, non_compliant, errors}, devices: {id: {...}}}` |

## Qué se implementó

### 1) Grupos — nueva vista `#v-groups` (sidebar: INFRAESTRUCTURA)

- Tabla de grupos con `device_count` real + refresco.
- Botón «Ver equipos» por fila: `GET /api/groups/{g}/devices`; `404` →
  «sin equipos asociados» (estado honesto, no error genérico).
- Estado vacío explicado: «Sin grupos definidos».
- Alta inline **solo** si `currentUser.role === 'admin'`. Si el POST devuelve
  `403` → toast «Crear grupos requiere rol admin»; `409` → el `detail` real del
  backend (grupo duplicado). Para operator la tarjeta de alta muestra el aviso
  🔒 «requiere rol admin» en gris.

### 2) NetBox — nueva vista `#v-netbox` (sidebar: ADMINISTRACIÓN)

- Guard de rol: sin ser admin, la vista completa es «🔒 Requiere rol admin»
  (gris). Los endpoints `/api/netbox/*` son solo admin.
- Tarjeta de disponibilidad sondeando `GET /api/netbox/sites`:
  - `200` → punto verde «NetBox responde» + tabla de sites (estado, región, equipos).
  - Sites vacíos → «NetBox responde, pero sin sites definidos» (gris).
  - `503` o fallo de red → «NetBox no disponible» en **gris**, nunca verde ni rojo de alarmismo.
- Botón «Sincronizar» con **confirmación inline** (mismo patrón que el panel de
  la bulk-bar: panel inline con descripción + Ejecutar/Cancelar, sin `prompt()`).
- Resultado veraz: «X creados · Y actualizados · Z omitidos» a partir de la
  respuesta real. `403`/`503`/otros → mensaje honesto correspondiente.

### 3) Compliance — existente, no duplicado

- Si `currentUser.role !== 'admin'`: la vista muestra «🔒 Cumplimiento requiere
  rol admin» y no hace fetch (antes la llamada explotaba en 403 con mensaje
  genérico / loop de «Cargando...»). Ítem de sidebar se conserva (visibilidad
  con guard en la vista, coherente con «todos los grupos visibles, RBAC valida
  cada acción» del código existente).
- Para admin:
  - El `<select>` hardcodeado Core/Edge/Access se cambió por `<input>` +
    `<datalist>` poblado con los grupos **reales** de `GET /api/groups` (fín de
    opciones prefabricadas). Si la lista no pudo verificarse, se avisa.
  - El textarea ya no trae un baseline de ejemplo pre-llenado (evita guardar un
    baseline ficticio por accidente): ahora es `placeholder`.
  - Sin equipos cargados → nota «Sin equipos cargados del inventario todavía».
  - `checkCompliance()` ahora verifica `r.ok`. **Bug de honestidad corregido**:
    antes un 403 renderizaba ✅ para todos los equipos (el fallback
    `d.compliant !== false`). Ahora responde el formato dict real del backend
    (`{total, compliant, non_compliant, results:{id:…}}`), con resumen
    `okN/total equipos cumplen` y ❌ veraz por dispositivo.
  - `setBaseline()` ahora verifica `r.ok` (antes toasta éxito aunque fallara).
  - Nuevo: botón «Reporte completo» (`GET /api/compliance/report`) con
    `okN/total` + rows; sin equipos → estado vacío honesto.

### 4) i18n

- Bloque nuevo `Object.assign` en **es y en** con las claves nuevas
  (`nav_groups`, `nav_netbox`, `view_groups`, `view_netbox`, `grp_*`, `nb_*`,
  `comp_*`, `np_unverified` «No verificable» / «Not verifiable»).
- `swView()` ganó dos ramas (`groups`, `netbox`); ninguna vista existente cambió
  su routing. Hash `#groups` / `#netbox` funciona por deep-link.

## Estados honestos aplicados (reglas del PRODUCT.md)

- Sin datos o API caída → **gris** «No verificable» (`np_unverified`, con el
  helper `npGray()`), jamás verde.
- Ninguna respuesta simulada: todos los contadores/provisiones vienen del
  backend. Lo que el backend no devuelve no se pinta.

## Verificación realizada

- `pytest tests/test_dashboard_routes.py -q` → **6 passed**.
- `<script>` inlines extraídos → `node --check` → **OK**.
- Chequeo de claves: todas las claves nuevas tienen definición en **es y en**
  (name-defs = 2). Las `data-i18n` dinámicas (`${key}`, `${k}`, etc.) son
  verbales/claves dinámicas preexistentes.
- Nota menor preexistente (fuera de alcance del brief, no se tocó):
  `data-i18n="nav_terminal"` ("Console") no tiene clave definida en `I18N`;
  al cambiar de idioma el menú de Console mostrará el literal. Sugerido para
  una próxima limpieza de i18n general del panel.

## Cómo verificar manualmente

```bash
cd ../netpulse-ui && .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8083
# http://127.0.0.1:8083
```

- **operator**: ve Grupos (lista + device_count, alta en gris «requiere rol
  admin»), ve NetBox «require rol admin», ve Compliance «requiere rol admin».
- **admin**: Grupos con alta real; NetBox con disponibilidad + sync inline;
  Compliance 3 pasos + Reporte completo; estados vacíos según datos reales.

## Merge

UI pertenece a la ventana A y es dueña de `dashboard.html` en esta ola. No se
tocó `app/main.py` ni routers. Sin regresiones de navegación: todas las ramas
`swView` previas intactas y el alias de hash `#monitoreo → monitoring` se
conserva.
