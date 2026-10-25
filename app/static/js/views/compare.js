/**
 * NetPulse NOC — vista Compare: comparación de configuraciones y de backups
 * (views/compare.js).
 *
 * Extraída del <script> inline de app/static/dashboard.html (Tarjeta 10D-G,
 * lote g), sin cambio de comportamiento, mismo orden relativo que el monolito
 * (dCmp 3965 < bdPopulateDevices 3999 < bdLoadBackups 4008 < bdDiff 4022).
 * Top-level sin ejecución de lógica: ninguna función lee #cmp-a/#cmp-b al
 * evaluarse — toda lectura de selects es en tiempo de llamada, como exige la
 * tarjeta (el pisado de la selección lo hace loadDev en CADA latido de 30 s
 * con selectedIndex=1, trampa §7.6 del mapa: vive en views/devices.js, tal
 * cual, no aquí).
 *
 * Contenido nombre a nombre:
 * - `dCmp` — diff de configs raw (GET /config/{id}/raw ×2), render de
 *   #cmp-r. Consumidores: onchange de #cmp-a/#cmp-b (markup) y qaM del modal
 *   #dp (inline residual, lote h).
 * - `bdPopulateDevices` — rellena #bd-dev; la llama swView('compare').
 * - `bdLoadBackups` — carga la lista de backups al doble select #bd-a/#bd-b.
 * - `bdDiff` — diff servido por GET /config/{id}/backups/diff -> #bd-stats /
 *   #bd-r.
 *
 * f bdLoadBackups mantiene el `const A = document.getElementById('bd-a')`
 * local que eclipsa el global `A` (mapa §7.3): tal cual, no se toca.
 *
 * Lo que NO viajó (regla 2): core.js (A, h, toast), `devices` (estado
 * compartido, inline), `esc` (inline), `qaM` (modal #dp, inline residual).
 */

    // ── COMPARE ──
    async function dCmp() {
      const a = document.getElementById('cmp-a').value, b = document.getElementById('cmp-b').value;
      if (!a || !b || a === b) { document.getElementById('cmp-r').innerHTML = '<pre style="color:var(--text-ter)">Seleccioná dos diferentes</pre>'; return }
      const el = document.getElementById('cmp-r'); el.innerHTML = '<div class="sp"></div>';
      try {
        const ra = await fetch(`${A}/config/${a}/raw`, { headers: h() }), ta = await ra.text();
        const rb = await fetch(`${A}/config/${b}/raw`, { headers: h() }), tb = await rb.text();
        const aNoCfg = ta.includes('\u26a0\ufe0f') || ta.includes('Config no disponible');
        const bNoCfg = tb.includes('\u26a0\ufe0f') || tb.includes('Config no disponible');
        if (aNoCfg || bNoCfg) {
          el.innerHTML = `<div class="empty"><span class="icon"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M3 8h18m-4-4 4 4-4 4M21 16H3m4-4-4 4 4 4"/></svg></span><br>${aNoCfg ? a : b} no tiene config disponible vía NAPALM.<br><small style="color:var(--text-ter)">${aNoCfg && bNoCfg ? 'Ambos dispositivos usan API propietaria.' : 'Usá Facts para comparar información.'}</small></div>`;
          return;
        }
        el.innerHTML = `<div class="cg2"><div class="col"><h3><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg> ${a}</h3><pre style="max-height:300px;font-size:9px">${esc(ta)}</pre></div><div class="col"><h3><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg> ${b}</h3><pre style="max-height:300px;font-size:9px">${esc(tb)}</pre></div></div>`;
        const la = ta.split('\n'), lb = tb.split('\n'); let diff = '';
        for (let i = 0; i < Math.max(la.length, lb.length); i++)if (la[i] !== lb[i]) {
          if (la[i] !== undefined) diff += `<span class="diff-d">-${esc(la[i])}</span>`;
          if (lb[i] !== undefined) diff += `<span class="diff-a">+${esc(lb[i])}</span>`
        }
        if (diff) el.innerHTML += `<div style="margin-top:8px"><h3 style="font-size:11px;color:var(--text-ter);text-transform:uppercase;margin-bottom:4px"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="9"/></svg><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="9"/></svg> Diferencias</h3><pre style="max-height:200px;font-size:9px">${diff}</pre></div>`
      } catch (e) { el.innerHTML = `<pre style="color:var(--red)">${e.message}</pre>` }
    }

    function bdPopulateDevices() {
      const sel = document.getElementById('bd-dev');
      if (!sel) return;
      const cur = sel.value;
      sel.innerHTML = (devices || []).map(d => `<option value="${esc(d.id)}">${esc(d.id)}</option>`).join('');
      if (cur) sel.value = cur;
      bdLoadBackups();
    }

    async function bdLoadBackups() {
      const dev = document.getElementById('bd-dev')?.value;
      if (!dev) return;
      try {
        const r = await fetch(`${A}/config/${encodeURIComponent(dev)}/backups`, { headers: h() });
        const list = r.ok ? await r.json() : [];
        const opts = list.map(b => `<option value="${esc(b.filename)}">${esc(b.filename)} · ${new Date(b.timestamp * 1000).toLocaleString('es')}</option>`).join('');
        const A = document.getElementById('bd-a'), B = document.getElementById('bd-b');
        if (A) A.innerHTML = opts || '<option value="">(sin backups)</option>';
        if (B) B.innerHTML = opts || '<option value="">(sin backups)</option>';
        if (list.length > 1) { A.selectedIndex = 1; B.selectedIndex = 0; }
      } catch (e) { }
    }

    async function bdDiff() {
      const dev = document.getElementById('bd-dev')?.value;
      const a = document.getElementById('bd-a')?.value, b = document.getElementById('bd-b')?.value;
      const box = document.getElementById('bd-r'), statsEl = document.getElementById('bd-stats');
      if (!dev || !a || !b) { box.innerHTML = '<pre style="color:var(--text-ter)">Seleccioná dispositivo y dos backups</pre>'; return }
      box.innerHTML = '<div class="sp"></div>'; statsEl.textContent = '';
      try {
        const r = await fetch(`${A}/config/${encodeURIComponent(dev)}/backups/diff?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`, { headers: h() });
        const d = await r.json();
        if (!r.ok) { box.innerHTML = `<pre style="color:var(--red)">${esc(d.detail || 'Error')}</pre>`; return }
        statsEl.innerHTML = `<span style="color:var(--green)">+${d.stats.added}</span> · <span style="color:var(--red)">−${d.stats.removed}</span> · ${d.stats.total} líneas`;
        const rows = d.rows.map(rw => `<div style="display:grid;grid-template-columns:1fr 1fr;border-bottom:1px solid var(--border)">
          <div style="padding:1px 6px;white-space:pre-wrap;word-break:break-all;${rw.type === 'del' || rw.type === 'change' ? 'background:rgba(239,68,68,.12);color:var(--red)' : 'color:var(--text-secondary)'}">${rw.a == null ? '' : esc(rw.a)}</div>
          <div style="padding:1px 6px;white-space:pre-wrap;word-break:break-all;border-left:1px solid var(--border);${rw.type === 'add' || rw.type === 'change' ? 'background:rgba(35,165,90,.12);color:var(--green)' : 'color:var(--text-secondary)'}">${rw.b == null ? '' : esc(rw.b)}</div>
        </div>`).join('');
        box.innerHTML = `<div style="font-family:var(--mono);font-size:10px;max-height:420px;overflow:auto;background:var(--panel);border:1px solid var(--border);border-radius:8px">${rows}</div>`;
      } catch (e) { box.innerHTML = `<pre style="color:var(--red)">${e.message}</pre>` }
    }
