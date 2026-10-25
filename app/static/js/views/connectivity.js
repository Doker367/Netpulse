/**
 * NetPulse NOC — vista Conectividad (views/connectivity.js).
 *
 * Extraída del <script> inline de app/static/dashboard.html (Tarjeta 10D-A,
 * lote 1) sin cambio de comportamiento: _pingCard + loadConn, mismas líneas
 * en el mismo orden relativo.
 *
 * Depende (en tiempo de llamada, no de carga): core.js (A, h, toast) e
 * inline de dashboard.html (esc). Handlers que la resuelven por window:
 * onclick="loadConn()" del markup de #v-connectivity y swView('connectivity')
 * → loadConn() (swView sigue en el inline; los scripts clásicos ya cargaron
 * al momento del click, orden estático entre core.js → vistas → inline).
 *
 * Clásico, SIN defer/async, SIN 'use strict'; su top-level no ejecuta nada
 * (solo declaraciones `function`), por eso puede cargar entre core.js y el
 * inline sin adelantar comportamiento.
 */

// ── CONECTIVIDAD (ping a targets externos) ──
    function _pingCard(d) {
      const ok = d && d.success;
      const rtt = d && d.rtt_avg ? Number(d.rtt_avg).toFixed(1) : '—';
      const loss = d ? Math.round(d.packet_loss || 0) : 100;
      const color = ok ? 'var(--green)' : 'var(--red)';
      return `<div style="display:flex;gap:16px;align-items:center;font-size:12px;padding:6px 4px;flex-wrap:wrap">
        <span style="color:${color};font-weight:700">${ok ? '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg> Online' : '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> Sin respuesta'}</span>
        <span><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="13" r="8"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="9" y1="2" x2="15" y2="2"/></svg> <strong>${rtt} ms</strong></span>
        <span><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m22 17-8.5-8.5-5 5L2 7M16 17h6v-6"/></svg> pérdida <strong>${loss}%</strong></span>
        <span style="color:var(--text-ter)">${d ? (d.probes_received || 0) : 0}/${d ? (d.probes_sent || 0) : 0} paquetes</span>
      </div>`;
    }
    async function loadConn() {
      const g = document.getElementById('conn-grid'); if (!g || !t) return;
      g.innerHTML = '<div class="sp"></div>';
      const targets = [
        { host: '8.8.8.8', label: 'Internet (Google DNS)' },
        { host: '1.1.1.1', label: 'Internet (Cloudflare)' },
      ];
      g.innerHTML = targets.map(tg =>
        `<div class="mp"><h4 class="mph"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 12m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0"/><path d="M16.24 7.76a6 6 0 0 1 0 8.49"/><path d="M7.76 16.24a6 6 0 0 1 0-8.49"/></svg> ${tg.label} <span style="font-weight:400;font-size:10px;color:var(--text-muted)">desde el servidor NetPulse</span></h4><div id="sping-${tg.host}" style="padding:8px"><div class="sp"></div></div></div>`
      ).join('') +
        `<div class="mp" style="grid-column:1/-1"><h4 class="mph"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg> Latencia a dispositivos <span style="font-weight:400;font-size:10px;color:var(--text-muted)">(medida por el poller)</span></h4><div id="conn-devices" style="padding:4px"><div class="sp"></div></div></div>`;

      // Pings desde el servidor (independiente de la CLI de los equipos)
      await Promise.all(targets.map(async tg => {
        const el = document.getElementById(`sping-${tg.host}`); if (!el) return;
        try {
          const r = await fetch(`${A}/system/ping?target=${encodeURIComponent(tg.host)}&count=3`, { method: 'POST', headers: h(), signal: AbortSignal.timeout(12000) });
          el.innerHTML = _pingCard(await r.json());
        } catch (e) { el.innerHTML = '<span style="color:var(--red)"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> Error de sondeo</span>'; }
      }));

      // Latencia/pérdida por dispositivo (snapshot del poller)
      try {
        const r = await fetch(`${A}/system/devices`, { headers: h() });
        const snap = await r.json();
        const devs = Object.values(snap.devices || {}).sort((a, b) => (a.device_id || '').localeCompare(b.device_id || ''));
        const dv = document.getElementById('conn-devices');
        if (!devs.length) { dv.innerHTML = '<div style="padding:16px;color:var(--text-ter);font-size:12px">Sin datos aún. El poller recolecta cada ' + (snap.interval || 60) + 's.</div>'; return; }
        dv.innerHTML = `<table style="width:100%;border-collapse:collapse;font-size:12px">
          <thead><tr style="border-bottom:1px solid var(--border);color:var(--text-muted);font-size:10px;text-transform:uppercase;letter-spacing:.5px">
            <th style="padding:10px;text-align:left">Dispositivo</th><th style="padding:10px;text-align:left">Estado</th>
            <th style="padding:10px;text-align:left">Latencia</th><th style="padding:10px;text-align:left">Pérdida</th>
            <th style="padding:10px;text-align:left">Último error</th></tr></thead>
          <tbody>${devs.map(m => {
            const online = m.online !== false;
            return `<tr style="border-bottom:1px solid var(--border-s)">
              <td style="padding:10px;font-weight:600;color:var(--text)">${esc(m.device_id)} <span class="badge" style="background:var(--surface);font-size:9px">${esc((m.driver || '').toUpperCase())}</span></td>
              <td style="padding:10px"><span class="badge ${online ? 'badge-g' : 'badge-r'}">${online ? 'ONLINE' : 'OFFLINE'}</span></td>
              <td style="padding:10px;color:var(--text-secondary)">${m.latency_ms ? Number(m.latency_ms).toFixed(2) + ' ms' : '—'}</td>
              <td style="padding:10px;color:${(m.packet_loss || 0) > 0 ? 'var(--orange)' : 'var(--text-secondary)'}">${Math.round(m.packet_loss || 0)}%</td>
              <td style="padding:10px;color:var(--text-ter);font-size:11px">${esc(m.error || '—')}</td>
            </tr>`;
          }).join('')}</tbody></table>`;
      } catch (e) {
        const dv = document.getElementById('conn-devices');
        if (dv) dv.innerHTML = '<div style="padding:16px;color:var(--red);font-size:12px"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> No se pudo obtener la latencia de dispositivos</div>';
      }
    }
