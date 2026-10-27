/**
 * NetPulse NMS — SFTP (vista #v-terminal, pestaña "SFTP"):
 * navegación, subida, descarga y drag & drop (views/sftp.js).
 *
 * Extraída del <script> inline de app/static/dashboard.html (Tarjeta 10D-I,
 * lote i) SIN cambio de comportamiento: bloque contiguo byte a byte en el
 * mismo orden relativo (comentario GESTOR SFTP < _formatBytes < _formatTime <
 * _sftpNormPath < _sftpJoin < _sftpArg < _sftpSetSummary < _sftpProgress <
 * _sftpProgressDone < sftpHideBanner < sftpShowBanner <
 * sftpSetTransferEnabled < sftpRenderBreadcrumb < _sftpNotice <
 * _SFTP_ICON_WARN < _SFTP_ICON_FOLDER < _sftpClearList < _sftpErrorInfo <
 * sftpApplyError < sftpLoadDir < sftpRefresh < sftpOpenDir < sftpNavUp <
 * sftpUploadSelectedFile < sftpHandleDrop < _sftpFilenameFromCd <
 * _sftpSaveBlob < sftpDownloadFile).
 *
 * Contrato: docs/handoffs/sftp-ui.md (list/upload/download, RBAC, puerto SSH
 * real, timeout de kex, banner persistente POR CAUSA —401/501/502/504/403/404/
 * 400/500—, nunca "tabla vacía" ante error, no pintar items si `!res.ok`).
 * Se conserva la lógica y los textos tal cual.
 *
 * Estado: `_sftpCurrentPath`, `_sftpDeviceId`, `_sftpBlocked`, `_sftpBusy`,
 * `_sftpLastGoodPath` se declaran en views/terminal.js (donde vivían, en el
 * mismo bloque del monolito) y aquí se leen/escriben en tiempo de llamada
 * (binding `let` del entorno léxico global compartido por scripts clásicos).
 * `_SFTP_ICON_WARN/_SFTP_ICON_FOLDER` son `const` de este archivo.
 *
 * Drag & drop: el dropzone del markup llama `sftpHandleDrop(event)`; NO se
 * reutiliza ni se toca `initDragDrop` de views/monitoring.js (drag & drop de
 * los charts, `let dragSrc`), que sigue siendo exclusivo de Monitoring.
 *
 * Cruces con Terminal (todos en tiempo de llamada, sin duplicar):
 * `switchTermTab('terminal')` desde `_sftpErrorInfo` (501 → abrir consola) y
 * `sftpLoadDir` desde `switchTermTab`/`termDevChanged` (views/terminal.js).
 *
 * Dependencias: core.js (`A`, `h`, `t`, `toast`, `__t`), inline (`esc`,
 * `escAttr`, `_detText`, `devices`), views/terminal.js (estado `_sftp*` +
 * `switchTermTab`). Handlers por window: `sftpLoadDir`, `sftpRefresh`,
 * `sftpNavUp`, `sftpUploadSelectedFile`, `sftpHandleDrop`, `sftpHideBanner`,
 * `sftpDownloadFile` (onclick generado), `sftpOpenDir` (onclick generado).
 * Script clásico sin defer/async, cargado tras views/terminal.js; top-level
 * sin lógica: solo `const` de iconos y declaraciones `function`.
 *
 * Defectos preexistentes conservados: `logout` no aborta transferencias ni
 * limpia el estado SFTP (§7.15 del mapa).
 */

    // ── GESTOR SFTP (TRANSFERENCIA DE ARCHIVOS) ──
    function _formatBytes(bytes) {
      if (!bytes || bytes === 0) return '0 B';
      const k = 1024, dm = 1;
      const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
      const i = Math.floor(Math.log(bytes) / Math.log(k));
      return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
    }

    function _formatTime(sec) {
      if (!sec) return '—';
      const d = new Date(sec * 1000);
      return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }

    // ── SFTP: utilidades de ruta, banner honesto y progreso ──
    function _sftpNormPath(p) {
      let s = String(p == null ? '/' : p).trim();
      if (!s) return '/';
      if (s.charAt(0) !== '/') s = '/' + s;
      s = s.replace(/\/{2,}/g, '/');
      if (s.length > 1) s = s.replace(/\/+$/, '');
      return s || '/';
    }

    function _sftpJoin(dir, name) {
      const d = String(dir || '/').replace(/\/+$/, '');
      return `${d}/${String(name)}`;
    }

    // Argumento seguro para onclick: encodeURIComponent deja pasar ' y por eso se sustituye.
    function _sftpArg(v) { return encodeURIComponent(String(v)).replace(/'/g, '%27'); }

    function _sftpSetSummary(text) {
      const el = document.getElementById('sftp-summary');
      if (el) el.textContent = text;
    }

    function _sftpProgress(text) {
      const box = document.getElementById('sftp-progress');
      const txt = document.getElementById('sftp-progress-text');
      if (txt) txt.textContent = text;
      if (box) box.style.display = 'flex';
    }

    function _sftpProgressDone() {
      const box = document.getElementById('sftp-progress');
      if (box) box.style.display = 'none';
      const txt = document.getElementById('sftp-progress-text');
      if (txt) txt.textContent = '';
    }

    function sftpHideBanner() {
      const box = document.getElementById('sftp-banner');
      if (box) box.style.display = 'none';
      const detail = document.getElementById('sftp-banner-detail');
      if (detail) { detail.textContent = ''; detail.style.display = 'none'; }
      const action = document.getElementById('sftp-banner-action');
      if (action) { action.style.display = 'none'; action.onclick = null; action.textContent = ''; }
    }

    // tone: 'gray' (no verificable / sin respuesta), 'warn' (credenciales o permisos), 'error', 'ok'
    function sftpShowBanner(tone, title, text, detail, actionLabel, actionFn) {
      const box = document.getElementById('sftp-banner');
      if (!box) return;
      const tones = {
        gray: { border: 'var(--text-ter)', bg: 'var(--panel)', title: 'var(--text)' },
        warn: { border: 'var(--orange)', bg: 'rgba(249,115,22,0.08)', title: 'var(--orange)' },
        error: { border: 'var(--red)', bg: 'rgba(239,68,68,0.08)', title: 'var(--red)' },
        ok: { border: 'var(--green)', bg: 'rgba(16,185,129,0.08)', title: 'var(--green)' }
      };
      const c = tones[tone] || tones.gray;
      box.style.display = 'flex';
      box.style.borderLeftColor = c.border;
      box.style.background = c.bg;
      const titleEl = document.getElementById('sftp-banner-title');
      const textEl = document.getElementById('sftp-banner-text');
      const detailEl = document.getElementById('sftp-banner-detail');
      if (titleEl) { titleEl.textContent = title || ''; titleEl.style.color = c.title; }
      if (textEl) textEl.textContent = text || '';
      if (detailEl) {
        const showDetail = !!detail && detail !== text;
        detailEl.textContent = showDetail ? detail : '';
        detailEl.style.display = showDetail ? 'block' : 'none';
      }
      const action = document.getElementById('sftp-banner-action');
      if (action) {
        if (actionLabel && actionFn) {
          action.style.display = 'inline-block';
          action.textContent = actionLabel;
          action.onclick = (ev) => { if (ev) ev.preventDefault(); actionFn(); };
        } else {
          action.style.display = 'none';
          action.onclick = null;
          action.textContent = '';
        }
      }
    }

    // 501: no se ofrece reintentar SFTP; subir/descargar quedan deshabilitados hasta cambiar de equipo.
    function sftpSetTransferEnabled(on) {
      _sftpBlocked = !on;
      const up = document.getElementById('sftp-upload-btn');
      if (up) {
        up.disabled = !on;
        up.title = on ? '' : __t('sftp_blocked_hint');
      }
      const inp = document.getElementById('sftp-file-input');
      if (inp) inp.disabled = !on;
      document.querySelectorAll('.sftp-dl-btn').forEach(btn => {
        btn.disabled = !on;
        btn.title = on ? '' : __t('sftp_blocked_hint');
      });
    }

    function sftpRenderBreadcrumb(path) {
      const el = document.getElementById('sftp-breadcrumb');
      if (!el) return;
      const parts = _sftpNormPath(path).split('/').filter(Boolean);
      let acc = '';
      let html = `<button type="button" class="sftp-crumb-btn" onclick="sftpLoadDir('/')"${parts.length ? '' : ' aria-current="page"'}>/</button>`;
      parts.forEach((part, i) => {
        acc += '/' + part;
        const last = i === parts.length - 1;
        html += `<span class="sftp-crumb-sep" aria-hidden="true">/</span>`;
        html += `<button type="button" class="sftp-crumb-btn" onclick="sftpLoadDir(decodeURIComponent('${_sftpArg(acc)}'))"${last ? ' aria-current="page"' : ''}>${esc(part)}</button>`;
      });
      el.innerHTML = html;
    }

    function _sftpNotice(title, text, opts) {
      const o = opts || {};
      const color = o.color || 'var(--text-ter)';
      const titleColor = o.titleColor || 'var(--text-secondary)';
      return `<div style="padding:44px 24px;text-align:center">
        <div style="font-size:28px;margin-bottom:10px;color:${color}">${o.icon || ''}</div>
        <div style="font-size:13px;font-weight:700;color:${titleColor};margin-bottom:6px">${esc(title)}</div>
        <div style="font-size:11px;color:var(--text-ter);line-height:1.6;max-width:620px;margin:0 auto">${esc(text)}</div>
      </div>`;
    }

    const _SFTP_ICON_WARN = '<svg class="ic" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m12 3 10 18H2Z"/><path d="M12 9v4M12 17h.01"/></svg>';
    const _SFTP_ICON_FOLDER = '<svg class="ic" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>';

    // Un fallo nunca deja el listado vacío como si fuera éxito: bloque explícito + resumen «sin verificar».
    function _sftpClearList(reason) {
      const listContainer = document.getElementById('sftp-file-list');
      if (listContainer) {
        listContainer.innerHTML = _sftpNotice(__t('sftp_list_fail'), reason || __t('sftp_list_fail_d'), {
          icon: _SFTP_ICON_WARN, color: 'var(--red)', titleColor: 'var(--red)'
        });
      }
      _sftpSetSummary(__t('sftp_summary_none'));
    }

    // Traduce código HTTP + `detail` literal del contrato a banner (título, texto y acción).
    function _sftpErrorInfo(status, detail, ctx) {
      const dev = document.getElementById('term-dev')?.value || '';
      const op = (ctx && ctx.op) || 'list';
      const path = (ctx && ctx.path) || _sftpCurrentPath || '/';
      const d = detail || __t('sftp_err_generic');
      if (status === 401) {
        return { tone: 'warn', title: __t('sftp_err_auth_title'), text: __t('sftp_err_auth_text'), detail: d };
      }
      if (status === 501) {
        return {
          tone: 'gray', title: __t('sftp_err_501_title'), text: __t('sftp_err_501_text'), detail: d,
          actionLabel: __t('sftp_open_terminal'), action: () => switchTermTab('terminal')
        };
      }
      if (status === 502) {
        // Kex/banner incompleto (contrato docs/handoffs/sftp-ui.md): NO es credenciales.
        // Banner naranja con el detail literal; sin acción de reintento para no reintentar en bucle.
        if (/^Negociación SSH incompleta/i.test(d)) {
          return { tone: 'warn', title: __t('sftp_err_kex_title'), text: __t('sftp_err_kex_text'), detail: d };
        }
        return { tone: 'gray', title: __t('sftp_err_502_title'), text: d, detail: '' };
      }
      if (status === 504) {
        return { tone: 'gray', title: __t('sftp_err_504_title'), text: __t('sftp_err_504_text').replace('{d}', dev || '—'), detail: d };
      }
      if (status === 403) {
        const writing = op === 'upload';
        return {
          tone: 'warn',
          title: writing ? __t('sftp_err_403_up') : __t('sftp_err_403_read'),
          text: d,
          detail: ''
        };
      }
      if (status === 404) {
        if (/dispositivo/i.test(d)) return { tone: 'warn', title: __t('sftp_err_404_dev'), text: d, detail: '' };
        return {
          tone: 'warn', title: __t('sftp_err_404_file'), text: d, detail: '',
          actionLabel: __t('sftp_reload_dir'), action: () => sftpRefresh()
        };
      }
      if (status === 400) {
        const info = { tone: 'warn', title: __t('sftp_err_400_title'), text: d, detail: '' };
        if (_sftpLastGoodPath && _sftpLastGoodPath !== path) {
          info.actionLabel = __t('sftp_back_last');
          info.action = () => sftpLoadDir(_sftpLastGoodPath);
        }
        return info;
      }
      if (status === 500) {
        return { tone: 'error', title: __t('sftp_err_500_title'), text: d, detail: __t('sftp_err_500_text') };
      }
      return { tone: 'gray', title: __t('sftp_err_generic_title'), text: d, detail: '' };
    }

    function sftpApplyError(status, detail, ctx) {
      const info = _sftpErrorInfo(status, detail, ctx);
      sftpShowBanner(info.tone, info.title, info.text, info.detail, info.actionLabel, info.action);
      if (status === 501) sftpSetTransferEnabled(false);
      return info;
    }

    async function sftpLoadDir(path) {
      const devId = document.getElementById('term-dev')?.value;
      const listContainer = document.getElementById('sftp-file-list');
      if (!listContainer) return;

      if (!devId) {
        _sftpDeviceId = null;
        _sftpBusy = false;
        sftpSetTransferEnabled(true);
        sftpHideBanner();
        _sftpProgressDone();
        _sftpCurrentPath = '/';
        sftpRenderBreadcrumb('/');
        _sftpSetSummary(__t('sftp_summary_none'));
        listContainer.innerHTML = _sftpNotice(__t('sftp_no_dev_title'), __t('sftp_no_dev_text'), { icon: _SFTP_ICON_FOLDER });
        return;
      }

      // Cambiar de equipo limpia el estado y vuelve a «/»: nunca se deja visible el listado de otro equipo.
      if (_sftpDeviceId !== devId) {
        _sftpDeviceId = devId;
        _sftpBlocked = false;
        _sftpBusy = false;
        sftpSetTransferEnabled(true);
        sftpHideBanner();
        _sftpProgressDone();
        _sftpLastGoodPath = '/';
        path = '/';
      }

      path = _sftpNormPath(path || '/');
      _sftpCurrentPath = path;
      const pathInput = document.getElementById('sftp-path-input');
      if (pathInput) pathInput.value = path;
      sftpRenderBreadcrumb(path);
      sftpHideBanner();
      _sftpSetSummary(__t('sftp_summary_loading'));
      listContainer.innerHTML = `<div style="padding:44px 24px;text-align:center;color:var(--text-ter)"><div class="sp" style="margin:0 auto 12px"></div>${esc(__t('sftp_loading'))}</div>`;

      try {
        const res = await fetch(`${A}/sftp/${encodeURIComponent(devId)}/list?path=${encodeURIComponent(path)}`, {
          headers: h()
        });
        if (!res.ok) {
          const body = await res.json().catch(() => null);
          const detail = _detText(body, __t('sftp_err_generic'));
          _sftpClearList(detail);
          sftpApplyError(res.status, detail, { op: 'list', path });
          return; // 401/403/404/501/502/504/…: jamás se pintan items
        }
        const data = await res.json().catch(() => null);
        if (!data || data.success !== true || !Array.isArray(data.items)) {
          _sftpClearList(__t('sftp_err_badres_text'));
          sftpShowBanner('error', __t('sftp_err_badres_title'), __t('sftp_err_badres_text'), '', '', null);
          return;
        }

        const items = data.items;
        _sftpLastGoodPath = path;
        sftpSetTransferEnabled(true);

        if (items.length === 0) {
          _sftpSetSummary(__t('sftp_summary_empty').replace('{p}', path));
          listContainer.innerHTML = _sftpNotice(__t('sftp_empty_title'), __t('sftp_empty_detail'), {
            icon: _SFTP_ICON_FOLDER, color: 'var(--text-ter)', titleColor: 'var(--text-secondary)'
          });
          return;
        }

        const dirCount = items.filter(x => x.is_dir).length;
        const fileCount = items.length - dirCount;
        _sftpSetSummary(__t('sftp_summary')
          .replace('{n}', String(items.length))
          .replace('{d}', String(dirCount))
          .replace('{f}', String(fileCount))
          .replace('{p}', path));

        listContainer.innerHTML = items.map(item => {
          const isDir = item.is_dir;
          const icon = isDir ? '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>' : '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>';
          const nameArg = `decodeURIComponent('${_sftpArg(item.name)}')`;
          const clickHandler = isDir ? `onclick="sftpOpenDir(${nameArg})"` : '';
          const actionBtn = isDir
            ? `<button class="btn btn-o btn-xs" onclick="sftpOpenDir(${nameArg})" style="padding:3px 8px;font-size:10px">${esc(__t('sftp_open'))}</button>`
            : `<button class="btn btn-p btn-xs sftp-dl-btn" onclick="sftpDownloadFile(${nameArg})" style="padding:3px 8px;font-size:10px" title="${escAttr(__t('sftp_download'))}"${_sftpBlocked ? ' disabled' : ''}><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M12 3v12m-5-5 5 5 5-5"/></svg> ${esc(__t('sftp_download'))}</button>`;

          return `
            <div class="sftp-row ${isDir ? 'sftp-dir' : ''}" ${clickHandler}>
              <div style="font-size:16px;text-align:center">${icon}</div>
              <div class="sftp-name" style="font-weight:${isDir ? '600' : '400'};color:${isDir ? 'var(--text)' : 'var(--text-secondary)'};word-break:break-all">${esc(item.name)}</div>
              <div style="text-align:right;font-family:var(--mono);color:var(--text-ter)">${isDir ? '—' : _formatBytes(item.size)}</div>
              <div style="font-family:var(--mono);color:var(--text-ter);font-size:11px">${esc(item.permissions || '—')}</div>
              <div style="color:var(--text-ter);font-size:11px">${_formatTime(item.mtime)}</div>
              <div style="text-align:center">${actionBtn}</div>
            </div>
          `;
        }).join('');

      } catch (err) {
        // Red caída o respuesta ilegible: estado honesto, nunca un listado vacío de éxito.
        _sftpClearList(__t('sftp_list_fail_d'));
        sftpShowBanner('error', __t('sftp_err_api_title'), __t('sftp_err_api_text'), (err && err.message) ? String(err.message) : '', '', null);
        toast(__t('sftp_err_api_title'), 'e');
      }
    }

    function sftpRefresh() {
      sftpLoadDir(_sftpCurrentPath || '/');
    }

    function sftpOpenDir(dirName) {
      sftpLoadDir(_sftpJoin(_sftpCurrentPath || '/', dirName));
    }

    function sftpNavUp() {
      let cur = (_sftpCurrentPath || '/').replace(/\/+$/, '');
      if (!cur || cur === '/') return;
      const parts = cur.split('/').filter(Boolean);
      parts.pop();
      const parent = parts.length ? '/' + parts.join('/') : '/';
      sftpLoadDir(parent);
    }

    async function sftpUploadSelectedFile(files) {
      if (!files || !files.length) return;
      const file = files[0];
      const devId = document.getElementById('term-dev')?.value;
      const input = document.getElementById('sftp-file-input');
      const upBtn = document.getElementById('sftp-upload-btn');

      if (!devId) { toast(__t('sftp_select_dev'), 'w'); if (input) input.value = ''; return; }
      if (_sftpBlocked) { toast(__t('sftp_blocked_hint'), 'w'); if (input) input.value = ''; return; }
      if (_sftpBusy) { toast(__t('sftp_busy'), 'w'); if (input) input.value = ''; return; }

      const curPath = _sftpCurrentPath || '/';
      const targetPath = _sftpNormPath(_sftpJoin(curPath, file.name));
      _sftpBusy = true;
      if (upBtn) upBtn.disabled = true;
      sftpHideBanner();
      _sftpProgress(__t('sftp_up_start').replace('{f}', file.name).replace('{s}', _formatBytes(file.size)));

      try {
        const res = await fetch(`${A}/sftp/${encodeURIComponent(devId)}/upload?remote_dir=${encodeURIComponent(curPath)}&filename=${encodeURIComponent(file.name)}`, {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${t}`,
            'Content-Type': 'application/octet-stream'
          },
          body: file
        });
        if (!res.ok) {
          // Sin 2xx no hay éxito: se muestra el `detail` literal del backend.
          const body = await res.json().catch(() => null);
          const detail = _detText(body, __t('sftp_up_fail'));
          sftpApplyError(res.status, detail, { op: 'upload', path: targetPath });
          toast(detail, 'e');
          return;
        }
        const data = await res.json().catch(() => null);
        if (!data || data.success !== true) {
          const detail = _detText(data, __t('sftp_up_unconfirmed'));
          sftpShowBanner('error', __t('sftp_up_unconfirmed_title'), detail, '', '', null);
          toast(detail, 'e');
          return;
        }
        toast(data.message || __t('sftp_up_ok'), 's');
        await sftpLoadDir(curPath);
      } catch (err) {
        const msg = (err && err.message) ? String(err.message) : '';
        sftpShowBanner('error', __t('sftp_err_api_title'), __t('sftp_err_api_text'), msg, '', null);
        toast(`${__t('sftp_up_fail')}${msg ? ': ' + msg : ''}`, 'e');
      } finally {
        _sftpBusy = false;
        _sftpProgressDone();
        if (input) input.value = '';
        sftpSetTransferEnabled(!_sftpBlocked);
      }
    }

    function sftpHandleDrop(ev) {
      ev.preventDefault();
      const dropzone = document.getElementById('sftp-dropzone');
      if (dropzone) dropzone.style.borderColor = 'var(--border)';
      if (ev.dataTransfer && ev.dataTransfer.files && ev.dataTransfer.files.length) {
        sftpUploadSelectedFile(ev.dataTransfer.files);
      }
    }

    function _sftpFilenameFromCd(cd, fallback) {
      if (cd) {
        const star = /filename\*=UTF-8''([^;]+)/i.exec(cd);
        if (star && star[1]) {
          try { return decodeURIComponent(star[1].trim().replace(/^"|"$/g, '')); } catch (e) { /* usa el nombre del listado */ }
        }
        const plain = /filename="?([^";]+)"?/i.exec(cd);
        if (plain && plain[1]) return plain[1].trim();
      }
      return fallback;
    }

    function _sftpSaveBlob(blob, name) {
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.style.display = 'none';
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      setTimeout(() => {
        window.URL.revokeObjectURL(url);
        if (a.parentNode) a.parentNode.removeChild(a);
      }, 1000);
    }

    async function sftpDownloadFile(filename) {
      const devId = document.getElementById('term-dev')?.value;
      if (!devId) { toast(__t('sftp_select_dev'), 'w'); return; }
      if (_sftpBlocked) { toast(__t('sftp_blocked_hint'), 'w'); return; }
      if (_sftpBusy) { toast(__t('sftp_busy'), 'w'); return; }

      const fullPath = _sftpNormPath(_sftpJoin(_sftpCurrentPath || '/', filename));
      _sftpBusy = true;
      sftpHideBanner();
      _sftpProgress(__t('sftp_dl_start').replace('{f}', filename));

      try {
        const res = await fetch(`${A}/sftp/${encodeURIComponent(devId)}/download?remote_path=${encodeURIComponent(fullPath)}`, {
          headers: h()
        });
        if (!res.ok) {
          // Cualquier código distinto de 200 es JSON con `detail`: no se guarda blob ni se finge descarga.
          const body = await res.json().catch(() => null);
          const detail = _detText(body, __t('sftp_dl_fail'));
          sftpApplyError(res.status, detail, { op: 'download', path: fullPath });
          toast(detail, 'e');
          return;
        }
        const blob = await res.blob();
        const name = _sftpFilenameFromCd(res.headers.get('Content-Disposition'), filename);
        _sftpSaveBlob(blob, name);
        toast(__t('sftp_dl_ok').replace('{f}', name), 's');
      } catch (err) {
        const msg = (err && err.message) ? String(err.message) : '';
        sftpShowBanner('error', __t('sftp_err_api_title'), __t('sftp_err_api_text'), msg, '', null);
        toast(`${__t('sftp_dl_fail')}${msg ? ': ' + msg : ''}`, 'e');
      } finally {
        _sftpBusy = false;
        _sftpProgressDone();
        sftpSetTransferEnabled(!_sftpBlocked);
      }
    }
