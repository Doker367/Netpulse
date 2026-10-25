/**
 * NetPulse NOC — vista NetBox (views/netbox.js).
 *
 * Extraída del <script> inline de app/static/dashboard.html (Tarjeta 10D-D,
 * lote 4) sin cambio de comportamiento: el bloque completo NETBOX (contrato
 * docs/handoffs/netbox-ui.md) — constantes NB_SVG_* y loadNetbox + funciones
 * nb* exclusivas — en el mismo orden relativo.
 *
 * Lo que NO viajó (verificado por grep, regla 2):
 * - `__isAdmin` / `npGray` (bloque Groups del inline) — también los usan
 *   Compliance, Reports, Sites y Retention → quedan; esta vista los consume
 *   en tiempo de llamada (banner 403 / SOLO admin intacto).
 * - `_detText` (bloque Reports del inline) — compartido (SFTP, discovery,
 *   Admin, Reports) → queda.
 * - `A`, `h`, `t`, `__t`, `toast`, `esc`, `escAttr` — core.js o inline.
 *
 * Handlers por window: botón Refresh de #v-netbox, swView('netbox'),
 * nbSyncConfirm/nbSyncRun/nbSyncCancel resueltos desde el HTML que loadNetbox
 * genera. Script clásico sin defer/async (order de carga: core → vistas
 * previas → reports → retention → sites → netbox → inline; sus símbolos solo
 * se resuelven en tiempo de llamada).
 */

    // ── NETBOX (contrato docs/handoffs/netbox-ui.md; /api/netbox/* es SOLO admin) ──
    // GET  /api/netbox/status → checklist de configuración (no contacta a NetBox; 200 aunque no esté configurado).
    // POST /api/netbox/sync   → {status: 'ok'|'partial', solicitados, creados[], actualizados[], sin_cambios,
    //                            saltados: [{device_id, motivo}], errores: [{device_id, detalle}],
    //                            inventario_total, netbox_total}.
    // Reglas honestas: un fallo (503/5xx/4xx) muestra el detail literal y NUNCA "0 importados" como éxito;
    // NetBox sin configurar no es un error del panel: banner gris + botón de sync deshabilitado.
    // GET /api/netbox/sites sigue cargándose como lectura accesoria (tabla de sites si NetBox responde).
    const NB_SVG_LOCK = '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>';
    const NB_SVG_SYNC = '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M21 2v6h-6"/><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M3 22v-6h6"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/></svg>';
    const NB_SVG_OK = '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--green)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><polyline points="20 6 9 17 4 12"/></svg>';
    const NB_SVG_NO = '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--text-ter)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';

    async function loadNetbox() {
      const el = document.getElementById('netbox-content'); if (!el || !t) return;
      if (!__isAdmin()) { el.innerHTML = npGray(`${NB_SVG_LOCK} ${__t('nb_admin_required')}`); return; }
      el.innerHTML = '<div class="empty"><div class="sp"></div></div>';

      // 1) Checklist de configuración (no contacta a NetBox; responde al instante).
      let st = null;
      try {
        const r = await fetch(`${A}/netbox/status`, { headers: h(), signal: AbortSignal.timeout(10000) });
        if (r.status === 403) { el.innerHTML = npGray(`${NB_SVG_LOCK} ${__t('nb_admin_required')}`); return; }
        if (!r.ok) {
          const b = await r.json().catch(() => null);
          el.innerHTML = npGray(`${__t('nb_status_fail')} · ${esc(_detText(b, `HTTP ${r.status}`))}`);
          return;
        }
        st = await r.json();
      } catch (e) { el.innerHTML = npGray(`${__t('nb_status_fail')} · ${esc(e.message)}`); return; }
      if (!st || typeof st !== 'object' || Array.isArray(st)) { el.innerHTML = npGray(__t('nb_status_fail')); return; }

      const cfg = st.configurado === true, tok = st.tiene_token === true;
      const row = (ok, label, extra) => `<div style="display:flex;align-items:center;gap:8px;padding:3px 0">${ok ? NB_SVG_OK : NB_SVG_NO}<span style="font-size:12px;color:var(--text-secondary)">${label}</span>${extra || ''}</div>`;
      const checkRows = row(cfg, __t('nb_check_cfg'))
        + row(tok, __t('nb_check_token'))
        + row(cfg, __t('nb_check_url'), `<span style="font-family:var(--mono);font-size:11px;color:${cfg ? 'var(--text)' : 'var(--text-ter)'}">${esc(st.url_enmascarada || '—')}</span>`);

      const noHace = Array.isArray(st.no_hace) ? st.no_hace.map(x => `<li style="margin:2px 0;word-break:break-word">${esc(x)}</li>`).join('') : '';
      const scopeLine = typeof st.alcance === 'string' ? st.alcance : '';
      const whatDetail = (scopeLine || noHace) ? `
            <details style="margin-top:10px;font-size:11px;color:var(--text-secondary)">
              <summary style="cursor:pointer;font-weight:600">${__t('nb_what_detail')}</summary>
              ${scopeLine ? `<div style="margin-top:8px;word-break:break-word"><b>${__t('nb_scope')}</b><div style="margin-top:2px">${esc(scopeLine)}</div></div>` : ''}
              ${noHace ? `<div style="margin-top:8px"><b>${__t('nb_no_hace')}</b><ul style="margin:4px 0 0 16px;padding:0">${noHace}</ul></div>` : ''}
            </details>` : '';

      const syncBtn = cfg
        ? `<button class="btn btn-p btn-sm" onclick="nbSyncConfirm()">${NB_SVG_SYNC} ${__t('nb_sync')}</button>`
        : `<button class="btn btn-p btn-sm" disabled style="opacity:.55;cursor:not-allowed" title="${escAttr(__t('nb_sync_disabled_hint'))}" aria-disabled="true">${NB_SVG_SYNC} ${__t('nb_sync')}</button>`;
      // Sin configuración: mensaje literal del backend + variables a definir; el botón queda inactivo.
      const cfgBanner = cfg ? '' : `<div role="status" style="background:var(--bg2);border:1px solid var(--border-s);border-left:3px solid var(--text-ter);border-radius:var(--radius);padding:12px 14px;margin-top:10px">
            <b style="font-size:12px">${__t('nb_not_configured_title')}</b>
            <div style="font-size:12px;color:var(--text-secondary);margin-top:4px;word-break:break-word">${esc(st.mensaje || __t('nb_not_configured_title'))}</div>
            <div style="font-size:11px;color:var(--text-ter);margin-top:6px">${__t('nb_not_configured_vars')}</div>
          </div>`;

      el.innerHTML = `
        <div class="sc" style="margin-bottom:12px">
          <div class="sl"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg> ${__t('nb_what_title')}</div>
          <div style="font-size:12px;color:var(--text-secondary);margin-top:6px;line-height:1.5">${__t('nb_what_text')}</div>
          ${whatDetail}
        </div>
        <div class="sc" style="margin-bottom:12px">
          <div class="sl">${__t('nb_checklist_title')}</div>
          <div style="margin-top:6px">${checkRows}</div>
          ${cfgBanner}
        </div>
        <div class="sc" style="margin-bottom:12px">
          <div class="sl">${NB_SVG_SYNC} ${__t('nb_sync_title')}</div>
          <div style="font-size:11px;color:var(--text-secondary);margin-top:6px">${__t('nb_sync_desc')}</div>
          <div style="margin-top:8px">${syncBtn}</div>
          <div id="nb-panel" style="display:none;margin-top:12px;padding:14px;background:var(--panel);border:1px solid var(--accent);border-radius:var(--radius)"></div>
          <div id="nb-result" style="margin-top:10px"></div>
        </div>
        <div class="sc" id="nb-sites">
          <div class="sl">${__t('nb_sites_title')}</div>
          <div id="nb-sites-body" style="font-size:11px;color:var(--text-ter);margin-top:6px">${__t('nb_sites_checking')}</div>
        </div>`;

      nbLoadSites();
    }

    // Lectura accesoria: GET /api/netbox/sites solo cuando NetBox responde; en otro caso, nota honesta con detail literal.
    async function nbLoadSites() {
      const bodyEl = document.getElementById('nb-sites-body'); if (!bodyEl || !t) return;
      let sites = null, note = '';
      try {
        const r = await fetch(`${A}/netbox/sites`, { headers: h(), signal: AbortSignal.timeout(20000) });
        const b = (r.ok) ? await r.json() : await r.json().catch(() => null);
        if (r.status === 403) note = __t('nb_admin_required');
        else if (!r.ok) note = esc(_detText(b, `${__t('nb_unavailable')} · HTTP ${r.status}`));
        else sites = b;
      } catch (e) { note = `${__t('nb_unavailable')} · ${esc(e.message)}`; }
      const sitesList = sites !== null ? (Array.isArray(sites) ? sites : []) : null;
      const statusLine = sitesList !== null
        ? `<span style="width:10px;height:10px;border-radius:50%;background:var(--green);display:inline-block;margin-right:4px;vertical-align:middle"></span> ${__t('nb_online')}`
        : `<span style="width:10px;height:10px;border-radius:50%;background:var(--text-ter);display:inline-block;margin-right:4px;vertical-align:middle"></span> <span style="color:var(--text-ter)">${note || __t('np_unverified')}</span>`;
      const rows = (sitesList || []).map(s => `<tr>
        <td style="font-weight:500">${esc(s.name || '?')}</td>
        <td style="font-size:11px">${esc(s.status || '—')}</td>
        <td style="font-size:11px;color:var(--text-secondary)">${esc(s.region || '—')}</td>
        <td><span class="badge" style="background:var(--surface)">${Number(s.device_count ?? 0)}</span></td>
      </tr>`);
      bodyEl.innerHTML = `<div>${statusLine}</div>`
        + (sitesList !== null
          ? (rows.length
            ? `<div style="border:1px solid var(--border);border-radius:6px;margin-top:10px"><table><tr><th>${__t('nb_col_site')}</th><th>${__t('nb_col_status')}</th><th>${__t('nb_col_region')}</th><th>${__t('nb_col_devices')}</th></tr>${rows.join('')}</table></div>`
            : `<div style="margin-top:10px">${__t('nb_sites_empty')}</div>`)
          : '');
    }

    // Confirmación inline (mismo patrón que el panel de la bulk-bar), sin prompt() ciego.
    function nbSyncConfirm() {
      const p = document.getElementById('nb-panel'); if (!p) return;
      p.innerHTML = `
        <div class="bt">${NB_SVG_SYNC} ${__t('nb_sync_confirm_title')}</div>
        <div style="font-size:11px;color:var(--text-secondary);margin-top:6px">${__t('nb_sync_desc')}</div>
        <div style="display:flex;gap:8px;margin-top:10px">
          <button class="btn btn-p btn-sm" onclick="nbSyncRun()">${NB_SVG_SYNC} ${__t('nb_sync_run')}</button>
          <button class="btn btn-o btn-sm" onclick="nbSyncCancel()">${__t('nb_cancel')}</button>
        </div>`;
      p.style.display = 'block';
    }
    function nbSyncCancel() {
      const p = document.getElementById('nb-panel');
      if (p) { p.style.display = 'none'; p.innerHTML = ''; }
    }

    function nbList(label, itemsHtml) {
      return `<details style="margin-top:8px"><summary style="cursor:pointer;font-size:12px;font-weight:600;color:var(--text-secondary)">${label}</summary>
        <ul style="margin:6px 0 0 16px;padding:0;font-size:11px;color:var(--text-secondary);word-break:break-word">${itemsHtml}</ul></details>`;
    }

    function nbRenderResult(d) {
      const res = document.getElementById('nb-result'); if (!res) return;
      // Campos del contrato nuevo; fallback a los heredados (created/updated/skipped) sin inventar datos.
      const created = Array.isArray(d.creados) ? d.creados : null;
      const updated = Array.isArray(d.actualizados) ? d.actualizados : null;
      const skipped = Array.isArray(d.saltados) ? d.saltados : null;
      const errores = Array.isArray(d.errores) ? d.errores : null;
      const nCreados = created ? created.length : (Number(d.created) || 0);
      const nAct = updated ? updated.length : (Number(d.updated) || 0);
      const sinCamb = Number(d.sin_cambios) || 0;
      const nSal = skipped ? skipped.length : (Number(d.skipped) || 0);
      const nErr = errores ? errores.length : 0;
      const partial = (d.status === 'partial') || (!d.status && (nSal + nErr) > 0);

      const chip = (col, n, label) => `<span style="display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;background:var(--bg2);border:1px solid var(--border-s);font-size:11px;color:var(--text-secondary)"><b style="font-size:13px;color:${col}">${n}</b>${label}</span>`;
      const chips = chip('var(--green)', nCreados, __t('nb_count_created'))
        + chip('var(--accent)', nAct, __t('nb_count_updated'))
        + chip('var(--text-secondary)', sinCamb, __t('nb_count_unchanged'))
        + chip(nSal ? 'var(--orange)' : 'var(--text-ter)', nSal, __t('nb_count_skipped'))
        + chip(nErr ? 'var(--red)' : 'var(--text-ter)', nErr, __t('nb_count_errors'));

      const bits = [];
      if (created && created.length) bits.push(nbList(__t('nb_list_created').replace('{n}', String(created.length)), created.map(id => `<li>${esc(id)}</li>`).join('')));
      if (updated && updated.length) bits.push(nbList(__t('nb_list_updated').replace('{n}', String(updated.length)), updated.map(id => `<li>${esc(id)}</li>`).join('')));
      if (skipped && skipped.length) bits.push(nbList(__t('nb_list_skipped').replace('{n}', String(skipped.length)), skipped.map(s => `<li><span style="font-family:var(--mono)">${esc(s.device_id || '—')}</span> — ${esc(s.motivo || '—')}</li>`).join('')));
      if (errores && errores.length) bits.push(nbList(__t('nb_list_errors').replace('{n}', String(errores.length)), errores.map(s => `<li><span style="font-family:var(--mono)">${esc(s.device_id || '—')}</span> — ${esc(s.detalle || '—')}</li>`).join('')));

      const head = partial
        ? `<div style="background:rgba(245,158,11,.12);border:1px solid var(--border-s);border-left:3px solid var(--orange);border-radius:var(--radius);padding:12px 14px">
            <b style="font-size:12px;color:var(--orange)">${__t('nb_res_partial').replace('{n}', String(nSal + nErr))}</b>
            <div style="font-size:11px;color:var(--text-secondary);margin-top:4px">${__t('nb_res_partial_hint')}</div>
          </div>`
        : `<div style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--green)">${NB_SVG_OK} ${__t('nb_res_ok')}</div>`;
      const totals = (d.inventario_total !== undefined && d.inventario_total !== null)
        ? `<div style="font-size:11px;color:var(--text-ter);margin-top:8px">${__t('nb_totals').replace('{a}', String(Number(d.inventario_total) || 0)).replace('{b}', String(Number(d.netbox_total) || 0))}</div>`
        : '';
      res.innerHTML = `${head}<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">${chips}</div>${bits.join('')}${totals}`;
      return { partial };
    }

    function nbFailBanner(det) {
      return `<div role="alert" style="background:var(--bg2);border:1px solid var(--border-s);border-left:3px solid var(--text-ter);border-radius:var(--radius);padding:12px 14px">
        <b style="font-size:12px">${__t('nb_sync_fail')}</b>
        <div style="font-size:12px;color:var(--text-secondary);margin-top:4px;word-break:break-word">${esc(det || '')}</div>
      </div>`;
    }

    async function nbSyncRun() {
      nbSyncCancel();
      const res = document.getElementById('nb-result'); if (!res) return;
      res.innerHTML = `<div style="display:flex;align-items:center;gap:10px;font-size:12px;color:var(--text-secondary)"><span class="sp" style="width:14px;height:14px"></span>${__t('nb_sync_running')}</div>`;
      try {
        const r = await fetch(`${A}/netbox/sync`, { method: 'POST', headers: h(), signal: AbortSignal.timeout(120000) });
        const b = await r.json().catch(() => null);
        if (r.status === 403) { res.innerHTML = npGray(`${NB_SVG_LOCK} ${__t('nb_admin_required')}`); return; }
        if (!r.ok) {
          // 503 (no configurado / NetBox caído), 500 inventario local, 401/403 de NetBox:
          // detail literal, nada pintado como éxito.
          res.innerHTML = nbFailBanner(_detText(b, `HTTP ${r.status}`));
          return;
        }
        const out = nbRenderResult(b);
        const plain = out.partial
          ? __t('nb_res_partial').replace('{n}', String((Array.isArray(b.saltados) ? b.saltados.length : (Number(b.skipped) || 0)) + (Array.isArray(b.errores) ? b.errores.length : 0)))
          : `${__t('nb_res_ok')}: ${Number(b.creados ? b.creados.length : (Number(b.created) || 0))} ${__t('nb_count_created')} · ${Number(b.actualizados ? b.actualizados.length : (Number(b.updated) || 0))} ${__t('nb_count_updated')}`;
        toast(esc(plain), out.partial ? 'error' : 's');
      } catch (e) {
        res.innerHTML = nbFailBanner(`${__t('nb_unavailable')}: ${e.message}`);
      }
    }
