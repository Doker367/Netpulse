/**
 * NetPulse NOC — detalle de equipo (modal #dp) y restos del detalle antiguo por
 * pestañas (views/device-detail.js).
 *
 * Extraída del <script> inline de app/static/dashboard.html (Tarjeta 10D-H,
 * lote h), sin cambio de comportamiento: líneas en el mismo orden relativo que
 * en el monolito (dTab < setDTab < selD < rCmdBig < closeD < qaB < qaS <
 * qaP < qaM < loadF < loadI < curC < loadC < uc < dep < cDep < dlC <
 * renderCmd < rCmd < tplT < renderTpl < tplP < tplR < tplA < tplC; dos tramos
 * contiguos del monolito separados por los helpers compartidos que QUEDAN en
 * el inline: _fmtBytes/_fmtUptime/alDur y populateCmdDevices).
 *
 * Decisión de topología (la tarjeta pide decidir con grep): DOS archivos.
 * El detalle es una unidad cerrada (el modal #dp está fuera de las 24 vistas)
 * y la portada tiene sus latidos (dashboard.js). Entre ambos archivos NO hay
 * un solo helper privado compartido (verificado por grep): los cruces son
 * llamadas en tiempo de llamada (selD→renderD, rCmd→updStats) que resuelven
 * contra window sin duplicar nada; por eso el corte en dos es limpio.
 *
 * Carga: script clásico sin defer/async, tras compare.js y ANTES de
 * views/dashboard.js (orden que fija la tarjeta 10D-H). Top-level = solo
 * declaraciones `function` + `let` de estado: nada se lee a la evaluación.
 *
 * Contenido nombre a nombre:
 * - Estado: `let dTab` (pestaña activa del #dp) y `let cmdH` (simulado
 *   historial del terminal del detalle). cmdH era un ítem de la línea let
 *   compartida del inline (`let devices, sel, aTab, cmdH, bSel, bMode`,
 *   L3026 del archivo pre-lote); sus ÚNICOS consumidores son rCmdBig y rCmd,
 *   ambos aquí (verificado por grep), así que deja el grupo y viaja como let
 *   interno de este archivo. En el inline quedan devices/sel/aTab/bSel/bMode.
 * - `setDTab(t)` — tabs del #dp; ATENCIÓN: el parámetro `t` eclipsa el token
 *   (§7.3 del mapa), conservado tal cual.
 * - `selD(id)` — abre el modal: 4 fetch en paralelo, setLang(currentLang),
 *   repinta #dm/#tabs/#dc/#qa, intervalo de 5 s en `window.dLiveT`
 *   (logout en core.js lo limpia en tiempo de llamada, mismo patrón que
 *   repState en el Lote D). Genera los onclick de setDTab, qaB/qaS/qaP/qaM,
 *   dlC y rCmdBig, todos definidos aquí mismo.
 * - `rCmdBig(id)` — terminal del tab Comandos (c-big/t-big); dentro `t` es el
 *   elemento del DOM, NO el JWT (usa h(), que lee el token global) — trampa
 *   §7.3 conservada tal cual.
 * - `closeD` — cierra el modal; lo llaman el markup del #dp (botonera),
 *   delDev (views/devices.js) y la propia ventana.
 * - Quick actions: `qaB` (POST backup + loadBk), `qaS`, `qaP` (el prompt()
 *   es preexistente) y `qaM` (swView('compare') + dCmp, views/compare.js).
 * - `dlC` — descarga de config; lee `curC` (estado del detalle antiguo).
 * - Código muerto del mapa §2.3, movido tal cual y NO revivido: loadF,
 *   loadI, loadC, uc, dep, cDep (restos del detalle antiguo por pestañas)
 *   y renderCmd/rCmd + renderTpl/tplP/tplR/tplA/tplC con `let curC` y
 *   `let tplT` (solo se invocaban desde HTML que genera código muerto).
 *
 * Lo que NO viajó (regla 2, resuelto en tiempo de llamada por grep):
 * core.js (A, h, t, toast, __t, setLang, _npTick), el bloque de estado
 * compartido del inline (devices/sel/bSel/bMode), esc/escAttr/iconTextHTML
 * (inline), swView (inline, lote k), loadBk (backups.js), dCmp (compare.js),
 * renderD/updStats (dashboard.js — mismo lote, archivo distinto).
 */

let cmdH = [];

    let dTab = 'Overview';
    function setDTab(t) {
      dTab = t;
      document.querySelectorAll('#tabs .tab-btn').forEach(b => {
        b.style.color = b.dataset.t === t ? 'var(--text)' : 'var(--text-ter)';
        b.style.borderBottom = b.dataset.t === t ? '2px solid var(--accent)' : '2px solid transparent';
      });
      document.querySelectorAll('.tab-content').forEach(c => c.style.display = c.id === 'tc-' + t ? 'block' : 'none');
    }

    // ── DETAIL (all-in-one) ──
    async function selD(id) {
      if (window.dLiveT) clearInterval(window.dLiveT); window.dLiveT = null;
      sel = devices.find(d => d.id === id); renderD();
      document.getElementById('dp').classList.add('active');
      const d = sel;
      document.getElementById('dt').textContent = id;
      const detailHealth = d._online === true ? 'ov_online' : d._online === false ? 'ov_offline' : 'ov_unverified';
      document.getElementById('dm').innerHTML = `<span class="badge" style="background:var(--surface);color:var(--text-secondary)">${esc(d.driver.toUpperCase())}</span> <span class="badge" style="background:var(--surface);color:var(--text-secondary)">${esc(d.type)}</span> <span style="font-size:12px;color:var(--text-ter)">:${esc(d.port)}</span> <span data-i18n="${detailHealth}" style="margin-left:8px;font-size:12px;color:${d._online === true ? 'var(--green)' : d._online === false ? 'var(--red)' : 'var(--text-ter)'}">${__t(detailHealth)}</span>`;

      // Tabs Header
      document.getElementById('tabs').innerHTML = `
        <div style="display:flex;gap:24px;border-bottom:1px solid var(--border);margin-bottom:16px;padding:0 8px">
          ${[['Overview', 'tab_ov'], ['Interfaces', 'tab_if'], ['Config', 'tab_cfg'], ['Comandos', 'tab_cmd']].map(([t, key]) =>
        `<div class="tab-btn" data-t="${t}" data-i18n="${key}" onclick="setDTab('${t}')" style="padding:10px 4px;cursor:pointer;font-size:13px;font-weight:500;transition:all .2s;color:var(--text-ter);border-bottom:2px solid transparent">${__t(key)}</div>`
      ).join('')}
        </div>
      `;

      document.getElementById('dc').innerHTML = `<div class="sp" style="margin:20px auto"></div><div style="text-align:center;font-size:11px;color:var(--text-ter)" data-i18n="m_loading">${__t('m_loading')}</div>`;

      const encId = encodeURIComponent(id);
      // Load all data in parallel
      const [rF, rI, rC] = (sel._online !== false) ? await Promise.all([
        fetch(`${A}/facts/${encId}`, { headers: h() }).catch(() => null),
        fetch(`${A}/facts/${encId}/interfaces`, { headers: h() }).catch(() => null),
        fetch(`${A}/config/${encId}/raw`, { headers: h() }).catch(() => null),
      ]) : [null, null, null];
      const facts = rF?.ok ? await rF.json() : null;
      const ifaces = rI?.ok ? await rI.json() : null;
      const config = rC?.ok ? await rC.text() : '\u26a0\ufe0f No disponible';

      if (facts && !sel._online) {
        sel._online = true;
        renderD();
      }

      let cpu = 0, memPct = 0, hddPct = 0, memStr = '—';
      try {
        if (sel._online === false && !facts) throw new Error('Device Offline');
        const rr = await fetch(`${A}/facts/${encId}/resources`, { headers: h() });
        if (rr.ok) {
          const rd = await rr.json(); cpu = rd.cpu || 0;
          if (rd.memory_total) {
            const mu = rd.memory_used || rd.memory_total - rd.memory_free;
            memPct = Math.round(mu / rd.memory_total * 100); memStr = `${Math.round(mu / 1024 / 1024)}/${Math.round(rd.memory_total / 1024 / 1024)}MB`;
          }
          if (rd.hdd_total) hddPct = Math.round((rd.hdd_total - rd.hdd_free) / rd.hdd_total * 100);
          sel._cpu = cpu + '%'; sel._mem = memPct + '%'; sel._dsk = hddPct + '%';
          const cEl = document.getElementById(`rcpu-${id}`); if (cEl) cEl.textContent = sel._cpu;
          const mEl = document.getElementById(`rmem-${id}`); if (mEl) mEl.textContent = sel._mem;
          const dEl = document.getElementById(`rdsk-${id}`); if (dEl) dEl.textContent = sel._dsk;
        }
      } catch (e) { }

      const ifList = ifaces?.interfaces || [];
      document.getElementById('qa').innerHTML = `<div style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:24px;align-items:center;">
        ${[['<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="22" y1="12" x2="2" y2="12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><line x1="6" y1="16" x2="6.01" y2="16"/><line x1="10" y1="16" x2="10.01" y2="16"/></svg> Backup', `qaB('${id}')`, 'qa_bk'], ['<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="22" y1="12" x2="2" y2="12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><line x1="6" y1="16" x2="6.01" y2="16"/><line x1="10" y1="16" x2="10.01" y2="16"/></svg> Guardar RAM', `qaS('${id}')`, 'qa_sv'], ['<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 12m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0"/><path d="M16.24 7.76a6 6 0 0 1 0 8.49"/><path d="M7.76 16.24a6 6 0 0 1 0-8.49"/></svg> Ping Test', `qaP('${id}')`, 'qa_ping'], ['<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M12 3v12m-5-5 5 5 5-5"/></svg> Exportar', `dlC('${id}')`, 'qa_exp'], ['<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M3 8h18m-4-4 4 4-4 4M21 16H3m4-4-4 4 4 4"/></svg> Comparar', `qaM('${id}')`, 'qa_cmp']]
          .map(([t, f, k]) => `<button class="btn btn-o btn-sm" data-i18n="${k}" onclick="${f}" style="flex:1;min-width:120px;padding:10px 14px;font-size:12px;font-weight:600;border:1px solid var(--border-s);background:var(--elevated);border-radius:var(--radius);transition:all 0.2s;box-shadow:0 2px 6px rgba(0,0,0,0.08);display:flex;flex-direction:row;align-items:center;justify-content:center;gap:8px;white-space:nowrap" onmouseover="this.style.borderColor='var(--accent)';this.style.transform='translateY(-1px)'" onmouseout="this.style.borderColor='var(--border-s)';this.style.transform='translateY(0)'">${__t(k)}</button>`).join('')}
      </div><div id="ping-result" style="display:none;margin-bottom:16px;"></div>`;
      setLang(currentLang);

      document.getElementById('dc').innerHTML = `
  <div class="tab-content" id="tc-Overview" style="display:none">
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:20px;margin-bottom:24px">
      <div class="mp" style="background:var(--elevated);padding:24px;border-radius:var(--radius-lg);border:1px solid var(--border);box-shadow:0 4px 12px rgba(0,0,0,0.1)"><h4 class="mph" data-i18n="nav_inventory" style="font-size:14px;color:var(--text);margin-bottom:16px;border-bottom:1px solid var(--border-s);padding-bottom:12px;text-transform:uppercase;letter-spacing:0.5px">Inventory</h4>
        <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:16px;">
          ${[['Hostname', facts?.hostname], ['OS', (facts?.os_version || '').substring(0, 35)], ['Modelo', facts?.model], ['Vendor', facts?.vendor], ['Serial', facts?.serial_number], ['Uptime', facts?.uptime > 0 ? Math.floor(facts.uptime / 60) + ' min' : 'N/A']]
          .map(([k, v]) => `<div><div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px">${k}</div><div style="font-weight:600;font-size:14px;color:${v === 'Unknown' || v === 'N/A' ? 'var(--text-ter)' : 'var(--text)'}">${esc(v || '?')}</div></div>`).join('')}
        </div>
      </div>
      <div class="mp" style="background:var(--elevated);padding:24px;border-radius:var(--radius-lg);border:1px solid var(--border);box-shadow:0 4px 12px rgba(0,0,0,0.1)"><h4 class="mph" data-i18n="nav_infrastructure" style="font-size:14px;color:var(--text);margin-bottom:16px;border-bottom:1px solid var(--border-s);padding-bottom:12px;text-transform:uppercase;letter-spacing:0.5px">Hardware</h4>
        <div id="live-hw" style="display:flex;gap:24px;justify-content:center;align-items:center;padding:16px 0">
          ${[{ label: 'CPU', pct: cpu, c: 'var(--accent)' }, { label: 'RAM', pct: memPct, c: 'var(--green)', s: memStr }, { label: 'DISK', pct: hddPct, c: 'var(--yellow)' }].map(o => `
          <div style="text-align:center;flex:1"><div class="donut" style="--pct:${o.pct};width:80px;height:80px;margin:0 auto"><svg viewBox="0 0 36 36" fill="none" stroke="currentColor"><path class="donut-bg" d="M18 2.0845a15.9155 15.9155 0 0 1 0 31.831a15.9155 15.9155 0 0 1 0-31.831"/><path class="donut-fg" stroke="${o.c}" stroke-dasharray="${o.pct},100" d="M18 2.0845a15.9155 15.9155 0 0 1 0 31.831a15.9155 15.9155 0 0 1 0-31.831"/></svg><div class="donut-lbl" style="font-size:16px;font-weight:700">${o.pct}%</div></div><div style="font-size:12px;font-weight:600;margin-top:12px;color:var(--text-secondary)">${o.label}</div>${o.s ? '<div style="font-size:10px;color:var(--text-ter);margin-top:4px">' + o.s + '</div>' : ''}</div>`).join('')}
        </div>
      </div>
    </div>
  </div>

  <div class="tab-content" id="tc-Interfaces" style="display:none">
    <div class="mp"><h4 class="mph"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a17 17 0 0 1 0 20 17 17 0 0 1 0-20"/></svg> Interfaces de Red <span class="badge" style="background:var(--surface);float:right">${ifList.length}</span></h4>
      ${ifList.length ? `<table class="tbl" style="width:100%;margin-top:8px"><tr><th style="padding:10px;text-align:left;color:var(--text-muted);font-size:10px">INTERFAZ</th><th style="padding:10px;text-align:left;color:var(--text-muted);font-size:10px">ESTADO</th><th style="padding:10px;text-align:left;color:var(--text-muted);font-size:10px">MAC ADDRESS</th><th style="padding:10px;text-align:left;color:var(--text-muted);font-size:10px">VELOCIDAD</th></tr>
      ${ifList.slice(0, 30).map(i => `<tr style="border-bottom:1px solid var(--border-s)"><td style="padding:10px;font-weight:600;font-size:13px;color:var(--text)">${i.name}</td><td style="padding:10px"><span class="badge ${i.is_up ? 'badge-g' : 'badge-r'}">${i.is_up ? 'UP' : 'DOWN'}</span></td><td style="padding:10px;font-size:12px;font-family:var(--mono);font-weight:500;color:var(--text-secondary)">${i.mac_address || '—'}</td><td style="padding:10px;font-size:12px;color:var(--text-secondary)">${i.speed > 0 ? i.speed + ' Mbps' : '—'}</td></tr>`).join('')}
      ${ifList.length > 30 ? `<tr><td colspan="4" style="padding:10px;text-align:center;color:var(--text-ter);font-size:11px">Mostrando 30 de ${ifList.length}</td></tr>` : ''}</table>`
          : '<div style="padding:32px;text-align:center;color:var(--text-ter);font-size:13px">N/A o No disponibles mediante API</div>'}
    </div>
  </div>

  <div class="tab-content" id="tc-Config" style="display:none">
    <div class="mp" style="display:flex;flex-direction:column;min-height:400px"><h4 class="mph" style="display:flex;justify-content:space-between;align-items:center"><span><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20 7h-9"/><path d="M14 17H5"/><circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/></svg> Configuración Activa</span> <span style="font-size:10px;color:var(--text-ter)">${config.split('\\n').filter(l => l.trim()).length} LÍNEAS</span></h4>
      ${config.includes('\u26a0\ufe0f') ? `<div style="background:var(--yellow-bg);border:1px solid rgba(251,191,36,.3);border-radius:var(--radius);padding:16px;font-size:13px;color:var(--orange);line-height:1.6;margin-top:10px">${iconTextHTML(config).replace(/\\n/g, '<br>')}</div>` : `<pre style="flex:1;overflow:auto;margin-top:10px;font-size:12px;line-height:1.6;color:var(--text-secondary);background:var(--bg);padding:16px;border-radius:var(--radius-sm);font-family:var(--mono);white-space:pre;border:1px solid var(--border-s)">${esc(config.substring(0, 5000))}${config.length > 5000 ? '\\n\\n... (truncado por memoria UI)' : ''}</pre>`}
    </div>
  </div>

  <div class="tab-content" id="tc-Comandos" style="display:none">
    <div class="mp" style="min-height:400px"><h4 class="mph"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M4 17l6-6-6-6"/><path d="M12 19h8"/></svg> Exec terminal</h4>
      <div class="fr2" style="background:var(--bg);padding:12px;border-radius:var(--radius-sm);border:1px solid var(--border);margin:10px 0"><textarea id="c-big" placeholder="${id.includes('mikrotik') ? '/system/resource/print' : 'show version'}" rows="2" style="min-height:40px;flex:1;background:transparent;border:0;outline:none;resize:vertical" onkeydown="if(event.key==='Enter'&&event.ctrlKey)rCmdBig('${id}')"></textarea>
        <div style="display:flex;flex-direction:column;gap:6px">
          <button class="btn btn-p btn-sm" onclick="rCmdBig('${id}')" style="padding:8px 16px;font-weight:600">Enter <svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><polygon points="6 4 20 12 6 20"/></svg></button>
          <button class="btn btn-o btn-xs" onclick="document.getElementById('t-big').innerHTML='<span style=\\'color:var(--text-ter)\\'>Limpiado</span>'">⌫ Clear</button>
        </div>
      </div>
      <div class="terminal" id="t-big" style="background:var(--panel);border:1px solid var(--border-s);padding:16px;border-radius:var(--radius-sm);min-height:200px;max-height:400px;overflow-y:auto;font-size:12px;font-family:var(--mono)"><span class="p">${id}$</span> Ready.</div>
    </div>
  </div>
  </div>
  `;
      setDTab('Overview');
      document.getElementById('dp').scrollIntoView({ behavior: 'smooth', block: 'nearest' });

      window.dLiveT = setInterval(async () => {
        if (!document.getElementById('dp').classList.contains('active') || sel?.id !== id) {
          clearInterval(window.dLiveT); return;
        }
        let c = 0, mp = 0, hp = 0, ms = '—';
        try {
          const rr = await fetch(`${A}/facts/${encId}/resources?_t=${Date.now()}`, { headers: h() });
          if (rr.ok) {
            const rd = await rr.json(); c = Math.round(rd.cpu || 0);
            if (rd.memory_total) {
              const mu = rd.memory_used || rd.memory_total - rd.memory_free;
              mp = Math.round(mu / rd.memory_total * 100); ms = `${Math.round(mu / 1024 / 1024)}/${Math.round(rd.memory_total / 1024 / 1024)}MB`;
            }
            if (rd.hdd_total) hp = Math.round((rd.hdd_total - rd.hdd_free) / rd.hdd_total * 100);

            const hwEl = document.getElementById('live-hw');
            if (hwEl) {
              hwEl.innerHTML = [{ label: 'CPU', pct: c, c: 'var(--accent)' }, { label: 'RAM', pct: mp, c: 'var(--green)', s: ms }, { label: 'DISK', pct: hp, c: 'var(--yellow)' }].map(o => `
              <div style="text-align:center;flex:1"><div class="donut" style="--pct:${o.pct};width:64px;height:64px;margin:0 auto"><svg viewBox="0 0 36 36" fill="none" stroke="currentColor"><path class="donut-bg" d="M18 2.0845a15.9155 15.9155 0 0 1 0 31.831a15.9155 15.9155 0 0 1 0-31.831"/><path class="donut-fg" stroke="${o.c}" stroke-dasharray="${o.pct},100" d="M18 2.0845a15.9155 15.9155 0 0 1 0 31.831a15.9155 15.9155 0 0 1 0-31.831"/></svg><div class="donut-lbl" style="font-size:12px">${o.pct}%</div></div><div style="font-size:11px;font-weight:600;margin-top:8px;color:var(--text-secondary)">${o.label}</div>${o.s ? '<div style="font-size:9px;color:var(--text-ter)">' + o.s + '</div>' : ''}</div>`).join('');
            }
          }
        } catch (e) { }
      }, 5000);
    }

    async function rCmdBig(id) {
      const i = document.getElementById('c-big'), t = document.getElementById('t-big');
      const c = i.value.split('\n').filter(c => c.trim()); if (!c.length) return;
      cmdH.push(i.value); let o = ''; t.innerHTML = '<div class="sp"></div>';
      for (const q of c) {
        o += `<span class="p">${id}$ </span>${esc(q)}\n`;
        const r = await fetch(`${A}/command/${encodeURIComponent(id)}`, { method: 'POST', headers: h(), body: JSON.stringify({ commands: [q] }) });
        const d = await r.json();
        const txt = d.results?.[0]?.output || d.results?.[0]?.error || '';
        if (!txt && (q.startsWith('ping') || q.startsWith('traceroute'))) o += `<span style="color:var(--cyan);font-size:11px"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg> Usá <svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 12m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0"/><path d="M16.24 7.76a6 6 0 0 1 0 8.49"/><path d="M7.76 16.24a6 6 0 0 1 0-8.49"/></svg> Ping en acciones rápidas</span>\n`;
        else if (!txt) o += `<span style="color:var(--orange);font-size:11px"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m12 3 10 18H2Z"/><path d="M12 9v4M12 17h.01"/></svg> Sin salida</span>\n`;
        else o += `${esc(txt)}\n`;
      }
      t.innerHTML = o; t.scrollTop = t.scrollHeight;
    }

    function closeD() { sel = null; document.getElementById('dp').classList.remove('active'); if (window.dLiveT) clearInterval(window.dLiveT); window.dLiveT = null; renderD() }

    async function qaB(id) { const r = await fetch(`${A}/config/${encodeURIComponent(id)}/backup`, { method: 'POST', headers: h() }); const d = await r.json(); toast(d.success ? '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg> Backup creado' : '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> Error', ''); loadBk() }
    async function qaS(id) {
      try {
        const r = await fetch(`${A}/config/${encodeURIComponent(id)}/save`, { method: 'POST', headers: h() });
        const d = await r.json().catch(() => ({}));
        if (r.ok && d.success) { toast('<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="22" y1="12" x2="2" y2="12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><line x1="6" y1="16" x2="6.01" y2="16"/><line x1="10" y1="16" x2="10.01" y2="16"/></svg> Configuración guardada (NVRAM)', 's'); }
        else { toast(`<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> No se pudo guardar: ${d.error || d.detail || 'error'}`, 'error'); }
      } catch (e) { toast('<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> Error: ' + e.message, 'error'); }
    }
    async function qaP(id) {
      const devObj = devices.find(d => d.id === id);
      const defTarget = (devObj?.hostname && devObj.hostname !== '127.0.0.1') ? devObj.hostname : (id.includes('mikrotik') ? '8.8.8.8' : '1.1.1.1');
      const tgt = prompt('Target:', defTarget); if (!tgt) return;
      const el = document.getElementById('ping-result'); el.style.display = 'block'; el.innerHTML = '<div class="sp"></div>';
      const r = await fetch(`${A}/facts/${encodeURIComponent(id)}/ping?target=${encodeURIComponent(tgt)}`, { method: 'POST', headers: h() });
      if (!r.ok) { el.innerHTML = '<div style="color:var(--red);padding:8px">Error en prueba de ping</div>'; return }
      const d = await r.json();
      el.innerHTML = `<div class="mp"><h4 class="mph"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 12m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0"/><path d="M16.24 7.76a6 6 0 0 1 0 8.49"/><path d="M7.76 16.24a6 6 0 0 1 0-8.49"/></svg> Ping a ${tgt}</h4><div style="display:flex;gap:16px;padding:8px">
    <div><span style="color:var(--text-ter);font-size:10px">Paquetes</span><br><span style="font-weight:700">${d.probes_received || 0}/${d.probes_sent || 0}</span></div>
    <div><span style="color:var(--text-ter);font-size:10px">RTT min</span><br><span style="font-weight:700">${(d.rtt_min || 0).toFixed(1)}ms</span></div>
    <div><span style="color:var(--text-ter);font-size:10px">RTT avg</span><br><span style="font-weight:700">${(d.rtt_avg || 0).toFixed(1)}ms</span></div>
    <div><span style="color:var(--text-ter);font-size:10px">RTT max</span><br><span style="font-weight:700">${(d.rtt_max || 0).toFixed(1)}ms</span></div>
    <span style="color:${d.success ? 'var(--green)' : 'var(--red)'};font-weight:600">${d.success ? '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg> Éxito' : '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> Falló'}</span>
  </div></div>`;
    }
    function qaM(id) { swView('compare'); document.getElementById('cmp-a').value = id; if (document.getElementById('cmp-b').value === id) document.getElementById('cmp-b').selectedIndex = 1; dCmp() }

    async function loadF(id, el) {
      const r = await fetch(`${A}/facts/${encodeURIComponent(id)}`, { headers: h() });
      if (!r.ok) { el.innerHTML = '<div class="empty"><div class="icon"><svg class="ic" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false" style="opacity:0.4;color:var(--text-ter)"><path d="m12 3 10 18H2Z"/><path d="M12 9v4M12 17h.01"/></svg></div>Error</div>'; return }
      const d = await r.json();

      // Load resources
      let cpu = 0, memPct = 0, hddPct = 0;
      try {
        const rr = await fetch(`${A}/facts/${encodeURIComponent(id)}/resources`, { headers: h() });
        if (rr.ok) {
          const rd = await rr.json();
          if (rd.memory_total) {
            const mu = rd.memory_used || rd.memory_total - rd.memory_free;
            memPct = Math.round(mu / rd.memory_total * 100);
          }
          cpu = rd.cpu || 0;
          if (rd.hdd_total) hddPct = Math.round((rd.hdd_total - rd.hdd_free) / rd.hdd_total * 100);
        }
      } catch (e) { }

      el.innerHTML = `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px;margin-bottom:12px">
    ${[['Hostname', d.hostname], ['OS', (d.os_version || '').substring(0, 28)], ['Modelo', d.model], ['Vendor', d.vendor], ['Serial', d.serial_number], ['Uptime', d.uptime > 0 ? Math.floor(d.uptime / 60) + ' min' : 'N/A'], ['Interfaces', d.interface_count + '']]
          .map(([k, v]) => `<div class="fg"><label>${k}</label><div style="font-weight:500;font-size:13px;color:${v === 'Unknown' || v === 'N/A' ? 'var(--text-ter)' : 'var(--text)'}">${esc(v || '?')}</div></div>`).join('')}</div>
    <div style="display:flex;gap:16px;margin-top:8px;justify-content:center">
      ${[
          { label: 'CPU', pct: cpu, color: 'var(--accent)' },
          { label: 'Mem', pct: memPct, color: 'var(--green)' },
          { label: 'Disco', pct: hddPct, color: 'var(--yellow)' }
        ].map(o => `<div style="text-align:center"><div class="donut" style="--pct:${o.pct}"><svg viewBox="0 0 36 36" fill="none" stroke="currentColor"><path class="donut-bg" d="M18 2.0845a15.9155 15.9155 0 0 1 0 31.831a15.9155 15.9155 0 0 1 0-31.831"/><path class="donut-fg" stroke="${o.color}" stroke-dasharray="${o.pct},100" d="M18 2.0845a15.9155 15.9155 0 0 1 0 31.831a15.9155 15.9155 0 0 1 0-31.831"/></svg><div class="donut-lbl">${o.pct}%</div></div><div style="font-size:10px;margin-top:4px;color:var(--text-ter)">${o.label}</div></div>`).join('')}
    </div>`
    }

    async function loadI(id, el) {
      try {
        const r = await fetch(`${A}/facts/${encodeURIComponent(id)}/interfaces`, { headers: h() });
        if (!r.ok || r.status !== 200) { el.innerHTML = '<div class="empty"><span class="icon"><svg class="ic" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false" style="opacity:0.4;color:var(--text-ter)"><circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a17 17 0 0 1 0 20 17 17 0 0 1 0-20"/></svg></span><br>Interfaces no disponibles</div>'; return }
        const d = await r.json();
        if (!d.interfaces || !d.interfaces.length) { el.innerHTML = '<div class="empty"><span class="icon"><svg class="ic" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false" style="opacity:0.4;color:var(--text-ter)"><circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a17 17 0 0 1 0 20 17 17 0 0 1 0-20"/></svg></span><br>0 interfaces</div>'; return }
        el.innerHTML = `<div style="margin-bottom:8px;font-size:12px;color:var(--text-ter)">${d.total} total · <span style="color:var(--green);font-weight:500">${d.up_count} UP</span> · <span style="color:var(--red);font-weight:500">${d.down_count} DOWN</span></div>
    <div style="max-height:300px;overflow-y:auto;border:1px solid var(--border);border-radius:6px"><table><tr><th>Interfaz</th><th>Estado</th><th>MAC</th><th>Speed</th></tr>
    ${d.interfaces.map(i => `<tr><td style="font-weight:500">${i.name}</td><td><span class="badge ${i.is_up ? 'badge-g' : 'badge-r'}">${i.is_up ? 'UP' : 'DOWN'}</span></td><td style="font-size:10px;color:var(--text-ter)">${i.mac_address || '—'}</td><td>${i.speed || '—'}</td></tr>`).join('')}</table></div>`
      } catch (e) { el.innerHTML = '<div class="empty"><span class="icon"><svg class="ic" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false" style="opacity:0.4;color:var(--text-ter)"><circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a17 17 0 0 1 0 20 17 17 0 0 1 0-20"/></svg></span><br>Error</div>' }
    }

    let curC = '';
    async function loadC(id, el) {
      const r = await fetch(`${A}/config/${encodeURIComponent(id)}/raw`, { headers: h() });
      if (!r.ok) { el.innerHTML = '<div class="empty"><span class="icon"><svg class="ic" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false" style="opacity:0.4;color:var(--text-ter)"><path d="M20 7h-9"/><path d="M14 17H5"/><circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/></svg></span><br>Error al obtener config</div>'; return }
      curC = await r.text();
      if (!curC || curC.includes('\u26a0\ufe0f') || curC.includes('Config no disponible')) {
        const msg = curC.includes('mikrotik') || id.includes('mikrotik')
          ? '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m12 3 10 18H2Z"/><path d="M12 9v4M12 17h.01"/></svg> MikroTik usa API RouterOS. La config no se obtiene vía NAPALM.\n\n<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m16 3 5 5-4 1-4 4-1 5-6-6 5-1 4-4zM2 22l6-6"/></svg> Usá las pestañas Facts e Interfaces para info.\n<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m16 3 5 5-4 1-4 4-1 5-6-6 5-1 4-4zM2 22l6-6"/></svg> El botón <svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="22" y1="12" x2="2" y2="12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><line x1="6" y1="16" x2="6.01" y2="16"/><line x1="10" y1="16" x2="10.01" y2="16"/></svg> Backup funciona correctamente.'
          : curC || '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m12 3 10 18H2Z"/><path d="M12 9v4M12 17h.01"/></svg> Config no disponible para este dispositivo.';
        el.innerHTML = `<pre style="color:var(--orange);font-size:12px;white-space:pre-wrap;line-height:1.6">${iconTextHTML(msg)}</pre>`;
        return;
      }
      el.innerHTML = `<div class="actions" style="margin-bottom:8px">
    <button class="btn btn-o btn-sm" onclick="dlC('${id}')"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M12 3v12m-5-5 5 5 5-5"/></svg> Descargar</button>
    <button class="btn btn-o btn-sm" onclick="qaB('${id}')"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="22" y1="12" x2="2" y2="12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><line x1="6" y1="16" x2="6.01" y2="16"/><line x1="10" y1="16" x2="10.01" y2="16"/></svg> Backup</button>
    <button class="btn btn-p btn-sm" id="db" style="display:none" onclick="dep('${id}')"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M4.5 16.5c-1.5 1.3-2 5-2 5s3.7-.5 5-2M12 15l-3-3a22 22 0 0 1 3-6c3-4 10-4 10-4s0 7-4 10a22 22 0 0 1-6 3ZM9 12H4s.5-3 3-4h4M12 15v5s3-.5 4-3v-4"/><circle cx="16" cy="8" r="1"/></svg> Desplegar</button></div>
  <textarea id="ce" style="min-height:350px;font-family:var(--mono);font-size:11px;line-height:1.6" oninput="document.getElementById('db').style.display='block';document.getElementById('ub').style.display='inline-block'">${esc(curC)}</textarea>
  <button class="btn btn-o btn-xs" id="ub" style="display:none;margin-top:4px" onclick="uc()"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M3 10h11a7 7 0 0 1 0 14M3 10l6-6M3 10l6 6"/></svg> Restaurar</button>
  <div id="dep-r" style="margin-top:8px;display:none"></div>`
    }
    function uc() { document.getElementById('ce').value = curC; document.getElementById('db').style.display = 'none'; document.getElementById('ub').style.display = 'none' }
    async function dep(id) {
      const c = document.getElementById('ce').value, r = document.getElementById('dep-r');
      r.style.display = 'block'; r.innerHTML = '<div class="sp"></div> Analizando...';
      try {
        const re = await fetch(`${A}/config/${id}/deploy/dry-run`, { method: 'POST', headers: h(), body: JSON.stringify({ candidate_config: c }) });
        const d = await re.json(); r._c = c;
        r.innerHTML = `<div style="margin-bottom:6px;font-weight:500;font-size:12px"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M12 11h4"/><path d="M12 16h4"/><path d="M8 11h.01"/><path d="M8 16h.01"/></svg> Revisá el diff:</div><pre style="max-height:240px;font-size:10px;background:rgba(0,0,0,.3)">${(d.diff || 'Sin cambios').split('\n').map(l => {
          if (l.startsWith('@@')) return `<span class="diff-h">${esc(l)}</span>`; if (l.startsWith('+')) return `<span class="diff-a">${esc(l)}</span>`; if (l.startsWith('-')) return `<span class="diff-d">${esc(l)}</span>`; return esc(l)
        }).join('\n')}</pre>
      <div class="actions"><button class="btn btn-s" onclick="cDep('${id}')"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg> Confirmar</button>
      <button class="btn btn-o" onclick="r.style.display='none'"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> Cancelar</button></div>`
      } catch (e) { r.innerHTML = `<div style="color:var(--red)">${e.message}</div>` }
    }
    async function cDep(id) {
      const r = document.getElementById('dep-r'); r.innerHTML = '<div class="sp"></div> Aplicando...';
      try {
        const re = await fetch(`${A}/config/${id}/deploy`, { method: 'POST', headers: h(), body: JSON.stringify({ candidate_config: r._c }) });
        const d = await re.json();
        if (d.committed) { r.innerHTML = '<div style="color:var(--green);font-weight:500"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg> Cambios aplicados</div>'; curC = r._c; toast('<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg> Desplegado', 's') }
        else r.innerHTML = `<div style="color:var(--red)"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> ${d.error || 'Falló'}</div>`
      } catch (e) { r.innerHTML = `<div style="color:var(--red)">${e.message}</div>` }
    }
    async function dlC(id) {
      try {
        // Si no se abrió la pestaña Config, traer la config ahora (antes descargaba vacío).
        if (!curC) {
          const r = await fetch(`${A}/config/${encodeURIComponent(id)}/raw`, { headers: h() });
          curC = r.ok ? await r.text() : '';
        }
        const content = curC && curC.trim() ? curC : `Sin configuración disponible para ${id}`;
        const a = document.createElement('a');
        a.href = URL.createObjectURL(new Blob([content], { type: 'text/plain' }));
        a.download = `${id}_config.txt`;
        a.click();
        URL.revokeObjectURL(a.href);
        toast(content.startsWith('Sin configuración') ? '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m12 3 10 18H2Z"/><path d="M12 9v4M12 17h.01"/></svg> Config no disponible' : '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M12 3v12m-5-5 5 5 5-5"/></svg> Descarga iniciada', content.startsWith('Sin') ? 'error' : 's');
      } catch (e) { toast('<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> Error al exportar: ' + e.message, 'error'); }
    }

    function renderCmd(id, el) {
      const uid = crypto.randomUUID?.() || Date.now();
      // Sugerencia de ping con la IP real del equipo (antes: target fijo 172.30.0.10).
      const _dev = (devices || []).find(x => x.id === id) || {};
      const _tgt = _dev.hostname || _dev.host || _dev.ip || '10.0.0.1';
      el.innerHTML = `<div class="fr2">
    <textarea id="c-${uid}" placeholder="show version&#10;show ip route&#10;ping ${_tgt}" rows="3" style="min-height:60px" onkeydown="if(event.key==='Enter'&&event.ctrlKey)rCmd('${id}','${uid}')"></textarea>
    <div style="display:flex;flex-direction:column;gap:3px">
      <button class="btn btn-p btn-sm" onclick="rCmd('${id}','${uid}')" style="padding:8px 14px" aria-label="Ejecutar comando"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><polygon points="6 4 20 12 6 20"/></svg></button>
      <button class="btn btn-o btn-xs" onclick="document.getElementById('t-${uid}').innerHTML='<span style=\\'color:var(--text-ter)\\'>Limpiado</span>'">⌫</button></div></div>
  <div class="terminal" id="t-${uid}"><span class="p">${id}$ </span><span style="color:var(--text-ter)">Listo</span></div>`
    }
    async function rCmd(id, uid) {
      const input = document.getElementById(`c-${uid}`), term = document.getElementById(`t-${uid}`);
      const cmds = input.value.split('\n').filter(c => c.trim()); if (!cmds.length) return;
      cmdH.push(input.value); if (cmdH.length > 50) cmdH.shift(); updStats();
      let out = ''; term.innerHTML = '<div class="sp"></div>';
      for (const c of cmds) {
        try {
          const r = await fetch(`${A}/command/${id}`, { method: 'POST', headers: h(), body: JSON.stringify({ commands: [c] }) });
          const d = await r.json(); const txt = d.results?.[0]?.output || '';
          if (!txt && /^ping/.test(c)) out += `<span class="p">${id}$ </span>${esc(c)}\n<span style="color:var(--cyan)"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg> Ping enviado</span>\n`;
          else if (!txt) out += `<span class="p">${id}$ </span>${esc(c)}\n<span style="color:var(--orange)"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m12 3 10 18H2Z"/><path d="M12 9v4M12 17h.01"/></svg> Sin salida</span>\n`;
          else out += `<span class="p">${id}$ </span>${esc(c)}\n<pre style="background:none;border:none;padding:4px 0;margin:0 0 6px 0;font-size:11px;max-height:200px">${esc(txt.substring(0, 2800))}</pre>`
        } catch (e) { out += `<span class="p">${id}$ </span>${esc(c)}\n<span class="e">${e.message}</span>\n` }
      }
      term.innerHTML = out + `<div style="margin-top:4px;font-size:9px;color:var(--text-ter);border-top:1px solid var(--border-s);padding-top:4px">${cmds.length} comando(s) · ${new Date().toLocaleTimeString()}</div>`;
      term.scrollTop = term.scrollHeight
    }

    let tplT = null;
    async function renderTpl(id, el) {
      const r = await fetch(`${A}/templates`, { headers: h() }); if (!r.ok) { el.innerHTML = '<div class="empty"><span class="icon"><svg class="ic" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false" style="opacity:0.4;color:var(--text-ter)"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg></span><br>Sin templates</div>'; return }
      const tpls = await r.json(), dev = devices.find(d => d.id === id);
      el.innerHTML = `<div class="fr"><div class="fg"><label>Template</label><select id="tpl-s" onchange="tplP('${id}')"><option value="">—</option>${tpls.map(t => `<option value="${t.name}">${t.name}</option>`).join('')}</select></div>
    <div class="fg"><label>Driver</label><input value="${dev?.driver || 'ios'}" readonly></div></div>
  <div id="tpl-p"></div><div id="tpl-r" style="margin-top:8px"></div><div class="actions" id="tpl-a"></div>`
    }
    async function tplP(id) {
      const t = document.getElementById('tpl-s').value; if (!t) return;
      const r = await fetch(`${A}/templates/${t}`, { headers: h() }); const tp = await r.json();
      document.getElementById('tpl-p').innerHTML = tp.parameters?.length ? `<div class="fr">${tp.parameters.map(p => `<div class="fg"><label>${p}</label><input class="tpl-p" data-p="${p}" placeholder="${p}"></div>`).join('')}</div>
  <button class="btn btn-o btn-xs" onclick="tplR('${id}')" style="margin-top:4px"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg> Vista</button>` : '';
      document.getElementById('tpl-a').innerHTML = `<button class="btn btn-p btn-sm" onclick="tplA('${id}')"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg> Dry-Run</button>
  <button class="btn btn-s btn-sm" id="tpl-c" style="display:none" onclick="tplC('${id}')"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg> Confirmar</button>`
    }
    async function tplR(id) {
      const p = {}; document.querySelectorAll('.tpl-p').forEach(el => p[el.dataset.p] = el.value);
      const r = await fetch(`${A}/templates/${document.getElementById('tpl-s').value}/render`, { method: 'POST', headers: h(), body: JSON.stringify({ driver: devices.find(d => d.id === id)?.driver || 'ios', params: p }) });
      const d = await r.json(); document.getElementById('tpl-r').innerHTML = `<pre style="max-height:100px;font-size:10px">${esc(d.rendered_config || '')}</pre>`
    }
    async function tplA(id) {
      const p = {}; document.querySelectorAll('.tpl-p').forEach(el => p[el.dataset.p] = el.value);
      const el = document.getElementById('tpl-r'); el.innerHTML = '<div class="sp"></div>';
      try {
        const r = await fetch(`${A}/templates/${document.getElementById('tpl-s').value}/apply`, { method: 'POST', headers: h(), body: JSON.stringify({ device_id: id, params: p }) });
        const d = await r.json();
        el.innerHTML = `<div style="margin-bottom:4px;font-size:11px;font-weight:500"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M12 11h4"/><path d="M12 16h4"/><path d="M8 11h.01"/><path d="M8 16h.01"/></svg> Diff:</div>${(d.diff || '').split('\n').map(l => {
          if (l.startsWith('@@')) return `<span class="diff-h">${esc(l)}</span>`; if (l.startsWith('+')) return `<span class="diff-a">${esc(l)}</span>`; if (l.startsWith('-')) return `<span class="diff-d">${esc(l)}</span>`; return esc(l)
        }).join('\n')}`;
        if (d.session_token) { tplT = d.session_token; document.getElementById('tpl-c').style.display = 'inline-block'; toast('Revisá el diff', 'i') }
      } catch (e) { el.innerHTML = `<div style="color:var(--red)">${e.message}</div>` }
    }
    async function tplC(id) {
      if (!tplT) { toast('Primero dry-run', 'error'); return }
      const el = document.getElementById('tpl-r'); el.innerHTML = '<div class="sp"></div>';
      try {
        const r = await fetch(`${A}/templates/${document.getElementById('tpl-s').value}/apply/confirm`, { method: 'POST', headers: h(), body: JSON.stringify({ session_token: tplT }) });
        const d = await r.json(); el.innerHTML = d.success || d.committed ? '<div style="color:var(--green);font-weight:500"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg> Aplicado</div>' : `<div style="color:var(--red)"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> ${d.error || 'Falló'}</div>`;
        if (d.success || d.committed) toast('<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg> Template aplicado', 's'); tplT = null; document.getElementById('tpl-c').style.display = 'none'
      } catch (e) { el.innerHTML = `<div style="color:var(--red)">${e.message}</div>` }
    }