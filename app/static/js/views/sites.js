/**
 * NetPulse NOC — vista Sitios remotos (views/sites.js).
 *
 * Extraída del <script> inline de app/static/dashboard.html (Tarjeta 10D-D,
 * lote 4) sin cambio de comportamiento: estado `_r4Epochs` (site_id → buffer
 * epoch; nunca se limpia), helpers del bloque OLA 4 y loadSites/`_devState`/
 * `_sitesCard`/siteToggle/siteHist. Líneas en el mismo orden relativo entre sí.
 *
 * Lo que NO viajó (verificado por grep, regla 2):
 * - `_fmtBytes` — hay DOS declaraciones en el inline y por hoisting gana la
 *   segunda (bloque OLA 4, quedada IN SITU); su única llamada es
 *   loadRetention (views/retention.js). Moverla haría ganar la sombreada
 *   (bloque Performance) y cambiaría el formateo. Esta vista no la usa.
 * - `_isoLocal` — definida EN ESTE FICHERO (única copia, sin duplicar) y la
 *   consume también views/retention.js en tiempo de llamada (views/reports →
 *   retention → sites → netbox → inline; todas las llamadas son runtime).
 * - `alDur` (bloque Performance del inline) — la llama `_ageHuman`; también
 *   la usan views/alerts.js (Lote C) → queda en el inline.
 * - `__isAdmin` / `npGray` / `esc` / `escAttr` / `_detText` — core/inline,
 *   tiempo de llamada. El estado recordado 'site' usa `_npState*` de core.
 * - Comentario del bloque OLA 4 copiado en el header (documenta el contrato
 *   /api/remote/*); scopes RBAC ya habían viajado a views/admin.js (Lote C).
 *
 * Nota: `_det40` es alias muerto de `_detText` (mapa §2.3) y viaja tal cual.
 *
 * Depende (en tiempo de llamada): core.js (A, h, t, __t, _npState*), inline
 * (npGray, esc, escAttr, _detText, alDur). Handlers por window: botón Refresh
 * de #v-sites, swView('sites') y onclick generados (siteToggle, siteHist).
 * Script clásico sin defer/async.
 */

    // ── OLA 4: SITIOS REMOTOS + RETENCIÓN + SCOPES RBAC ─────────
    // Cliente puro de /api/remote/* (collectors-site.md §8/§9.3), /api/retention/*
    // (retention.md) y /api/admin/users + /api/groups (rbac-site.md). Sin paths
    // fuera de esos contratos. Regla de oro: site stale nunca verde; ok=false
    // nunca "caído"; null nunca se muestra como 0. Un site provisionado sin
    // heartbeat (contact="never") se pinta gris «sin contacto», sin métricas.

    const _r4Epochs = {}; // site_id → último buffer_epoch visto (detecta "Buffer reiniciado")

    function _det40(body, fb) { return _detText(body, fb); }

    function _ageHuman(s) {
      s = Math.max(0, Math.round(Number(s) || 0));
      if (s < 60) return `${s} s`;
      if (s < 3600) return `${Math.floor(s / 60)} min`;
      return alDur(s);
    }

    function _isoLocal(iso) {
      if (!iso) return '—';
      const d = new Date(iso);
      return isNaN(d.getTime()) ? esc(String(iso)) : d.toLocaleString();
    }

    async function loadSites() {
      const el = document.getElementById('sites-content'); if (!el || !t) return;
      el.innerHTML = '<div class="empty"><div class="sp"></div></div>';
      let body = null, r;
      try {
        r = await fetch(`${A}/remote/sites`, { headers: h(), signal: AbortSignal.timeout(15000) });
      } catch (e) {
        el.innerHTML = npGray(`${__t('np_unverified')} · ${esc(e.message)}`);
        return;
      }
      try { body = await r.json(); } catch (e) { }
      if (r.status === 403) { el.innerHTML = npGray(`<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg> ${esc(_detText(body, 'Acceso denegado'))}`); return; }
      if (!r.ok) { el.innerHTML = npGray(`${__t('np_unverified')} · HTTP ${r.status}${_detText(body, '') ? ' · ' + esc(_detText(body, '')) : ''}`); return; }
      const sites = (body && Array.isArray(body.sites)) ? body.sites : [];
      if (!sites.length) {
        el.innerHTML = npGray(`${__t('sites_empty')}<br><span style="font-size:11px;color:var(--text-ter)">${__t('sites_help')}</span>`);
        return;
      }
      el.innerHTML = sites.map(s => _sitesCard(s)).join('');
      // Estado recordado: si el operador dejó un site expandido se reexpande;
      // los datos se re-cargan como hoy (fetch fresca en siteToggle).
      const lastSite = _npStateGet('site');
      if (lastSite && sites.some(s => String(s.site_id) === String(lastSite))) siteToggle(String(lastSite), true);
    }

    // Estados por device (§9.3, exactos). current=false NUNCA verde.
    function _devState(d) {
      const f = d.freshness || {};
      if (!f.current) return `<td><span class="badge" style="background:var(--surface);color:var(--text-ter)">${__t('sites_st_nodata')}</span><div style="font-size:10px;color:var(--text-ter);margin-top:2px">${__t('sites_last_known')}: ${esc(String(d.reachable === null || d.reachable === undefined ? '—' : (d.reachable ? __t('sites_st_ok') : __t('sites_st_noresp'))))}</div></td>`;
      if (d.ok === false) return `<td><span class="badge" style="background:var(--surface);color:var(--text-ter)">${__t('dw_st_error')}</span><div style="font-size:10px;color:var(--text-ter);margin-top:2px">${esc(d.error || '—')}</div></td>`;
      if (d.reachable) return `<td><span class="badge" style="background:rgba(34,197,94,.15);color:var(--green)">${__t('sites_st_ok')}</span></td>`;
      return `<td><span class="badge" style="background:rgba(239,68,68,.15);color:var(--red)">${__t('sites_st_noresp')}</span><div style="font-size:10px;color:var(--text-ter);margin-top:2px">${esc(d.error || '—')}</div></td>`;
    }

    function _sitesCard(s) {
      const c = s.contact === 'fresh';
      const age = s.last_contact_age_s;
      const contactHtml = c
        ? `<span class="badge" style="background:rgba(34,197,94,.15);color:var(--green)">fresh · ${__t('sites_last_seen')}: ${_ageHuman(age)}</span>`
        : `<span class="badge" style="background:var(--surface);color:var(--text-ter)">${__t('sites_stale')}${age == null ? '' : ': ' + _ageHuman(age)}</span>`;
      const never = s.contact === 'never';
      const cols = s.collectors || [];
      const cl = cols[0] || {};
      const buffs = [];
      const buf = cl.buffer || {};
      const epochKey = `${s.site_id}`;
      if ((buf.pending ?? 0) > 0) buffs.push(`<div style="background:rgba(245,158,11,.12);border:1px solid var(--border-s);border-radius:4px;padding:4px 8px;font-size:11px;margin-top:6px"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M5 2h14M5 22h14M7 2v4l10 12v4M17 2v4L7 18v4"/></svg> ${__t('sites_buf_pending').replace('{n}', String(buf.pending)).replace('{ts}', esc(String(buf.oldest_pending_ts || '—')))}</div>`);
      if ((buf.dropped_total ?? 0) > 0) buffs.push(`<div style="background:var(--bg2);border:1px solid var(--border-s);border-radius:4px;padding:4px 8px;font-size:11px;margin-top:6px">${__t('sites_buf_dropped').replace('{n}', String(buf.dropped_total)).replace('{ts}', esc(String(buf.gap_since || '—')))}</div>`);
      if (cl.paused_reason === 'disk_low') buffs.push(`<div style="background:var(--bg2);border:1px solid var(--border-s);border-radius:4px;padding:4px 8px;font-size:11px;margin-top:6px">${__t('sites_buf_disklow')}</div>`);
      if (cl.time_quality === 'skewed') buffs.push(`<div style="background:var(--bg2);border:1px solid var(--border-s);border-radius:4px;padding:4px 8px;font-size:11px;margin-top:6px"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg> ${__t('sites_skew').replace('{s}', String(cl.clock_skew_s ?? '—'))}</div>`);
      if (_r4Epochs[epochKey] && buf.buffer_epoch && _r4Epochs[epochKey] !== buf.buffer_epoch) buffs.push(`<div style="background:var(--bg2);border:1px solid var(--border-s);border-radius:4px;padding:4px 8px;font-size:11px;margin-top:6px">${__t('sites_epoch')}</div>`);
      if (buf.buffer_epoch) _r4Epochs[epochKey] = buf.buffer_epoch;
      const dvs = s.devices || {};
      return `<div style="background:var(--panel);border:1px solid var(--border);border-radius:var(--radius-lg);padding:16px;margin-bottom:14px">
        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
          <div>
            <b style="font-size:15px">${esc(s.site_id)}</b>
            <span style="font-size:11px;color:var(--text-ter);margin-left:8px">${esc(cl.collector_id || '—')}</span>
          </div>
          ${contactHtml}
          <button class="btn btn-o btn-xs" onclick="siteToggle('${escAttr(s.site_id)}')" id="sites-btn-${escAttr(s.site_id)}">${__t('sites_view_devices')}</button>
        </div>
        ${buffs.join('')}
        ${never
          ? `<div style="font-size:12px;margin-top:10px;color:var(--text-ter)">${__t('sites_never_help')}</div>`
          : `<div style="display:flex;gap:14px;flex-wrap:wrap;font-size:12px;margin-top:10px;color:var(--text-secondary)">
          <span>${__t('sites_st_ok')}: <b style="color:var(--green)">${Number(dvs.reachable ?? 0)}</b></span>
          <span>${__t('sites_st_noresp')}: <b style="color:var(--red)">${Number(dvs.unreachable ?? 0)}</b></span>
          <span>${__t('dw_st_error')}: <b style="color:var(--text-ter)">${Number(dvs.unverifiable ?? 0)}</b></span>
          <span>${__t('sites_unmatched')}: <b style="color:var(--orange)">${Number(dvs.unmatched ?? 0)}</b></span>
        </div>`}
        <div id="sites-devs-${escAttr(s.site_id)}" style="display:none;margin-top:12px"></div>
      </div>`;
    }

    async function siteToggle(siteId, force) {
      const box = document.getElementById(`sites-devs-${siteId}`);
      const btn = document.getElementById(`sites-btn-${siteId}`);
      if (!box) return;
      if (!force && box.style.display !== 'none') {
        box.style.display = 'none';
        _npStateSet('site', ''); // ningún site expandido
        return;
      }
      _npStateSet('site', String(siteId || '')); // se reexpande al volver (Tarjeta 9B/9C)
      box.style.display = 'block';
      box.innerHTML = '<div class="sp" style="margin:12px auto"></div>';
      let body = null, r;
      try {
        r = await fetch(`${A}/remote/sites/${encodeURIComponent(siteId)}/devices?include_unmatched=true`, { headers: h(), signal: AbortSignal.timeout(15000) });
        try { body = await r.json(); } catch (e) { }
        if (!r.ok) { box.innerHTML = npGray(`${__t('np_unverified')} · HTTP ${r.status}${_detText(body, '') ? ' · ' + esc(_detText(body, '')) : ''}`); return; }
        if (body && body.contact === 'never') { box.innerHTML = npGray(__t('sites_never_help')); return; }
        const list = Array.isArray(body.devices) ? body.devices : [];
        if (!list.length) { box.innerHTML = npGray(__t('sites_no_devs')); return; }
        const rows = list.map(d => {
          const inv = d.inventory_match === true
            ? `<span class="badge" style="background:rgba(34,197,94,.15);color:var(--green)">${__t('sites_inv_ok')}</span>`
            : (d.inventory_match === false
              ? `<span class="badge" style="background:rgba(245,158,11,.12);color:var(--orange)">${__t('sites_inv_no')}</span>`
              : `<span class="badge" style="background:var(--surface);color:var(--text-ter)">${__t('sites_inv_unv')}<div style="display:none">${esc(d.inventory_error || '')}</div></span>`);
          const srcs = d.source || {};
          return `<tr>
            <td style="font-weight:500">${esc(d.device_id)}</td>
            <td style="font-size:11px;color:var(--text-secondary)">${esc(srcs.site_id || '—')} / ${esc(srcs.collector_id || '—')}</td>
            ${_devState(d)}
            <td style="font-family:var(--mono,monospace);font-size:11px">${d.latency_ms === null || d.latency_ms === undefined ? '—' : Number(d.latency_ms).toFixed(1)}</td>
            <td style="font-size:11px;color:var(--text-secondary)">${_isoLocal(d.freshness && d.freshness.last_sample_ts)}</td>
            <td>${inv}</td>
            <td><button class="btn btn-o btn-xs" onclick="siteHist('${escAttr(siteId)}','${escAttr(d.device_id)}')">${__t('sites_hist_btn')}</button></td>
          </tr>`;
        }).join('');
        box.innerHTML = `
          <div style="border:1px solid var(--border);border-radius:6px;margin-bottom:8px"><table>
            <tr><th>${__t('sites_col_dev')}</th><th>${__t('sites_col_source')}</th><th>${__t('sites_col_state')}</th><th>${__t('sites_col_lat')}</th><th>${__t('sites_col_last')}</th><th>${__t('sites_col_inv')}</th><th></th></tr>
            ${rows}</table></div>
          <div id="sites-hist-${escAttr(siteId)}"></div>`;
      } catch (e) {
        box.innerHTML = npGray(`${__t('np_unverified')} · ${esc(e.message)}`);
      }
    }

    async function siteHist(siteId, devId) {
      const box = document.getElementById(`sites-hist-${siteId}`); if (!box) return;
      box.innerHTML = '<div class="sc"><div class="sl"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m22 7-8.5 8.5-5-5L2 17M16 7h6v6"/></svg> ' + __t('sites_hist_title') + ' — ' + esc(devId) + '</div><div id="sh-chart-' + esc(devId) + '" style="padding:8px 0"><div class="sp"></div></div></div>';
      let body = null, r;
      try {
        r = await fetch(`${A}/remote/sites/${encodeURIComponent(siteId)}/devices/${encodeURIComponent(devId)}/history?hours=6`, { headers: h(), signal: AbortSignal.timeout(15000) });
        try { body = await r.json(); } catch (e) { }
        const el = document.getElementById(`sh-chart-${devId}`);
        if (!el) return;
        if (r.status === 404) { el.innerHTML = npGray(__t('sites_hist_nodata')); return; }
        if (r.status === 422) { el.innerHTML = npGray(`${esc(_detText(body, 'ventana demasiado grande'))} · ${__t('sites_hist_less')}`); return; }
        if (!r.ok) { el.innerHTML = npGray(`${__t('np_unverified')} · HTTP ${r.status}`); return; }
        const pts = Array.isArray(body.points) ? body.points : [];
        if (!pts.length) { el.innerHTML = npGray(__t('sites_hist_empty')); return; }
        // Puntos crudos, sin interpolar: un fallo es una muestra reachable=false; un hueco es un hueco.
        const maxLat = Math.max(1, ...pts.map(p => Number(p.latency_ms) || 0));
        const mk = (h) => Math.round(Math.min(60, Math.max(3, h)));
        el.innerHTML = `<div style="display:flex;align-items:flex-end;gap:2px;height:64px;flex-wrap:nowrap;overflow:hidden">${
          pts.map(p => {
            const down = p.ok === true && p.reachable === false;
            const unver = p.ok === false || p.reachable === null || p.reachable === undefined;
            const col = down ? 'var(--red)' : (unver ? 'var(--text-ter)' : 'var(--accent)');
            const hgt = down ? 60 : (unver ? 6 : mk((Number(p.latency_ms) || 0) / maxLat * 60));
            const ti = `${_isoLocal(p.ts)} — ${down ? __t('sites_st_noresp') : (unver ? __t('dw_st_error') + (p.error ? ': ' + p.error : '') : (Number(p.latency_ms) || 0).toFixed(1) + ' ms')}`;
            return `<div title="${escAttr(ti)}" style="width:6px;height:${hgt}px;background:${col};border-radius:2px;opacity:.85"></div>`;
          }).join('')}</div>
          <div style="font-size:10px;color:var(--text-ter);margin-top:4px">${__t('sites_hist_caption')}</div>`;
      } catch (e) {
        const el = document.getElementById(`sh-chart-${devId}`);
        if (el) el.innerHTML = npGray(`${__t('np_unverified')} · ${esc(e.message)}`);
      }
    }
