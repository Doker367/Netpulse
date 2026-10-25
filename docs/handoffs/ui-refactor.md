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
| 8 performance+alerts+admin | **hecha (este lote)** · NOTA: backups y compare del mismo "fase 8" del mapa quedan fuera: acopladas a `loadDev` (tarjeta especial aparte) |
| 9 reports, retention, sites, netbox | después; usan `__isAdmin`/`npGray`/`_detText`/`_npState*`/`_fmtBytes` (la ganadora, hoy solo la llama Retention) |
| 10 commands, monitoring (con reasignación de `refMon` en el mismo archivo) | después |
| 11 devices+discovery, 12 dashboard+device-detail, 13 terminal/sftp, 14 topology | pendiente (más riesgosas) |
| 15 router (`swView`), 16 boot | al final por diseño |
