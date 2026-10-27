// NetPulse NMS — Helpers compartidos (Lote K): formato de importes/tiempos,
// población del selector de Commands y avisos «no verificable». Contenido y
// orden heredados del inline del monolito (la 1.ª _fmtBytes sigue sombreada
// por la del final de router.js, trampa §7.1 del mapa: no reordenar).


    // ── PORTADA / DASHBOARD — viajó a views/dashboard.js (Lote h) ──
    // loadDonuts (donuts por tarjeta), refreshAlertSummary (latido 45 s → badge
    // del sidebar/header), init (arranque autenticado: 4 latidos _npTick +
    // setInterval(loadDonuts, 120000) no cancelable, defecto preexistente §7.12
    // conservado tal cual), loadH (latido 10 s), fDev (filtro #sb), renderD,
    // los DOS listeners top-level (#grid y #ops-focus-items, que siguen siendo
    // sentencias sueltas sin DOMContentLoaded: el script carga tras el markup),
    // goToOverviewDevices, showAddDevicePanel, updateDashboardHero y updStats.
    // El estado compartido (devices/sel/bSel/bMode y alertSummary/overviewState/
    // inventoryError) queda aquí; dashboard.js lo resuelve en tiempo de llamada.

    // ── INVENTARIO CANÓNICO (loadDev, latido 30 s) ──
    // Viajó a views/devices.js (Lote F). Sus consumidores: init (arranque),
    // latido _npTick(loadDev, 30000), botón "Actualizar" del overview (markup),
    // delDev/addInventoryDevice (views/devices.js), dwOpen/dwImport
    // (views/discovery.js), loadReports (views/reports.js) y swView('terminal')
    // — todos lo resuelven en tiempo de llamada contra window. Sus acoples del
    // latido (renderD/loadDonuts/updStats y el repintado de #cmp-a/#cmp-b,
    // trampa §7.6 conservada) viajaron a views/dashboard.js (Lote h).
    // buildSchedForms viajó a views/backups.js (Lote G): pinta los selects
    // #sched-single-form/#sched-rec-form desde el inventario (trampa §7.6 del
    // mapa, conservada). Consumidores: loadDev (views/devices.js, latido 30 s)
    // y swView('backups') — ambos la resuelven en tiempo de llamada.

    // ── BULK OPERATIONS (selección múltiple del grid) ──
    // Viajó a views/devices.js (Lote F): tB, tB2, updB, bClear, bulkPanel,
    // bulkClosePanel, bulkReset, bulkSummary, bCmd, bCmdRun, bPing y bFacts.
    // Los botones del #bulk-bar (markup #v-dashboard), el checkbox que genera
    // renderD (tB2) y el onclick="bulkClosePanel()" del panel resuelven por
    // window. fDev (filtro del buscador) y el listener del #grid viajaron a
    // views/dashboard.js (Lote h) junto con renderD, que los llama; el estado
    // bSel/bMode (línea 2 del script) queda aquí y lo consultan en tiempo de
    // llamada.


    // ── DETALLE DE EQUIPO (modal #dp) — viajó a views/device-detail.js (Lote h) ──
    // setDTab, selD (4 fetch + setLang + intervalo de 5 s en window.dLiveT),
    // rCmdBig (c-big/t-big), closeD, qaB/qaS/qaP/qaM y dlC → views/device-detail.js,
    // que se carga ANTES de views/dashboard.js. También viajaron los restos de
    // la primera versión por pestañas (loadF/loadI/loadC, renderCmd/rCmd,
    // renderTpl y la familia tpl*, uc/dep/cDep): código muerto del mapa §2.3,
    // movido tal cual y NO revivido. El estado dTab/curC/tplT viajó con su
    // bloque; cmdH era un ítem de la línea let compartida del grupo de estado
    // (devices/sel/aTab/cmdH/bSel/bMode) y viajó también: sus únicos
    // consumidores eran rCmdBig y rCmd. En el grupo quedan devices/sel/aTab/
    // bSel/bMode. Todas las llamadas cruzadas (selD→renderD,
    // rCmd→updStats, qaM→dCmp/swView, qaB→loadBk, logout→dLiveT) resuelven en
    // tiempo de llamada contra window.
    // ── PERFORMANCE VIEW (Real Data) ──
    function _fmtBytes(b) {
      b = Number(b) || 0;
      const u = ['B', 'KB', 'MB', 'GB', 'TB'];
      let i = 0;
      while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
      return b.toFixed(b >= 100 || i === 0 ? 0 : 1) + ' ' + u[i];
    }
    function _fmtUptime(s) {
      s = Math.max(0, parseInt(s) || 0);
      const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
      if (d) return `${d}d ${h}h`;
      if (h) return `${h}h ${m}m`;
      return `${m}m`;
    }
    // `_donut` viajó a views/reports.js (Lote D): su única llamada restante era
    // repRender (el dashboard pinta sus donuts en su propio template; grep).

    function alDur(s) {
      s = Math.max(0, Math.round(Number(s) || 0));
      const m = Math.floor(s / 60), h = Math.floor(m / 60), d = Math.floor(h / 24);
      if (m < 60) return `${m}m`;
      if (h < 24) return (h ? `${h}h ` : '') + `${m % 60}m`;
      return `${d}d ${h % 24}h`;
    }

    // ── GLOBAL COMMAND CENTER ──
    function populateCmdDevices() {
      const s = document.getElementById('cmd-dev');
      if (s) s.innerHTML = devices.map(d => `<option value="${d.id}">${d.id} (${d.driver})</option>`).join('');
    }

    // backupAllDevices viajó a views/backups.js (Lote G): su único consumidor
    // es el onclick del botón "Backup All" de #v-backups (markup), window en el
    // click; llama loadBk (backups.js) al terminar.

    // delDev viajó a views/devices.js (Lote F): su único llamador es el
    // onclick="delDev(sel.id)" del modal #dp (markup, aquí), resuelto contra
    // window en el click. Nada la lee a nivel de evaluación.


    // ── BACKUPS y COMPARE — viajaron (Lote G) ──
    // dCmp, bdPopulateDevices, bdLoadBackups y bdDiff → views/compare.js:
    // consumidores = onchange de #cmp-a/#cmp-b/#bd-dev y onclick de #v-compare
    // (markup), swView('compare') y qaM (modal #dp, inline residual) — todos en
    // tiempo de llamada contra window; nada top-level lee los selects.
    // offsiteExport, loadBk (latido 60 s), dlBackup y el scheduler completo
    // (loadJobs, doBackupMulti, scheduleBackup, scheduleRecurring, cancelJob)
    // → views/backups.js. La llamada populateCmdDevices() de loadBk resuelve el
    // símbolo inline del bloque GLOBAL COMMAND CENTER (decisión del Lote G:
    // sus consumidores restantes son loadBk en backups.js y swView('commands')
    // aquí — N>1 usuarios, regla 2). qaB/qaM viajaron a views/device-detail.js
    // (Lote h) con el modal #dp; sus llamadas (loadBk/dCmp/swView) resuelven en
    // tiempo de llamada contra window.

    // ── GRUPOS DE DISPOSITIVOS (módulo dormido: GET /api/groups) ──
    // Contrato real: GET /api/groups → 200 para viewer con [{name, description, device_count}].
    // POST /api/groups → 201 admin, 409 si existe duplicado, 403 si operador no admin.
    // GET /api/groups/{grupo}/devices → 404 si el grupo está vacío o no existe.
    function __isAdmin() { return (currentUser?.role || 'viewer') === 'admin'; }
    function npGray(msgHtml) {
      return `<div class="empty" style="background:var(--bg2)"><span class="icon" style="filter:grayscale(1)">—</span><br><span style="color:var(--text-ter)">${msgHtml}</span></div>`;
    }

    // ── NETBOX → views/netbox.js (Lote D) ──
    // Viajó el bloque completo: constantes NB_SVG_*, loadNetbox, nbLoadSites,
    // nbSyncConfirm, nbSyncCancel, nbList, nbRenderResult, nbFailBanner y
    // nbSyncRun. Quedan aquí (compartidos): __isAdmin/npGray (arriba) y
    // _detText (bloque REPORTES SLA, más abajo).

    // ── DEVICE MANAGER (V-INVENTORY) ──
    // Viajó a views/devices.js (Lote F): _invContainer, toggleInvAuth,
    // toggleInvCli, estado let _invGroupsCache (logout en core.js lo invalida
    // en tiempo de llamada), _invGroupEls, _invGroupSetManual, invGroupChanged,
    // invRefreshGroups, invReadGroup, invPreloadGroup, addInventoryDevice
    // (grupo del alta Ola 9: envía `group`, 403 literal y aviso de equipo sin
    // grupo intactos) y loadInventory. Archivo de alta accesible desde el
    // overview (showAddDevicePanel viajó a views/dashboard.js, Lote h) y emite
    // onclick="selD(...)", que vive en views/device-detail.js (Lote h).

