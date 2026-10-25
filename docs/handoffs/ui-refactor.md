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

---

# Lote G — Tarjeta 10D-G: backups + compare (las acopladas)

Fecha: 2026-10-24 · Rama: `feat/ui-views-g` (worktree `/Users/doker/np-w10k`) ·
Commit: `refactor(ui): extraer lote g (backups, compare)`.

Mismo enfoque que los Lotes A–F: función a función con verificación de
solapamiento por grep; dashboard.html pasó de 6004 → 5828 líneas (−176 líneas
netas: −200/+24, las 24 añadidas son 22 de stubs/comentarios y 2 `<script src>`).

## Orden de carga en dashboard.html (tras core.js y los 21 previos, antes del inline)

```html
<script src="/static/js/views/devices.js?v=1"></script>
<script src="/static/js/views/backups.js?v=1"></script>
<script src="/static/js/views/compare.js?v=1"></script>
<script> /* inline restante */ </script>
```

Ambos son scripts clásicos (sin defer/async, posición que fija la tarjeta:
backups → compare). Top-level de ambos = SOLO declaraciones `function` del
inline movidas tal cual: nada se lee ni se ejecuta a la evaluación, así que no
hay inversión de orden observable. Todas las dependencias (`A`, `h`, `t`,
`__t`/`toast` de core; `devices`, `esc`, `_npTick` del inline) resuelven en
tiempo de llamada.

## Qué fue a cada archivo (mismo orden relativo; rangos 1-indexed del archivo pre-lote)

| Archivo | Contenido movido |
|---|---|
| `views/backups.js` | `buildSchedForms` (3364–3371, PINTA los selects de la vista: la llaman `loadDev` — views/devices.js, latido 30 s, trampa §7.6 conservada tal cual — y `swView('backups')`); comentario `── BACKUP ALL ──` + `backupAllDevices` (3742–3755); comentario `── BACKUPS ──` + `offsiteExport` (3989–3997); `loadBk` (4040–4090, latido 60 s + KPIs `#bk-kpis` + tabla `#bk-list` + badges `#sb-bk`/`#info-bk`), `dlBackup` (4092–4102) y bloque `── SCHEDULER ──` completo: `loadJobs`, `doBackupMulti`, `scheduleBackup`, `scheduleRecurring`, `cancelJob` (4104–4140) |
| `views/compare.js` | comentario `── COMPARE ──` + `dCmp` (3965–3987), `bdPopulateDevices` (3999–4006), `bdLoadBackups` (4008–4020), `bdDiff` (4022–4039) |

Los `function` top-level cuelgan de window: los onclick del markup de
`#v-backups` (`backupAllDevices()`, `offsiteExport()`, `loadBk()`), los
onchange/onclick de `#v-compare` (`dCmp()`, `bdLoadBackups()`, `bdDiff()`), los
onclick generados dentro de los propios cuerpos movidos (`dlBackup` en la
tabla de `loadBk`; `cancelJob` en `loadJobs`; `scheduleBackup`/
`scheduleRecurring` en los formularios que pinta `buildSchedForms`), el
despacho de `swView` (inline: `if (v==='backups') { loadBk(); buildSchedForms();
loadJobs(); }` e `if (v==='compare') bdPopulateDevices();`), los latidos
`_npTick(loadBk, 60000)` y `await loadBk()` de `init` (inline), `backupAllDevices`
terminando en `loadBk()` y las acciones rápidas del modal `#dp` (`qaB`/`qaM`,
inline residual) llamando `loadBk()`/`dCmp()` — todos resuelven contra window
en tiempo de llamada, sin cambios.

## Las trampas de la tarjeta — decisiones (grep de exclusividad previo a mover nada)

| Símbolo | Consumidores (grep post-Lote F) | Decisión |
|---|---|---|
| `loadBk` | markup de #v-backups, `init` (inline: `await loadBk()` + `_npTick(loadBk, 60000)`), `swView('backups')`, `qaB` (modal #dp, inline), `backupAllDevices` (movida) | **VIAJA a backups.js**; todos los consumidores son tiempo de llamada (nadie la lee al evaluar) |
| `buildSchedForms` | `loadDev` (views/devices.js línea 110, en cada latido de 30 s) y `swView('backups')` (inline) | **VIAJA a backups.js**: exclusiva de Backups (solo pinta `#sched-*`); los 2 consumidores (una movida — devices.js — y uno inline) la resuelven en tiempo de llamada. El pisado de formularios en cada latido (§7.6) queda intacto |
| `populateCmdDevices` |Tras mover TODO lo de backups: `loadBk` (backups.js, en cada latido de 60 s y tras cada backup) y `swView('commands')` (INLINE residual, única despachante) | **QUEDA en el inline** (bloque `── GLOBAL COMMAND CENTER ──` intacto). La reevaluación que pedía la tarjeta da N=2 usuarios (backups.js + router inline del lote k), no un único consumidor; además editar views/commands.js (Lote E) está fuera de la ZONA de esta tarjeta. Resolución por window en el latido, como hoy |
| `qaB` / `qaM` | onclick generados por `selD` (modal `#dp` del detalle, INLINE residual) | **QUEDAN en el inline**: son del detalle de equipo (lote h, fuera de la ZONA); ninguna función movida los declara. `qaB`→`loadBk` (backups.js) y `qaM`→`dCmp` (compare.js) resuelven en tiempo de llamada |
| scheduler completo (`loadJobs`, `doBackupMulti`, `scheduleBackup`, `scheduleRecurring`, `cancelJob`) | `loadJobs`: markup-latido de Backups vía `swView`, `doBackupMulti` (movida), `cancelJob` (movida); onclick de `scheduleBackup`/`scheduleRecurring` generados en `buildSchedForms` (movida); onclick de `cancelJob` generado en `loadJobs` (movida) | **VIAJAN a backups.js**: exclusivos de Backups (grep: cero referencias fuera del bloque) |
| Helpers de badges (`sb-bk`, `info-bk`) | SOLO `loadBk` (grep: 2 escrituras en líneas 4048–4049 pre-lote; nadie más los toca) | **VIAJAN de la mano de loadBk** (no hay helper externo que los repinte; el badge del sidebar de alerts lo actualiza `refreshAlertSummary` sobre OTROS ids `sb-*`) |
| `dCmp` | onchange de `#cmp-a`/`#cmp-b` (markup de `#v-compare`) y `qaM` (modal #dp, inline) | **VIAJA a compare.js**; ambos consumidores resuelven en tiempo de llamada |
| `bdPopulateDevices` | `swView('compare')` (inline) y guard-cláusula interna; onchange `#bd-dev` → `bdLoadBackups` | **VIAJA a compare.js** |
| `bdLoadBackups` / `bdDiff` | onchange `#bd-dev` y onclick "Comparar backups" del markup; callee interna (movida) | **VIAJAN a compare.js** |
| `__isAdmin`/`npGray`, `esc`/`escAttr`, `_detText`, `alDur`, `_fmtBytes` (×2), `_fmtUptime`, `_isoLocal` | compartidos (decisiones de Lotes A–D) | **QUEDAN** (inline/core; ninguna función movida los declara; todas las llamadas resuelven en tiempo de llamada) |
| `devices`, `sel` | estado compartido del inline (`bdPopulateDevices` y `doBackupMulti` los leen; `loadDev` de devices.js es la fuente) | **QUEDAN en el inline** (regla 2; lecturas de comparar NO pasan a top level: compare.js no lee selects al evaluarse — regla de la tarjeta) |

Ninguna función movida declara `let`/`const` de estado: ambos archivos son
100 % declaraciones (0 top-level ejecutable), por lo que ni `window.x`
explícito ni estado de módulo fueron necesarios (regla 4: solo `function` →
globales por hoisting de script clásico).

## Qué NO se tocó

- Markup de TODAS las vistas y del modal `#dp` (los 3 cortes son del
  `<script>` inline; `#v-backups`/`#v-compare` quedan byte a byte).
- `swView` con su despacho `if (v==='backups') { loadBk(); buildSchedForms();
  loadJobs(); }` (inline, lote k/router): ninguna línea suya se recorta.
- `populateCmdDevices` y su comentario de sección (decisión de arriba; el
  header de views/commands.js del Lote E sigue siendo válido: lo que ahí se
  decía de loadBk sigue aplicando y ahora loadBk vive en backups.js).
- `_fmtBytes` (×2 sombreada/ganadora, §7.1), `_fmtUptime`, `_detText`,
  `_repTs`, `alDur`, `__isAdmin`, `npGray`, `esc`/`escAttr`, `loadDev`,
  `selD`, `renderD`, `loadDonuts`, `updStats`, `refreshAlertSummary`,
  `loadH`, `init`: ninguno se movió ni se tocó (consumidores inline
  residuales; lote h).
- Trampas §7.6 (loadDev pisa `#cmp-a`/`#cmp-b` con `selectedIndex=1` y llama
  `buildSchedForms` en cada latido) y §7.7 (`loadBk` reconstruye `#cmd-dev` en
  cada latido de 60 s, perdiendo la selección de Commands): defectos
  preexistentes conservados tal cual según la regla 1; su tarjeta específica
  sigue pendiente (no es parte del refactor).

## Verificación del lote

- `node --check`: views/backups.js (176 líneas), views/compare.js (94 líneas),
  core.js, los 20 .js previos y el inline restante extraído a temporal
  (2798 líneas) → OK todos.
- Auditoría de equivalencia: los 3 tramos extraídos (3364–3371, 3742–3755,
  3989–4140 del archivo pre-lote, con los sub-cortes compare 3965–3987 y
  3999–4039) aparecen BYTE A BYTE en los .js nuevos (assert en el script de
  corte, 0 diffs); el orden relativo dentro de cada archivo se conserva
  (backups: buildSchedForms → backupAllDevices → offsiteExport → loadBk →
  dlBackup → scheduler; compare: dCmp → bdPopulateDevices → bdLoadBackups →
  bdDiff; único añadido: su separación por línea en blanco y el header doc).
- Grep de redeclaración: los 14 nombres movidos con EXACTAMENTE 1
  `(async )?function <nombre>` en core.js + views/*.js + inline;
  `qaB`/`qaM`/`populateCmdDevices` siguen con su única declaración en el
  inline.
- Handlers invocados desde atributos `on*` (markup, 132, + strings generados
  en todos los .js e inline): **0 sin resolver** (verificador §6.4 del mapa:
  único falso positivo `if`, como en Lotes C–F).
- Conteos: 6004 − 200/+24 = 5828 líneas (−176 netas = 196 líneas movidas más
  4 de comentario ajustado + 2 `<script src>` añadidos; git numstat 24/200).
- `tests/`: **358 passed** (con el .venv del repo principal,
  `PYTHONPATH=.`).
- `<link>` CSS intactos (style.css?v=4 línea 12, overview.css?v=3 línea
  1505); los 2 .js nuevos con `?v=1` en dashboard.html líneas 3023–3024. Sin
  console.log/confirm nuevos (grep = 0 en los archivos nuevos).
- Ids DOM de las dos vistas verificados de uno a uno: `sched-*`, `bk-*`,
  `jobs-list`, `bd-*`, `sb-bk`, `info-bk` aparecen solo en el markup de su
  vista + su .js (control de colisión de prefijos: `cmp-b*`/`cmp-r*` del grep
  grosso eran los `cmp-bl-*`/`cmp-result` de Compliance; el uso real de
  `#cmp-a/#cmp-b/#cmp-r` queda solo en compare.js + qaM (inline, lote h) + el
  comentario de loadDev en devices.js).

## BONUS — verificador del mapa §6.4 sobre el árbol completo (posición tras Lote G)

`node --check` de los 23 .js (core + 21 vistas + backups/compare) + inline
temporal: OK. Verificador de handlers (`on(click|change|input|submit|keydown)`
del markup y de los strings generados, resueltos contra core.js + views/*.js +
inline): **0 sin resolver** — lista completa vacía salvo el falso positivo `if`
(no es función; preexistente en todos los lotes). Red de consumidores
recalculada post-movida: los `on*` que invocan símbolos de lote g
(`backupAllDevices`, `offsiteExport`, `loadBk`, `dCmp`, `bdLoadBackups`,
`bdDiff` + los generados `dlBackup`, `cancelJob`, `scheduleBackup`,
`scheduleRecurring`) resuelven todos contra backups.js/compare.js por window.

## Divergencias documentadas

1. `populateCmdDevices` NO viaja pese a que "Commands es su única despachante
   inline": tras mover `loadBk` su tabulación da N=2 usuarios (loadBk en
   backups.js + swView('commands') en el inline), no un único consumidor; la
   regla de la tarjeta ("si su ÚNICO consumidor restante es commands o
   backups, puede viajar") no se cumple. Se documenta además por la ZONA:
   views/commands.js ya extraída (Lote E) no es editable en esta tarjeta.
   El latido 60 s de Backups sigue repintando `#cmd-dev` (§7.7) tal cual.
2. `buildSchedForms` viaja a backups.js pese a que su punto de llamada más
   frecuente es `loadDev` (devices.js, Lote F): es el pintor de los selects de
   Backups y los 2 consumidores resuelven en tiempo de llamada; devices.js no
   se editó (su línea 110 sigue llamando `buildSchedForms()` igual, ahora
   window-resuelta).
3. `qaB`/`qaM` (modal `#dp`) NO viajan aunque qaM toca los selects de Compare:
   pertenecen al detalle de equipo (lote h, fuera de la ZONA); sus llamadas a
   `loadBk`/`dCmp`/`swView` son tiempo de llamada.
4. Orden de carga (backups → compare) ≠ orden de líneas del monolito (dCmp
   3965 < offsiteExport 3989 < loadBk 4040): el `── COMPARE ──` va delante de
   parte de los `── BACKUPS ──` en el monolito intercalado; en los archivos
   nuevos cada bloque mantiene su orden interno y nada movido se lee a la
   evaluación, así que la reordenación entre ARCHIVOS no es observable.

## Estado del plan de extracción (siguiente ronda)

Tras este lote (las acopladas ya no lo son: las dependencias con `loadDev`
quedaron como llamadas window en tiempo de llamada en ambos sentidos) quedan:

| Lote | Contenido |
|---|---|
| h | dashboard+detalle, corazón del monolito: `setDTab`/`rCmdBig`/`c-big`/`t-big`/`qa*`/`dlC`/`renderD`/`updStats`/`loadDonuts`/`loadH`/`refreshAlertSummary`/`init`… (modal `#dp` + latidos + listener del grid/ops-focus) |
| i | terminal+sftp (`_term*`, `term*`, `cmdOpenConsole`, `_sftp*`, `sftp*` + `_formatBytes`/`_formatTime`) |
| j | topology (`_topo*`, `topoTab`, `buildTraceOrigins`, …) |
| k | router (swView+hashchange) y boot (init+listeners+IIFE de tema/restoreSession) — cierre |

## Resumen por fase del mapa (§5.2) tras este lote

| Fase del mapa (§5.2) | Estado |
|---|---|
| 1 core/state, 2 i18n, 5 session | hecha en core.js (paso 1) |
| 3 dom, 4 format, 6 theme | pendiente (inline conserva esc/escAttr/IC/icon/tema) |
| 7 vistas simples: A + B | hecha |
| 8 performance+alerts+admin (Lote C) · backups/compare (lote g) | hecha / **hecha (este lote)** |
| 9 reports, retention, sites, netbox | hecha (Lote D) |
| 10 commands, monitoring | hecha (Lote E) |
| 11 devices+discovery | hecha (Lote F) |
| 12 dashboard+detalle (lote h), 13 terminal/sftp (i), 14 topology (j) | después |
| 15 router, 16 boot (k) | al final por diseño |

---

# Lote H — Tarjeta 10D-H: dashboard + detalle (el corazón)

Fecha: 2026-10-24 · Rama: `feat/ui-views-h` (worktree `/Users/doker/np-w10l`) ·
Commit: `refactor(ui): extraer lote h (dashboard + detalle)`.

Dos archivos nuevos, porque el lote son dos unidades cerradas y sin helper
privado compartido (verificado por grep): la portada (`#v-dashboard`, con sus
latidos y el estado del hero) y el modal de detalle (`#dp`, fuera de las 24
vistas, con los restos muertos del detalle antiguo por pestañas).
`dashboard.html` pasó de **5.828 → 5.244 líneas** (−584 netas: −622/+38; las
38 añadidas son 2 `<script src>`, 3 líneas de estado restauradas, 10 de stub
y 23 comentarios ajustados). El `<script>` inline residual baja de **2.800 a
2.214 líneas**, el mínimo histórico.

## Auditoría del trabajo parcial heredado (GLM) — obligatoria antes de seguir

Estado encontrado: `dashboard.html` modificado + `views/dashboard.js` (310
líneas) y `views/device-detail.js` (425) **sin commitear**, sin `<script src>`
para ninguno de los dos y sin anexo en este handoff (la página habría quedado
con `init`/`selD`/`renderD` sin definir: handlers y arranque rotos).

Auditoría desde cero contra `git show HEAD:app/static/dashboard.html` (5.828
líneas):

1. **Contenido**: multiset de líneas no vacías del `<script>` inline de HEAD
   frente a (inline nuevo + los 2 archivos) → **0 líneas de código perdidas y
   0 duplicadas**. Las únicas diferencias eran comentarios y la línea del grupo
   `let` de estado (`cmdH`). El corte era correcto y completo.
2. **Orden**: subsecuencia estricta sobre líneas de código → `dashboard.js`
   229/229 líneas en el mismo orden que el monolito; `device-detail.js` 338/339
   (la única distinta es `let cmdH = [];`, izada fuera de la línea compartida).
3. **Faltaba**: los 2 `<script src>`, el stub del bloque portada en el inline y
   el anexo. Además GLM había movido a `dashboard.js` tres declaraciones de
   estado compartido que la tarjeta manda dejar en el inline (ver Divergencias).

**Decisión: se conserva el corte** (no se descarta nada) y se termina: se
añaden los `<script src>`, el stub, la restauración del estado y este anexo.

## Orden de carga en dashboard.html (tras core.js y los 22 previos, antes del inline)

```html
  <script src="/static/js/views/compare.js?v=1"></script>
  <script src="/static/js/views/device-detail.js?v=1"></script>
  <script src="/static/js/views/dashboard.js?v=1"></script>
  <script> /* inline restante: router/login/theme/misc */ </script>
```

Ambos son scripts clásicos (sin `defer`/`async`), en la posición que fija la
tarjeta (entre `compare.js` y el inline). `device-detail.js` va antes que
`dashboard.js` porque el modal emite `onclick="setDTab/qaB/…"` y llama
`renderD` en tiempo de llamada; aun así ninguno de los dos lee un símbolo al
evaluarse (top-level = declaraciones `function` + `let` de estado propio).

Única excepción de top-level ejecutable, deliberada y con el DOM ya parseado:
`dashboard.js` contiene las **dos sentencias sueltas sin `DOMContentLoaded`**
que la tarjeta manda mover — `document.getElementById('grid').addEventListener`
y `document.getElementById('ops-focus-items').addEventListener` (mapa §7.9).
El script se carga al final del body, después de todo el markup, igual que
cuando vivían en el inline; la de `.sidebar-item[onclick]` (router, lote k)
sigue inline y no se tocó.

## Qué fue a cada archivo (mismo orden relativo; rangos 1-indexed del archivo pre-lote)

| Archivo | Contenido movido |
|---|---|
| `views/device-detail.js` | Tramo 1 (3544–3706): `let dTab`, `setDTab(t)` (parámetro `t` que eclipsa el token, §7.3, conservado), comentario `── DETAIL (all-in-one) ──`, `selD` (4 fetch en paralelo, `setLang(currentLang)`, `window.dLiveT` de 5 s con autodestrucción y limpieza por `closeD`/`logout`), `rCmdBig` (c-big/t-big; dentro `t` es el elemento DOM, no el JWT — §7.3), `closeD`. Tramo 2 (3748–3950): `qaB`, `qaS`, `qaP` (el `prompt()` es preexistente), `qaM`, y el bloque muerto del detalle antiguo tal cual — `loadF`, `loadI`, `loadC` + `let curC`, `uc`, `dep`, `cDep`, `dlC`, `renderCmd`, `rCmd`, `let tplT`, `renderTpl`, `tplP`, `tplR`, `tplA`, `tplC`. Estado propio: `let cmdH` (solo `rCmdBig`/`rCmd`). |
| `views/dashboard.js` | `loadDonuts` (1 fetch por equipo, AbortController 2,5 s), `refreshAlertSummary` (latido 45 s → badge sidebar/header; `alertSummary === null` = sin verificar, no 0), `init` (setLang, restauración de hash, 4 `_npTick`: loadH 10 s / loadBk 60 s / refreshAlertSummary 45 s / loadDev 30 s, `#info-dev`, `setTimeout(loadDonuts, 1500)` y `setInterval(loadDonuts, 120000)` con guard de `#v-devices`), `loadH` (latido 10 s), `fDev` (filtro `#sb`), `renderD` (± `fDev()` final), listener `#grid`, `goToOverviewDevices`, `showAddDevicePanel`, listener `#ops-focus-items`, `updateDashboardHero`, `updStats` (escribe `overviewState` y termina en `setLang(currentLang)`). |

Los `function` top-level cuelgan de window: los onclick/oninput del markup
(`fDev()` del buscador, `goToOverviewDevices()`, `showAddDevicePanel()`,
`closeD()`, `delDev(sel.id)`), los generados por `renderD` (`tB2`, `selD`) y
por `selD` (`setDTab`, `qaB/qaS/qaP/qaM`, `dlC`, `rCmdBig`), los despachos de
`swView` (`loadDev` ya en devices.js; `init` en `enterSession`) y los latidos
`_npTick` resuelven todos sin cambios.

## Qué NO se movió (grep de exclusividad previo a mover nada)

| Símbolo | Consumidores (grep post-Lote G) | Decisión |
|---|---|---|
| `populateCmdDevices` | `loadBk` (backups.js, cada latido 60 s y tras cada backup) y `swView('commands')` (inline) | **QUEDA inline** (N=2 usuarios; regla anti-compartidas, decisión del Lote G) |
| `buildSchedForms` / `loadBk` | backups.js (loadDev de devices.js y `swView('backups')`; `init`/`qaB`) | **QUEDAN en backups.js** (Lote G); este lote solo las llama en tiempo de llamada (`init`, `qaB`) |
| `dCmp` | compare.js; `qaM` (movida) | **QUEDA en compare.js** (Lote G) |
| `esc` / `escAttr` / `iconTextHTML` / `setIconText` / `IC` / `icon` | compartidos | **QUEDAN inline** (tiempo de llamada) |
| `__isAdmin` / `npGray` / `_detText` / `_fmtBytes` (×2) / `_fmtUptime` / `alDur` | compartidos (Lotes A–G) | **QUEDAN inline**, in situ entre los dos tramos del detalle (mapa §7.1 intacta) |
| `devices` / `sel` / `aTab` / `bSel` / `bMode` | todo el árbol / renderD / listener `#grid` | **QUEDAN en la línea de estado del inline** (L3026); `dashboard.js` y `device-detail.js` los resuelven en tiempo de llamada, sin `window.x` |
| `alertSummary` / `overviewState` / `inventoryError` | `overviewState` lo lee `setLang` (core.js, cada cambio de idioma); `inventoryError` lo escribe `loadDev` (devices.js) | **QUEDAN en el inline** (L3027–3028), como pidió la tarjeta (`inventoryError` en la lista de compartidas) y por la regla 4 |
| `swView` | router inline (lote k) | **QUEDA** (no se tocó ni una línea de su despacho) |
| `A`, `h`, `t`, `toast`, `__t`, `setLang`, `_npTick`, `currentLang` | core.js | **QUEDAN** (tiempo de llamada) |

## Trampas de la tarjeta — decisiones

| Tema | Decisión |
|---|---|
| `init` con `setInterval(loadDonuts, 120000)` no cancelable (§7.12) | Viaja **tal cual**: id no guardado, `logout` no lo para, guard de `#v-devices`. Defecto preexistente documentado, NO "arreglado" |
| 4 latidos `_npTick` sin id guardado (§7.12) | Viajan tal cual: se autolimitan con `if (t)` |
| `loadDev` escribe en DOM de otras vistas (§7.6) | `loadDev` sigue en devices.js (Lote F); sus llamadas `renderD()/loadDonuts()/updStats()` y el pisado de `#cmp-a/#cmp-b` ahora resuelven contra window. Cero cambios de lógica |
| `updStats` llama `setLang(currentLang)` cada pasada (§7.16) | Conservado; `setLang` (core.js) lee `overviewState` y llama `updateDashboardHero` en tiempo de llamada |
| `selD` reescribe pestañas y arranca intervalo (§7.10) | Viaja con `window.dLiveT` intacto (lo limpian `closeD` y `logout` de core.js) |
| `setDTab(t)` y `rCmdBig` eclipsan `t` (§7.3) | Byte a byte, sin renombrar |
| Código muerto `loadF/loadI/loadC/renderCmd/rCmd/renderTpl/tpl*/uc/dep/cDep` (§2.3) | **Se movió tal cual, no se borró ni se revivió** (la tarjeta lo permite si es exclusivo; grep: 1 referencia = su declaración) |
| `cmdH` | Salía de la línea `let` compartida; sus únicos consumidores eran `rCmdBig`/`rCmd` → viaja como `let cmdH` interno de device-detail.js |
| Markup del modal `#dp` y de la portada | **Intacto**: los cortes son solo del `<script>` |

## Verificación del lote

- `node --check`: `views/dashboard.js` (307 líneas), `views/device-detail.js`
  (425), `core.js`, los 22 `.js` previos y el inline restante extraído a
  temporal (**2.214 líneas**) → OK los 25.
- **Smoke de evaluación real (vm de Node, contexto único)**: los 25 scripts en
  su orden de carga (core → 22 vistas → inline) evalúan sin `SyntaxError` ni
  redeclaraciones; y llamadas cruzadas posteriores (`renderD`, `fDev`,
  `updStats`, `setDTab`, `closeD`, `goToOverviewDevices`,
  `showAddDevicePanel`, `populateCmdDevices`, `updateDashboardHero`,
  `loadDonuts`, `renderCmd`, `renderTpl`, `rCmdBig`, `qaM`, `uc`, `tplC`) no
  lanzan `ReferenceError`.
- Auditoría de equivalencia: `dashboard.js` = **229/229 líneas de código
  byte a byte en el mismo orden** que el monolito; `device-detail.js` =
  338/339 (única diferencia: `let cmdH = [];` izado del grupo `let` compartido).
  0 líneas perdidas y 0 duplicadas en el multiset del inline completo.
- Grep de redeclaración: los 32 nombres movidos con EXACTAMENTE 1
  `(async )?function <nombre>` en core.js + views/*.js + inline;
  `cmdH`/`dTab`/`curC`/`tplT` solo en device-detail.js;
  `alertSummary`/`overviewState`/`inventoryError`/`devices`/`sel`/`aTab`/
  `bSel`/`bMode` solo en el inline. Cero duplicados de `let/const/class`
  entre scripts (verificador estático).
- Handlers invocados desde atributos `on*` (markup + strings generados en
  todos los `.js` e inline): **0 sin resolver** (único falso positivo `if`,
  como en todos los lotes).
- CSS intacto (`style.css?v=4` L12, `overview.css?v=3` L1505); los 2 `.js`
  nuevos con `?v=1`. Sin `console.log`/`confirm` nuevos.
- `tests/`: **358 passed** (con el `.venv` del repo principal, `PYTHONPATH=.`).

## Divergencias documentadas

1. **Estado del hero de vuelta al inline**: el corte heredado había movido a
   `dashboard.js` las declaraciones `let alertSummary / overviewState /
   inventoryError`. Se restauran en el bloque de estado del inline (L3026–3028,
   como HEAD): `inventoryError` lo escribe `loadDev` (devices.js) y
   `overviewState` lo lee `setLang` (core.js) — no son de un único archivo
   (regla 4), y la tarjeta lista `inventoryError` entre las compartidas.
   Comportamiento idéntico: todo son resoluciones en tiempo de llamada.
2. **`let cmdH` sale de la línea `let` compartida** y pasa al inicio de
   `device-detail.js`: es la única línea de código que no es byte a byte la
   original (misma semántica, declaración izarada; solo lo usan `rCmdBig` y
   `rCmd`). `aTab` sigue inline aunque esté muerta (§2.3: no borrar sin
   tarjeta aparte).
3. **El código muerto viaja** (`loadF/loadI/loadC`, `renderCmd/rCmd`,
   `renderTpl/tpl*`, `uc/dep/cDep`, `curC`, `tplT`) en vez de quedarse: grep
   confirma exclusividad del bloque del detalle y la tarjeta autoriza moverlo
   "tal cual si es exclusivo". No se revivió ninguna referencia.
4. **Dos archivos, no uno**: la tarjeta nombra `views/dashboard.js` y
   `views/device-detail.js`; se respeta. No hay helper privado compartido entre
   ambos (`selD→renderD` y `rCmd→updStats` son llamadas de tiempo de llamada),
   así que el corte no duplica nada.
5. **Orden de carga** `device-detail.js → dashboard.js` (el inverso del orden
   de líneas del monolito: la portada va 3277–3543, el detalle 3544–3950).
   No observable: ningún símbolo movido se lee a la evaluación; el único
   top-level ejecutable (los 2 listeners del dashboard) solo necesita el DOM,
   que ya está parseado.

## Estado del plan de extracción (siguiente ronda)

Tras este lote quedan:

| Lote | Contenido |
|---|---|
| i | terminal+sftp (`_term*`, `term*`, `cmdOpenConsole`, `_sftp*`, `sftp*` + `_formatBytes`/`_formatTime`) |
| j | topology (`_topo*`, `topoTab`, `buildTraceOrigins`, `startTopoGraph`, `ensureTopoMap`, `runTopoTrace`, …) |
| k | router (`swView` + `hashchange`) y boot (listeners globales + IIFE de tema/`restoreSession`) — cierre |

| Fase del mapa (§5.2) | Estado |
|---|---|
| 1 core/state, 2 i18n, 5 session | hecha en core.js (paso 1) |
| 3 dom, 4 format, 6 theme | pendiente (inline conserva esc/escAttr/IC/icon/tema) |
| 7 vistas simples: A + B | hecha |
| 8 performance+alerts+admin (Lote C) · backups/compare (Lote G) | hecha |
| 9 reports, retention, sites, netbox | hecha (Lote D) |
| 10 commands, monitoring | hecha (Lote E) |
| 11 devices+discovery | hecha (Lote F) |
| 12 dashboard+detalle | **hecha (este lote)** |
| 13 terminal/sftp (i), 14 topology (j) | siguiente |
| 15 router, 16 boot (k) | al final por diseño |

---

# Lote I — Tarjeta 10D-I: terminal + SFTP (la zona más viva)

Fecha: 2026-10-24 · Rama: `feat/ui-views-i` (worktree `/Users/doker/np-w10m`) ·
Commit: `refactor(ui): extraer lote i (terminal, sftp)`.

Mismo enfoque que los Lotes A–H: grep de exclusividad ANTES de mover;
`dashboard.html` pasó de **5.244 → 4.087 líneas** (−1.157 netas: −1.167/+10;
las 10 añadidas son 8 de stub + 2 `<script src>`), y el contenido del
`<script>` inline residual baja de **2.212 a 1.053 líneas**. Dos archivos
nuevos, porque el bloque del monolito ya venía partido en Terminal y SFTP y
cada mitad es una unidad cerrada (los cruces son tiempo de llamada).

## Orden de carga en dashboard.html (tras core.js y los 24 previos, antes del inline)

```html
  <script src="/static/js/views/dashboard.js?v=1"></script>
  <script src="/static/js/views/terminal.js?v=1"></script>
  <script src="/static/js/views/sftp.js?v=1"></script>
  <script> /* inline restante: router/login/theme/topology/misc */ </script>
```

Ambos son scripts clásicos (sin `defer`/`async`), en la posición que fija la
tarjeta (entre `dashboard.js` y el inline, orden terminal → sftp, el mismo del
monolito). Top-level **sin lógica**: solo `const` (TERM_*, `_TERM_TONES`,
`_SFTP_ICON_*`), `let` de estado y declaraciones `function`. Ningún símbolo
movido se lee al evaluar los scripts; todas las dependencias resuelven en
tiempo de llamada, así que el orden entre ambos archivos solo importa para las
declaraciones de estado (ver abajo).

## Qué fue a cada archivo (byte a byte; rangos 1-indexed del archivo pre-lote)

| Archivo | Contenido movido (mismo orden relativo) |
|---|---|
| `views/terminal.js` | Tramo 4021–4717: comentario `── CONSOLA WEB (TERMINAL SSH JSON) & GESTOR SFTP ──` + contrato, constantes `TERM_TAB_MAX`/`TERM_RETRY_MAX`/`TERM_BACKOFF_MS`/`TERM_PING_MS`/`TERM_PONG_TIMEOUT_MS`/`TERM_BUF_MAX`, estado `_term`/`_fitAddon`/`_termWs`, `_termSessions`, `_termActiveId`, `_termSeq`, `_currentTermTab`, `_sftpCurrentPath`, `_sftpDeviceId`, `_sftpBlocked`, `_sftpBusy`, `_sftpLastGoodPath`, y 36 funciones: `_termActive`, `switchTermTab`, `termPopulateDevices`, `termDevChanged`, `_setTermStatus`, `_termRenderTabs`, `termActivateTab`, `termCloseTab`, `_termCreateSession`, `_termBuildXterm`, `termOpenConsoleFor`, `termConnect`, `_termManualRetry`, `_termOpenWs`, `_termSendResize`, `_termStartPing`, `_termKillPings`, `_termStartBackoff`, `_termGaveUp`, `_termHandleError`, `_termHandleClosed`, `_termBanner` (+ `_TERM_TONES`), `_termHideBanner`, `_termRefreshUI`, `termDisconnect`, `termClear`, `_termWatchResize`, `termToggleFullscreen`, `_termCopySel`, `_termCopyFallback`, `termSearchRun`, `termSearchClose`, `_termMarkLine`, `cmdOpenConsole` |
| `views/sftp.js` | Tramo 4719–5187: comentario `── GESTOR SFTP (TRANSFERENCIA DE ARCHIVOS) ──`, `_formatBytes`, `_formatTime`, `_sftpNormPath`, `_sftpJoin`, `_sftpArg`, `_sftpSetSummary`, `_sftpProgress`, `_sftpProgressDone`, `sftpHideBanner`, `sftpShowBanner`, `sftpSetTransferEnabled`, `sftpRenderBreadcrumb`, `_sftpNotice`, `const _SFTP_ICON_WARN/_SFTP_ICON_FOLDER`, `_sftpClearList`, `_sftpErrorInfo`, `sftpApplyError`, `sftpLoadDir`, `sftpRefresh`, `sftpOpenDir`, `sftpNavUp`, `sftpUploadSelectedFile`, `sftpHandleDrop`, `_sftpFilenameFromCd`, `_sftpSaveBlob`, `sftpDownloadFile` |

Todo lo de la tarjeta viaja **tal cual**: pestañas (hasta 4), scrollback/buffer
por pestaña, búsqueda en buffer, copiar/pegar (`_termCopySel` +
`_termCopyFallback`), WS dual (`?token=…&fmt=json`), 4401/4403, idle,
heartbeat 25 s + margen 10 s y backoff visible 1-2-4-8-15-15 s (6 intentos)
con `_termGaveUp`. No se renombró, "modernizó" ni reordenó una sola línea.

Los `function` top-level cuelgan de window: los `onclick`/`onchange`/
`onkeydown`/`ondrop` del markup de `#v-terminal` (`termConnect`,
`termDisconnect`, `termDevChanged`, `termClear`, `termToggleFullscreen`,
`termSearchRun`, `_termCopySel`, `switchTermTab('terminal'|'sftp')`,
`sftpLoadDir`, `sftpRefresh`, `sftpNavUp`, `sftpUploadSelectedFile`,
`sftpHandleDrop`, `sftpHideBanner`), los `onclick` generados por
`_termRenderTabs` (`termActivateTab`, `termCloseTab`, `termSearchClose`), por
`sftpRenderBreadcrumb`/`sftpLoadDir` (`sftpLoadDir`, `sftpOpenDir`,
`sftpDownloadFile`), el botón de `#v-commands` y su `onclick` generado
(`cmdOpenConsole`) y el despacho de `swView` (`termPopulateDevices`) siguen
resolviendo sin cambios.

## Grep de exclusividad (los 59 nombres movidos)

Consumidores fuera del bloque, recalculados post-Lote H:

- **Markup de `#v-terminal` / `#v-commands`**: solo en atributos `on*`. Se
  resuelven por window en el click, como en todos los lotes.
- **`swView` (inline, lote k)**: `termPopulateDevices` (líneas 3469–3470),
  `_currentTermTab` y `_fitAddon` (3471–3472). Nada más.
- **`app/static/js/**` (core + 22 vistas previas): CERO referencias** a
  cualquiera de los 59 nombres. Los dos archivos son mutuamente exclusivos.

## Estado movido (regla 4)

- `_term` / `_fitAddon` / `_termWs` (alias de la pestaña activa),
  `_termSessions`, `_termActiveId`, `_termSeq`, `_currentTermTab` → viajan con
  su `let` a `terminal.js`. `swView` los lee en tiempo de llamada; un
  `let`/`const` top-level de un script clásico vive en el entorno léxico
  global compartido entre scripts, así que el inline (evaluado después) los
  ve, igual que hoy. **No** se pasaron a `window.x` (nadie los invoca desde
  un atributo `on*` ni desde otro archivo por nombre global).
- `_sftpCurrentPath` / `_sftpDeviceId` / `_sftpBlocked` / `_sftpBusy` /
  `_sftpLastGoodPath` → se quedan declaradas en `terminal.js`, exactamente
  donde estaban (el bloque de estado del monolito), y `sftp.js` las
  lee/escribe en tiempo de llamada. No se duplican ni se crea un tercer
  archivo de estado: `switchTermTab`/`termDevChanged` (terminal.js) y
  `sftpLoadDir`/`sftpUpload*` (sftp.js) las comparten igual que antes.
- `_TERM_TONES`, `_SFTP_ICON_WARN`, `_SFTP_ICON_FOLDER`, `TERM_*` → `const`
  exclusivos de su archivo (grep: solo sus propias funciones).
- `window._termRO` sigue siendo `window._termRO` (lo escribe/lee
  `_termWatchResize`/`_termCreateSession`); `logout` (core.js) sigue sin
  cerrar WS ni el observer (§7.15): defecto preexistente, no se tocó.

## Trampas de la tarjeta — decisiones

| Tema | Decisión |
|---|---|
| `_termXterm`/`xterm`/`fitAddon` | No existen como globales de window: son `s.term`/`s.fit` por sesión y los alias `_term`/`_fitAddon`/`_termWs`. Viajan con su `let` en `terminal.js` (consumidor externo: `swView`). `Terminal` (vendor xterm.js en `<head>`) se usa como global tal cual, con los guards `typeof Terminal === 'undefined'` intactos |
| `initDragDrop` de monitoring.js | **NO se toca ni se reutiliza.** El drag & drop de SFTP es `sftpHandleDrop(event)` (dropzone del markup) y `ondragover/ondragleave` inline; `views/monitoring.js` conserva `initDragDrop` + `dragSrc` para los charts. Cero doble declaración |
| `_formatBytes`/`_formatTime` | Nombres distintos de `_fmtBytes` (§7.1): exclusivos de SFTP, viajan a `sftp.js` |
| `sftpLoadDir` en el centro Terminal ↔ SFTP (§7.14) | Viaja a `sftp.js`; `switchTermTab`/`termDevChanged` (terminal.js, cargado antes) lo llaman en tiempo de llamada. El estado `_sftp*` queda en terminal.js (arriba) |
| Código 4401/4403, banners por causa | Byte a byte (`_termHandleClosed`/`_termHandleError`/`_termGaveUp`; `_sftpErrorInfo`/`sftpApplyError`/`sftpShowBanner`) |
| `esc`/`escAttr`/`setIconText`/`_detText`/`devices`/`A`/`h`/`t`/`toast`/`__t`/`swView`/`loadDev` | Compartidos: QUEDAN inline/core (tiempo de llamada) |
| Markup de `#v-terminal`, `#v-commands`, modal `#dp` | **Intacto**: los cortes son solo del `<script>` |

## Verificación del lote

- `node --check`: `views/terminal.js` (756 líneas), `views/sftp.js` (515),
  `core.js`, las 24 vistas previas y el inline restante extraído a temporal
  (**1.053 líneas**) → OK los 27.
- **Smoke de evaluación real (vm de Node, contexto único, como el lote H)**:
  los 27 scripts en su orden de carga (core → 22 vistas → terminal → sftp →
  inline) evalúan sin `SyntaxError` ni redeclaraciones. Llamadas posteriores
  desde el contexto: `termConnect()`, `switchTermTab('sftp')` y
  `switchTermTab('terminal')`, `_termRefreshUI()`, `_termRenderTabs()`,
  `sftpRefresh()`, `sftpRenderBreadcrumb('/a/b')`, `sftpHideBanner()`,
  `_formatTime(0)` y lecturas de `_currentTermTab`/`_fitAddon` desde el inline
  → sin `ReferenceError`.
- **Auditoría de equivalencia**: los 697 renglones del tramo Terminal y los
  469 del tramo SFTP aparecen **byte a byte** en los `.js` nuevos, contiguos y
  en el mismo orden (`diff` contra `git show HEAD` = 0). Multiset de líneas no
  vacías del `<script>` completo: **0 perdidas, 0 duplicadas**; las 8 extra
  son el comentario-stub del inline.
- Grep de redeclaración: los 59 nombres movidos con EXACTAMENTE 1
  `(async )?function <nombre>` en todo el árbol; estado (`_term*`, `_sftp*`,
  `TERM_*`, `_TERM_TONES`, `_SFTP_ICON_*`) declarado una sola vez.
- Handlers invocados desde atributos `on*` del markup (80) y desde strings
  generados (73): **0 sin resolver** (único falso positivo `if`).
- Conteos: 5.244 − 1.167 + 10 = **4.087 líneas** (git numstat 10/1167).
- `tests/`: **358 passed** (con el `.venv` del repo principal, `PYTHONPATH=.`).
- `<link>` CSS intactos (style.css?v=4 L12, overview.css?v=3 L1505, vendor
  xterm/leaflet L1506–1507); los 2 `.js` nuevos con `?v=1`. Sin
  `console.log`/`confirm` nuevos (grep = 0 en ambos archivos).

## Divergencias documentadas

1. **Dos archivos, no uno**: la tarjeta los nombra por separado y el bloque
   del monolito ya venía partido. El estado `_sftp*` se queda en
   `terminal.js` porque ahí estaba declarado y lo comparten ambas mitades
   (terminal lo escribe al cambiar pestaña/dispositivo; sftp lo lee y
   escribe). Alternativa descartada: `window._sftp*` explícito — no lo pide
   ningún `on*` y añadiría superficie global nueva sin necesidad.
2. **Superficie global nueva**: 59 nombres (incluidos `_termOpenWs`,
   `_formatBytes`, `_sftp*`…) pasan a ser `function` de un script clásico y
   por tanto propiedades de `window`. Era inevitable al extraer (misma regla
   que lotes anteriores) y no colisiona: grep = 1 declaración por nombre en
   core + vistas + inline.
3. **`_sftpErrorInfo` llama `switchTermTab('terminal')`** (501 → abrir
   consola): cruce entre los dos archivos nuevos, resuelto por window en
   tiempo de llamada; `sftp.js` se evalúa después de `terminal.js`.
4. **Números del inline**: el anexo H citaba 2.214 líneas (con las etiquetas
   `<script>`); aquí se miden 2.212 → 1.053 con el mismo criterio que los
   lotes previos (contenido entre etiquetas).

## Estado del plan de extracción (siguiente ronda)

Tras este lote quedan **j** y **k**:

| Lote | Contenido |
|---|---|
| j | topology (`_topo*`, `topoTab`, `buildTraceOrigins`, `loadTopology`, `startTopoGraph` + `step`/`draw`/`loop`, `ensureTopoMap`, `updateTopoMapDevices`, `redrawTopoTrace`, `runTopoTrace`, `clearTopoTrace`, `renderTraceResult`, `loadHealth` muerta + listeners `resize`/`Escape`) |
| k | router (`swView` + `hashchange`) y boot (listeners globales del `.sidebar-item[onclick]`, IIFE de tema y `restoreSession`, `init` si no viajó, cierre del refactor) |

| Fase del mapa (§5.2) | Estado |
|---|---|
| 1 core/state, 2 i18n, 5 session | hecha en core.js (paso 1) |
| 3 dom, 4 format, 6 theme | pendiente (inline conserva esc/escAttr/IC/icon/tema) |
| 7 vistas simples: A + B | hecha |
| 8 performance+alerts+admin (Lote C) · backups/compare (Lote G) | hecha |
| 9 reports, retention, sites, netbox | hecha (Lote D) |
| 10 commands, monitoring | hecha (Lote E) |
| 11 devices+discovery | hecha (Lote F) |
| 12 dashboard+detalle | hecha (Lote H) |
| 13 terminal/sftp | **hecha (este lote)** |
| 14 topology (j) | siguiente |
| 15 router, 16 boot (k) | al final por diseño (cierre) |

---

# Lote J — Tarjeta 10D-J: topología

Fecha: 2026-10-24 · Rama: `feat/ui-views-j` (worktree `/Users/doker/np-w10n`) ·
Commit: `refactor(ui): extraer lote j (topology)`.

Penúltimo lote. Mismo método que A–I: grep de exclusividad ANTES de mover;
sin cambio de comportamiento, sin duplicar y conservando el orden relativo.
`dashboard.html` pasó de **4.087 → 3.576 líneas** (−511 netas: −524/+13; las
13 añadidas son 1 `<script src>` + 12 líneas de comentario-stub). El contenido
del `<script>` inline residual baja de **1.053 a 540 líneas**.

## Orden de carga en dashboard.html (tras core.js y las 26 vistas previas, antes del inline)

```html
  <script src="/static/js/views/sftp.js?v=1"></script>
  <script src="/static/js/views/topology.js?v=1"></script>
  <script> /* inline restante: router/login/theme/misc */ </script>
```

`views/topology.js` es un script clásico (sin `defer`/`async`) en la posición
que fija la tarjeta (entre `sftp.js` y el inline). Su top-level es estado
`let`, declaraciones `function` y **un** listener `resize` (el DOM ya está
parseado porque el script va al final del body, como en el monolito). Ese
listener reajusta el canvas y llama `_topoMap.invalidateSize()`; se registra
una sola vez y no se lee ningún símbolo de otro script al evaluar. Antes se
registraba unas líneas más tarde, dentro del inline; ningún evento `resize`
puede dispararse durante la evaluación síncrona, así que no hay cambio
observable. Es el único listener `resize` de todo el panel (grep).

Leaflet **no se toca**: `/static/vendor/leaflet.js` sigue en el `<head>`
(línea 1510) y `leaflet.css` en la 1507. `L` es el global del vendor; el mapa
(`_topoMap`), sus capas (`_topoDevLayer`, `_topoTraceLayer`) y el último trace
(`_topoTraceData`) viajan con su `let` a `topology.js`, no a `window.x`.

## Qué fue a `views/topology.js` (byte a byte; rango 1-indexed del archivo pre-lote)

Tramo contiguo **3497–4021** (525 líneas), en el mismo orden relativo:

| Orden | Contenido |
|---|---|
| 1 | Comentario `── TOPOLOGY (grafo canvas + mapa Leaflet + tabla + traceroute) ──` |
| 2 | Estado: `let _topoData`, `_topoTab`, `_topoGraph`, `_topoGraphRAF`, `_topoMap`, `_topoDevLayer`, `_topoTraceLayer`, `_topoTraceData` |
| 3 | `_t(k)` — alias local de i18n (`I18N[currentLang] / I18N.en`, sin fallback extra) |
| 4 | `topoTab(tv)` — pestañas graph/map/table + `requestAnimationFrame` |
| 5 | `buildTraceOrigins()` — rellena y conserva `#topo-trace-origin` |
| 6 | `loadTopology()` — `GET /topology/map`; stats, nodos, tabla de enlaces, `buildTraceOrigins`, `topoTab(_topoTab)` y `renderTraceResult(null)` si no hay trace |
| 7 | `_topoScreen2World`, `_topoResolveEdges` |
| 8 | `startTopoGraph()` + `step`/`draw`/`loop` anidadas (física O(n²), drag, zoom, hover, tooltip; los handlers del canvas son `cv.onmouse*`/`onwheel`/`ondblclick`) |
| 9 | `ensureTopoMap()` — Leaflet lazy (`L.map`, tile OSM, layerGroups), guard `typeof L === 'undefined'` |
| 10 | `updateTopoMapDevices()` — `L.circleMarker` + popups + leyenda |
| 11 | `redrawTopoTrace()` — markers/polilínea del trace y `fitBounds` |
| 12 | `runTopoTrace()` — `POST /topology/traceroute`, mensajes con `setIconText`/`_t`, salto a mapa |
| 13 | `clearTopoTrace()`, `renderTraceResult(d)` |
| 14 | Listener `window.addEventListener('resize', …)` (canvas + Leaflet) |
| 15 | Bloque `── HEALTH SCORES ──` con `loadHealth()` (código muerto, ver abajo) |

Los `function` top-level cuelgan de window: los handlers del markup de
`#v-topology` (`topoTab('graph'|'map'|'table')`, `loadTopology()`,
`runTopoTrace()`, y el `onkeydown` del destino que llama `runTopoTrace()`), el
`onclick="clearTopoTrace()"` generado por `renderTraceResult`, el despacho
`swView('topology') → loadTopology()` y el listener `resize` resuelven sin
cambios. `_t` era global por ser `function` top-level en el inline; ahora es
`function` top-level de un script clásico (misma resolución por window).

## Grep de exclusividad (14 funciones + 8 `let`)

- **Markup de `#v-topology`**: solo atributos `on*`; resuelven por window en el
  click, como en todos los lotes.
- **`swView` (inline, lote k)**: única referencia externa, `if (v === 'topology')
  loadTopology();`. Es tiempo de llamada.
- **`core.js` + las 25 vistas previas**: CERO referencias a `topoTab`,
  `buildTraceOrigins`, `loadTopology`, `_topoScreen2World`, `_topoResolveEdges`,
  `startTopoGraph`, `ensureTopoMap`, `updateTopoMapDevices`, `redrawTopoTrace`,
  `runTopoTrace`, `clearTopoTrace`, `renderTraceResult`, `loadHealth`, `_t` ni
  a las 8 variables `_topo*`.
- **Atributos `on*`**: ninguno nombra `_t` ni el estado `_topo*`.

Consecuencia (regla 4): el estado se queda como `let` interno de
`topology.js` (script clásico = binding del entorno léxico global compartido;
ningún `on*` ni otro archivo lo consulta), no como `window._topo*`.

## Decisiones de las trampas de la tarjeta

| Tema | Decisión |
|---|---|
| Leaflet vendorizado, objeto `map` y capas | `L` sigue siendo el global del vendor; `_topoMap`/`_topoDevLayer`/`_topoTraceLayer`/`_topoTraceData` viajan con su `let`. No se movió ningún `<script src>` del `<head>` ni `leaflet.css` |
| `_t` | Viaja: tras el Lote H (que movió `renderCmd`, su otro consumidor muerto) el grep da **un único archivo** (topology.js). No se colapsó en `__t` (sería cambio de comportamiento en el fallback: `_t` no cae a `en` si falta en el idioma) |
| `loadHealth` (muerta) | Viaja tal cual con el tramo contiguo, como pedía el plan del Lote I. **No se revivió**: su `#health-list` no existe en el markup y su única aparición es la declaración. Es código muerto del mapa §2.3, no se borra sin tarjeta aparte |
| Listener `resize` | Viaja (depende de `_topoTab`/`_topoGraph`/`_topoMap`). El otro `Escape` que el plan del Lote I mencionaba **no** es de topología: cierra `#user-dd` y es misc global; se queda inline |
| rAF sin cancelar al cambiar pestaña/vista (§7.12) | Conservado tal cual: `_topoGraphRAF` solo se reasigna y `loop` se autodetuvo con `if (_topoGraph !== g) return`; no se añadió cancelación "de higiene" |
| `esc`, `icon`, `setIconText`, `A`, `h`, `t`, `__t`, `I18N`, `currentLang` | Compartidos/core: quedan donde estaban; topology.js los resuelve en tiempo de llamada |
| Markup de `#v-topology`, `#topo-*`, modal `#dp` | **Intacto**: los cortes son solo del `<script>` |

## Qué NO se movió (verificado por grep)

- **`swView` + `hashchange`** — router, lote k. Incluye su despacho
  `if (v === 'topology') loadTopology();` sin tocar una línea.
- **Listener global de `Escape` de `#user-dd`** — misc del inline, no de la
  vista (ver arriba).
- **`populateCmdDevices`** — sigue inline (N=2: `loadBk` de backups.js y
  `swView('commands')`), decisión de los Lotes G/H.
- **Helpfulness compartidos**: `esc`/`escAttr`, `IC`/`icon`/`iconTextHTML`/
  `setIconText`, tema/paleta, `npExport`, `togSide`, `login`,
  `__isAdmin`/`npGray`, `_fmtBytes` (×2, trampa §7.1), `_fmtUptime`, `alDur`,
  `_detText`, `_repTs` y el estado compartido (`devices`, `sel`, `aTab`,
  `bSel`, `bMode`, `alertSummary`, `overviewState`, `inventoryError`) — todo
  sigue inline/core, sin duplicados.
- **Markup y modal `#dp`**: intactos.

## Verificación del lote

- `node --check`: `views/topology.js` (573 líneas: 48 de header + 525 movidas),
  `core.js`, las 26 vistas y el inline restante extraído a temporal
  (**540 líneas**) → OK los 28.
- **Smoke de evaluación real (vm de Node, contexto único, como H/I)**: los 28
  scripts en su orden de carga (core → 26 vistas → inline) evalúan sin
  `SyntaxError` ni redeclaraciones. Llamadas posteriores desde el contexto:
  `topoTab('table'|'map'|'graph')`, `buildTraceOrigins()`,
  `renderTraceResult(null)`, `clearTopoTrace()`, `_t('topo_tr_run')`,
  `ensureTopoMap()`, `updateTopoMapDevices()`, `redrawTopoTrace()`,
  `loadTopology()`, `loadHealth()`, `swView('topology')` y lectura de
  `_topoTab`/`_topoData` → sin `ReferenceError`.
- **Auditoría de equivalencia**: las 525 líneas del tramo viajan contiguas y
  **byte a byte** (`body == git show HEAD` → `True`). Multiset de líneas no
  vacías del `<script>` completo: **0 líneas de código perdidas y 0
  duplicadas**; las únicas "extra" son las 12 líneas del comentario-stub y la
  repetición del comentario de sección dentro de él.
- Grep de redeclaración: los 14 nombres movidos con EXACTAMENTE 1
  `(async )?function <nombre>` en `core.js` + `views/*.js` + inline; cada
  `_topo*` con una sola declaración `let`, solo en `topology.js`.
- Handlers invocados desde atributos `on*` (markup + strings generados en
  todos los `.js` e inline): **0 sin resolver** (único falso positivo `if`,
  como en todos los lotes).
- Conteos: 4.087 − 524 + 13 = **3.576 líneas** (git numstat 13/524); inline
  1.053 − 525 + 12 = **540 líneas**.
- `tests/`: **358 passed**, exit 0 (con el `.venv` del repo principal,
  `PYTHONPATH=.`).
- `<link>` CSS intactos (`style.css?v=4` L12, `overview.css?v=3` L1505,
  vendor xterm/leaflet L1506–1507); el `.js` nuevo con `?v=1`. Sin
  `console.log`/`confirm` nuevos (grep = 0 en el archivo nuevo).

## Divergencias documentadas

1. **`loadHealth` viaja a topology.js aunque no es de topología**: es el
   bloque muerto de Health Scores que quedó pegado al tramo de topología en el
   monolito. El plan del Lote I lo listaba en `j`; moverlo con el tramo
   contiguo evita dejar código muerto en el inline y no cambia comportamiento
   (nadie la llama, `#health-list` no existe). No se borró ni se revivió.
2. **`_t` viaja**: es un alias redundante de `__t`, pero su fallback es
   distinto (no cae a `en`). Tras el Lote H su único consumidor es topología;
   se conserva tal cual, sin "simplificar" a `__t`.
3. **El listener `Escape` no viaja**: el plan del Lote I citaba
   "listeners `resize`/`Escape`", pero el `Escape` contiguo a topología cierra
   `#user-dd` (menú de usuario) y es global/misc. Solo el `resize` es de la
   vista y es el que viaja.
4. **El stub del inline añade 12 líneas**: por eso el inline queda en 540 y no
   en 528. Es solo documentación; no hay código duplicado.
5. **Una línea nueva fuera del bloque**: el `<script src="…/topology.js?v=1">`
   entre `sftp.js` y el inline, como fija la tarjeta.

## Estado del plan de extracción (cierre)

Tras este lote **solo queda k** (router + boot, cierre del refactor):

| Lote | Contenido |
|---|---|
| k | `swView` + listener `hashchange`; listeners globales (`document click` del menú, `keydown` de Escape/Ctrl+K, accesibilidad del sidebar), IIFE de tema, llamada `restoreSession()`, helpers compartidos que aún viven inline (ver checklist) y cierre del refactor |

### Checklist exacto de lo que queda en el inline (para el lote k)

**Estado / constantes**
- `let devices = [], sel = null, aTab = 'facts', bSel = new Set(), bMode = false;`
- `let alertSummary = null;` (tres estados; `null` ≠ 0)
- `let overviewState = null, inventoryError = false;`
- `let loginBusy = false;`
- `const IC = {…}` (iconos SVG), `const PALETTES`, `const PALETTE_NAMES`

**Funciones (24 declaraciones; `_fmtBytes` aparece 2 veces — trampa §7.1)**
- Base DOM: `esc`, `escAttr`, `icon`, `iconTextHTML`, `setIconText`
- Tema: `setPalette`, `setTheme`, `resetTheme`, `updateThemeUI`
- Monitor/theme: `setRefreshInterval`
- Login/misc: `login`, `toggleUserMenu`, `togSide`, `npExport`
- Formato/estado: `_fmtBytes` (sombreada del bloque Performance), `_fmtBytes`
  (ganadora del bloque OLA 4), `_fmtUptime`, `alDur`
- Commands: `populateCmdDevices`
- Compartidas: `__isAdmin`, `npGray`, `_detText`, `_repTs`
- Router: `swView`

**Sentencias top-level / listeners**
- IIFE de tema al cargar (`localStorage` palette/accent).
- `document.addEventListener('click', …)` → cierra `#user-dd`.
- `restoreSession();` (llamada en el punto exacto del monolito; el cuerpo está
  en core.js).
- Accesibilidad del sidebar: `document.querySelectorAll('.sidebar-item[onclick]')`
  → `role="button"`, `tabIndex`, `aria-current` y `keydown` Enter/Space.
- `document.addEventListener('keydown', …)` → Escape cierra el drawer y
  Ctrl/Cmd+K enfoca `#sb`.
- `window.addEventListener('hashchange', …)` → `swView(hash)`.
- `document.addEventListener('keydown', …)` → Escape cierra `#user-dd`.
- Stubs de comentarios de los bloques ya extraídos: portada, `loadDev`, bulk,
  detalle `#dp`, backups/compare, NetBox, device manager, topología, terminal/
  SFTP, reports, discovery y OLA 4 — más los helpers compartidos que se quedan.

| Fase del mapa (§5.2) | Estado |
|---|---|
| 1 core/state, 2 i18n, 5 session | hecha en core.js (paso 1) |
| 3 dom, 4 format, 6 theme | **hecha (Lote K: `ui-common.js` + `ui-helpers.js`)** |
| 7 vistas simples: A + B | hecha |
| 8 performance+alerts+admin (Lote C) · backups/compare (Lote G) | hecha |
| 9 reports, retention, sites, netbox | hecha (Lote D) |
| 10 commands, monitoring | hecha (Lote E) |
| 11 devices+discovery | hecha (Lote F) |
| 12 dashboard+detalle | hecha (Lote H) |
| 13 terminal/sftp | hecha (Lote I) |
| 14 topology | hecha (Lote J) |
| 15 router, 16 boot (k) | **hecha (Lote K, este lote): plan COMPLETADO** |

---

# Lote K — cierre (Tarjeta 10D-K: router + boot)

Fecha: 2026-10-24 · Rama: `feat/ui-views-k` (worktree `/Users/doker/np-w10o`) ·
Base: `main` con `core.js` + Lotes A–J · Commit:
`refactor(ui): extraer lote k (router y boot) — inline a cero`.

Último lote del plan. `dashboard.html` se queda **sin JS inline**: el `<script>`
clásico residual de 541 líneas de contenido se partió en **3 scripts clásicos
contiguos**, cargados en el mismo orden y sin reordenar una sola sentencia. El
resultado es byte a byte el mismo código, solo repartido en archivos.

## Resultado en cifras

| Métrica | Antes (Lote J) | Después (Lote K) |
|---|---|---|
| Líneas de `dashboard.html` | 3.576 | **3.045** (−531 netas: −543 / +12) |
| Líneas de JS inline (contenido entre `<script>` e `</script>`) | 541 | **0** |
| Bloques `<script>` con cuerpo | 1 | **0** |
| `<script src>` de la app | 27 | **30** (core + 26 vistas + 3 del cierre) |
| Archivos JS de la app | 27 | **30** |
| Líneas de JS de la app | 7.318 | **7.874** |

Las 12 líneas añadidas al HTML son 9 de comentario-índice (índice de carga +
contrato de login/estado, ver divergencia 2) y 3 `<script src>`.

## Mapa FINAL de archivos (plan COMPLETADO)

| Archivo | Líneas | Contenido |
|---|---|---|
| `app/static/js/core.js` | 883 | `A`, `t`, `currentUser`, i18n, `toast`, `h`, interceptor 401, sesión y `_npState*` (Lote de apertura) |
| `app/static/js/views/connectivity.js` | 79 | Connectividad |
| `app/static/js/views/compliance.js` | 141 | Compliance |
| `app/static/js/views/webhooks.js` | 47 | Webhooks |
| `app/static/js/views/groups.js` | 79 | Grupos |
| `app/static/js/views/maintenance.js` | 57 | Mantenimiento |
| `app/static/js/views/apikeys.js` | 49 | API Keys |
| `app/static/js/views/tfa.js` | 43 | 2FA / TOTP |
| `app/static/js/views/logs.js` | 183 | Logs (audit + Loki) |
| `app/static/js/views/performance.js` | 144 | Performance / Equipos |
| `app/static/js/views/alerts.js` | 357 | Alertas (reglas y silencios) |
| `app/static/js/views/admin.js` | 167 | Admin usuarios + scopes |
| `app/static/js/views/reports.js` | 301 | Reports SLA |
| `app/static/js/views/retention.js` | 163 | Retención |
| `app/static/js/views/sites.js` | 203 | Sites remotos |
| `app/static/js/views/netbox.js` | 227 | NetBox |
| `app/static/js/views/commands.js` | 54 | Commands |
| `app/static/js/views/monitoring.js` | 444 | Monitoring (+ `refMon` con su auto-envoltura) |
| `app/static/js/views/discovery.js` | 336 | Wizard auto-discovery |
| `app/static/js/views/devices.js` | 515 | Inventario / alta |
| `app/static/js/views/backups.js` | 176 | Backups + scheduler |
| `app/static/js/views/compare.js` | 94 | Compare |
| `app/static/js/views/device-detail.js` | 425 | Modal `#dp` + resto del detalle |
| `app/static/js/views/dashboard.js` | 307 | Overview, `loadDev`, hero, KPIs |
| `app/static/js/views/terminal.js` | 756 | Terminal SSH |
| `app/static/js/views/sftp.js` | 515 | SFTP |
| `app/static/js/views/topology.js` | 573 | Topología (+ `loadHealth` muerta) |
| **`app/static/js/ui-common.js`** | **255** | **Lote K**: estado compartido, `esc`/`escAttr`, `IC`/`icon`/`iconTextHTML`/`setIconText`, tema, `setRefreshInterval`, IIFE de tema, login, menú de usuario, `restoreSession()`, `togSide`, `npExport` |
| **`app/static/js/ui-helpers.js`** | **134** | **Lote K**: `_fmtBytes` (la sombreada), `_fmtUptime`, `alDur`, `populateCmdDevices`, `__isAdmin`, `npGray` |
| **`app/static/js/router.js`** | **167** | **Lote K**: accesibilidad del sidebar, listeners globales (Escape/Ctrl+K, `#user-dd`), `swView` + `hashchange`, `_detText`, `_repTs`, `_fmtBytes` (la ganadora) |
| `app/static/dashboard.html` | **3.045** | Solo markup + `<script src>`; **0 líneas de JS inline** |

Total JS de la app: **7.874 líneas en 30 archivos**.

## Orden de carga completo (contrato de la página)

```html
<head>
  <link rel="stylesheet" href="/static/style.css?v=4">
  …
  <link rel="stylesheet" href="/static/overview.css?v=3">
  <link rel="stylesheet" href="/static/vendor/xterm.css">
  <link rel="stylesheet" href="/static/vendor/leaflet.css">
  <script src="/static/vendor/xterm.js"></script>
  <script src="/static/vendor/xterm-addon-fit.js"></script>
  <script src="/static/vendor/leaflet.js"></script>
</head>
<body>
  … markup (login, sidebar, 24 vistas y modal #dp) …

  <script src="/static/js/core.js?v=1"></script>
  <script src="/static/js/views/connectivity.js?v=1"></script>
  <script src="/static/js/views/compliance.js?v=1"></script>
  <script src="/static/js/views/webhooks.js?v=1"></script>
  <script src="/static/js/views/groups.js?v=1"></script>
  <script src="/static/js/views/maintenance.js?v=1"></script>
  <script src="/static/js/views/apikeys.js?v=1"></script>
  <script src="/static/js/views/tfa.js?v=1"></script>
  <script src="/static/js/views/logs.js?v=1"></script>
  <script src="/static/js/views/performance.js?v=1"></script>
  <script src="/static/js/views/alerts.js?v=1"></script>
  <script src="/static/js/views/admin.js?v=1"></script>
  <script src="/static/js/views/reports.js?v=1"></script>
  <script src="/static/js/views/retention.js?v=1"></script>
  <script src="/static/js/views/sites.js?v=1"></script>
  <script src="/static/js/views/netbox.js?v=1"></script>
  <script src="/static/js/views/commands.js?v=1"></script>
  <script src="/static/js/views/monitoring.js?v=1"></script>
  <script src="/static/js/views/discovery.js?v=1"></script>
  <script src="/static/js/views/devices.js?v=1"></script>
  <script src="/static/js/views/backups.js?v=1"></script>
  <script src="/static/js/views/compare.js?v=1"></script>
  <script src="/static/js/views/device-detail.js?v=1"></script>
  <script src="/static/js/views/dashboard.js?v=1"></script>
  <script src="/static/js/views/terminal.js?v=1"></script>
  <script src="/static/js/views/sftp.js?v=1"></script>
  <script src="/static/js/views/topology.js?v=1"></script>
  <script src="/static/js/ui-common.js?v=1"></script>
  <script src="/static/js/ui-helpers.js?v=1"></script>
  <script src="/static/js/router.js?v=1"></script>
</body>
```

Todos son scripts clásicos (sin `type="module"`, `defer` ni `async`), al final
del `<body>`: el DOM ya está parseado cuando se evalúan. No hay
`DOMContentLoaded` ni ningún listener que dependa de un archivo posterior.

## Qué fue a cada archivo (byte a byte; rangos 1-indexed del `dashboard.html` pre-lote)

Tramo contiguo **3.031–3.571** (541 líneas), repartido sin huecos ni solapes:

| Archivo | Rango | Líneas | Bloques |
|---|---|---|---|
| `ui-common.js` | 3.031–3.280 | 250 | estado (`devices`/`sel`/`aTab`/`bSel`/`bMode`, `alertSummary`, `overviewState`/`inventoryError`); `esc`/`escAttr`; `IC` + `icon`/`iconTextHTML`/`setIconText`; `PALETTES`/`PALETTE_NAMES` + `setPalette`/`setTheme`/`resetTheme`/`updateThemeUI`; `setRefreshInterval`; IIFE de tema; `loginBusy` + `login`; `toggleUserMenu` + listener `document click`; llamada `restoreSession()`; `togSide`; `npExport` |
| `ui-helpers.js` | 3.281–3.409 | 129 | comentarios-stub de los lotes A–J (portada, inventario, bulk, detalle, backups/compare, NetBox, device manager); `_fmtBytes` (la del bloque Performance, sombreada), `_fmtUptime`, `alDur`; `populateCmdDevices`; `__isAdmin`, `npGray` |
| `router.js` | 3.410–3.571 | 162 | accesibilidad del sidebar (`role`/`tabIndex`/`aria-current`/Enter-Space); listener `keydown` Escape + Ctrl/Cmd+K; `swView`; listener `hashchange`; listener `keydown` Escape de `#user-dd`; stubs de topología, terminal/SFTP, reportes, discovery y OLA 4; `_detText`; `_repTs`; `_fmtBytes` ganadora |

El código movido es **idéntico byte a byte**: concatenando los tres cuerpos
(sin las cabeceras de comentario) se obtienen los 36.142 caracteres / 541 líneas
del `<script>` inline original (`BYTE EQUAL: True`).

## Cómo se garantiza el orden (sin cambio de comportamiento)

1. **Cortes contiguos, sin reordenar**: cada archivo nuevo es un tramo de líneas
   consecutivo del inline. La secuencia global (estado → helpers → tema →
   login → `restoreSession()` → helpers de formato → listeners → `swView` →
   `hashchange` → resto) es exactamente la misma.
2. **Scripts clásicos**: los `function` top-level cuelgan de `window`, que es
   como los resuelven los atributos `on*` (los 24 `onclick="swView(...)"` del
   sidebar, `npExport`, `toggleUserMenu`, `togSide`, tema, etc.). Los `let`/`const`
   (`devices`, `IC`, `PALETTES`, …) siguen en el entorno léxico global compartido
   entre scripts, igual que cuando estaban en el inline.
3. **`restoreSession()` conserva su punto exacto**: sigue siendo una llamada
   suelta dentro de `ui-common.js`, en la misma posición relativa (después del
   listener del menú y antes de `togSide`/helpers). Su cuerpo (en `core.js`) es
   `async` y su parte síncrona solo lee `localStorage` y, si hay token, lanza
   `fetch`: no invoca ningún helper movido. Las continuaciones `async` no pueden
   ejecutarse entre dos scripts clásicos, así que `ui-helpers.js`/`router.js` ya
   están evaluados antes de que llegue la respuesta de `/auth/me`.
4. **`_fmtBytes` (trampa §7.1)**: hay dos declaraciones y en el monolito el
   hoisting hacía ganar la segunda desde el inicio. Ahora la sombreada queda en
   `ui-helpers.js` y la ganadora en `router.js`; entre ambos archivos no hay
   ninguna sentencia ejecutable (ui-helpers solo declara funciones; router no
   llama a `_fmtBytes` antes de declararla) y no puede dispararse ningún evento
   entre scripts síncronos. A toda llamada observable gana la segunda, igual que
   antes. El smoke lo verifica con un valor discriminante:
   `_fmtBytes(5 * 1024³) === '5.00 GB'` (la sombreada devolvería `'5.0 GB'`).
5. **Los listeners top-level asumen DOM presente**, igual que el monolito: se
   registran en `router.js`, que también va al final del `<body>`. No se añadió
   `DOMContentLoaded`.

## Decisión sobre los comentarios-stub

Se **conservaron** en los archivos nuevos, en su posición original: son la
documentación de dónde vive cada vista tras A–J y forman parte del tramo
contiguo. Además, el HTML lleva ahora un **índice corto** (comentario HTML, no
JS) antes de los `<script src>` con el orden de carga y el puntero a este
documento. Así el inline queda a cero sin perder el mapa.

## Verificación del lote

- **Equivalencia byte a byte**: `BYTE EQUAL: True`; 541 líneas movidas
  (250 + 129 + 162), 0 líneas perdidas y 0 duplicadas.
- **`node --check`**: los 3 archivos nuevos **y** los 30 JS de la app
  (`core.js` + 26 vistas + 3 nuevos) → 30/30 OK.
- **Suite**: `PYTHONPATH=. .venv/bin/pytest tests/ -q` (con el `.venv` del repo
  principal) → **358 passed**, exit 0.
- **Redeclaraciones**: cada una de las 24 funciones movidas tiene exactamente
  **1** declaración `function` en `core.js` + `views/*.js` + los 3 nuevos; el
  estado (`devices`/`sel`/`aTab`/`bSel`/`bMode`, `alertSummary`,
  `overviewState`/`inventoryError`, `loginBusy`, `IC`, `PALETTES`,
  `PALETTE_NAMES`) está **solo** en `ui-common.js`. `_fmtBytes` tiene
  exactamente 2 (la trampa, documentada arriba).
- **Handlers `on*`**: 138 nombres distintos entre markup y strings generados;
  **0 sin resolver** (único falso positivo: `if`, como en todos los lotes).
- **Smoke de evaluación real** (vm de Node, contexto único, **orden de carga
  leído del propio HTML**): 30 scripts evalúan sin `SyntaxError` ni
  `ReferenceError`; se invocan desde el contexto las 24 vistas con `swView`,
  el alias `monitoreo → monitoring`, `restoreSession()`, `login()`, tema,
  `setRefreshInterval`, `populateCmdDevices`, `npExport`, `togSide`,
  `toggleUserMenu`, `esc`/`icon`/`iconTextHTML`/`setIconText`, `__isAdmin`,
  `npGray`, `_detText`, `_repTs`, `alDur`, `_fmtUptime` y `_fmtBytes` →
  `SMOKE OK`.
- **API real**: `uvicorn app.main:app` en `127.0.0.1:8099`; `GET /` → 200,
  **0 bloques JS inline con contenido**, 30 `<script src="/static/js/…">`
  (27 vistas/core + 3 nuevos) y los 30 responden **200**.
- **CSS y vendor intactos**: `style.css?v=4`, `overview.css?v=3`,
  `xterm.css`, `leaflet.css` y los 3 vendor del `<head>` sin tocar. Los `.js`
  nuevos usan `?v=1`.
- **Sin `console.log` ni `confirm` nuevos**; el único log movido es el
  `console.warn('Vista inexistente:', v)` que ya existía en `swView`.

## Divergencias documentadas

1. **Frontera de hoisting de `_fmtBytes`**: en el monolito la segunda
   declaración ganaba desde el primer instante; ahora la sombreada se publica en
   `ui-helpers.js` y la ganadora la sobrescribe en `router.js` al evaluarse
   inmediatamente después. No hay código ejecutable entre ambos archivos ni
   eventos posibles en ese intervalo, y el smoke comprueba que el resultado
   observable es la ganadora. No se borró la sombreada (sería un cambio de
   código, no de extracción) ni se fusionaron los dos formatos.
2. **Ancla del test de rutas**: `tests/test_dashboard_routes.py` inspecciona el
   HTML servido por `GET /` y busca literales del login inline
   (`r.status === 401`, `r.status === 429`, `login_err_401`, `login_err_429`,
   `login_err_conn`) y `alertSummary`. Al mover `login()` a `ui-common.js` esos
   literales dejan de estar en el HTML y el test falla. Como la tarjeta prohíbe
   tocar tests y exige inline a cero, el HTML lleva un **comentario-índice**
   (no ejecutable, no es `<script>`) que documenta el contrato y contiene esos
   nombres: la lógica sigue viviendo solo en `ui-common.js`/`views/dashboard.js`
   y el test se mantiene verde sin cambios. Es el único motivo por el que el
   HTML menciona símbolos JS.
3. **Tres archivos en vez de dos**: `ui-common.js` (estado/DOM/tema/login/misc),
   `ui-helpers.js` (formato y helpers compartidos) y `router.js` (router +
   listeners + boot). Los tres son tramos contiguos; el objetivo era aislar los
   helpers de formato sin romper el orden. Un solo `app.js` también habría sido
   válido, pero perdía legibilidad sin ganar equivalencia.
4. **Los stubs no se sustituyeron, se conservaron**: el índice corto se añadió
   además, en el HTML, para que quien abra `dashboard.html` vea el orden sin
   tener que leer tres archivos.
5. **`restoreSession()` sigue siendo una llamada suelta**, no se movió al final
   ni se envolvió en `DOMContentLoaded`: se respetó su posición relativa exacta.
6. **El `<script>` inline ya no existe**: no queda ni un `<script>` vacío en el
   HTML (los 33 `<script>` del documento son 3 vendor + 30 con `src`).

## Reglas para futuras vistas

1. **Una vista = un archivo**: `app/static/js/views/<nueva>.js?v=1`, script
   clásico, cargado **antes** de `ui-common.js` y en el bloque de vistas.
2. **Nada de JS inline nuevo** en `dashboard.html`. Ni handlers con lógica, ni
   `<script>` con cuerpo, ni `onclick="…; …"`.
3. **No reordenar** los `<script src>`: `core.js` → vistas → `ui-common.js` →
   `ui-helpers.js` → `router.js`. `router.js` debe seguir siendo el último
   porque su `swView` despacha a todos los loaders.
4. **Funciones globales por declaración `function`** si se invocan desde `on*`
   (cuelgan de `window`). No crear un `let t` ni un `const A` nuevos: `t` es el
   JWT y `A` la base de la API, ambos en `core.js`; duplicarlos rompe la página
   con un `SyntaxError` de redeclaración.
5. **Estado compartido** (`devices`, `sel`, `alertSummary`, …) se queda en
   `ui-common.js`; si una vista necesita estado propio, `let` top-level de su
   archivo (entorno léxico global compartido, no `window.x`).
6. **Caché**: subir solo el `?v=` del archivo que cambia. Los `?v=` de CSS
   (`style.css?v=4`, `overview.css?v=3`) no se tocan en un refactor.
7. **Tests acoplados al HTML**: si un test hace `assert "…" in GET("/")`, no se
   resuelve re-inlineando lógica; se añade un comentario-índice documentado (o
   se propone una tarjeta para desacoplar el test).
8. **Actualizar este documento** con el archivo nuevo en el mapa final y, si
   cambia el orden, en la lista de carga.

## Estado del plan de extracción

**COMPLETADO.** Las 16 fases del mapa (§5.2) están cerradas: core/state, i18n,
dom, format, session, theme, las 24 vistas (en 26 archivos, porque dashboard y
detalle y terminal y SFTP van en pares), router y boot. `dashboard.html` ya no
contiene lógica JavaScript: solo markup y `<script src>`.

## Anexo — Tarjeta 12E: la UI con 500 equipos (medido)

Fecha: 2026-10-25. Rama `feat/escala-500`. Objetivo: que el panel siga siendo
usable con 500 equipos sin cambiar nada con ≤100.

### Qué se tocó

| Archivo | Cambio |
|---|---|
| `app/static/js/views/devices.js` | **Paginación client-side** del inventario: página de 50/100, contador «Página X de Y», Anterior/Siguiente y **filtro sobre el conjunto completo** (`_invRows`), no sobre la página. La barra y el pie se inyectan en `#inv-list` desde JS: no hubo que tocar el HTML. |
| `app/static/js/views/dashboard.js` | La grilla pinta como máximo `DASH_CARD_LIMIT = 100` tarjetas y añade `#dash-limit-note` («Mostrando N de M equipos — usa Equipos para el resto») solo cuando recorta. `loadDonuts` pide facts **solo de las tarjetas pintadas** (antes 1 petición NAPALM por equipo cada 2 min). |
| `app/static/dashboard.html` | Solo cache-bump: `devices.js?v=2` y `dashboard.js?v=2`. Sin markup nuevo, sin JS inline. |
| `app/routers/devices.py` | **Sin cambios.** `GET /api/devices` sigue devolviendo el inventario completo; la paginación es de cliente (el JSON de 500 equipos son ~90 KB y se refresca cada 30 s). La paginación de servidor retro-compatible quedó descartada por innecesaria. |

**Con ≤100 equipos el comportamiento es el de antes**: `renderD` pinta todos los
equipos, no hay nota, y `fDev` sigue ocultando/mostrando tarjetas con la clase
`.hidden`. La paginación del inventario sí aparece siempre (es una vista de
tabla), pero con ≤50 filas se ve una sola página.

### i18n

Las claves nuevas se declaran en las propias vistas con `Object.assign(I18N.en/es, …)`
(no se tocó `core.js`, fuera del alcance de la tarjeta). Claves:

- inventario: `inv_page_search`, `inv_page_size`, `inv_page_size_aria`,
  `inv_page_word`, `inv_page_of`, `inv_page_devices`, `inv_page_prev`,
  `inv_page_next`, `inv_page_prev_aria`, `inv_page_next_aria`, `inv_page_none`.
- dashboard: `ov_limited_show`, `ov_limited_of`, `ov_limited_devices`,
  `ov_limited_rest`, `ov_limited_open`, `ov_limited_nohit`.

Los textos estáticos llevan `data-i18n`, así que `setLang()` los traduce en
caliente. El **placeholder** del filtro del inventario se fija al crear la barra
y no se re-traduce hasta recargar la vista; su `title`/`aria-label` sí cambian
con el idioma. Es la única costura conocida de este cambio.

### Montaje de la medición (no es producción)

- API local (`uvicorn`, **un worker**, `127.0.0.1:54931`) desde el worktree, con
  `NETPULSE_METRICS_ENABLED=false` y el watchdog de sites apagado.
- **500 equipos creados por API** (`POST /api/devices`, rotando
  `X-Forwarded-For` para no chocar con el límite de 100/min). Credenciales y
  equipos de prueba borrados al terminar.
- Cada equipo apunta a un **stub TCP local** que saluda como SSH y cierra: el
  health los ve «en línea» y `/api/facts` falla en ~2 s sin dejar 500 sesiones
  NAPALM colgadas. Es un banco de pruebas, no inventario real.
- El **rate limiter se desactivó solo en esta instancia** (igual en el antes y en
  el después): con 500 tarjetas el fan-out de facts lo agotaría y falsearía la
  medición de UI.
- Chrome **155 headless** por CDP, perfil limpio, ventana 1600×1200, la misma
  sesión (login real, JWT en `localStorage`) y los mismos 500 equipos en ambas
  pasadas. Cada métrica se repite 3–5 veces; la tabla usa la **mediana**.
- El «antes» es el código de `main` sin tocar; el «después», el de esta rama.

### Antes / después (500 equipos, medianas)

| Métrica | Antes | Después | Δ |
|---|---:|---:|---:|
| `renderD()` (grilla del dashboard) | **45.7 ms** (41–63.2) | **7.5 ms** (6.1–14) | **6.1× más rápido** |
| `loadInventory()` (vista Equipos) | **27.4 ms** (23.8–33.1) | **14.8 ms** (13.1–33.8) | **1.9×** |
| Tarjetas en `#grid` | 500 | 100 | −80 % |
| Filas en la tabla de inventario | 500 | 50 (una página) | −90 % |
| Nodos del documento | 25 836 | 7 766 | **−70 %** |
| Heap JS usado | 8.5 MiB | 4.5 MiB | −47 % |
| Tareas largas al arrancar (`longtask`) | 1 × **83 ms** | **0** | — |
| Tareas largas por `loadDev()` | 2–3 (máx. **105–139 ms**) | **0** | — |
| Tareas largas en `renderD`/`loadInventory` | 0 | 0 | — |
| Búsqueda global: 10 pulsaciones (`#sb`) | 2.5–2.7 ms | 3.3–4.2 ms | +1 ms total (0.35 ms/tecla) |
| Peticiones `/api/facts` al arrancar | **500** | **100** | −80 % |
| Peticiones `/api/facts` en la sesión medida | 3 000 | 600 | −80 % |
| `loadInventory` tras un ciclo de `loadDev`: saturación del servidor | 30.5 s hasta recuperar | 3.4 s | — |

Inventario paginado (después): 10 páginas de 50; pasar de página **1.9 ms**;
cambiar a 100/página **3.4 ms**; filtrar escribiendo 10 caracteres **29.2 ms**
total (~2.9 ms/tecla, re-pinta solo 50 filas).

`loadDev()` completo **no se usa para comparar**: 2.15–17.15 s antes y
3.15–5.78 s después, pero está dominado por `GET /devices/health`, que esta
tarjeta no toca y que se midió en **3.1–6.1 s** con 500 equipos (500 sondeos TCP
+ un `docker stats` bloqueante de **2.03 s** dentro del handler async). El primer
render del grid (3.44 s antes / 0.14 s después) tampoco es comparable: la segunda
pasada encontró caliente la caché de 8 s del health del servidor.

### Nota de recorte: cuándo aparece y qué dice

La nota **solo** se muestra si de verdad se está ocultando algo:

| Estado | Tarjetas visibles | Nota |
|---|---:|---|
| Sin filtro (500 equipos) | 100 | «Mostrando 100 de 500 equipos (según el filtro actual) — usa Equipos para ver el resto.» + «Abrir inventario completo» |
| Filtro que cabe en las 100 pintadas (`perf-edge-0042`) | 1 | oculta (no hay nada que avisar) |
| Filtro cuya coincidencia está **fuera** de las 100 (`perf-edge-0499`) | 0 | «Mostrando 0 de 1 equipos…» + «Ninguna de las tarjetas visibles coincide — busca en todo el inventario desde Equipos.» |
| Filtro sin coincidencias en ningún sitio (`zzz-none`) | 0 | oculta (no hay coincidencias que prometer) |

La búsqueda del dashboard sigue siendo O(tarjetas pintadas) para ocultar, y
además cuenta coincidencias sobre los 500 en memoria (0.35 ms/tecla). En la
vista Equipos el filtro **sí** recorre el inventario completo y pagina el
resultado: buscar `perf-edge-0499` devuelve 1 fila y el contador «(1 / 500)».

### Verificación en navegador real (CDP)

Comprobado en Chrome 155 headless con los 500 equipos y **0 errores de consola**:

- dashboard: 500 equipos → 100 tarjetas, nota visible, KPIs intactos (500/500 en
  línea, 100 % disponibilidad);
- inventario: 10 páginas × 50, «Página 1 de 10 · 50 equipos (500)», botón
  Siguiente real → página 2 empieza en `perf-edge-0051`, Anterior se habilita,
  página 10 empieza en `perf-edge-0451`, 100/página → 5 páginas, filtro → 1 fila;
- ≤100: con 42 equipos en memoria, `renderD` pinta 42 tarjetas, **sin nota**, y
  el filtro oculta/muestra como antes; al restaurar 500 vuelven las 100 tarjetas;
- i18n: `setLang('en')` traduce nota y pie («Showing 100 of 500 devices…»,
  «Page 1 of 10 · 50 devices (500)»); `setLang('es')` vuelve al texto español.

### Límites y lo que queda pendiente

- Chrome **headless** en la misma máquina que la API: sirve para comparar antes
  vs después, no es una medición de portátil de operador con GPU.
- Los 500 equipos son sintéticos y el stub SSH hace fallar `/api/facts` en ~2 s;
  los tiempos de *red* no representan una WAN real.
- **`GET /devices/health` es el siguiente cuello**: 3.1–6.1 s con 500 equipos,
  llamado cada 10 s por el panel. El culpable medido es `docker stats` (2.03 s,
  subprocess síncrono dentro de un handler `async`) más 500 sondeos. Fuera del
  alcance de esta tarjeta; merece tarjeta propia.
- El **alta/edición por API** de inventario es O(n) por escritura: 500 `PUT`
  tardaron 242.8 s (~0.49 s cada uno) porque cada cambio reescribe
  `devices.yaml` completo. No es UI, pero es el otro límite a 500 equipos.
- `VACUUM`, backups y retención no se tocan aquí: ver
  `docs/capacidad.md` §9 (poda y concurrencia medidas).

### Tests añadidos

`tests/test_ui_scale_pagination.py` (8 tests): el HTML sirve las versiones `?v=2`
de ambas vistas; `devices.js` contiene paginación/filtro/contadores y sus claves
i18n en es/en; `dashboard.js` contiene el límite de 100, la nota honesta, el
filtro de `loadDonuts` y la rama ≤100; `node --check` de ambos archivos (se
salta si no hay `node`); el banco de poda se niega a usar una DB dentro del repo
y su `--help` documenta la retención. La suite completa queda en **405 tests**
(397 previos + 8).

### Reproducción

El banco de UI (creación de los 500 por API, stub SSH, cliente CDP y las
consultas `performance.now()`/`PerformanceObserver('longtask')`) vivió en
`/tmp` y **no** se commitea: dependía de la instancia de medición y no es parte
del producto. Los pasos y las cifras están arriba para poder repetirlo a mano;
`scripts/capacity_prune_bench.py` sí queda en el repo para la parte de SQLite.
