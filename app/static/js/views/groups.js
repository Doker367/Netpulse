/**
 * NetPulse NMS — vista Grupos de dispositivos (views/groups.js).
 *
 * Extraída del <script> inline de app/static/dashboard.html (Tarjeta 10D-A,
 * lote 1) sin cambio de comportamiento: loadGroups, grpDevs, createGroup,
 * mismas líneas en el mismo orden relativo.
 *
 * NO SE MOVIERON (compartidas, prohibido duplicar): __isAdmin() y npGray(),
 * que viven en el bloque de Groups del inline pero los usan Compliance,
 * NetBox, Admin (userScope), Sites, Retention y Reports. El contrato de la
 * API de grupos sigue documentado junto a ellas en dashboard.html.
 *
 * Depende (en tiempo de llamada, no de carga): core.js (A, h, toast, __t) e
 * inline (esc, escAttr, __isAdmin, npGray). Handlers que las resuelven por
 * window: swView('groups') → loadGroups(); onclick generados por
 * loadGroups() → grpDevs(...), createGroup(). Scripts clásicos sin
 * defer/async ya cargaron al momento del click (orden estático).
 * Top-level sin ejecución.
 */

    async function loadGroups() {
      const el = document.getElementById('groups-content'); if (!el || !t) return;
      el.innerHTML = '<div class="empty"><div class="sp"></div></div>';
      try {
        const r = await fetch(`${A}/groups`, { headers: h(), signal: AbortSignal.timeout(15000) });
        if (r.status === 403) { el.innerHTML = npGray(`<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg> ${__t('grp_admin_required')}`); return; }
        if (!r.ok) { el.innerHTML = npGray(`${__t('np_unverified')} · HTTP ${r.status}`); return; }
        const groups = await r.json();
        const list = Array.isArray(groups) ? groups : [];
        const rows = list.map(g => `<tr>
          <td style="font-weight:500">${esc(g.name || '?')}</td>
          <td style="font-size:11px;color:var(--text-secondary)">${esc(g.description || '—')}</td>
          <td><span class="badge" style="background:var(--surface)">${Number(g.device_count ?? 0)}</span></td>
          <td><button class="btn btn-o btn-xs" onclick="grpDevs('${escAttr(g.name || '')}')">${__t('grp_view_devices')}</button></td>
        </tr>`);
        el.innerHTML = `
          ${rows.length ? `
            <div style="border:1px solid var(--border);border-radius:6px;margin-bottom:12px"><table><tr><th>${__t('grp_col_group')}</th><th>${__t('grp_col_desc')}</th><th>${__t('grp_col_devices')}</th><th></th></tr>${rows.join('')}</table></div>
            <div id="grp-devs" style="margin-bottom:12px"></div>`
            : npGray(`<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20 17a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3.9a2 2 0 0 1-1.69-.9l-.81-1.2a2 2 0 0 0-1.67-.9H8a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2Z"/><path d="M2 8v11a2 2 0 0 0 2 2h14"/></svg> ${__t('grp_empty')}`)}
          <div class="sc">
            <div class="sl"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 5v14M5 12h14"/></svg> ${__t('grp_add_title')}</div>
            ${__isAdmin()
            ? `<div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">
                  <input id="grp-name" placeholder="${__t('grp_name_ph')}" style="flex:1;min-width:140px">
                  <input id="grp-desc" placeholder="${__t('grp_desc_ph')}" style="flex:2;min-width:200px">
                  <button class="btn btn-p btn-sm" onclick="createGroup()"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 5v14M5 12h14"/></svg> ${__t('grp_add_btn')}</button>
                </div>`
            : `<div style="font-size:11px;color:var(--text-ter);margin-top:8px"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg> ${__t('grp_admin_required')}</div>`}
          </div>`;
      } catch (e) { el.innerHTML = npGray(`${__t('np_unverified')}: ${esc(e.message)}`); }
    }
    async function grpDevs(name) {
      const el = document.getElementById('grp-devs'); if (!el || !name) return;
      el.innerHTML = `<div class="bt"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m12 3 9 5v8l-9 5-9-5V8zM3 8l9 5 9-5M12 13v8M7.5 5.5l9 5"/></svg> ${esc(name)} · …</div>`;
      try {
        const r = await fetch(`${A}/groups/${encodeURIComponent(name)}/devices`, { headers: h(), signal: AbortSignal.timeout(15000) });
        // 404 = grupo vacío o inexistente: estado honesto, no error genérico.
        if (r.status === 404) { el.innerHTML = `<div class="bt" style="color:var(--text-ter)"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m12 3 9 5v8l-9 5-9-5V8zM3 8l9 5 9-5M12 13v8M7.5 5.5l9 5"/></svg> ${esc(name)} · ${__t('grp_devices_none')}</div>`; return; }
        if (!r.ok) { el.innerHTML = `<div class="bt" style="color:var(--text-ter)">${__t('np_unverified')} · HTTP ${r.status}</div>`; return; }
        const devs = await r.json();
        const list = Array.isArray(devs) ? devs : [];
        el.innerHTML = `<div class="bt"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m12 3 9 5v8l-9 5-9-5V8zM3 8l9 5 9-5M12 13v8M7.5 5.5l9 5"/></svg> ${esc(name)} · ${list.length} ${list.length === 1 ? 'equipo' : 'equipos'}</div>
          ${list.map(d => `<span class="tag" style="font-size:10px;padding:3px 8px;margin:2px">${esc(d.id || d.hostname || '?')}</span>`).join('')}`;
      } catch (e) { el.innerHTML = `<div class="bt" style="color:var(--text-ter)">${__t('np_unverified')}: ${esc(e.message)}</div>`; }
    }
    async function createGroup() {
      const name = document.getElementById('grp-name')?.value.trim();
      const desc = document.getElementById('grp-desc')?.value.trim() || '';
      if (!name) { toast(__t('grp_name_missing'), 'error'); return; }
      try {
        const r = await fetch(`${A}/groups`, { method: 'POST', headers: h(), body: JSON.stringify({ name, description: desc }) });
        if (r.status === 403) { toast('<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg> ' + __t('grp_admin_required'), 'error'); return; }
        if (r.status === 409) { const d = await r.json().catch(() => ({})); toast(esc(d.detail || __t('grp_exists')), 'error'); return; }
        if (!r.ok) { toast(`${__t('grp_create_fail')} · HTTP ${r.status}`, 'error'); return; }
        toast(`<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg> ${__t('grp_created').replace('{g}', name)}`, 's');
        loadGroups();
      } catch (e) { toast(`${__t('grp_create_fail')}: ${e.message}`, 'error'); }
    }
