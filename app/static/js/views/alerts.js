/**
 * NetPulse NMS — vista Alerts: alertas activas + admin de silencios y reglas
 * (views/alerts.js). Vista más grande del tramo medio.
 *
 * Extraída del <script> inline de app/static/dashboard.html (Tarjeta 10D-C,
 * lote 3) sin cambio de comportamiento: loadAlerts, loadAlertSilences,
 * loadAlertRules + helpers `al*`/`sil*` del bloque, líneas en el mismo orden
 * relativo. El estado de módulo `alSilData`, `alSilConfirmId`, `alRulesData`
 * (`let`) y `alRulesOpen` (`const`) viaja con el bloque: al nivel superior de
 * un script clásico comparte el entorno léxico global, igual que cuando vivía
 * en el inline.
 *
 * Lo que NO viajó (verificado por grep, regla 2):
 * - `alDur` — la usan los renders de esta vista y `_ageHuman` (bloque
 *   Sites/Retention, inline): compartida, queda en el inline; los renders
 *   la resuelven en tiempo de llamada.
 * - `refreshAlertSummary` — badge del sidebar del dashboard (le imprime en
 *   #sb-alerts y llama updStats): es OTRA cosa, sigue en el inline.
 * - `__isAdmin`, `esc`, `escAttr`, `toast`, `t`, `h`, `A`, `devices` —
 *   core / compartidos.
 *
 * Depende (en tiempo de llamada): core.js (A, h, __t, toast), inline
 * (esc, escAttr, alDur, devices). Handlers por window: botón Refresh de
 * #v-alerts (onclick="loadAlerts();loadAlertSilences();loadAlertRules()"),
 * swView('alerts'), togSilForm/silAddMatcher/alSubmitSilence en el markup
 * del formulario, sil-confirm `alSilCancel`, `alToggleExpr` en la tabla de
 * reglas. Script clásico sin defer/async; LET/CONST top-level sin ejecución
 * de lógica.
 */

    // ── ALERTS VIEW (Real Threshold-Based) ──
    async function loadAlerts() {
      const kpis = document.getElementById('alert-kpis');
      const list = document.getElementById('alert-list');
      if (!kpis || !list) return;
      list.innerHTML = '<div class="sp" style="margin:20px auto"></div>';

      const alerts = [];
      let prometheusAvailable = false;
      const push = (sev, dev, msg, icon) => alerts.push({ sev, dev, msg, icon });

      // ── Alertas de Prometheus (reglas) ──
      try {
        const ar = await fetch(`${A}/alerts/active`, { headers: h() });
        if (ar.ok) {
          const ad = await ar.json();
          prometheusAvailable = ad.status === 'success';
          (ad.alerts || []).forEach(a => {
            const sev = a.severity === 'critical' ? 'critical' : a.severity === 'warning' ? 'warning' : 'info';
            const icon = a.severity === 'critical' ? '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M7 18v-6a5 5 0 0 1 10 0v6M5 18h14v3H5zM12 2v2M3 7l2 1M21 7l-2 1M5 2l1 2M19 2l-1 2"/></svg>' : a.severity === 'warning' ? '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m12 3 10 18H2Z"/><path d="M12 9v4M12 17h.01"/></svg>' : '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>';
            push(sev, a.device_id || a.name, `${a.name}: ${a.summary || a.description || ''}`.trim(), icon);
          });
        }
      } catch (e) { }
      if (!prometheusAvailable) push('warning', 'Prometheus', 'No se pudieron consultar las alertas de Prometheus; su estado es desconocido', '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m12 3 10 18H2Z"/><path d="M12 9v4M12 17h.01"/></svg>');

      // ── Recursos del servidor NetPulse ──
      let ov = null;
      try { const r = await fetch(`${A}/system/overview`, { headers: h(), signal: AbortSignal.timeout(8000) }); if (r.ok) ov = await r.json(); } catch (e) { }
      if (ov) {
        const cpu = ov.host?.cpu?.percent ?? 0, mem = ov.host?.memory?.percent ?? 0;
        if (cpu > 90) push('critical', 'Servidor NetPulse', `CPU en ${Math.round(cpu)}% — carga crítica`, '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 3c1 5 6 6 6 12a6 6 0 0 1-12 0c0-3 2-5 3-6 0 3 1 4 2 4 2-3 2-6 1-10Z"/></svg>');
        else if (cpu > 80) push('warning', 'Servidor NetPulse', `CPU en ${Math.round(cpu)}% — carga elevada`, '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>');
        if (mem > 92) push('critical', 'Servidor NetPulse', `RAM en ${Math.round(mem)}% — memoria casi agotada`, '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="3" y="6" width="18" height="12" rx="2"/><path d="M7 10h3v4H7zM14 10h3v4h-3zM7 18v3M12 18v3M17 18v3"/></svg>');
        else if (mem > 85) push('warning', 'Servidor NetPulse', `RAM en ${Math.round(mem)}% — uso elevado`, '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="3" y="6" width="18" height="12" rx="2"/><path d="M7 10h3v4H7zM14 10h3v4h-3zM7 18v3M12 18v3M17 18v3"/></svg>');
        (ov.host?.disks || []).filter(dk => !String(dk.mountpoint).startsWith('/System/Volumes')).forEach(dk => {
          if (dk.percent > 90) push('critical', 'Servidor NetPulse', `Disco ${dk.mountpoint} en ${Math.round(dk.percent)}% — espacio crítico`, '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="22" y1="12" x2="2" y2="12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><line x1="6" y1="16" x2="6.01" y2="16"/><line x1="10" y1="16" x2="10.01" y2="16"/></svg>');
          else if (dk.percent > 80) push('warning', 'Servidor NetPulse', `Disco ${dk.mountpoint} en ${Math.round(dk.percent)}% — espacio bajo`, '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="22" y1="12" x2="2" y2="12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><line x1="6" y1="16" x2="6.01" y2="16"/><line x1="10" y1="16" x2="10.01" y2="16"/></svg>');
        });
        const l5 = ov.host?.cpu?.load5 ?? 0, cores = ov.host?.cpu?.count_logical ?? 1;
        if (cores && l5 > cores * 2) push('warning', 'Servidor NetPulse', `Load promedio alto (${l5}) sobre ${cores} núcleos`, '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m22 7-8.5 8.5-5-5L2 17M16 7h6v6"/></svg>');
        if (ov.poller && ov.poller.running === false) push('warning', 'Servidor NetPulse', 'El poller de métricas está detenido', '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/></svg>');
      }

      // ── Recursos/latencia de dispositivos (snapshot del poller) ──
      let snap = null;
      try { const r = await fetch(`${A}/system/devices`, { headers: h(), signal: AbortSignal.timeout(8000) }); if (r.ok) snap = await r.json(); } catch (e) { }
      const devMetrics = (snap && snap.devices) || {};
      const known = devices.length ? devices.map(d => d.id) : Object.keys(devMetrics);
      known.forEach(id => {
        const m = devMetrics[id] || {};
        const knownDevice = devices.find(d => d.id === id);
        if (m.online === false || (m.online == null && knownDevice?._online === false)) push('critical', id, 'Dispositivo OFFLINE (sin respuesta ICMP/recursos)', '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="9"/></svg>');
        const cpu = Math.round(m.cpu || 0), mem = Math.round(m.memory_percent || 0), dsk = Math.round(m.disk_percent || 0);
        if (cpu > 90) push('critical', id, `CPU en ${cpu}% — carga crítica`, '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 3c1 5 6 6 6 12a6 6 0 0 1-12 0c0-3 2-5 3-6 0 3 1 4 2 4 2-3 2-6 1-10Z"/></svg>');
        else if (cpu > 75) push('warning', id, `CPU en ${cpu}% — carga elevada`, '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>');
        if (mem > 90) push('critical', id, `RAM en ${mem}% — memoria casi agotada`, '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="3" y="6" width="18" height="12" rx="2"/><path d="M7 10h3v4H7zM14 10h3v4h-3zM7 18v3M12 18v3M17 18v3"/></svg>');
        else if (mem > 80) push('warning', id, `RAM en ${mem}% — uso elevado`, '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="3" y="6" width="18" height="12" rx="2"/><path d="M7 10h3v4H7zM14 10h3v4h-3zM7 18v3M12 18v3M17 18v3"/></svg>');
        if (dsk > 90) push('critical', id, `Disco en ${dsk}% — espacio crítico`, '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="22" y1="12" x2="2" y2="12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><line x1="6" y1="16" x2="6.01" y2="16"/><line x1="10" y1="16" x2="10.01" y2="16"/></svg>');
        else if (dsk > 80) push('warning', id, `Disco en ${dsk}% — espacio bajo`, '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="22" y1="12" x2="2" y2="12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><line x1="6" y1="16" x2="6.01" y2="16"/><line x1="10" y1="16" x2="10.01" y2="16"/></svg>');
        const loss = m.packet_loss, lat = m.latency_ms;
        if (loss > 20) push('critical', id, `Pérdida de paquetes ${Math.round(loss)}%`, '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m22 17-8.5-8.5-5 5L2 7M16 17h6v-6"/></svg>');
        else if (loss > 5) push('warning', id, `Pérdida de paquetes ${Math.round(loss)}%`, '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m22 17-8.5-8.5-5 5L2 7M16 17h6v-6"/></svg>');
        if (lat > 200) push('warning', id, `Latencia alta ${Number(lat).toFixed(0)} ms`, '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M3 17a10 10 0 1 1 18 0M12 13l4-4"/><circle cx="12" cy="13" r="1"/></svg>');
        if (m.error && m.online !== false) push('warning', id, `Recursos no disponibles: ${m.error}`, '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m12 3 10 18H2Z"/><path d="M12 9v4M12 17h.01"/></svg>');
      });

      if (!alerts.length) {
        const verified = known.length > 0 && known.every(id => {
          const state = devMetrics[id]?.online;
          return state === true || (state == null && devices.find(d => d.id === id)?._online === true);
        });
        if (prometheusAvailable && verified) push('info', 'Sistema', `Los ${known.length} equipos conocidos responden y no hay alertas activas`, '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg>');
        else push('info', 'Observabilidad', 'Estado sin verificar: revisa Prometheus y la salud de los equipos', '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>');
      }
      const rank = { critical: 0, warning: 1, info: 2 };
      alerts.sort((a, b) => rank[a.sev] - rank[b.sev]);

      const crit = alerts.filter(a => a.sev === 'critical').length;
      const warn = alerts.filter(a => a.sev === 'warning').length;
      const info = alerts.filter(a => a.sev === 'info').length;

      kpis.innerHTML = [
        { label: 'Critical', val: crit, bg: 'var(--red-bg)', color: 'var(--red)', border: 'rgba(239,68,68,.3)' },
        { label: 'Warning', val: warn, bg: 'var(--yellow-bg)', color: 'var(--yellow)', border: 'rgba(245,158,11,.3)' },
        { label: 'Info', val: info, bg: 'var(--blue-bg)', color: 'var(--accent)', border: 'rgba(59,130,246,.3)' },
        { label: 'Total', val: alerts.length, bg: 'var(--panel)', color: 'var(--text)', border: 'var(--border)' },
      ].map(k => `<div style="background:${k.bg};border:1px solid ${k.border};padding:12px 20px;border-radius:var(--radius-sm);min-width:100px">
        <div style="font-size:9px;color:${k.color};text-transform:uppercase;font-weight:600;letter-spacing:.5px">${k.label}</div>
        <div style="font-weight:700;font-size:20px;color:${k.color}">${k.val}</div>
      </div>`).join('');

      const sevMap = { critical: { bg: 'var(--red-bg)', border: 'rgba(239,68,68,0.2)', color: 'var(--red)', label: 'CRITICAL' }, warning: { bg: 'var(--yellow-bg)', border: 'rgba(245,158,11,0.2)', color: 'var(--yellow)', label: 'WARNING' }, info: { bg: 'var(--blue-bg)', border: 'rgba(59,130,246,0.2)', color: 'var(--accent)', label: 'INFO' } };

      list.innerHTML = alerts.map(a => {
        const s = sevMap[a.sev] || sevMap.info;
        return `<div style="background:${s.bg};border:1px solid ${s.border};padding:14px 18px;border-radius:var(--radius-sm);display:flex;justify-content:space-between;align-items:center;gap:12px">
          <div style="display:flex;align-items:center;gap:12px;flex:1;min-width:0">
            <span style="font-size:18px">${a.icon}</span>
            <div style="min-width:0">
              <div style="display:flex;align-items:center;gap:8px;margin-bottom:2px">
                <span class="badge" style="background:${s.color};color:#fff;font-size:8px;padding:2px 6px">${s.label}</span>
                <strong style="font-size:13px;color:var(--text)">${a.dev}</strong>
              </div>
              <div style="font-size:12px;color:var(--text-secondary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${a.msg}</div>
            </div>
          </div>
          <div style="font-size:10px;color:var(--text-ter);white-space:nowrap">${new Date().toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit' })}</div>
        </div>`;
      }).join('');
    }

    // ── ADMIN DE ALERTAS: silencios (Alertmanager) + reglas (Prometheus) ──
    // Contrato: docs/handoffs/alerts_ui.md — prefijo /api/alerts/admin (nunca otro path).
    // Principio PRODUCT.md #4: ante error de red/servicio se muestra estado explícito gris,
    // nunca verde ni listas vacías que parezcan éxito.
    let alSilData = null, alSilConfirmId = null, alRulesData = null;
    const alRulesOpen = new Set();

    function alUnavail(key, detail) {
      // Banner gris "no verificable" (PRODUCT.md #4): nunca verde, nunca vacío aparentando éxito.
      return `<div style="background:var(--panel);border:1px dashed var(--border);border-radius:var(--radius-sm);padding:12px 14px;font-size:12px;color:var(--text-ter)">
        ${esc(__t(key))}${detail ? ` — <span title="${escAttr(detail)}" style="color:var(--text-muted)">${esc(detail)}</span>` : ''}</div>`;
    }

    async function loadAlertSilences() {
      const box = document.getElementById('sil-list');
      if (!box) return;
      alSilConfirmId = null;
      box.innerHTML = '<div class="sp" style="margin:14px auto"></div>';
      let data = null, detail = '';
      try {
        const r = await fetch(`${A}/alerts/admin/silences`, { headers: h() });
        const j = await r.json().catch(() => ({}));
        if (r.ok && j && j.status === 'success') data = j;
        else detail = (j && j.detail) || `HTTP ${r.status}`;
      } catch (e) { detail = e.message || 'network error'; }
      if (!data) { box.innerHTML = alUnavail('al_sil_unavail', detail); return; }
      alSilData = data;
      alRenderSilences();
    }

    function alSilChips(matchers) {
      return (matchers || []).map(m =>
        `<span title="${escAttr(m.value)}${m.isRegex ? ' (regex)' : ''}" style="background:var(--surface);border:1px solid var(--border-s);border-radius:999px;padding:2px 8px;font-size:9px;color:var(--text-secondary);white-space:nowrap">${esc(`${m.name}=${m.value}`)}${m.isRegex ? '<span style="color:var(--text-ter)">~</span>' : ''}</span>`
      ).join(' ');
    }

    function alRenderSilences() {
      const box = document.getElementById('sil-list');
      if (!box) return;
      const list = (alSilData && alSilData.silences) || [];
      if (!list.length) {
        box.innerHTML = `<div style="background:var(--panel);border:1px solid var(--border);border-radius:var(--radius-sm);padding:12px 14px;font-size:12px;color:var(--text-ter)">${esc(__t('al_sil_none'))}</div>`;
        return;
      }
      const head = ['al_th_scope', 'al_th_owner', 'al_th_expires', 'al_th_comment']
        .map(k => `<span style="font-size:9px;color:var(--text-ter);text-transform:uppercase;letter-spacing:.5px;font-weight:600">${esc(__t(k))}</span>`).join('');
      box.innerHTML = `<div style="display:flex;gap:12px;padding:0 4px 6px">${head}<span></span></div>` + list.map(s => {
        const ends = s.endsAt ? new Date(s.endsAt).toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit' }) : '—';
        const confirmRow = alSilConfirmId === s.id
          ? `<div id="sil-confirm" style="margin-top:8px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:11px;color:var(--text-secondary);background:var(--bg);border:1px solid var(--border-s);border-radius:var(--radius-sm);padding:8px 10px">
              <span>${esc(__t('al_sil_delete_confirm'))}</span>
              <button type="button" class="btn btn-p btn-sm" onclick="alSilCancel('${escAttr(s.id)}',true)" style="padding:4px 10px;font-size:10px">${esc(__t('al_sil_yes'))}</button>
              <button type="button" class="btn btn-o btn-sm" onclick="alSilCancel(false)" style="padding:4px 10px;font-size:10px">${esc(__t('al_sil_no'))}</button>
            </div>`
          : '';
        return `<div style="background:var(--panel);border:1px solid var(--border);border-radius:var(--radius-sm);padding:10px 14px">
          <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
            <div style="flex:2 1 260px;min-width:0;display:flex;gap:6px;flex-wrap:nowrap;overflow:hidden">${alSilChips(s.matchers) || '<span style="font-size:11px;color:var(--text-ter)">—</span>'}</div>
            <div style="flex:1 1 110px;font-size:11px;color:var(--text-secondary)">${esc(s.createdBy || '—')}</div>
            <div style="flex:1 1 140px;font-size:11px;color:var(--text-secondary)">${esc(ends)} ${s.expires_in > 0 ? `<span style="color:var(--text-ter)">· ${esc(alDur(s.expires_in))}</span>` : ''}</div>
            <div style="flex:2 1 180px;font-size:11px;color:var(--text-ter);white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${escAttr(s.comment || '')}">${esc(s.comment || '—')}</div>
            <button type="button" onclick="alSilCancel('${escAttr(s.id)}')" aria-label="${escAttr(__t('al_sil_delete_confirm'))}" title="${escAttr(__t('al_sil_delete_confirm'))}"
              style="background:none;border:1px solid var(--border-s);border-radius:6px;color:var(--text-ter);cursor:pointer;padding:3px 8px;font-size:10px;line-height:1"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
          </div>${confirmRow}</div>`;
      }).join('');
    }

    function togSilForm(force) {
      const f = document.getElementById('sil-form');
      if (!f) return;
      const open = force === undefined ? f.style.display === 'none' : !!force;
      f.style.display = open ? 'block' : 'none';
      if (open) {
        const mrows = document.getElementById('sil-mrows');
        if (mrows && !mrows.children.length) silAddMatcher();
        alSilFormMsg('', '');
      }
    }

    function silAddMatcher() {
      const box = document.getElementById('sil-mrows');
      if (!box) return;
      const row = document.createElement('div');
      row.style.cssText = 'display:flex;gap:6px;align-items:center';
      row.innerHTML = `<input class="sil-m-name" type="text" placeholder="${escAttr(__t('al_sil_matcher_name_ph'))}" style="flex:0 0 130px;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:5px 8px;border-radius:var(--radius-sm);font-size:11px;box-sizing:border-box">
        <input class="sil-m-value" type="text" placeholder="${escAttr(__t('al_sil_matcher_value_ph'))}" style="flex:1;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:5px 8px;border-radius:var(--radius-sm);font-size:11px;box-sizing:border-box">
        <label style="font-size:10px;color:var(--text-ter);display:flex;gap:4px;align-items:center;white-space:nowrap"><input class="sil-m-re" type="checkbox">${esc(__t('al_sil_regex'))}</label>
        <button type="button" onclick="this.parentElement.remove()" aria-label="Quitar condición" style="background:none;border:1px solid var(--border-s);border-radius:6px;color:var(--text-ter);cursor:pointer;padding:3px 8px;font-size:10px;line-height:1"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>`;
      box.appendChild(row);
    }

    function alSilFormMsg(text, kind) {
      const box = document.getElementById('sil-form-msg');
      if (!box) return;
      box.style.display = text ? 'block' : 'none';
      box.textContent = text;
      // gris para "no verificable" (503), naranja para error corregible (422)
      box.style.border = text ? `1px solid ${kind === 'warn' ? 'var(--orange)' : 'var(--border)'}` : 'none';
      box.style.color = kind === 'warn' ? 'var(--orange)' : 'var(--text-ter)';
      box.style.background = kind === 'warn' ? 'var(--yellow-bg)' : 'var(--panel)';
    }

    async function alSubmitSilence() {
      const payload = {};
      const dev = (document.getElementById('sil-dev')?.value || '').trim();
      const grp = (document.getElementById('sil-group')?.value || '').trim();
      const dur = document.getElementById('sil-dur')?.value;
      const comment = (document.getElementById('sil-comment')?.value || '').trim();
      const matchers = [...document.querySelectorAll('#sil-mrows > div')]
        .map(r => ({ name: r.querySelector('.sil-m-name')?.value.trim(), value: r.querySelector('.sil-m-value')?.value.trim(), isRegex: !!r.querySelector('.sil-m-re')?.checked }))
        .filter(m => m.name && m.value);
      if (dev) payload.device_id = dev;
      if (grp) payload.group = grp;
      if (matchers.length) payload.matchers = matchers;
      if (!payload.device_id && !payload.group && !payload.matchers.length) { silAddMatcher(); alSilFormMsg(__t('al_sil_invalid_scope'), 'warn'); return; }
      payload.duration = dur;
      if (comment) payload.comment = comment;

      let j = {};
      try {
        const r = await fetch(`${A}/alerts/admin/silences`, { method: 'POST', headers: h(), body: JSON.stringify(payload) });
        j = await r.json().catch(() => ({}));
        if (r.ok && j && j.status === 'created') {
          togSilForm(false);
          toast(`${__t('al_sil_created')}: ${j.id}`, 's', 4500);
          loadAlertSilences();
          return;
        }
        const unable = r.status === 503;
        alSilFormMsg((unable ? __t('al_sil_create_unavail') : (j.detail || `${__t('al_sil_err_generic')} (HTTP ${r.status})`)) + (unable && j.detail ? ` — ${j.detail}` : ''), unable ? '' : 'warn');
        if (r.status === 401 || r.status === 403) toast(j.detail || __t('al_sil_err_generic'), 'e');
      } catch (e) { alSilFormMsg(`${__t('al_sil_err_generic')} — ${e.message || 'network error'}`, ''); }
    }

    async function alSilCancel(id, yes) {
      if (!id) { alSilConfirmId = null; alRenderSilences(); return; }
      if (!yes) { alSilConfirmId = id; alRenderSilences(); return; }
      try {
        const r = await fetch(`${A}/alerts/admin/silences/${encodeURIComponent(id)}`, { method: 'DELETE', headers: h() });
        const j = await r.json().catch(() => ({}));
        if (r.ok && j && j.status === 'deleted') {
          toast(`${__t('al_sil_deleted')}: ${j.id}`, 's', 4000);
          alSilConfirmId = null;
          loadAlertSilences();
        } else if (r.status === 404) {
          toast(__t('al_sil_404'), 'e', 4500);
          loadAlertSilences();
        } else {
          toast(j.detail || `${__t('al_sil_err_generic')} (HTTP ${r.status})`, 'e', 4500);
          alSilConfirmId = null;
          alRenderSilences();
        }
      } catch (e) { toast(`${__t('al_sil_err_generic')} — ${e.message || 'network error'}`, 'e', 4500); }
    }

    async function loadAlertRules() {
      const box = document.getElementById('rules-list');
      if (!box) return;
      alRulesOpen.clear();
      box.innerHTML = '<div class="sp" style="margin:14px auto"></div>';
      let data = null, detail = '';
      try {
        const r = await fetch(`${A}/alerts/admin/rules`, { headers: h() });
        const j = await r.json().catch(() => ({}));
        if (r.ok && j && j.status === 'success') data = j;
        else detail = (j && j.detail) || `HTTP ${r.status}`;
      } catch (e) { detail = e.message || 'network error'; }
      if (!data) { box.innerHTML = alUnavail('al_rules_unavail', detail); return; }
      alRulesData = data;
      alRenderRules();
    }

    function alRenderRules() {
      const box = document.getElementById('rules-list');
      if (!box || !alRulesData) return;
      const rules = alRulesData.rules || [];
      if (!rules.length) {
        box.innerHTML = `<div style="background:var(--panel);border:1px solid var(--border);border-radius:var(--radius-sm);padding:12px 14px;font-size:12px;color:var(--text-ter)">${esc(__t('al_rules_none'))}</div>`;
        return;
      }
      const sevMap = { critical: { bg: 'var(--red-bg)', color: 'var(--red)', label: 'CRITICAL' }, warning: { bg: 'var(--yellow-bg)', color: 'var(--yellow)', label: 'WARNING' }, info: { bg: 'var(--blue-bg)', color: 'var(--accent)', label: 'INFO' } };
      const labelsOf = r => {
        const entries = Object.entries(r.labels || {}).filter(([k]) => k !== 'severity');
        if (!entries.length) return `<span style="color:var(--text-ter)">—</span>`;
        const shown = entries.slice(0, 3);
        return shown.map(([k, v]) => `<span style="background:var(--surface);border:1px solid var(--border-s);border-radius:999px;padding:2px 7px;font-size:9px;color:var(--text-ter);white-space:nowrap">${esc(`${k}=${v}`)}</span>`).join(' ')
          + (entries.length > 3 ? `<span style="font-size:9px;color:var(--text-ter)">+${entries.length - 3}</span>` : '');
      };
      const forOf = r => { const s = Number(r.for) || 0; return s < 60 ? `${s}s` : alDur(s); };
      const head = ['al_th_name', 'al_th_expr', 'al_th_for', 'al_th_sev', 'al_th_labels']
        .map(k => `<span style="font-size:9px;color:var(--text-ter);text-transform:uppercase;letter-spacing:.5px;font-weight:600">${esc(__t(k))}</span>`).join('');
      box.innerHTML = `<div style="display:flex;gap:12px;padding:0 4px 6px">${head}</div>` + rules.map((r, i) => {
        const sev = sevMap[r.severity], open = alRulesOpen.has(i);
        const sevBadge = sev
          ? `<span class="badge" style="background:${sev.color};color:#fff;font-size:8px;padding:2px 6px">${sev.label}</span>`
          : `<span style="font-size:9px;color:var(--text-ter)">—</span>`;
        const expr = String(r.expr || '').trim();
        const expanded = open || expr.length <= 120;
        return `<div style="background:var(--panel);border:1px solid var(--border);border-radius:var(--radius-sm);padding:10px 14px">
          <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
            <div style="flex:1 1 130px;font-size:12px;font-weight:600;color:var(--text)">${esc(r.name || '—')}${r.group ? `<span title="${escAttr(r.group)}" style="font-size:9px;color:var(--text-ter);font-weight:400"> · ${esc(r.group)}</span>` : ''}</div>
            <div style="flex:3 1 260px;min-width:0">${expanded
              ? `<pre style="margin:0;font-family:var(--mono);font-size:10px;color:var(--text-secondary);white-space:pre-wrap;word-break:break-word;cursor:${expr.length > 120 ? 'pointer' : 'default'}" onclick="alToggleExpr(${i})" title="${escAttr(__t('al_expand'))}">${esc(expr)}</pre>`
              : `<code style="font-family:var(--mono);font-size:10px;color:var(--text-secondary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:block;cursor:pointer" onclick="alToggleExpr(${i})" title="${escAttr(__t('al_expand'))}">${esc(expr)}</code>`}</div>
            <div style="flex:0 0 auto;font-size:10px;color:var(--text-ter)">${forOf(r)}</div>
            <div style="flex:0 0 auto;min-width:64px">${sevBadge}</div>
            <div style="flex:2 1 160px;display:flex;flex-wrap:nowrap;gap:5px;overflow:hidden">${labelsOf(r)}</div>
          </div></div>`;
      }).join('');
    }

    function alToggleExpr(i) {
      if (!alRulesOpen.has(i)) alRulesOpen.add(i); else alRulesOpen.delete(i);
      alRenderRules();
    }
