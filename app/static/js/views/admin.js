/**
 * NetPulse NOC — vista Admin usuarios + scopes RBAC (views/admin.js).
 *
 * Extraída del <script> inline de app/static/dashboard.html (Tarjeta 10D-C,
 * lote 3) sin cambio de comportamiento: loadUsers, createUser, delUser y los
 * helpers de scopes userScope* (la tabla los invoca vía onclick generado en
 * loadUsers), más la caché `_r4Groups` que solo llena/lee `userScopePanel`.
 * Tarjeta 12D añade revokeUserSessions (botón por usuario, POST
 * revoke-sessions, confirmación y detail literal en 403/404).
 * Líneas en el mismo orden relativo entre sí (el orden original era:
 * loadUsers…delUser < _r4Groups < userScope*).
 *
 * Lo que NO viajó (verificado por grep, regla 2):
 * - `__isAdmin` / `npGray` — bloque de Groups del inline; también los usan
 *   Compliance, NetBox, Reports, Sites y Retention: quedan.
 * - `t`, `currentUser`, `h`, `A`, `toast`, `__t`, `currentLang`, `esc`,
 *   `escAttr`, `_detText` — core / compartidos; `userScopeSave`,
 *   `userScopeClearExec` leen `_detText` (bloque Reports, inline) en tiempo
 *   de llamada.
 * - El `confirm(...)` de `delUser` es el preexistente en el onclick
 *   generado; no se añadió ninguno nuevo.
 *
 * Depende (en tiempo de llamada): core.js (A, h, __t, toast, t,
 * currentUser), inline (__isAdmin, esc, escAttr, _detText). Handlers por
 * window: botón Refresh de #v-admin y swView('admin') → loadUsers;
 * createUser/delUser/userScope* resueltos desde el HTML que loadUsers y
 * userScopePanel generan. Script clásico sin defer/async; `let _r4Groups`
 * es binding del entorno léxico global (no window).
 */

    // ── ADMIN USERS ──
    async function loadUsers() {
      const el = document.getElementById('admin-content'); if (!el || !t) return;
      try {
        const r = await fetch(`${A}/admin/users`, { headers: h() });
        if (!r.ok) { el.innerHTML = '<div class="empty"><span class="icon"><svg class="ic" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false" style="opacity:0.4;color:var(--text-ter)"><path d="M20 7h-9"/><path d="M14 17H5"/><circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/></svg></span><br>Sin permisos de administrador</div>'; return }
        const users = await r.json();
        const roleColors = { admin: '#ef4444', operator: '#f59e0b', viewer: '#3b82f6' };
        el.innerHTML = `<div class="sc" style="margin-bottom:12px"><div class="sl" style="display:flex;align-items:center;gap:8px"><span style="font-size:18px"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="7" r="4"/><path d="M5 21v-2a7 7 0 0 1 14 0v2"/></svg></span> Usuarios del sistema</div></div>
    <div style="border:1px solid var(--border);border-radius:8px;margin-bottom:16px">
      ${(Array.isArray(users) ? users : users.users || []).map(u => `<div class="user-item">
        <div class="uic" style="background:${roleColors[u.role] || 'var(--accent-l)'};color:#fff">${(u.name || u.username)[0].toUpperCase()}</div>
        <div style="flex:1"><div class="ui-n">${esc(u.name || u.username)} <span style="font-weight:400;color:var(--text-ter);font-size:11px">@${u.username}</span></div>
        <div class="ui-r">${u.role}</div></div>
        <span class="badge" style="background:${roleColors[u.role] || 'var(--accent-l)'};color:#fff">${u.role}</span>
        ${userScopeBadge(u)}
        ${__isAdmin() ? `<button class="btn btn-o btn-xs" onclick="userScopePanel('${escAttr(u.username)}')" aria-label="Configurar permisos"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg></button>` : ''}
        ${__isAdmin() ? `<button class="btn btn-o btn-xs" onclick="revokeUserSessions('${escAttr(u.username)}')" aria-label="${escAttr(__t('adm_revoke'))}" title="${escAttr(__t('adm_revoke'))}"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg></button>` : ''}
        <button class="btn btn-o btn-xs" onclick="if(confirm('Eliminar ${u.username}?'))delUser('${u.username}')" aria-label="${currentLang === 'en' ? 'Delete user' : 'Eliminar usuario'}"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
        <div id="sc-panel-${escAttr(u.username)}" style="display:none;flex-basis:100%"></div>
      </div>`).join('')}
    </div>
    <div class="sc">
      <div class="sl" style="display:flex;align-items:center;gap:8px"><span style="font-size:18px"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 5v14M5 12h14"/></svg></span> Crear nuevo usuario</div>
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr auto;gap:8px;margin-top:8px">
        <input id="new-u" placeholder="Usuario" style="font-size:12px">
        <input id="new-p" type="password" placeholder="Contraseña" style="font-size:12px">
        <input id="new-n" placeholder="Nombre" style="font-size:12px">
        <select id="new-r" style="font-size:12px;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:7px 10px;border-radius:6px">
          <option value="viewer">Viewer</option><option value="operator">Operator</option><option value="admin">Admin</option>
        </select>
        <button class="btn btn-p btn-sm" onclick="createUser()" aria-label="Agregar"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 5v14M5 12h14"/></svg></button>
      </div>
    </div>`
      } catch (e) { el.innerHTML = '<div class="empty"><span class="icon"><svg class="ic" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false" style="opacity:0.4;color:var(--text-ter)"><path d="M20 7h-9"/><path d="M14 17H5"/><circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/></svg></span><br>Sin permisos de administrador</div>' }
    }
    async function createUser() {
      const u = document.getElementById('new-u')?.value, p = document.getElementById('new-p')?.value;
      const n = document.getElementById('new-n')?.value, r = document.getElementById('new-r')?.value;
      if (!u || !p) return;
      try { await fetch(`${A}/admin/users`, { method: 'POST', headers: h(), body: JSON.stringify({ username: u, password: p, name: n || u, role: r || 'viewer' }) }); toast(`<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg> Usuario ${u} creado`, 's'); loadUsers() }
      catch (e) { toast('Error al crear usuario', 'error') }
    }
    async function delUser(u) {
      try { await fetch(`${A}/admin/users/${u}`, { method: 'DELETE', headers: h() }); toast(`<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> ${u} eliminado`, 'info'); loadUsers() }
      catch (e) { toast('No puedes eliminarte a ti mismo', 'error') }
    }

    // Cierra todas las sesiones del usuario incrementando token_version.
    // Confirmación explícita; 403/404 muestran el detail literal del backend.
    async function revokeUserSessions(username) {
      if (!confirm(__t('adm_revoke_confirm').replace('{u}', username))) return;
      try {
        const r = await fetch(`${A}/admin/users/${encodeURIComponent(username)}/revoke-sessions`, { method: 'POST', headers: h() });
        let b = null; try { b = await r.json(); } catch (e) { }
        if (!r.ok) {
          toast(`${__t('adm_revoke_fail')} ${esc(_detText(b, 'HTTP ' + r.status))}`, 'error');
          return;
        }
        toast(`${__t('adm_revoke_ok').replace('{u}', esc(username)).replace('{v}', String(b && b.token_version != null ? b.token_version : '?'))}`, 's');
        loadUsers();
      } catch (e) {
        toast(`${__t('np_unverified')} · ${esc(e.message)}`, 'error');
      }
    }

    let _r4Groups = null; // cache de /api/groups para el panel de scopes

    // ── SCOPES RBAC (contrato rbac-site.md; PUT /api/admin/users/{username}) ──
    function userScopeBadge(u) {
      const g = u.scope_group, s = u.scope_sites;
      if (g == null && s == null) return `<span class="badge" style="background:var(--surface);color:var(--text-secondary)">${__t('scp_all')}</span>`;
      if (g == null && Array.isArray(s) && !s.length) return `<span class="badge" style="background:rgba(239,68,68,.15);color:var(--red)">${__t('scp_none')}</span>`;
      const parts = [];
      if (g) parts.push(esc(g));
      if (Array.isArray(s)) s.forEach(x => { if (!parts.some(xx => xx.toLowerCase() === esc(x).toLowerCase())) parts.push(esc(x)); });
      return `<span class="badge" style="background:rgba(59,130,246,.15);color:var(--accent)">${parts.join(' + ')}</span>`;
    }

    async function userScopePanel(username) {
      const box = document.getElementById(`sc-panel-${username}`); if (!box) return;
      if (box.style.display !== 'none') { box.style.display = 'none'; return; }
      let users = null;
      try {
        const r = await fetch(`${A}/admin/users`, { headers: h() });
        if (r.ok) { const j = await r.json(); users = Array.isArray(j) ? j : (j.users || []); }
      } catch (e) { }
      const u = (users || []).find(x => x.username === username) || {};
      if (!_r4Groups) {
        try { const rg = await fetch(`${A}/groups`, { headers: h(), signal: AbortSignal.timeout(10000) }); if (rg.ok) _r4Groups = await rg.json(); } catch (e) { }
      }
      const glist = Array.isArray(_r4Groups) ? _r4Groups.map(g => g.name).filter(Boolean) : [];
      const opt = (v, cur) => `<option value="${escAttr(v)}" ${cur === v ? 'selected' : ''}>${esc(v)}</option>`;
      box.style.display = 'block';
      box.innerHTML = `
        <div style="background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:10px;margin-top:6px">
          <b style="font-size:12px">${__t('scp_title')}</b> <span style="font-size:11px;color:var(--text-ter)">@${esc(username)}</span>
          ${glist.length ? '' : `<div style="font-size:11px;color:var(--text-ter);margin-top:4px">${__t('scp_groups_unverified')}</div>`}
          <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">
            <select id="sc-g-${escAttr(username)}" style="flex:1;min-width:140px;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:7px 10px;border-radius:6px;font-size:12px">
              <option value="">— ${__t('scp_no_main')} —</option>
              ${glist.map(g => opt(g, u.scope_group || '')).join('')}
            </select>
          </div>
          ${glist.length ? `<div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap">
            ${glist.map(g => `<label style="font-size:11px;color:var(--text-secondary)"><input type="checkbox" class="sc-s-${escAttr(username)}" value="${escAttr(g)}" ${(u.scope_sites || []).some(x => String(x).toLowerCase() === g.toLowerCase()) ? 'checked' : ''}> ${esc(g)}</label>`).join('')}
          </div>` : ''}
          <div style="font-size:11px;color:var(--text-ter);margin-top:6px">${__t('scp_note')}</div>
          <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">
            <button class="btn btn-p btn-sm" onclick="userScopeSave('${escAttr(username)}')">${__t('scp_save')}</button>
            <button class="btn btn-o btn-sm" onclick="userScopeClear('${escAttr(username)}')">${__t('scp_clear')}</button>
            <button class="btn btn-o btn-sm" onclick="userScopePanel('${escAttr(username)}')">${__t('dw_close')}</button>
          </div>
          <div id="sc-out-${escAttr(username)}" style="margin-top:8px;font-size:11px"></div>
        </div>`;
    }

    async function userScopeSave(username) {
      const out = document.getElementById(`sc-out-${username}`); if (!out) return;
      const g = document.getElementById(`sc-g-${username}`)?.value;
      const sites = Array.from(document.querySelectorAll(`.sc-s-${CSS.escape(username)}:checked`)).map(x => x.value);
      const body = { scope_group: g || null, scope_sites: sites.length ? sites : null };
      try {
        const r = await fetch(`${A}/admin/users/${encodeURIComponent(username)}`, { method: 'PUT', headers: h(), body: JSON.stringify(body) });
        let b = null; try { b = await r.json(); } catch (e) { }
        if (!r.ok) { out.innerHTML = `<div style="color:var(--orange)">${esc(_detText(b, 'HTTP ' + r.status))}</div>`; return; }
        toast(`${__t('scp_saved')}. ${__t('scp_relogin')}`, 's');
        loadUsers(); // refresco completo: el badge refleja el 200 real del backend
      } catch (e) {
        out.innerHTML = `<div style="color:var(--orange)">${__t('np_unverified')} · ${esc(e.message)}</div>`;
      }
    }

    async function userScopeClearConfirm(username) {
      const out = document.getElementById(`sc-out-${username}`); if (!out) return;
      out.innerHTML = `<div class="bt" style="margin-bottom:8px;border-left-color:var(--orange)">${__t('scp_clear_confirm')}</div>
        <div style="display:flex;gap:8px"><button class="btn btn-p btn-sm" onclick="userScopeClearExec('${escAttr(username)}')">${__t('al_sil_yes')}</button>
        <button class="btn btn-o btn-sm" onclick="userScopePanelClose('${escAttr(username)}')">${__t('al_sil_no')}</button></div>`;
    }
    function userScopePanelClose(username) {
      const box = document.getElementById(`sc-panel-${username}`);
      const out = document.getElementById(`sc-out-${username}`);
      if (out) out.innerHTML = '';
      if (box) box.style.display = 'none';
    }
    async function userScopeClear(username) { userScopeClearConfirm(username); }
    async function userScopeClearExec(username) {
      const out = document.getElementById(`sc-out-${username}`); if (!out) return;
      try {
        const r = await fetch(`${A}/admin/users/${encodeURIComponent(username)}`, { method: 'PUT', headers: h(), body: JSON.stringify({ scope_group: null, scope_sites: null }) });
        let b = null; try { b = await r.json(); } catch (e) { }
        if (!r.ok) { out.innerHTML = `<div style="color:var(--orange)">${esc(_detText(b, 'HTTP ' + r.status))}</div>`; return; }
        toast(`${__t('scp_cleared')}`, 's');
        loadUsers();
      } catch (e) {
        out.innerHTML = `<div style="color:var(--orange)">${__t('np_unverified')} · ${esc(e.message)}</div>`;
      }
    }
