/**
 * NetPulse NOC — vista Webhooks (views/webhooks.js).
 *
 * Extraída del <script> inline de app/static/dashboard.html (Tarjeta 10D-A,
 * lote 1) sin cambio de comportamiento: loadWebhooks, addWebhook,
 * deleteWebhook, mismas líneas en el mismo orden relativo.
 *
 * Depende (en tiempo de llamada, no de carga): core.js (A, h, toast) e
 * inline de dashboard.html (esc, currentLang). Handlers que las resuelven
 * por window: swView('webhooks') → loadWebhooks(); onclick generados por
 * loadWebhooks() → addWebhook(), deleteWebhook(). Scripts clásicos sin
 * defer/async ya cargaron al momento del click (orden estático).
 * Top-level sin ejecución.
 */

// ── WEBHOOKS ──
    async function loadWebhooks() {
      const el = document.getElementById('webhooks-content'); if (!el || !t) return;
      try {
        const r = await fetch(`${A}/notifications/webhooks`, { headers: h() });
        const whs = await r.json();
        const list = Array.isArray(whs) ? whs : (whs.webhooks || whs.results || []);
        el.innerHTML = `<div class="sc" style="margin-bottom:12px"><div class="sl">Nuevo webhook</div>
      <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">
        <input id="wh-url" placeholder="https://hooks.slack.com/..." style="flex:2;min-width:200px">
        <select id="wh-event" style="flex:1">
          <option value="device_down">Device Down</option>
          <option value="device_up">Device Up</option>
          <option value="backup_complete">Backup Complete</option>
          <option value="all">Todos los eventos</option>
        </select>
        <button class="btn btn-p btn-sm" onclick="addWebhook()"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 5v14M5 12h14"/></svg> Agregar</button>
      </div>
    </div>
    ${list.length ? `<div style="border:1px solid var(--border);border-radius:6px"><table><tr><th>URL</th><th>Eventos</th><th>Acción</th></tr>
      ${list.map(w => `<tr><td style="max-width:250px;overflow:hidden;text-overflow:ellipsis;font-size:10px">${esc(w.url || '?')}</td>
      <td>${(w.events || ['all']).join(', ')}</td>
      <td><button class="btn btn-o btn-xs" onclick="deleteWebhook('${w.id || w.url}')" aria-label="${currentLang === 'en' ? 'Delete webhook' : 'Eliminar webhook'}"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button></td></tr>`).join('')}</table></div>` : '<div class="empty"><span class="icon"><svg class="ic" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false" style="opacity:0.4;color:var(--text-ter)"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg></span><br>Sin webhooks configurados</div>'}`
      } catch (e) { el.innerHTML = `<div class="empty"><span class="icon"><svg class="ic" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false" style="opacity:0.4;color:var(--text-ter)"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg></span><br>Cargando...</div>` }
    }
    async function addWebhook() {
      const url = document.getElementById('wh-url')?.value; const event = document.getElementById('wh-event')?.value;
      if (!url) return;
      try { await fetch(`${A}/notifications/webhooks`, { method: 'POST', headers: h(), body: JSON.stringify({ url, events: event === 'all' ? ['device_down', 'device_up', 'backup_complete'] : [event] }) }); toast('<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg> Webhook agregado', 's'); loadWebhooks() }
      catch (e) { toast('Error al agregar webhook', 'error') }
    }
    async function deleteWebhook(id) { try { await fetch(`${A}/notifications/webhooks/${id}`, { method: 'DELETE', headers: h() }); toast('<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> Webhook eliminado', 'info'); loadWebhooks() } catch (e) { } }
