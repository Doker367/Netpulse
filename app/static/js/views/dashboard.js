/**
 * NetPulse NOC — vista Dashboard (portada #v-dashboard): hero, KPIs, grid de
 * equipos, donuts de recursos y latidos (views/dashboard.js).
 *
 * Extraída del <script> inline de app/static/dashboard.html (Tarjeta 10D-H,
 * lote h, el corazón), sin cambio de comportamiento: líneas en el mismo orden
 * relativo que en el monolito (loadDonuts 3277 <
 * refreshAlertSummary 3322 < init 3341 < loadH 3370 < fDev 3396 < renderD
 * 3410 < listener #grid 3452 < goToOverviewDevices 3457 <
 * showAddDevicePanel 3463 < listener #ops-focus-items 3470 <
 * updateDashboardHero 3476 < updStats 3524).
 *
 * Carga: script clásico sin defer/async, DESPUÉS de views/device-detail.js y
 * ANTES del inline residual (scripts al final del body, tras todo el markup:
 * los DOS listeners top-level que viajaron aquí — #grid y #ops-focus-items —
 * pueden registrarse porque el DOM ya existe, igual que cuando vivían en el
 * inline, nunca en <head>).
 *
 * Contenido nombre a nombre:
 * - Estado del portal: `alertSummary` (null = fuente sin verificar: semántica
 *   de tres estados §7 del mapa, no convertir null en 0), `overviewState` e
 *   `inventoryError` QUEDAN declarados en el bloque de estado del inline
 *   (L3026-3028 del archivo pre-lote): los escribe loadDev (views/devices.js,
 *   inventoryError) y los lee setLang (core.js, overviewState), así que no son
 *   de un único archivo (regla 4). Aquí se resuelven en tiempo de llamada,
 *   igual que devices/sel/bSel/bMode.
 * - `loadDonuts` — métricas CPU/RAM/disco por tarjeta del grid (un fetch por
 *   equipo con AbortController de 2,5 s; ids rcpu-/rmem-/rdsk-/rlat-/rrx-/rtx-).
 * - `refreshAlertSummary` — latido 45 s: GET /alerts/active → badge del
 *   sidebar y del header; llama updStats solo si el hero ya está computado.
 * - `init` — arranque autenticado (lo llama enterSession, core.js): setLang,
 *   restaura la vista del hash, 4 latidos `_npTick` (loadH 10 s, loadBk 60 s,
 *   refreshAlertSummary 45 s, loadDev 30 s), #info-dev, setTimeout de
 *   loadDonuts a 1,5 s y setInterval(loadDonuts, 120000) con guard de
 *   #v-devices. Los ids de los ticks NO se guardan y no se cancelan:documento
 *   preexistente del mapa (7,12) NO se "arregla" aquí.
 * - `loadH` — latido 10 s de salud (GET /devices/health) → renderD + updStats.
 * - `fDev` — filtro del buscador (#sb) sobre el grid.
 * - `renderD` — pinta el grid (#grid); lee devices/sel/bSel/bMode del bloque
 *   de estado compartido del inline (tiempo de llamada) y llama fDev al
 *   final. Los onclick que genera (tB2 bulk, selD detalle) resuelven por
 *   window (tB2 en views/devices.js, selD en device-detail.js).
 *   Tarjeta 12E: con >100 equipos pinta solo DASH_CARD_LIMIT tarjetas y añade
 *   #dash-limit-note (aviso honesto); con ≤100 no cambia nada. `loadDonuts`
 *   pide facts solo de las tarjetas pintadas.
 * - listener top-level #grid — delegación: click en tarjeta abre selD salvo
 *   en modo bulk; consulta bMode (inline).
 * - `goToOverviewDevices` y `showAddDevicePanel` — acciones del hero (swView
 *   del lote k, tiempo de llamada).
 * - listener top-level #ops-focus-items — delegación del hero: selD /
 *   goToOverviewDevices / swView('alerts').
 * - `updateDashboardHero` — hero bilingüe (lo llama setLang, core.js).
 * - `updStats` — KPIs (#stats/#info-dev-header/#hdr-network-dot); escribe
 *   overviewState y termina llamando setLang(currentLang) en cada pasada.
 *
 * Cross-call verificado por grep (todo en tiempo de llamada, nada a nivel de
 * evaluación): loadDev (devices.js) llama renderD/loadDonuts/updStats en su
 * latido de 30 s; loadMonitoring (monitoring.js) llama refreshAlertSummary;
 * setLang y enterSession (core.js) llaman updateDashboardHero e init;
 * loadInventory (devices.js) emite onclick="selD(...)" (device-detail.js).
 * El detalle de equipo vive en views/device-detail.js, cargado ANTES de este
 * archivo.
 */

    // ── ESCALA (Tarjeta 12E): límite de tarjetas en la grilla del dashboard ──
    // Con >100 equipos pintar 500 tarjetas cuesta ~50 ms por render (latido de
    // salud cada 10 s) y deja 25 000 nodos en el DOM. Aquí se pintan como mucho
    // 100 y se avisa con un texto honesto; con ≤100 equipos el comportamiento es
    // EXACTAMENTE el de antes (sin nota, sin recorte).
    // La búsqueda global (#sb, fDev) sigue ocultando sobre las tarjetas pintadas
    // (coste O(tarjetas), no O(inventario)) y, además, cuenta las coincidencias
    // del inventario completo para poder avisar en la nota.
    const DASH_CARD_LIMIT = 100;
    Object.assign(I18N.en, {
      ov_limited_show: 'Showing',
      ov_limited_of: 'of',
      ov_limited_devices: 'devices (current filter applied)',
      ov_limited_rest: '— use Devices for the rest.',
      ov_limited_open: 'Open full inventory',
      ov_limited_nohit: 'None of the visible cards matches — search the full inventory in Devices.'
    });
    Object.assign(I18N.es, {
      ov_limited_show: 'Mostrando',
      ov_limited_of: 'de',
      ov_limited_devices: 'equipos (según el filtro actual)',
      ov_limited_rest: '— usa Equipos para ver el resto.',
      ov_limited_open: 'Abrir inventario completo',
      ov_limited_nohit: 'Ninguna de las tarjetas visibles coincide — busca en todo el inventario desde Equipos.'
    });


    // ── DEVICE PARAMS ──
    async function loadDonuts() {
      // Solo se piden recursos de los equipos que tienen tarjeta en el DOM: con
      // >100 equipos la grilla está limitada, y pedir 500 recursos NAPALM cada
      // 2 min saturaba el servidor sin mostrar nada (medido en la tarjeta 12E).
      const rendered = devices.filter(d => document.getElementById(`rcpu-${d.id}`));
      rendered.forEach(async d => {
        const dC = document.getElementById(`rcpu-${d.id}`);
        const dM = document.getElementById(`rmem-${d.id}`);
        const dD = document.getElementById(`rdsk-${d.id}`);
        const rL = document.getElementById(`rlat-${d.id}`);
        const rRx = document.getElementById(`rrx-${d.id}`);
        const rTx = document.getElementById(`rtx-${d.id}`);

        // Si el dispositivo está apagado, limpiar métricas y no consultar facts para evitar timeouts
        if (d._online !== true) {
          d._cpu = '—'; d._mem = '—'; d._dsk = '—';
          d._lat = '—'; d._rx = '—'; d._tx = '—';
          if (dC) dC.textContent = '—';
          if (dM) dM.textContent = '—';
          if (dD) dD.textContent = '—';
          if (rL) rL.textContent = '—';
          if (rRx) rRx.textContent = '—';
          if (rTx) rTx.textContent = '—';
          return;
        }

        try {
          const controller = new AbortController();
          const timer = setTimeout(() => controller.abort(), 2500);
          const rr = await fetch(`${A}/facts/${d.id}/resources?_t=${Date.now()}`, { headers: h(), signal: controller.signal });
          clearTimeout(timer);
          if (rr.ok) {
            const rd = await rr.json();
            d._cpu = Math.round(rd.cpu || 0) + '%';
            if (rd.memory_total) d._mem = Math.round(((rd.memory_used || rd.memory_total - rd.memory_free) / rd.memory_total) * 100) + '%';
            if (rd.hdd_total) d._dsk = Math.round((rd.hdd_total - rd.hdd_free) / rd.hdd_total * 100) + '%';

            if (dC) dC.textContent = d._cpu;
            if (dM && d._mem) dM.textContent = d._mem;
            if (dD && d._dsk) dD.textContent = d._dsk;
          }
        } catch (e) { }

        if (rL && d._lat) rL.textContent = d._lat;
        if (rRx && d._rx) rRx.textContent = d._rx;
        if (rTx && d._tx) rTx.textContent = d._tx;
      });
    }

    async function refreshAlertSummary() {
      if (!t) return;
      try {
        const response = await fetch(`${A}/alerts/active`, { headers: h(), signal: AbortSignal.timeout(10000) });
        if (!response.ok) throw new Error('alertas no disponibles');
        const result = await response.json();
        if (result.status !== 'success') throw new Error('Prometheus no disponible');
        alertSummary = Number(result.firing) || 0;
      } catch (e) { alertSummary = null; }
      const count = alertSummary === null ? '—' : String(alertSummary);
      document.getElementById('hdr-alert-count').textContent = count;
      const dot = document.getElementById('hdr-alert-dot');
      dot.style.background = alertSummary === null ? 'var(--text-ter)' : alertSummary > 0 ? 'var(--red)' : 'var(--green)';
      const badge = document.querySelector('.sidebar-item[onclick*="alerts"] .badge');
      if (badge) { badge.textContent = count; badge.style.background = alertSummary > 0 ? 'var(--red)' : 'var(--surface)'; }
      document.getElementById('hdr-alerts').title = alertSummary === null ? 'Prometheus no disponible; alertas sin verificar' : 'Alertas activas de Prometheus';
      if (overviewState) updStats();
    }

    async function init() {
      setLang(currentLang); // Auto-apply persisted language
      // Restaurar la vista de la URL (p. ej. '#devices' tras un refresco a mitad de incidente).
      const hv = decodeURIComponent(location.hash.slice(1));
      if (hv && document.getElementById(`v-${hv}`)) swView(hv);
      void refreshAlertSummary();
      await loadDev(); await loadBk();
      // Los latidos corren solo con sesión activa (_npTick): tras logout quedan en no-ops.
      _npTick(loadH, 10000); _npTick(loadBk, 60000);
      _npTick(refreshAlertSummary, 45000);
      _npTick(loadDev, 30000);
      document.getElementById('info-dev').textContent = devices.length
      // Cargar recursos de hardware tras inicio
      setTimeout(() => loadDonuts(), 1500);
      setInterval(() => { if (document.getElementById('v-devices')?.classList.contains('active')) loadDonuts() }, 120000);
    }

    async function loadH() {
      try {
        const r = await fetch(`${A}/devices/health`); if (!r.ok) throw new Error('salud no disponible'); const hl = await r.json();
        for (const d of devices) {
          if (d.id in hl) {
            const val = hl[d.id];
            if (typeof val === 'object' && val !== null) {
              // online=null significa "sin medición fresca": ni verde ni rojo.
              d._online = val.online === null ? null : !!val.online;
              d._lat = d._online === true ? (val.latency_ms != null ? val.latency_ms + 'ms' : '<1ms') : '—';
              d._rx = d._online === true ? (val.rx || '0.0 Mb/s') : '—';
              d._tx = d._online === true ? (val.tx || '0.0 Mb/s') : '—';
            } else {
              d._online = !!val;
              if (!d._online) { d._lat = '—'; d._rx = '—'; d._tx = '—'; }
            }
          } else {
            d._online = null;
            d._lat = '—'; d._rx = '—'; d._tx = '—';
          }
        }
        renderD(); updStats();
      } catch (e) {
        devices.forEach(d => { d._online = null; d._lat = '—'; d._rx = '—'; d._tx = '—'; });
        renderD(); updStats();
      }
    }
    function _dashHaystack(d) {
      return [d.id, d.hostname, d.driver, d.type, ...(d.tags || [])].join(' ').toLowerCase();
    }
    function fDev() {
      const q = (document.getElementById('sb').value || '').toLowerCase();
      // Recorte por página fija: se oculta en las tarjetas ya pintadas (igual que
      // antes, coste O(tarjetas pintadas), no O(inventario)).
      document.querySelectorAll('#grid .dc').forEach(c => c.classList.toggle('hidden', !(c.dataset.s || '').toLowerCase().includes(q)));
      overLimitNoteTotals(q);
    }

    // Crea (una vez) la nota de recorte del dashboard y actualiza sus números.
    function _dashLimitNote(grid) {
      let note = document.getElementById('dash-limit-note');
      if (!note) {
        note = document.createElement('div');
        note.id = 'dash-limit-note';
        note.setAttribute('role', 'status');
        note.style.cssText = 'display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:0 0 14px;padding:10px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--bg);font-size:11px;color:var(--text-secondary)';
        note.innerHTML = `<span>
            <span data-i18n="ov_limited_show">${esc(__t('ov_limited_show'))}</span>
            <strong id="dash-limit-shown" style="color:var(--text)">100</strong>
            <span data-i18n="ov_limited_of">${esc(__t('ov_limited_of'))}</span>
            <strong id="dash-limit-total" style="color:var(--text)">0</strong>
            <span data-i18n="ov_limited_devices">${esc(__t('ov_limited_devices'))}</span>
            <span data-i18n="ov_limited_rest">${esc(__t('ov_limited_rest'))}</span>
          </span>
          <span id="dash-limit-hint" data-i18n="ov_limited_nohit" style="display:none;flex-basis:100%;color:var(--yellow)">${esc(__t('ov_limited_nohit'))}</span>
          <button type="button" class="btn btn-o btn-xs" onclick="swView('devices')"
            data-i18n="ov_limited_open" style="margin-left:auto;padding:4px 10px;font-size:10px">${esc(__t('ov_limited_open'))}</button>`;
        grid.parentNode.insertBefore(note, grid);
      }
      return note;
    }

    function overLimitNoteTotals(q) {
      const note = document.getElementById('dash-limit-note');
      if (!note) return;
      const matching = q ? devices.filter(d => _dashHaystack(d).includes(q)).length : devices.length;
      const visible = [...document.querySelectorAll('#grid .dc')].filter(c => !c.classList.contains('hidden')).length;
      const shown = document.getElementById('dash-limit-shown');
      const total = document.getElementById('dash-limit-total');
      if (shown) shown.textContent = String(visible);
      if (total) total.textContent = String(matching);
      const hint = document.getElementById('dash-limit-hint');
      if (hint) hint.style.display = (q && visible === 0 && matching > 0) ? '' : 'none';
      // La nota solo tiene sentido si de verdad se está ocultando algo: con un
      // filtro que cabe entero en las tarjetas pintadas no hay nada que avisar.
      note.style.display = (matching > visible) ? 'flex' : 'none';
    }

    function _dashCard(d) {
      return `
    <div class="dc${sel?.id === d.id ? ' selected' : ''}${bSel.has(d.id) ? ' bulk' : ''}${d._online === true ? ' online' : d._online === false ? ' offline' : ''}" style="display:flex;flex-direction:column;align-items:flex-start;position:relative;cursor:pointer" data-id="${escAttr(d.id)}" data-s="${escAttr([d.id, d.hostname, d.driver, d.type, ...(d.tags || [])].join(' '))}">
      <input type="checkbox" class="bulk-cb"${bSel.has(d.id) ? ' checked' : ''} onchange="tB2(this.closest('.dc').dataset.id,this.checked)" onclick="event.stopPropagation()" aria-label="Seleccionar ${escAttr(d.id)}" style="position:absolute;top:16px;right:16px;z-index:var(--z-control);">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;width:100%;padding-right:24px;">
        <div style="display:flex;align-items:center;gap:8px">
          <span aria-hidden="true" style="width:10px;height:10px;border-radius:50%;background:${d._online === true ? 'var(--green)' : d._online === false ? 'var(--red)' : 'var(--text-ter)'}"></span>
          <strong style="font-size:15px;color:var(--text);letter-spacing:-0.2px">${esc(d.id)}</strong>
        </div>
        <span class="device-status" data-i18n="${d._online === true ? 'ov_online' : d._online === false ? 'ov_offline' : 'ov_unverified'}">${__t(d._online === true ? 'ov_online' : d._online === false ? 'ov_offline' : 'ov_unverified')}</span>
      </div>
      <div class="device-endpoint" style="font-family:var(--mono);font-size:11px;color:var(--text-secondary);margin-bottom:16px;width:100%;display:flex;justify-content:space-between;align-items:center;">
        <span style="display:flex;align-items:center;gap:4px;"><svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"></path></svg> ${esc(d.hostname)}:${esc(d.port)}</span>
        <span style="font-family:var(--sans);text-transform:uppercase;background:var(--bg);padding:2px 6px;border-radius:4px;border:1px solid var(--border-s);font-size:9px;">${esc(d.driver)}</span>
      </div>

      <div class="device-metrics" style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:14px;width:100%">
        <div style="background:var(--bg);padding:8px;border-radius:6px;border:1px solid var(--border-s);text-align:center;">
          <div style="font-size:10px;color:var(--text-muted);margin-bottom:2px">CPU</div>
          <div style="font-size:13px;font-weight:600;color:var(--text)" id="rcpu-${escAttr(d.id)}">${d._online ? (d._cpu || '—') : '—'}</div>
        </div>
        <div style="background:var(--bg);padding:8px;border-radius:6px;border:1px solid var(--border-s);text-align:center;">
          <div style="font-size:10px;color:var(--text-muted);margin-bottom:2px">RAM</div>
          <div style="font-size:13px;font-weight:600;color:var(--text)" id="rmem-${escAttr(d.id)}">${d._online ? (d._mem || '—') : '—'}</div>
        </div>
        <div style="background:var(--bg);padding:8px;border-radius:6px;border:1px solid var(--border-s);text-align:center;">
          <div style="font-size:10px;color:var(--text-muted);margin-bottom:2px" data-i18n="ov_disk">${__t('ov_disk')}</div>
          <div style="font-size:13px;font-weight:600;color:var(--text)" id="rdsk-${escAttr(d.id)}">${d._online ? (d._dsk || '—') : '—'}</div>
        </div>
      </div>

      <div class="device-traffic" style="display:flex;justify-content:space-between;width:100%;align-items:center;background:var(--bg);padding:8px 10px;border-radius:6px;border:1px solid var(--border-s);">
        <div style="font-size:11px;color:var(--text-ter);display:flex;gap:12px;">
          <span style="display:flex;align-items:center;gap:4px;" title="Descarga (RX)"><span class="device-traffic-label">RX</span><span style="color:var(--text-secondary)" id="rrx-${escAttr(d.id)}">${d._online ? (d._rx || '0.0 Mb/s') : '—'}</span></span>
          <span style="display:flex;align-items:center;gap:4px;" title="Subida (TX)"><span class="device-traffic-label">TX</span><span style="color:var(--text-secondary)" id="rtx-${escAttr(d.id)}">${d._online ? (d._tx || '0.0 Mb/s') : '—'}</span></span>
        </div>
        <div style="font-size:11px;color:var(--text-ter)"><span data-i18n="card_latency">Latencia:</span> <span style="color:var(--text);font-weight:600" id="rlat-${escAttr(d.id)}">${d._online ? (d._lat || '<1ms') : '—'}</span></div>
      </div>
      <button class="ops-device-open" type="button" data-i18n="ov_detail">Ver detalle</button>
    </div>`;
    }

    function renderD() {
      const grid = document.getElementById('grid');
      if (!grid) return;
      const overLimit = devices.length > DASH_CARD_LIMIT;
      const shown = overLimit ? devices.slice(0, DASH_CARD_LIMIT) : devices;
      grid.innerHTML = shown.map(_dashCard).join('');
      if (overLimit) _dashLimitNote(grid);
      else {
        const stale = document.getElementById('dash-limit-note');
        if (stale) stale.remove();
      }
      fDev();
    }
    document.getElementById('grid').addEventListener('click', event => {
      if (event.target.closest('.bulk-cb')) return;
      const card = event.target.closest('.dc');
      if (card && (event.target.closest('.ops-device-open') || !bMode)) selD(card.dataset.id);
    });
    function goToOverviewDevices() {
      if (!devices.length) { swView('devices'); return; }
      const heading = document.getElementById('ops-devices-heading');
      heading.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'start' });
      heading.focus({ preventScroll: true });
    }
    function showAddDevicePanel() {
      // Un solo formulario de registro: vive en la vista Dispositivos.
      // El panel duplicado del dashboard se retiró; su botón llega aquí con foco.
      swView('devices');
      const input = document.querySelector('#v-devices #inv-id');
      if (input) setTimeout(() => input.focus({ preventScroll: true }), 200);
    }
    document.getElementById('ops-focus-items').addEventListener('click', event => {
      const button = event.target.closest('[data-device]');
      if (button) selD(button.dataset.device);
      else if (event.target.closest('[data-more]')) goToOverviewDevices();
      else if (event.target.closest('[data-alerts]')) swView('alerts');
    });
    function updateDashboardHero() {
      if (!overviewState) return;
      const { networkKey, on, off, unknown } = overviewState;
      const spanish = currentLang === 'es';
      const headline = spanish ? {
        state_ok: alertSummary > 0 ? 'Equipos en línea, hay alertas' : 'Red operativa', state_degraded: 'Red con incidencias',
        state_down: 'Red fuera de línea', state_unknown: 'Estado sin verificar',
        state_empty: 'Sin equipos registrados'
      } : {
        state_ok: alertSummary > 0 ? 'Devices online, alerts active' : 'Network operational', state_degraded: 'Network needs attention',
        state_down: 'Network offline', state_unknown: 'Status unverified',
        state_empty: 'No devices registered'
      };
      const description = inventoryError
        ? (spanish ? 'No se pudo consultar el inventario. Comprueba la API y vuelve a actualizar.' : 'The inventory could not be loaded. Check the API and try again.')
        : networkKey === 'state_unknown'
          ? (spanish ? `${unknown} de ${devices.length} equipos sin verificación de salud.` : `${unknown} of ${devices.length} devices have no verified health status.`)
          : networkKey === 'state_empty'
            ? (spanish ? 'Agrega un equipo para comenzar a supervisar la red.' : 'Add a device to start monitoring the network.')
            : networkKey === 'state_ok'
              ? (spanish ? `${on} de ${devices.length} equipos responden.${alertSummary > 0 ? ` Prometheus reporta ${alertSummary} alertas activas.` : ''}` : `${on} of ${devices.length} devices are responding.${alertSummary > 0 ? ` Prometheus reports ${alertSummary} active alerts.` : ''}`)
              : (spanish ? `${off} de ${devices.length} equipos no responden.` : `${off} of ${devices.length} devices are not responding.`);
      document.getElementById('ops-state').textContent = headline[networkKey];
      document.getElementById('ops-description').textContent = description;
      document.getElementById('ops-primary').textContent = networkKey === 'state_empty'
        ? (spanish ? 'Abrir inventario' : 'Open inventory')
        : off ? (spanish ? 'Revisar equipos' : 'Review devices')
          : (spanish ? 'Ver equipos' : 'View devices');
      document.querySelector('#v-dashboard .ops-hero').dataset.networkState = networkKey;
      const affected = devices.filter(device => device._online === false);
      const count = document.getElementById('ops-focus-count');
      count.textContent = inventoryError ? '—' : affected.length ? String(affected.length) : unknown ? '—' : '0';
      count.style.color = !inventoryError && affected.length ? 'var(--ops-danger)' : 'var(--ops-secondary)';
      const focus = document.getElementById('ops-focus-items');
      if (inventoryError) {
        focus.innerHTML = `<p>${spanish ? 'No se pudo consultar el inventario.' : 'The inventory could not be loaded.'}</p>`;
      } else if (affected.length) {
        focus.innerHTML = affected.slice(0, 2).map(device => `<button class="ops-focus-item" type="button" data-device="${escAttr(device.id)}"><span class="ops-focus-dot" aria-hidden="true"></span><strong>${esc(device.id)}</strong><small>${__t('ov_offline')}</small></button>`).join('')
          + (affected.length > 2 ? `<button type="button" class="ops-focus-more" data-more>${spanish ? `Ver ${affected.length - 2} equipos más` : `View ${affected.length - 2} more devices`}</button>` : '')
          + (unknown ? `<p>${spanish ? `${unknown} equipos más sin verificar.` : `${unknown} more devices unverified.`}</p>` : '');
      } else if (inventoryError || unknown) {
        focus.innerHTML = `<p>${spanish ? 'No se pudo confirmar el estado de los equipos.' : 'Device health could not be verified.'}</p>`;
      } else if (alertSummary > 0) {
        focus.innerHTML = `<p>${spanish ? `Sin equipos desconectados; hay ${alertSummary} alertas activas en Prometheus.` : `No offline devices; Prometheus reports ${alertSummary} active alerts.`}</p><button type="button" class="ops-focus-more" data-alerts>${spanish ? 'Ver alertas' : 'View alerts'}</button>`;
      } else {
        focus.innerHTML = `<p>${devices.length ? (alertSummary === null ? (spanish ? 'Sin equipos desconectados. Las alertas de Prometheus no están verificadas.' : 'No offline devices. Prometheus alerts are unverified.') : (spanish ? 'Ningún equipo fuera de línea.' : 'No devices are offline.')) : (spanish ? 'Los equipos que requieran atención aparecerán aquí.' : 'Devices needing attention will appear here.')}</p>`;
      }
    }
    function updStats() {
      const on = devices.filter(d => d._online === true).length;
      const off = devices.filter(d => d._online === false).length;
      const unknown = devices.length - on - off;
      const networkKey = inventoryError ? 'state_unknown' : !devices.length ? 'state_empty' : unknown ? 'state_unknown' : off === devices.length ? 'state_down' : off ? 'state_degraded' : 'state_ok';
      const networkColor = networkKey === 'state_ok' ? 'var(--green)' : networkKey === 'state_down' ? 'var(--red)' : networkKey === 'state_degraded' ? 'var(--yellow)' : 'var(--text-ter)';
      const header = document.getElementById('info-dev-header');
      header.setAttribute('data-i18n', networkKey);
      header.textContent = __t(networkKey);
      document.getElementById('hdr-network-dot').style.background = networkColor;
      document.getElementById('stats').innerHTML = `
        <div class="ops-stat" role="listitem"><strong class="sv">${inventoryError || unknown ? '—' : off}</strong><span class="sl" data-i18n="ov_offline">${__t('ov_offline')}</span></div>
        <div class="ops-stat" role="listitem"><strong class="sv g">${inventoryError || unknown ? '—' : on}</strong><span class="sl" data-i18n="ov_online">${__t('ov_online')}</span></div>
        <div class="ops-stat" role="listitem"><strong class="sv">${inventoryError ? '—' : devices.length}</strong><span class="sl" data-i18n="ov_devices">${__t('ov_devices')}</span></div>
        <div class="ops-stat" role="listitem"><strong class="sv">${devices.length && !unknown && !inventoryError ? Math.round(on / devices.length * 100) + '%' : '—'}</strong><span class="sl" data-i18n="ov_availability">${__t('ov_availability')}</span></div>
        <div class="ops-stat" role="listitem"><strong class="sv c">${alertSummary === null ? '—' : alertSummary}</strong><span class="sl" data-i18n="ov_alerts">${__t('ov_alerts')}</span></div>`;
      overviewState = { networkKey, on, off, unknown };
      setLang(currentLang);
    }