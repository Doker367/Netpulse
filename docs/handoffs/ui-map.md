# Mapa del monolito UI — `app/static/dashboard.html`

**Rama:** `feat/ui-map` (base `main`) · **Fecha:** 2026-10-24 · **Naturaleza:** documento de solo lectura.
**Objetivo:** inventario previo al refactor que dividirá el panel en módulos **sin cambiar comportamiento**. Este archivo es la referencia de los agentes de extracción; no sustituye a los contratos de `docs/handoffs/*.md`, solo los sitúa en el código.

Rutas citadas: todo `L###` se refiere a `app/static/dashboard.html` (9.830 líneas).

---

## 0. Cifras de partida

| Métrica | Valor | Cómo se midió |
|---|---|---|
| Líneas totales | 9.830 | `wc -l` |
| CSS inline | 1.647 (16,8 %) | `<style>` L13–L1504 + segundo `<style>` L1529–L1683 |
| HTML (markup) | 1.359 (13,8 %) | resto fuera de `<style>` y `<script>` |
| JS inline | 6.824 (69,4 %) | `<script>` L3003–L9826 |
| Diccionario i18n | 613 líneas (L3016–L3629) | 559 claves únicas, 127 atributos `data-i18n` |
| Funciones | 308 declaraciones = 302 de nivel superior + 6 anidadas | `function` con 4 espacios de sangría = global |
| Nombres duplicados | 1 (`_fmtBytes`, L5064 y L9431) | el segundo gana por hoisting |
| Vistas | **24** | `div.view#v-*` L1927–L2978 |
| Handlers inline | 230 atributos (132 en markup + 98 generados en strings JS) | `onclick/onchange/oninput/onsubmit/onkeydown/on*drag*` |
| Funciones invocadas desde inline | **146** (78 estáticas + 69 generadas, 1 solapada) | resolución contra `window` → deben seguir siendo globales |
| Accesos DOM | 402 `getElementById` · 52 `querySelector*` · 126 `fetch(` | recuento textual |
| Endpoints distintos | ~60 bajo `/api` | prefijo común `A` L3004–L3006 |

Reparto del archivo:

```
L1–L1511      <head>: CSS L13–L1504, vendor L1505–L1510
L1513–L1685   login screen (L1515) + <style> del login L1529–L1683
L1686–L1876   sidebar + overlay + header
L1926–L2978   .container con las 24 vistas
L2981–L2999   modal de detalle de equipo (#dp), FUERA de las vistas
L3003–L9826   único <script> clásico (no module), al final del body
```

---

## 1. Inventario de vistas

Las 24 vistas son `div.view` dentro de `.container`, todas ocultas salvo `#v-dashboard` (`class="view active"` L1927). La navegación es `swView(nombre)` (L6894), que activa `#v-<nombre>` y dispara el loader correspondiente (L6911–L6945). El sidebar tiene exactamente un `.sidebar-item[onclick="swView('…')"]` por vista (L1696–L1856).

| # | Vista | id | Markup | Disparador de carga | Funciones de carga | Endpoints | DOM principal (estático) |
|---|---|---|---|---|---|---|---|
| 1 | Panel / Overview | `v-dashboard` | L1927–L1971 (+ modal L2981–L2999) | `init()` L4507 al entrar en sesión; latidos `_npTick` | `loadDev` L4524 (30 s), `loadH` L4608 (10 s), `refreshAlertSummary` L4488 (45 s), `loadDonuts` L4443 (1,5 s + 120 s), `renderD` L4765, `updStats` L4879, `updateDashboardHero` L4831 | `GET /devices`, `/devices/health`, `/alerts/active`, `/facts/{id}/resources`, `/config/{id}/raw` (desde `selD`), `/command/{id}` | `ops-state`, `ops-description`, `ops-primary`, `ops-focus-title`, `ops-focus-count`, `ops-focus-items`, `stats`, `grid`, `bulk-bar`, `bulk-t`, `bulk-tags`, `bulk-panel`, `bulk-r`, `ops-devices-heading`, `ops-inventory-note`; modal: `dp`, `dt`, `dm`, `qa`, `tabs`, `dc`; dinámicos `tc-*`, `c-big`, `t-big`, `ping-result`, `live-hw`, `r{cpu,mem,dsk}-{id}`, `tpl-*` |
| 2 | Commands | `v-commands` | L1972–L2042 | `swView` → `populateCmdDevices` L5703; ejecución manual | `execGlobalCmd` L5707, `runQuickCmd` L5729 | `POST /command/{id}` | `cmd-dev`, `cmd-quick-list`, `cmd-input`, `cmd-status`, `cmd-output` |
| 3 | Terminal + SFTP | `v-terminal` | L2043–L2180 | `swView` → `termPopulateDevices` L7727; conexión manual | `termConnect` L7942, `_termOpenWs` L7962, `_termCreateSession` L7839, `switchTermTab` L7694, `sftpLoadDir` L8588 | `WS /ws/terminal/{device}?token&fmt=json`, `GET /api/sftp/…` | 35 ids: `term-dev`, `term-status-badge/dot/text`, `term-pane-terminal`, `term-tabs`, `term-info-host`, `term-connect`, `term-disconnect`, `term-search`, `term-wrapper`, `term-container`, `term-search-panel`, `tab-btn-terminal`, `tab-btn-sftp`; SFTP: `sftp-banner*`, `sftp-progress*`, `sftp-path-input`, `sftp-file-input`, `sftp-upload-btn`, `sftp-breadcrumb`, `sftp-dropzone`, `sftp-file-list`, `sftp-summary`, `sftp-refresh-btn`; dinámicos `term-session-{n}`, `term-xterm-{n}`, `term-banner-*` |
| 4 | Backups | `v-backups` | L2181–L2233 | `swView` → `loadBk` L6037 + `buildSchedForms` L4600 + `loadJobs` L6102; latido 60 s | `loadBk`, `loadJobs`, `doBackupMulti` L6118, `scheduleBackup` L6132, `scheduleRecurring` L6133 | `GET /config/{id}/backups`, `POST /config/{id}/backup`, `/scheduler/jobs` | `bk-kpis`, `bk-list`, `sched-single-form`, `sched-rec-form`, `jobs-list`; badge lateral `sb-bk`, `info-bk`; dinámicos `sched-dev`, `sched-delay*`, `sched-rec-*` |
| 5 | Compliance | `v-compliance` | L2234–L2244 | `swView` | `loadCompliance` L6187, `checkCompliance` L6256, `setBaseline` L6241, `loadCompReport` L6284 | `GET/POST /compliance/*`, `/groups` | `compliance-content`; dinámicos `cmp-bl-group`, `cmp-bl-groups`, `cmp-bl-config`, `cmp-result` |
| 6 | Webhooks | `v-webhooks` | L2245–L2255 | `swView` | `loadWebhooks` L6584, `addWebhook` L6608, `deleteWebhook` L6614 | `/notifications/webhooks` | `webhooks-content`; dinámicos `wh-url`, `wh-event` |
| 7 | NetBox | `v-netbox` | L2256–L2266 | `swView` | `loadNetbox` L6391, `nbLoadSites` L6462, `nbSyncRun` L6559 | `GET /netbox/status`, `POST /netbox/sync`, `/netbox/sites` | `netbox-content`; dinámicos `nb-panel`, `nb-result`, `nb-sites`, `nb-sites-body` |
| 8 | Connectivity | `v-connectivity` | L2267–L2275 | `swView` | `loadConn` L4079, `_pingCard` L4067 | `/system/devices`, `/system/ping` | `conn-grid`; dinámico `conn-devices` |
| 9 | Groups | `v-groups` | L2276–L2286 | `swView` | `loadGroups` L6318, `grpDevs` L6350, `createGroup` L6364 | `GET/POST /groups`, `GET /groups/{g}/devices` | `groups-content`; dinámicos `grp-name`, `grp-desc`, `grp-devs` |
| 10 | Sites | `v-sites` | L2287–L2297 | `swView` | `loadSites` L9452, `siteToggle` L9523, `siteHist` L9569 | `/remote/sites`, `/remote/sites/{id}/devices`, `…/history?hours=6` | `sites-content`; dinámicos `sites-hist-{id}`, `sh-chart-{dev}` |
| 11 | Theme | `v-theme` | L2298–L2337 | `swView` | `updateThemeUI` L3878; acciones `setPalette` L3849, `setTheme` L3864, `resetTheme` L3872, `setRefreshInterval` L3894 | — (solo `localStorage`) | `palette-grid`, `theme-grid`, `theme-preview`, `theme-name`, `theme-hex`, `refresh-interval`, `refresh-current` |
| 12 | Performance / Equipment | `v-performance` | L2338–L2349 | `swView` | `loadPerformance` L5083 (+ `_fmtBytes` L9431 — hay otra declaración sombreada en L5064 —, `_fmtUptime` L5071, `_donut` L5078) | `/system/devices`, `/ops/health/all`, `/facts/{id}/resources` | `perf-kpis`, `perf-grid` |
| 13 | Alerts | `v-alerts` | L2350–L2416 | `swView` (3 loaders) | `loadAlerts` L5205, `loadAlertSilences` L5337, `loadAlertRules` L5479 + `al*` (L5323–L5541) | `/alerts/active`, `/system/overview`, `/system/devices`, `/alerts/admin/silences`, `/alerts/admin/rules` | `alert-kpis`, `alert-list`, `alerts-admin`, `sil-form`, `sil-dev`, `sil-group`, `sil-mrows`, `sil-dur`, `sil-comment`, `sil-form-msg`, `sil-list`, `rules-list`; dinámicos `sil-confirm` |
| 14 | Logs | `v-logs` | L2417–L2482 | `swView` → `loadLogs`; cambio de fuente | `loadLogs` L5639, `switchLogSource` L5542, `loadLokiLabels` L5561, `loadLokiLogs` L5588 | `/audit`, `/audit/stats`, `/logs/labels`, `/logs/query` | `log-source`, `log-filter-action`, `log-limit`, `log-stats`, `log-terminal`, `log-count`, `log-body`, `loki-controls`, `loki-container`, `loki-hours`, `loki-search` |
| 15 | Reports SLA | `v-reports` | L2483–L2493 | `swView` | `loadReports` L8881, `repApply` L9005, `repRender` L9039 (+ L8848–L9120) | `POST /reports/availability`, `GET /reports/availability/cached`, `/groups` | `rep-content`; dinámicos `rep-desde`, `rep-hasta`, `rep-group`, `rep-devs`, `rep-results` |
| 16 | Devices / Inventory | `v-devices` | L2494–L2686 | `swView` → `loadInventory` L6834 | `loadInventory`, `addInventoryDevice` L6729, `invRefreshGroups` L6672, `invPreloadGroup` L6709 + wizard `dw*` L9129–L9427 | `GET /devices`, `/groups`, `/discovery/candidates`, `/discovery/scan`, `/discovery/import` | 24 ids `inv-*` (`inv-id`, `inv-host`, `inv-driver`, `inv-group`, `inv-group-text`, `inv-protocol`, `inv-auth-ssh`, `inv-user`, `inv-pass`, `inv-secret`, `inv-port`, `inv-auth-snmp`, `inv-snmp-ro`, `inv-cli-*`, `inv-netmiko-type`, `inv-list`), `dw-panel`; dinámicos `dw-src`, `dw-user`, `dw-pass`, `dw-group`, `dw-tipo`, `dw-driver` |
| 17 | Retención | `v-retention` | L2687–L2697 | `swView` | `loadRetention` L9605, `retPruneDry` L9672, `retPruneConfirm` L9706, `retPruneExec` L9716 | `/retention/backups`, `/retention/inventory`, `POST /retention/audit-prune` | `ret-content`; dinámicos `ret-prune-box`, `ret-days`, `ret-dry-btn`, `ret-prune-out` |
| 18 | Admin usuarios | `v-admin` | L2698–L2709 | `swView` | `loadUsers` L6140, `createUser` L6174, `delUser` L6181 + scopes `userScope*` L9737–L9825 | `/admin/users`, `PUT /admin/users/{u}`, `/groups` | `admin-content`; dinámicos `new-u`, `new-p`, `new-n`, `new-r`, `sc-panel-{u}`, `sc-g-{u}`, `sc-out-{u}` |
| 19 | Compare | `v-compare` | L2710–L2731 | `swView` → `bdPopulateDevices` L5996 | `dCmp` L5963, `bdLoadBackups` L6005, `bdDiff` L6019 | `/config/{id}/raw`, `/config/{id}/backups` | `cmp-a`, `cmp-b`, `cmp-r`, `bd-dev`, `bd-a`, `bd-b`, `bd-stats`, `bd-r` |
| 20 | Topology | `v-topology` | L2732–L2788 | `swView` | `loadTopology` L7086, `topoTab` L7056, `startTopoGraph` L7192, `ensureTopoMap` L7392, `runTopoTrace` L7459 | `/topology/map`, `POST /topology/traceroute` | `topo-tabs`, `topo-tab-*`, `topo-trace-origin`, `topo-trace-dest`, `topo-trace-btn`, `topo-trace-msg`, `topo-stats`, `topo-view-*`, `topo-canvas`, `topo-map`, `topo-map-legend`, `topo-nodes`, `topo-links`, `topo-links-list`, `topo-trace-result` |
| 21 | Monitoring | `v-monitoring` | L2789–L2847 | `swView` → `loadMonitoring` + `refMon`; auto-refresh 15 s | `loadMonitoring` L6976, `refMon` L4245, `queryProm` L4129, `_nativeChart` L4160, `renderMonSpark` L6962 | `/monitoring/overview`, `/monitoring/series`, `/alerts/active`, `/system/prometheus/query*` | `mon-grafana-link`, `mon-services`, `mon-cards`, `mon-chart-cpu/mem/disk`, `mon-range`, `mon-interval`, `mon-grid`, `mon-charts`, `mon-targets`, `mon-grafana`; dinámicos `nm-*`, `tc-<label>` (charts) |
| 22 | Maintenance | `v-maintenance` | L2848–L2869 | `swView` | `loadMaint` L7569, `createMaint` L7586, `delMaint` L7602 | `/ops/maintenance` | `maint-form`, `maint-dev`, `maint-start`, `maint-end`, `maint-reason`, `maint-list` |
| 23 | API Keys | `v-apikeys` | L2870–L2895 | `swView` | `loadKeys` L7611, `createKey` L7623, `revokeKey` L7637 | `/security/api-keys` | `key-form`, `key-name`, `key-role`, `key-created`, `key-list` |
| 24 | 2FA / TOTP | `v-2fa` | L2896–L2978 | ninguna al entrar | `setup2FA` L7645, `verify2FA` L7657 | `POST /security/2fa/setup`, `POST /security/2fa/verify` | `2fa-result`, `2fa-secret`, `2fa-code`, `2fa-result-msg` |

Notas transversales de las vistas:

- **Sin loader al entrar:** `v-dashboard` (lo alimentan los latidos de `init`), `v-2fa` (solo botones) y `v-theme` (solo `updateThemeUI`).
- **Vista fusionada:** `swView('monitoreo')` se redirige a `monitoring` (L6896) por compatibilidad de hash.
- **Modal fuera de las vistas:** `#dp` (L2981) es el detalle de equipo y lo usa el grid del dashboard y la tabla de `v-devices` (`loadInventory` emite `onclick="selD('…')"`, L6865).
- **Vistas que escriben en DOM de otra vista:** ver §5 y §7 (es la razón principal de que la extracción no pueda ser puramente mecánica).

---

## 2. Inventario de funciones

308 declaraciones (302 de nivel superior, 6 anidadas: `drawLine` L4292, `drawMulti` L4306, `drawBars` L4335; `step` L7248, `draw` L7281, `loop` L7355). Solo las de nivel superior son globales y, por tanto, visibles desde `onclick`.

### 2.1 Funciones compartidas (2+ vistas o infraestructura)

`n` = referencias textuales en el archivo (incluye la declaración). «Inline» = invocada además desde un atributo `on*` del HTML o de un string generado.

| Firma | Línea | n | Inline | Quién la usa / para qué |
|---|---|---|---|---|
| `__t(k)` | L3660 | 440 | no | i18n de runtime: todas las vistas, `toast`, mensajes de error. Cae a `en` y luego a la clave. |
| `_t(k)` | L7052 | 19 | no | Alias de `__t` sin fallback a `en`; usado en topología y en `renderCmd` (muerto). Redundante con `__t`. |
| `setLang(lang)` | L3630 | 5 | sí (`#lang-select`) | Login, `init`, `selD`, `updStats`. Recorre **todo** `[data-i18n]`, `[data-i18n-title]`, `[data-i18n-aria]` y llama `updateDashboardHero`. |
| `toast(m, ty='info', d=3000)` | L3662 | 109 | no | Feedback global; crea/elimina nodos con `setTimeout`. |
| `h()` | L3663 | 200 | no | Cabeceras HTTP con `Authorization: Bearer ${t}`. Depende del global `t`. |
| `esc(s)` / `escAttr(s)` | L3734 / L3735 | 255 / 48 | `esc` sí (generado) | Escape de todo HTML interpolado. `escAttr` para atributos. |
| `icon(name, cls)` | L3814 | 36 | no | Devuelve SVG desde `IC` (L3738–L3813). |
| `iconTextHTML(value)` / `setIconText(el, value)` | L3817 / L3822 | 4 / 11 | no | Sustituye solo SVGs de la lista blanca `IC`; el resto se escapa. `setIconText` lo usan `toast`, `updB`, `tB`, `termToggleFullscreen`, `runTopoTrace`. |
| `swView(v)` | L6894 | 34 | sí (24 items del sidebar + botones) | Router: activa vista, marca sidebar, escribe `location.hash` (L6950) y **llama a 28 loaders**. Ver §5 y §7. |
| `_npStateKey/_Get/_Set/_Clear(key[, value])` | L3979–L4001 | 3/3/4/2 | no | Estado por usuario `np_u:<user>:<key>`. Usado solo por `rep` (`loadReports` L8928, `repApply` L9031) y `site` (`loadSites` L9473, `siteToggle` L9529/L9532); `_npStateClear` desde `logout` L4006. |
| `window.fetch` (wrapper) | L3670–L3693 | — | no | Interceptor 401 global; exime `/auth/login`, `/auth/refresh`, `/auth/me` y distingue 401 de SFTP por `detail`. |
| `sessionExpired(msg)` | L3695 | 5 | no | `logout()` + aviso único (`_np401Done`). Llamada por el wrapper y `refreshSession`. |
| `_npTick(fn, ms)` | L3703 | 5 | no | `setInterval` que no hace nada sin `t`. Registra 4 latidos en `init`. |
| `scheduleRefresh()` / `stopRefresh()` / `refreshSession()` | L3711 / L3716 / L3719 | 3 / 2 / 2 | no | Renovación silenciosa a los 25 min (`NP_REFRESH_MS` L3710). |
| `login()` | L3939 | 51 | sí (submit del login) | Login manual. |
| `logout()` | L4003 | 6 | sí | Limpia token, estado por usuario, `repState`, `_invGroupsCache` y 3 intervalos de `window`. |
| `enterSession(me, fallbackName)` | L3917 | 3 | no | Punto único de entrada: `login` y `restoreSession`. Llama `init()`. |
| `showLoginError(message)` | L3912 | 8 | no | Login/sesión. |
| `toggleUserMenu(ev)` | L3966 | 2 | sí | Menú de usuario. |
| `togSide()` | L4039 | 6 | sí (3 sitios) | Drawer móvil; también desde `swView` y Escape. |
| `npExport(kind)` | L4051 | 4 | sí | Descarga `GET /api/export/{kind}`. |
| `loadDev()` | L4524 | 11 | sí | **Inventario canónico** (`devices`). Lo llaman `init`, el latido de 30 s, `loadReports`, `dwOpen`, `dwImport`, `addInventoryDevice`, `delDev` y `swView('terminal')` cuando aún no hay inventario. |
| `loadBk()` | L6037 | 7 | sí | Latido 60 s + vista Backups + `qaB` + `backupAllDevices`. |
| `loadInventory()` | L6834 | 6 | sí | Vista Devices; emite `selD(...)` en cada fila (L6865). |
| `renderD()` | L4765 | 11 | no | Grid del dashboard; depende de `sel`, `bSel`, `bMode`, `devices`. |
| `selD(id)` | L4910 | 4 | sí (desde filas de inventario) | Panel de detalle: 4 `fetch`, `setLang`, `setInterval` de 5 s (`window.dLiveT`). |
| `closeD()` | L5061 | 4 | sí | Cierra `#dp` y su intervalo. |
| `setDTab(t)` | L4900 | 3 | sí | Pestañas del detalle. **Parámetro `t` que eclipsa el token.** |
| `updStats()` | L4879 | 8 | no | KPIs + `setLang(currentLang)` en cada pasada (L4896). |
| `updateDashboardHero()` | L4831 | 2 | no | Hero del dashboard; depende de `overviewState` y `alertSummary`. |
| `refreshAlertSummary()` | L4488 | 4 | no | `GET /alerts/active` → badge sidebar + `updStats`. Latido 45 s y `loadMonitoring`. |
| `loadDonuts()` | L4443 | 4 | no | CPU/RAM/disk por tarjeta; 1 `fetch` por equipo. |
| `buildSchedForms()` | L4600 | 3 | no | **Escribe selects de Backups** desde `loadDev` y `swView('backups')`. |
| `populateCmdDevices()` | L5703 | 3 | no | **Escribe `#cmd-dev` de Commands**; lo llama `loadBk` en cada latido. |
| `runQuickCmd(cmd)` | L5729 | 9 | sí | Atajo de Commands. |
| `__isAdmin()` / `npGray(msgHtml)` | L6314 / L6315 | 6 / 30 | no | Rol y banner gris «no verificable». Viven en el bloque de Groups pero los usan Compliance, NetBox, Reports, Retention, Sites y `nbSyncRun`. |
| `_detText(body, fallback)` | L8848 | 22 | no | Extrae `detail/motivo/mensaje/error`; usado por Reports, Retention, Sites, NetBox, Discovery, SFTP y los alias `_det40` L9429 y `retDet` L9670. |
| `_fmtBytes(n)` | **L9431** (gana) / L5064 (sombreada) | 6 | no | Ver §7.1: la segunda declaración anula la primera. |
| `_fmtUptime(s)` | L5071 | 3 | no | Performance y `_repDur`. |
| `_donut(pct,color,size)` | L5078 | 2 | no | Performance y `repRender`. |
| `alDur(s)` | L5323 | 4 | no | Alerts y `_ageHuman`. |
| `_ageHuman(s)` / `_isoLocal(iso)` / `_ymd(x)` / `_repTs(s)` / `_repDur(s)` | L9439 / L9446 / L8860 / L8855 / L8861 | 3 / 7 / 9 / 7 / 2 | no | Helpers de formato de Sites/Reports/Retention; `_isoLocal` lo usan también `retPruneDry` y `dwStep2/3`. |
| `setRefreshInterval(secs)` | L3894 | 3 | sí (`#mon-interval`, `#refresh-interval`) | Recrea `window._rInt`, llama `refMon()` y `loadMonitoring()` si la vista está activa. Está en el bloque Theme pero pertenece a Monitoring. |
| `initDragDrop()` / `saveChartOrder()` / `loadChartOrder()` | L4379 / L4416 / L4420 | 2 / 3 / 2 | no | Monitoring. `refMon` se reasigna para llamarlo (L4439–L4440). |
| `cmdOpenConsole()` / `termOpenConsoleFor(deviceId)` / `switchTermTab(tab)` | L8353 / L7927 / L7694 | 2 / 2 / 5 | `cmdOpenConsole` sí | Puente Commands ↔ Terminal; `switchTermTab` carga SFTP y resetea `_sftpCurrentPath`. |
| `qaB/qaS/qaP/qaM/dlC(id)` + `rCmdBig(id)` | L5758–L5783, L5872, L5045 | 3/2/2/2/3 + 3 | sí (generados en `selD`) | Acciones rápidas del detalle; `qaM` salta a `compare` y llama `dCmp`. |
| `_termActive()` + familia `_term*` | L7692–L8349 | 12–15 | solo `_termCopySel` | Estado de pestañas/WS/heartbeat; nadie fuera del bloque Terminal. |
| `_sftp*` + `sftp*` | L8371–L8838 | 2–12 | `sftp*` públicos (8) | Solo vista Terminal; `_sftpErrorInfo` llama `switchTermTab('terminal')` (L8537). |

### 2.2 Funciones por vista (uso exclusivo)

| Vista / bloque | Funciones (línea) |
|---|---|
| Dashboard | `renderD` L4765, `fDev` L4634, `tB` L4639, `tB2` L4644, `updB` L4648, `bClear` L4654, `bulkPanel` L4657, `bulkClosePanel` L4663, `bulkReset` L4669, `bulkSummary` L4674, `bCmd` L4678, `bCmdRun` L4698, `bPing` L4721, `bFacts` L4745, `goToOverviewDevices` L4812, `showAddDevicePanel` L4818, `updateDashboardHero` L4831, `updStats` L4879 |
| Detalle de equipo (modal) | `setDTab` L4900, `selD` L4910, `closeD` L5061, `qaB` L5758, `qaS` L5759, `qaP` L5767, `qaM` L5783, `dlC` L5872, `rCmdBig` L5045 |
| Commands | `populateCmdDevices` L5703, `execGlobalCmd` L5707, `runQuickCmd` L5729 |
| Backups | `loadBk` L6037, `loadJobs` L6102, `doBackupMulti` L6118, `scheduleBackup` L6132, `scheduleRecurring` L6133, `cancelJob` L6134, `dlBackup` L6089, `offsiteExport` L5987 |
| Compare | `dCmp` L5963, `bdPopulateDevices` L5996, `bdLoadBackups` L6005, `bdDiff` L6019 |
| Compliance | `loadCompliance` L6187, `setBaseline` L6241, `checkCompliance` L6256, `loadCompReport` L6284 |
| Groups | `loadGroups` L6318, `grpDevs` L6350, `createGroup` L6364 |
| NetBox | `loadNetbox` L6391, `nbLoadSites` L6462, `nbSyncConfirm` L6491, `nbSyncCancel` L6502, `nbList` L6507, `nbRenderResult` L6512, `nbFailBanner` L6552, `nbSyncRun` L6559 |
| Webhooks | `loadWebhooks` L6584, `addWebhook` L6608, `deleteWebhook` L6614 |
| Devices / inventario | `_invContainer` L6617, `toggleInvAuth` L6621, `toggleInvCli` L6635, `_invGroupEls` L6649, `_invGroupSetManual` L6657, `invGroupChanged` L6664, `invRefreshGroups` L6672, `invReadGroup` L6701, `invPreloadGroup` L6709, `addInventoryDevice` L6729, `loadInventory` L6834 |
| Discovery wizard | `_dwEstadoText` L9121, `_dwResList` L9125, `dwOpen` L9129, `dwClose` L9147, `dwReset` L9153, `dwRender` L9159, `dwPanelError` L9174, `dwStep1` L9186, `dwToggleAllSources` L9208, `dwScan` L9215, `dwStep2` L9245, `dwToStep3` L9274, `dwStep3` L9277, `dwSel` L9322, `dwSelAll` L9327, `dwToStep4` L9335, `dwStep4` L9338, `dwImport` L9368 |
| Alerts | `loadAlerts` L5205, `alDur` L5323, `alUnavail` L5331, `loadAlertSilences` L5337, `alSilChips` L5354, `alRenderSilences` L5360, `togSilForm` L5391, `silAddMatcher` L5403, `alSilFormMsg` L5415, `alSubmitSilence` L5426, `alSilCancel` L5458, `loadAlertRules` L5479, `alRenderRules` L5496, `alToggleExpr` L5535 |
| Logs | `switchLogSource` L5542, `loadLokiLabels` L5561, `loadLokiLogs` L5588, `loadLogs` L5639 |
| Monitoring | `queryProm` L4129, `queryPromRange` L4137, `_avgByTs` L4145, `_countByTs` L4153, `_nativeChart` L4160, `renderNativeMon` L4186, `refMon` L4245 (+3 anidadas), `initDragDrop` L4379, `saveChartOrder` L4416, `loadChartOrder` L4420, `monNum` L6956, `renderMonSpark` L6962, `loadMonitoring` L6976 |
| Topology | `topoTab` L7056, `buildTraceOrigins` L7075, `loadTopology` L7086, `_topoScreen2World` L7164, `_topoResolveEdges` L7170, `startTopoGraph` L7192 (+`step`/`draw`/`loop`), `ensureTopoMap` L7392, `updateTopoMapDevices` L7405, `redrawTopoTrace` L7430, `runTopoTrace` L7459, `clearTopoTrace` L7496, `renderTraceResult` L7505 |
| Maintenance | `loadMaint` L7569, `showMaintForm` L7585, `createMaint` L7586, `delMaint` L7602 |
| API Keys | `showKeyForm` L7610, `loadKeys` L7611, `createKey` L7623, `revokeKey` L7637 |
| 2FA | `setup2FA` L7645, `verify2FA` L7657 |
| Terminal | `_termActive` L7692, `switchTermTab` L7694, `termPopulateDevices` L7727, `termDevChanged` L7742, `_setTermStatus` L7761, `_termRenderTabs` L7782, `termActivateTab` L7797, `termCloseTab` L7816, `_termCreateSession` L7839, `_termBuildXterm` L7894, `termOpenConsoleFor` L7927, `termConnect` L7942, `_termManualRetry` L7953, `_termOpenWs` L7962, `_termSendResize` L8054, `_termStartPing` L8061, `_termKillPings` L8075, `_termStartBackoff` L8081, `_termGaveUp` L8101, `_termHandleError` L8112, `_termHandleClosed` L8136, `_termBanner` L8161, `_termHideBanner` L8189, `_termRefreshUI` L8195, `termDisconnect` L8214, `termClear` L8230, `_termWatchResize` L8238, `termToggleFullscreen` L8252, `_termCopySel` L8270, `_termCopyFallback` L8284, `termSearchRun` L8302, `termSearchClose` L8334, `_termMarkLine` L8339, `cmdOpenConsole` L8353 |
| SFTP | `_formatBytes` L8371, `_formatTime` L8379, `_sftpNormPath` L8386, `_sftpJoin` L8395, `_sftpArg` L8401, `_sftpSetSummary` L8403, `_sftpProgress` L8408, `_sftpProgressDone` L8415, `sftpHideBanner` L8422, `sftpShowBanner` L8432, `sftpSetTransferEnabled` L8470, `sftpRenderBreadcrumb` L8485, `_sftpNotice` L8500, `_sftpClearList` L8515, `_sftpErrorInfo` L8526, `sftpApplyError` L8581, `sftpLoadDir` L8588, `sftpRefresh` L8694, `sftpOpenDir` L8698, `sftpNavUp` L8702, `sftpUploadSelectedFile` L8711, `sftpHandleDrop` L8767, `_sftpFilenameFromCd` L8776, `_sftpSaveBlob` L8788, `sftpDownloadFile` L8802 |
| Reports | `_repTs` L8855, `_ymd` L8860, `_repDur` L8861, `repBanner` L8866, `_repGroups` L8870, `loadReports` L8881, `_repFillRestored` L8953, `repPreset` L8963, `repWindow` L8970, `repParams` L8986, `repQS` L8996, `repApply` L9005, `repRender` L9039 |
| Sites | `_r4Epochs` L9426, `_devState` L9478, `_sitesCard` L9486, `siteToggle` L9523, `siteHist` L9569 |
| Retention | `loadRetention` L9605, `retInvalidate` L9669, `retPruneDry` L9672, `retPruneConfirm` L9706, `retPruneExec` L9716 |
| Admin / scopes | `loadUsers` L6140, `createUser` L6174, `delUser` L6181, `userScopeBadge` L9737, `userScopePanel` L9747, `userScopeSave` L9785, `userScopeClearConfirm` L9801, `userScopePanelClose` L9807, `userScopeClear` L9813, `userScopeClearExec` L9814 |
| Router / boot | `swView` L6894, `init` L4507, `togSide` L4039 (infra) |

### 2.3 Código muerto o inalcanzable (no borrar sin tarjeta aparte)

| Función | Línea | Evidencia |
|---|---|---|
| `loadHealth` | L7551 | 1 referencia (su declaración). La vista de Health Scores ya no existe; `loadPerformance` usa `/ops/health/all`. |
| `loadF` / `loadI` / `loadC` | L5785 / L5817 / L5830 | 1 referencia cada una. Restos del detalle antiguo por pestañas. |
| `renderCmd` / `rCmd(id, uid)` | L5889 / L5901 | Solo se invocan desde HTML generado por `renderCmd`, que nunca se llama. |
| `renderTpl` / `tplP` / `tplR` / `tplA` / `tplC` | L5920–L5961 | La cadena arranca en `renderTpl`, nunca llamado; sus `onclick` solo existen si se pinta. |
| `uc` / `dep` / `cDep` | L5849 / L5850 / L5863 | Solo desde HTML generado por `loadC`/`dep`, ambos inalcanzables. |
| `_det40` / `retDet` | L9429 / L9670 | Alias de `_detText` sin uso. |
| `aTab`, `curC`, `tplT` | L3007, L5829, L5919 | Estado de las funciones muertas. |

### 2.4 Duplicados y alias

- `_fmtBytes` declarada dos veces (L5064 y L9431): **gana la segunda** en todo el archivo (§7.1).
- `__t` y `_t` hacen lo mismo con distinto fallback (L3660 vs L7052).
- `_detText` tiene dos alias muertos (`_det40`, `retDet`).
- `npGray` es el mismo patrón visual que `alUnavail` (L5331) y `repBanner` (L8866): tres helpers de «no verificable» con firmas distintas.

---

## 3. Infraestructura global identificada

| Símbolo | Definición | Línea | Quién lo usa | Notas de extracción |
|---|---|---|---|---|
| `A` (base API) | `const A = location.origin + '/api'` con fallback `localhost:8082` | L3004–L3006 | 126 `fetch` | Debe existir antes de cualquier otro módulo (const de nivel superior, no va a `window`). |
| `t` (JWT) | `let t = ''` | L3007 | `h()`, `_npTick`, wrapper 401, `scheduleRefresh`, `refreshSession`, login/logout, `selD`, `loadBk` (`if (!t) return`), `dwOpen`, terminal WS (query `?token=`), `sftpUploadSelectedFile`, `load*` con guard | **No renombrar en la fase de extracción**: 26+ usos y shadowing local (§7.3). |
| `currentUser` | `let currentUser = null` | L3007 | `__isAdmin`, `load*` por rol, `_npStateKey`, `logout` | Objeto `{username, role}` de `/auth/me`. |
| `currentLang` | `let currentLang = localStorage['np_lang'] \|\| 'es'` | L3013 | `setLang`, `__t`, `_t`, `updateDashboardHero` | Persistido en `np_lang`. |
| `I18N` | diccionario `{en, es}` + 7 `Object.assign` | L3016–L3629 | `setLang`, `__t`, `_t` | 559 claves; 446 sin uso detectable por `__t(`/`data-i18n` (pueden usarse como claves dinámicas, p. ej. `__t(detailHealth)`). |
| `setLang(lang)` | ver §2.1 | L3630 | login, `init`, `selD`, `updStats` | Efecto global sobre el DOM: no convertir en función de una vista. |
| `__t(k)` / `_t(k)` | L3660 / L7052 | — | todo | `__t` es la API pública; `_t` puede colapsarse en `__t` en un commit aparte. |
| `toast(m, ty, d)` | L3662 | — | 109 usos | Añade nodo a `body`; no depende de ninguna vista. |
| `h()` | L3663 | — | 200 usos | Depende de `t`; no mover a un módulo que pueda cargarse antes que el estado. |
| Wrapper de `fetch` (401) | `const _npOrigFetch = window.fetch.bind(window)` + reasignación | L3670–L3693 | global | Se instala al evaluar el script, antes de `restoreSession` (L4018). Orden crítico. |
| `sessionExpired` | L3695 | — | wrapper, `refreshSession` | Único aviso por sesión (`_np401Done` L3012). |
| `_npTick(fn, ms)` | L3703 | — | `init` (4 latidos) | No guarda los ids: no se pueden limpiar. Ver §7.12. |
| `scheduleRefresh` / `stopRefresh` / `refreshSession` | L3711 / L3716 / L3719 | — | `enterSession`, `logout`, `restoreSession` | `_refreshTimer` L3011. |
| `esc` / `escAttr` | L3734 / L3735 | — | 255 / 48 | Núcleo de seguridad de la UI: no duplicar. |
| `IC` + `icon` + `iconTextHTML` + `setIconText` | L3738–L3824 | — | iconos de 30+ sitios | `iconTextHTML` usa una whitelist construida en cada llamada (`Object.keys(IC).map(icon)`, L3819). |
| `PALETTES` / `PALETTE_NAMES` + `setPalette` / `setTheme` / `resetTheme` / `updateThemeUI` | L3827–L3893 | — | `swView('theme')`, botones inline, IIFE L3906 | `updateThemeUI` repinta `#theme-grid` y `#palette-grid` con `onclick` generados. |
| `setRefreshInterval(secs)` | L3894 | — | `#refresh-interval`, `#mon-interval` | Vive en Theme, opera Monitoring (§2.1). |
| `loginBusy` | L3911 | — | `login` | Guard anti doble submit. |
| `showLoginError` / `enterSession` / `login` / `restoreSession` | L3912 / L3917 / L3939 / L4018–L4037 | — | login y arranque | `restoreSession` es una IIFE: se ejecuta al cargar el script. |
| `toggleUserMenu` + listener `document.click` | L3966 / L3970 | — | header | Listener global que cierra el dropdown. |
| `_npStateKey/_Get/_Set/_Clear` | L3979–L4001 | — | reports + sites + `logout` | Prefijo `np_u:<user>:`. Limpieza en `logout`. |
| `logout()` | L4003 | — | menú, `sessionExpired` | Limpia `_npStateClear`, `repState`, `_invGroupsCache`, `_rInt`, `_mInt`, `dLiveT`. **No** cierra terminal/SFTP (§7.15). |
| `togSide()` | L4039 | — | overlay, 2 botones, Escape, `swView` | `if (window.innerWidth >= 1280) return` (L4041). |
| `npExport(kind)` | L4051 | — | 4 botones | — |
| `swView(v)` + listener `hashchange` | L6894 / L6948 | — | sidebar, botones, `hashchange` | Punto de acoplamiento máximo (§5). |
| `init()` | L4507 | — | `enterSession` | Arranque de la app autenticada. |
| `currentUser` / `_np401Done` / `_refreshTimer` / `inventoryError` / `alertSummary` / `overviewState` | L3007–L3012 | — | dashboard y sesión | Ver §4. |

---

## 4. Estado mutable global

Variables de nivel superior (fuera de funciones). «Compartida» = la leen/escriben 2+ vistas.

| Variable | Línea | Tipo / valor | Vistas que la tocan | ¿Compartida? | Notas |
|---|---|---|---|---|---|
| `t` | L3007 | string JWT | todas | sí | Ver §3. |
| `devices` | L3007 | `Array<{id,hostname,driver,port,type,protocol,group,tags,_online,_lat,_rx,_tx,_cpu,_mem,_dsk}>` | dashboard, commands, terminal, backups, compare, performance, alerts, reports, devices, discovery, mant., topology, compliance | **sí (32 funciones)** | Única fuente de inventario en cliente. `loadDev` la reemplaza entera; `selD`/`loadPerformance` escriben `_cpu/_mem/_dsk` encima. |
| `sel` | L3007 | objeto device o `null` | dashboard (`renderD`, `selD`, `closeD`), detalle, modal `delDev(sel.id)` L2989 | sí | **Eclipsada por `const sel` local** en i18n L3651, theme L3888, Loki L5566, compare L5997, inventory L6637, etc. |
| `aTab` | L3007 | `'facts'` | ninguna | no | Muerta. |
| `cmdH` | L3007 | `string[]` | `rCmdBig` L5046 (push), `rCmd` L5901 | no | Historial de comandos del detalle, sin persistencia ni lectura. |
| `bSel` | L3007 | `Set<string>` | dashboard (`tB`,`tB2`,`updB`,`bClear`,`bCmd`,`bPing`,`bFacts`,`bCmdRun`) | no | Selección múltiple del grid. |
| `bMode` | L3007 | boolean | `tB`, `renderD` | no | — |
| `currentUser` | L3007 | objeto o `null` | sesión, `__isAdmin`, `_npStateKey` | sí | — |
| `alertSummary` | L3008 | `number \| null` (`null` = sin verificar) | `refreshAlertSummary`, `updStats`, `updateDashboardHero` | dashboard | Semántica de tres estados; no convertir `null` en 0 (§7). |
| `overviewState` | L3009 | `{networkKey,on,off,unknown}` | `updStats` (escribe), `updateDashboardHero`, `setLang`, `refreshAlertSummary` | dashboard + i18n | `setLang` lo consulta en cada cambio de idioma. |
| `inventoryError` | L3009 | boolean | `loadDev`, `updStats`, `updateDashboardHero` | dashboard | — |
| `_refreshTimer` | L3011 | timeout id | `scheduleRefresh`, `stopRefresh` | sesión | — |
| `_np401Done` | L3012 | boolean | wrapper 401, `sessionExpired`, `enterSession` | sesión | — |
| `currentLang` | L3013 | `'es' \| 'en'` | i18n global | sí | — |
| `loginBusy` | L3911 | boolean | `login` | login | — |
| `dragSrc` | L4378 | elemento DOM | monitoring | no | Drag & drop de charts. |
| `_origRefMon` | L4439 | función | monitoring | no | Copia de `refMon` antes de envolverlo (L4440). |
| `dTab` | L4899 | `'Overview'` | `setDTab`, `selD` | modal | Pestaña activa del detalle. |
| `alSilData` / `alSilConfirmId` / `alRulesData` | L5320 | objetos/`null` | alerts | no | Caché de silencios/reglas. |
| `alRulesOpen` | L5321 | `Set` | alerts | no | Filas expandidas. |
| `curC` / `tplT` | L5829 / L5919 | string / objeto | detalle muerto | no | Solo código inalcanzable. |
| `NB_SVG_LOCK/SYNC/OK/NO` | L6386–L6389 | strings SVG | netbox | no | Constantes, no estado. |
| `_invGroupsCache` | L6648 | `null \| false \| string[]` | `invRefreshGroups`, `invPreloadGroup`, `logout` | devices | Tres estados: sin cargar / no verificable / lista. Se invalida en `logout` (L4007). |
| `_topoData` | L7043 | `{nodes,links}` | `loadTopology` (escribe), `_topoResolveEdges`, `updateTopoMapDevices`, `redrawTopoTrace`, `buildTraceOrigins` | topology | — |
| `_topoTab` | L7044 | `'graph'\|'map'\|'table'` | `topoTab`, `loadTopology`, `startTopoGraph`, `renderTraceResult`, listener resize | topology | — |
| `_topoGraph` / `_topoGraphRAF` | L7045–L7046 | simulación / rAF id | `startTopoGraph`, `loop`, `renderTraceResult` | topology | El rAF no se cancela al cambiar de pestaña o de vista. |
| `_topoMap` / `_topoDevLayer` / `_topoTraceLayer` / `_topoTraceData` | L7047–L7050 | Leaflet / capas / último trace | `ensureTopoMap`, `updateTopoMapDevices`, `redrawTopoTrace`, `runTopoTrace`, `renderTraceResult`, `clearTopoTrace` | topology | Leaflet es global (`L`, vendor L1510). |
| `TERM_TAB_MAX/RETRY_MAX/BACKOFF_MS/PING_MS/PONG_TIMEOUT_MS/BUF_MAX` | L7675–L7680 | constantes | terminal | no | — |
| `_term`, `_fitAddon`, `_termWs` | L7681 | alias de la pestaña activa | `_termOpenWs`, `termActivateTab`, `termCloseTab`, `swView` (`_fitAddon.fit`) | terminal | **Alias compatibles**, no fuente de verdad: la real es `_termSessions`. |
| `_termSessions` | L7682 | `Array<session>` | 6 funciones terminal | terminal | Cada sesión: `term, fit, ws, pane, ct, buf, pingT, pongT, retry{attempt,timer}, status, lastErr, closedSeen, closing`. |
| `_termActiveId` / `_termSeq` | L7683–L7684 | number | terminal | terminal | — |
| `_currentTermTab` | L7685 | `'terminal'\|'sftp'` | `switchTermTab`, `termActivateTab`, `termConnect`, `termDevChanged`, `swView` | terminal + router | El router la lee (L6941). |
| `_sftpCurrentPath` | L7686 | string | `sftpLoadDir`, `sftpOpenDir`, `sftpRefresh`, `switchTermTab`, `termDevChanged` | terminal | — |
| `_sftpDeviceId` / `_sftpBlocked` / `_sftpBusy` / `_sftpLastGoodPath` | L7687–L7690 | varios | SFTP | terminal | `_sftpBlocked` = 501; `_sftpBusy` = una transferencia. |
| `_TERM_TONES` / `_SFTP_ICON_*` | L8160 / L8511–L8512 | constantes | terminal/SFTP | no | — |
| `repState` | L8845 | última ventana consultada | `loadReports`, `repApply`, `logout` | reports | Se pone a `null` en `logout` (L4007). |
| `repGroupsCache` | L8846 | `string[] \| null` | `loadReports` | reports | — |
| `dw` | L9115 | `{step,metodo,scan,sel,reused}` | 13 funciones del wizard | devices | Se destruye en `dwClose`. |
| `DW_ESTADO` | L9117 | mapa estado→clave i18n | wizard | no | — |
| `_r4Epochs` | L9426 | `{site_id: buffer_epoch}` | `_sitesCard` | sites | Objeto que **nunca se limpia**; crece con los sitios. |
| `_r4Groups` | L9427 | `string[] \| null` | `userScopePanel` (v-admin) | **sí (declarada en el bloque Sites, usada en Admin)** | Caché de `/groups` para scopes. |
| `_retPreview` | L9603 | `{dias,borraria} \| null` | retention | retention | Requisito del contrato: sin simulación no hay ejecución. |

Estado en `window` (no declarado con `let`/`const`, por eso el nombre público):

| Clave | Línea | Uso |
|---|---|---|
| `window._rInt` | L3898–L3902 | Auto-refresh elegido en Theme/Monitoring. Limpiado en `logout`. |
| `window._mInt` | L6915–L6916 | Auto-refresh de 15 s de Monitoring. Limpiado en `swView('monitoring')` y `logout`. |
| `window.dLiveT` | L5020–L5042 | Refresco de 5 s del detalle. Limpiado en `selD`, `closeD`, `logout`. |
| `window._chartRO` | L4388 | `ResizeObserver` de charts (se reutiliza, `observe()` se repite). |
| `window._termRO` | L8241 | `ResizeObserver` del terminal. |

---

## 5. Orden de extracción recomendado

### 5.1 Reglas que condicionan todo el plan

1. **No pasar a ES modules.** 146 funciones se resuelven desde atributos `on*` contra el scope global. La división debe hacerse con **`<script>` clásicos** al final del `<body>`, en orden fijo. Las declaraciones `function` de nivel superior en un script clásico sí quedan en `window`; los `let`/`const` de nivel superior comparten el entorno léxico global entre scripts, pero **no** van a `window`.
2. **Cada paso debe ser un commit verificable** con `node --check` del JS extraído y una pasada manual por las 24 vistas (no hay tests de UI).
3. **Extraer primero lo que no tiene estado ni DOM propio**; dejar para el final router, dashboard, terminal/SFTP y topología.
4. **No renombrar símbolos** en esta fase (ni `t`, ni `sel`, ni `h`, ni `esc`).

### 5.2 Módulos candidatos, en orden

| Fase | Módulo | Qué lleva | Depende de | Riesgo específico |
|---|---|---|---|---|
| 0 | (sin mover código) | Alta de `static/js/` + `<script src="…?v=1">` al final del body, en el orden final. El inline se vacía por bloques al final de cada fase. | — | Duplicar listeners si un bloque se ejecuta dos veces. Mantener el script **después** del markup (§6.3). |
| 1 | `core/state.js` | `A`, `t`, `devices`, `currentUser`, `sel`, `aTab`, `cmdH`, `bSel`, `bMode`, `alertSummary`, `overviewState`, `inventoryError`, `currentLang`, `_refreshTimer`, `_np401Done`, `loginBusy` (L3004–L3013, L3911) | nada | Debe cargarse **primero**: los demás scripts asignan a estas variables en funciones, pero `restoreSession`/`login` las leen en cuanto se ejecutan. |
| 2 | `core/i18n.js` | `I18N` completo (L3016–L3629), `setLang`, `__t`, `_t` (L3630–L3660, L7052) | `state`, `esc/setIconText`, `updateDashboardHero` | `setLang` toca 3 atributos `data-i18n-*` y llama a una función del dashboard (L3658). Dejar ese gancho explícito (callback) o mover `setLang` después del dashboard. Clave ausente detectada: `nav_terminal` (L1735) no existe en el diccionario; se queda el texto inglés del HTML. |
| 3 | `core/dom.js` | `esc`, `escAttr`, `IC`, `icon`, `iconTextHTML`, `setIconText` (L3734–L3824) | nada | `iconTextHTML` reconstruye la whitelist con `Object.keys(IC)` en cada llamada (L3819); si se «optimiza» a constante hay que congelar `IC`. |
| 4 | `core/format.js` | `_fmtBytes` (**solo la versión L9431**), `_fmtUptime`, `_donut`, `_detText`, `_det40`, `retDet`, `_ymd`, `_repTs`, `_repDur`, `_ageHuman`, `_isoLocal`, `alDur`, `npGray`, `__isAdmin` | `esc` | `_fmtBytes` duplicada: mover solo la que gana (§7.1). `alDur` vive en Alerts y `npGray`/`__isAdmin` en Groups: aquí se desacoplan por primera vez. |
| 5 | `core/session.js` | wrapper `fetch` 401, `sessionExpired`, `_npTick`, `scheduleRefresh`, `stopRefresh`, `refreshSession`, `h`, `login`, `logout`, `enterSession`, `showLoginError`, `toggleUserMenu`, `_npState*` | `state`, `i18n`, `format` (nada), `init` (lo llama) | El wrapper debe instalarse **antes** de `restoreSession`; por eso la IIFE se queda en `boot.js` (fase 12) y no aquí. `logout` limpia intervalos de otras vistas → dejar esos ids en `state`/`window`. |
| 6 | `core/theme.js` | `PALETTES`, `PALETTE_NAMES`, `setPalette`, `setTheme`, `resetTheme`, `updateThemeUI`, IIFE de arranque L3906–L3910 | `dom` | `updateThemeUI` genera `onclick="setTheme(...)"` y `onclick="setPalette(...)"` (L3882, L3892): ambas deben seguir globales. **No** mover aquí `setRefreshInterval` (llama a Monitoring). |
| 7 | `views/connectivity.js`, `views/compliance.js`, `views/webhooks.js`, `views/groups.js`, `views/maintenance.js`, `views/apikeys.js`, `views/tfa.js`, `views/logs.js` | Sus bloques completos (§2.2) | `core/*` | Bajo. `loadCompliance`/`loadGroups` usan `__isAdmin`/`npGray` (ya en core). `logs` tiene dos fuentes (audit/Loki) con estado implícito en el DOM. |
| 8 | `views/backups.js`, `views/compare.js`, `views/performance.js`, `views/alerts.js`, `views/admin.js` | Sus bloques + `userScope*` de L9737–L9825 + `_r4Groups` | `core/*`, `loadDev` (performance/compare dependen de `devices`) | `loadBk` escribe en `#sb-bk`, `#info-bk` y **`populateCmdDevices()`** (L6047) → acopla Backups con Commands. `loadBk` hace 1 request por equipo en serie. `loadUsers` pinta los scopes de Admin, declarados en el bloque Sites. |
| 9 | `views/reports.js`, `views/retention.js`, `views/sites.js`, `views/netbox.js` | Sus bloques | `core/*`, `_npState*`, `_detText`, `loadDev` | `loadReports` llama `loadDev()` si `devices` está vacío (L8885); `sites` y `retention` comparten `_r4Epochs`/`_isoLocal`/`npGray`. NetBox es solo admin: no debe romperse el banner 403. |
| 10 | `views/commands.js`, `views/monitoring.js` | Sus bloques; `refMon` **con** su reasignación L4439–L4440 en el mismo archivo | `core/*`, `setRefreshInterval`, `refreshAlertSummary` | `refMon` se redefine a sí misma al cargar: si el wrapper queda en otro archivo, `_origRefMon` puede capturar la función equivocada. `initDragDrop` acumula listeners en cada refresco (§7.13). |
| 11 | `views/devices.js` + `views/discovery.js` | Inventario/alta (L6617–L6891) y wizard (L9111–L9419), con `_invGroupsCache`, `dw`, `DW_ESTADO` | `core/*`, `loadDev`, `selD`, `swView` | `loadInventory` emite `selD(...)` (dependencia circular con dashboard) y `addInventoryDevice` llama `loadDev` + `loadInventory` con `setTimeout` de 1 s. |
| 12 | `views/dashboard.js` + `views/device-detail.js` | `init`, `loadDev`, `loadH`, `renderD`, `renderD`-helpers, `updStats`, `updateDashboardHero`, bulk (L4507–L5062) y modal de detalle (`selD`, `setDTab`, `qa*`, `dlC`, `rCmdBig`) | todos los `core`, `populateCmdDevices`, `buildSchedForms`, `dCmp`, `terms` | `loadDev` escribe en DOM de Backups y Compare (L4587–L4593, L4600) y `updStats` llama `setLang` cada 30 s. `selD` reescribe 4 pestañas y arranca un intervalo de 5 s. |
| 13 | `views/terminal.js` + `views/sftp.js` | L7672–L8369 y L8370–L8839 | `core/*`, `swView`, `_currentTermTab` | xterm y `L` son globales de vendor. Cada sesión tiene 3 timers + WS + `ResizeObserver`; `switchTermTab` y `termDevChanged` acoplan ambas vistas. El token viaja en la URL del WS (L7976). |
| 14 | `views/topology.js` | L7042–L7548 + listeners resize | `core/*`, `icon`, `setIconText` | rAF (`_topoGraphRAF`), canvas, Leaflet lazy, `Escape` y `resize` globales. |
| 15 | `router.js` | `swView` + `hashchange` (L6894–L6954) | **todas** las vistas | Último por diseño: es el único punto que conoce los 28 loaders. Mantener el guard de reentrada (`target.classList.contains('active')`) y la escritura de hash. |
| 16 | `boot.js` | `init` (si no se movió en 12), IIFE `restoreSession`, IIFE de tema, listeners de nivel superior (L3970, L4807, L4825, L6874–L6891, L6948, L7035, L7545) | todo | Si un listener global se registra dos veces (por recarga parcial o doble inclusión), hay dobles cargas. `init` registra 4 `_npTick` + 1 `setInterval` no cancelable. |

### 5.3 Grafo de dependencias (texto)

```
state ──> i18n ──> dom ──> format ──> session ──> theme
  │                                             │
  └─────────────────────────────────────────────┴──> views/*  (cada vista: core + loaders ajenos puntuales)

views/connectivity ──┐
views/compliance ────┤
views/webhooks ──────┤
views/groups ────────┤
views/maintenance ───┤
views/apikeys ───────┤
views/tfa ───────────┤
views/logs ──────────┘
views/backups ───────> commands.populateCmdDevices
views/compare ───────> dashboard.loadDev (selects cmp-a/cmp-b)
views/performance ───> dashboard.devices
views/alerts ────────> dashboard.devices (indirecto vía /system/devices)
views/admin ─────────> groups._r4Groups / loadUsers
views/reports ───────> dashboard.loadDev
views/sites ─────────> core._detText / core._isoLocal / core.npGray
views/retention ─────> core._detText / core._fmtBytes / core.npGray
views/netbox ────────> core._detText / core.npGray
views/commands <────── backups.loadBk
views/monitoring ────> dashboard.refreshAlertSummary
views/devices ───────> dashboard.loadDev + dashboard.selD + router.swView
views/dashboard ─────> commands.populateCmdDevices + backups.buildSchedForms + compare.dCmp
views/terminal <────── commands.cmdOpenConsole (swView + termOpenConsoleFor)
views/sftp <────────── terminal.switchTermTab / termDevChanged
router ──────────────> TODAS las vistas (28 loaders)
boot ────────────────> init + session.restoreSession + listeners globales
```

Ciclos reales detectados (no romperlos, documentarlos):

- `dashboard.loadDev ↔ backups.buildSchedForms` (dev llama a un pintor de otra vista).
- `backups.loadBk → commands.populateCmdDevices` (latido que reescribe un select ajeno).
- `views/devices.loadInventory → dashboard.selD → views/compare` (`qaM`).
- `monitoring.setRefreshInterval → monitoring.refMon + monitoring.loadMonitoring` aunque la función se declare en Theme.

---

## 6. Patrones de compatibilidad

### 6.1 Montaje actual del JS

- **Un solo `<script>` clásico** (sin `type="module"`, sin `defer`, sin `async`) al final del `<body>`: L3003–L9826. Todo el markup ya está parseado cuando se evalúa.
- No hay `DOMContentLoaded`: el código asume que el DOM existe. Varias sentencias de nivel superior lo requieren de forma dura:
  - `document.getElementById('grid').addEventListener(...)` L4807,
  - `document.getElementById('ops-focus-items').addEventListener(...)` L4825,
  - `document.querySelectorAll('.sidebar-item[onclick]')…` L6874,
  - `document.addEventListener('keydown', …)` L6883, `L7035`,
  - `window.addEventListener('hashchange', …)` L6948 y `resize` L7545.
  Si el script subiera al `<head>` sin `defer`, la primera lanzaría `TypeError` y abortaría el resto.
- IIFEs que se ejecutan al cargar: tema L3906–L3910 y `restoreSession` L4018–L4037.
- Listeners a nivel `document`/`window`: 7 (menú, grid, focus-items, teclado×2, hashchange, resize). No hay `removeEventListener` en ningún sitio.
- Handlers inline en el markup: **132 atributos**. Handlers inline generados por strings JS: **98**. Total **230**; **146 funciones distintas** dependen de seguir siendo globales (lista completa en §6.4).

### 6.2 Qué debe seguir siendo global

1. Las **146** funciones de §6.4 (invocadas desde `onclick/onchange/oninput/onsubmit/onkeydown/on*drag*`).
2. Las funciones llamadas desde `setTimeout`/`setInterval` por nombre o por referencia capturada (`loadDonuts`, `refMon`, `loadMonitoring`, `_npTick` recibe la función como argumento: si se importa como módulo, la referencia sigue funcionando; si se renombra, no).
3. `window.fetch` (reasignado) y `window.dTerm*`/`window._rInt`/`window._mInt`/`window.dLiveT`/`window._chartRO`/`window._termRO`.
4. `setLang`, `setPalette`, `setTheme`, `resetTheme`, `setRefreshInterval`, `topoTab`, `switchTermTab`, `term*`, `sftp*`, `dw*`: aparecen en atributos `on*`.
5. `delDev(sel.id)` (L2989) depende de que `sel` sea global y visible desde el markup.

### 6.3 Restricciones de carga

- El script debe seguir **después del markup** (o usar `defer` en todos los archivos). Recomendación: mantenerlo al final del `<body>` para no alterar el momento en que corren `restoreSession` y el IIFE de tema.
- Orden mínimo: `state → i18n → dom → format → session → theme → views/* → router → boot`. El no cumplirlo produce `ReferenceError` en tiempo de evaluación (no de llamada) en: `A` (usada por los `fetch`), `I18N` (usada por los `Object.assign`), `IC` (usada por `icon`), `PALETTES` (usado por el IIFE de tema) y `_npOrigFetch`.
- Dependencias de vendor, todas en `<head>` y bloqueantes:
  - `/static/vendor/xterm.js` L1508, `/static/vendor/xterm-addon-fit.js` L1509, `/static/vendor/leaflet.js` L1510.
  - El código comprueba `typeof Terminal === 'undefined'` (L7840, L7963) para el estado honesto «xterm no cargó».
  - Leaflet se usa como global `L` (`ensureTopoMap` L7392) y se carga con `loading="lazy"` solo el iframe de Grafana; el mapa se inicializa bajo demanda.
- CSS **no tocar**:
  - `/static/style.css?v=4` L12,
  - `/static/overview.css?v=3` L1505,
  - `/static/vendor/xterm.css` L1506,
  - `/static/vendor/leaflet.css` L1507.
  Los `?v=` son parte del contrato de caché del despliegue; cualquier cambio de versión debe ir en su propia tarjeta. Los nuevos `.js` deben llevar su propio `?v=1` y subir solo cuando cambien.
- El HTML servido incluye dos bloques `<style>` (L13–L1504 y L1529–L1683); el segundo está **dentro del body**, después del login. No moverlo a un `.css` en esta fase: cambia el orden de cascada.

### 6.4 Lista de funciones que deben seguir en `window` (146)

Estáticas (78): `_termCopySel`, `addInventoryDevice`, `alSubmitSilence`, `bClear`, `bCmd`, `bFacts`, `bPing`, `backupAllDevices`, `bdDiff`, `bdLoadBackups`, `closeD`, `cmdOpenConsole`, `createKey`, `createMaint`, `dCmp`, `delDev`, `dwOpen`, `execGlobalCmd`, `fDev`, `goToOverviewDevices`, `invGroupChanged`, `invPreloadGroup`, `loadAlertRules`, `loadAlertSilences`, `loadAlerts`, `loadBk`, `loadCompliance`, `loadConn`, `loadDev`, `loadGroups`, `loadInventory`, `loadLogs`, `loadLokiLogs`, `loadMonitoring`, `loadNetbox`, `loadPerformance`, `loadReports`, `loadRetention`, `loadSites`, `loadTopology`, `loadUsers`, `loadWebhooks`, `login`, `logout`, `npExport`, `offsiteExport`, `refMon`, `resetTheme`, `runQuickCmd`, `runTopoTrace`, `setLang`, `setRefreshInterval`, `setup2FA`, `sftpHandleDrop`, `sftpHideBanner`, `sftpLoadDir`, `sftpNavUp`, `sftpRefresh`, `sftpUploadSelectedFile`, `showAddDevicePanel`, `showKeyForm`, `showMaintForm`, `silAddMatcher`, `swView`, `switchLogSource`, `switchTermTab`, `termClear`, `termConnect`, `termDevChanged`, `termDisconnect`, `termSearchRun`, `termToggleFullscreen`, `togSide`, `togSilForm`, `toggleInvAuth`, `toggleInvCli`, `toggleUserMenu`, `topoTab`, `verify2FA`.

Generadas en strings (69): `addWebhook`, `alSilCancel`, `alToggleExpr`, `bulkClosePanel`, `cDep`, `cancelJob`, `checkCompliance`, `clearTopoTrace`, `createGroup`, `createUser`, `delMaint`, `delUser`, `deleteWebhook`, `dep`, `dlBackup`, `dlC`, `dwClose`, `dwImport`, `dwReset`, `dwScan`, `dwSel`, `dwSelAll`, `dwToStep3`, `dwToStep4`, `dwToggleAllSources`, `grpDevs`, `loadCompReport`, `nbSyncCancel`, `nbSyncConfirm`, `nbSyncRun`, `qaB`, `rCmd`, `rCmdBig`, `repApply`, `repPreset`, `retInvalidate`, `retPruneConfirm`, `retPruneDry`, `retPruneExec`, `revokeKey`, `scheduleBackup`, `scheduleRecurring`, `selD`, `setBaseline`, `setDTab`, `setPalette`, `setTheme`, `sftpDownloadFile`, `sftpLoadDir`, `sftpOpenDir`, `siteHist`, `siteToggle`, `tB2`, `termActivateTab`, `termCloseTab`, `termSearchClose`, `tplA`, `tplC`, `tplP`, `tplR`, `uc`, `userScopeClear`, `userScopeClearExec`, `userScopePanel`, `userScopePanelClose`, `userScopeSave`.

(`sftpLoadDir` está en ambas listas; por eso la unión es 146 y no 147.)

Verificación recomendada tras cada extracción:

```bash
# 1) sintaxis del JS extraído
node --check app/static/js/views/<vista>.js
# 2) handlers inline sin resolver (debe imprimir vacío)
python3 - <<'EOF'
import re,glob
html=open('app/static/dashboard.html').read()
js=''.join(open(f).read() for f in glob.glob('app/static/js/**/*.js',recursive=True))
inline={m for m in re.findall(r'on(?:click|change|input|submit|keydown)="([A-Za-z_$][\w$]*)\(',html)}
missing=[f for f in inline if not re.search(r'function\s+'+f+r'\b|'+f+r'\s*=\s*(async\s*)?(function|\()',js)]
print(sorted(missing))
EOF
```

---

## 7. Trampas: lo que NO se debe tocar a ciegas

### 7.1 `_fmtBytes` duplicada (L5064 y L9431)

Dos declaraciones con el mismo nombre en el mismo scope. Por hoisting **gana la segunda** para todas las llamadas, incluidas las del detalle de equipo (L9642 en Retention, y las que se hicieron pensando en L5064). Formatos distintos: la primera usa `toFixed(0|1)` con unidades `B..TB`; la segunda devuelve `1.00 GB` (dos decimales) y no soporta TB. Extraer **solo la segunda** reproduce el comportamiento actual; mover ambas o «arreglar» la primera cambia la UI.

### 7.2 `refMon` se redefine a sí misma (L4439–L4440)

```js
const _origRefMon = refMon;
refMon = async function () { await _origRefMon(); initDragDrop() };
```

`refMon` es una declaración de función (L4245) reasignada al evaluar el script. Si el wrapper se mueve a otro archivo cargado después del bloque de Monitoring, `_origRefMon` puede capturar la versión ya envuelta (recursión) o `undefined` si el orden se invierte. **Ambas cosas deben vivir en el mismo archivo, en ese orden.**

### 7.3 El token `t` está eclipsado en varios sitios

- `setDTab(t)` L4900 (parámetro).
- `rCmdBig(id)` L5046: `const i = …, t = document.getElementById('t-big')` — dentro de esa función `t` **no** es el JWT; usa `h()`, que sí lo lee del global. Cualquier refactor que «simplifique» `h()` a un parámetro rompe esto.
- `topoTab(tv)`, `_t(k)`, `topoTab` y múltiples `t =>` de callbacks.
- `const sel = …` local en i18n (L3651), theme (L3888), Loki (L5566), Compare (L5997), inventario (L6637, L6651…), etc., eclipsa el `sel` global del detalle.

**Regla:** no renombrar `t` ni `sel` en la fase de extracción, ni introducir un `const t` nuevo.

### 7.4 Orden de ejecución del interceptor 401

El wrapper se instala en L3670–L3693, **antes** de la IIFE `restoreSession` (L4018) y de cualquier `fetch` de los loaders. Si `session.js` se carga después de `boot.js`, la restauración de sesión no pasa por el interceptor y un 401 al arrancar no devuelve al login. Mantener `state → session → … → boot`.

### 7.5 `swView` escribe el hash y el `hashchange` vuelve a llamar a `swView`

L6950: `if (location.hash.slice(1) !== v) location.hash = v;`
L6948: listener que llama `swView(v)` solo si la vista no está activa. Si al dividir se registra el listener dos veces (p. ej. router incluido dos veces), cada navegación dispara dos cargas de la misma vista. El guard de L6951 es la única protección.

### 7.6 `loadDev` escribe en DOM de otras vistas

- Rellena `#cmp-a` y `#cmp-b` y fuerza `selectedIndex = 1` (L4588–L4593): **en cada latido de 30 s pisa la selección del operador en Compare**.
- Llama `buildSchedForms()` (L4587), que reescribe `#sched-single-form` / `#sched-rec-form` de Backups y pierde lo que el usuario estuviera escribiendo.
- Al terminar, `renderD(); loadDonuts(); updStats()` y `updStats` llama `setLang(currentLang)` (L4896), que recorre todo el documento. Es el mayor coste periódico de la UI.

No «optimizar» esto dentro de la extracción: primero mover tal cual, luego una tarjeta específica.

### 7.7 `loadBk` reescribe el selector de Commands

L6047 llama `populateCmdDevices()` en cada ejecución (latido de 60 s y tras cualquier backup). Reconstruye `#cmd-dev` y **pierde la selección** del operador en la vista Commands. Además hace un `fetch` por dispositivo en serie (L6041) y escribe en el sidebar (`#sb-bk`, `#info-bk`, L6045–L6046).

### 7.8 `loadInventory` depende del dashboard (ciclo)

Emite `onclick="selD('${d.id}')"` en cada fila (L6865). `selD` vive en el bloque del dashboard y abre el modal `#dp`. Extraer `devices.js` antes que `dashboard.js` deja una referencia a una función que aún no existe si el orden de carga se invierte: `selD` solo se invoca por click, así que basta con que el archivo del dashboard esté cargado antes de que el usuario pulse; aun así, el orden recomendado lo pone después.

### 7.9 Listeners de nivel superior que asumen DOM ya presente

L4807 (`#grid`), L4825 (`#ops-focus-items`), L6874 (`.sidebar-item[onclick]`). Son sentencias sueltas entre funciones. Al cortar el archivo por rangos es fácil que caigan dentro del módulo equivocado y se ejecuten antes de que exista el nodo. Deben ir a `boot.js` al final.

### 7.10 `selD` reescribe pestañas y arranca un intervalo

L4910–L5062: 4 `fetch` en paralelo, `setLang(currentLang)` (L4969), varios bloques `innerHTML` (`#dm`, `#tabs`, `#dc`, `#qa`, `#tc-*`) y `textContent` (`#dt`), `scrollIntoView` y `setInterval` de 5 s con `window.dLiveT` (L5020). Ese intervalo se autodestruye si `#dp` deja de estar activo o cambia `sel` (L5021–L5022), y lo limpian `closeD` y `logout`. Si se mueve a un módulo con su propio estado, el id debe seguir en `window` o `logout` deja de poder limpiarlo.

### 7.11 `cmdH`, `bSel` y `bMode` sobreviven al cambio de vista

`bSel`/`bMode` no se resetean al salir del dashboard: si el usuario activa multiselección y navega, al volver siguen activos. `renderD` los consulta (L4768). No añadir reseteos «de higiene»: cambia comportamiento.

### 7.12 Intervalos que nunca se limpian

- `init` L4521: `setInterval(loadDonuts, 120000)` sin id guardado; `logout` no lo puede parar. Solo hace trabajo si `#v-devices` está activa.
- `_npTick` L3703: los 4 latidos de `init` (L4517–L4520) tienen id pero no se guardan; se autolimitan con `if (t)`, así que tras logout quedan vivos y inertes. Al extraer `init`, no convertirlos en `setInterval` sin guarda.
- `window._mInt` L6916: sí se limpia en `swView('monitoring')` y `logout`.
- `window._rInt` L3899: solo si el usuario elige intervalo en Theme/Monitoring.

### 7.13 `initDragDrop` acumula listeners

`refMon` llama `initDragDrop` en cada refresco (15 s o el intervalo elegido). `initDragDrop` hace `el.addEventListener('dragstart'|'dragover'|'dragleave'|'drop'|'dragend', …)` sobre los `.mon-chart` fijos (L4394–L4411) **sin comprobar si ya estaban**: los handlers se multiplican. También llama `window._chartRO.observe(el)` repetidamente (L4391). No es parte del refactor arreglarlo, pero sí es un riesgo al mover el bloque: si `initDragDrop` se ejecuta además desde otro sitio, el efecto se duplica.

### 7.14 `sftpLoadDir` en el centro de Terminal ↔ SFTP

Lo llaman `switchTermTab` (L7722), `termDevChanged` (L7757), `sftpRefresh`, `sftpOpenDir`, `sftpNavUp`, el `change` del input de ruta y los botones. Además usa `_sftpDeviceId`, `_sftpCurrentPath`, `_sftpBlocked`, `_sftpBusy` y modifica `#sftp-file-list` completo. Extraer SFTP antes de Terminal deja esas variables en el archivo equivocado.

### 7.15 `logout` no cierra terminal ni SFTP

L4003–L4015 limpia `_refreshTimer`, `np_u:*`, `repState`, `_invGroupsCache`, `_rInt`, `_mInt` y `dLiveT`, pero **no** recorre `_termSessions`: los WebSocket siguen abiertos con el token anterior y el `ResizeObserver` (`window._termRO`) sigue observando. Es un defecto existente; documentarlo y no «arreglarlo» dentro de un commit de extracción (cambiaría el ciclo de vida de la sesión).

### 7.16 `setLang` como efecto secundario universal

`updStats` lo invoca cada 30 s (L4896), `selD` en cada apertura de detalle (L4969) y `init` al arrancar (L4508). Recorre `[data-i18n]` (127 nodos) y llama `updateDashboardHero` si hay `overviewState` (L3657). Cualquier módulo que lo importe hereda ese efecto; no convertirlo en «traducir solo mi vista».

### 7.17 `currentUser` es `null` hasta `enterSession`

`loadInventory`, `loadReports`, `loadCompliance` etc. consultan `currentUser?.role`; `__isAdmin()` devuelve `false` con `null` (rol por defecto `viewer`, L6314). No cambiar el valor por defecto a `admin` «para simplificar».

### 7.18 Estado por usuario que no debe migrarse a `localStorage` plano

`_npStateGet/Set` usan `np_u:<username>:<key>` (L3979–L3991) y `logout` los borra (L3992–L4001). Solo hay dos claves reales: `rep` y `site`. Si un módulo nuevo escribe con `localStorage.setItem` directo, sobrevive al logout y mezcla usuarios en un navegador compartido.

### 7.19 Nombres cortos y ambiguos usados en inline

`h()` (cabeceras), `t` (token), `sel` (equipo), `A` (API), `_t` (i18n), `esc` (escape), `togSide`, `uc`, `dep`, `qaB/qaS/qaP/qaM`, `dlC`, `tB/tB2`, `bd*`, `nb*`, `dw*`. Todos resuelven contra `window`. Cualquier intento de encapsularlos en un namespace (`NP.h`) exige reescribir 230 atributos: **fuera del alcance de esta fase**.

### 7.20 XSS y frontera de confianza al mover código

`esc`/`escAttr` se aplican de forma desigual: `loadInventory` interpola `${d.id}`, `${d.host}`, `${d.type}`, `${d.group}` **sin** `esc` en la tabla (L6849–L6866), mientras `renderD` usa `escAttr` en `data-id` (L4768). Al extraer, no unificar el escapado en el mismo commit: cambia el HTML generado y merece su propia revisión de seguridad.

---

## 8. Anexos

### 8.1 Mapa de temporizadores y observadores

| Timer / observer | Línea | Vive en | Se limpia |
|---|---|---|---|
| `_refreshTimer` (25 min) | L3714 | session | `stopRefresh`, `scheduleRefresh`, `logout` |
| 4 × `_npTick` (10 s, 30 s, 45 s, 60 s) | L4517–L4520 | init | nunca (guarda `if (t)`) |
| `setInterval(loadDonuts, 120 s)` | L4521 | init | nunca |
| `window._rInt` (5–60 s) | L3899 | theme/monitoring | `setRefreshInterval`, `logout` |
| `window._mInt` (15 s) | L6916 | router | `swView('monitoring')`, `logout` |
| `window.dLiveT` (5 s) | L5020 | device-detail | `selD`, `closeD`, `logout` |
| `window._chartRO` (ResizeObserver) | L4388 | monitoring | nunca |
| `_topoGraphRAF` | L7359 | topology | nunca (solo se reasigna) |
| `s.pingT` / `s.pongT` (25 s / 10 s) | L8063–L8068 | terminal | `_termKillPings`, `termCloseTab`, `termDisconnect` |
| `s.retry.timer` (1–15 s) | L8096 | terminal | `_termOpenWs`, `_termManualRetry`, `termCloseTab`, `_termStartBackoff` |
| `window._termRO` (ResizeObserver) | L8241 | terminal | nunca |
| `setTimeout` de feedback (toast, status) | L3662, L5727, L8697… | varios | automático |

### 8.2 Prefijos y convenciones de nombres (útiles para agrupar)

| Prefijo | Dominio | Ejemplos |
|---|---|---|
| `_np*` | sesión / estado por usuario | `_npTick`, `_np401Done`, `_npStateGet` |
| `_term*`, `term*` | consola SSH | `_termOpenWs`, `termConnect` |
| `_sftp*`, `sftp*` | gestor de archivos | `sftpLoadDir`, `_sftpErrorInfo` |
| `_topo*`, `topo*` | topología | `_topoGraph`, `topoTab` |
| `_inv*`, `inv*` | inventario/alta | `_invGroupsCache`, `invGroupChanged` |
| `dw*`, `_dw*` | wizard discovery | `dwStep1`, `_dwEstadoText` |
| `al*`, `sil*` | alertas | `alSubmitSilence`, `silAddMatcher` |
| `_rep*`, `rep*` | reportes SLA | `_repGroups`, `repApply` |
| `ret*` | retención | `retPruneDry` |
| `nb*` | NetBox | `nbSyncRun` |
| `bd*` | backups diff | `bdDiff` |
| `qa*` | acciones del detalle | `qaP`, `qaM` |

### 8.3 Checklist para cada commit de extracción

1. `git diff --stat` limitado a `dashboard.html` (solo se recorta el bloque) + archivo nuevo en `static/js/`.
2. `node --check` del archivo nuevo.
3. Comprobar que ninguna función del bloque extraído queda también en el inline (doble declaración = la última gana silenciosamente).
4. Recorrer: login → dashboard (grid + detalle) → devices (alta) → terminal → sftp → monitoring → topology → un vista de cada grupo.
5. Verificar que el hash (`#devices`, `#terminal`, …) sigue restaurando la vista tras F5.
6. No tocar `?v=` de CSS; subir solo el `?v=` del JS modificado.

---

## Reporte final

- **Total de vistas:** 24 (`#v-dashboard` … `#v-2fa`, L1927–L2978) + 1 modal de detalle (`#dp`, L2981) que no es vista.
- **Líneas:** 9.830 totales → **JS 6.824 (69,4 %)**, **CSS 1.647 (16,8 %)**, **HTML 1.359 (13,8 %)**.
- **Top-5 funciones más sensibles:**
  1. `window.fetch` wrapper 401 (L3670–L3693) — se instala una vez, afecta a los 126 `fetch` y al arranque de sesión.
  2. `swView(v)` (L6894) — router con 28 loaders, hash y auto-refresh de monitoring.
  3. `init()` (L4507) — 4 latidos, 1 intervalo no cancelable y las primeras cargas de 3 vistas.
  4. `loadDev()` (L4524) — inventario canónico que escribe en Backups y Compare y dispara `setLang` cada 30 s.
  5. `selD(id)` (L4910) — 4 `fetch`, 4 `innerHTML`, `setLang` y un intervalo de 5 s; lo invocan dashboard e inventario.
  *Menciones:* `logout()` (L4003, limpieza incompleta de terminal), `refMon` (L4245, se reasigna a sí misma), `loadInventory()` (L6834, ciclo con `selD`).
- **Orden de extracción en una línea:** `state → i18n → dom → format → session → theme → vistas simples (connectivity, compliance, webhooks, groups, maintenance, apikeys, tfa, logs) → backups/compare/performance/alerts/admin → reports/retention/sites/netbox → commands/monitoring → devices+discovery → dashboard+detalle → terminal+sftp → topology → router → boot`.
