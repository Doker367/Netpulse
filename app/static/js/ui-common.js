// NetPulse NOC — UI común (Lote K): estado compartido, escape/iconos, tema,
// login, menú de usuario y miscelánea global. Script clásico cargado tras
// las vistas y antes de ui-helpers.js/router.js. Su top-level cuelga de
// window, que es como lo resuelven los atributos on* del markup.

    let devices = [], sel = null, aTab = 'facts', bSel = new Set(), bMode = false;
    let alertSummary = null; // null significa fuente sin verificar, no cero alertas.
    let overviewState = null, inventoryError = false;

    function esc(s) { return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;') }
    function escAttr(s) { return esc(s).replace(/"/g, '&quot;').replace(/'/g, '&#39;') }

    // ── Iconos SVG (sin emojis) ──────────────────────────────
    const IC = {
      grip: '<circle cx="9" cy="5" r="1"/><circle cx="9" cy="12" r="1"/><circle cx="9" cy="19" r="1"/><circle cx="15" cy="5" r="1"/><circle cx="15" cy="12" r="1"/><circle cx="15" cy="19" r="1"/>',
      compare: '<path d="M3 8h18m-4-4 4 4-4 4M21 16H3m4-4-4 4 4 4"/>',
      pause: '<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>',
      arrowUp: '<path d="M12 19V5m-7 7 7-7 7 7"/>',
      undo: '<path d="M3 10h11a7 7 0 0 1 0 14M3 10l6-6M3 10l6 6"/>',
      maximize: '<path d="M8 3H3v5M16 3h5v5M3 16v5h5M21 16v5h-5"/>',
      arrowRight: '<path d="M5 12h14m-6-6 6 6-6 6"/>',
      mapPin: '<path d="M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/>',
      user: '<circle cx="12" cy="7" r="4"/><path d="M5 21v-2a7 7 0 0 1 14 0v2"/>',
      hourglass: '<path d="M5 2h14M5 22h14M7 2v4l10 12v4M17 2v4L7 18v4"/>',
      rocket: '<path d="M4.5 16.5c-1.5 1.3-2 5-2 5s3.7-.5 5-2M12 15l-3-3a22 22 0 0 1 3-6c3-4 10-4 10-4s0 7-4 10a22 22 0 0 1-6 3ZM9 12H4s.5-3 3-4h4M12 15v5s3-.5 4-3v-4"/><circle cx="16" cy="8" r="1"/>',
      pin: '<path d="m16 3 5 5-4 1-4 4-1 5-6-6 5-1 4-4zM2 22l6-6"/>',
      gauge: '<path d="M3 17a10 10 0 1 1 18 0M12 13l4-4"/><circle cx="12" cy="13" r="1"/>',
      flame: '<path d="M12 3c1 5 6 6 6 12a6 6 0 0 1-12 0c0-3 2-5 3-6 0 3 1 4 2 4 2-3 2-6 1-10Z"/>',
      square: '<rect x="3" y="3" width="18" height="18" rx="2"/>',
      squareCheck: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="m8 12 3 3 5-6"/>',
      route: '<circle cx="6" cy="18" r="3"/><circle cx="18" cy="6" r="3"/><path d="M6 15V6a3 3 0 0 1 6 0v12a3 3 0 0 0 6 0V9"/>',
      circle: '<circle cx="12" cy="12" r="9"/>',
      chart: '<path d="M3 3v18h18M7 14v3M12 9v8M17 5v12"/>',
      trendUp: '<path d="m22 7-8.5 8.5-5-5L2 17M16 7h6v6"/>',
      trendDown: '<path d="m22 17-8.5-8.5-5 5L2 7M16 17h6v-6"/>',
      sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2m-3-9-1.5 1.5m-11 11L3 21M3 3l1.5 1.5m15 15L21 21"/>',
      moon: '<path d="M20.9 13A9 9 0 0 1 11 3.1 9 9 0 1 0 20.9 13Z"/>',
      heart: '<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z"/>',
      clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
      key: '<circle cx="8" cy="15" r="5"/><path d="m11.5 11.5 9-9M16 7l3 3M19 4l3 3"/>',
      network: '<rect x="9" y="2" width="6" height="6" rx="1"/><rect x="2" y="16" width="6" height="6" rx="1"/><rect x="16" y="16" width="6" height="6" rx="1"/><path d="M12 8v4M5 16v-4h14v4"/>',
      map: '<path d="m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3zM9 3v15M15 6v15"/>',
      trash: '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/>',
      archive: '<rect x="3" y="3" width="18" height="4" rx="1"/><path d="M5 7v14h14V7M10 11h4"/>',
      siren: '<path d="M7 18v-6a5 5 0 0 1 10 0v6M5 18h14v3H5zM12 2v2M3 7l2 1M21 7l-2 1M5 2l1 2M19 2l-1 2"/>',
      palette: '<circle cx="13.5" cy="6.5" r=".5"/><circle cx="17.5" cy="10.5" r=".5"/><circle cx="8.5" cy="7.5" r=".5"/><circle cx="6.5" cy="12.5" r=".5"/><path d="M12 22a10 10 0 1 1 10-10 4 4 0 0 1-4 4h-2a2 2 0 0 0-2 2 2 2 0 0 1-2 4Z"/>',
      cloud: '<path d="M20 16a4 4 0 0 0-.88-7.9A7 7 0 1 0 4 16Z"/>',
      package: '<path d="m12 3 9 5v8l-9 5-9-5V8zM3 8l9 5 9-5M12 13v8M7.5 5.5l9 5"/>',
      shuffle: '<path d="m18 14 4 4-4 4M18 2l4 4-4 4M2 18h2.5a6 6 0 0 0 5-3l5-6a6 6 0 0 1 5-3H22M2 6h2.5a6 6 0 0 1 5 3l5 6a6 6 0 0 0 5 3H22"/>',
      search: '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
      upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
      info: '<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>',
      fileText: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>',
      settings: '<path d="M20 7h-9"/><path d="M14 17H5"/><circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/>',
      folders: '<path d="M20 17a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3.9a2 2 0 0 1-1.69-.9l-.81-1.2a2 2 0 0 0-1.67-.9H8a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2Z"/><path d="M2 8v11a2 2 0 0 0 2 2h14"/>',
      shieldCheck: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/>',
      folder: '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
      hardDrive: '<line x1="22" y1="12" x2="2" y2="12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><line x1="6" y1="16" x2="6.01" y2="16"/><line x1="10" y1="16" x2="10.01" y2="16"/>',
      clipboard: '<rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M12 11h4"/><path d="M12 16h4"/><path d="M8 11h.01"/><path d="M8 16h.01"/>',
      radio: '<path d="M12 12m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0"/><path d="M16.24 7.76a6 6 0 0 1 0 8.49"/><path d="M7.76 16.24a6 6 0 0 1 0-8.49"/>',
      terminal: '<path d="M4 17l6-6-6-6"/><path d="M12 19h8"/>',
      target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4"/>',
      eye: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>',
      server: '<rect x="3" y="4" width="18" height="7" rx="1"/><rect x="3" y="13" width="18" height="7" rx="1"/><line x1="7" y1="7.5" x2="7" y2="7.5"/><line x1="7" y1="16.5" x2="7" y2="16.5"/>',
      link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
      monitor: '<rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>',
      camera: '<path d="M3 7h11a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H3z"/><path d="M16 9l5-2v10l-5-2"/><circle cx="7" cy="12" r="2"/>',
      switchic: '<circle cx="12" cy="5" r="2"/><circle cx="5" cy="19" r="2"/><circle cx="19" cy="19" r="2"/><line x1="12" y1="7" x2="5" y2="17"/><line x1="12" y1="7" x2="19" y2="17"/>',
      shield: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/>',
      router: '<rect x="2" y="14" width="20" height="7" rx="2"/><line x1="6" y1="17.5" x2="6" y2="17.5"/><line x1="6" y1="4" x2="6" y2="10"/><line x1="18" y1="4" x2="18" y2="10"/><line x1="6" y1="10" x2="18" y2="10"/>',
      alert: '<path d="m12 3 10 18H2Z"/><path d="M12 9v4M12 17h.01"/>',
      flask: '<path d="M9 3h6"/><path d="M10 3v6l-5 9a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-9V3"/>',
      download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M12 3v12m-5-5 5 5 5-5"/>',
      refresh: '<path d="M21 2v6h-6"/><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M3 22v-6h6"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/>',
      play: '<polygon points="6 4 20 12 6 20"/>',
      stop: '<rect x="6" y="6" width="12" height="12" rx="1"/>',
      timer: '<circle cx="12" cy="13" r="8"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="9" y1="2" x2="15" y2="2"/>',
      external: '<path d="M14 3h7v7"/><line x1="10" y1="14" x2="21" y2="3"/><path d="M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5"/>',
      cpu: '<rect x="5" y="5" width="14" height="14" rx="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="1" x2="9" y2="5"/><line x1="15" y1="1" x2="15" y2="5"/><line x1="9" y1="19" x2="9" y2="23"/><line x1="15" y1="19" x2="15" y2="23"/><line x1="1" y1="9" x2="5" y2="9"/><line x1="1" y1="15" x2="5" y2="15"/><line x1="19" y1="9" x2="23" y2="9"/><line x1="19" y1="15" x2="23" y2="15"/>',
      memory: '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M7 10h3v4H7zM14 10h3v4h-3zM7 18v3M12 18v3M17 18v3"/>',
      disk: '<line x1="12" y1="2" x2="12" y2="22"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>',
      lock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
      ban: '<circle cx="12" cy="12" r="9"/><line x1="5.5" y1="5.5" x2="18.5" y2="18.5"/>',
      check: '<path d="m20 6-11 11-5-5"/>',
      close: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
      plus: '<path d="M12 5v14M5 12h14"/>',
      globe: '<circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a17 17 0 0 1 0 20 17 17 0 0 1 0-20"/>',
      activity: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    };
    function icon(name, cls) { return `<svg class="ic${cls ? ' ' + cls : ''}" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${IC[name] || ''}</svg>`; }

    // Only local icon markup is trusted. API/user text is always escaped.
    function iconTextHTML(value) {
      const trusted = new Set(Object.keys(IC).map(name => icon(name)));
      return String(value ?? '').split(/(<svg\b[^>]*>[\s\S]*?<\/svg>)/g)
        .map(part => trusted.has(part) ? part : esc(part).replace(/\u26a0\ufe0f?/g, icon('alert'))).join('');
    }
    function setIconText(element, value) {
      element.innerHTML = iconTextHTML(value);
    }

    // ── THEME ──
    const PALETTES = {
      'dark-indigo': {
        bg: '#08090a', bg2: 'rgba(255,255,255,0.02)', panel: '#0f1011', elevated: '#191a1b',
        text: '#f7f8f8', text2: '#d0d6e0', text3: '#8a8f98', text4: '#62666d',
        accent: '#5e6ad2', accen: '#7170ff', border: 'rgba(255,255,255,0.08)', borders: 'rgba(255,255,255,0.05)',
        surface: 'rgba(255,255,255,0.04)', borderh: 'rgba(255,255,255,0.14)'
      },
      'dark-amber': {
        bg: '#0a0808', bg2: 'rgba(255,255,255,0.02)', panel: '#12100e', elevated: '#1c1917',
        text: '#f7f8f8', text2: '#d6d0c0', text3: '#928a78', text4: '#6b6558',
        accent: '#f59e0b', accen: '#fbbf24', border: 'rgba(255,255,255,0.08)', borders: 'rgba(255,255,255,0.05)',
        surface: 'rgba(255,255,255,0.04)', borderh: 'rgba(255,255,255,0.14)'
      },
      'light-clean': {
        bg: '#f5f6f7', bg2: '#ffffff', panel: '#ffffff', elevated: '#f0f1f2',
        text: '#0f1011', text2: '#374151', text3: '#6b7280', text4: '#9ca3af',
        accent: '#4f46e5', accen: '#4338ca', border: 'rgba(0,0,0,0.1)', borders: 'rgba(0,0,0,0.06)',
        surface: 'rgba(0,0,0,0.04)', borderh: 'rgba(0,0,0,0.18)'
      },
    };
    const PALETTE_NAMES = [['dark-indigo', '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20.9 13A9 9 0 0 1 11 3.1 9 9 0 1 0 20.9 13Z"/></svg> Oscuro Índigo'], ['dark-amber', '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20.9 13A9 9 0 0 1 11 3.1 9 9 0 1 0 20.9 13Z"/></svg> Oscuro Ámbar'], ['light-clean', '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2m-3-9-1.5 1.5m-11 11L3 21M3 3l1.5 1.5m15 15L21 21"/></svg> Claro']];

    function setPalette(name) {
      const p = PALETTES[name]; if (!p) return;
      const root = document.documentElement;
      root.style.setProperty('--bg', p.bg); root.style.setProperty('--bg2', p.bg2);
      root.style.setProperty('--panel', p.panel); root.style.setProperty('--elevated', p.elevated);
      root.style.setProperty('--text', p.text); root.style.setProperty('--text-secondary', p.text2);
      root.style.setProperty('--text-ter', p.text3); root.style.setProperty('--text-muted', p.text4);
      root.style.setProperty('--accent', p.accent); root.style.setProperty('--accent-h', p.accent);
      root.style.setProperty('--border', p.border); root.style.setProperty('--border-s', p.borders);
      root.style.setProperty('--surface', p.surface || 'rgba(255,255,255,0.05)');
      root.style.setProperty('--border-h', p.borderh || 'rgba(255,255,255,0.12)');
      localStorage.setItem('netpulse-palette', name);
      if (document.getElementById('theme-grid')) updateThemeUI();
    }

    function setTheme(color) {
      document.documentElement.style.setProperty('--accent', color);
      document.documentElement.style.setProperty('--accent-h', color);
      document.documentElement.style.setProperty('--accent-l', color + '1f');
      localStorage.setItem('netpulse-accent', color);
      updateThemeUI();
    }

    function resetTheme() {
      localStorage.removeItem('netpulse-palette');
      localStorage.removeItem('netpulse-accent');
      setPalette('dark-indigo');
    }

    function updateThemeUI() {
      const colors = [['#5e6ad2', 'Indigo'], ['#3b82f6', 'Azul'], ['#8b5cf6', 'Violeta'], ['#06b6d4', 'Cian'], ['#10b981', 'Verde'], ['#f59e0b', 'Ámbar'], ['#ef4444', 'Rojo'], ['#ec4899', 'Rosa'], ['#6366f1', 'Índigo claro'], ['#84cc16', 'Lima']];
      const grid = document.getElementById('theme-grid');
      if (grid) grid.innerHTML = colors.map(([c, n]) => `<div class="theme-card" onclick="setTheme('${c}')" style="cursor:pointer;border:2px solid ${localStorage.getItem('netpulse-accent') === c ? 'var(--accent)' : 'var(--border)'};border-radius:var(--radius);padding:16px;text-align:center;transition:all.15s;background:var(--bg2)"><div style="width:40px;height:40px;border-radius:50%;background:${c};margin:0 auto 8px;box-shadow:0 2px 8px ${c}44"></div><div style="font-size:12px;font-weight:500">${n}</div></div>`).join('');
      const saved = localStorage.getItem('netpulse-accent') || '#5e6ad2';
      const el = document.getElementById('theme-preview'); if (el) el.style.background = saved;
      const nm = document.getElementById('theme-name'); if (nm) nm.textContent = colors.find(([c]) => c === saved)?.[1] || 'Personalizado';
      const hx = document.getElementById('theme-hex'); if (hx) hx.textContent = saved;
      const pal = localStorage.getItem('netpulse-palette') || 'dark-indigo';
      const iv = localStorage.getItem('netpulse-refresh-interval') || '15';
      const sel = document.getElementById('refresh-interval'); if (sel) sel.value = iv;
      const cr = document.getElementById('refresh-current'); if (cr) cr.textContent = iv + 's';
      const pg = document.getElementById('palette-grid');
      if (pg) pg.innerHTML = PALETTE_NAMES.map(([k, n]) => `<div class="theme-card${pal === k ? ' active' : ''}" onclick="setPalette('${k}')" style="cursor:pointer;border:2px solid ${pal === k ? 'var(--accent)' : 'var(--border)'};border-radius:var(--radius);padding:16px;text-align:center;background:var(--bg2)">${n}</div>`).join('');
    }

    function setRefreshInterval(secs) {
      const n = parseInt(secs);
      localStorage.setItem('netpulse-refresh-interval', secs);
      const cr = document.getElementById('refresh-current'); if (cr) cr.textContent = secs + 's';
      if (window._rInt) { clearInterval(window._rInt); window._rInt = null }
      if (n > 0) window._rInt = setInterval(() => {
        refMon();
        if (document.getElementById('v-monitoring')?.classList.contains('active')) loadMonitoring();
      }, n * 1000);
    }

    // Load saved theme on page load
    (function () {
      const p = localStorage.getItem('netpulse-palette'); if (p) setPalette(p);
      const c = localStorage.getItem('netpulse-accent'); if (c) document.documentElement.style.setProperty('--accent', c)
    })();

    let loginBusy = false;
    async function login() {
      if (loginBusy) return;
      const u = document.getElementById('user').value.trim(), p = document.getElementById('pass').value;
      const button = document.getElementById('login-submit');
      const error = document.getElementById('login-error');
      error.textContent = '';
      error.style.display = 'none';
      loginBusy = true;
      button.disabled = true;
      button.textContent = __t('login_busy');
      try {
        const r = await fetch(`${A}/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: u, password: p }) });
        if (!r.ok) {
          if (r.status === 401) showLoginError(__t('login_err_401'));
          else if (r.status === 429) showLoginError(__t('login_err_429'));
          else showLoginError(__t('login_err_generic'));
          return;
        }
        const d = await r.json(); t = d.access_token;
        localStorage.setItem('np_token', t); // F5 no desloguea: ver restoreSession()
        const mr = await fetch(`${A}/auth/me`, { headers: { Authorization: `Bearer ${t}` } });
        if (!mr.ok) { t = ''; localStorage.removeItem('np_token'); showLoginError(__t('login_err_session')); return; }
        const me = await mr.json();
        enterSession(me, u);
      } catch (e) { t = ''; localStorage.removeItem('np_token'); showLoginError(__t('login_err_conn')); }
      finally { loginBusy = false; button.disabled = false; button.textContent = __t('login_submit'); }
    }
    function toggleUserMenu(ev) {
      ev.stopPropagation();
      document.getElementById('user-dd').classList.toggle('open');
    }
    document.addEventListener('click', () => {
      const dd = document.getElementById('user-dd');
      if (dd) dd.classList.remove('open');
    });

    // ── RESTAURAR SESIÓN AL CARGAR LA PÁGINA ──
    // El cuerpo vive en core.js (window.restoreSession); la llamada queda en el
    // punto exacto donde estaba la IIFE para no adelantar el fetch de /auth/me
    // respecto del comportamiento original.
    restoreSession();
    function togSide() {
      // En escritorio (>=1280px) el rail siempre es visible; el toggle solo tiene sentido en pantallas medias.
      if (window.innerWidth >= 1280) return;
      const open = document.getElementById('sidebar').classList.toggle('open');
      document.getElementById('sidebar-overlay').classList.toggle('active', open);
      const menu = document.querySelector('.menu-btn');
      menu.setAttribute('aria-expanded', String(open));
      if (open) document.querySelector('.sidebar-item.active')?.focus();
      else menu.focus();
    }

    // ── EXPORT DATA ──
    function npExport(kind) {
      const url = `${A}/export/${kind}`;
      fetch(url, { headers: h() })
        .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.blob(); })
        .then(blob => {
          const a = document.createElement('a');
          a.href = URL.createObjectURL(blob);
          a.download = kind === 'configs' ? 'netpulse_configs.zip' : `netpulse_inventory.${kind}`;
          a.click();
          URL.revokeObjectURL(a.href);
          toast('<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M12 3v12m-5-5 5 5 5-5"/></svg> Export iniciado', 's');
        })
        .catch(e => toast('<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> Error al exportar: ' + e.message, 'error'));
    }

