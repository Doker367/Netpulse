/**
 * NetPulse NOC — vista Monitoring: stack Prometheus/nativo, drag&drop de
 * charts y métricas de plataforma (views/monitoring.js).
 *
 * Extraída del <script> inline de app/static/dashboard.html (Tarjeta 10D-E,
 * lote E) sin cambio de comportamiento, en DOS tramos que conservan el orden
 * relativo original (3270–3582 << 5022–5100 del archivo pre-lote):
 *
 * 1. Stack (comentario `── MONITOREO (Prometheus nativo) ──`): queryProm,
 *    queryPromRange, _avgByTs, _countByTs, _nativeChart, renderNativeMon,
 *    refMon (con sus 3 helper anidados drawLine/drawMulti/drawBars),
 *    comentario `── DRAG AND DROP for monitoring charts ──` + `let dragSrc`,
 *    initDragDrop, saveChartOrder, loadChartOrder, y la REASIGNACIÓN de
 *    refMon (`const _origRefMon = refMon; refMon = async function...
 *    initDragDrop()`), que viaja JUNTO con la declaración en el mismo
 *    archivo y en el mismo orden (mapa §7.2): el wrapper se evalúa en el
 *    top-level de este script, donde `refMon` ya está izada, así que
 *    `_origRefMon` captura la versión real — nunca una ya envuelta.
 * 2. Plataforma (comentario `── MONITOREO (secciones stack + plataforma) ──`):
 *    monNum, renderMonSpark, loadMonitoring.
 *
 * Lo que NO viajó (verificado por grep, regla 2):
 * - `setRefreshInterval` — vive en el bloque Theme del inline; crea
 *   `window._rInt` y llama `refMon()`/`loadMonitoring()` en tiempo de llamada.
 *   Un solo consumidor en markup pero opera Monitoring: la tarjeta lo deja
 *   donde estaba ( decisión de Lotes A-D: no mover funciones de otra zona).
 * - `loadDonuts` (latido 120 s) — es del DASHBOARD: la llaman init y loadDev;
 *   NINGUNA función de monitoring la usa. Queda en el inline.
 * - `refreshAlertSummary` — dashboard (badge sidebar); `loadMonitoring` la
 *   llama en tiempo de llamada si hay alertas.
 * - La lógica del intervalo `window._mInt` (15 s) — la GESTIONA `swView`
 *   (inline residual): no se movió; este archivo solo trae los workers que
 *   el intervalo ejecuta (`loadMonitoring`, `refMon`).
 * - `devices`, `A`, `h`, `t`, `icon`, `esc`, `localStorage` 'np-chart-order' —
 *   core/estado/inline compartidos; resuelven en tiempo de llamada.
 *
 * Gotcha conservado (mapa §7.13): `initDragDrop` acumula listeners y
 * re-observa `window._chartRO` en cada refresco; se mueve tal cual, sin
 * "arreglarlo" (cambiaría el comportamiento).
 *
 * Depende (en tiempo de llamada): core.js (A, h), inline (devices,
 * refreshAlertSummary, setRefreshInterval, swView, loadDonuts NO). Handlers
 * por window: botón Refresh y select #mon-range de #v-monitoring
 * (onclick="refMon()"), botón Actualizar (onclick="loadMonitoring()"),
 * swView('monitoring') → loadMonitoring()+refMon() cada 15 s, y
 * setRefreshInterval (onchange de #refresh-interval / #mon-interval).
 * Script clásico sin defer/async; top-level sin ejecución de lógica salvo
 * el `let dragSrc = null` y la reasignación `refMon` (necesarias aquí:
 * son el punto 1).
 */

    // ── MONITOREO (Prometheus nativo) ──
    async function queryProm(expr) {
      try {
        const r = await fetch(`${A}/prometheus/v1/query?query=${encodeURIComponent(expr)}`, { headers: h(), signal: AbortSignal.timeout(6000) });
        if (!r.ok) return null;
        const d = await r.json(); return d.data?.result || null;
      } catch (e) { return null }
    }

    async function queryPromRange(expr, hours = 1) {
      try {
        const end = Math.floor(Date.now() / 1000), start = end - hours * 3600;
        const r = await fetch(`${A}/prometheus/v1/query_range?query=${encodeURIComponent(expr)}&start=${start}&end=${end}&step=60s`, { headers: h(), signal: AbortSignal.timeout(8000) });
        if (!r.ok) return null;
        const d = await r.json(); return d.data?.result || null;
      } catch (e) { return null }
    }
    function _avgByTs(devMap, key) {
      const byTs = {};
      Object.values(devMap || {}).forEach(series => (series || []).forEach(pt => {
        if (pt[key] === undefined || pt[key] === null) return;
        (byTs[pt.ts] = byTs[pt.ts] || []).push(Number(pt[key]) || 0);
      }));
      return Object.keys(byTs).map(t => [Number(t), byTs[t].reduce((a, b) => a + b, 0) / byTs[t].length]).sort((a, b) => a[0] - b[0]);
    }
    function _countByTs(devMap) {
      const byTs = {};
      Object.values(devMap || {}).forEach(series => (series || []).forEach(pt => {
        byTs[pt.ts] = (byTs[pt.ts] || 0) + (pt.online ? 1 : 0);
      }));
      return Object.keys(byTs).map(t => [Number(t), byTs[t]]).sort((a, b) => a[0] - b[0]);
    }
    function _nativeChart(id, series, colors, unit) {
      const el = document.getElementById(id)?.querySelector('.chart-area'); if (!el) return;
      const valid = series.filter(s => s.points && s.points.length >= 1);
      if (!valid.length) { el.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted);font-size:10px">Recolectando… (primera muestra)</div>'; return; }
      const w = el.clientWidth || 320, h = 130, p = 8;
      const all = valid.flatMap(s => s.points);
      const xm = Math.min(...all.map(pt => pt[0])), xx = Math.max(...all.map(pt => pt[0]));
      const ymax = Math.max(...all.map(pt => pt[1]), 1) * 1.15;
      const sx = t => (t - xm) / (xx - xm || 1) * (w - 2 * p) + p;
      const sy = y => h - p - (y / ymax) * (h - 2 * p);
      let svg = `<svg viewBox="0 0 ${w} ${h}" style="width:100%;height:100%" fill="none" stroke="currentColor">`;
      valid.forEach((s, i) => {
        const clr = colors[i % colors.length];
        if (s.points.length === 1) {
          const [t, y] = s.points[0];
          svg += `<circle cx="${sx(t)}" cy="${sy(y)}" r="3" fill="${clr}"/>`;
        } else {
          const pts = s.points.map(([t, y]) => `${sx(t)},${sy(y)}`).join(' ');
          svg += `<polyline points="${pts}" fill="none" stroke="${clr}" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>`;
        }
        const last = s.points[s.points.length - 1];
        svg += `<text x="${w - 2}" y="${12 + i * 11}" fill="${clr}" font-size="9" text-anchor="end" font-weight="600">${s.label}: ${last[1].toFixed(1)}${unit}</text>`;
      });
      svg += '</svg>';
      el.innerHTML = svg;
    }
    async function renderNativeMon(g) {
      const charts = document.getElementById('mon-charts');
      const hrs = parseInt(document.getElementById('mon-range')?.value || 6);
      let ov = null, hist = null, liveHost = null;
      await Promise.all([
        fetch(`${A}/system/overview`, { headers: h(), signal: AbortSignal.timeout(8000) })
          .then(r => r.ok ? r.json() : null).then(d => { ov = d; }).catch(() => { }),
        fetch(`${A}/system/history?hours=${hrs}`, { headers: h(), signal: AbortSignal.timeout(8000) })
          .then(r => r.ok ? r.json() : null).then(d => { hist = d; }).catch(() => { }),
        fetch(`${A}/system/host`, { headers: h(), signal: AbortSignal.timeout(8000) })
          .then(r => r.ok ? r.json() : null).then(d => { liveHost = d; }).catch(() => { }),
      ]);
      const host = (liveHost && liveHost.cpu) ? liveHost : (ov?.host || {});
      const cpu = host.cpu || {}, mem = host.memory || {};
      let ds = ov?.devices_summary || {};
      // Si el poller aún no tiene snapshot, derivar el resumen del historial.
      if (!ds.total && hist?.devices) {
        const latest = Object.values(hist.devices).map(s => s[s.length - 1]).filter(Boolean);
        const avg = k => latest.length ? Math.round(latest.reduce((a, x) => a + (Number(x[k]) || 0), 0) / latest.length * 100) / 100 : 0;
        ds = { total: latest.length, online: latest.filter(x => x.online).length, avg_cpu: avg('cpu'), avg_latency_ms: avg('latency_ms') };
      }
      const cards = [
        { l: 'Servidor CPU', v: (cpu.percent ?? 0) + '%', c: '#5b6bf7' },
        { l: 'Servidor RAM', v: (mem.percent ?? 0) + '%', c: '#23a55a' },
        { l: 'Load (1m)', v: (cpu.load1 ?? 0), c: '#f0b232' },
        { l: 'Dispositivos UP', v: `${ds.online ?? 0}/${ds.total ?? 0}`, c: '#a855f7' },
        { l: 'CPU disp. prom', v: (ds.avg_cpu ?? 0) + '%', c: '#06b6d4' },
        { l: 'Latencia prom', v: (ds.avg_latency_ms ?? 0) + ' ms', c: '#ea580c' },
      ];
      g.innerHTML = cards.map(c => `<div class="mon-card" style="border-top:3px solid ${c.c}"><h4>${c.l}</h4><div class="mon-val">${c.v}</div></div>`).join('');
      if (!charts) return;

      const count = hist?.count || 0;
      charts.innerHTML = `
        <div class="mp" style="width:100%;padding:12px 16px;border-left:3px solid var(--accent);background:var(--panel);font-size:12px;color:var(--text-secondary);margin-bottom:4px">
          <svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M3 3v18h18M7 14v3M12 9v8M17 5v12"/></svg> <strong>Modo nativo NetPulse</strong> — historial local (${count} muestra${count === 1 ? '' : 's'} en ${hrs}h, una cada ${ov?.poller?.interval || 60}s). Prometheus no detectado.
        </div>
        <div class="mon-chart" id="nm-host"><h4><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg> Servidor CPU / RAM (%)</h4><div class="chart-area"></div></div>
        <div class="mon-chart" id="nm-disk"><h4><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="22" y1="12" x2="2" y2="12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><line x1="6" y1="16" x2="6.01" y2="16"/><line x1="10" y1="16" x2="10.01" y2="16"/></svg> Servidor Disco / Load</h4><div class="chart-area"></div></div>
        <div class="mon-chart" id="nm-online"><h4><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="9"/></svg> Dispositivos Online</h4><div class="chart-area"></div></div>
        <div class="mon-chart" id="nm-devcpu"><h4><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20 7h-9"/><path d="M14 17H5"/><circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/></svg> CPU promedio dispositivos (%)</h4><div class="chart-area"></div></div>
        <div class="mon-chart" id="nm-lat"><h4><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 12m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0"/><path d="M16.24 7.76a6 6 0 0 1 0 8.49"/><path d="M7.76 16.24a6 6 0 0 1 0-8.49"/></svg> Latencia promedio (ms)</h4><div class="chart-area"></div></div>
        <div class="mon-chart" id="nm-devmem"><h4><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="3" y="6" width="18" height="12" rx="2"/><path d="M7 10h3v4H7zM14 10h3v4h-3zM7 18v3M12 18v3M17 18v3"/></svg> RAM promedio dispositivos (%)</h4><div class="chart-area"></div></div>`;

      const hostSeries = hist?.host || [];
      _nativeChart('nm-host', [
        { label: 'CPU', points: hostSeries.map(x => [x.ts, x.cpu]) },
        { label: 'RAM', points: hostSeries.map(x => [x.ts, x.memory_percent]) },
      ], ['#5b6bf7', '#23a55a'], '%');
      _nativeChart('nm-disk', [
        { label: 'Disco', points: hostSeries.map(x => [x.ts, x.disk_percent]) },
        { label: 'Load', points: hostSeries.map(x => [x.ts, x.load1]) },
      ], ['#f0b232', '#a855f7'], '');
      const devs = hist?.devices || {};
      _nativeChart('nm-online', [{ label: 'Online', points: _countByTs(devs) }], ['#23a55a'], '');
      _nativeChart('nm-devcpu', [{ label: 'CPU', points: _avgByTs(devs, 'cpu') }], ['#06b6d4'], '%');
      _nativeChart('nm-lat', [{ label: 'Lat', points: _avgByTs(devs, 'latency_ms') }], ['#ea580c'], '');
      _nativeChart('nm-devmem', [{ label: 'RAM', points: _avgByTs(devs, 'memory_percent') }], ['#23a55a'], '%');
    }
    async function refMon() {
      const g = document.getElementById('mon-grid'); if (!g) return;

      // Si Prometheus no responde, usar métricas nativas (host + poller)
      const probe = await queryProm('up');
      if (probe === null) { renderNativeMon(g); return; }

      const labels = ['Requests/s', 'Dispositivos UP', 'Auditoría', 'Latencia p95', 'Ops NAPALM', 'Logins'];
      const queries = ['sum(rate(netpulse_requests_total[5m]))', 'count(netpulse_devices_up==1)', 'sum(netpulse_audit_events_total)',
        'histogram_quantile(0.95,sum(rate(netpulse_request_duration_seconds_bucket[5m]))by(le))', 'sum(netpulse_napalm_operations_total)', 'sum(netpulse_auth_total)'];
      const colors = ['#5b6bf7', '#23a55a', '#f0b232', '#a855f7', '#06b6d4', '#ea580c'];
      const results = await Promise.all(queries.map(q => queryProm(q)));
      g.innerHTML = results.map((res, i) => {
        let v = res?.[0]?.value?.[1];
        if (v == null || v === '') return `<div class="mon-card" style="border-top:3px solid ${colors[i]}"><h4>${labels[i]}</h4><div class="mon-val">—</div></div>`;
        v = parseFloat(v);
        if (isNaN(v)) return `<div class="mon-card" style="border-top:3px solid ${colors[i]}"><h4>${labels[i]}</h4><div class="mon-val">—</div></div>`;
        if (i === 0) v = v.toFixed(2);
        else if (i === 1 || i === 2 || i === 4 || i === 5) v = Math.round(v).toLocaleString('es');
        else if (i === 3) v = v < 1 ? (v * 1000).toFixed(1) + ' ms' : v.toFixed(2) + ' s';
        return `<div class="mon-card" style="border-top:3px solid ${colors[i]}"><h4>${labels[i]}</h4><div class="mon-val">${v}</div></div>`;
      }).join('');

      const hrs = parseInt(document.getElementById('mon-range')?.value || 6);
      const R = hrs * 3600;
      // Update time range label
      const rl = document.getElementById('mon-range-label');
      if (rl) rl.textContent = `Últimas ${hrs}h — datos desde ${new Date(Date.now() - hrs * 3600 * 1000).toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit' })} hasta ${new Date().toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit' })}`;

      // Build chart containers dynamically if empty
      const mc = document.getElementById('mon-charts');
      if (mc && !mc.querySelector('.mon-chart[id^="mc-"]')) {
        const chartDefs = [
          { id: 'mc-requests', title: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m22 7-8.5 8.5-5-5L2 17M16 7h6v6"/></svg> Request Rate' },
          { id: 'mc-latency', title: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="13" r="8"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="9" y1="2" x2="15" y2="2"/></svg> API Latency <span class="mon-tags"><span class="mt p95 active">p95</span></span>' },
          { id: 'mc-devices', title: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="9"/></svg> Estado Dispositivos' },
          { id: 'mc-ops', title: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20 7h-9"/><path d="M14 17H5"/><circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/></svg> Operaciones NAPALM' },
          { id: 'mc-errors', title: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> Errores NAPALM' },
          { id: 'mc-paths', title: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="6" cy="18" r="3"/><circle cx="18" cy="6" r="3"/><path d="M6 15V6a3 3 0 0 1 6 0v12a3 3 0 0 0 6 0V9"/></svg> Requests por Endpoint' },
          { id: 'mc-auth', title: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg> Intentos de Auth' },
          { id: 'mc-napalm-lat', title: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="13" r="8"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="9" y1="2" x2="15" y2="2"/></svg> Latencia NAPALM <span class="mon-tags"><span class="mt p95 active">p95</span></span>' },
          { id: 'mc-uptime', title: '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 12m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0"/><path d="M16.24 7.76a6 6 0 0 1 0 8.49"/><path d="M7.76 16.24a6 6 0 0 1 0-8.49"/></svg> Uptime Dispositivo' },
        ];
        mc.innerHTML = chartDefs.map(c => `<div class="mon-chart" id="${c.id}"><h4>${c.title}</h4><div class="chart-area"></div></div>`).join('');
      }

      // ── Draw single line chart ──
      async function drawLine(id, q, clr, unit, labelF) {
        const el = document.getElementById(id)?.querySelector('.chart-area'); if (!el) return;
        const data = await queryPromRange(q, hrs);
        if (!data || !data[0]?.values) { el.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted);font-size:10px">Sin datos</div>'; return }
        const v = data[0].values.map(([t, val]) => [t, parseFloat(val)]).filter(([t, val]) => !isNaN(val));
        if (v.length < 2) { el.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted);font-size:10px">Pocos datos</div>'; return }
        const w = el.clientWidth || 320, h = 130, p = 8, ys = v.map(x => x[1]);
        const xm = v[0][0], xx = v[v.length - 1][0], ym = 0, ya = Math.max(...ys) * 1.2 || 1;
        const sx = t => (t - xm) / (xx - xm || 1) * (w - 2 * p) + p, sy = y => h - p - (y - ym) / (ya - ym) * (h - 2 * p);
        const pts = v.map(([t, y]) => `${sx(t)},${sy(y)}`).join(' ');
        el.innerHTML = `<svg viewBox="0 0 ${w} ${h}" style="width:100%;height:100%" fill="none" stroke="currentColor"><defs><linearGradient id="g-${id}"><stop offset="0%" stop-color="${clr}" stop-opacity=".15"/><stop offset="100%" stop-color="${clr}" stop-opacity="0"/></linearGradient></defs><polygon points="${pts} ${sx(xx)},${h - p} ${sx(xm)},${h - p}" fill="url(#g-${id})"/><polyline points="${pts}" fill="none" stroke="${clr}" stroke-width="1.5" stroke-linecap="round"/><text x="${w - 2}" y="14" fill="${clr}" font-size="10" text-anchor="end" font-weight="600">${(labelF || (x => x.toFixed(1) + unit))(ys[ys.length - 1])}</text></svg>`
      }

      // ── Draw multi-line chart ──
      async function drawMulti(id, qs, clrs, labels, unit, sameScale) {
        const el = document.getElementById(id)?.querySelector('.chart-area'); if (!el) return;
        const datasets = await Promise.all(qs.map(q => queryPromRange(q, hrs)));
        const valid = datasets.map((d, i) => d?.[0]?.values?.map(([t, v]) => [t, parseFloat(v)]).filter(([t, v]) => !isNaN(v)) || []).filter(v => v.length > 1);
        if (!valid.length) { el.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted);font-size:10px">Sin datos</div>'; return }
        const w = el.clientWidth || 320, h = 130, p = 8;
        const allVals = valid.flat();
        const xm = allVals[0][0], xx = allVals[allVals.length - 1][0];
        const ya = sameScale ? Math.max(...allVals.map(x => x[1])) * 1.2 || 1 : 0;
        const sy = sameScale ? y => h - p - (y - 0) / (ya - 0) * (h - 2 * p) : null;
        let html = `<svg viewBox="0 0 ${w} ${h}" style="width:100%;height:100%" fill="none" stroke="currentColor"><g>`;
        const txs = Math.min(valid.length, 5);
        for (let i = 0; i < txs; i++) {
          const v = valid[i], lbl = labels[i] || '?', clr = clrs[i % clrs.length];
          const mi = Math.min(...v.map(x => x[1])), ma = Math.max(...v.map(x => x[1]));
          const ymin = sameScale ? 0 : mi, ymax = sameScale ? ya : ma * 1.2 || 1;
          const lsy = sameScale ? sy : y => h - p - (y - ymin) / (ymax - ymin) * (h - 2 * p);
          const sx = t => (t - xm) / (xx - xm || 1) * (w - 2 * p) + p;
          const pts = v.map(([t, y]) => `${sx(t)},${lsy(y)}`).join(' ');
          html += `<polyline points="${pts}" fill="none" stroke="${clr}" stroke-width="1.5" opacity=".8" stroke-linecap="round"/>`;
          // Last value label
          const lv = v[v.length - 1];
          html += `<text x="${sx(lv[0]) + 2}" y="${lsy(lv[1]) + 3}" fill="${clr}" font-size="8" font-weight="500">${lbl}</text>`;
        }
        html += `</g></svg>`;
        el.innerHTML = html;
      }

      // ── Draw bar chart ──
      async function drawBars(id, q) {
        const el = document.getElementById(id)?.querySelector('.chart-area'); if (!el) return;
        const data = await queryProm(q);
        if (!data || !data.length) { el.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted);font-size:10px">Sin datos</div>'; return }
        const items = data.map(r => ({ name: r.metric?.device_id || r.metric?.instance || '?', val: parseFloat(r.value[1]) })).filter(r => r.val > 0).sort((a, b) => b.val - a.val).slice(0, 5);
        if (!items.length) { el.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted);font-size:10px">Sin datos</div>'; return }
        const mx = Math.max(...items.map(r => r.val)) || 1;
        const w = el.clientWidth || 320, h = Math.max(items.length * 24 + 20, 60);
        const bars = items.map((r, i) => `<div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;font-size:10px"><span style="width:80px;text-align:right;color:var(--text-secondary);font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${r.name}</span><div style="height:16px;background:var(--accent-l);border-radius:3px;flex:1;min-width:4px"><div style="height:100%;width:${r.val / mx * 100}%;background:var(--accent);border-radius:3px;transition:width.3s"></div></div><span style="color:var(--text-ter);width:40px;text-align:right">${r.val.toFixed(1)}</span></div>`).join('');
        el.innerHTML = `<div style="padding:4px 0">${bars}</div>`;
      }

      // Draw all charts
      drawLine('mc-requests', `sum(rate(netpulse_requests_total[${R}s]))`, '#5b6bf7', ' r/s');
      drawLine('mc-latency', `histogram_quantile(0.95,sum(rate(netpulse_request_duration_seconds_bucket[${R}s]))by(le))`, '#a855f7', ' s');

      // Dynamic device chart — only shows registered devices
      if (devices.length) {
        const colors = ['#5b6bf7', '#a855f7', '#06b6d4', '#23a55a', '#f59e0b', '#ef4444', '#ec4899'];
        const qArr = devices.map(d => `netpulse_devices_up{device_id="${d.id}"}`);
        const names = devices.map(d => d.id);
        const cols = devices.map((_, i) => colors[i % colors.length]);
        drawMulti('mc-devices', qArr, cols, names, '', true);
      }
      drawBars('mc-ops', `sum by(device_id)(netpulse_napalm_operations_total)`);

      // Additional: NAPALM error rate by type
      drawLine('mc-errors', `sum(rate(netpulse_napalm_operations_total{status!="success"}[${R}s]))by(device_id,status)`, '#ef4444', ' ops/s');

      // Additional: request rate per path
      drawLine('mc-paths', `sum(rate(netpulse_requests_total[${R}s]))by(path)`, '#22d3ee', ' r/s');

      // Auth attempts
      drawLine('mc-auth', `sum(rate(netpulse_auth_total[${R}s]))by(status)`, '#f59e0b', ' /s');

      // NAPALM latency p95 by device
      drawLine('mc-napalm-lat', `histogram_quantile(0.95,sum(rate(netpulse_napalm_duration_seconds_bucket[${R}s]))by(device_id,le))`, '#a78bfa', ' s');

      // Device uptime (if available as metric)
      drawLine('mc-uptime', `netpulse_devices_up`, '#23a55a', ' ', false, true);
    }

    // ── DRAG AND DROP for monitoring charts ──
    let dragSrc = null;
    function initDragDrop() {
      document.querySelectorAll('.mon-chart').forEach(el => {
        el.setAttribute('draggable', 'true');
        // Add handle
        if (!el.querySelector('.drag-handle')) {
          const h = document.createElement('div'); h.className = 'drag-handle'; h.innerHTML = icon('grip');
          h.title = 'Arrastrar para reordenar';
          el.appendChild(h);
        }
        if (!window._chartRO) window._chartRO = new ResizeObserver(() => {
          if (document.getElementById('dp').classList.contains('active')) return;
          saveChartOrder();
        });
        window._chartRO.observe(el);

        el.addEventListener('dragstart', e => {
          dragSrc = el; el.style.opacity = '.4';
          e.dataTransfer.effectAllowed = 'move';
        });
        el.addEventListener('dragover', e => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; el.classList.add('drag-over') });
        el.addEventListener('dragleave', () => el.classList.remove('drag-over'));
        el.addEventListener('drop', e => {
          e.preventDefault(); el.classList.remove('drag-over');
          if (dragSrc && dragSrc !== el) {
            const parent = el.parentNode;
            const srcNext = dragSrc.nextSibling;
            parent.insertBefore(dragSrc, el);
            if (srcNext) parent.insertBefore(el, srcNext); else parent.appendChild(el);
            saveChartOrder();
          }
          if (dragSrc) dragSrc.style.opacity = '1'; dragSrc = null;
        });
        el.addEventListener('dragend', () => { if (dragSrc) dragSrc.style.opacity = '1'; dragSrc = null; document.querySelectorAll('.mon-chart').forEach(c => c.classList.remove('drag-over')) });
      });
      // Restore saved order
      loadChartOrder();
    }
    function saveChartOrder() {
      const items = [...document.querySelectorAll('.mon-chart')].map(e => ({ id: e.id, w: e.style.width, h: e.style.height })).filter(x => x.id);
      try { localStorage.setItem('np-chart-order', JSON.stringify(items)) } catch (e) { }
    }
    function loadChartOrder() {
      try {
        const saved = JSON.parse(localStorage.getItem('np-chart-order') || '[]');
        if (!saved.length) return;

        const grid = document.querySelector('#v-monitoring .mon-chart')?.parentNode;
        if (!grid) return;
        saved.forEach(item => {
          const id = item.id || item;
          const el = document.getElementById(id);
          if (el) {
            if (item.w) el.style.width = item.w;
            if (item.h) el.style.height = item.h;
            grid.appendChild(el);
          }
        });
      } catch (e) { }
    }
    // Run drag-drop init after refMon renders charts
    const _origRefMon = refMon;
    refMon = async function () { await _origRefMon(); initDragDrop() };

    // ── MONITOREO (secciones stack + plataforma) ─────────────
    function monNum(v, suffix) {
      if (v === null || v === undefined || isNaN(v)) return '—';
      const n = Math.round(Number(v) * 10) / 10;
      return n + (suffix || '');
    }

    function renderMonSpark(id, data) {
      const el = document.getElementById(id);
      if (!el) return;
      const pts = (data && data.series && data.series[0] && data.series[0].points) || [];
      if (!pts.length) { el.innerHTML = '<div style="color:var(--text-ter);font-size:12px">Sin datos</div>'; return; }
      const max = Math.max(1, ...pts.map(p => p[1]));
      el.innerHTML = pts.map(p => {
        const h = Math.max(3, Math.round(p[1] / max * 60));
        const col = p[1] > 85 ? 'var(--red)' : (p[1] > 70 ? '#f59e0b' : 'var(--accent)');
        return `<div title="${new Date(p[0] * 1000).toLocaleTimeString()} — ${Math.round(p[1])}%"
          style="flex:1;min-width:2px;height:${h}px;background:${col};border-radius:2px 2px 0 0;opacity:.85"></div>`;
      }).join('');
    }

    async function loadMonitoring() {
      try {
        const ov = await fetch(`${A}/monitoring/overview`, { headers: h() }).then(r => r.json()).catch(() => ({}));
        const svc = ov.services || {};
        document.getElementById('mon-services').innerHTML = Object.entries(svc).map(([name, s]) =>
          `<div class="sc" style="display:flex;align-items:center;gap:8px;padding:8px 14px">
            <span style="width:9px;height:9px;border-radius:50%;background:${s.up ? 'var(--green)' : 'var(--red)'}"></span>
            <b style="font-size:12px;text-transform:capitalize">${esc(name)}</b>
            <span style="font-size:11px;color:var(--text-ter)">:${(s.url || '').split(':').pop()}</span>
          </div>`).join('');

        const m = ov.metrics || {};
        document.getElementById('mon-cards').innerHTML = [
          ['Host CPU', monNum(m.host_cpu, '%'), 'cpu'],
          ['Host Memoria', monNum(m.host_mem, '%'), 'memory'],
          ['Host Disco', monNum(m.host_disk, '%'), 'server'],
          ['Dispositivos up', (m.devices_up ?? '—') + '/' + (m.devices_total ?? '—'), 'link'],
        ].map(([label, value, ic]) => `<div class="sc" style="display:flex;align-items:center;gap:12px;padding:14px 18px;flex:1;min-width:150px">
            <div style="color:var(--accent)">${icon(ic, 'ic-lg')}</div>
            <div><div style="font-size:11px;color:var(--text-muted);text-transform:uppercase">${label}</div>
            <div style="font-size:22px;font-weight:700">${value}</div></div></div>`).join('');

        // (Las alertas activas tienen su vista dedicada: se enlaza, no se duplica.)
        try {
          const al = await fetch(`${A}/alerts/active`, { headers: h() }).then(r => r.json()).catch(() => ({}));
          if (al.status || al.alerts) void refreshAlertSummary(); // mantiene el badge del nav y del header al día
        } catch (e) { /* silencioso */ }

        const targets = ov.targets || [];
        document.getElementById('mon-targets').innerHTML = targets.length
          ? `<table style="width:100%;border-collapse:collapse;font-size:12px"><thead><tr style="text-align:left;color:var(--text-ter)">
              <th style="padding:6px">job</th><th>URL</th><th>estado</th></tr></thead><tbody>
              ${targets.map(t => `<tr style="border-top:1px solid var(--border)">
                <td style="padding:6px;font-family:var(--mono)">${esc(t.job || '')}</td>
                <td style="font-family:var(--mono);color:var(--text-secondary)">${esc(t.url || '')}</td>
                <td><span class="badge" style="background:${t.health === 'up' ? 'var(--green-bg)' : 'rgba(239,68,68,.2)'}">${esc(t.health || '?')}</span></td>
              </tr>`).join('')}</tbody></table>`
          : '<div style="color:var(--text-ter);font-size:12px">Sin targets (¿Prometheus apagado?)</div>';

        const gUrl = (ov.links && ov.links.grafana_dashboard) || '';
        const link = document.getElementById('mon-grafana-link');
        if (link) link.href = gUrl || 'http://localhost:3000';
        const ifr = document.getElementById('mon-grafana');
        if (ifr && gUrl && !ifr.src) ifr.src = gUrl + '&refresh=10s';

        const [cpu, mem, disk] = await Promise.all([
          fetch(`${A}/monitoring/series?query=${encodeURIComponent('netpulse_host_cpu_percent')}&minutes=60`, { headers: h() }).then(r => r.json()).catch(() => ({})),
          fetch(`${A}/monitoring/series?query=${encodeURIComponent('netpulse_host_memory_percent')}&minutes=60`, { headers: h() }).then(r => r.json()).catch(() => ({})),
          fetch(`${A}/monitoring/series?query=${encodeURIComponent('max(netpulse_host_disk_percent)')}&minutes=60`, { headers: h() }).then(r => r.json()).catch(() => ({})),
        ]);
        renderMonSpark('mon-chart-cpu', cpu);
        renderMonSpark('mon-chart-mem', mem);
        renderMonSpark('mon-chart-disk', disk);
      } catch (e) {
        const el = document.getElementById('mon-targets');
        if (el) el.innerHTML = '<div class="empty">Error cargando monitoring: ' + esc(e.message) + '</div>';
      }
    }
