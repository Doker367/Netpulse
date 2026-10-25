// NetPulse NOC — Router y arranque (Lote K): accesibilidad del sidebar,
// listeners globales (Escape, Ctrl/Cmd+K, click del menú), swView +
// hashchange y los helpers residuales. Último <script> de la página:
// sustituye al bloque inline del monolito sin cambiar su posición.

    // La navegación del panel también funciona con teclado y lector de pantalla.
    document.querySelectorAll('.sidebar-item[onclick]').forEach(item => {
      item.setAttribute('role', 'button');
      item.tabIndex = 0;
      if (item.classList.contains('active')) item.setAttribute('aria-current', 'page');
      item.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); item.click(); }
      });
    });
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && document.getElementById('sidebar').classList.contains('open')) togSide();
      // El buscador global anuncia (Ctrl+K): que sea verdad, en todas las plataformas.
      if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        const search = document.getElementById('sb');
        if (search && document.getElementById('main').style.display !== 'none') { search.focus(); search.select(); }
      }
    });

    // ── SWITCH VIEW ──
    function swView(v) {
      // La vista "Métricas" se fusionó en "Monitoreo": el hash #monitoreo sigue funcionando.
      if (v === 'monitoreo') v = 'monitoring';
      const target = document.getElementById(`v-${v}`);
      if (!target) { console.warn('Vista inexistente:', v); return; }
      document.querySelectorAll('.view').forEach(e => e.classList.remove('active'));
      target.classList.add('active');
      document.querySelectorAll('.sidebar-item').forEach(e => {
        const active = e.getAttribute('onclick') === `swView('${v}')`;
        e.classList.toggle('active', active);
        if (active) e.setAttribute('aria-current', 'page');
        else e.removeAttribute('aria-current');
      });
      if (document.getElementById('sidebar').classList.contains('open') && window.innerWidth < 900) togSide();
      if (v === 'backups') { loadBk(); buildSchedForms(); loadJobs(); }
      if (v === 'devices') loadInventory();
      if (v === 'webhooks') loadWebhooks();
      if (v === 'admin') loadUsers();
      if (v === 'monitoring') {
        loadMonitoring(); refMon();
        // Un solo auto-refresh para la vista: gobierna stack y métricas de plataforma.
        if (window._mInt) clearInterval(window._mInt);
        window._mInt = setInterval(() => { loadMonitoring(); refMon(); }, 15000);
      }
      if (v === 'connectivity') loadConn();
      if (v === 'performance') loadPerformance();
      if (v === 'alerts') { loadAlerts(); loadAlertSilences(); loadAlertRules(); }
      if (v === 'logs') loadLogs();
      if (v === 'commands') populateCmdDevices();
      if (v === 'theme') updateThemeUI();
      if (v === 'topology') loadTopology();
      if (v === 'maintenance') loadMaint();
      if (v === 'apikeys') loadKeys();
      if (v === 'compliance') loadCompliance();
      if (v === 'groups') loadGroups();
      if (v === 'sites') loadSites();
      if (v === 'retention') loadRetention();
      if (v === 'netbox') loadNetbox();
      if (v === 'reports') loadReports();
      if (v === 'terminal') {
        // Si el inventario aún no terminó de cargar (init/loadDev en curso), se
        // repuebla el selector al terminar en vez de dejarlo vacío.
        if (!devices.length) loadDev().then(termPopulateDevices);
        else termPopulateDevices();
        if (_currentTermTab === 'terminal' && _fitAddon) {
          setTimeout(() => { try { _fitAddon.fit(); } catch (e) { } }, 100);
        }
      }
      if (v === 'compare') bdPopulateDevices();
      // La vista vive en la URL: refresco y enlaces compartidos aterrizan donde el operador estaba.
      if (location.hash.slice(1) !== v) location.hash = v;
    }

    // Boton atras/adelante y vistas abiertas por URL sincronizan con la vista activa.
    window.addEventListener('hashchange', () => {
      const v = location.hash.slice(1);
      const target = v && document.getElementById(`v-${v}`);
      if (!target || target.classList.contains('active')) return;
      swView(v);
    });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        const dd = document.getElementById('user-dd');
        if (dd) dd.classList.remove('open');
      }
    });

    // ── TOPOLOGY (grafo canvas + mapa Leaflet + tabla + traceroute) ──
    // Viajó a views/topology.js (Lote J), contiguo y byte a byte: estado
    // `_topo*` (incluido el mapa Leaflet y sus capas), `_t` (alias local de
    // i18n, exclusivo de esta vista desde el Lote H), topoTab, buildTraceOrigins,
    // loadTopology (GET /topology/map), _topoScreen2World, _topoResolveEdges,
    // startTopoGraph (+ step/draw/loop), ensureTopoMap, updateTopoMapDevices,
    // redrawTopoTrace, runTopoTrace (POST /topology/traceroute), clearTopoTrace,
    // renderTraceResult y el listener `resize` que reajusta canvas/Leaflet.
    // También el bloque HEALTH SCORES con `loadHealth` (código muerto del mapa
    // §2.3, movido tal cual y NO revivido). El vendor Leaflet (`L`) sigue en el
    // <head> y `leaflet.css` intacto. Quedan aquí: `swView` (router, lote k) con
    // su `if (v === 'topology') loadTopology();`, el listener global de Escape
    // de #user-dd (misc) y los helpers compartidos esc/icon/setIconText/core.

    // ── CONSOLA WEB (TERMINAL SSH JSON) & GESTOR SFTP → views/terminal.js + views/sftp.js (Lote I) ──
    // El bloque completo viajó contiguo, en dos archivos y sin cambios de
    // comportamiento: views/terminal.js (constantes TERM_*, estado
    // _term/_termSessions/_currentTermTab/_sftp*, switchTermTab, term*,
    // _term*, cmdOpenConsole) y views/sftp.js (_formatBytes/_formatTime,
    // _sftp*, sftp*). Queda AQUÍ el despacho de swView (router, lote k), que
    // lee `_currentTermTab`/`_fitAddon` (bindings let de terminal.js) y llama
    // `termPopulateDevices`, todo en tiempo de llamada contra window.

    // ── REPORTES SLA → views/reports.js (Lote D) ──
    // Viajó: repState/repGroupsCache, _ymd, _repDur (usa _fmtUptime de aquí),
    // repBanner, _repGroups, loadReports, _repFillRestored, repPreset,
    // repWindow, repParams, repQS, repApply, repRender y `_donut` (antes en
    // el bloque PERFORMANCE). Quedan aquí (compartidos, regla 2): `_detText`
    // (NetBox, SFTP, discovery y Admin) y `_repTs` (el wizard de discovery).

    function _detText(body, fallback) {
      const d = body && body.detail;
      if (typeof d === 'string') return d;
      if (d && typeof d === 'object') return d.motivo || d.mensaje || d.error || JSON.stringify(d);
      if (body && body.message) return body.message;
      return fallback || '';
    }
    function _repTs(s) {
      if (!s) return '—';
      const d = new Date(s);
      return isNaN(d.getTime()) ? esc(String(s)) : d.toLocaleString();
    }

    // ── WIZARD DE AUTO-DISCOVERY (contrato docs/handoffs/discovery.md) ─────
    // Viajó a views/discovery.js (Lote F): estado let dw + const DW_ESTADO,
    // _dwEstadoText, _dwResList y dwOpen/dwClose/dwReset/dwRender/dwPanelError/
    // dwStep1/dwToggleAllSources/dwScan/dwStep2/dwToStep3/dwStep3/dwSel/
    // dwSelAll/dwToStep4/dwStep4/dwImport. Consumidores: botón dwOpen del
    // markup de #v-devices y onclicks generados (window). dwImport llama
    // loadInventory/loadDev (views/devices.js) y _repTs/_detText QUEDAN inline
    // (compartidas con reports/admin/sftp, mapa §2.1).

    // ── OLA 4: SITIOS REMOTOS + RETENCIÓN → views/sites.js y views/retention.js (Lote D) ──
    // Viajó el bloque (los scopes RBAC ya estaban en views/admin.js, Lote C):
    // estado _r4Epochs/_retPreview, alias muertos _det40/retDet, _ageHuman,
    // _isoLocal (una copia única, en views/sites.js; retention la consume en
    // tiempo de llamada) y loadSites/siteToggle/siteHist/loadRetention/ret*.
    // Queda aquí `_fmtBytes` (regla 2 con TRAMPA §7.1 del mapa): hay DOS
    // declaraciones en el archivo y por hoisting gana esta segunda; la del
    // bloque PERFORMANCE está sombreada y su cuerpo NO es equivalente. Su
    // única llamada es loadRetention (views/retention.js), resuelta en tiempo
    // de llamada: si esta declaración viajara, ganaría la sombreada y
    // cambiaría el formateo de tamaños.

    function _fmtBytes(n) {
      const v = Number(n) || 0;
      if (v < 1024) return `${v} B`;
      if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`;
      if (v < 1024 * 1024 * 1024) return `${(v / 1048576).toFixed(1)} MB`;
      return `${(v / 1073741824).toFixed(2)} GB`;
    }



