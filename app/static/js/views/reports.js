/**
 * NetPulse NOC — vista Reportes SLA (views/reports.js).
 *
 * Extraída del <script> inline de app/static/dashboard.html (Tarjeta 10D-D,
 * lote 4) sin cambio de comportamiento: estado de filtros (repState,
 * repGroupsCache), helpers y funciones de la vista (rep*, loadReports) y
 * `_donut`, que antes vivía en el bloque PERFORMANCE del inline y su única
 * llamada restante era repRender (el dashboard pinta sus donuts en su propio
 * template; verificado por grep). Líneas en el mismo orden relativo entre sí
 * (orden original: _donut < comentario de contratos < repState/repGroupsCache
 * < _ymd…repRender).
 *
 * Lo que NO viajó (verificado por grep, regla 2):
 * - `_detText` (bloque Reports del inline) y `_repTs` — compartidos: los
 *   consumen NetBox, SFTP, el wizard de discovery y views/admin.js en tiempo
 *   de llamada.
 * - `_fmtUptime` — la llaman views/performance.js (Lote C) y `_repDur` de esta
 *   vista: queda en el inline, resolución en tiempo de llamada.
 * - `_fmtBytes` — solo la llama loadRetention (/views/retention.js), pero hay
 *   DOS declaraciones en el inline y por hoisting gana la segunda (bloque OLA
 *   4; la del bloque Performance está sombreada y su cuerpo NO es
 *   equivalente). Moverla haría ganar la sombreada y cambiaría el formateo.
 * - `devices`, `loadDev`, `currentUser`, `_npStateGet/_npStateSet('rep')`,
 *   `esc`, `escAttr` — estado/compartidos del inline y core.js; `logout`
 *   (core.js) asigna `repState = null` y resuelve el binding de este archivo
 *   (entorno léxico global compartido) en tiempo de llamada.
 *
 * Depende (en tiempo de llamada): core.js (A, h, t, __t, toast, _npState*),
 * inline (devices, loadDev, esc, escAttr, _detText, _repTs, _fmtUptime).
 * Handlers por window: botón Refresh de #v-reports, swView('reports') y los
 * onclick que loadReports/repRender/repApply generan. Script clásico sin
 * defer/async.
 */

    // `_donut` vivía en el bloque PERFORMANCE del inline (antes de _fmtUptime);
    // su única llamada restante era repRender (verificado por grep), así que viaja
    // aquí respetando su orden relativo (4216 < comentario de contratos < 7006).
    function _donut(pct, color, size) {
      pct = Math.max(0, Math.min(100, Math.round(pct || 0)));
      size = size || 72;
      return `<div class="donut" style="--pct:${pct};width:${size}px;height:${size}px;margin:0 auto"><svg viewBox="0 0 36 36" fill="none" stroke="currentColor"><path class="donut-bg" d="M18 2.0845a15.9155 15.9155 0 0 1 0 31.831a15.9155 15.9155 0 0 1 0-31.831"/><path class="donut-fg" stroke="${color}" stroke-dasharray="${pct},100" d="M18 2.0845a15.9155 15.9155 0 0 1 0 31.831a15.9155 15.9155 0 0 1 0-31.831"/></svg><div class="donut-lbl" style="font-size:13px;font-weight:700">${pct}%</div></div>`;
    }

    // ── REPORTES SLA (contrato docs/handoffs/reportes-sla.md) ──────────────
    // POST /api/reports/availability      → operator/admin; recalcula y sustituye el reporte del mismo hash.
    // GET  /api/reports/availability/cached → viewer+; SOLO lee el último reporte para los MISMOS
    //        parámetros (device_ids repetido como query param). 404 = cache miss, no error genérico.
    // Honestidad: sin muestras no se pinta 0 % ni 100 %; cobertura parcial nunca con verde "certificado".
    let repState = null;      // última ventana consultada (para el botón Refresh de la vista)
    let repGroupsCache = null; // {array} nombre de grupos; null = GET /api/groups no verificable

    function _ymd(x) { return x.toISOString().slice(0, 10); }
    function _repDur(s) {
      const n = Number(s) || 0;
      if (n < 60) return Math.round(n * 10) / 10 + ' s';
      return _fmtUptime(n);
    }
    function repBanner(msg, warn) {
      return `<div class="empty" style="background:var(--bg2)"><span class="icon" style="filter:grayscale(${warn ? 0 : 1})">—</span><br><span style="color:${warn ? 'var(--text-secondary)' : 'var(--text-ter)'}">${msg}</span></div>`;
    }

    async function _repGroups() {
      try {
        const r = await fetch(`${A}/groups`, { headers: h(), signal: AbortSignal.timeout(8000) });
        if (r.ok) {
          const g = await r.json();
          return Array.isArray(g) ? g.map(x => x.name).filter(Boolean) : [];
        }
      } catch (e) { /* grupos no verificables: el filtro queda honesto en gris */ }
      return null;
    }

    async function loadReports() {
      const el = document.getElementById('rep-content');
      if (!el || !t) return;
      el.innerHTML = '<div class="sp" style="margin:20px auto"></div>';
      if (!devices.length) { try { await loadDev(); } catch (e) { } } // Selectores rellenos con inventario real
      if (repGroupsCache === null) repGroupsCache = await _repGroups();
      const groups = repGroupsCache || [];
      const grpOpts = groups.length
        ? groups.map(g => `<option value="${escAttr(g)}">${esc(g)}</option>`).join('')
        : '';
      const canWrite = ['operator', 'admin'].includes(currentUser?.role || 'viewer');
      el.innerHTML = `
        <div style="background:var(--panel);border:1px solid var(--border);border-radius:var(--radius-lg);padding:16px;margin-bottom:20px">
          <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:end">
            <div class="fg" style="margin-bottom:0"><label data-i18n="rep_lbl_desde">${__t('rep_lbl_desde')}</label>
              <input id="rep-desde" type="date" value="${_ymd(new Date(Date.now() - 6 * 86400000))}" style="background:var(--bg);border:1px solid var(--border);color:var(--text);padding:6px 10px;border-radius:var(--radius-sm);font-size:12px"></div>
            <div class="fg" style="margin-bottom:0"><label data-i18n="rep_lbl_hasta">${__t('rep_lbl_hasta')}</label>
              <input id="rep-hasta" type="date" value="${_ymd(new Date())}" style="background:var(--bg);border:1px solid var(--border);color:var(--text);padding:6px 10px;border-radius:var(--radius-sm);font-size:12px"></div>
            <div class="fg" style="margin-bottom:0"><label data-i18n="rep_presets">${__t('rep_presets')}</label>
              <div style="display:flex;gap:6px">
                <button class="btn btn-o btn-xs" onclick="repPreset(7)">7d</button>
                <button class="btn btn-o btn-xs" onclick="repPreset(14)">14d</button>
                <button class="btn btn-o btn-xs" onclick="repPreset(30)">30d</button>
              </div></div>
            <div class="fg" style="margin-bottom:0"><label data-i18n="rep_lbl_group">${__t('rep_lbl_group')}</label>
              <select id="rep-group" style="background:var(--bg);border:1px solid var(--border);color:var(--text);padding:6px 10px;border-radius:var(--radius-sm);font-size:12px;min-width:150px">
                <option value="">${__t('rep_any_group')}</option>${grpOpts}
                ${repGroupsCache === null ? `<option value="" disabled>${__t('rep_groups_unverified')}</option>` : ''}
              </select></div>
            <div class="fg" style="margin-bottom:0"><label data-i18n="rep_lbl_devices">${__t('rep_lbl_devices')}</label>
              <select id="rep-devs" multiple size="2" style="background:var(--bg);border:1px solid var(--border);color:var(--text);padding:6px 10px;border-radius:var(--radius-sm);font-size:12px;min-width:170px">
                ${devices.map(d => `<option value="${escAttr(d.id)}">${esc(d.id)}</option>`).join('') || `<option value="" disabled>${__t('rep_no_devices_yet')}</option>`}
              </select></div>
            <div style="display:flex;gap:8px">
              <button class="btn btn-o btn-sm" onclick="repApply(false)">${__t('rep_btn_cached')}</button>
              ${canWrite ? `<button class="btn btn-p btn-sm" onclick="repApply(true)">${__t('rep_btn_regen')}</button>` : ''}
            </div>
          </div>
          ${canWrite ? '' : `<div style="font-size:11px;color:var(--text-ter);margin-top:10px">${__t('rep_viewer_only')}</div>`}
          <div style="font-size:11px;color:var(--text-ter);margin-top:10px">${__t('rep_window_note')}</div>
        </div>
        <div id="rep-results"><div class="sp"></div></div>`;

      // Estado recordado (por usuario, Tarjeta 9B/9C): al entrar se rellenan los
      // filtros sin lanzar consulta automática — el operador decide si consulta
      // (cached GET) o regenera (POST). Solo en la primera visita sin nada
      // guardado se mantiene el comportamiento actual (consultar caché).
      const guardado = _npStateGet('rep');
      if (repState && repState.regenerate === false) {
        const dd = document.getElementById('rep-desde'), hh = document.getElementById('rep-hasta');
        if (dd) dd.value = _ymd(new Date(repState.desde));
        if (hh) hh.value = _ymd(new Date(repState.hasta));
        const g = document.getElementById('rep-group');
        if (g) g.value = repState.group || '';
        const ds = document.getElementById('rep-devs');
        if (ds) Array.from(ds.options).forEach(o => { o.selected = (repState.device_ids || []).includes(o.value); });
        await repApply(false);
      } else if (guardado && typeof guardado === 'object') {
        _repFillRestored(guardado);
        const res = document.getElementById('rep-results');
        if (res) res.innerHTML = repBanner(__t('rep_filters_restored'), false);
      } else if (repState) {
        // La última consulta fue Regenerar (POST): se rellenan filtros sin reproducir el POST.
        _repFillRestored(repState);
        const res = document.getElementById('rep-results');
        if (res) res.innerHTML = repBanner(__t('rep_filters_restored'), false);
      } else {
        await repApply(false);
      }
    }

    // Rellena los filtros de Reportes desde un estado guardado; no dispara ninguna consulta.
    function _repFillRestored(st) {
      const dd = document.getElementById('rep-desde'), hh = document.getElementById('rep-hasta');
      if (dd && st.desde) dd.value = _ymd(new Date(st.desde));
      if (hh && st.hasta) hh.value = _ymd(new Date(st.hasta));
      const g = document.getElementById('rep-group');
      if (g && st.group && Array.from(g.options).some(o => o.value === st.group)) g.value = st.group;
      const ds = document.getElementById('rep-devs');
      if (ds) Array.from(ds.options).forEach(o => { o.selected = ((st.device_ids || []).includes(o.value)); });
    }

    function repPreset(days) {
      const now = new Date();
      const d = document.getElementById('rep-desde'), hh = document.getElementById('rep-hasta');
      if (d) d.value = _ymd(new Date(now.getTime() - (days - 1) * 86400000));
      if (hh) hh.value = _ymd(now);
    }

    function repWindow() {
      const dv = document.getElementById('rep-desde')?.value;
      const hv = document.getElementById('rep-hasta')?.value;
      if (!dv || !hv) { toast(__t('rep_need_dates'), 'w'); return null; }
      let desde = new Date(dv + 'T00:00:00Z');
      let hasta = new Date(new Date(hv + 'T00:00:00Z').getTime() + 86400000); // "hasta" cubre su día completo
      const now = new Date();
      if (hasta.getTime() > now.getTime()) {
        hasta = now; // el contrato rechaza "hasta" futuro: se recorta y se avisa
        toast(__t('rep_future_clamped'), 'w');
      }
      if (!(desde.getTime() < hasta.getTime())) { toast(__t('rep_bad_window'), 'error'); return null; }
      if (hasta.getTime() - desde.getTime() > 31 * 86400000) { toast(__t('rep_window_max'), 'error'); return null; }
      return { desde: desde.toISOString(), hasta: hasta.toISOString() };
    }

    function repParams(w) {
      const p = { desde: w.desde, hasta: w.hasta };
      const ds = document.getElementById('rep-devs');
      const ids = ds ? Array.from(ds.options).filter(o => o.selected).map(o => o.value) : [];
      if (ids.length) p.device_ids = ids; // [] no se envía: omitirlo = inventario completo (contrato)
      const g = document.getElementById('rep-group')?.value;
      if (g) p.group = g;
      return p;
    }

    function repQS(p) {
      const q = new URLSearchParams();
      q.set('desde', p.desde);
      q.set('hasta', p.hasta);
      (p.device_ids || []).forEach(i => q.append('device_ids', i)); // repetido como query param (contrato GET cached)
      if (p.group) q.set('group', p.group);
      return q.toString();
    }

    async function repApply(regenerate) {
      const el = document.getElementById('rep-results');
      if (!el || !t) return;
      const canRegen = ['operator', 'admin'].includes(currentUser?.role || 'viewer');
      if (regenerate && !canRegen) { el.innerHTML = repBanner(__t('rep_viewer_only'), true); return; }
      const w = repWindow(); if (!w) return;
      const p = repParams(w);
      el.innerHTML = '<div class="sp" style="margin:20px auto"></div>';
      try {
        const r = regenerate
          ? await fetch(`${A}/reports/availability`, { method: 'POST', headers: h(), body: JSON.stringify(p), signal: AbortSignal.timeout(30000) })
          : await fetch(`${A}/reports/availability/cached?${repQS(p)}`, { headers: h(), signal: AbortSignal.timeout(15000) });
        if (r.status === 404) {
          el.innerHTML = repBanner(__t('rep_cache_miss'), false) +
            (canRegen ? `<div style="margin-top:12px"><button class="btn btn-p btn-sm" onclick="repApply(true)">${__t('rep_btn_regen')}</button></div>` : '');
          return;
        }
        if (!r.ok) {
          let body = null; try { body = await r.json(); } catch (e) { }
          // 503/403/401/422: el detalle literal del backend gana la tarjeta.
          el.innerHTML = repBanner(`${__t('rep_unavailable')} · ${esc(_detText(body, 'HTTP ' + r.status))}`, true);
          return;
        }
        const data = await r.json();
        repState = { ...p, regenerate: regenerate === true };
        // Recordar filtros por usuario (sin regenerate flag: no es un dato, ni falta que hace).
        _npStateSet('rep', { desde: p.desde, hasta: p.hasta, group: p.group || '', device_ids: p.device_ids || [] });
        repRender(el, data, regenerate === true);
      } catch (e) {
        // Red caída / timeout: banner gris "no verificable", nunca un 0 % decorativo.
        el.innerHTML = repBanner(`${__t('rep_unavailable')} · ${esc(e.name === 'TimeoutError' ? __t('rep_timeout') : e.message)}`, true);
      }
    }

    function repRender(el, data, regenerated) {
      const excl = Array.isArray(data.excluidos) ? data.excluidos : [];
      const equipos = Array.isArray(data.equipos) ? data.equipos : [];
      const canRegen = ['operator', 'admin'].includes(currentUser?.role || 'viewer');
      const head = `
        <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:14px">
          <div style="font-size:12px;color:var(--text-secondary)">
            ${esc(_repTs(data.desde))} → ${esc(_repTs(data.hasta))}${data.group ? ' · ' + __t('grp_col_group') + ': ' + esc(data.group) : ''}
            ${data.recoleccion_activa === false ? ` · <span style="color:var(--text-ter)">${__t('rep_collector_off')}</span>` : ''}
            ${regenerated ? '' : ` · <span class="badge" style="background:var(--surface)">${__t('rep_from_cache')}</span>`}
          </div>
          ${canRegen ? `<button class="btn btn-o btn-sm" onclick="repApply(true)">${__t('rep_recalc')}</button>` : ''}
        </div>`;
      const mensaje = data.mensaje
        ? `<div class="bt" style="border-left:3px solid #f59e0b;margin-bottom:12px">${esc(data.mensaje)}</div>` : '';
      const exclBlock = excl.length ? `
        <div style="background:var(--panel);border:1px solid var(--border-s);border-radius:var(--radius-sm);padding:12px;margin-bottom:12px">
          <b style="font-size:12px">${__t('rep_excl_n').replace('{n}', String(excl.length))}</b>
          <table style="width:100%;border-collapse:collapse;font-size:11px;margin-top:8px">
            <thead><tr style="text-align:left;color:var(--text-muted);font-size:10px;text-transform:uppercase">
              <th style="padding:4px 8px 4px 0">device_id</th><th style="padding:4px 8px 4px 0">${__t('rep_col_reason')}</th></tr></thead>
            <tbody>${excl.map(x => `<tr style="border-top:1px solid var(--border-s)">
              <td style="padding:4px 8px 4px 0;font-family:var(--mono)">${esc(x.device_id || '—')}</td>
              <td style="padding:4px 8px 4px 0;color:var(--text-secondary)">${esc(x.reason || '—')}</td></tr>`).join('')}</tbody>
          </table>
        </div>` : '';

      if (data.estado === 'sin_datos' || !equipos.length) {
        // Bloque gris con el mensaje EXACTO del backend; nunca 0 % ni 100 % pintados.
        el.innerHTML = head + mensaje + exclBlock + repBanner(
          `${__t('rep_state_nodata')}${!("mensaje" in data) ? ' · ' + __t('rep_unavailable') : ''}`, false);
        return;
      }

      const cards = equipos.map(e => {
        const co = e.cobertura || {};
        const parcial = !!co.parcial;
        const inc = Array.isArray(e.incidentes) ? e.incidentes : [];
        return `<div style="background:var(--panel);border:1px solid var(--border);border-radius:var(--radius-lg);padding:16px">
          <div style="display:flex;gap:14px;align-items:flex-start;flex-wrap:wrap">
            <div style="flex:0 0 100px">${_donut(e.uptime_percent, parcial ? '#f59e0b' : 'var(--green)', 88)}</div>
            <div style="flex:1;min-width:240px">
              <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
                <b style="font-size:14px">${esc(e.device_id)}</b>
                ${parcial ? `<span class="badge" style="background:rgba(245,158,11,.2);color:#f59e0b">${__t('rep_partial')}</span>` : ''}
              </div>
              <div style="font-size:12px;color:var(--text-secondary);margin-top:6px">${esc(String(e.uptime_percent))}% ${__t('rep_of_samples').replace('{n}', String(co.n_muestras ?? '—'))}</div>
              ${co.descripcion ? `<div style="font-size:11px;color:var(--orange);margin-top:4px">${esc(co.descripcion)}</div>` : ''}
              <div style="font-size:11px;color:var(--text-ter);margin-top:4px">
                ${__t('rep_first_sample')}: ${esc(_repTs(co.first_ts))} · ${__t('rep_last_sample')}: ${esc(_repTs(co.last_ts))}
                · ${__t('rep_hours_req').replace('{g}', String(co.horas_solicitadas ?? '—')).replace('{o}', String(co.horas_observadas ?? '—'))}
              </div>
              ${inc.length ? `
              <table style="width:100%;border-collapse:collapse;font-size:11px;margin-top:10px">
                <thead><tr style="text-align:left;color:var(--text-muted);font-size:10px;text-transform:uppercase">
                  <th style="padding:4px 8px 4px 0">${__t('rep_inc_from')}</th>
                  <th style="padding:4px 8px 4px 0">${__t('rep_inc_last')}</th>
                  <th style="padding:4px 8px 4px 0">${__t('rep_inc_dur')}</th></tr></thead>
                <tbody>${inc.map(x => `<tr style="border-top:1px solid var(--border-s)">
                  <td style="padding:4px 8px 4px 0">${esc(_repTs(x.inicio))}</td>
                  <td style="padding:4px 8px 4px 0">${esc(_repTs(x.fin))}</td>
                  <td style="padding:4px 8px 4px 0">${esc(_repDur(x.duracion_segundos))}<span style="color:var(--text-ter)"> · ${__t('rep_inc_single')}</span></td>
                </tr>`).join('')}</tbody>
              </table>` : `<div style="font-size:11px;color:var(--text-ter);margin-top:10px">${__t('rep_no_incidents')}</div>`}
            </div>
          </div>
        </div>`;
      }).join('');
      el.innerHTML = head + mensaje + exclBlock +
        `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(380px,1fr));gap:12px">${cards}</div>`;
    }
