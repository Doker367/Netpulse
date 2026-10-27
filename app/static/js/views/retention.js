/**
 * NetPulse NMS — vista Retención (views/retention.js).
 *
 * Extraída del <script> inline de app/static/dashboard.html (Tarjeta 10D-D,
 * lote 4) sin cambio de comportamiento: estado `_retPreview` (sin simulación
 * previa no hay ejecución — contrato retention.md), loadRetention y las
 * funciones ret* exclusivas. Líneas en el mismo orden relativo entre sí.
 *
 * Lo que NO viajó (verificado por grep, regla 2):
 * - `_fmtBytes` — hay DOS declaraciones en el inline y por hoisting gana la
 *   segunda (bloque OLA 4, quedada IN SITU; la del bloque Performance está
 *   sombreada y su cuerpo NO es equivalente). loadRetention la resuelve en
 *   tiempo de llamada contra la ganadora: si esta viajara, ganaría la
 *   sombreada y cambiaría el formateo de tamaños.
 * - `_isoLocal` — vive en views/sites.js (bloque original SITIOS REMOTOS);
 *   esta vista la consume en tiempo de llamada (este módulo se evalúa antes,
 *   pero todas las llamadas son runtime, tras cargar los 15 scripts).
 * - `__isAdmin` / `npGray` / `_detText` / `esc` / `escAttr` — compartidos
 *   (inline/core), resolución en tiempo de llamada.
 *
 * Código muerto trasladado tal cual (mapa §2.3, no borrar sin tarjeta aparte):
 * `retDet` es un alias sin uso de `_detText`.
 *
 * Depende (en tiempo de llamada): core.js (A, h, t, __t, toast),
 * views/sites.js (_isoLocal), inline (__isAdmin, npGray, esc, _fmtBytes,
 * _detText). Handlers por window: botón Refresh de #v-retention,
 * swView('retention') y los onclick generados (retInvalidate, retPruneDry,
 * retPruneConfirm, retPruneExec). Script clásico sin defer/async.
 */

    // ── RETENCIÓN (contrato retention.md; dry-run obligatorio) ──
    let _retPreview = null; // {dias, borraria} — invalidada al cambiar días

    async function loadRetention() {
      const el = document.getElementById('ret-content'); if (!el || !t) return;
      el.innerHTML = '<div class="empty"><div class="sp"></div></div>';
      let inv = null, bks = null, err = '';
      try {
        const [ri, rb] = await Promise.allSettled([
          fetch(`${A}/retention/inventory`, { headers: h(), signal: AbortSignal.timeout(15000) }),
          fetch(`${A}/retention/backups`, { headers: h(), signal: AbortSignal.timeout(15000) })
        ]);
        if (ri.status === 'rejected') err = `${__t('np_unverified')} · ${ri.reason.message}`;
        else {
          if (!ri.value.ok) err = `${__t('np_unverified')} · HTTP ${ri.value.status}`;
          inv = await ri.value.json();
        }
        if (rb.status === 'fulfilled' && rb.value.ok) bks = await rb.value.json();
      } catch (e) { err = err || `${__t('np_unverified')} · ${esc(e.message)}`; }
      if (!inv) { el.innerHTML = npGray(err); return; }
      const p = inv.prometheus || {}, a = inv.audit || {}, b = inv.backups || {};
      const estadoBadge = (st) => (st && st !== 'con datos')
        ? `<div style="font-size:11px;color:var(--text-ter);margin-top:4px">${esc(st)}</div>` : '';
      const row = (k, v) => `<div style="display:flex;justify-content:space-between;gap:8px;padding:3px 0"><span style="color:var(--text-secondary)">${esc(k)}</span><b style="font-size:12px">${v}</b></div>`;
      const cards = `
        <div style="background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:12px">
          <b style="font-size:13px">Prometheus (TSDB)</b>
          ${row('retention_time', p.retention_time === null || p.retention_time === undefined ? `<span style="color:var(--text-ter)">${__t('ret_unmeasured')}</span>` : esc(String(p.retention_time)))}
          ${row('retention_size', p.retention_size === null || p.retention_size === undefined ? `<span style="color:var(--text-ter)">${__t('ret_unmeasured')}</span>` : esc(String(p.retention_size)))}
          ${estadoBadge(p.estado)}
        </div>
        <div style="background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:12px">
          <b style="font-size:13px">${__t('ret_audit')}</b>
          ${row(__t('ret_records'), a.registros === null || a.registros === undefined ? `<span style="color:var(--text-ter)">${__t('ret_unmeasured')}</span>` : Number(a.registros))}
          ${row(__t('ret_oldest'), a.mas_viejo === null || a.mas_viejo === undefined ? `<span style="color:var(--text-ter)">${__t('ret_unmeasured')}</span>` : _isoLocal(a.mas_viejo))}
          ${estadoBadge(a.estado)}
        </div>
        <div style="background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:12px">
          <b style="font-size:13px">${__t('ret_backups')}</b>
          ${row(__t('ret_files'), b.ruta === null || b.ruta === undefined ? `<span style="color:var(--text-ter)">${esc(b.estado || __t('ret_unmeasured'))}</span>` : esc(String(b.archivos ?? 0)))}
          ${row(__t('ret_usage'), b.ruta === null || b.ruta === undefined ? '—' : _fmtBytes(b.uso_bytes))}
          ${row(__t('ret_oldest'), b.ruta === null || b.ruta === undefined ? '—' : (b.mas_viejo ? _isoLocal(b.mas_viejo) : `<span style="color:var(--text-ter)">${__t('ret_unmeasured')}</span>`))}
          ${estadoBadge(b.estado)}
        </div>`;
      const bRows = (bks && Array.isArray(bks.backups)) ? bks.backups.map(x => `<tr>
        <td style="font-weight:500">${esc(x.nombre || '—')}</td>
        <td style="font-family:var(--mono,monospace);font-size:11px">${Number(x.tamano_bytes ?? 0)}</td>
        <td style="font-size:11px;color:var(--text-secondary)">${_isoLocal(x.fecha)}</td></tr>`).join('') : '';
      const canPrune = __isAdmin();
      el.innerHTML = `
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-bottom:16px">${cards}</div>
        ${bRows ? `<div style="border:1px solid var(--border);border-radius:6px;margin-bottom:16px"><table>
          <tr><th>${__t('ret_name')}</th><th>${__t('ret_size')}</th><th>${__t('ret_mtime')}</th></tr>${bRows}</table></div>` : ''}
        ${canPrune ? `
        <div class="sc" id="ret-prune-box">
          <div class="sl"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/></svg> ${__t('ret_prune_title')}</div>
          <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap;align-items:center">
            <label style="font-size:12px;color:var(--text-secondary)" data-i18n="ret_days">${__t('ret_days')}</label>
            <select id="ret-days" onchange="retInvalidate()" style="background:var(--bg);border:1px solid var(--border);color:var(--text);padding:7px 10px;border-radius:6px;font-size:12px">
              <option value="7">7</option><option value="30" selected>30</option><option value="90">90</option><option value="365">365</option>
            </select>
            <button class="btn btn-o btn-sm" onclick="retPruneDry()" id="ret-dry-btn">${__t('ret_dry_btn')}</button>
          </div>
          <div id="ret-prune-out" style="margin-top:10px;font-size:12px"></div>
        </div>` : `<div style="font-size:11px;color:var(--text-ter);margin-bottom:12px">${__t('ret_viewer_note')}</div>`}`;
    }

    function retInvalidate() { _retPreview = null; const o = document.getElementById('ret-prune-out'); if (o) o.innerHTML = ''; }
    function retDet(body, fb) { return _detText(body, fb); }

    async function retPruneDry() {
      const out = document.getElementById('ret-prune-out'); if (!out) return;
      const dias = Number(document.getElementById('ret-days')?.value || 0);
      if (!dias) { out.innerHTML = `<div style="color:var(--orange)">${__t('ret_pick_days')}</div>`; return; }
      out.innerHTML = '<div class="sp" style="margin:12px auto"></div>';
      let body = null, r;
      try {
        r = await fetch(`${A}/retention/audit-prune`, { method: 'POST', headers: h(), body: JSON.stringify({ dias, dry_run: true }), signal: AbortSignal.timeout(15000) });
        try { body = await r.json(); } catch (e) { }
        if (!r.ok) {
          out.innerHTML = r.status === 422
            ? `<div style="color:var(--orange)">${esc(_detText(body, 'HTTP 422'))}</div>`
            : npGray(`${__t('np_unverified')} · ${esc(_detText(body, 'HTTP ' + r.status))}`);
          return;
        }
        _retPreview = { dias, borraria: Number(body.borraria ?? 0) };
        const muestras = Array.isArray(body.muestras) ? body.muestras : [];
        const mrows = muestras.map(m => `<tr>
          <td style="font-size:11px">${_isoLocal(m.timestamp)}</td>
          <td style="font-size:11px;font-family:var(--mono,monospace)">${esc(String(m.action || '—'))}</td>
          <td style="font-size:11px">${esc(String(m.username || '—'))}</td></tr>`).join('');
        out.innerHTML = `
          <div class="bt" style="margin-bottom:8px">${__t('ret_dry_result').replace('{n}', String(_retPreview.borraria)).replace('{d}', String(dias))}</div>
          ${mrows ? `<div style="border:1px solid var(--border);border-radius:6px;overflow:hidden"><table style="font-size:11px"><tr><th>${__t('ret_col_ts')}</th><th>${__t('ret_col_action')}</th><th>${__t('ret_col_user')}</th></tr>${mrows}</table></div>` : ''}
          <div style="font-size:11px;color:var(--text-ter);margin-top:4px">${__t('ret_still_skip')}</div>
          <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap">
            <button class="btn btn-p btn-sm" onclick="retPruneConfirm()">${__t('ret_exec_btn')}</button>
            <button class="btn btn-o btn-sm" onclick="retInvalidate()">${__t('ret_cancel_btn')}</button>
          </div>`;
      } catch (e) {
        out.innerHTML = npGray(`${__t('np_unverified')} · ${esc(e.message)}`);
      }
    }

    async function retPruneConfirm() {
      const out = document.getElementById('ret-prune-out'); if (!out) return;
      if (!_retPreview) return; // sin simulación previa no hay segunda llamada (exigida por contrato)
      out.innerHTML = `<div class="bt" style="margin-bottom:10px;border-left-color:var(--red)">${__t('ret_confirm_msg').replace('{n}', String(_retPreview.borraria)).replace('{d}', String(_retPreview.dias))}</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn btn-p btn-sm" style="background:var(--red)" onclick="retPruneExec()">${__t('ret_exec_now')}</button>
          <button class="btn btn-o btn-sm" onclick="retInvalidate()">${__t('ret_cancel_btn')}</button>
        </div>`;
    }

    async function retPruneExec() {
      const out = document.getElementById('ret-prune-out'); if (!out || !_retPreview) return;
      out.innerHTML = '<div class="sp" style="margin:12px auto"></div>';
      let body = null, r;
      try {
        r = await fetch(`${A}/retention/audit-prune`, { method: 'POST', headers: h(), body: JSON.stringify({ dias: _retPreview.dias, dry_run: false }), signal: AbortSignal.timeout(30000) });
        try { body = await r.json(); } catch (e) { }
        if (!r.ok) {
          out.innerHTML = npGray(`${__t('np_unverified')} · ${esc(_detText(body, 'HTTP ' + r.status))}`);
          return;
        }
        toast(`${__t('ret_done').replace('{n}', String(body.borrados ?? 0))}`, 's');
        out.innerHTML = `<div class="bt" style="margin-bottom:8px">${__t('ret_done').replace('{n}', String(body.borrados ?? 0))}</div><div style="font-size:11px;color:var(--text-ter)">${__t('ret_kept').replace('{n}', String(body.kept ?? 0))}</div>`;
        _retPreview = null;
        loadRetention();
      } catch (e) {
        out.innerHTML = npGray(`${__t('np_unverified')} · ${esc(e.message)}`);
      }
    }
