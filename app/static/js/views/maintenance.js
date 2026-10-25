/**
 * NetPulse NOC — vista Ventanas de mantenimiento (views/maintenance.js).
 *
 * Extraída del <script> inline de app/static/dashboard.html (Tarjeta 10D-B,
 * lote 2) sin cambio de comportamiento: loadMaint, showMaintForm,
 * createMaint y delMaint, mismas líneas en el mismo orden relativo.
 *
 * Depende (en tiempo de llamada, no de carga): core.js (A, h, toast),
 * inline de dashboard.html (esc) y el array compartido `devices` (estado
 * del dashboard, que sigue en el inline): loadMaint lo usa para rellenar
 * el select #maint-dev. Handlers que las resuelven por window:
 * swView('maintenance') → loadMaint(); onclick del markup de
 * #v-maintenance → showMaintForm() / createMaint(); onclick generados por
 * loadMaint() → delMaint(...). Scripts clásicos sin defer/async ya
 * cargaron al momento del click (orden estático). Top-level sin ejecución.
 */

    // ── MAINTENANCE WINDOWS ──────────────────────────────────
    async function loadMaint() {
      try {
        const r = await fetch(`${A}/ops/maintenance`, { headers: h() });
        if (!r.ok) return;
        const list = await r.json();
        const devSel = document.getElementById('maint-dev');
        if (devSel && devices.length) {
          devSel.innerHTML = devices.map(d => `<option value="${d.id}">${d.id}</option>`).join('');
        }
        document.getElementById('maint-list').innerHTML = list.length ?
          `<table><tr><th>Dispositivo</th><th>Inicio</th><th>Fin</th><th>Razón</th><th></th></tr>` +
          list.map(w => `<tr><td>${esc(w.device_id)}</td><td>${esc(w.start)}</td><td>${esc(w.end)}</td><td>${esc(w.reason)}</td>
            <td><button class="btn btn-d btn-xs" onclick="delMaint('${w.id}')" aria-label="Eliminar"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/></svg></button></td></tr>`).join('') +
          `</table>` : '<div class="empty">Sin ventanas de mantenimiento</div>';
      } catch (e) { }
    }
    function showMaintForm() { document.getElementById('maint-form').style.display = 'block' }
    async function createMaint() {
      const dev = document.getElementById('maint-dev').value;
      const start = document.getElementById('maint-start').value;
      const end = document.getElementById('maint-end').value;
      const reason = document.getElementById('maint-reason').value;
      if (!dev || !start || !end) { toast('Dispositivo, inicio y fin requeridos', 'e'); return }
      try {
        const r = await fetch(`${A}/ops/maintenance`, {
          method: 'POST', headers: h(), body: JSON.stringify({
            device_id: dev, start: new Date(start).toISOString(), end: new Date(end).toISOString(), reason
          })
        });
        if (!r.ok) { const d = await r.json(); throw new Error(d.detail || 'Error') }
        toast('<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg> Ventana creada', 's'); document.getElementById('maint-form').style.display = 'none'; loadMaint();
      } catch (e) { toast('<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> ' + e.message, 'e') }
    }
    async function delMaint(id) {
      try {
        await fetch(`${A}/ops/maintenance/${id}`, { method: 'DELETE', headers: h() });
        toast('Ventana eliminada', 's'); loadMaint();
      } catch (e) { }
    }
