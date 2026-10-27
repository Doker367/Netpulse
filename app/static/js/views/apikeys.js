/**
 * NetPulse NMS — vista API Keys de colector (views/apikeys.js).
 *
 * Extraída del <script> inline de app/static/dashboard.html (Tarjeta 10D-B,
 * lote 2) sin cambio de comportamiento: showKeyForm, loadKeys, createKey
 * y revokeKey, mismas líneas en el mismo orden relativo.
 *
 * Depende (en tiempo de llamada, no de carga): core.js (A, h, toast) e
 * inline de dashboard.html (esc). Handlers que las resuelven por window:
 * swView('apikeys') → loadKeys(); onclick del markup de #v-apikeys →
 * showKeyForm() / createKey(); onclick generados por loadKeys() →
 * revokeKey(...). Scripts clásicos sin defer/async ya cargaron al momento
 * del click (orden estático). Top-level sin ejecución.
 */

    // ── API KEYS ──────────────────────────────────────────────
    function showKeyForm() { document.getElementById('key-form').style.display = 'block' }
    async function loadKeys() {
      try {
        const r = await fetch(`${A}/security/api-keys`, { headers: h() });
        if (!r.ok) return;
        const list = await r.json();
        document.getElementById('key-list').innerHTML = list.length ?
          `<table><tr><th>Nombre</th><th>Rol</th><th>Creada</th><th></th></tr>` +
          list.map(k => `<tr><td>${esc(k.name)}</td><td>${esc(k.role)}</td><td>${esc((k.created_at || '').substring(0, 16))}</td>
            <td><button class="btn btn-d btn-xs" onclick="revokeKey('${k.id}')" aria-label="Eliminar"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/></svg></button></td></tr>`).join('') +
          `</table>` : '<div class="empty">Sin API keys</div>';
      } catch (e) { }
    }
    async function createKey() {
      const name = document.getElementById('key-name').value;
      const role = document.getElementById('key-role').value;
      if (!name) { toast('Nombre requerido', 'e'); return }
      try {
        const r = await fetch(`${A}/security/api-keys`, { method: 'POST', headers: h(), body: JSON.stringify({ name, role }) });
        if (!r.ok) { const d = await r.json(); throw new Error(d.detail || 'Error') }
        const k = await r.json();
        const box = document.getElementById('key-created');
        box.style.display = 'block';
        box.innerHTML = `<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="8" cy="15" r="5"/><path d="m11.5 11.5 9-9M16 7l3 3M19 4l3 3"/></svg> Guarda esta key (solo se muestra una vez):<br><b>${esc(k.key)}</b>`;
        document.getElementById('key-form').style.display = 'none'; loadKeys();
      } catch (e) { toast('<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> ' + e.message, 'e') }
    }
    async function revokeKey(id) {
      try {
        await fetch(`${A}/security/api-keys/${id}`, { method: 'DELETE', headers: h() });
        toast('Key revocada', 's'); loadKeys();
      } catch (e) { }
    }
