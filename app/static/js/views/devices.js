/**
 * NetPulse NMS — vista Dispositivos / Inventario (views/devices.js).
 *
 * Extraída del <script> inline de app/static/dashboard.html (Tarjeta 10D-F,
 * Lote f) sin cambio de comportamiento. Bloques, en el mismo orden relativo
 * que en el inline original:
 *   1. `loadDev` — el latido canónico de inventario (30 s desde init/_npTick;
 *      reemplaza el array `devices` entero, pisa los selects #cmp-a/#cmp-b de
 *      Compare con selectedIndex=1 y llama buildSchedForms de Backups en cada
 *      pasada, tal cual — trampa §7.6 del mapa, tarjeta aparte).
 *   2. Bulk operations de la grilla del overview: tB, tB2, updB, bClear,
 *      bulkPanel, bulkClosePanel, bulkReset, bulkSummary, bCmd, bCmdRun,
 *      bPing y bFacts.
 *   3. `delDev` (DELETE /devices; único llamador: onclick="delDev(sel.id)"
 *      del modal #dp del overview, resuelto contra window en el click).
 *   4. DEVICE MANAGER (V-INVENTORY): _invContainer, toggleInvAuth,
 *      toggleInvCli, estado let _invGroupsCache + _invGroupEls,
 *      _invGroupSetManual, invGroupChanged, invRefreshGroups, invReadGroup,
 *      invPreloadGroup, addInventoryDevice (grupo del alta Ola 9: envía
 *      `group`, 403 literal del backend y aviso de equipo sin grupo —
 *      Tarjeta 9B, lógica intacta) y loadInventory.
 *      Tarjeta 12E: loadInventory pinta el inventario PAGINADO en cliente
 *      (INV_PAGE_SIZES 50/100, controles #inv-search / #inv-page-size /
 *      #inv-pager inyectados en #inv-list) y filtra sobre el conjunto completo
 *      (_invRows), no sobre la página visible. GET /api/devices no cambia.
 *
 * Lo que NO viajó (verificado por grep, regla 2):
 * - `selD` (modal #dp): consumidores inline residuales — listener del #grid
 *   y del #ops-focus-items del dashboard (quedan inline). loadInventory
 *   sigue emitiendo onclick="selD(...)": se resuelve por window en el click.
 * - `fDev` (filtro del buscador) — renderD la llama tras cada render del
 *   grid (renderD queda inline, lote h).
 * - familia de render del overview: renderD, updStats, updateDashboardHero,
 *   loadDonuts, refreshAlertSummary, loadH, buildSchedForms — dashboard
 *   (lote h). buildSchedForms/populateCmdDevices siguen escribiendo DOM de
 *   Backups/Commands desde el latido, en tiempo de llamada.
 * - Estado compartido del inline: devices, sel, bSel, bMode, inventoryError
 *   (binding del entorno léxico global: loadDev/bulk lo escriben, renderD y
 *   el listener del grid lo leen).
 * - esc/escAttr/setIconText (inline) y core.js (A, h, t, toast, __t, _npTick).
 *   logout (core.js) invalida _invGroupsCache en tiempo de llamada (mismo
 *   patrón que repState en el Lote D).
 *
 * Handlers por window: botón Refresh de #v-devices, onblur/onchange/oninput
 * del formulario de alta, onclick del bulk-bar de #v-dashboard, swView
 * ('devices' y 'terminal'), onclick de las filas de inventario (selD) y los
 * setTimeout que relanzan loadInventory/loadDev tras alta/borrado.
 * Script clásico sin defer/async.
 */

    // ── i18n de la paginación del inventario (Tarjeta 12E) ──
    // Se declara aquí, no en core.js: la tarjeta solo toca la vista. I18N es un
    // const de top-level de core.js (entorno léxico global compartido) y este
    // script se evalúa después, así que el Object.assign llega a tiempo.
    Object.assign(I18N.en, {
      inv_page_search: 'Filter by ID, host, driver, type or group…',
      inv_page_size: 'per page',
      inv_page_size_aria: 'Rows per page',
      inv_page_word: 'Page',
      inv_page_of: 'of',
      inv_page_devices: 'devices',
      inv_page_prev: 'Previous',
      inv_page_next: 'Next',
      inv_page_prev_aria: 'Previous page',
      inv_page_next_aria: 'Next page',
      inv_page_none: 'No device matches this filter.'
    });
    Object.assign(I18N.es, {
      inv_page_search: 'Filtrar por ID, host, driver, tipo o grupo…',
      inv_page_size: 'por página',
      inv_page_size_aria: 'Filas por página',
      inv_page_word: 'Página',
      inv_page_of: 'de',
      inv_page_devices: 'equipos',
      inv_page_prev: 'Anterior',
      inv_page_next: 'Siguiente',
      inv_page_prev_aria: 'Página anterior',
      inv_page_next_aria: 'Página siguiente',
      inv_page_none: 'Ningún equipo coincide con este filtro.'
    });

    async function loadDev() {
      try {
        let newDevs = [];
        // Cargar inventario y estado de salud en paralelo para mostrar el estado real desde el inicio
        const [rDev, rHealth] = await Promise.allSettled([
          fetch(`${A}/devices`, { headers: h() }),
          fetch(`${A}/devices/health`, { headers: h() })
        ]);

        if (rDev.status === 'fulfilled' && rDev.value.ok) {
          inventoryError = false;
          const inv = await rDev.value.json();
          if (Array.isArray(inv)) newDevs = inv.map(i => ({
            id: i.id,
            hostname: i.host || i.ip || i.hostname,
            driver: i.driver || 'ios',
            port: i.port || 22,
            type: i.type || i.protocol || 'device',
            protocol: i.protocol || 'ssh',
            group: i.group || null, // necesario para precargar el grupo en el alta (Tarjeta 9B/9C)
            tags: i.tags || []
          }));
        } else {
          inventoryError = true;
          devices.forEach(d => { d._online = null; });
          renderD(); updStats();
          return;
        }

        let hl = {};
        if (rHealth.status === 'fulfilled' && rHealth.value.ok) {
          try { hl = await rHealth.value.json(); } catch (e) { }
        }

        // Aplicar estado de salud real y latencia inmediatamente
        newDevs.forEach(nd => {
          const od = devices.find(x => x.id === nd.id);
          if (nd.id in hl) {
            const val = hl[nd.id];
            if (typeof val === 'object' && val !== null) {
              // online=null = sin medición fresca (desconocido), no offline.
              nd._online = val.online === null ? null : !!val.online;
              nd._lat = nd._online === true ? (val.latency_ms != null ? val.latency_ms + 'ms' : '<1ms') : '—';
              nd._rx = nd._online === true ? (val.rx || '0.0 Mb/s') : '—';
              nd._tx = nd._online === true ? (val.tx || '0.0 Mb/s') : '—';
            } else {
              nd._online = !!val;
              nd._lat = nd._online ? (od?._lat || '<1ms') : '—';
              nd._rx = nd._online ? (od?._rx || '0.0 Mb/s') : '—';
              nd._tx = nd._online ? (od?._tx || '0.0 Mb/s') : '—';
            }
          } else {
            // Sin respuesta de salud no equivale a equipo desconectado.
            nd._online = null;
            nd._lat = '—';
            nd._rx = '—';
            nd._tx = '—';
          }
          if (od) {
            nd._cpu = od._cpu; nd._mem = od._mem; nd._dsk = od._dsk;
          }
        });

        devices = newDevs;
        renderD(); loadDonuts(); updStats(); buildSchedForms();
        ['cmp-a', 'cmp-b'].forEach(id => {
          const s = document.getElementById(id); if (s) {
            s.innerHTML = devices.map(d => `<option value="${d.id}">${d.id}</option>`).join('');
            if (s.options.length > 1) s.selectedIndex = 1
          }
        });
      } catch (e) {
        inventoryError = true;
        devices.forEach(d => { d._online = null; });
        renderD(); updStats();
      }
    }

    function tB() {
      bMode = !bMode; setIconText(document.getElementById('bulk-t'), bMode ? '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="m8 12 3 3 5-6"/></svg> Multi' : '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="3" y="3" width="18" height="18" rx="2"/></svg> Multi');
      document.querySelectorAll('.bulk-cb').forEach(c => c.style.display = bMode ? 'block' : 'none');
      if (!bMode) { bSel.clear(); renderD(); document.getElementById('bulk-bar').style.display = 'none'; bulkReset() }
    }
    function tB2(id, chk) {
      chk ? bSel.add(id) : bSel.delete(id);
      [...document.querySelectorAll('#grid .dc')].find(card => card.dataset.id === id)?.classList.toggle('bulk', chk); updB()
    }
    function updB() {
      const bar = document.getElementById('bulk-bar');
      if (!bSel.size) { bar.style.display = 'none'; bulkReset(); return }
      bar.style.display = 'block'; setIconText(document.getElementById('bulk-t'), `<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m18 14 4 4-4 4M18 2l4 4-4 4M2 18h2.5a6 6 0 0 0 5-3l5-6a6 6 0 0 1 5-3H22M2 6h2.5a6 6 0 0 1 5 3l5 6a6 6 0 0 0 5 3H22"/></svg> ${bSel.size} seleccionado(s)`);
      document.getElementById('bulk-tags').innerHTML = [...bSel].map(id => `<span class="tag">${id}</span>`).join('')
    }
    function bClear() { bSel.clear(); renderD(); document.getElementById('bulk-bar').style.display = 'none'; bulkReset() }

    // ── Bulk: panel de confirmación y feedback honesto ──
    function bulkPanel(html) {
      const p = document.getElementById('bulk-panel');
      if (!p) return;
      p.innerHTML = html;
      p.style.display = 'block';
    }
    function bulkClosePanel() {
      const p = document.getElementById('bulk-panel');
      if (!p) return;
      p.style.display = 'none';
      p.innerHTML = '';
    }
    function bulkReset() {
      bulkClosePanel();
      const r = document.getElementById('bulk-r');
      if (r) r.innerHTML = '';
    }
    function bulkSummary(res, total, ok, fail) {
      res.innerHTML += `<div style="font-size:11px;font-weight:600;margin-top:8px;color:${fail ? 'var(--red)' : 'var(--green)'}">${ok}/${total} ok${fail ? ` · ${fail} fallaron — ver detalles` : ''}</div>`;
    }

    function bCmd() {
      const ids = [...bSel]; if (!ids.length) return;
      bulkReset();
      // Confirmación inline: comando verbatim + chips de destino, sin prompt() ciego.
      bulkPanel(`
        <div class="bt" style="margin-bottom:10px"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M4 17l6-6-6-6"/><path d="M12 19h8"/></svg> Ejecutar comando en ${ids.length} ${ids.length === 1 ? 'equipo' : 'equipos'}</div>
        <form id="bp-form" style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
          <input id="bp-cmd" type="text" placeholder="show version" autocomplete="off" spellcheck="false"
            style="flex:1;min-width:220px;height:34px;background:var(--bg);border:1px solid var(--border);border-radius:var(--radius-sm);padding:0 12px;color:var(--text);font-size:12px;font-family:var(--mono)">
          <button type="submit" class="btn btn-p btn-sm"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><polygon points="6 4 20 12 6 20"/></svg> Ejecutar</button>
          <button type="button" class="btn btn-o btn-sm" onclick="bulkClosePanel()">Cancelar</button>
        </form>
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:10px">${ids.map(id => `<span class="tag" style="font-size:10px;padding:3px 8px">${esc(id)}</span>`).join('')}</div>
        <div style="font-size:10px;color:var(--text-ter);margin-top:8px">Se usa la credencial guardada de cada equipo; el comando viaja verbatim a una sola línea.</div>`);
      document.getElementById('bp-cmd')?.focus();
      document.getElementById('bp-form')?.addEventListener('submit', e => {
        e.preventDefault();
        bCmdRun((e.target.elements['bp-cmd'].value || '').trim());
      });
    }
    async function bCmdRun(cmd) {
      if (!cmd) { toast('Escribe un comando para ejecutar', 'error'); return; }
      const ids = [...bSel]; if (!ids.length) return;
      bulkClosePanel();
      const res = document.getElementById('bulk-r'); if (!res) return;
      res.innerHTML = `<div class="bt"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M4 17l6-6-6-6"/><path d="M12 19h8"/></svg> Ejecutando en ${ids.length} ${ids.length === 1 ? 'equipo' : 'equipos'}…</div>`;
      let ok = 0, fail = 0;
      for (const id of ids) {
        let bad = false, err = '', out = '';
        try {
          const r = await fetch(`${A}/command/${encodeURIComponent(id)}`, { method: 'POST', headers: h(), body: JSON.stringify({ commands: [cmd] }), signal: AbortSignal.timeout(60000) });
          const d = await r.json().catch(() => ({}));
          bad = !r.ok || !!d.error || !!d.results?.[0]?.error;
          if (bad) err = d.detail || (typeof d.error === 'string' ? d.error : typeof d.results?.[0]?.error === 'string' ? d.results[0].error : '') || `HTTP ${r.status}`;
          else out = (d.results?.[0]?.output || '').trim();
        } catch (e) { bad = true; err = e.message; }
        if (bad) fail++; else ok++;
        res.innerHTML += `<pre style="font-size:10px;margin-top:4px;padding:8px;border-radius:6px;background:var(--bg);border:1px solid ${bad ? 'var(--red)' : 'var(--border)'}"><span style="color:${bad ? 'var(--red)' : 'var(--accent-h)'};font-weight:600">${bad ? '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>' : '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg>'} ${esc(id)}$ </span>${esc(cmd)}\n${bad ? esc(err || 'falló') : esc(out.length > 400 ? out.substring(0, 400) + '…' : out)}</pre>`;
      }
      bulkSummary(res, ids.length, ok, fail);
      if (fail) toast(`<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M4 17l6-6-6-6"/><path d="M12 19h8"/></svg> ${ok}/${ids.length} ok · ${fail} fallaron — ver detalles en la barra`, 'error');
      else toast(`<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M4 17l6-6-6-6"/><path d="M12 19h8"/></svg> ${ok}/${ids.length} ejecutados`, 's');
    }
    async function bPing() {
      const ids = [...bSel];
      if (!ids.length) { toast('Selecciona al menos un equipo', 'error'); return; }
      // Ping desde el servidor NetPulse hacia el host de cada equipo seleccionado
      // (antes usaba un target fijo 172.30.0.10 no explicable en la UI).
      const byId = new Map(devices.map(d => [d.id, d.hostname || d.host || d.ip]));
      bulkClosePanel();
      const res = document.getElementById('bulk-r'); if (!res) return;
      res.innerHTML = `<div class="bt"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 12m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0"/><path d="M16.24 7.76a6 6 0 0 1 0 8.49"/><path d="M7.76 16.24a6 6 0 0 1 0-8.49"/></svg> Pingueando ${ids.length} ${ids.length === 1 ? 'equipo' : 'equipos'}…</div>`;
      let ok = 0, fail = 0;
      for (const id of ids) {
        const target = byId.get(id);
        if (!target) { fail++; res.innerHTML += `<div style="font-size:10px;padding:6px 0;border-bottom:1px solid var(--border)"><span style="color:var(--accent-h);font-weight:500"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> ${esc(id)}</span>: sin IP conocida en el inventario</div>`; continue; }
        try {
          const r = await fetch(`${A}/system/ping?target=${encodeURIComponent(target)}&count=3`, { method: 'POST', headers: h(), signal: AbortSignal.timeout(12000) });
          const d = await r.json();
          if (d && d.success) ok++; else fail++;
          res.innerHTML += `<div style="font-size:10px;padding:6px 0;border-bottom:1px solid var(--border)"><span style="color:var(--accent-h);font-weight:500">${esc(id)}</span> → ${esc(target)}: ${d && d.success ? '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg> ' + Number(d.rtt_avg || 0).toFixed(1) + ' ms · ' + Math.round(d.packet_loss || 0) + '% pérdida' : '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> sin respuesta'}</div>`;
        } catch (e) { fail++; res.innerHTML += `<div style="color:var(--red);font-size:10px">${esc(id)} → ${esc(target)}: ${esc(e.message)}</div>` }
      }
      bulkSummary(res, ids.length, ok, fail);
      if (fail) toast(`<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 12m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0"/><path d="M16.24 7.76a6 6 0 0 1 0 8.49"/><path d="M7.76 16.24a6 6 0 0 1 0-8.49"/></svg> ${ok}/${ids.length} respondieron · ${fail} sin respuesta`, 'error');
      else toast(`<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 12m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0"/><path d="M16.24 7.76a6 6 0 0 1 0 8.49"/><path d="M7.76 16.24a6 6 0 0 1 0-8.49"/></svg> ${ok}/${ids.length} respondieron`, 's');
    }
    async function bFacts() {
      const ids = [...bSel];
      if (!ids.length) { toast('Selecciona al menos un equipo', 'error'); return; }
      bulkClosePanel();
      const res = document.getElementById('bulk-r'); if (!res) return;
      res.innerHTML = `<div class="bt"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M12 11h4"/><path d="M12 16h4"/><path d="M8 11h.01"/><path d="M8 16h.01"/></svg> Facts de ${ids.length} ${ids.length === 1 ? 'equipo' : 'equipos'}…</div>`;
      try {
        const r = await fetch(`${A}/bulk/facts`, { method: 'POST', headers: h(), body: JSON.stringify(ids) });
        const d = await r.json();
        const rows = (d.results || []).filter(rx => rx);
        const got = rows.filter(rx => rx.data && Object.keys(rx.data).length).length;
        res.innerHTML = `<div class="bt"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M12 11h4"/><path d="M12 16h4"/><path d="M8 11h.01"/><path d="M8 16h.01"/></svg> Facts de ${ids.length} ${ids.length === 1 ? 'equipo' : 'equipos'}</div><table style="margin-top:4px;width:100%"><tr><th>Device</th><th>OS</th><th>Modelo</th></tr>
      ${rows.map(rx => {
          const okRow = rx.data && Object.keys(rx.data).length;
          return `<tr><td style="font-weight:500">${esc(rx.device_id || rx.id || '?')}</td><td style="font-size:10px">${esc(((rx.data || {}).os_version || '?').substring(0, 22))}</td><td>${esc((rx.data || {}).model || '?')}</td></tr>`;
        }).join('')}</table>${got < ids.length ? `<div style="font-size:10px;color:var(--yellow);margin-top:6px">Sin datos en ${ids.length - got} de ${ids.length} equipo(s)</div>` : ''}`;
        if (got < ids.length) toast(`<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M12 11h4"/><path d="M12 16h4"/><path d="M8 11h.01"/><path d="M8 16h.01"/></svg> Facts: ${got}/${ids.length} con datos`, 'error');
      } catch (e) { res.innerHTML = `<div style="color:var(--red)">${esc(e.message)}</div>`; toast(`<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M12 11h4"/><path d="M12 16h4"/><path d="M8 11h.01"/><path d="M8 16h.01"/></svg> Facts falló: ${e.message}`, 'error'); }
    }

    async function delDev(id) {
      if (!confirm(`¿Estás seguro de borrar el dispositivo ${id}?`)) return;
      try {
        const r = await fetch(`${A}/devices/${encodeURIComponent(id)}`, { method: 'DELETE', headers: h() });
        if (r.ok || r.status === 204) { toast(`<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg> Dispositivo ${id} eliminado`, 's'); closeD(); setTimeout(() => { loadInventory(); loadDev(); }, 500); }
        else { const d = await r.json(); toast(`<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> Error: ${d.detail || 'Fallo al borrar'}`, 'error'); }
      } catch (e) { toast(`<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> Error: ${e.message}`, 'error'); }
    }

    // ── DEVICE MANAGER (V-INVENTORY) ──
    function _invContainer(el) {
      return el ? (el.closest('#add-device-panel') || el.closest('#v-devices') || document) : document;
    }

    function toggleInvAuth(el) {
      const container = _invContainer(el);
      const protoEl = container.querySelector('#inv-protocol') || document.getElementById('inv-protocol');
      const p = protoEl ? protoEl.value : 'ssh';
      const sshEl = container.querySelector('#inv-auth-ssh') || document.getElementById('inv-auth-ssh');
      const snmpEl = container.querySelector('#inv-auth-snmp') || document.getElementById('inv-auth-snmp');
      if (sshEl) sshEl.style.display = (p === 'ssh' || p === 'telnet') ? 'block' : 'none';
      if (snmpEl) snmpEl.style.display = (p === 'snmp') ? 'block' : 'none';
      if (p !== 'snmp') {
        const cliEl = container.querySelector('#inv-cli-fields');
        if (cliEl) cliEl.style.display = 'none';
      }
    }

    function toggleInvCli(el) {
      const container = _invContainer(el);
      const sel = container.querySelector('#inv-cli-transport') || document.getElementById('inv-cli-transport');
      const fields = container.querySelector('#inv-cli-fields') || document.getElementById('inv-cli-fields');
      if (!fields) return;
      fields.style.display = (sel && (sel.value === 'telnet' || sel.value === 'ssh')) ? 'flex' : 'none';
    }

    // ── GRUPO EN EL ALTA (Tarjeta 9B/9C) ──
    // El <select> del formulario "Nuevo Dispositivo" usa la MISMA llamada que la vista
    // Grupos: GET /api/groups (200 para viewer con [{name, description, device_count}]).
    // Si falla: opción vacía + aviso gris "lista no verificable" + texto libre.
    // El grupo vacío NO bloquea el alta: solo avisa (los usuarios con scope no podrán operarlo).
    let _invGroupsCache = null; // null = sin cargar; false = no verificable; array = nombres
    function _invGroupEls() {
      return {
        sel: document.getElementById('inv-group'),
        txt: document.getElementById('inv-group-text'),
        note: document.getElementById('inv-group-note'),
        empty: document.getElementById('inv-group-empty-note')
      };
    }
    function _invGroupSetManual(txtVal) {
      const els = _invGroupEls();
      if (!els.sel) return;
      els.sel.value = '__manual__';
      if (els.txt) els.txt.value = txtVal || '';
      invGroupChanged();
    }
    function invGroupChanged() {
      const els = _invGroupEls();
      if (!els.sel) return;
      const manual = els.sel.value === '__manual__';
      if (els.txt) els.txt.style.display = manual ? '' : 'none';
      const val = manual ? ((els.txt && els.txt.value.trim()) || '') : els.sel.value;
      if (els.empty) els.empty.style.display = val ? 'none' : '';
    }
    async function invRefreshGroups() {
      const els = _invGroupEls();
      if (!els.sel || _invGroupsCache) { invGroupChanged(); return; }
      // Conservar lo que el operador ya escribió entre recargas de la vista.
      const keepManual = els.sel.value === '__manual__';
      const keepTxt = (els.txt && els.txt.value) || '';
      const keepSel = els.sel.value || '';
      try {
        const r = await fetch(`${A}/groups`, { headers: h(), signal: AbortSignal.timeout(15000) });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        const groups = await r.json();
        const names = (Array.isArray(groups) ? groups : []).map(g => g.name).filter(Boolean);
        _invGroupsCache = names;
        els.sel.innerHTML = `<option value="">${esc(__t('inv_group_none'))}</option>`
          + names.map(n => `<option value="${escAttr(n)}">${esc(n)}</option>`).join('')
          + `<option value="__manual__">${esc(__t('inv_group_manual_opt'))}</option>`;
        if (els.txt) els.txt.style.display = 'none';
        if (els.note) els.note.style.display = 'none';
        if (keepManual && keepTxt.trim()) _invGroupSetManual(keepTxt);
        else if (keepSel && names.includes(keepSel)) els.sel.value = keepSel;
      } catch (e) {
        _invGroupsCache = false; // lista de grupos no verificable: select vacío + texto libre
        els.sel.innerHTML = `<option value="">${esc(__t('inv_group_none'))}</option>`
          + `<option value="__manual__">${esc(__t('inv_group_manual_opt'))}</option>`;
        if (els.txt) els.txt.style.display = '';
        if (els.note) els.note.style.display = '';
      }
      invGroupChanged();
    }
    function invReadGroup(container) {
      const get = id => (container.querySelector('#' + id) || document.getElementById(id));
      const sel = get('inv-group');
      const txt = get('inv-group-text');
      if (!sel) return '';
      const val = (sel.value === '__manual__') ? ((txt && txt.value.trim()) || '') : (sel.value || '').trim();
      return val;
    }
    function invPreloadGroup() {
      // El formulario es upsert (ID existente → 409 → PUT). Si el ID coincide con un
      // equipo del inventario, precargamos su grupo actual para no descartarlo al actualizar.
      const idEl = document.getElementById('inv-id');
      if (!idEl || !idEl.value.trim()) return;
      const d = devices.find(x => x && x.id === idEl.value.trim());
      if (!d || !d.group) return;
      const els = _invGroupEls();
      if (!els.sel) return;
      const has = Array.from(els.sel.options).some(o => o.value === d.group);
      if (_invGroupsCache === false) _invGroupSetManual(d.group);
      else if (has) { els.sel.value = d.group; invGroupChanged(); }
      else if (_invGroupsCache === null) invRefreshGroups().then(() => {
        const after = _invGroupEls();
        if (after.sel && Array.from(after.sel.options).some(o => o.value === d.group)) { after.sel.value = d.group; invGroupChanged(); }
        else _invGroupSetManual(d.group);
      });
      else _invGroupSetManual(d.group);
    }

    async function addInventoryDevice(triggerEl) {
      const container = _invContainer(triggerEl);
      const getVal = (id, fallback = '') => {
        const el = container.querySelector('#' + id) || document.getElementById(id);
        return el ? (el.value.trim ? el.value.trim() : el.value) : fallback;
      };

      const protocol = getVal('inv-protocol', 'ssh');
      const driver = getVal('inv-driver', 'ios');
      const data = {
        id: getVal('inv-id'),
        hostname: getVal('inv-host'),
        driver: driver,
        protocol: protocol
      };

      // Grupo (DeviceCreate.group / DeviceUpdate.group ya lo aceptan en el backend).
      // Vacío = alta sin grupo: aviso veraz (no bloquea), los usuarios con scope no podrán operarlo.
      const groupVal = invReadGroup(container);
      if (groupVal) data.group = groupVal;
      else toast(`${__t('inv_group_empty_warn')}`, 'w');

      if (!data.id || !data.hostname) {
        toast('ID / Hostname e IP son obligatorios', 'error');
        return;
      }

      if (data.protocol === 'ssh' || data.protocol === 'telnet') {
        data.username = getVal('inv-user', 'manager') || 'manager';
        data.password = getVal('inv-pass', '');
        data.enable_password = getVal('inv-secret', '');
        data.port = parseInt(getVal('inv-port') || (data.protocol === 'telnet' ? 23 : 22));
      } else if (data.protocol === 'snmp' || data.driver === 'snmp') {
        data.protocol = 'snmp';
        data.snmp_ro = getVal('inv-snmp-ro', 'public') || 'public';
        data.community = data.snmp_ro;
        data.port = 161;
        data.username = 'snmp';
        data.password = '';
        const cliT = (getVal('inv-cli-transport', '') || '').toLowerCase();
        if (cliT === 'telnet' || cliT === 'ssh') {
          data.cli_transport = cliT;
          data.cli_port = parseInt(getVal('inv-cli-port') || (cliT === 'telnet' ? 23 : 22));
          data.username = getVal('inv-cli-user', '') || 'admin';
          data.password = getVal('inv-cli-pass', '');
          const nmt = getVal('inv-netmiko-type', '');
          if (nmt) data.netmiko_device_type = nmt;
        }
      }

      // Si el usuario seleccionó driver snmp
      if (data.driver === 'snmp') {
        data.protocol = 'snmp';
        if (!data.port) data.port = 161;
      }

      toast('<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M5 2h14M5 22h14M7 2v4l10 12v4M17 2v4L7 18v4"/></svg> Registrando dispositivo...', 'i');
      try {
        let r = await fetch(`${A}/devices`, {
          method: 'POST',
          headers: h(),
          body: JSON.stringify(data)
        });
        // Upsert: si el ID ya existe (409) actualizamos los campos (incl. modo dual SNMP+CLI)
        if (r.status === 409) {
          r = await fetch(`${A}/devices/${encodeURIComponent(data.id)}`, {
            method: 'PUT',
            headers: h(),
            body: JSON.stringify(data)
          });
        }
        if (r.ok) {
          toast('<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg> Equipo añadido correctamente.', 's');
          // Limpiar forms y recargar
          const idEl = container.querySelector('#inv-id') || document.getElementById('inv-id');
          const hostEl = container.querySelector('#inv-host') || document.getElementById('inv-host');
          if (idEl) idEl.value = '';
          if (hostEl) hostEl.value = '';
          const gSel = container.querySelector('#inv-group') || document.getElementById('inv-group');
          if (gSel) gSel.value = '';
          const gTxt = container.querySelector('#inv-group-text') || document.getElementById('inv-group-text');
          if (gTxt) gTxt.value = '';
          invGroupChanged();
          const panel = document.getElementById('add-device-panel');
          if (panel) panel.style.display = 'none';
          setTimeout(() => { loadInventory(); loadDev(); }, 1000);
        } else {
          const d = await r.json();
          let errDetail = d.detail || d.error || 'Error desconocido';
          if (Array.isArray(d.detail)) {
            errDetail = d.detail.map(e => `${e.loc ? e.loc.join('.') : ''}: ${e.msg}`).join(', ');
          }
          // 403 por scope: el detail literal del backend gana, y se dice claro que el
          // equipo NO se creó (estado honesto, nunca "equipo añadido" ante un fallo).
          if (r.status === 403) {
            toast(`<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg> ${__t('inv_403_nocreate')} ${esc(errDetail)}`, 'error');
            return;
          }
          toast(`<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> Error: ${errDetail}`, 'error');
        }
      } catch (e) {
        toast('<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> Fetch error: ' + e.message, 'error');
      }
    }

    // ── PAGINACIÓN DEL INVENTARIO (Tarjeta 12E: escala a 500 equipos) ──
    // Paginación client-side: el backend sigue devolviendo todo el inventario
    // (GET /api/devices no cambia) y aquí se pinta solo una página. La búsqueda
    // filtra SIEMPRE sobre el conjunto completo recibido (_invRows), nunca sobre
    // la página visible. Con ≤50 equipos se ve igual que antes, más el pie.
    const INV_PAGE_SIZES = [50, 100];
    let _invRows = [];       // último inventario completo recibido
    let _invPage = 1;
    let _invPageSize = 50;
    let _invQuery = '';

    function _invMatches() {
      const q = _invQuery.trim().toLowerCase();
      if (!q) return _invRows;
      return _invRows.filter(d => [d.id, d.host, d.ip, d.hostname, d.driver, d.type, d.group]
        .filter(Boolean).join(' ').toLowerCase().includes(q));
    }
    function _invPages() {
      return Math.max(1, Math.ceil(_invMatches().length / _invPageSize));
    }
    function invGoPage(p) {
      const total = _invPages();
      const n = Math.min(Math.max(1, Math.round(Number(p) || 1)), total);
      if (n === _invPage) return;
      _invPage = n;
      _invRenderRows();
    }
    function invNextPage() { invGoPage(_invPage + 1); }
    function invPrevPage() { invGoPage(_invPage - 1); }
    function invSetPageSize(n) {
      const size = INV_PAGE_SIZES.includes(Number(n)) ? Number(n) : 50;
      if (size === _invPageSize) return;
      _invPageSize = size;
      _invPage = 1;
      _invRenderRows();
    }
    function invSearch(v) {
      const q = v || '';
      if (q === _invQuery) return;
      _invQuery = q;
      _invPage = 1;
      _invRenderRows();
    }

    function _invRowHtml(d) {
      return `<tr style="border-bottom:1px solid var(--border-s);transition:background .15s" onmouseover="this.style.background='var(--surface-hover)'" onmouseout="this.style.background='transparent'">
        <td style="padding:12px 10px;font-weight:600;color:var(--text)">${d.id} <span style="display:inline-block;margin-left:4px;width:6px;height:6px;border-radius:50%;background:${d._online === true || (d._online == null && d.status === 'active') ? 'var(--green)' : d._online === false ? 'var(--red)' : 'var(--text-ter)'}"></span></td>
        <td style="padding:12px 10px;font-family:var(--mono);color:var(--text-secondary)">${d.host || d.ip || d.hostname || '—'}</td>
        <td style="padding:12px 10px;color:var(--text-ter)">${d.port || 22}</td>
        <td style="padding:12px 10px;color:var(--text-secondary)"><span class="badge" style="background:#202124">${(d.driver || 'desconocido').toUpperCase()}</span></td>
        <td style="padding:12px 10px;color:var(--text-ter)">${(d.type || '—')} / ${(d.group || '—')}</td>
        <td style="padding:12px 10px;text-align:right">
          <button class="btn btn-o btn-xs" style="padding:4px 8px;font-size:9px" onclick="selD('${d.id}')">Detalle</button>
        </td>
      </tr>`;
    }

    function _invRenderRows() {
      const wrap = document.getElementById('inv-table-wrap');
      if (!wrap) return;
      const rows = _invMatches();
      const pages = Math.max(1, Math.ceil(rows.length / _invPageSize));
      if (_invPage > pages) _invPage = pages;
      if (_invPage < 1) _invPage = 1;
      const start = (_invPage - 1) * _invPageSize;
      const pageRows = rows.slice(start, start + _invPageSize);

      if (!rows.length) {
        wrap.innerHTML = !_invRows.length
          ? '<div class="empty"><span class="icon"><svg class="ic" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false" style="opacity:0.4;color:var(--text-ter)"><path d="m12 3 9 5v8l-9 5-9-5V8zM3 8l9 5 9-5M12 13v8M7.5 5.5l9 5"/></svg></span><br>El inventario está vacío. Agrega equipos a la izquierda.</div>'
          : `<div class="empty" id="inv-empty">${esc(__t('inv_page_none'))}</div>`;
      } else {
        wrap.innerHTML = `<table style="width:100%;border-collapse:collapse;font-size:12px;text-align:left">
          <thead><tr style="border-bottom:1px solid var(--border);color:var(--text-muted);font-size:10px;text-transform:uppercase;letter-spacing:.5px">
            <th style="padding:12px 10px;font-weight:600">ID / Hostname</th>
            <th style="padding:12px 10px;font-weight:600">Host (IP)</th>
            <th style="padding:12px 10px;font-weight:600">Puerto</th>
            <th style="padding:12px 10px;font-weight:600">Driver</th>
            <th style="padding:12px 10px;font-weight:600">Tipo / Grupo</th>
            <th style="padding:12px 10px;text-align:right">Acciones</th>
          </tr></thead>
          <tbody>${pageRows.map(_invRowHtml).join('')}</tbody>
        </table>`;
      }

      const pager = document.getElementById('inv-pager');
      if (pager) {
        const btn = 'padding:4px 10px;font-size:10px';
        // Pie encima de la tabla (no al final): #inv-list crece con la página y
        // con 50 filas un pie al final quedaría a 2 600 px de scroll.
        pager.innerHTML = `
          <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;margin-bottom:10px;padding-bottom:8px;border-bottom:1px solid var(--border-s)">
            <div style="font-size:11px;color:var(--text-ter)">
              <span data-i18n="inv_page_word">${esc(__t('inv_page_word'))}</span>
              <strong id="inv-page-cur" style="color:var(--text-secondary)">${_invPage}</strong>
              <span data-i18n="inv_page_of">${esc(__t('inv_page_of'))}</span>
              <strong id="inv-page-total" style="color:var(--text-secondary)">${pages}</strong>
              · <strong id="inv-count" style="color:var(--text-secondary)">${pageRows.length}</strong>
              <span data-i18n="inv_page_devices">${esc(__t('inv_page_devices'))}</span>
              <span id="inv-count-all">(${rows.length}${_invQuery.trim() ? ' / ' + _invRows.length : ''})</span>
            </div>
            <div style="display:flex;gap:6px">
              <button type="button" class="btn btn-o" id="inv-prev" style="${btn}" onclick="invPrevPage()" ${_invPage <= 1 ? 'disabled' : ''}
                data-i18n="inv_page_prev" data-i18n-aria="inv_page_prev_aria" aria-label="${escAttr(__t('inv_page_prev_aria'))}">${esc(__t('inv_page_prev'))}</button>
              <button type="button" class="btn btn-o" id="inv-next" style="${btn}" onclick="invNextPage()" ${_invPage >= pages ? 'disabled' : ''}
                data-i18n="inv_page_next" data-i18n-aria="inv_page_next_aria" aria-label="${escAttr(__t('inv_page_next_aria'))}">${esc(__t('inv_page_next'))}</button>
            </div>
          </div>`;
      }
    }

    async function loadInventory() {
      const el = document.getElementById('inv-list'); if (!el) return;
      invRefreshGroups(); // <select> de Grupo del formulario "Nuevo Dispositivo" (falla → fallback)
      // Barra de filtro + contenedor de tabla + pie de paginación: se crean una
      // sola vez para no perder el foco ni el filtro al recargar el inventario.
      if (!document.getElementById('inv-table-wrap')) {
        el.innerHTML = `
          <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:12px">
            <input id="inv-search" type="search" autocomplete="off" spellcheck="false"
              placeholder="${escAttr(__t('inv_page_search'))}" title="${escAttr(__t('inv_page_search'))}"
              data-i18n-title="inv_page_search" oninput="invSearch(this.value)"
              style="flex:1 1 220px;min-width:200px;height:32px;background:var(--bg);border:1px solid var(--border);border-radius:var(--radius-sm);padding:0 10px;color:var(--text);font-size:12px">
            <select id="inv-page-size" onchange="invSetPageSize(this.value)"
              data-i18n-aria="inv_page_size_aria" aria-label="${escAttr(__t('inv_page_size_aria'))}"
              style="flex:0 0 auto;width:88px;height:32px;background:var(--bg);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text);font-size:11px;padding:0 8px">
              ${INV_PAGE_SIZES.map(n => `<option value="${n}">${n}</option>`).join('')}
            </select>
            <span style="font-size:10px;color:var(--text-ter)" data-i18n="inv_page_size">${esc(__t('inv_page_size'))}</span>
          </div>
          <div id="inv-pager"></div>
          <div id="inv-table-wrap"></div>`;
        const sizeSel = document.getElementById('inv-page-size');
        if (sizeSel) sizeSel.value = String(_invPageSize);
        const search = document.getElementById('inv-search');
        if (search) search.value = _invQuery;
      }
      const wrap = document.getElementById('inv-table-wrap');
      if (wrap) wrap.innerHTML = '<div class="sp" style="margin:20px auto"></div>';
      try {
        const r = await fetch(`${A}/devices`, { headers: h() });
        let list = [];
        if (r.ok) list = await r.json();
        if (!r.ok || !Array.isArray(list)) list = devices;

        _invRows = Array.isArray(list) ? list : [];
        _invRenderRows();
      } catch (e) {
        if (wrap) wrap.innerHTML = '<div class="empty"><span class="icon"><svg class="ic" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false" style="opacity:0.4;color:var(--text-ter)"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></span><br>Error cargando inventario: ' + esc(e.message) + '</div>';
        const pager = document.getElementById('inv-pager');
        if (pager) pager.innerHTML = '';
      }
    }
