# Handoff — Refactor UI paso 1: `core.js` (TARJETA 10C)

Fecha: 2026-10-24 · Rama: `feat/ui-core` · Commit: `refactor(ui): extraer core.js sin cambio de comportamiento`
Base: `main` · Zona: `app/static/dashboard.html` (solo extracción), `app/static/js/core.js` (nuevo), este doc.

## Qué es core.js

`app/static/js/core.js` (~830 líneas) contiene EXCLUSIVAMENTE la infraestructura
compartida del panel, extraída línea a línea del `<script>` inline de
`dashboard.html` (path del refactor: las vistas se extraen en pasos posteriores,
esta tarjeta no toca ninguna vista):

| Bloque | Contenido nombre a nombre |
|---|---|
| Base API | `const A` (origen + `/api`) |
| Estado de sesión | `let t` (JWT), `let currentUser`, `let _refreshTimer`, `let _np401Done`, `const _npOrigFetch`, `const NP_REFRESH_MS` |
| i18n | `const I18N` **completo** (es/en, las 22 llamadas `Object.assign(I18N…)` íntegras y sin claves nuevas ni perdidas), `function setLang`, `function __t` (el traductor se llama `__t`, no `t`: `t` es el token) |
| Avisos genéricos | `function toast`, `function showLoginError` (compartidos por varias vistas; no son solo de una) |
| Sesión | `function h` (headers auth), interceptor 401 de `window.fetch` (exclusiones `/auth/login`, `/auth/refresh`, `/auth/me` + 401 de SFTP vía `detail`), `function sessionExpired`, `function _npTick`, `function scheduleRefresh`, `function stopRefresh`, `function refreshSession`, `function enterSession`, `function logout`, `function restoreSession` |
| Estado recordado | `_npStateKey`, `_npStateGet`, `_npStateSet`, `_npStateClear` (prefijo por usuario `np_u:<username>:`) |
| Exposición | `window.logout = logout;` … (bloque final con la API pública del core, ver abajo) |

Verificación de equivalencia: cada línea borrada del inline aparece en `core.js`
en el mismo orden (auditado con script). Únicas transformaciones, ambas mecánicas:
1. El `let t = '', devices = [], …, currentUser = null;` combinado se partió:
   sesión (`t`, `currentUser`) → core.js; estado de vista (`devices`, `sel`,
   `aTab`, `cmdH`, `bSel`, `bMode`) → queda en el inline tal cual.
2. La IIFE `(async function restoreSession() { … })();` es ahora
   `async function restoreSession() { … }` en core.js; **la invocación sigue
   dentro del inline en el punto exacto** donde estaba (para no adelantar el
   `fetch` de `/auth/me` respecto del comportamiento original).

### Lo que se decidió NO mover (y por qué)

- `swView` — NO es infraestructura pura: es el despachador que se acopla a
  loaders de cada vista (`loadBk`, `loadInventory`, `loadMonitoring`, …).
  Decisión: queda en dashboard.html; cuando se extraigan vistas habrá que
  decidir si swView mantiene los `if (v === …)` con llamadas directas o pasa a
  un registro de vistas.
- `login()` — manipula la pantalla de login (DOM de la vista de login) y muestra
  el texto `r.status === 401/429` que cubren los tests de rutas.
- `esc`, `escAttr`, `IC`, `icon`, `iconTextHTML`, `setIconText`, `PALETTES`,
  tema/paleta — genéricos-leaning pero fuera del alcance de la tarjeta; core
  los usa en tiempo de llamada (ver enlaces cruzados). Podrían migrar en un
  paso posterior si se quiere core autosuficiente.
- `toggleUserMenu`, listener global de cierre de menú, `togSide`, `npExport`.

### Enlaces cruzados que quedan (resueltos en tiempo de llamada, como antes)

- core → dashboard: `setIconText` (en `setLang`/`toast`), `updateDashboardHero`
  (en `setLang`), `init` (en `enterSession`), `repState`/`_invGroupsCache`
  (asignado a `null` en `logout`). Resuelven por ser `function`/`let` top-level
  del inline (entorno léxico global compartido / props de window).
- dashboard → core: `login()` usa `__t`/`h`/`showLoginError`; `init`, vistas y
  renders usan `__t`, `I18N`, `currentLang`, `t`, `A`, `toast`, `_npStateGet/Set`.

## Orden de carga (decisión)

En `dashboard.html`, línea ~3003, inmediatamente después del markup:

```html
<script src="/static/js/core.js?v=1"></script>
<script> /* inline restante */ </script>
```

- Script clásico, SIN `defer` ni `async`, SIEMPRE antes del inline y antes de
  cualquier `static/js/views/*.js` futuro: el top-level del inline y las vistas
  leen `A`, `t`, `singleLanguage`, etc. en su primera pasada.
- Caché: misma convención de query versionada que el resto de estáticos
  (`style.css?v=4`, `overview.css?v=3`). El HTML se sirve con `no-cache`
  (test de rutas lo cubre), pero los estáticos pueden cachearse: al tocar
  `core.js`, subir `?v=N` **en dashboard.html**.
- Los vendor (`xterm`, `leaflet`) van en `<head>` y no dependen del core en
  module-level; se mantienen como estaban.

## Restricciones para quien toque dashboard/core desde ahora

1. NO re-inlinear el contenido de core.js ni declarar de nuevo `let`/`const`/
   `function` con esos nombres ni en el inline ni en las vistas (redeclarar
   `let t` o `const A` rompe TODA la página con SyntaxError).
2. NO usar `defer`/`async` en el `<script src="/static/js/core.js?v=N">`.
3. NO mover la llamada `restoreSession()` del punto donde está (tarde el
   `fetch` de `/auth/me` — existe verificado en este punto exacto).
4. Claves i18n nuevas van en core.js (`I18N.es/.en`); ninguna vista define
   su propio diccionario hasta que su tarjeta lo diga.
5. `t` es el token y `__t` es el traductor; no renombrar ni crear `window.t`
   (el token no debe exponerse a mano: los bindings léxicos ya lo comparten).
6. Cualquier `onclick`/`onchange` del HTML nuevo que llame a algo del core
   solo funcionará si ese nombre está en el bloque de exposición del footer
   de core.js. Los `function` top-level ya cuelgan de window; los
   `let`/`const` NO, y no son accesibles desde atributos HTML.
7. Al cambiar core.js, correr: `node --check` del core Y del inline extraído,
   más `PYTHONPATH=. .venv/bin/pytest tests/ -q` (355 verdes).

## Pendiente para las siguientes tarjetas

- Paso 2+: extraer vistas a `app/static/js/views/<vista>.js` de una en una,
  cargándolas DESPUÉS de core.js (mismo patrón clásico sin defer); el core
  no toca DOM de vistas: si una función del core necesita algo de vista, es
  señal de que pertenece a la vista (o hay que parametrizarla).
- `swView` sigue en el inline; al extraer vistas conviene moverla última y
  con un registro de loaders.
- `esc/escAttr/IC/icon/setIconText`, tema/paleta y el login pueden migrar
  después si se decide; hoy son atendidos por resolución en tiempo de llamada.
- Tests que referencian cadenas movidas: ninguno rompió; futuros tests que
  busquen texto de core.js deben apuntar a `/static/js/core.js`.

---

# Lote A — Tarjeta 10D-B: vistas simples (connectivity, compliance, webhooks, groups)

Fecha: 2026-10-24 · Rama: `feat/ui-views-a` (worktree `/Users/doker/np-w10e`) ·
Commit: `refactor(ui): extraer vistas simples (connectivity, compliance, webhooks, groups)`.

ROUND 2 del plan (§5.2 del mapa, fase 7): 4 vistas simples extraídas a
archivos propios. El enfoque es **función a función con verificación de
solapamiento por grep**, no "bloque completo": cualquier nombre usado por otra
vista o por infraestructura SE QUEDA donde estaba.

## Orden de carga en dashboard.html

```html
<script src="/static/js/core.js?v=1"></script>
<script src="/static/js/views/connectivity.js?v=1"></script>
<script src="/static/js/views/compliance.js?v=1"></script>
<script src="/static/js/views/webhooks.js?v=1"></script>
<script src="/static/js/views/groups.js?v=1"></script>
<script> /* inline restante */ </script>
```

Los 4 son scripts clásicos (sin defer/async) cargados ENTRE core.js y el
inline. Argumento de orden (regla 5): los loaders que `swView` (inline) despacha
— `loadConn`, `loadWebhooks` — y los que resuelven inline HTML generado
(`setBaseline`, `checkCompliance`, `loadCompReport`, `grpDevs`, `createGroup`,
`addWebhook`, `deleteWebhook`) se resuelven contra window en el momento del
click/llamada, y para entonces los 4 scripts estáticos ya evaluaron
(top-level sin ejecución: solo declaraciones `function`). El inline no leer
ningún símbolo de estas vistas a la carga, así que no hay inversión de orden.
Cada archivo lleva su propio `?v=1`; los `<link>` CSS (v4 / v3) intactos.

## Qué función fue a cada archivo

| Archivo | Funciones movidas (declaración original en el inline de main) |
|---|---|
| `views/connectivity.js` | `_pingCard`, `loadConn` (+ comentario de sección del bloque) |
| `views/compliance.js` | `loadCompliance`, `setBaseline`, `checkCompliance`, `loadCompReport` (+ comentario de sección) |
| `views/webhooks.js` | `loadWebhooks`, `addWebhook`, `deleteWebhook` (+ comentario de sección) |
| `views/groups.js` | `loadGroups`, `grpDevs`, `createGroup` |

Cada cuerpo se copió línea a línea en el mismo orden relativo; los archivos
solo añaden un header de documentación. Las `function` top-level de un script
clásico cuelgan de window, así que los `onclick` inline del markup
(`onclick="loadConn()"` en `#v-connectivity`, `onclick="loadWebhooks()"`
en `#v-webhooks`) y los generados en strings siguen resolviendo sin cambios.

## Qué NO se movió (verificado por grep, compartido)

- **`__isAdmin()` y `npGray()`** — declaran en el bloque de Groups del inline
  (L5503/L5504 del mapa) pero lo usan Compliance, NetBox, Admin/userScope,
  Sites, Retention y Reports. Regla 2: quedan en el inline, sin duplicar.
  En `dashboard.html` conservan su comentario de sección y el contrato del
  API de grupos que las documentaba (el header de `views/groups.js` explica
  por qué no viajaron).
- **`esc` / `escAttr`** — núcleo de escape compartido (inline L3009–L3010,
  fuera del alcance de core.js en el paso 1); las 4 vistas los usan en
  tiempo de llamada. Quedan en el inline.
- **`A`, `t`, `h`, `toast`, `__t`, `currentLang`, `devices`** — core o estado
  compartido; resuelven por el entorno léxico global / window en tiempo de
  llamada. `loadCompliance` lee el array `devices` (estado del dashboard):
  sigue declarado en el inline, no se movió.
- **`swView`** — no tocado (sigue en el inline con todos sus despachos).

## Verificación del lote

- `node --check`: los 4 archivos nuevos + el inline extraído a un archivo
  temporal → OK los 5.
- Grep de redeclaración: cada uno de los 12 nombres movidos queda con
  exactamente 1 `function <nombre>` en todo el árbol (views + core + inline);
  `__isAdmin`/`npGray` quedan solo en el inline.
- `tests/`: suite verde (355 tests, exit 0, sin tests tocados — ninguno
  referenciaba texto de estas funciones).
- `<link>` CSS sin cambios (style.css?v=4, overview.css?v=3).

## Estado del plan de extracción (siguiente ronda)

| Fase del mapa (§5.2) | Estado |
|---|---|
| 1 core/state, 2 i18n, 5 session | hecha en core.js (paso 1) |
| 3 dom, 4 format, 6 theme | pendiente (inline conserva esc/escAttr/IC/icon/tema) |
| 7 vistas simples: connectivity, compliance, webhooks, groups | **hecha (este lote)** |
| 7 resto: maintenance, apikeys, tfa, logs | pendiente; siguiente tanda de "vistas simples". Nota: `logs` tiene 2 fuentes (audit/Loki) y `switchLogSource` comparte helpers de Loki |
| 8 backups+compare+performance+alerts+admin(userScope+`_r4Groups`) | pendiente; acoplamientos: `loadBk→populateCmdDevices`, performance/compare leen `devices` de dashboard |
| 9 reports, retention, sites, netbox | pendiente; usan `__isAdmin`/`npGray`/`_detText`/`_npState*` (core ya tiene _npState*) |
| 10 commands, monitoring (con reasignación de `refMon` en el mismo archivo) | pendiente |
| 11 devices+discovery, 12 dashboard+device-detail, 13 terminal/sftp, 14 topology | pendiente (más riesgosas) |
| 15 router (`swView`), 16 boot | al final por diseño |

---

# Lote B — Tarjeta 10D-B: vistas simples, lote 2 (maintenance, apikeys, tfa, logs)

Fecha: 2026-10-24 · Rama: `feat/ui-views-b` (worktree `/Users/doker/np-w10f`) ·
Commit: `refactor(ui): extraer vistas simples b (maintenance, apikeys, tfa, logs)`.

Mismo enfoque que el Lote A: función a función con verificación de overlap por
grep; dashboard.html pasó de 8745 → 8483 líneas (−266 movidas, +4 `<script src>`).

## Orden de carga en dashboard.html (tras core.js, antes del inline)

```html
<script src="/static/js/core.js?v=1"></script>
<script src="/static/js/views/connectivity.js?v=1"></script>
<script src="/static/js/views/compliance.js?v=1"></script>
<script src="/static/js/views/webhooks.js?v=1"></script>
<script src="/static/js/views/groups.js?v=1"></script>
<script src="/static/js/views/maintenance.js?v=1"></script>
<script src="/static/js/views/apikeys.js?v=1"></script>
<script src="/static/js/views/tfa.js?v=1"></script>
<script src="/static/js/views/logs.js?v=1"></script>
<script> /* inline restante */ </script>
```

## Qué fue a cada archivo

| Archivo | Funciones movidas (mismo orden relativo) |
|---|---|
| `views/maintenance.js` | `loadMaint`, `showMaintForm`, `createMaint`, `delMaint` (+ comentario de sección) |
| `views/apikeys.js` | `showKeyForm`, `loadKeys`, `createKey`, `revokeKey` (+ comentario de sección) |
| `views/tfa.js` | `setup2FA`, `verify2FA` (+ comentario de sección) |
| `views/logs.js` | `switchLogSource`, `loadLokiLabels`, `loadLokiLogs`, `loadLogs` (+ 2 comentarios de sección del bloque LOGS VIEW) |

Los `function` top-level cuelgan de window: los `onclick` del markup de
`#v-maintenance/#v-apikeys/#v-2fa/#v-logs`, los `onchange` de los controles
Loki/audit y los onclick generados (`delMaint(...)`, `revokeKey(...)`)
siguen resolviendo sin cambios. `swView` (inline) despachaba `loadMaint`,
`loadKeys` y `loadLogs`: se resuelve en tiempo de llamada, sin tocar el
despachador.

## Qué NO se movió (verificado por grep)

- **`__isAdmin` / `npGray` / `esc` / `escAttr`** — ninguna de las 14 funciones
  movidas los declara; las 4 vistas los usan solo en tiempo de llamada
  (`esc` en renders, `__isAdmin`/`npGray` en otras vistas). Siguen donde
  estaban (inline/core según el Lote A): sin duplicación, 0 redeclaraciones
  (grep = 1 por nombre en todo el árbol).
- **`devices`** — `loadMaint` lee el array compartido del inventario para
  rellenar `#maint-dev`; sigue declarado en el inline (estado del dashboard).
- **`A`, `h`, `toast`, `__t`** — core.js.
- **DOM exclusivo verificado:** `log-*`, `loki-*` (solo `#v-logs`),
  `maint-*` (solo `#v-maintenance`), `key-*` (solo `#v-apikeys`),
  `2fa-*` (solo `#v-2fa`). Tras la extracción, cada id aparece solo en el
  markup de su vista; `log-filter-action` es un control con handler
  de `#v-logs`, no se comparte con otra vista.

## Verificación del lote

- `node --check`: 4 archivos nuevos OK + inline restante extraído a temporal
  (5467 líneas) OK.
- Grep de redeclaración: los 14 nombres con exactamente 1 `function` en
  dashboard.html + core.js + views/*.js.
- `tests/`: 355 passed (con .venv del repo principal).
- `<link>` CSS intactos (style.css?v=4, overview.css?v=3); cada `.js` nuevo
  con su `?v=1`. Sin console.log/confirm nuevos.

## Estado del plan de extracción (siguiente ronda)

| Fase del mapa (§5.2) | Estado |
|---|---|
| 1 core/state, 2 i18n, 5 session | hecha en core.js (paso 1) |
| 3 dom, 4 format, 6 theme | pendiente (inline conserva esc/escAttr/IC/icon/tema) |
| 7 vistas simples: connectivity, compliance, webhooks, groups | hecha (Lote A) |
| 7 resto: maintenance, apikeys, tfa, logs | **hecha (este lote)** |
| 8 backups+compare+performance+alerts+admin(userScope+`_r4Groups`) | pendiente (siguiente ronda); acoplamientos: `loadBk→populateCmdDevices`, performance/compare leen `devices` de dashboard |
| 9 reports, retention, sites, netbox | después; usan `__isAdmin`/`npGray`/`_detText`/`_npState*` |
| 10 commands, monitoring (con reasignación de `refMon` en el mismo archivo) | después |
| 11 devices+discovery, 12 dashboard+device-detail, 13 terminal/sftp, 14 topology | pendiente (más riesgosas) |
| 15 router (`swView`), 16 boot | al final por diseño |


---

# Lote C — Tarjeta 10D-C: tramo medio (performance, alerts, admin)

Fecha: 2026-10-24 · Rama: `feat/ui-views-c` (worktree `/Users/doker/np-w10g`) ·
Commit: `refactor(ui): extraer tramo medio c (performance, alerts, admin)`.

Mismo enfoque que los Lotes A/B: función a función con verificación de
solapamiento por grep; dashboard.html pasó de 8483 → 7900 líneas
(−585 líneas netas, +3 `<script src>`).

## Orden de carga en dashboard.html (tras core.js y los 8 previos, antes del inline)

```html
<script src="/static/js/views/logs.js?v=1"></script>
<script src="/static/js/views/performance.js?v=1"></script>
<script src="/static/js/views/alerts.js?v=1"></script>
<script src="/static/js/views/admin.js?v=1"></script>
<script> /* inline restante */ </script>
```

Los 3 son scripts clásicos (sin defer/async). Ninguno lee un símbolo del
inline a la carga (top-level = declaraciones `function` + 3 `let`/`const` de
estado de vista), así que no hay inversión de orden posible: todas las
dependencias (`A`, `h`, `t`, `esc`, `escAttr`, `__t`, `toast`, `__isAdmin`,
`devices`, `alDur`, `_fmtUptime`, `_detText`) resuelven en tiempo de llamada
contra el entorno léxico global / window.

## Qué fue a cada archivo

| Archivo | Funciones movidas (mismo orden relativo) |
|---|---|
| `views/performance.js` | `loadPerformance` (única función: ver decisión sobre `_fmtBytes`/`_fmtUptime`/`_donut` abajo) |
| `views/alerts.js` | `loadAlerts` (+ comentario de sección), comentario+estado `alSilData/alSilConfirmId/alRulesData` (`let`) y `alRulesOpen` (`const`), `alUnavail`, `loadAlertSilences`, `alSilChips`, `alRenderSilences`, `togSilForm`, `silAddMatcher`, `alSilFormMsg`, `alSubmitSilence`, `alSilCancel`, `loadAlertRules`, `alRenderRules`, `alToggleExpr` |
| `views/admin.js` | comentario `── ADMIN USERS ──` + `loadUsers`, `createUser`, `delUser`; y `let _r4Groups` (con su comentario) + comentario `── SCOPES RBAC ──` + `userScopeBadge`, `userScopePanel`, `userScopeSave`, `userScopeClearConfirm`, `userScopePanelClose`, `userScopeClear`, `userScopeClearExec` |

`refreshAlertSummary` (badge del sidebar del dashboard) NO se tocó: es otra
cosa, sigue en el inline. El `confirm('Eliminar …')` de `delUser` es el
preexistente del onclick generado por `loadUsers`; no se añadió ninguno nuevo.

## Decisión `performance`: grep previo a mover nada

| Helper | Usado por (grep post-Lotes A/B) | Decisión |
|---|---|---|
| `_fmtBytes` | `loadPerformance` **no** la llama; la ÚNICA llamada es `_fmtBytes(b.uso_bytes)` de `retPruneExec` (Retention, inline), que resuelve a la **segunda** declaración (la que gana por hoisting, bloque Sites). La primera (bloque Performance) está sombreada | NINGUNA se mueve; ambas quedan en el inline. Mover la ganadora rompería Retention; mover la sombreada no cambia nada y tocaría la trampa §7.1 del mapa sin motivo |
| `_fmtUptime` | `loadPerformance` y `_repDur` (Reports, inline) | compartida → queda |
| `_donut` | `repRender` (Reports, inline). `loadPerformance` NO la llama (pinta los donuts inline en su template) | queda |

Resultado: `views/performance.js` contiene solo `loadPerformance` (~117
líneas) y los tres helpers siguen en el inline bajo el comentario
`── PERFORMANCE VIEW (Real Data) ──` que los documentaba.

## Qué NO se movió (verificado por grep)

- **`alDur`** — la usan `alRenderSilences` y `alRenderRules` de esta vista,
  pero también `_ageHuman` (bloque Sites/Retention del inline):
  compartida → queda en el inline; las renders movidas la resuelven en
  tiempo de llamada.
- **`refreshAlertSummary`** — dashboard: cada 45 s escribe el badge del
  sidebar y llama `updStats`.
- **`__isAdmin` / `npGray`** — bloque de Groups del inline; también los usan
  Compliance, NetBox, Reports, Sites y Retention (decisión del Lote A que se respeta).
- **`esc` / `escAttr` / `_detText`** — compartidos (inline; `_detText` en el
  bloque Reports): `loadUsers` y los userScope* los usan en tiempo de llamada.
- **`t`, `currentUser`, `h`, `A`, `toast`, `__t`, `currentLang`, `devices`** —
  core.js o estado compartido del inline.
- **`userScope*`** se movieron porque grep demostró exclusividad con la vista
  Admin (los onclick que los invocan se generan en el propio `loadUsers` y
  `userScopePanel`). Con ellos viaja `let _r4Groups`: su ÚNICO consumidor es
  `userScopePanel` (estaba declarada en el bloque Sites, pero Sites nunca la
  lee). Si la extracción futura de Sites esperaba encontrarla inline, ya sabe
  dónde está (header de `admin.js`).

## Verificación del lote

- `node --check`: 3 archivos nuevos OK + inline restante extraído a temporal
  (4881 líneas) OK.
- Auditoría de equivalencia de líneas: los 551 renglones no-vacíos del tramo
  movido (rangos 1-indexed del archivo pre-lote: 4218–4337, 4339–4449,
  4451–4456, 4466–4673, 5112–5157, 8080, 8389–8478) aparecen en los .js en el
  mismo orden, byte a byte (0 diffs contra `git show HEAD`).
- Grep de redeclaración: los 24 nombres movidos con exactamente 1
  `(async )?function <nombre>` en core.js + views/*.js + el inline;
  `_fmtBytes` (×2), `_fmtUptime`, `_donut`, `alDur`, `__isAdmin`, `npGray` y
  `refreshAlertSummary` siguen solo en el inline.
- Handlers inline sin resolver: 0 (la lista del mapa §6.4 da vacío salvo
  `if`, que no es función).
- `tests/`: 358 passed (con el .venv del repo principal).
- `<link>` CSS intactos (style.css?v=4, overview.css?v=3); los 3 .js nuevos
  con `?v=1`. Sin console.log/confirm nuevos.

## Estado del plan de extracción (siguiente ronda)

| Fase del mapa (§5.2) | Estado |
|---|---|
| 1 core/state, 2 i18n, 5 session | hecha en core.js (paso 1) |
| 3 dom, 4 format, 6 theme | pendiente (inline conserva esc/escAttr/IC/icon/tema) |
| 7 vistas simples: connectivity, compliance, webhooks, groups | hecha (Lote A) |
| 7 resto: maintenance, apikeys, tfa, logs | hecha (Lote B) |
| 8 performance+alerts+admin | hecha (Lote C) · NOTA: backups y compare del mismo "fase 8" del mapa quedan fuera: acopladas a `loadDev` (tarjeta especial aparte) |
| 9 reports, retention, sites, netbox | **hecha (Lote D, siguiente anexo)** |
| 10 commands, monitoring (con reasignación de `refMon` en el mismo archivo) | siguiente |
| 11 devices+discovery, 12 dashboard+device-detail, 13 terminal/sftp, 14 topology | pendiente (más riesgosas) |
| 15 router (`swView`), 16 boot | al final por diseño |

---

# Lote D — Tarjeta 10D-D: reports, retention, sites, netbox

Fecha: 2026-10-24 · Rama: `feat/ui-views-d` (worktree `/Users/doker/np-w10h`) ·
Commit: `refactor(ui): extraer lote d (reports, retention, sites, netbox)`.

Mismo enfoque que los Lotes A/B/C: función a función con verificación de
solapamiento por grep; dashboard.html pasó de 7900 → 7158 líneas
(−742 líneas netas, +4 `<script src>`).

## Orden de carga en dashboard.html (tras core.js y los 11 previos, antes del inline)

```html
<script src="/static/js/views/admin.js?v=1"></script>
<script src="/static/js/views/reports.js?v=1"></script>
<script src="/static/js/views/retention.js?v=1"></script>
<script src="/static/js/views/sites.js?v=1"></script>
<script src="/static/js/views/netbox.js?v=1"></script>
<script> /* inline restante */ </script>
```

Los 4 son scripts clásicos (sin defer/async). Ninguno lee un símbolo del
inline ni de otro .js a la carga (top-level = declaraciones `function`,
`let`/`const` de estado y constantes NB_SVG_*, sin llamadas top-level); todas
las dependencias (`A`, `h`, `t`, `__t`, `toast`, `_npState*`, `devices`,
`loadDev`, `esc`, `escAttr`, `__isAdmin`, `npGray`, `_detText`, `_repTs`,
`_fmtUptime`, `_fmtBytes`, `alDur`) resuelven en tiempo de llamada. El orden
que fija la tarjeta (reports → retention → sites → netbox) es distinto del
orden de líneas del monolito (netbox < reports < sites < retention): no es
observable porque nada movido se lee al evaluar.

## Qué fue a cada archivo

| Archivo | Contenido movido (mismo orden relativo; rangos 1-indexed del archivo pre-lote) |
|---|---|
| `views/reports.js` | comentario de contratos + estado `let repState` / `let repGroupsCache` (7001–7007), `_ymd`, `_repDur`, `repBanner`, `_repGroups`, `loadReports`, `_repFillRestored`, `repPreset`, `repWindow`, `repParams`, `repQS`, `repApply`, `repRender` (7021–7270), y `_donut` (4216–4220, antes en el bloque PERFORMANCE del inline) |
| `views/retention.js` | comentario del contrato + `let _retPreview` (7762–7763), `loadRetention`, `retInvalidate`, `retDet` (alias muerto de `_detText`, mapa §2.3), `retPruneDry`, `retPruneConfirm`, `retPruneExec` (7765–7894) |
| `views/sites.js` | comentario del bloque OLA 4, `const _r4Epochs` (7587), `_det40` (alias muerto, 7589), `_ageHuman` + `_isoLocal` (7599–7610), `loadSites` + `_devState` + `_sitesCard` + `siteToggle` + `siteHist` (7612–7760) |
| `views/netbox.js` | comentario de contrato + constantes `NB_SVG_LOCK/SYNC/OK/NO` + `loadNetbox`, `nbLoadSites`, `nbSyncConfirm`, `nbSyncCancel`, `nbList`, `nbRenderResult`, `nbFailBanner`, `nbSyncRun` (4676–4879, el bloque completo) |

Los `function` top-level cuelgan de window: los onclick del markup
(`loadReports/loadNetbox/loadSites/loadRetention` en los botones Refresh),
los despachos de `swView` (inline) y los onclick generados (`repApply`,
`repPreset`, `siteToggle`, `siteHist`, `retInvalidate`, `retPruneDry`,
`retPruneConfirm`, `retPruneExec`, `nbSyncConfirm/Run/Cancel`) siguen
resolviendo sin cambios. El estado recordado 'rep'/'site' usa `_npState*`
de core.js (intacto).

## Decisión de helpers (grep de exclusividad previo a mover nada)

| Helper | Consumidores (grep post-Lote C) | Decisión |
|---|---|---|
| `_fmtBytes` | DOS declaraciones en el inline: L4202 (bloque Performance, sombreada) y L7591 (bloque OLA 4, ganadora por hoisting, cuerpos NO equivalentes: la ganadora formatea con KB/MB/GB y se detiene en GB). ÚNICA llamada: `loadRetention` (`_fmtBytes(b.uso_bytes)`) | QUEDAN las dos en el inline, IN SITU. Mover la ganadora a views/retention.js haría ganar la sombreada (el inline se evalúa después de las vistas) y cambiaría el formateo de tamaños — la trampa §7.1 del mapa. `loadRetention` (views/retention.js) la resuelve en tiempo de llamada contra la ganadora tal como hoy |
| `_fmtUptime` | `views/performance.js` (Lote C) y `_repDur` (Reports, movida) | QUEDA en el inline; `_repDur` resuelve en tiempo de llamada |
| `_donut` | Única llamada: `repRender` (movida). `loadDonuts` del dashboard NO la usa (pinta donuts en su template). Divergencia con el Lote C: entonces repRender seguía inline; ahora viajó | VIAJA a views/reports.js, respetando su orden relativo original (4216 < 7006). El stub del bloque PERFORMANCE en el inline lo documenta |
| `_repTs` | `repRender`/`loadReports` (movidas) y el wizard de discovery `dwRender` (`dw_scan_done`/`dw_reused`, inline residual) | QUEDA en el inline |
| `_detText` | NetBox, SFTP, wizard discovery, views/admin.js y la propia Reports | QUEDA en el inline (bloque REPORTES SLA, entre los stubs) |
| `_isoLocal` | Sites (2) + Retention (4); el mapa citaba dwStep2/3 pero en el código actual el wizard la usa con `_repTs` | UNA copia única, viaja a views/sites.js (su bloque original); retention la consume en tiempo de llamada (se evalúa antes, pero todas las llamadas son runtime). Sin duplicar |
| `_ageHuman` | Solo `_sitesCard` (movida) | VIAJA a views/sites.js; su llamada a `alDur` (inline) resuelve en tiempo de llamada |
| `_det40` / `retDet` | Alias muertos de `_detText` (mapa §2.3, no borrar sin tarjeta aparte) | Viajan tal cual con su vista (sites/retention) |
| `__isAdmin` / `npGray` | Compliance, Reports, NetBox, Sites, Retention, Admin | QUEDAN en el inline (decisión Lote A respetada; NetBox conserva el banner 403/SOLO admin) |
| `alDur` | `views/alerts.js` (Lote C) + `_ageHuman` | QUEDA en el inline |
| `esc` / `escAttr` | Compartidos | QUEDAN en el inline |

## Estado movido

- `repState`, `repGroupsCache` → views/reports.js (`let` top-level: binding del
  entorno léxico global compartido entre scripts clásicos). `logout` (core.js)
  asigna `repState = null` y lo resuelve en tiempo de llamada (imposible que
  corra antes de que los 17 scripts estén evaluados: el logout solo existe tras
  un login completo).
- `_r4Epochs` → views/sites.js; `_retPreview` → views/retention.js.
- `NB_SVG_*` → views/netbox.js (`const`; solo las consumen las funciones del
  propio fichero).
- Verificado por grep: ninguna de esas variables la lee el inline residual ni
  otras vistas ('site' y 'rep' son las ÚNICAS claves de `_npState*` en todo el
  árbol; publishes/mapa/topología no las tocan).

## Qué NO se movió (verificado por grep)

- `_fmtBytes` (×2), `_fmtUptime`, `_repTs`, `_detText`, `__isAdmin`, `npGray`,
  `alDur`, `esc`/`escAttr` — ver tabla de decisiones arriba.
- Las 4 vistas de este lote mantienen sus IDs DOM exclusivos (`rep-*`,
  `ret-*`, `sites-*`/`sh-chart-*`, `nb-*`/`netbox-content`): ninguna otra
  vista ni el dashboard los escribe. `loadInventory` (devices/discovery) y su
  `selD(...)` no se tocan; el modal `#dp` tampoco.
- `loadDonuts`/`refreshAlertSummary`/dashboard: no se tocaron.

## Verificación del lote

- `node --check`: los 4 archivos nuevos OK + inline restante extraído a
  temporal (4133 líneas) OK.
- Auditoría de equivalencia: las 600 líneas no-vacías movidas (rangos
  4216–4220, 4676–4879, 7001–7007, 7021–7270, 7587–7589, 7599–7610,
  7612–7760, 7762–7894 del archivo pre-lote) aparecen byte a byte en los .js
  nuevos; 0 líneas perdidas (contadores de líneas before − after − vistas).
  Cada bloque original aparece como subsecuencia contigua en su destino
  (orden relativo preservado; único reorden: `_donut` va delante de su
  contrato en reports.js, igual que su posición original 4216 < 7006).
- Grep de redeclaración: los 34 nombres movidos quedan con exactamente 1
  `function` en todo el árbol (core.js + views/*.js + inline);
  `_fmtBytes` mantiene sus 2 (ambas inline), `_fmtUptime`/`_detText`/
  `_repTs`/`__isAdmin`/`npGray`/`alDur`/`esc`/`escAttr`/`loadDev` solo inline/core.
- Handlers invocados desde atributos on* (markup + strings generados):
  0 sin resolver.
- `tests/`: 358 passed (con el .venv del repo principal).
- `<link>` CSS intactos (style.css?v=4, overview.css?v=3); los 4 .js nuevos
  con `?v=1`. Sin console.log/confirm nuevos.

## Divergencias documentadas

1. `_donut` viaja (reports.js): diverge del apunte del Lote C ("queda"),
   correcto entonces porque repRender seguía inline; el grep regla 2 ahora da
   un único consumidor movido. El header de views/reports.js lo explica.
2. `_fmtBytes` NO viaja aunque su único llamador (loadRetention) sí:
   la victoria por hoisting solo vale dentro del mismo script; repetir la
   declaración en views/retention.js (prohibido por la regla 1) o mover la
   ganadora promovería a la sombreada (cuerpos distintos), con cambio de
   formateo. Queda in situ documentada en el stub del bloque OLA 4.
3. `_isoLocal` viaja a views/sites.js aunque Retention también la usa (y
   retention.js se evalúa ANTES que sites.js): resoluciones en tiempo de
   llamada, ninguna llamada a nivel de evaluación; una sola copia.
4. Orden de carga (reports → retention → sites → netbox) ≠ orden de líneas
   del monolito (netbox 4676 < reports 7001 < sites 7581 < retention 7762):
   fija la tarjeta; no observable porque ningún símbolo movido se lee al
   evaluar los scripts.

## Estado del plan de extracción (siguiente ronda)

| Fase del mapa (§5.2) | Estado |
|---|---|
| 1 core/state, 2 i18n, 5 session | hecha en core.js (paso 1) |
| 3 dom, 4 format, 6 theme | pendiente (inline conserva esc/escAttr/IC/icon/tema) |
| 7 vistas simples: connectivity, compliance, webhooks, groups | hecha (Lote A) |
| 7 resto: maintenance, apikeys, tfa, logs | hecha (Lote B) |
| 8 performance+alerts+admin | hecha (Lote C) · backups y compare quedan como tarjeta especial acoplada a `loadDev` |
| 9 reports, retention, sites, netbox | **hecha (este lote)** |
| 10 commands, monitoring (con reasignación de `refMon` en el mismo archivo) | siguiente |
| 11 devices+discovery, 12 dashboard+detalle | después |
| 13 terminal+sftp, 14 topology | después |
| router (`swView`) | después |
| boot | al final por diseño |

---

# Lote E — Tarjeta 10D-E: commands, monitoring

Fecha: 2026-10-24 · Rama: `feat/ui-views-e` (worktree `/Users/doker/np-w10i`) ·
Commit: `refactor(ui): extraer lote e (commands, monitoring)`.

Mismo enfoque que los Lotes A-D: función a función con verificación de
solapamiento por grep; dashboard.html pasó de 7158 → 6740 líneas (−418 líneas
movidas netas, +2 `<script src>`).

## Orden de carga en dashboard.html (tras core.js y los 15 previos, antes del inline)

```html
<script src="/static/js/views/netbox.js?v=1"></script>
<script src="/static/js/views/commands.js?v=1"></script>
<script src="/static/js/views/monitoring.js?v=1"></script>
<script> /* inline restante */ </script>
```

Ambos son scripts clásicos (sin defer/async). El único top-level ejecutable de
los dos es, intencionadamente, el de monitoring.js: `let dragSrc = null` y la
reasignación `refMon` (§7.2 del mapa, ver abajo). Ningún símbolo de la vista
se lee a la evaluación en orden inverso: `views/commands.js` es todo
declaraciones `function`.

## Qué fue a cada archivo

| Archivo | Contenido movido (mismo orden relativo; rangos 1-indexed del archivo pre-lote) |
|---|---|
| `views/commands.js` | `execGlobalCmd` (4236–4257; sus helpers de output/status eran locales de la función, viajan dentro) y `runQuickCmd` (4258–4261) |
| `views/monitoring.js` | Tramo 1 (3270–3582, block-stack completo): comentario `── MONITOREO (Prometheus nativo) ──`, `queryProm`, `queryPromRange`, `_avgByTs`, `_countByTs`, `_nativeChart`, `renderNativeMon`, `refMon` (+ `drawLine`/`drawMulti`/`drawBars` anidadas), comentario `── DRAG AND DROP for monitoring charts ──`, `let dragSrc`, `initDragDrop`, `saveChartOrder`, `loadChartOrder`, comentario "Run drag-drop init..." y la reasignación `const _origRefMon = refMon; refMon = async function () { await _origRefMon(); initDragDrop() }`. Tramo 2 (5022–5100, métricas de plataforma): comentario `── MONITOREO (secciones stack + plataforma) ──`, `monNum`, `renderMonSpark`, `loadMonitoring` |

Los `function` top-level cuelgan de window: los onclick/onchange del markup
(`refMon()` del select `#mon-range` y del botón Refresh, `loadMonitoring()` del
botón Actualizar, `execGlobalCmd()` del textarea/botón, `runQuickCmd('…')` en
los 8 atajos), el intervalo `window._mInt` de 15 s y `setRefreshInterval`
(inline) que ejecutan `loadMonitoring`+`refMon`, y el `swView('commands')`
despachando `populateCmdDevices` (inline) siguen resolviendo sin cambios.

## La trampa `refMon` (mapa §7.2) — decisión

`refMon` (declaración `async function`, pre-lote L3387) se REASIGNA a sí misma
en la misma pasada de evaluación (pre-lote L3581–3582): `const _origRefMon =
refMon; refMon = async function () { await _origRefMon(); initDragDrop() }`.

Decisión: **viajó a monitoring.js junto con la declaración, en el mismo
orden** (declaración L52–364, reasignación L363–364 del archivo nuevo). En un
script clásico las declaraciones `function` top-level se izan ANTES de las
sentencias del script, así que `const _origRefMon` captura la versión real —
exactamente como en el monolito. Tras evaluar monitoring.js, `refMon` es el
wrapper con `initDragDrop`; los consumidores (`setRefreshInterval` y
`swView('monitoring')`, ambos inline; onclick del markup) lo resuelven en
tiempo de llamada contra window, y NINGUNO se ejecuta durante la evaluación
de otro script: no hay riesgo de capturar una versión ya envuelta.
Grep de exclusividad de `_origRefMon`: solo monitoring.js.

## Qué NO se movió (grep de exclusividad, regla 2)

| Símbolo | Consumidores (grep post-Lote D) | Decisión |
|---|---|---|
| `populateCmdDevices` | `loadBk` (pre-lote L4576; Backups, inline residual: latido 60 s + tras cada backup) y `swView('commands')` | **QUEDA en el inline** (regla anti-compartidas, como `__isAdmin`/`npGray` en el Lote A); el comentario de sección `── GLOBAL COMMAND CENTER ──` se queda con ella |
| `setRefreshInterval` | onchange de `#refresh-interval` (Theme) y `#mon-interval` (Monitoring); opera `window._rInt` | QUEDA (bloque Theme del inline; llama `refMon`/`loadMonitoring` en tiempo de llamada) |
| `loadDonuts` (latido 120 s) | `init` (`setTimeout` 1.5 s + `setInterval` 120 s con guard de `#v-devices`) y `loadDev` — NINGUNA función de monitoring la llama | QUEDA: es del DASHBOARD (inline) |
| `refreshAlertSummary` | badge del sidebar; `loadMonitoring` la llama (tiempo de llamada) si hay alertas | QUEDA (dashboard, inline) |
| Lógica de `window._mInt` | `swView('monitoring')` (inline) | QUEDA: la GESTIONA swView; monitoring.js solo trae los workers (`loadMonitoring`, `refMon`) |
| `devices`, `A`, `h`, `t`, `toast`, `esc`, `icon`, `__t`, `currentLang` | core.js o estado compartido del inline | QUEDAN (resolución en tiempo de llamada) |
| `c-big`/`t-big`/`rCmdBig`, `dCmp`, `qa*`, `dlC` | modal `#dp` del dashboard/detalle | NO SE TOCARON (la tarjeta lo prohíbe); ninguna función movida los usa |
| `localStorage['np-chart-order']`, `window._chartRO` | solo `initDragDrop`/`saveChartOrder`/`loadChartOrder` | viajan con ellas (sin cambio de clave ni de versión) |

## Estado movido

- `dragSrc` → views/monitoring.js (`let` top-level). Su ÚNICO consumidor es
  `initDragDrop` y sus closures (mismo archivo). La regla 4 pide `window.x`
  para no-funciones, pero grep demostró que ningún atributo `on*` del markup
  lo referencia; se mantiene el patrón de estado de módulo de los Lotes C/D
  (`alSilData`, `_r4Groups`, `repState`).
- `_origRefMon` → views/monitoring.js (`const` top-level; solo lo lee el
  wrapper del propio archivo).
- La reevaluación de `refMon` al cargar la página mantiene el MISMO efecto
  global que antes (wrapper instalado una vez, `_origRefMon` → función real).

## Verificación del lote

- `node --check`: views/monitoring.js, views/commands.js, core.js y el inline
  restante extraído a temporal (3714 líneas) → OK los 4.
- Auditoría de equivalencia: los 3 tramos movidos (4236–4261, 3270–3582,
  5022–5100 del archivo pre-lote) aparecen BYTE A BYTE en los .js nuevos
  (diff = 0), en el mismo orden relativo (líneas 52–364 y 366–444 de
  monitoring.js; único separador añadido: la línea en blanco que separa los
  dos tramos).
- Grep de redeclaración: los 17 nombres de funciones tocados
  (`execGlobalCmd`, `runQuickCmd`, `queryProm`, `queryPromRange`, `_avgByTs`,
  `_countByTs`, `_nativeChart`, `renderNativeMon`, `refMon`, `initDragDrop`,
  `saveChartOrder`, `loadChartOrder`, `monNum`, `renderMonSpark`,
  `loadMonitoring`, `populateCmdDevices` — la última sigue inline) con
  EXACTAMENTE 1 `function <nombre>` en todo el árbol (core.js + views/*.js +
  inline); `dragSrc`/`_origRefMon` solo en monitoring.js.
- Handlers invocados desde atributos `on*` (markup + strings generados):
  0 sin resolver (script del §6.4 del mapa; solo falso positivo `if`, como en
  Lotes C/D).
- Conteos: 7158 − 418 movidos + 2 `<script src>` = 6740 líneas.
- `tests/`: **358 passed** (con el .venv del repo principal, `PYTHONPATH=.`).
- `<link>` CSS intactos (style.css?v=4, overview.css?v=3); los 2 .js nuevos
  con `?v=1`. Sin console.log/confirm nuevos (grep = 0 en los archivos nuevos).

## Divergencias documentadas

1. `populateCmdDevices` NO viaja aunque 2 de 3 consumidores son de esta vista
   (swView y el markup de commands): `loadBk` (Backups, inline residual) la
   llama en CADA latido de 60 s y tras cada backup — regla anti-compartidas,
   como pactó el Lote A. Cuando la tarjeta de Backups/Compare (g) extraiga
   `loadBk` se re-evaluará la exclusividad.
2. `loadDonuts` NO viaja a monitoring: su intervalo de 120 s lo registran
   `init` y `loadDev` (dashboard), y el grep da cero usos desde monitoring.
3. `dragSrc` viaja como `let` (no `window.dragSrc`): regla 4 en su lectura
   estricta pediría `window.x`, pero su único consumidor es `initDragDrop`
   (mismo archivo) y ningún atributo `on*` lo consulta.
4. El comentario de sección de commands se quedó inline en vez de viajar:
   sigue documentando el código que permaneció (`populateCmdDevices`); los
   headers de los .js llevan el contrato completo.

## Estado del plan de extracción (siguiente ronda)

Tras este lote quedan (sugerencia de próximos lotes de la tarjeta):
| Lote | Contenido |
|---|---|
| f | devices+discovery (`loadInventory`, `addInventoryDevice`, bloques `_inv*`/`dw*` con `_invGroupsCache`, `dw`, `DW_ESTADO`) |
| g | backups/compare (`loadBk`, `loadJobs`, `doBackupMulti`, `scheduleBackup`, `scheduleRecurring`, `cancelJob`, `dlBackup`, `offsiteExport`, `dCmp`, `bd*`, `buildSchedForms` — tarjeta especial acoplada a `loadDev`: al extraer, re-evaluar `populateCmdDevices`) |
| h | dashboard+detalle (el monolito central: init/loadDev/renderD/bulk/updStats/updateDashboardHero/loadDonuts/refreshAlertSummary/loadH + modal `#dp`: selD/closeD/setDTab/qa*/dlC/rCmdBig) |
| i | terminal+sftp (`_term*`, `term*`, `cmdOpenConsole`, `_sftp*`, `sftp*` + `_formatBytes`/`_formatTime`) |
| j | topology (`_topo*`, `topoTab`, `buildTraceOrigins`, …) |
| k | router (swView+hashchange) y boot (init+listeners+IIFE de tema/restoreSession) — cierre |

Estados por fase del mapa (§5.2): fases 1/2/5 hechas (core), 3/4/6 pendientes
(esc/escAttr/IC/icon/tema, `_fmt*`/`_detText`/`alDur`…), fase 7 hecha
(Lotes A-B), fase 8 parcial (Lote C; backups/compare → lote g), fase 9 hecha
(Lote D), **fase 10 hecha (este lote)**, fases 11-16 pendientes en el orden
f/g/h/i/j/k.

---

# Lote F — Tarjeta 10D-F: devices + discovery (con `loadDev` y bulk)

Fecha: 2026-10-24 · Rama: `feat/ui-views-f` (worktree `/Users/doker/np-w10j`) ·
Commit: `refactor(ui): extraer lote f (devices + discovery)`.

Mismo enfoque que los Lotes A-E: función a función con verificación de
solapamiento por grep; dashboard.html pasó de 6740 → 6004 líneas (−736 líneas
netas, +2 `<script src>`).

## Orden de carga en dashboard.html (tras core.js y los 19 previos, antes del inline)

```html
<script src="/static/js/views/monitoring.js?v=1"></script>
<script src="/static/js/views/discovery.js?v=1"></script>
<script src="/static/js/views/devices.js?v=1"></script>
<script> /* inline restante */ </script>
```

Ambos son scripts clásicos (sin defer/async), en la posición que fija la
tarjeta (entre monitoring.js y el inline; orden discovery → devices). Ambos
son solo declaraciones `function` + `let`/`const` de estado propio (ninguna
sentencia de nivel superior executable): ningún símbolo movido se lee a la
evaluación, así que el orden entre ellos no es observable.

## Qué fue a cada archivo (mismo orden relativo; rangos 1-indexed del archivo pre-lote)

| Archivo | Contenido movido |
|---|---|
| `views/devices.js` | `loadDev` (3354–3429, **latido canónico 30 s**: viaja la función completa tal cual, trampa §7.6 intacta — pisa `#cmp-a`/`#cmp-b` con `selectedIndex=1` y llama `buildSchedForms` en cada pasada); comentario `── BULK OPERATIONS ──` + bulk de la grilla del overview: `tB`, `tB2`, `updB`, `bClear`, `bulkPanel`, `bulkClosePanel`, `bulkReset`, `bulkSummary`, `bCmd`, `bCmdRun`, `bPing`, `bFacts` (3469–3593); `delDev` (3940–3947); comentario `── DEVICE MANAGER (V-INVENTORY) ──` + `_invContainer`, `toggleInvAuth`, `toggleInvCli`, estado `let _invGroupsCache` + comentario/grupo Tarjeta 9B + `_invGroupEls`, `_invGroupSetManual`, `invGroupChanged`, `invRefreshGroups`, `invReadGroup`, `invPreloadGroup`, `addInventoryDevice`, `loadInventory` (4345–4601) |
| `views/discovery.js` | Comentario de contrato (docs/handoffs/discovery.md) + estado `let dw` + `const DW_ESTADO` + `_dwEstadoText`, `_dwResList` y las 17 funciones del wizard: `dwOpen`, `dwClose`, `dwReset`, `dwRender`, `dwPanelError`, `dwStep1`, `dwToggleAllSources`, `dwScan`, `dwStep2`, `dwToStep3`, `dwStep3`, `dwSel`, `dwSelAll`, `dwToStep4`, `dwStep4`, `dwImport` (6405–6712) |

Los `function` top-level cuelgan de window: botón Refresh de `#v-devices`,
`onblur`/`onchange`/`oninput` del formulario de alta, botones del `#bulk-bar`
de `#v-dashboard`, onclick="delDev(sel.id)" del modal `#dp`, onclick de las
filas de inventario (`selD`), `onclick="dwOpen()"` y todo el onclick generado
por dwRender/dwStep* — además de init/`_npTick(loadDev, 30000)`/`swView`
('devices', 'terminal') y `loadReports`/`dwOpen`/`dwImport`/`addInventoryDevice`
/`delDev` — siguen resolviendo sin cambios.

## Las trampas de la tarjeta — decisiones

| Símbolo | Consumidores (grep post-Lote E) | Decisión |
|---|---|---|
| `loadDev` | `init` (inline, 3343/3347), botón "Actualizar" del overview (markup), `delDev`/`addInventoryDevice` (movidas), `dwOpen`/`dwImport` (discovery.js), `loadReports` (reports.js), `swView('terminal')` (inline) | **VIAJA completa a devices.js** (la tarjeta lo instruye: "mueve la función completa tal cual"). NINGUNO de sus consumidores la lee a nivel de evaluación: todos resuelven contra window en tiempo de llamada. Los acoples del latido (renderD/loadDonuts/updStats/buildSchedForms + selects de Compare) quedan inline y se resuelven igual; tarjeta especial de backups/compare (lote g) pendiente |
| `selD` | loadInventory emite `onclick="selD(...)"` (cada fila: window en el click OK) PERO el listener top-level del `#grid` y el del `#ops-focus-items` — INLINE RESIDUAL (3640, 3657) — la llaman, y el modal `#dp` la consume | **QUEDA inline** (la tarjeta: "consumidores inline residuales → QUEDA; no moverla por conveniencia") |
| `delDev` | ÚNICO llamador: `onclick="delDev(sel.id)"` del modal `#dp` (markup, queda) — se resuelve por window en el click; nada la lee a la evaluación | **VIAJA a devices.js** (candidata de la tarjeta); el modal `#dp` no se tocó |
| Bulk (`tB`…`bFacts`) | Botones del `#bulk-bar` (markup que queda) y checkbox que genera `renderD` (que queda) — window en el click | **VIAJAN a devices.js** (tarjeta: "pertenecen a devices"). `fDev` NO viaja: `renderD` la llama al final de cada render (inline residual) |
| `bSel`/`bMode`/`devices`/`sel` | Movidas (bulk, loadDev) Y las que quedan (renderD 3598/3599, listener del `#grid` 3640) | **QUEDAN en el inline** (declaración `let` original 3021–3024, sin tocar): bindings del entorno léxico global compartido, tiempo de llamada |
| `_invGroupsCache` | Solo las movidas de devices.js + `logout` (core.js la asigna a `null`, 826) | **VIAJA a devices.js** (`let`); logout la resuelve en tiempo de llamada (mismo patrón que `repState`, Lote D: el logout solo existe tras login, imposible antes de evaluar los 21 scripts) |
| `dw`/`DW_ESTADO` | Solo el bloque wizard | **VIAJAN a discovery.js** (`let`/`const`); ningún atributo on* ni otro script los consulta |
| `_detText` / `_repTs` | dwScan/dwStep2/dwStep3/dwImport (movidas) + sftp/admin/reports/etc (inline) | **QUEDAN inline** (compartidas, mapa §2.1) |
| `populateCmdDevices`, `buildSchedForms`, `renderD`, `loadDonuts`, `updStats`, `refreshAlertSummary`, `loadH`, `init`, `swView` | acoples fuera de devices | **QUEDAN inline** (dashboard, lote h; backups/compare, lote g) |
| `esc`/`escAttr`/`setIconText`/core (`A`,`h`,`t`,`toast`,`__t`,`_npTick`) | compartidos | QUEDAN (tiempo de llamada) |

## Qué NO se tocó

- Markup de TODAS las vistas y del modal `#dp` (ni qa*/rCmdBig/c-big/soporte):
  los 5 cortes son del `<script>` inline, y el markup de `#v-devices`/`#dw-panel`
  se queda tal cual (sus handlers resuelven por window).
- Lógica Ola 9 de `addInventoryDevice` (envío de `group`, 403 literal del
  backend, aviso de equipo sin grupo, upsert 409→PUT, `setTimeout` 1 s) y de
  `loadInventory`: byte a byte.
- El bloque de accesibilidad del sidebar (4603–4620 pre-lote) quedó EXACTO tras
  el corte: es infraestructura (mapa §7.9, candidato a boot.js), no devices.

## Verificación del lote

- `node --check`: views/discovery.js, views/devices.js, core.js, los 17 .js
  previos y el inline restante extraído a temporal (2978 líneas) → OK.
- Auditoría de equivalencia: los 5 tramos movidos (3354–3429, 3469–3593,
  3940–3947, 4345–4601 del archivo pre-lote; 6405–6712) aparecen BYTE A BYTE
  en los .js nuevos (0 líneas perdidas; orden relativo preservado en cada
  archivo: loadDev → bulk → delDev → DEVICE MANAGER; wizard completo en
  discovery.js).
- Grep de redeclaración: los 43 nombres de funciones movidos con EXACTAMENTE 1
  `(async )?function <nombre>` en core.js + views/*.js + inline;
  `_invGroupsCache` solo en devices.js, `dw`/`DW_ESTADO` solo en discovery.js.
- Handlers invocados desde atributos `on*` (markup + strings generados en
  todos los .js): 0 sin resolver.
- Conteos: 6740 → 6004 (git numstat: −772/+36 = −736 netas; las 36 añadidas
  son 34 líneas de stub + 2 `<script src>`).
- `tests/`: **358 passed** (con el .venv del repo principal, `PYTHONPATH=.`).
- `<link>` CSS intactos (style.css?v=4, overview.css?v=3); los 2 .js nuevos
  con `?v=1`. Sin console.log/confirm nuevos (el único `confirm` en
  devices.js es el preexistente de `delDev`).

## Divergencias documentadas

1. `loadDev` viaja (la tarjeta lo instruye, antes planificado para el lote h):
   el grep demuestra 0 lecturas a nivel de evaluación de sus consumidores;
   `renderD`/`buildSchedForms`/`updStats`/`loadDonuts` (inline) y los selects
   de Compare siguen resuviéndose en tiempo de llamada. El lote g
   (backups/compare) y el h (dashboard+detalle) conservan su plan.
2. `selD` NO viaja a pesar de que loadInventory (movida) emite su onclick:
   el listener del `#grid` y el del `#ops-focus-items` son consumidores
   inline residuales (regla 2). Ambos onclick (generados y listener) la
   resuelven por window sin cambio.
3. `fDev` NO viaja con el resto de grilla-filtering: no es bulk y su
   llamador (`renderD`) queda inline. El stub lo documenta.
4. `delDev` viaja aunque su único llamador es el markup del modal `#dp`
   (que queda): resolución por window en el click (mismo caso que los
   onclick que la tarjeta declara válidos). Modal intacto.
5. `bSel`/`bMode` NO viajan aunque la mayoría de sus consumidores sí:
   renderD y el listener del `#grid` (que quedan para el lote h) los leen —
   evitar `window.x` explícito innecesario (regla 4); seguimiento en el
   bloque de estado del inline.

## Estado del plan de extracción (siguiente ronda)

Tras este lote quedan:
| Lote | Contenido |
|---|---|
| g | backups/compare (tarjeta especial acoplada a `loadDev` ahora en views/devices.js: al extraer, re-evaluar `populateCmdDevices`) |
| h | dashboard+detalle (init/renderD/updStats/updateDashboardHero/loadDonuts/refreshAlertSummary/loadH/buildSchedForms + modal `#dp`: selD/closeD/setDTab/qa*/dlC/rCmdBig + listener del grid/ops-focus) |
| i | terminal+sftp (`_term*`, `term*`, `cmdOpenConsole`, `_sftp*`, `sftp*` + `_formatBytes`/`_formatTime`) |
| j | topology (`_topo*`, `topoTab`, `buildTraceOrigins`, …) |
| k | router (swView+hashchange) y boot (init+listeners+IIFE de tema/restoreSession) — cierre |

Estados por fase del mapa (§5.2): fases 1/2/5 hechas (core), 3/4/6 pendientes,
fases 7 y 9 hechas, fase 10 hecha (Lote E), **fase 11 hecha (este lote)**,
fases 12–16 pendientes en el orden g/h/i/j/k.

## Resumen por fase del mapa (§5.2) tras este lote

| Fase del mapa (§5.2) | Estado |
|---|---|
| 1 core/state, 2 i18n, 5 session | hecha en core.js (paso 1) |
| 3 dom, 4 format, 6 theme | pendiente (inline conserva esc/escAttr/IC/icon/tema) |
| 7 vistas simples: A + B | hecha |
| 8 performance+alerts+admin (Lote C) · backups/compare → lote g | hecha / pendiente |
| 9 reports, retention, sites, netbox | hecha (Lote D) |
| 10 commands, monitoring | hecha (Lote E) |
| 11 devices+discovery | **hecha (este lote)** |
| 12 dashboard+detalle (lote h), 13 terminal/sftp (i), 14 topology (j) | después |
| 15 router, 16 boot (k) | al final por diseño |
