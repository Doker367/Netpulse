/**
 * NetPulse NMS — wizard de auto-discovery (views/discovery.js).
 *
 * Extraída del <script> inline de app/static/dashboard.html (Tarjeta 10D-F,
 * Lote f) tal cual, sin cambio de comportamiento: estado `let dw` +
 * `const DW_ESTADO`, helpers _dwEstadoText/_dwResList y las 17 funciones del
 * wizard (dwOpen, dwClose, dwReset, dwRender, dwPanelError, dwStep1,
 * dwToggleAllSources, dwScan, dwStep2, dwToStep3, dwStep3, dwSel, dwSelAll,
 * dwToStep4, dwStep4, dwImport). El comentario de contrato
 * (docs/handoffs/discovery.md) viaja al final de este header en su bloque
 * original. El wizard es autónomo: su único DOM es #dw-panel (markup de
 * #v-devices, intacto).
 *
 * Enlaces en tiempo de llamada (resueltos al invocarse contra window /
 * entorno léxico global compartido):
 * - dwOpen/dwImport llaman loadDev y loadInventory (views/devices.js, Lote f)
 *   para refrescar el inventario tras importar.
 * - dwScan/dwImport usan _detText y _repTs — INLINE (compartidas con
 *   reports/admin/sftp; mapa §2.1) — y esc/escAttr/__t/toast/h/A/t.
 * - Botón del markup de #v-devices (onclick="dwOpen()") y todo el onclick
 *   generado por dwRender/dwStep* resuelven por window.
 *
 * Orden de carga (decisión de la tarjeta): discovery.js ANTES que devices.js;
 * ambos son solo declaraciones + let/const de estado propio, ningún símbolo
 * movido se lee a nivel de evaluación, así que el orden no es observable.
 * Script clásico sin defer/async.
 */

    // ── WIZARD DE AUTO-DISCOVERY (contrato docs/handoffs/discovery.md) ─────
    // GET  /api/discovery/candidates → último escaneo en memoria; timestamp null = sin escaneo (gris, no error).
    // POST /api/discovery/scan       → síncrono (~25 s máx); fallos por equipo van en origenes[].estado, sin 500/502.
    // POST /api/discovery/import     → operator; NUNCA auto-import: siempre confirmación explícita del paso 4.
    let dw = null;

    const DW_ESTADO = {
      ok: 'dw_st_ok', sin_vecinos: 'dw_st_sin_vecinos', sin_soporte: 'dw_st_sin_soporte',
      sin_credenciales: 'dw_st_sin_cred', error: 'dw_st_error'
    };
    function _dwEstadoText(estado) {
      const k = DW_ESTADO[estado];
      return k ? __t(k) : String(estado || '—');
    }
    function _dwResList(items, color) {
      return (items || []).map(x => `<li style="margin:3px 0">${esc(x.ip || '?')}${x.id ? ' → <b>' + esc(x.id) + '</b>' : ''}${x.motivo ? ` <span style="color:${color}">· ${esc(x.motivo)}</span>` : ''}</li>`).join('');
    }

    async function dwOpen() {
      const p = document.getElementById('dw-panel');
      if (!p || !t) return;
      p.style.display = 'block';
      p.innerHTML = '<div class="sp" style="margin:20px auto"></div>';
      if (!devices.length) { try { await loadDev(); } catch (e) { } } // orígenes del inventario real
      dw = { step: 1, metodo: 'lldp', scan: null, sel: new Set(), reused: false };
      // ¿Hay escaneo previo en memoria? GET candidates es de solo lectura (viewer).
      try {
        const r = await fetch(`${A}/discovery/candidates`, { headers: h(), signal: AbortSignal.timeout(8000) });
        if (r.ok) {
          const s = await r.json();
          if (s && s.timestamp) { dw.scan = s; dw.step = 3; dw.reused = true; dwRender(); return; }
        }
      } catch (e) { /* último escaneo no verificable: el paso 1 sigue igual de honesto */ }
      dwRender();
    }

    function dwClose() {
      const p = document.getElementById('dw-panel');
      if (p) { p.style.display = 'none'; p.innerHTML = ''; }
      dw = null;
    }

    function dwReset() {
      if (!dw) dw = { step: 1, metodo: 'lldp', scan: null, sel: new Set(), reused: false };
      dw.step = 1; dw.scan = null; dw.sel = new Set(); dw.reused = false;
      dwRender();
    }

    function dwRender() {
      const p = document.getElementById('dw-panel'); if (!p || !dw) return;
      let body = '';
      if (dw.step === 1) body = dwStep1();
      else if (dw.step === 2) body = dwStep2();
      else if (dw.step === 3) body = dwStep3();
      else body = dwStep4();
      p.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
          <b style="font-size:14px">${__t('dw_title')} · ${__t('dw_step_n').replace('{n}', String(dw.step))}/4</b>
          <button class="btn btn-o btn-sm" onclick="dwClose()">${__t('dw_close')}</button>
        </div>
        <div style="font-size:11px;color:var(--text-ter);margin:-8px 0 12px">${__t('dw_help')}</div>${body}`;
    }

    function dwPanelError(msg) {
      const p = document.getElementById('dw-panel'); if (!p) return;
      p.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
          <b style="font-size:14px">${__t('dw_title')}</b>
          <button class="btn btn-o btn-sm" onclick="dwClose()">${__t('dw_close')}</button>
        </div>
        <div class="empty" style="background:var(--bg2)"><span style="color:var(--text-ter)">${msg}</span></div>
        <div style="margin-top:12px"><button class="btn btn-o btn-sm" onclick="dwReset()">${__t('dw_back_step1')}</button></div>`;
    }

    // Paso 1: orígenes + método. Sin orígenes → honestidad, no error.
    function dwStep1() {
      if (!devices.length) {
        return `<div class="empty" style="background:var(--bg2)"><span style="color:var(--text-ter)">${__t('dw_no_origins')}</span></div>
          <div style="margin-top:10px"><button class="btn btn-o btn-sm" onclick="dwClose()">${__t('dw_close')}</button></div>`;
      }
      return `
        <div style="font-size:11px;color:var(--text-secondary);margin-bottom:10px">${__t('dw_step1_desc')}</div>
        <div style="margin-bottom:12px;font-size:12px">
          <span style="font-size:10px;text-transform:uppercase;color:var(--text-muted);margin-right:8px">${__t('dw_metodo')}</span>
          <label style="margin-right:12px"><input type="radio" name="dw-met" value="lldp" checked> LLDP</label>
          <label style="display:inline-flex;align-items:center;gap:5px"><input type="radio" name="dw-met" value="cdp"> CDP <span style="font-size:10px;color:var(--orange)">${__t('dw_cdp_note')}</span></label>
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
          <div style="font-size:10px;text-transform:uppercase;color:var(--text-muted)">${__t('dw_origins')} (${devices.length})</div>
          <button class="btn btn-o btn-xs" onclick="dwToggleAllSources()">${__t('dw_pick_all')}</button>
        </div>
        <div id="dw-src" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:6px;max-height:220px;overflow-y:auto;border:1px solid var(--border-s);border-radius:6px;padding:10px">
          ${devices.map(d => `<label style="font-size:11px;display:flex;gap:6px;align-items:center"><input type="checkbox" value="${escAttr(d.id)}" class="dw-src-ck"><span>${esc(d.id)}${d.hostname ? ` <span style="color:var(--text-ter)">· ${esc(d.hostname)}</span>` : ''}${d.driver ? ` <span style="color:var(--text-ter)">· ${esc(d.driver)}</span>` : ''}</span></label>`).join('')}
        </div>
        <div style="margin-top:14px"><button class="btn btn-p btn-sm" onclick="dwScan()">${__t('dw_scan_btn')}</button></div>`;
    }

    function dwToggleAllSources() {
      const cks = document.querySelectorAll('#dw-src input[type="checkbox"]');
      if (!cks.length) return;
      const all = Array.from(cks).every(c => c.checked);
      cks.forEach(c => { c.checked = !all; });
    }

    async function dwScan() {
      const src = Array.from(document.querySelectorAll('#dw-src input:checked')).map(c => c.value);
      if (!src.length) { toast(__t('dw_pick_origins'), 'w'); return; }
      const metodo = document.querySelector('input[name="dw-met"]:checked')?.value || 'lldp';
      dw.sel = new Set(); dw.reused = false; dw.metodo = metodo;
      const p = document.getElementById('dw-panel'); if (!p) return;
      // Spinner honesto: el escaneo es síncrono y puede tardar hasta ~25 s.
      p.innerHTML = `<div class="bt" style="margin-bottom:12px">${__t('dw_scanning')}</div>
        <div class="sp" style="margin:20px auto"></div>
        <div style="font-size:11px;color:var(--text-ter)">${__t('dw_scanning_note')}</div>`;
      try {
        const r = await fetch(`${A}/discovery/scan`, {
          method: 'POST', headers: h(), body: JSON.stringify({ source_ids: src, metodo }),
          signal: AbortSignal.timeout(40000)
        });
        if (!r.ok) {
          let body = null; try { body = await r.json(); } catch (e) { }
          // 503 = fallo global; un equipo caído llega como origenes[].estado, no aquí.
          dwPanelError(`${__t('np_unverified')} · ${esc(_detText(body, 'HTTP ' + r.status))}`);
          return;
        }
        dw.scan = await r.json();
        dw.step = 2;
        dwRender();
      } catch (e) {
        dwPanelError(`${__t('np_unverified')} · ${esc(e.name === 'TimeoutError' ? __t('rep_timeout') : e.message)}`);
      }
    }

    // Paso 2: resultado del contrato. Los fallos POR EQUIPO se listan aquí, no como error total.
    function dwStep2() {
      const s = dw.scan; if (!s) return dwStep1();
      const res = s.resumen || {};
      const orig = Array.isArray(s.origenes) ? s.origenes : [];
      const rows = orig.map(o => {
        const omit = (Array.isArray(o.omitidos) ? o.omitidos : [])
          .map(x => `<div style="font-size:10px;color:var(--text-ter);margin-left:12px">· ${esc(x.nombre || '?')} — ${esc(x.motivo || '')}</div>`).join('');
        return `<div style="border-top:1px solid var(--border-s);padding:6px 0;font-size:11px">
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <b>${esc(o.id)}</b>
            <span class="badge" style="background:${o.estado === 'error' ? 'rgba(239,68,68,.2)' : '#202124'};${o.estado === 'error' ? 'color:var(--red)' : ''}">${esc(_dwEstadoText(o.estado))}</span>
            ${o.estado === 'ok' ? `<span style="color:var(--text-ter)">${esc(String(o.vecinos ?? 0))} vecinos · ${esc(String(o.candidatos ?? 0))} candidatos</span>` : ''}
            ${o.motivo ? `<span style="color:${o.estado === 'error' ? 'var(--red)' : 'var(--text-ter)'}">${esc(o.motivo)}</span>` : ''}
          </div>${omit}</div>`;
      }).join('');
      const cands = Array.isArray(s.candidatos) ? s.candidatos : [];
      const emptyMsg = cands.length ? '' : `
        <div class="empty" style="background:var(--bg2);margin-top:10px"><span style="color:var(--text-ter)">${esc(s.mensaje || __t('dw_no_candidates'))}</span></div>`;
      return `
        <div style="font-size:11px;color:var(--text-secondary);margin-bottom:8px">${__t('dw_scan_done')} · ${esc(_repTs(s.timestamp))} · ${esc(String(s.metodo || '').toUpperCase())}
          · ${__t('dw_res_origins')}: ${esc(String(res.origenes ?? 0))} (${esc(String(res.origenes_ok ?? 0))} OK / ${esc(String(res.origenes_con_error ?? 0))} ${__t('dw_res_failed')})</div>
        <div style="border:1px solid var(--border-s);border-radius:6px;padding:10px">${rows}</div>
        ${emptyMsg}
        <div style="display:flex;gap:8px;margin-top:14px">
          ${cands.length ? `<button class="btn btn-p btn-sm" onclick="dwToStep3()">${__t('dw_next_candidates')} (${cands.length})</button>` : ''}
          <button class="btn btn-o btn-sm" onclick="dwReset()">${__t('dw_back_step1')}</button>
        </div>`;
    }

    function dwToStep3() { if (dw) { dw.step = 3; dwRender(); } }

    // Paso 3: tabla de candidatos con checkbox; en_inventario desabilitado (no seleccionable).
    function dwStep3() {
      const s = dw.scan; if (!s) return dwStep1();
      const cands = Array.isArray(s.candidatos) ? s.candidatos : [];
      if (!cands.length) {
        return `<div class="empty" style="background:var(--bg2)"><span style="color:var(--text-ter)">${esc(s.mensaje || __t('dw_no_candidates'))}</span></div>
          <div style="margin-top:12px"><button class="btn btn-o btn-sm" onclick="dwReset()">${__t('dw_rescan')}</button></div>`;
      }
      const elig = cands.filter(c => !c.en_inventario);
      const allSel = elig.length > 0 && elig.every(c => dw.sel.has(c.ip));
      const rows = cands.map(c => {
        const check = !c.en_inventario;
        return `<tr style="border-top:1px solid var(--border-s);${check ? '' : 'opacity:.55'}">
          <td style="padding:6px 8px 6px 0;text-align:center"><input type="checkbox" ${check ? '' : 'disabled'} ${dw.sel.has(c.ip) ? 'checked' : ''} onchange="dwSel('${escAttr(c.ip)}', this.checked)"></td>
          <td style="padding:6px 8px 6px 0;font-family:var(--mono);font-size:11px">${esc(c.ip)}</td>
          <td style="padding:6px 8px 6px 0;font-size:11px">${esc(c.nombre_sugerido || '—')}</td>
          <td style="padding:6px 8px 6px 0">${c.plataforma_sugerida
            ? `<span class="badge" style="background:#202124">${esc(c.plataforma_sugerida)}</span>`
            : `<span class="badge" style="background:rgba(245,158,11,.2);color:#f59e0b">${__t('dw_no_platform')}</span>`}</td>
          <td style="padding:6px 8px 6px 0;font-size:10px;color:var(--text-ter)">${esc((c.origenes || [c.origen]).join(', '))}</td>
          <td style="padding:6px 8px 6px 0;font-size:10px;color:var(--text-ter)">${esc(c.interfaz_local || '—')} → ${esc(c.interfaz_remota || '—')}</td>
          <td style="padding:6px 8px 6px 0">${c.en_inventario
            ? `<span class="badge" style="background:var(--surface)">${__t('dw_in_inventory')}</span>`
            : `<span class="badge" style="background:var(--accent-l)">${__t('dw_proposed')}</span>`}</td>
        </tr>`;
      }).join('');
      return `
        ${dw.reused ? `<div class="bt" style="margin-bottom:10px">${__t('dw_reused')} · ${esc(_repTs(s.timestamp))}</div>` : ''}
        <table style="width:100%;border-collapse:collapse;font-size:12px;text-align:left">
          <thead><tr style="color:var(--text-muted);font-size:10px;text-transform:uppercase">
            <th style="text-align:center;width:34px"><input type="checkbox" ${allSel ? 'checked' : ''} onchange="dwSelAll(this.checked)"></th>
            <th style="padding:4px 8px 4px 0">IP</th>
            <th style="padding:4px 8px 4px 0">${__t('dw_col_name')}</th>
            <th style="padding:4px 8px 4px 0">${__t('dw_col_platform')}</th>
            <th style="padding:4px 8px 4px 0">${__t('dw_col_origen')}</th>
            <th style="padding:4px 8px 4px 0">${__t('dw_col_if')}</th>
            <th style="padding:4px 8px 4px 0">${__t('dw_col_state')}</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
        <div style="display:flex;gap:8px;margin-top:14px">
          <button class="btn btn-o btn-sm" onclick="dwReset()">${__t('dw_rescan')}</button>
          <button class="btn btn-p btn-sm" onclick="dwToStep4()" ${dw.sel.size ? '' : 'disabled'}>${__t('dw_next_confirm')} (${dw.sel.size})</button>
        </div>`;
    }

    function dwSel(ip, on) {
      if (!dw) return;
      if (on) dw.sel.add(ip); else dw.sel.delete(ip);
      dwRender();
    }
    function dwSelAll(on) {
      const s = dw && dw.scan; if (!s) return;
      (Array.isArray(s.candidatos) ? s.candidatos : []).forEach(c => {
        if (!c.en_inventario) { if (on) dw.sel.add(c.ip); else dw.sel.delete(c.ip); }
      });
      dwRender();
    }

    function dwToStep4() { if (dw && dw.sel.size) { dw.step = 4; dwRender(); } }

    // Paso 4: panel de confirmación estilo bulk-bar. NUNCA auto-import: [Importar] explícito.
    function dwStep4() {
      const s = dw.scan;
      const sel = Array.from(dw.sel);
      const chips = sel.map(ip => `<span class="tag" style="font-size:10px;padding:3px 8px;margin:2px">${esc(ip)}</span>`).join('');
      const needsDriver = !!s && (Array.isArray(s.candidatos) ? s.candidatos : []).some(c => dw.sel.has(c.ip) && !c.plataforma_sugerida);
      const drivers = ['ios', 'iosxr', 'nxos', 'junos', 'hp_comware', 'hp_procurve', 'huawei', 'alcatel_aos', 'ros', 'snmp'];
      return `
        <div class="bt" style="margin-bottom:12px">${__t('dw_confirm_title').replace('{n}', String(sel.length))}</div>
        <div style="margin-bottom:14px">${chips}</div>
        <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:end;margin-bottom:10px">
          <div class="fg" style="margin-bottom:0;min-width:130px"><label>${__t('dw_lbl_user')}</label><input id="dw-user" type="text" placeholder="admin" style="background:var(--bg);border:1px solid var(--border);color:var(--text);padding:6px 10px;border-radius:var(--radius-sm);font-size:12px"></div>
          <div class="fg" style="margin-bottom:0;min-width:130px"><label>${__t('dw_lbl_pass')}</label><input id="dw-pass" type="password" placeholder="••••••••" style="background:var(--bg);border:1px solid var(--border);color:var(--text);padding:6px 10px;border-radius:var(--radius-sm);font-size:12px"></div>
          <div class="fg" style="margin-bottom:0;min-width:130px"><label>${__t('dw_lbl_group')}</label><input id="dw-group" type="text" placeholder="Access" style="background:var(--bg);border:1px solid var(--border);color:var(--text);padding:6px 10px;border-radius:var(--radius-sm);font-size:12px"></div>
          <div class="fg" style="margin-bottom:0"><label>${__t('dw_lbl_tipo')}</label>
            <select id="dw-tipo" style="background:var(--bg);border:1px solid var(--border);color:var(--text);padding:6px 10px;border-radius:var(--radius-sm);font-size:12px">
              <option value="">router (default)</option><option value="switch">switch</option><option value="firewall">firewall</option><option value="server">server</option>
            </select></div>
          <div class="fg" style="margin-bottom:0"><label>${__t('dw_lbl_driver')}</label>
            <select id="dw-driver" style="background:var(--bg);border:1px solid var(--border);color:var(--text);padding:6px 10px;border-radius:var(--radius-sm);font-size:12px">
              <option value="">${__t('dw_driver_auto')}</option>${drivers.map(d => `<option value="${d}">${d}</option>`).join('')}
            </select></div>
        </div>
        <div style="font-size:11px;color:var(--orange);margin-top:6px">${__t('dw_cred_note')}</div>
        ${needsDriver ? `<div style="font-size:11px;color:var(--orange);margin-top:6px">${__t('dw_driver_needed')}</div>` : ''}
        <div style="display:flex;gap:8px;margin-top:14px">
          <button class="btn btn-p btn-sm" onclick="dwImport()">${__t('dw_import_btn')} (${sel.length})</button>
          <button class="btn btn-o btn-sm" onclick="dwToStep3()">${__t('dw_cancel_back')}</button>
        </div>`;
    }

    async function dwImport() {
      const p = document.getElementById('dw-panel'); if (!p || !dw) return;
      const ips = Array.from(dw.sel); if (!ips.length) return;
      const body = {
        candidates: ips,
        username: document.getElementById('dw-user')?.value.trim() || '',
        password: document.getElementById('dw-pass')?.value || '',
        group: document.getElementById('dw-group')?.value.trim() || null,
        tipo: document.getElementById('dw-tipo')?.value || null,
        driver: document.getElementById('dw-driver')?.value || null
      };
      p.innerHTML = `<div class="bt" style="margin-bottom:12px">${__t('dw_importing')}</div><div class="sp" style="margin:20px auto"></div>`;
      try {
        const r = await fetch(`${A}/discovery/import`, {
          method: 'POST', headers: h(), body: JSON.stringify(body), signal: AbortSignal.timeout(30000)
        });
        if (!r.ok) {
          let b = null; try { b = await r.json(); } catch (e) { }
          dwPanelError(`${__t('np_unverified')} · ${esc(_detText(b, 'HTTP ' + r.status))}`);
          return;
        }
        const res = await r.json();
        const rsum = res.resumen || {};
        // El inventario cambió: refrescar estado global; candidados siguen "Propuesto" (nunca se recarga la tabla sola).
        if ((res.creados || []).length) { loadInventory(); loadDev(); }
        p.innerHTML = `
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
            <b style="font-size:14px">${__t('dw_title')}</b>
            <button class="btn btn-o btn-sm" onclick="dwClose()">${__t('dw_close')}</button>
          </div>
          <div class="bt" style="margin-bottom:12px">${__t('dw_import_done').replace('{n}', String(rsum.creados ?? 0)).replace('{m}', String(rsum.solicitados ?? ips.length))}</div>
          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;font-size:12px">
            <div style="border:1px solid var(--border-s);border-radius:6px;padding:10px">
              <b style="color:var(--green)">${__t('dw_created')} (${esc(String(rsum.creados ?? 0))})</b>
              <ul style="margin-top:6px;padding-left:16px">${_dwResList(res.creados, 'var(--text-secondary)')}</ul>
            </div>
            <div style="border:1px solid var(--border-s);border-radius:6px;padding:10px">
              <b style="color:var(--orange)">${__t('dw_existed')} (${esc(String(rsum.ya_existian ?? 0))})</b>
              <ul style="margin-top:6px;padding-left:16px">${_dwResList(res.ya_existian, 'var(--text-ter)')}</ul>
            </div>
            <div style="border:1px solid var(--border-s);border-radius:6px;padding:10px">
              <b style="color:var(--red)">${__t('dw_failed')} (${esc(String(rsum.fallidos ?? 0))})</b>
              <ul style="margin-top:6px;padding-left:16px">${_dwResList(res.fallidos, 'var(--red)')}</ul>
            </div>
          </div>
          <div style="font-size:11px;color:var(--text-ter);margin-top:8px">${__t('dw_after_import')}</div>
          <div style="margin-top:12px"><button class="btn btn-o btn-sm" onclick="dwReset()">${__t('dw_rescan')}</button></div>`;
      } catch (e) {
        dwPanelError(`${__t('np_unverified')} · ${esc(e.message)}`);
      }
    }
