/**
 * NetPulse NMS — Terminal SSH (vista #v-terminal, pestaña "Terminal"):
 * consola xterm.js multi-pestaña con WebSocket en modo JSON
 * (views/terminal.js).
 *
 * Extraída del <script> inline de app/static/dashboard.html (Tarjeta 10D-I,
 * lote i) SIN cambio de comportamiento: el bloque viaja contiguo y byte a
 * byte, en el mismo orden relativo que en el monolito
 * (constantes TERM_* < estado _term/_termSessions/_currentTermTab <
 * _termActive < switchTermTab < termPopulateDevices < termDevChanged <
 * _setTermStatus < _termRenderTabs < termActivateTab < termCloseTab <
 * _termCreateSession < _termBuildXterm < termOpenConsoleFor < termConnect <
 * _termManualRetry < _termOpenWs < _termSendResize < _termStartPing <
 * _termKillPings < _termStartBackoff < _termGaveUp < _termHandleError <
 * _termHandleClosed < _termBanner < _termHideBanner < _termRefreshUI <
 * termDisconnect < termClear < _termWatchResize < termToggleFullscreen <
 * _termCopySel < _termCopyFallback < termSearchRun < termSearchClose <
 * _termMarkLine < cmdOpenConsole).
 *
 * Contrato: docs/handoffs/terminal.md (modo legacy + `?fmt=json` con eventos
 * connected/output/error/closed, auth 4401, scope 4403, ping/pong e idle).
 * NADA de la lógica se modernizó: pestañas (máx. 4), scrollback/buffer por
 * pestaña, búsqueda local en buffer, copiar/pegar, heartbeat 25 s con margen
 * 10 s, backoff visible 1-2-4-8-15-15 s con 6 intentos, banner honesto por
 * código y `_termGaveUp`/`termDisconnect` se conservan tal cual.
 *
 * Estado (viaja con su `let`/`const`, sin renombrar):
 * - `TERM_TAB_MAX/RETRY_MAX/BACKOFF_MS/PING_MS/PONG_TIMEOUT_MS/BUF_MAX`,
 *   `_TERM_TONES` (constantes de este archivo).
 * - `_term`, `_fitAddon`, `_termWs` — alias de la PESTAÑA ACTIVA
 *   (compatibilidad). `swView` (inline, lote k) lee `_currentTermTab` y
 *   `_fitAddon` en tiempo de llamada: son bindings `let` del entorno léxico
 *   global compartido entre scripts clásicos, igual que `repState` (Lote D) o
 *   `_invGroupsCache` (Lote F).
 * - `_termSessions`, `_termActiveId`, `_termSeq`, `_currentTermTab`.
 * - `_sftpCurrentPath`, `_sftpDeviceId`, `_sftpBlocked`, `_sftpBusy`,
 *   `_sftpLastGoodPath` — declaradas en ESTE bloque en el monolito y usadas
 *   por las funciones de views/sftp.js (cargado después) en tiempo de llamada;
 *   se quedan aquí, in situ, para no inventar un tercer archivo de estado.
 *
 * Dependencias (todas en tiempo de llamada): core.js (`A`, `h`, `t`, `toast`,
 * `__t`), inline (`esc`, `escAttr`, `setIconText`, `_detText`, `devices`,
 * `swView`, `loadDev`), vendor global `Terminal` (xterm.js +
 * xterm-addon-fit.js en <head>), `window._termRO` / `window.ResizeObserver` /
 * `navigator.clipboard` / `window.URL`.
 *
 * Handlers por window (markup de #v-terminal y strings generados): `termConnect`,
 * `termDisconnect`, `termDevChanged`, `termClear`, `termToggleFullscreen`,
 * `termSearchRun`, `_termCopySel`, `switchTermTab`, `termActivateTab`,
 * `termCloseTab`, `termSearchClose`, `cmdOpenConsole` (botón de #v-commands y
 * el `onclick` que emite `populateCmdDevices`).
 *
 * Defectos preexistentes CONSERVADOS (no son del refactor): `logout` no cierra
 * los WebSocket ni el `ResizeObserver` (§7.15 del mapa) y el token viaja en la
 * query del WS. Script clásico sin defer/async, cargado tras core.js y las
 * vistas previas y ANTES de views/sftp.js y del inline residual; top-level
 * sin lógica: solo constantes, `let` de estado y declaraciones `function`.
 */

    // ── CONSOLA WEB (TERMINAL SSH JSON) & GESTOR SFTP ──
    // Contrato: docs/handoffs/terminal.md — fmt=json con eventos connected/output/error/closed,
    // ping/pong por la UI y backoff de reconexión. Una pestaña = un dispositivo = un xterm + un WS.
    const TERM_TAB_MAX = 4;              // consolas simultáneas (una por dispositivo)
    const TERM_RETRY_MAX = 6;            // reintentos automáticos de reconexión
    const TERM_BACKOFF_MS = [1000, 2000, 4000, 8000, 15000, 15000];   // 1s..8s, tope 15s
    const TERM_PING_MS = 25000;          // heartbeat: {"type":"ping"} cada 25 s
    const TERM_PONG_TIMEOUT_MS = 10000;  // sin pong en ~10 s → conexión muerta
    const TERM_BUF_MAX = 1500000;        // buffer local de texto para búsqueda (scrollback vivo)
    let _term = null, _fitAddon = null, _termWs = null;   // alias de la PESTAÑA ACTIVA (compatibilidad)
    let _termSessions = [];              // sesiones por pestaña: xterm, ws, buffer y estado propios
    let _termActiveId = null;
    let _termSeq = 0;
    let _currentTermTab = 'terminal';
    let _sftpCurrentPath = '/';
    let _sftpDeviceId = null;   // equipo del último listado (para limpiar estado al cambiar)
    let _sftpBlocked = false;   // 501: el equipo no ofrece SFTP → sin subir/descargar
    let _sftpBusy = false;      // una transferencia a la vez
    let _sftpLastGoodPath = '/';

    function _termActive() { return _termSessions.find(x => x.id === _termActiveId) || null; }

    function switchTermTab(tab) {
      _currentTermTab = tab;
      const tabTerm = document.getElementById('tab-btn-terminal');
      const tabSftp = document.getElementById('tab-btn-sftp');
      const paneTerm = document.getElementById('term-pane-terminal');
      const paneSftp = document.getElementById('term-pane-sftp');

      if (tab === 'terminal') {
        if (tabTerm) tabTerm.className = 'btn btn-sm btn-p';
        if (tabSftp) tabSftp.className = 'btn btn-sm btn-o';
        if (paneTerm) paneTerm.style.display = 'flex';
        if (paneSftp) paneSftp.style.display = 'none';
        requestAnimationFrame(() => {
          setTimeout(() => {
            const s = _termActive();
            if (s && s.fit) {
              try { s.fit.fit(); } catch (e) { }
              _termSendResize(s);
            }
          }, 60);
        });
      } else {
        if (tabTerm) tabTerm.className = 'btn btn-sm btn-o';
        if (tabSftp) tabSftp.className = 'btn btn-sm btn-p';
        if (paneTerm) paneTerm.style.display = 'none';
        if (paneSftp) paneSftp.style.display = 'flex';
        const curDev = document.getElementById('term-dev')?.value;
        if (curDev) {
          sftpLoadDir(_sftpCurrentPath || '/');
        }
      }
    }

    function termPopulateDevices() {
      const sel = document.getElementById('term-dev');
      if (!sel) return;
      const currentVal = sel.value;
      const opts = (devices || []).map(d =>
        `<option value="${esc(d.id)}">${esc(d.id)} · ${esc(d.ip || d.hostname || '')}:${d.port || 22}</option>`
      ).join('');
      sel.innerHTML = opts || '<option value="">Sin dispositivos</option>';
      if (currentVal && Array.from(sel.options).some(o => o.value === currentVal)) {
        sel.value = currentVal;
      }
      termDevChanged();
      _termRefreshUI();
    }

    function termDevChanged() {
      const id = document.getElementById('term-dev')?.value;
      if (_termActive()) { _termRefreshUI(); }
      else {
        const dev = (devices || []).find(d => d.id === id);
        const hostEl = document.getElementById('term-info-host');
        if (hostEl) {
          if (dev) {
            hostEl.textContent = `ssh://${dev.username || 'admin'}@${dev.ip || dev.hostname || dev.id}:${dev.port || 22}`;
          } else {
            hostEl.textContent = id ? `ssh://${id}` : 'ssh://desconectado';
          }
        }
      }
      if (_currentTermTab === 'sftp' && id) {
        sftpLoadDir(_sftpCurrentPath || '/');
      }
    }

    function _setTermStatus(state, text) {
      const badge = document.getElementById('term-status-badge');
      const textEl = document.getElementById('term-status-text');
      if (!badge || !textEl) return;
      textEl.textContent = text;
      if (state === 'connected') {
        badge.style.background = 'rgba(16,185,129,0.12)';
        badge.style.borderColor = 'rgba(16,185,129,0.3)';
        badge.style.color = 'var(--green)';
      } else if (state === 'connecting') {
        badge.style.background = 'rgba(245,158,11,0.12)';
        badge.style.borderColor = 'rgba(245,158,11,0.3)';
        badge.style.color = '#f59e0b';
      } else {
        badge.style.background = 'rgba(239,68,68,0.12)';
        badge.style.borderColor = 'rgba(239,68,68,0.3)';
        badge.style.color = 'var(--red)';
      }
    }

    // ── Pestañas multi-dispositivo ──────────────────────────────
    function _termRenderTabs() {
      const strip = document.getElementById('term-tabs');
      if (!strip) return;
      if (!_termSessions.length) { strip.innerHTML = ''; return; }
      strip.innerHTML = _termSessions.map(s => {
        const active = s.id === _termActiveId;
        const dot = s.status.state === 'connected' ? 'var(--green)' : (s.status.state === 'connecting' ? '#f59e0b' : 'var(--red)');
        const base = 'display:inline-flex;align-items:center;gap:7px;padding:6px 9px 8px;font-size:11px;font-weight:600;border-radius:6px 6px 0 0;cursor:pointer;white-space:nowrap;max-width:230px;border:1px solid transparent;border-bottom:none;';
        const st = active
          ? 'background:#0d0e12;color:var(--text);'
          : 'background:transparent;color:var(--text-secondary);';
        return `<div role="tab" tabindex="0" aria-selected="${active}" title="${esc(s.device)} · ${esc(s.status.text || '')}" id="term-tab-${s.id}" ${active ? '' : `onclick="termActivateTab(${s.id})"`} style="${base}${st}" onkeydown="if(event.key==='Enter')termActivateTab(${s.id})"><span style="width:7px;height:7px;border-radius:50%;background:${dot};flex-shrink:0"></span><span style="overflow:hidden;text-overflow:ellipsis">${esc(s.device)}</span><button type="button" title="${esc(__t('term_close_tab'))}" aria-label="${esc(__t('term_close_tab'))}" onclick="event.stopPropagation();termCloseTab(${s.id})" style="background:none;border:0;color:${active ? 'var(--text)' : 'var(--text-ter)'};cursor:pointer;font-size:13px;line-height:1;padding:0 2px">×</button></div>`;
      }).join('');
    }

    function termActivateTab(id) {
      const s = _termSessions.find(x => x.id === id);
      if (!s) return;
      _termActiveId = id;
      _termSessions.forEach(x => { if (x.pane) x.pane.style.display = (x.id === id) ? 'flex' : 'none'; });
      _term = s.term; _fitAddon = s.fit; _termWs = s.ws;   // alias de la pestaña activa
      termSearchClose();
      _termRenderTabs();
      _termRefreshUI();
      _termWatchResize(s);
      requestAnimationFrame(() => {
        setTimeout(() => {
          try { if (s.fit) s.fit.fit(); } catch (e) { }
          _termSendResize(s);
          if (s.term && _currentTermTab === 'terminal') s.term.focus();
        }, 60);
      });
    }

    function termCloseTab(id) {
      const i = _termSessions.findIndex(x => x.id === id);
      if (i < 0) return;
      const s = _termSessions[i];
      _termKillPings(s);
      if (s.retry.timer) { clearTimeout(s.retry.timer); s.retry.timer = null; }
      s.closing = true;   // cierre por higiene: el servidor limpia la sesión SSH sin evento closed
      if (s.ws && s.ws.readyState === 1) {
        try { s.ws.send(JSON.stringify({ type: 'close' })); } catch (e) { }
        try { s.ws.close(); } catch (e) { }
      }
      try { if (s.term) s.term.dispose(); } catch (e) { }
      if (s.pane) s.pane.remove();
      _termSessions.splice(i, 1);
      if (_termActiveId === id) {
        _termActiveId = null; _term = null; _fitAddon = null; _termWs = null;
        const next = _termSessions[Math.max(0, i - 1)];
        if (next) termActivateTab(next.id);
        else _termRefreshUI();
      }
      _termRenderTabs();
    }

    function _termCreateSession(deviceId) {
      if (typeof Terminal === 'undefined') {   // honesto: sin xterm vendorizado no hay consola
        toast(__t('term_no_xterm'), 'e');
        return null;
      }
      const host = document.getElementById('term-container');
      if (!host) return null;
      const s = {
        id: ++_termSeq, device: deviceId,
        term: null, fit: null, ws: null, pane: null, ct: null,
        buf: '', lastErr: null, closedSeen: false, closing: false,
        status: { state: 'disconnected', text: __t('term_disconnected') },
        pingT: null, pongT: null,
        retry: { timer: null, attempt: 0 }
      };
      // Banner honesto por causa (mismo patrón que SFTP): título + motivo + detalle + acción única
      const banner = document.createElement('div');
      banner.id = `term-banner-${s.id}`;
      banner.setAttribute('role', 'alert');
      banner.setAttribute('aria-live', 'polite');
      banner.style.cssText = 'display:none;background:#13151b;border-left:3px solid var(--text-ter);padding:10px 14px;gap:12px;align-items:flex-start;flex-wrap:wrap;flex-shrink:0';
      banner.innerHTML = `<div style="flex:1;min-width:220px">
        <div id="term-banner-title-${s.id}" style="font-size:12px;font-weight:700;word-break:break-word"></div>
        <div id="term-banner-text-${s.id}" style="display:none;font-size:11px;line-height:1.5;color:var(--text-secondary);white-space:pre-wrap;word-break:break-word"></div>
        <div id="term-banner-detail-${s.id}" style="display:none;font-family:var(--mono);font-size:10px;color:var(--text-ter);margin-top:4px;line-height:1.45;white-space:pre-wrap;word-break:break-word"></div></div>
      <div style="display:flex;gap:6px;align-items:flex-start;flex-shrink:0">
        <button id="term-banner-action-${s.id}" class="btn btn-o btn-sm" type="button" hidden style="padding:4px 10px;font-size:11px"></button>
        <button id="term-banner-close-${s.id}" class="btn btn-o btn-sm" type="button" title="${esc(__t('term_banner_close'))}" style="padding:4px 9px;font-size:11px">✕</button></div>`;
      const xtermEl = document.createElement('div');
      xtermEl.id = `term-xterm-${s.id}`;
      xtermEl.style.cssText = 'flex:1;min-height:0;position:relative;width:100%';
      const pane = document.createElement('div');
      pane.id = `term-session-${s.id}`;
      pane.style.cssText = 'position:absolute;inset:0;display:none;flex-direction:column;background:#0a0b0e;overflow:hidden;padding:8px 12px;box-sizing:border-box';
      pane.appendChild(banner);
      pane.appendChild(xtermEl);
      if (!host.style.position) host.style.position = 'relative';
      host.appendChild(pane);
      s.pane = pane; s.ct = xtermEl;
      document.getElementById(`term-banner-close-${s.id}`).onclick = () => _termHideBanner(s);
      // Paste: si el paste llega al textarea oculto de xterm, xterm lo entrega por onData (una sola vía);
      // si llega a otro elemento del panel, se envía como {"type":"input"} según el contrato (§4).
      pane.addEventListener('paste', ev => {
        if (ev.target && ev.target.tagName === 'TEXTAREA') return;
        const txt = ev.clipboardData ? ev.clipboardData.getData('text') : '';
        if (txt && s.ws && s.ws.readyState === 1) {
          ev.preventDefault();
          try { s.ws.send(JSON.stringify({ type: 'input', data: txt })); } catch (e) { }
        }
      });
      _termBuildXterm(s, xtermEl);
      _termSessions.push(s);
      return s;
    }

    function _termBuildXterm(s, mount) {
      s.term = new Terminal({
        cursorBlink: true,
        fontSize: 13,
        fontFamily: 'JetBrains Mono, Menlo, Consolas, Monaco, monospace',
        theme: {
          background: '#0a0b0e',
          foreground: '#e6edf3',
          cursor: '#58a6ff',
          selectionBackground: 'rgba(88,166,255,0.3)',
          black: '#0a0b0e',
          red: '#ff7b72',
          green: '#3fb950',
          yellow: '#d29922',
          blue: '#58a6ff',
          magenta: '#bc8cff',
          cyan: '#39c5cf',
          white: '#b1bac4',
        },
        scrollback: 10000,
        convertEol: true,
      });
      s.fit = new FitAddon.FitAddon();
      s.term.loadAddon(s.fit);
      s.term.open(mount);
      // Teclado: texto plano tal cual (caso normal del protocolo).
      s.term.onData(d => {
        if (d && s.ws && s.ws.readyState === 1) {
          try { s.ws.send(d); } catch (e) { }
        }
      });
    }

    function termOpenConsoleFor(deviceId) {
      if (!deviceId) return null;
      let s = _termSessions.find(x => x.device === deviceId) || null;
      if (!s) {
        if (_termSessions.length >= TERM_TAB_MAX) {
          toast(__t('term_max_tabs').replace('{n}', TERM_TAB_MAX), 'w');
          return null;
        }
        s = _termCreateSession(deviceId);
      }
      termActivateTab(s.id);
      return s;
    }

    // ── Conexión JSON (contrato §3) ────────────────────────────
    function termConnect() {
      if (_currentTermTab !== 'terminal') switchTermTab('terminal');
      const id = document.getElementById('term-dev')?.value;
      if (!id) { toast(__t('term_need_device'), 'w'); return; }
      if (typeof Terminal === 'undefined') { toast(__t('term_no_xterm'), 'e'); return; }
      const s = termOpenConsoleFor(id);
      if (!s) return;
      if (s.ws && (s.ws.readyState === 0 || s.ws.readyState === 1)) { _termHideBanner(s); return; }  // ya conectando/conectada
      _termManualRetry(s);
    }

    function _termManualRetry(s) {
      if (!s) return;
      _termKillPings(s);
      if (s.retry.timer) { clearTimeout(s.retry.timer); s.retry.timer = null; }
      s.retry.attempt = 0;
      _termHideBanner(s);
      _termOpenWs(s);
    }

    function _termOpenWs(s) {
      if (typeof Terminal === 'undefined') return;
      _termKillPings(s);
      if (s.retry.timer) { clearTimeout(s.retry.timer); s.retry.timer = null; }
      s.closedSeen = false; s.closing = false; s.lastErr = null;
      if (s.ws) {
        s.ws.onclose = null; s.ws.onmessage = null; s.ws.onerror = null;
        try { s.ws.close(); } catch (e) { }
      }
      if (!s.term) _termBuildXterm(s, s.ct);
      // Reconexión = PTY nuevo: se limpia el buffer y se pide prompt nuevo (contrato §7).
      try { s.term.clear(); } catch (e) { }
      s.buf = '';
      const wsProto = location.protocol === 'https:' ? 'wss' : 'ws';
      const url = `${wsProto}://${location.host}/ws/terminal/${encodeURIComponent(s.device)}?token=${encodeURIComponent(t)}&fmt=json`;
      s.status = { state: 'connecting', text: __t('term_connecting').replace('{d}', s.device) };
      try { s.term.write(`\x1b[90m[${__t('term_connecting').replace('{d}', s.device)}]\x1b[0m\r\n`); } catch (e) { }
      try { s.term.focus(); } catch (e) { }
      s.ws = new WebSocket(url);
      if (s.id === _termActiveId) { _term = s.term; _fitAddon = s.fit; _termWs = s.ws; }

      requestAnimationFrame(() => {
        setTimeout(() => {
          try { if (s.fit) s.fit.fit(); } catch (e) { }
          _termSendResize(s);
        }, 80);
      });

      s.ws.onopen = () => {
        // El estado real llega con el evento connected (SSH + PTY ya abiertos).
        if (s.id === _termActiveId) _termRefreshUI();
      };

      s.ws.onmessage = ev => {
        let m = null;
        try { m = JSON.parse(ev.data); } catch (e) { return; }   // en fmt=json todo mensaje es JSON
        if (!m || typeof m !== 'object') return;
        if (m.type === 'connected') {
          // SSH y PTY confirmados: cabecera honesta, teclado y heartbeat del lado UI.
          s.retry.attempt = 0; s.closing = false;
          s.connInfo = `${m.user || ''}@${m.host || ''}:${m.port || 22}`;
          s.status = {
            state: 'connected',
            text: __t('term_connected_status').replace('{d}', s.device).replace('{h}', m.host).replace('{p}', String(m.port))
          };
          _termHideBanner(s);
          _termStartPing(s);
          _termSendResize(s);
          if (s.id === _termActiveId) { _termRefreshUI(); _termRenderTabs(); try { s.term.focus(); } catch (e) { } }
        } else if (m.type === 'output') {
          if (m.data) {           // chunks vacíos se ignoran; el orden se respeta tal cual
            try { s.term.write(m.data); } catch (e) { }
            s.buf += m.data;
            if (s.buf.length > TERM_BUF_MAX) s.buf = s.buf.slice(-Math.floor(TERM_BUF_MAX / 2));
          }
        } else if (m.type === 'pong') {
          if (s.pongT) { clearTimeout(s.pongT); s.pongT = null; }
        } else if (m.type === 'error') {
          _termHandleError(s, m);
        } else if (m.type === 'closed') {
          _termHandleClosed(s, m);
        }
        // Otros tipos: ignorados (el servidor también los ignora en sentido inverso).
      };

      s.ws.onclose = ev => {
        _termKillPings(s);
        if (s.id === _termActiveId) _termWs = null;
        if (s.closing) {   // desconexión del propio operador o cierre de pestaña
          s.status = { state: 'disconnected', text: __t('term_disconnected') };
          if (s.id === _termActiveId) _termRefreshUI();
          _termRenderTabs();
          return;
        }
        if (ev && ev.code === 4401) {
          // Auth: el navegador solo ve el cierre 4401 (contrato §2) — sin reintentos, sesion caducada.
          s.status = { state: 'disconnected', text: __t('term_expired_title') };
          _termBanner(s, 'gray', __t('term_expired_title'), __t('term_expired_text'), '');
          if (s.id === _termActiveId) _termRefreshUI();
          _termRenderTabs();
          return;
        }
        if (s.closedSeen) return;   // cierre ordenado: el banner del evento closed ya informa
        _termStartBackoff(s);       // cierre anómalo (sin closed): sí reconectar (contrato §7)
      };

      s.ws.onerror = () => {
        // onclose siempre llega tras onerror: el manejo (banner/backoff) es único ahí.
      };
      if (s.id === _termActiveId) { _termRenderTabs(); _termRefreshUI(); }
    }

    function _termSendResize(s) {
      if (s && s.ws && s.ws.readyState === 1 && s.term) {
        try { s.ws.send(JSON.stringify({ type: 'resize', cols: s.term.cols, rows: s.term.rows })); } catch (e) { }
      }
    }

    // ── Heartbeat del lado UI (contrato §5): ping cada 25 s, margen 10 s ──
    function _termStartPing(s) {
      _termKillPings(s);
      s.pingT = setInterval(() => {
        if (!s.ws || s.ws.readyState !== 1) { _termStartBackoff(s); return; }
        try { s.ws.send(JSON.stringify({ type: 'ping' })); } catch (e) { }
        if (s.pongT) clearTimeout(s.pongT);
        s.pongT = setTimeout(() => {
          s.pongT = null;
          try { s.ws.close(); } catch (e) { }   // sin pong: conexión muerta; onclose queda cubierto por el guard del backoff
          _termStartBackoff(s);
        }, TERM_PONG_TIMEOUT_MS);
      }, TERM_PING_MS);
    }

    function _termKillPings(s) {
      if (s.pingT) { clearInterval(s.pingT); s.pingT = null; }
      if (s.pongT) { clearTimeout(s.pongT); s.pongT = null; }
    }

    // ── Reconexión con backoff exponencial (1s..8s, tope 15s, 6 intentos) ──
    function _termStartBackoff(s) {
      if (s.closing) return;
      _termKillPings(s);
      if (s.retry.timer) return;   // ya hay un reintento programado (guarda anti doble-incremento)
      s.retry.attempt += 1;
      if (s.retry.attempt > TERM_RETRY_MAX) { _termGaveUp(s); return; }
      const delay = TERM_BACKOFF_MS[Math.min(s.retry.attempt - 1, TERM_BACKOFF_MS.length - 1)];
      const msg = __t('term_reconnecting').replace('{n}', s.retry.attempt);
      s.status = { state: 'connecting', text: msg };
      _termBanner(s, 'warn', msg, __t('term_connecting').replace('{d}', s.device), '',
        __t('term_retry_stop'), () => {
          if (s.retry.timer) { clearTimeout(s.retry.timer); s.retry.timer = null; }
          s.retry.attempt = 0;
          _termGaveUp(s);
        });
      s.retry.timer = setTimeout(() => { s.retry.timer = null; _termOpenWs(s); }, delay);
      if (s.id === _termActiveId) { _termRefreshUI(); }
      _termRenderTabs();
    }

    function _termGaveUp(s) {
      _termKillPings(s);
      s.status = { state: 'disconnected', text: __t('term_disconnected') };
      _termBanner(s, 'gray', __t('term_no_connection').replace('{d}', s.device), '',
        s.lastErr && s.lastErr.detail ? s.lastErr.detail : '',
        __t('term_retry_manual'), () => _termManualRetry(s));
      if (s.id === _termActiveId) { _termRefreshUI(); }
      _termRenderTabs();
    }

    // ── Eventos `error` (contrato §3.3) — banner honesto por código, sin cerrar la vista ──
    function _termHandleError(s, m) {
      _termKillPings(s);
      s.lastErr = { code: m.code, detail: m.detail || '' };
      s.status = { state: 'disconnected', text: __t('term_disconnected') };
      let title = __t('term_err_internal'), tone = 'gray', action = false;
      const d = m.detail || '';
      switch (m.code) {
        case 'ssh_auth': title = __t('term_err_ssh_auth'); tone = 'red'; break;             // sin reintento en bucle
        case 'ssh_connect': title = __t('term_err_ssh_connect'); action = true; break;      // reintento manual
        case 'device_not_found': title = __t('term_err_devnf'); break;                      // sin reintento
        case 'unsupported': title = __t('term_err_unsupported'); break;                     // sin reintento
        case 'auth': title = __t('term_expired_title'); break;                              // reservado: tratar como caducada
        default: title = __t('term_err_internal'); break;
      }
      const mentionsApi = d.indexOf('puerto API') !== -1;
      const extra = mentionsApi ? (d.endsWith('.') ? '' : '. ') + __t('term_err_api_hint') : '';
      s.lastErr.detail = d + extra;
      _termBanner(s, tone, title, '', s.lastErr.detail,
        action ? __t('term_retry_manual') : '', action ? () => _termManualRetry(s) : null);
      if (s.id === _termActiveId) _termRefreshUI();
      _termRenderTabs();
    }

    // ── Evento `closed` (contrato §3.4) — reason literal del servidor en el banner ──
    function _termHandleClosed(s, m) {
      _termKillPings(s);
      s.closedSeen = true;
      s.status = { state: 'disconnected', text: __t('term_disconnected') };
      const reason = m.reason || '';
      const map = {
        idle: { title: __t('term_closed_idle'), act: true },        // "timeout de inactividad"
        eof: { title: __t('term_closed_eof'), act: true },          // "fin de sesión"
        unreachable: { title: __t('term_closed_unreachable'), act: true },  // "equipo inalcanzable"
        operator: { title: __t('term_closed_operator'), act: false },       // "cerrado por el operador"
        device_not_found: { title: __t('term_closed_devnf'), act: false },
        internal: { title: __t('term_closed_internal'), act: false }
      };
      const info = map[m.code] || { title: __t('term_closed_generic'), act: false };
      const d = [reason ? __t('term_reason').replace('{r}', reason) : '',
        (m.code === 'unreachable' || m.code === 'device_not_found') && s.lastErr && s.lastErr.detail
          ? __t('term_last_error').replace('{d}', s.lastErr.detail) : ''].filter(Boolean).join('\n');
      _termBanner(s, 'gray', info.title, '', d,
        info.act ? __t('term_retry_manual') : '', info.act ? () => _termManualRetry(s) : null);
      if (s.id === _termActiveId) _termRefreshUI();
      _termRenderTabs();
    }

    // ── Banner por pestaña (título/motivo/detalle + una acción opcional) ──
    const _TERM_TONES = { red: 'var(--red)', warn: '#f59e0b', gray: 'var(--text-ter)' };
    function _termBanner(s, tone, title, text, detail, actionLabel, action) {
      const el = document.getElementById(`term-banner-${s.id}`);
      if (!el) return;
      const color = _TERM_TONES[tone] || 'var(--text-ter)';
      el.style.borderLeftColor = color;
      const tEl = document.getElementById(`term-banner-title-${s.id}`);
      const xEl = document.getElementById(`term-banner-text-${s.id}`);
      const dEl = document.getElementById(`term-banner-detail-${s.id}`);
      const aEl = document.getElementById(`term-banner-action-${s.id}`);
      if (tEl) { tEl.textContent = title || ''; tEl.style.color = tone === 'red' ? 'var(--red)' : 'var(--text)'; }
      if (xEl) {
        xEl.textContent = text || '';
        xEl.style.display = text ? 'block' : 'none';
      }
      if (dEl) {
        dEl.textContent = detail || '';
        dEl.style.display = detail ? 'block' : 'none';
      }
      if (aEl) {
        if (actionLabel && typeof action === 'function') {
          aEl.textContent = actionLabel;
          aEl.onclick = () => action(s);
          aEl.hidden = false;
        } else { aEl.hidden = true; aEl.onclick = null; }
      }
      el.style.display = 'flex';
    }

    function _termHideBanner(s) {
      const el = s && document.getElementById(`term-banner-${s.id}`);
      if (el) el.style.display = 'none';
    }

    // ── Refresco de la cabecera de la consola (seguimos la pestaña activa) ──
    function _termRefreshUI() {
      const s = _termActive();
      const host = document.getElementById('term-info-host');
      const bc = document.getElementById('term-connect');
      const bd = document.getElementById('term-disconnect');
      if (!s) {
        _setTermStatus('disconnected', __t('term_disconnected'));
        if (host) host.textContent = 'ssh://desconectado';
        if (bc) bc.style.display = 'inline-block';
        if (bd) bd.style.display = 'none';
        return;
      }
      _setTermStatus(s.status.state, s.status.text);
      if (host) host.textContent = s.connInfo ? `ssh://${s.connInfo}` : `ssh://${s.device}`;
      const live = !!(s.ws && s.ws.readyState === 1);
      if (bc) bc.style.display = live ? 'none' : 'inline-block';
      if (bd) bd.style.display = live ? 'inline-block' : 'none';
    }

    function termDisconnect() {
      const s = _termActive();
      if (!s) return;
      _termKillPings(s);
      if (s.retry.timer) { clearTimeout(s.retry.timer); s.retry.timer = null; }
      if (s.ws && s.ws.readyState === 1) {
        s.closing = true;   // {"type":"close"} → el servidor responde closed/operator (contrato §4)
        try { s.ws.send(JSON.stringify({ type: 'close' })); } catch (e) { }
        setTimeout(() => { try { if (s.ws) s.ws.close(); } catch (e) { } }, 1000);
      }
      s.status = { state: 'disconnected', text: __t('term_disconnected') };
      _termHideBanner(s);
      _termRefreshUI();
      _termRenderTabs();
    }

    function termClear() {
      const s = _termActive();
      if (!s) return;
      if (s.term) { s.term.clear(); s.term.focus(); }
      s.buf = '';   // "Limpiar" por pestaña también limpia el buffer de búsqueda
      termSearchClose();
    }

    function _termWatchResize(s) {
      if (window._termRO) { try { window._termRO.disconnect(); } catch (e) { } }
      if (!window.ResizeObserver || !s || !s.ct) return;
      window._termRO = new ResizeObserver(() => {
        requestAnimationFrame(() => {
          try {
            if (s.fit) s.fit.fit();
            _termSendResize(s);
          } catch (e) { }
        });
      });
      window._termRO.observe(s.ct);
    }

    function termToggleFullscreen() {
      const pane = document.getElementById('term-pane-terminal');
      const btn = document.getElementById('term-fullscreen-btn');
      if (!pane) return;
      if (!document.fullscreenElement) {
        pane.requestFullscreen().then(() => {
          if (btn) setIconText(btn, '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M8 3H3v5M16 3h5v5M3 16v5h5M21 16v5h-5"/></svg> Salir pantalla completa');
          setTimeout(() => { const s = _termActive(); if (s && s.fit) { try { s.fit.fit(); } catch (e) { } _termSendResize(s); } }, 120);
        }).catch(err => toast('Error pantalla completa: ' + err.message, 'e'));
      } else {
        document.exitFullscreen().then(() => {
          if (btn) setIconText(btn, '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M8 3H3v5M16 3h5v5M3 16v5h5M21 16v5h-5"/></svg> Pantalla completa');
          setTimeout(() => { const s = _termActive(); if (s && s.fit) { try { s.fit.fit(); } catch (e) { } _termSendResize(s); } }, 120);
        });
      }
    }

    // ── Copiar selección ──
    function _termCopySel() {
      const s = _termActive();
      let txt = '';
      try { txt = (s && s.term && s.term.hasSelection && s.term.hasSelection()) ? s.term.getSelection() : ''; } catch (e) { }
      if (!txt) { toast(__t('term_copy_none'), 'w'); return; }
      const done = () => toast(__t('term_copied'), 's');
      const fail = () => toast(__t('term_copy_fail'), 'e');
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(txt).then(done).catch(() => _termCopyFallback(txt, done, fail));
      } else {
        _termCopyFallback(txt, done, fail);
      }
    }

    function _termCopyFallback(txt, done, fail) {
      const old = document.getElementById('term-copy-ta');
      if (old) old.remove();
      const ta = document.createElement('textarea');
      ta.id = 'term-copy-ta';
      ta.value = txt;
      ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0;pointer-events:none';
      document.body.appendChild(ta);
      ta.select();
      let ok = false;
      try { ok = document.execCommand('copy'); } catch (e) { }
      ta.remove();
      ok ? done() : fail();
    }

    // ── Búsqueda en el scrollback (buffer local: el addon Search NO está vendorizado) ──
    // El texto se alimenta al recibir cada chunk `output`; Enter consulta este buffer y
    // muestra las líneas coincidentes en un panel (no se simula scrolling dentro de xterm).
    function termSearchRun() {
      const inp = document.getElementById('term-search');
      const panel = document.getElementById('term-search-panel');
      if (!inp || !panel) return;
      const q = inp.value;
      if (!q.trim() || !_termActive()) { panel.style.display = 'none'; return; }
      const lines = String(_termActive().buf).split(/\r\n|\r|\n/);
      const ql = q.toLowerCase();
      const matches = [];
      for (let i = 0; i < lines.length; i++) {
        if (lines[i].toLowerCase().indexOf(ql) !== -1) {
          matches.push({ n: i + 1, line: lines[i] });
          if (matches.length >= 51) break;
        }
      }
      let head = q.trim() ? __t('term_search_none').replace('{q}', esc(q)) : '';
      if (matches.length) head = __t('term_search_title').replace('{n}', matches.length).replace('{q}', esc(q));
      let html = `<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px">
        <span style="color:var(--text);font-weight:600;font-size:12px">${head}</span>
        <button class="btn btn-o btn-sm" type="button" onclick="termSearchClose()" style="padding:3px 9px;font-size:11px">✕ ${esc(__t('term_banner_close'))}</button></div>`;
      if (!matches.length) {
        html += `<div style="color:var(--text-ter);font-size:11px">${esc(__t('term_search_hint'))}</div>`;
      } else {
        html += '<div style="max-height:320px;overflow-y:auto;display:flex;flex-direction:column;gap:2px">' +
          matches.slice(0, 50).map(m =>
            `<div style="white-space:pre-wrap;word-break:break-all;font-family:var(--mono);font-size:11px;color:var(--text-secondary)"><span style="color:var(--text-ter);font-size:10px">L${m.n}</span> ${_termMarkLine(m.line, q)}</div>`).join('') +
          (matches.length > 50 ? `<div style="color:var(--text-ter);font-size:11px;margin-top:4px">+${matches.length - 50}…</div>` : '') + '</div>';
      }
      panel.innerHTML = html;
      panel.style.display = 'block';
    }

    function termSearchClose() {
      const panel = document.getElementById('term-search-panel');
      if (panel) panel.style.display = 'none';
    }

    function _termMarkLine(line, q) {
      const re = new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
      let out = '', last = 0, m, guard = 0;
      while ((m = re.exec(line)) !== null && guard < 500) {
        guard++;
        if (m[0].length === 0) { re.lastIndex++; continue; }
        out += esc(line.slice(last, m.index)) + `<span style="background:rgba(88,166,255,.45);border-radius:3px">${esc(m[0])}</span>`;
        last = m.index + m[0].length;
      }
      out += esc(line.slice(last));
      return out || '&nbsp;';
    }

    // ── Unificación Comandos ↔ Consola: abre pestaña con el equipo preseleccionado y conectado ──
    function cmdOpenConsole() {
      const id = document.getElementById('cmd-dev')?.value;
      if (!id) { toast(__t('term_need_device'), 'w'); return; }
      const open = () => {
        if (!devices.length) { toast(__t('term_need_device'), 'w'); return; }   // inventario aún cargando: nada a ciegas
        const sel = document.getElementById('term-dev');
        if (sel && Array.from(sel.options).some(o => o.value === id)) sel.value = id;
        termConnect();
      };
      const v = document.getElementById('v-terminal');
      if (v && v.classList.contains('active')) open();
      else {
        swView('terminal');
        setTimeout(open, 120);
      }
    }
