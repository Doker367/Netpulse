/**
 * NetPulse NMS — vista Compliance (views/compliance.js).
 *
 * Extraída del <script> inline de app/static/dashboard.html (Tarjeta 10D-A,
 * lote 1) sin cambio de comportamiento: loadCompliance, setBaseline,
 * checkCompliance, loadCompReport, mismas líneas en el mismo orden relativo.
 *
 * Depende (en tiempo de llamada, no de carga): core.js (A, h, toast, __t,
 * currentUser) e inline de dashboard.html (esc, escAttr, __isAdmin, npGray,
 * estado `devices`). __isAdmin/npGray SE QUEDAN en el inline: los comparten
 * NetBox, Admin, Sites, Retention y Reports (prohibido duplicar).
 *
 * Handlers que las resuelven por window: swView('compliance') →
 * loadCompliance(); onclick generados por loadCompliance() → setBaseline(),
 * checkCompliance(), loadCompReport(). Scripts clásicos sin defer/async ya
 * cargaron al momento del click (orden estático). Top-level sin ejecución.
 */

// ── COMPLIANCE (existente; /api/compliance/* es SOLO admin, 403 con operator) ──
    async function loadCompliance() {
      const el = document.getElementById('compliance-content'); if (!el || !t) return;
      // Guardia de rol honesta: sin rol admin no hay vista, ni verdes, ni error genérico.
      if (!__isAdmin()) {
        el.innerHTML = npGray(`<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg> ${__t('comp_admin_required')}`);
        return;
      }
      el.innerHTML = '<div class="empty"><div class="sp"></div></div>';
      let baselines = null, groups = null, groupsNote = '';
      try {
        const [rB, rG] = await Promise.all([
          fetch(`${A}/compliance/baselines`, { headers: h(), signal: AbortSignal.timeout(15000) }),
          fetch(`${A}/groups`, { headers: h(), signal: AbortSignal.timeout(15000) })
        ]);
        baselines = rB.ok ? await rB.json() : null;
        if (rG.ok) { const gl = await rG.json().catch(() => []); groups = Array.isArray(gl) ? gl.map(g => g.name).filter(Boolean) : []; }
        else groupsNote = `<div style="font-size:10px;margin-top:4px">${esc(__t('grp_list_unverified'))} · HTTP ${rG.status}</div>`;
      } catch (e) { baselines = null; }
      // Fallo de la API (403 u otro) → gris "no verificable", nunca pasos vacíos ni verdes.
      if (baselines === null) {
        el.innerHTML = npGray(`<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg> ${__t('np_unverified')}`);
        return;
      }
      const bl = Array.isArray(baselines) ? baselines.filter(b => typeof b === 'string') : [];
      const blHeader = bl.length
        ? `<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M12 11h4"/><path d="M12 16h4"/><path d="M8 11h.01"/><path d="M8 16h.01"/></svg> ${__t('comp_baselines')}: <b>${bl.map(esc).join(', ')}</b>`
        : `<span style="color:var(--text-ter)"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M12 11h4"/><path d="M12 16h4"/><path d="M8 11h.01"/><path d="M8 16h.01"/></svg> ${esc(__t('comp_no_baselines'))}</span>`;
      const grpSub = !groupsNote && Array.isArray(groups) ? (groups.length ? '' : `<div style="font-size:10px;color:var(--text-ter);margin-top:4px">${esc(__t('grp_empty'))}</div>`) : groupsNote;
      const dl = (Array.isArray(groups) ? groups : []).map(g => `<option value="${escAttr(g)}">${esc(g)}</option>`).join('');
      el.innerHTML = `<div style="margin-bottom:16px;font-size:12px;color:var(--text-ter)">${blHeader}${grpSub}</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
      <div class="sc">
        <div class="c-step" onclick="document.getElementById('cmp-bl-group').focus()"><span class="cs-n">1</span><div><div class="cs-t">${__t('comp_step1')}</div><div class="cs-d">${__t('comp_step1_d')}</div></div></div>
        <div class="fg" style="margin:12px 0 6px"><label>Grupo</label>
          <input id="cmp-bl-group" list="cmp-bl-groups" placeholder="Core" style="background:var(--bg);border:1px solid var(--border);color:var(--text);padding:8px 10px;border-radius:6px;width:100%;font-size:12px">
          <datalist id="cmp-bl-groups">${dl}</datalist>
        </div>
        <div class="c-step"><span class="cs-n">2</span><div><div class="cs-t">${__t('comp_step2')}</div><div class="cs-d">${__t('comp_step2_d')}</div></div></div>
        <textarea id="cmp-bl-config" placeholder="! Escribe aquí la configuración deseada (baseline)..." style="min-height:100px;margin-top:8px;font-size:11px"></textarea>
        <button class="btn btn-p btn-sm" onclick="setBaseline()" style="margin-top:6px"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="22" y1="12" x2="2" y2="12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><line x1="6" y1="16" x2="6.01" y2="16"/><line x1="10" y1="16" x2="10.01" y2="16"/></svg> ${__t('comp_save_baseline')}</button>
      </div>
      <div class="sc">
        <div class="c-step"><span class="cs-n">3</span><div><div class="cs-t">${__t('comp_step3')}</div><div class="cs-d">${__t('comp_step3_d')}</div></div></div>
        <div style="margin-top:6px;max-height:200px;overflow-y:auto;border:1px solid var(--border);border-radius:6px;padding:4px">
          ${(devices || []).length ? devices.map(d => `<label style="display:flex;align-items:center;gap:8px;font-size:12px;padding:5px 8px;cursor:pointer;border-radius:4px;transition:background .1s" onmouseover="this.style.background='rgba(255,255,255,.03)'" onmouseout="this.style.background=''">
            <input type="checkbox" class="cmp-chk" value="${escAttr(d.id)}" checked style="width:14px;accent-color:var(--accent)"> <span>${esc(d.id)}</span><span style="margin-left:auto;font-size:9px;color:var(--text-ter)">${esc(d.driver)}</span>
          </label>`).join('') : `<div style="font-size:11px;color:var(--text-ter);padding:8px">${__t('comp_no_devices')}</div>`}
        </div>
        <button class="btn btn-p btn-sm" onclick="checkCompliance()" style="margin-top:8px"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg> ${__t('comp_check_btn')}</button>
        <button class="btn btn-o btn-sm" onclick="loadCompReport()" style="margin-top:8px" ${(devices || []).length ? '' : 'disabled'}><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg> ${__t('comp_report_btn')}</button>
      </div>
    </div>
    <div id="cmp-result" style="margin-top:12px"></div>`;
    }
    async function setBaseline() {
      const group = document.getElementById('cmp-bl-group')?.value.trim();
      const config = document.getElementById('cmp-bl-config')?.value;
      if (!group || !config) return;
      try {
        const r = await fetch(`${A}/compliance/baseline`, { method: 'POST', headers: h(), body: JSON.stringify({ group, config }) });
        // Antes no se miraba r.ok: un 403/500 cantaba éxito. Ahora el toast dice la verdad.
        if (!r.ok) {
          const d = await r.json().catch(() => ({}));
          toast(`${__t('comp_baseline_fail')} · ${esc(d.detail || 'HTTP ' + r.status)}`, 'error');
          return;
        }
        toast(`<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg> ${__t('comp_baseline_saved').replace('{g}', group)}`, 's')
      } catch (e) { toast(`${__t('comp_baseline_fail')}: ${e.message}`, 'error') }
    }
    async function checkCompliance() {
      const checks = [...document.querySelectorAll('.cmp-chk:checked')].map(c => c.value);
      if (!checks.length) { toast(__t('comp_need_device'), 'error'); return }
      const el = document.getElementById('cmp-result');
      el.innerHTML = `<div class="sp"></div> ${__t('comp_checking')}`;
      try {
        const r = await fetch(`${A}/compliance/check`, { method: 'POST', headers: h(), body: JSON.stringify({ device_ids: checks }) });
        // Antes un 403 renderizaba estado para todos: sin r.ok se inventaba compliant=true. Nunca más.
        if (!r.ok) {
          const d = await r.json().catch(() => ({}));
          el.innerHTML = `<div style="border:1px solid var(--border);border-radius:6px;margin-top:8px;padding:10px;font-size:12px;color:var(--text-ter)">${__t('np_unverified')} · ${esc(d.detail || 'HTTP ' + r.status)}</div>`;
          return;
        }
        const d = await r.json();
        // Contrato real (device_ids): {total, compliant, non_compliant, results: {id: {compliant, violations}}}.
        const raw = d.results && typeof d.results === 'object' ? d.results : {};
        const list = Array.isArray(raw) ? raw : Object.entries(raw).map(([id, rx]) => ({ device_id: id, compliant: !!(rx && rx.compliant), violations: (rx && rx.violations) || [] }));
        const okCount = typeof d.compliant === 'number' ? d.compliant : list.filter(x => x.compliant).length;
        const badCount = typeof d.non_compliant === 'number' ? d.non_compliant : list.length - okCount;
        const total = Number(d.total) || list.length;
        el.innerHTML = `<div style="border:1px solid var(--border);border-radius:6px;margin-top:8px">
          <div style="padding:10px 12px;font-size:12px;font-weight:600;color:${badCount ? 'var(--yellow)' : 'var(--green)'}">${okCount}/${total} ${__t('comp_ok_summary')}${badCount ? ` · ${badCount} ${__t('comp_mal_summary')}` : ''}</div>
          <table><tr><th>${__t('comp_th_device')}</th><th>${__t('comp_th_result')}</th><th>${__t('comp_th_violations')}</th></tr>
          ${list.length ? list.map(rx => `<tr><td style="font-weight:500">${esc(rx.device_id || rx.id || '?')}</td>
          <td aria-label="${escAttr(__t(rx.compliant ? 'comp_ok_summary' : 'comp_mal_summary'))}">${rx.compliant ? '<span class="badge badge-g"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg></span>' : '<span class="badge badge-r"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></span>'}</td>
          <td>${(rx.violations || rx.errors || []).length || '—'}</td></tr>`).join('') : `<tr><td colspan="3" style="color:var(--text-ter)">${__t('np_unverified')}</td></tr>`}</table></div>`;
      } catch (e) { el.innerHTML = `<div style="color:var(--text-ter)">${__t('comp_check_fail')}: ${esc(e.message)}</div>` }
    }
    async function loadCompReport() {
      const el = document.getElementById('cmp-result'); if (!el) return;
      el.innerHTML = `<div class="sp"></div> ${__t('comp_report_running')}`;
      try {
        const r = await fetch(`${A}/compliance/report`, { headers: h(), signal: AbortSignal.timeout(120000) });
        if (!r.ok) {
          const d = await r.json().catch(() => ({}));
          el.innerHTML = `<div style="border:1px solid var(--border);border-radius:6px;margin-top:8px;padding:10px;font-size:12px;color:var(--text-ter)">${__t('np_unverified')} · ${esc(d.detail || 'HTTP ' + r.status)}</div>`;
          return;
        }
        const d = await r.json();
        // Contrato real: {summary: {total, compliant, non_compliant, errors}, devices: {id: {compliant, violations}}}.
        const s = d.summary || {};
        const list = Object.entries(d.devices || {}).map(([id, rx]) => ({ device_id: id, compliant: !!(rx && rx.compliant), violations: (rx && rx.violations) || [] }));
        const okN = s.compliant != null ? Number(s.compliant) : list.filter(x => x.compliant).length;
        const errN = s.errors != null ? Number(s.errors) : 0;
        const tot = Number(s.total) || list.length;
        el.innerHTML = `<div style="border:1px solid var(--border);border-radius:6px;margin-top:8px">
          <div style="padding:10px 12px;font-size:12px;font-weight:600">${tot ? `${okN}/${tot} ${__t('comp_ok_summary')}${errN ? ` · ${errN} ${__t('comp_errors')}` : ''}` : `<span style="color:var(--text-ter)">${__t('comp_no_devices')}</span>`}</div>
          ${tot ? `<table><tr><th>${__t('comp_th_device')}</th><th>${__t('comp_th_result')}</th><th>${__t('comp_th_violations')}</th></tr>
          ${list.map(rx => `<tr><td style="font-weight:500">${esc(rx.device_id || '?')}</td>
          <td aria-label="${escAttr(__t(rx.compliant ? 'comp_ok_summary' : 'comp_mal_summary'))}">${rx.compliant ? '<span class="badge badge-g"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg></span>' : '<span class="badge badge-r"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></span>'}</td>
          <td style="font-size:10px;max-width:280px;overflow:hidden;text-overflow:ellipsis">${esc((rx.violations || []).map(v => v.expected || v.got || '').filter(Boolean).join(' | ')) || '—'}</td></tr>`).join('')}</table>` : ''}</div>`;
      } catch (e) { el.innerHTML = `<div style="color:var(--text-ter)">${__t('comp_check_fail')}: ${esc(e.message)}</div>` }
    }
