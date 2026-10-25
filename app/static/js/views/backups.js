/**
 * NetPulse NOC — vista Backups: inventario de backups, backup masivo, offsite
 * y scheduler (views/backups.js).
 *
 * Extraída del <script> inline de app/static/dashboard.html (Tarjeta 10D-G,
 * lote g, la tarjeta "acoplada"), sin cambio de comportamiento: líneas en el
 * mismo orden relativo que en el monolito (buildSchedForms 3364 <
 * backupAllDevices 3742 < offsiteExport 3989 < loadBk 4040 < dlBackup 4092 <
 * scheduler 4104-4140). Top-level sin ejecución de lógica (solo declaraciones
 * `function`), así que el orden de carga entre vistas no es observable.
 *
 * Contenido nombre a nombre:
 * - `buildSchedForms` — pinta los selects de #sched-single-form /
 *   #sched-rec-form. La llaman loadDev (views/devices.js, en cada latido de
 *   30 s: trampa §7.6 del mapa, conservada tal cual) y swView('backups').
 * - `backupAllDevices` — botón "Backup All" de #v-backups; termina llamando
 *   `loadBk`.
 * - `offsiteExport` — exportación offsite (POST /config/offsite/export).
 * - `loadBk` — latido de 60 s (_npTick en init, inline residual) y refresco de
 *   la vista: KPIs (#bk-kpis), tabla (#bk-list) y badges #sb-bk / #info-bk
 *   (exclusivos de esta vista, verificado por grep). Acople documentado: junto
 *   al prepado de inventario llama `populateCmdDevices` — que NO viajó,
 *   sigue en el inline (bloque GLOBAL COMMAND CENTER): tras mover todo el
 *   bloque de backups sus consumidores son loadBk (aquí) y swView('commands')
 *   (inline), así que mantiene N>1 usuarios regla 2; Commands también la
 *   ejecuta (views/commands.js, Lote E). Escribir #cmd-dev (otra vista) es
 *   un efecto colateral del latido, conservado tal cual.
 * - `dlBackup` — descarga (onclick generado en la tabla de loadBk).
 * - Scheduler: `loadJobs` (lista de #jobs-list), `doBackupMulti`, sus dos
 *   wrappers `scheduleBackup`/`scheduleRecurring` (los onclick los generan
 *   buildSchedForms aquí mismo) y `cancelJob` (onclick generado por
 *   loadJobs). Exclusivos de Backups, verificado por grep.
 *
 * Lo que NO viajó (regla 2, tiempo de llamada): core.js (A, h, t, toast, _npTick,
 * __t), `devices` (estado compartido, inline), `esc` (inline), `qaB` — acción
 * rápida del modal #dp que llama loadBk (queda en el inline con el detalle de
 * equipo, lote h). Trampa §7.7 del mapa: loadBk reconstruye #cmd-dev en cada
 * latido y se come la selección del operador de Commands — defecto
 * preexistente, NO se arregla aquí.
 */

    function buildSchedForms() {
      const opts = '<option value="all">-- TODOS LOS DISPOSITIVOS --</option>' + devices.map(d => `<option value="${d.id}">${d.id} (${d.driver})</option>`).join('');
      const u = `<select class="sched-unit" style="background:var(--bg);border:1px solid var(--border);color:var(--text);padding:8px;border-radius:6px;width:90px;font-size:11px"><option value="1">Minutos</option><option value="60">Horas</option><option value="1440">Días</option><option value="10080">Semanas</option><option value="43200">Meses</option></select>`;
      const s = document.getElementById('sched-single-form'); if (s) s.innerHTML = `<div class="fg"><label>Dispositivo / Grupo</label><select id="sched-dev" style="background:var(--bg);border:1px solid var(--border);color:var(--text);padding:8px 10px;border-radius:6px;width:100%;font-size:12px">${opts}</select></div>
    <div class="fg" style="margin-top:6px"><label>Dentro de cuánto tiempo</label><div style="display:flex;gap:6px"><input id="sched-delay" type="number" placeholder="30" value="30" style="flex:1;min-width:0">${u.replace('class="sched-unit"', 'id="sched-delay-unit"')}<button class="btn btn-p btn-sm" onclick="scheduleBackup()" style="white-space:nowrap"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 5v14M5 12h14"/></svg> Programar</button></div></div>`;
      const r = document.getElementById('sched-rec-form'); if (r) r.innerHTML = `<div class="fg"><label>Dispositivo / Grupo</label><select id="sched-rec-dev" style="background:var(--bg);border:1px solid var(--border);color:var(--text);padding:8px 10px;border-radius:6px;width:100%;font-size:12px">${opts}</select></div>
    <div class="fg" style="margin-top:6px"><label>Ejecutar repetitivamente cada</label><div style="display:flex;gap:6px"><input id="sched-rec-int" type="number" placeholder="1" value="1" style="flex:1;min-width:0">${u.replace('class="sched-unit"', 'id="sched-rec-unit"')}<button class="btn btn-p btn-sm" onclick="scheduleRecurring()" style="white-space:nowrap"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 5v14M5 12h14"/></svg> Programar</button></div></div>`
    }

    // ── BACKUP ALL ──
    async function backupAllDevices() {
      toast('<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m12 3 9 5v8l-9 5-9-5V8zM3 8l9 5 9-5M12 13v8M7.5 5.5l9 5"/></svg> Iniciando backup masivo...', 'i');
      let ok = 0, fail = 0;
      for (const d of devices) {
        try {
          const r = await fetch(`${A}/config/${encodeURIComponent(d.id)}/backup`, { method: 'POST', headers: h() });
          const res = await r.json();
          if (res.success) ok++; else fail++;
        } catch (e) { fail++; }
      }
      toast(`<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg> ${ok} backups exitosos, ${fail} fallidos`, ok > 0 ? 's' : 'error');
      loadBk();
    }

    // ── BACKUPS ──
    async function offsiteExport() {
      try {
        const r = await fetch(`${A}/config/offsite/export`, { method: 'POST', headers: h() });
        const d = await r.json();
        if (!r.ok) { toast(d.detail || 'Error offsite', ''); return }
        toast(`<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20 16a4 4 0 0 0-.88-7.9A7 7 0 1 0 4 16Z"/></svg> Offsite: ${d.uploaded || 0} subidos, ${d.failed || 0} fallos${d.skipped ? ' (' + d.skipped + ')' : ''}`, d.failed ? '' : 's');
      } catch (e) { toast('Error offsite', ''); }
    }

    async function loadBk() {
      if (!t) return;
      try {
        let all = [];
        for (const d of devices) {
          try { const r = await fetch(`${A}/config/${encodeURIComponent(d.id)}/backups`, { headers: h() }); if (r.ok) { const l = await r.json(); all = all.concat(l.map(b => ({ ...b, device: d.id }))) } } catch (e) { }
        }
        all.sort((a, b) => b.timestamp - a.timestamp);
        document.getElementById('sb-bk').textContent = all.length;
        document.getElementById('info-bk').textContent = all.length;
        populateCmdDevices();

        // KPIs
        const kpis = document.getElementById('bk-kpis');
        const totalSize = all.reduce((s, b) => s + (b.size || 0), 0);
        const uniqueDevs = new Set(all.map(b => b.device || b.filename?.split('_')[0])).size;
        const lastBk = all.length ? new Date(all[0].timestamp * 1000).toLocaleString('es', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : '—';
        if (kpis) kpis.innerHTML = [
          { label: 'Total Backups', val: all.length, color: 'var(--accent)' },
          { label: 'Almacenamiento', val: totalSize > 1024 * 1024 ? (totalSize / 1024 / 1024).toFixed(1) + ' MB' : (totalSize / 1024).toFixed(1) + ' KB', color: 'var(--green)' },
          { label: 'Dispositivos', val: uniqueDevs, color: 'var(--yellow)' },
          { label: 'Último Backup', val: lastBk, color: 'var(--accent)' },
        ].map(k => `<div style="background:var(--panel);border:1px solid var(--border);border-radius:var(--radius-sm);padding:14px 16px;border-left:3px solid ${k.color}">
          <div style="font-size:9px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px">${k.label}</div>
          <div style="font-weight:700;font-size:15px;color:var(--text)">${k.val}</div>
        </div>`).join('');

        // Table
        const bel = document.getElementById('bk-list');
        if (bel) bel.innerHTML = all.length ? `<table style="width:100%;border-collapse:collapse">
          <thead><tr style="border-bottom:1px solid var(--border)">
            <th style="padding:12px 16px;text-align:left;font-size:10px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.5px">Dispositivo</th>
            <th style="padding:12px 16px;text-align:left;font-size:10px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.5px">Archivo</th>
            <th style="padding:12px 16px;text-align:left;font-size:10px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.5px">Tamaño</th>
            <th style="padding:12px 16px;text-align:left;font-size:10px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.5px">Fecha</th>
            <th style="padding:12px 16px;text-align:right;font-size:10px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.5px">Acción</th>
          </tr></thead>
          <tbody>${all.slice(0, 50).map(b => {
          const dev = b.device || b.filename?.split('_')[0] || '?';
          const sz = b.size > 1024 ? (b.size / 1024).toFixed(1) + ' KB' : b.size + ' B';
          const dt = new Date(b.timestamp * 1000).toLocaleString('es', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
          return `<tr style="border-bottom:1px solid var(--border-s);transition:background .15s" onmouseover="this.style.background='var(--surface-hover)'" onmouseout="this.style.background='transparent'">
              <td style="padding:10px 16px;font-weight:600;font-size:12px;color:var(--text)">${dev}</td>
              <td style="padding:10px 16px;font-family:var(--mono);font-size:11px;color:var(--text-secondary)">${esc(b.filename)}</td>
              <td style="padding:10px 16px;font-size:11px;color:var(--text-ter)">${sz}</td>
              <td style="padding:10px 16px;font-size:11px;color:var(--text-ter)">${dt}</td>
              <td style="padding:10px 16px;text-align:right"><button class="btn btn-o btn-xs" onclick="dlBackup('${dev}','${esc(b.filename)}')" style="font-size:10px"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M12 3v12m-5-5 5 5 5-5"/></svg> Download</button></td>
            </tr>`}).join('')}</tbody>
        </table>` : '<div style="padding:40px;text-align:center;color:var(--text-ter)"><div style="font-size:32px;margin-bottom:12px"><svg class="ic" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false" style="opacity:0.4;color:var(--text-ter)"><line x1="22" y1="12" x2="2" y2="12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><line x1="6" y1="16" x2="6.01" y2="16"/><line x1="10" y1="16" x2="10.01" y2="16"/></svg></div>No hay backups guardados.<br><small>Usa el botón "Backup All" o ejecuta un backup desde cada dispositivo.</small></div>';
      } catch (e) { }
    }

    async function dlBackup(id, fname) {
      try {
        const r = await fetch(`${A}/config/${encodeURIComponent(id)}/backups/${encodeURIComponent(fname)}`, { headers: h() });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        const blob = await r.blob();
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = fname;
        a.click(); URL.revokeObjectURL(a.href); toast('<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M12 3v12m-5-5 5 5 5-5"/></svg> Descargado', 'info')
      } catch (e) { toast('Error al descargar', 'error') }
    }

    // ── SCHEDULER ──
    async function loadJobs() {
      const el = document.getElementById('jobs-list'); if (!el || !t) return;
      try {
        const r = await fetch(`${A}/scheduler/jobs`, { headers: h() });
        const jobs = await r.json();
        if (!Array.isArray(jobs) || !jobs.length) { el.innerHTML = '<div class="empty"><span class="icon"><svg class="ic" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false" style="opacity:0.4;color:var(--text-ter)"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg></span><br>No hay trabajos programados</div>'; return }
        const activeJobs = jobs.filter(j => j.status !== 'cancelled');
        if (!activeJobs.length) { el.innerHTML = '<div class="empty"><span class="icon"><svg class="ic" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false" style="opacity:0.4;color:var(--text-ter)"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg></span><br>No hay trabajos activos (solo históricos cancelados)</div>'; return }
        el.innerHTML = `<div style="border:1px solid var(--border);border-radius:6px"><table><tr><th>Dispositivo</th><th>Tipo</th><th>Estado</th><th>Programado</th><th>Acción</th></tr>
      ${activeJobs.map(j => `<tr><td style="font-weight:500">${esc(j.device_id === 'all' ? 'Todos' : j.device_id || '?')}</td>
      <td>${j.job_type === 'recurring' ? '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M21 2v6h-6"/><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M3 22v-6h6"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/></svg> Recurrente' : '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M5 2h14M5 22h14M7 2v4l10 12v4M17 2v4L7 18v4"/></svg> Único'}</td>
      <td><span class="badge ${j.status === 'pending' || j.status === 'scheduled' ? 'badge-b' : j.status === 'completed' ? 'badge-g' : 'badge-r'}">${j.status || '?'}</span></td>
      <td style="font-size:10px">${j.run_at ? new Date(j.run_at).toLocaleString() : j.interval_minutes ? `Cada ${j.interval_minutes} min` : '—'}</td>
      <td><button class="btn btn-o btn-xs" onclick="cancelJob('${j.job_id || j.id}')" title="Cancelar"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button></td></tr>`).join('')}</table></div>`
      } catch (e) { if (document.getElementById('jobs-list')) document.getElementById('jobs-list').innerHTML = '<div style="font-size:11px;color:var(--text-ter)">Error</div>' }
    }
    async function doBackupMulti(isRec, dev, val, unit) {
      if (!dev || !val) return;
      const totalMin = parseInt(val) * parseInt(unit || 1);
      const devs = dev === 'all' ? devices.map(d => d.id) : [dev];
      try {
        for (let d of devs) {
          const ep = isRec ? '/scheduler/backup/recurring' : '/scheduler/backup';
          const body = isRec ? { device_id: d, interval_minutes: totalMin } : { device_id: d, delay_minutes: totalMin };
          await fetch(`${A}${ep}`, { method: 'POST', headers: h(), body: JSON.stringify(body) });
        }
        toast(`<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg> Programa activado para ${devs.length} equipo(s)`, 's'); loadJobs();
      } catch (e) { toast('Error al programar múltiples o individual', 'error') }
    }

    function scheduleBackup() { doBackupMulti(false, document.getElementById('sched-dev')?.value, document.getElementById('sched-delay')?.value, document.getElementById('sched-delay-unit')?.value); }
    function scheduleRecurring() { doBackupMulti(true, document.getElementById('sched-rec-dev')?.value, document.getElementById('sched-rec-int')?.value, document.getElementById('sched-rec-unit')?.value); }
    async function cancelJob(id) {
      try { await fetch(`${A}/scheduler/jobs/${id}`, { method: 'DELETE', headers: h() }); toast('<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> Trabajo cancelado', 'info'); loadJobs() }
      catch (e) { toast('Error al cancelar', 'error') }
    }
