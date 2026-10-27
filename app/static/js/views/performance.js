/**
 * NetPulse NMS — vista Performance / Equipment (views/performance.js).
 *
 * Extraída del <script> inline de app/static/dashboard.html (Tarjeta 10D-C,
 * lote 3) sin cambio de comportamiento: solo `loadPerformance`, la misma
 * línea a línea en el mismo orden relativo.
 *
 * Lo que NO viajó (verificado por grep, regla 2):
 * - `_fmtBytes` — declarada DOS VECES en el inline (la de este bloque, hoy
 *   sin llamadas, y la que gana en el bloque Sites/Retention, usada por
 *   retención). Mover la que gana o "neta" la sombreada podría cambiar el
 *   resultado de Retention: ambas quedan en el inline.
 * - `_fmtUptime` — la usan `loadPerformance` y `_repDur` (Reports, inline):
 *   compartida, queda.
 * - `_donut` — `loadPerformance` no la llama (dibuja los donuts inline en
 *   su template); la usa `repRender` (Reports, inline): queda.
 * - `devices` — array de inventario compartido, vive en el inline.
 *
 * Depende (en tiempo de llamada): core.js (A, h), inline (devices,
 * _fmtUptime). Handlers que la resuelven por window: botón Refresh de
 * #v-performance y swView('performance'). Script clásico sin defer/async;
 * top-level sin ejecución.
 */

    async function loadPerformance() {
      const kpis = document.getElementById('perf-kpis');
      const grid = document.getElementById('perf-grid');
      if (!kpis || !grid) return;
      kpis.innerHTML = '<div class="sp"></div>';
      grid.innerHTML = '<div class="sp" style="grid-column:1/-1"></div>';

      // Snapshot del poller (latencia/estado por dispositivo)
      let devMetrics = {};
      try {
        const dmr = await fetch(`${A}/system/devices`, { headers: h(), signal: AbortSignal.timeout(6000) });
        if (dmr.ok) devMetrics = (await dmr.json()).devices || {};
      } catch (e) { }

      // Health scores (antes vista "Health Scores", ahora integrada aquí)
      const healthMap = {};
      try {
        const hr = await fetch(`${A}/ops/health/all`, { headers: h(), signal: AbortSignal.timeout(15000) });
        if (hr.ok) (await hr.json()).forEach(h => { healthMap[h.device_id] = h; });
      } catch (e) { }

      // Preferir el snapshot del poller (ya trae CPU/RAM/disco/latencia/uptime).
      // Solo consultamos en vivo los equipos que no están en el snapshot.
      const promises = devices.map(async d => {
        const m = devMetrics[d.id];
        const hasSnap = m && typeof m.cpu !== 'undefined' && !m.error;
        if (hasSnap) {
          return {
            id: d.id, driver: d.driver, online: m.online !== false,
            cpu: Math.round(m.cpu || 0),
            memPct: Math.round(m.memory_percent || 0),
            memUsed: Math.round((m.memory_used || 0) / 1048576),
            memTotal: Math.round((m.memory_total || 0) / 1048576),
            dskPct: Math.round(m.disk_percent || 0),
            lat: m.latency_ms, loss: m.packet_loss, uptime: m.uptime_seconds,
            error: false,
          };
        }
        if (!d._online) return { id: d.id, driver: d.driver, online: false, cpu: 0, memPct: 0, memUsed: 0, memTotal: 0, dskPct: 0, lat: m?.latency_ms, loss: m?.packet_loss, error: true };
        try {
          const controller = new AbortController();
          const timer = setTimeout(() => controller.abort(), 3000);
          const rr = await fetch(`${A}/facts/${encodeURIComponent(d.id)}/resources?_t=${Date.now()}`, { headers: h(), signal: controller.signal });
          clearTimeout(timer);
          if (rr.ok) {
            const rd = await rr.json();
            const cpu = Math.round(rd.cpu || 0);
            const memPct = rd.memory_total ? Math.round(((rd.memory_used || rd.memory_total - rd.memory_free) / rd.memory_total) * 100) : 0;
            const memUsed = rd.memory_total ? Math.round((rd.memory_used || rd.memory_total - rd.memory_free) / 1024 / 1024) : 0;
            const memTotal = rd.memory_total ? Math.round(rd.memory_total / 1024 / 1024) : 0;
            const dskPct = rd.hdd_total ? Math.round((rd.hdd_total - rd.hdd_free) / rd.hdd_total * 100) : 0;
            return { id: d.id, driver: d.driver, online: true, cpu, memPct, memUsed, memTotal, dskPct, lat: m?.latency_ms, loss: m?.packet_loss, uptime: rd.uptime };
          }
        } catch (e) { }
        const cpu = parseInt(d._cpu) || 0;
        const memPct = parseInt(d._mem) || 0;
        const dskPct = parseInt(d._dsk) || 0;
        return { id: d.id, driver: d.driver, online: true, cpu, memPct, memUsed: 0, memTotal: 0, dskPct, lat: m?.latency_ms, loss: m?.packet_loss, uptime: m?.uptime_seconds };
      });
      const results = await Promise.all(promises);
      results.forEach(r => {
        const h = healthMap[r.id];
        r.score = h ? h.score : null;
        r.healthStatus = h ? h.status : null;
      });

      // KPIs
      const valid = results.filter(r => !r.error);
      const scored = results.filter(r => r.score !== null && r.score !== undefined);
      const avgHealth = scored.length ? Math.round(scored.reduce((s, r) => s + r.score, 0) / scored.length) : null;
      const avgCpu = valid.length ? Math.round(valid.reduce((s, r) => s + r.cpu, 0) / valid.length) : 0;
      const avgMem = valid.length ? Math.round(valid.reduce((s, r) => s + r.memPct, 0) / valid.length) : 0;
      const avgDsk = valid.length ? Math.round(valid.reduce((s, r) => s + r.dskPct, 0) / valid.length) : 0;
      const maxCpu = valid.length ? valid.reduce((m, r) => r.cpu > m.cpu ? r : m, valid[0]) : null;
      const cpuColor = avgCpu > 80 ? 'var(--red)' : avgCpu > 50 ? 'var(--yellow)' : 'var(--green)';
      const healthColor = avgHealth === null ? 'var(--text-ter)' : avgHealth >= 80 ? 'var(--green)' : avgHealth >= 50 ? 'var(--yellow)' : 'var(--red)';

      kpis.innerHTML = [
        { label: 'Health Score', val: avgHealth === null ? '—' : avgHealth + '/100', color: healthColor },
        { label: 'Avg CPU', val: avgCpu + '%', color: cpuColor },
        { label: 'Avg RAM', val: avgMem + '%', color: avgMem > 80 ? 'var(--red)' : 'var(--green)' },
        { label: 'Avg Disk', val: avgDsk + '%', color: avgDsk > 85 ? 'var(--red)' : 'var(--green)' },
        { label: 'Dispositivos', val: results.length, color: 'var(--accent)' },
        { label: 'Hot CPU', val: maxCpu ? maxCpu.id : '—', color: 'var(--orange)' },
      ].map(k => `<div style="background:var(--panel);border:1px solid var(--border);border-radius:var(--radius-sm);padding:14px 16px;border-left:3px solid ${k.color}">
        <div style="font-size:9px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px">${k.label}</div>
        <div style="font-weight:700;font-size:16px;color:var(--text)">${k.val}</div>
      </div>`).join('');

      // Per-device cards
      grid.innerHTML = results.map(r => `<div style="background:var(--panel);border:1px solid var(--border);border-radius:var(--radius-lg);padding:20px;opacity:${r.error ? .4 : 1}">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
          <div style="display:flex;align-items:center;gap:8px">
            <span style="width:8px;height:8px;border-radius:50%;background:${r.online && !r.error ? 'var(--green)' : 'var(--red)'}"></span>
            <strong style="font-size:14px">${r.id}</strong>
            <span class="badge" style="background:var(--surface);font-size:9px">${r.driver.toUpperCase()}</span>
            ${r.score !== null && r.score !== undefined ? `<span class="badge ${r.healthStatus === 'healthy' ? 'badge-g' : r.healthStatus === 'degraded' ? 'badge-b' : 'badge-r'}" style="font-size:9px"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z"/></svg> ${r.score}/100</span>` : ''}
          </div>
        </div>
        <div style="display:flex;gap:16px;justify-content:center;padding:8px 0">
          ${r.error ? '<div class="empty" style="padding:10px!important;flex:1"><span style="font-size:24px"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="9"/></svg></span><br><span style="font-size:10px;color:var(--text-ter)">Data Unreachable</span></div>' :
          [{ l: 'CPU', p: r.cpu, c: 'var(--accent)' }, { l: 'RAM', p: r.memPct, c: 'var(--green)', s: r.memUsed + '/' + r.memTotal + 'MB' }, { l: 'DISK', p: r.dskPct, c: 'var(--yellow)' }].map(o => `<div style="text-align:center;flex:1"><div class="donut" style="--pct:${o.p};width:56px;height:56px;margin:0 auto"><svg viewBox="0 0 36 36" fill="none" stroke="currentColor"><path class="donut-bg" d="M18 2.0845a15.9155 15.9155 0 0 1 0 31.831a15.9155 15.9155 0 0 1 0-31.831"/><path class="donut-fg" stroke="${o.c}" stroke-dasharray="${o.p},100" d="M18 2.0845a15.9155 15.9155 0 0 1 0 31.831a15.9155 15.9155 0 0 1 0-31.831"/></svg><div class="donut-lbl" style="font-size:11px">${o.p}%</div></div><div style="font-size:10px;font-weight:600;margin-top:6px;color:var(--text-secondary)">${o.l}</div>${o.s ? '<div style="font-size:9px;color:var(--text-ter)">' + o.s + '</div>' : ''}</div>`).join('')}
        </div>
        <div style="display:flex;gap:14px;justify-content:center;flex-wrap:wrap;margin-top:4px;padding-top:10px;border-top:1px solid var(--border-s);font-size:10px;color:var(--text-ter)">
          ${r.lat !== undefined && r.lat !== null ? `<span><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 12m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0"/><path d="M16.24 7.76a6 6 0 0 1 0 8.49"/><path d="M7.76 16.24a6 6 0 0 1 0-8.49"/></svg> Latencia: <strong style="color:var(--text-secondary)">${Number(r.lat).toFixed(2)} ms</strong></span>` : ''}
          ${r.loss !== undefined && r.loss !== null ? `<span><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m22 17-8.5-8.5-5 5L2 7M16 17h6v-6"/></svg> Pérdida: <strong style="color:${r.loss > 0 ? 'var(--orange)' : 'var(--text-secondary)'}">${Math.round(r.loss)}%</strong></span>` : ''}
          ${r.uptime ? `<span><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="13" r="8"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="9" y1="2" x2="15" y2="2"/></svg> Uptime: <strong style="color:var(--text-secondary)">${_fmtUptime(r.uptime)}</strong></span>` : ''}
        </div>
        <div style="margin-top:12px;display:flex;gap:6px">
          ${r.error ? '<span class="badge badge-r" style="font-size:9px">Device Offline / API Error</span>' : `
            ${r.cpu > 80 ? '<span class="badge badge-r" style="font-size:9px"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 3c1 5 6 6 6 12a6 6 0 0 1-12 0c0-3 2-5 3-6 0 3 1 4 2 4 2-3 2-6 1-10Z"/></svg> CPU Alta</span>' : ''}
            ${r.memPct > 85 ? '<span class="badge badge-r" style="font-size:9px"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m12 3 10 18H2Z"/><path d="M12 9v4M12 17h.01"/></svg> RAM Crítica</span>' : ''}
            ${r.dskPct > 90 ? '<span class="badge badge-r" style="font-size:9px"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="22" y1="12" x2="2" y2="12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><line x1="6" y1="16" x2="6.01" y2="16"/><line x1="10" y1="16" x2="10.01" y2="16"/></svg> Disco Lleno</span>' : ''}
            ${r.cpu <= 80 && r.memPct <= 85 && r.dskPct <= 90 ? '<span class="badge badge-g" style="font-size:9px"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg> Saludable</span>' : ''}
          `}
        </div>
      </div>`).join('');

      if (!results.length) grid.innerHTML = '<div style="grid-column:1/-1;text-align:center;padding:40px;color:var(--text-ter)">No hay datos de recursos disponibles. Verifica que los dispositivos estén online.</div>';
    }
