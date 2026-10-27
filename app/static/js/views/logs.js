/**
 * NetPulse NMS — vista Logs: auditoría + Loki (views/logs.js).
 *
 * Extraída del <script> inline de app/static/dashboard.html (Tarjeta 10D-B,
 * lote 2) sin cambio de comportamiento: switchLogSource, loadLokiLabels,
 * loadLokiLogs y loadLogs, mismas líneas en el mismo orden relativo.
 *
 * Ambas fuentes (audit y Loki) comparten el DOM de #v-logs (#log-body,
 * #log-stats, #log-count); el estado vive en el DOM, no hay variables
 * de módulo. `log-filter-action` es un control exclusivo de esta vista
 * (select de acción en el markup de #v-logs); ningún helper del bloque
 * es compartido con otra vista (verificado por grep).
 *
 * Depende (en tiempo de llamada, no de carga): core.js (A, h) e inline
 * de dashboard.html (esc). Handlers que las resuelven por window:
 * swView('logs') → loadLogs(); onchange de #log-source →
 * switchLogSource(); onchange de #log-filter-action/#log-limit y botones
 * de Loki → loadLogs() / loadLokiLogs(). Scripts clásicos sin defer/async
 * ya cargaron al momento del click (orden estático). Top-level sin
 * ejecución.
 */

    // ── LOGS VIEW (Real Audit Data) ──
    // ── LOG SOURCE SWITCH (Auditoría / Loki) ────────────────
    function switchLogSource() {
      const src = document.getElementById('log-source')?.value || 'audit';
      const lokiCtl = document.getElementById('loki-controls');
      const auditSel = document.getElementById('log-filter-action');
      const limitSel = document.getElementById('log-limit');
      if (src === 'loki') {
        if (lokiCtl) lokiCtl.style.display = 'block';
        if (auditSel) auditSel.style.display = 'none';
        if (limitSel) limitSel.style.display = 'none';
        loadLokiLabels();
        loadLokiLogs();
      } else {
        if (lokiCtl) lokiCtl.style.display = 'none';
        if (auditSel) auditSel.style.display = '';
        if (limitSel) limitSel.style.display = '';
        loadLogs();
      }
    }

    async function loadLokiLabels() {
      try {
        const r = await fetch(`${A}/logs/labels`, { headers: h() });
        if (!r.ok) return;
        const d = await r.json();
        const sel = document.getElementById('loki-container');
        if (!sel) return;
        const current = sel.value;
        sel.innerHTML = '<option value="">Todos los contenedores</option>';
        const labels = d.data || [];
        if (labels.includes('container')) {
          const qr = await fetch(`${A}/logs/query?query=%7Bcontainer%3D%22.%2B%22%7D&hours=24&limit=1`, { headers: h() });
          if (qr.ok) {
            const qd = await qr.json();
            const streams = qd?.data?.result || [];
            const names = [...new Set(streams.map(s => s.stream?.container).filter(Boolean))].sort();
            names.forEach(n => {
              const opt = document.createElement('option');
              opt.value = n; opt.textContent = n;
              sel.appendChild(opt);
            });
          }
        }
        if (current) sel.value = current;
      } catch (e) { }
    }

    async function loadLokiLogs() {
      const body = document.getElementById('log-body');
      const stats = document.getElementById('log-stats');
      const countEl = document.getElementById('log-count');
      if (!body) return;
      body.innerHTML = '<div class="sp" style="margin:20px auto"></div>';
      if (stats) stats.innerHTML = '';

      const container = document.getElementById('loki-container')?.value || '';
      const hours = document.getElementById('loki-hours')?.value || '6';
      const search = document.getElementById('loki-search')?.value.trim() || '';

      // Query selector: {container="X"} o todos
      let q = container ? `{container="${container}"}` : '{container=~".+"}';
      if (search) q += ` |= "${search.replace(/"/g, '\\"')}"`;

      try {
        const url = `${A}/logs/query?query=${encodeURIComponent(q)}&hours=${hours}&limit=200`;
        const r = await fetch(url, { headers: h() });
        if (!r.ok) { body.innerHTML = `<div style="color:var(--red);padding:16px">Error consultando Loki: HTTP ${r.status}</div>`; return; }
        const d = await r.json();
        const streams = d?.data?.result || [];
        let lines = [];
        streams.forEach(s => {
          (s.values || []).forEach(([ts, msg]) => {
            lines.push({ ts: parseInt(ts) / 1e9, container: s.stream?.container || '?', msg });
          });
        });
        lines.sort((a, b) => b.ts - a.ts);
        const total = lines.length;
        if (countEl) countEl.textContent = `${total} líneas`;
        if (!lines.length) {
          body.innerHTML = '<div style="color:var(--text-ter);padding:16px">Sin logs en el rango seleccionado</div>';
          return;
        }
        body.innerHTML = lines.slice(0, 200).map(l => {
          const t = new Date(l.ts * 1000).toLocaleTimeString('es', { hour12: false });
          const color = l.msg.toLowerCase().includes('error') ? 'var(--red)' :
            l.msg.toLowerCase().includes('warn') ? 'var(--yellow)' : 'var(--green)';
          return `<div style="display:flex;gap:8px;padding:2px 0;border-bottom:1px solid var(--border-s)">
            <span style="color:var(--text-ter);flex-shrink:0">${t}</span>
            <span style="color:var(--accent-h);flex-shrink:0">[${esc(l.container)}]</span>
            <span style="color:${color};white-space:pre-wrap;word-break:break-all">${esc(l.msg)}</span>
          </div>`;
        }).join('');
        body.scrollTop = 0;
      } catch (e) {
        body.innerHTML = `<div style="color:var(--red);padding:16px">Error: ${esc(e.message)}</div>`;
      }
    }

    async function loadLogs() {
      const body = document.getElementById('log-body');
      const stats = document.getElementById('log-stats');
      const countEl = document.getElementById('log-count');
      if (!body) return;
      body.innerHTML = '<div class="sp" style="margin:20px auto"></div>';

      const action = document.getElementById('log-filter-action')?.value || '';
      const limit = document.getElementById('log-limit')?.value || '100';

      try {
        // Fetch stats
        const sr = await fetch(`${A}/audit/stats`, { headers: h() });
        if (sr.ok) {
          const sd = await sr.json();
          stats.innerHTML = [
            { label: 'Total Events', val: sd.total_events?.toLocaleString('es') || '0', color: 'var(--accent)' },
            { label: 'Log Size', val: sd.file_size_bytes ? (sd.file_size_bytes / 1024).toFixed(1) + ' KB' : '0 KB', color: 'var(--green)' },
          ].map(k => `<div style="background:var(--panel);border:1px solid var(--border);padding:10px 16px;border-radius:var(--radius-sm);border-left:3px solid ${k.color}">
            <div style="font-size:9px;color:var(--text-muted);text-transform:uppercase">${k.label}</div>
            <div style="font-weight:700;font-size:14px;color:var(--text)">${k.val}</div>
          </div>`).join('');
        }

        // Fetch events
        let url = `${A}/audit?limit=${limit}`;
        if (action) url += `&action=${action}`;
        const r = await fetch(url, { headers: h() });
        if (!r.ok) { body.innerHTML = '<span style="color:var(--red)">Error al cargar logs</span>'; return; }
        const d = await r.json();
        const events = d.events || [];
        if (countEl) countEl.textContent = events.length + ' eventos';

        if (!events.length) {
          body.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-ter)">No hay eventos registrados con estos filtros</div>';
          return;
        }

        const actionColors = {
          facts_get: '#06b6d4', device_create: '#10b981', config_backup: '#f59e0b',
          command_exec: '#a855f7', login: '#3b82f6', api_request: '#7a7e85',
          default: '#5e6268'
        };

        body.innerHTML = events.map(e => {
          const ts = e.timestamp ? new Date(e.timestamp).toLocaleString('es', { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '?';
          const c = actionColors[e.action] || actionColors.default;
          const ok = e.success !== false;
          return `<div style="display:flex;gap:8px;padding:3px 0;border-bottom:1px solid rgba(255,255,255,0.03)">
            <span style="color:var(--text-ter);min-width:130px;font-size:10px">${ts}</span>
            <span style="color:${c};min-width:120px;font-weight:500">${e.action || '?'}</span>
            <span style="color:${ok ? 'var(--green)' : 'var(--red)'};min-width:16px">${ok ? '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m20 6-11 11-5-5"/></svg>' : '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>'}</span>
            <span style="color:var(--text-secondary);min-width:80px">${e.username || '—'}</span>
            <span style="color:var(--text-ter)">${e.device_id || ''} ${e.method || ''} ${e.path || ''} ${e.details || ''}</span>
          </div>`;
        }).join('');

        body.scrollTop = 0;
      } catch (e) {
        body.innerHTML = `<span style="color:var(--red)"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> ${e.message}</span>`;
      }
    }
