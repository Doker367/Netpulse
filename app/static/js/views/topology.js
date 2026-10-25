/**
 * NetPulse NOC — Topología (vista #v-topology, Tarjeta 10D-J / Lote J).
 *
 * Extraída del <script> inline de app/static/dashboard.html SIN cambio de
 * comportamiento: el bloque viaja CONTIGUO y byte a byte en el mismo orden
 * relativo que en el monolito (comentario TOPOLOGY < estado `_topo*` < `_t` <
 * `topoTab` < `buildTraceOrigins` < `loadTopology` < `_topoScreen2World` <
 * `_topoResolveEdges` < `startTopoGraph` —con `step`/`draw`/`loop` anidadas— <
 * `ensureTopoMap` < `updateTopoMapDevices` < `redrawTopoTrace` <
 * `runTopoTrace` < `clearTopoTrace` < `renderTraceResult` < listener `resize` <
 * bloque `HEALTH SCORES` con la función muerta `loadHealth`).
 *
 * Leaflet es un vendor global (`<script src="/static/vendor/leaflet.js">` en el
 * <head>): `L` se usa tal cual y el mapa/las capas (`_topoMap`, `_topoDevLayer`,
 * `_topoTraceLayer`, `_topoTraceData`) viajan con su `let` a este archivo. No se
 * tocó el vendor ni `leaflet.css`. El canvas (simulación con
 * `_topoGraph`/`_topoGraphRAF` y handlers `L.*`/DOM del canvas) también viaja
 * íntegro, sin cancelar el rAF al cambiar de pestaña o de vista (defecto
 * preexistente, §7.12/§7.9 del mapa, conservado).
 *
 * Estado: `_topoData`, `_topoTab`, `_topoGraph`, `_topoGraphRAF`, `_topoMap`,
 * `_topoDevLayer`, `_topoTraceLayer`, `_topoTraceData` son `let` de módulo
 * (script clásico: binding del entorno léxico global compartido). Grep: sus
 * únicos consumidores son funciones de este mismo archivo; ningún atributo
 * `on*` ni otro .js los nombra, así que no se pasaron a `window.x` (regla 4).
 * `_t(k)` es el alias local de i18n que usaba topología (y el `renderCmd`
 * muerto, ya movido en el Lote H): tras el grep su único consumidor es este
 * archivo, por eso viaja. `swView` (inline, lote k) solo llama `loadTopology()`
 * en tiempo de llamada.
 *
 * `loadHealth` es código muerto del bloque antiguo de Health Scores (mapa §2.3:
 * no borrar sin tarjeta aparte). Viaja tal cual con el tramo contiguo, como
 * pedía el plan del Lote I; NO se revivió: su `#health-list` no existe en el
 * markup y nadie la llama (grep: solo su declaración).
 *
 * Lo que NO viajó (grep de exclusividad): `swView` + `hashchange` (router,
 * lote k), el listener global de `Escape` que cierra `#user-dd` (misc del
 * inline; NO es de topología), `esc`, `setIconText`, `icon`, `A`, `h`, `t`,
 * `__t`, `I18N`/`currentLang` (core.js) y `devices`/estado compartido. Todo se
 * resuelve en tiempo de llamada contra el entorno léxico global / window.
 *
 * Handlers por window (markup de #v-topology y strings generados): `topoTab`,
 * `loadTopology`, `runTopoTrace`, `clearTopoTrace`. Script clásico sin
 * defer/async, cargado tras views/sftp.js y antes del inline residual; el
 * top-level es estado + declaraciones `function` + el listener `resize` (el DOM
 * ya está parseado porque el script va al final del body, como en el monolito).
 */

    // ── TOPOLOGY (grafo canvas + mapa Leaflet + tabla + traceroute) ──
    let _topoData = { nodes: [], links: [] };
    let _topoTab = 'graph';
    let _topoGraph = null;        // estado de simulación del canvas
    let _topoGraphRAF = null;
    let _topoMap = null;          // Leaflet (lazy)
    let _topoDevLayer = null;
    let _topoTraceLayer = null;
    let _topoTraceData = null;    // último traceroute ejecutado

    function _t(k) {
      return (I18N[currentLang] || I18N.en)[k] || k;
    }

    function topoTab(tv) {
      _topoTab = tv;
      const show = id => { const el = document.getElementById(id); if (el) el.style.display = ''; };
      const hide = id => { const el = document.getElementById(id); if (el) el.style.display = 'none'; };
      if (tv === 'graph') { show('topo-view-graph'); hide('topo-view-map'); hide('topo-view-table'); }
      if (tv === 'map') { hide('topo-view-graph'); show('topo-view-map'); hide('topo-view-table'); }
      if (tv === 'table') { hide('topo-view-graph'); hide('topo-view-map'); show('topo-view-table'); }
      ['graph', 'map', 'table'].forEach(x => {
        const b = document.getElementById(`topo-tab-${x}`);
        if (!b) return;
        const on = x === tv;
        b.style.background = on ? 'var(--accent-l)' : 'transparent';
        b.style.color = on ? 'var(--accent)' : 'var(--text-secondary)';
        b.style.fontWeight = on ? '700' : '500';
      });
      if (tv === 'graph') requestAnimationFrame(startTopoGraph);
      if (tv === 'map') requestAnimationFrame(() => { ensureTopoMap(); updateTopoMapDevices(); redrawTopoTrace(); });
    }

    function buildTraceOrigins() {
      const sel = document.getElementById('topo-trace-origin');
      if (!sel) return;
      const cur = sel.value;
      sel.innerHTML =
        `<option value="noc">Servidor NetPulse</option>` +
        ((_topoData.nodes || []).map(n =>
          `<option value="${esc(n.id)}">${esc(n.id)}${n.ip ? ` (${esc(n.ip)})` : ''}</option>`).join(''));
      if (cur && [...sel.options].some(o => o.value === cur)) sel.value = cur;
    }

    async function loadTopology() {
      try {
        const r = await fetch(`${A}/topology/map`, { headers: h() });
        if (!r.ok) return;
        const d = await r.json();
        _topoData = d;
        const nodes = d.nodes || [], links = d.links || [];

        document.getElementById('topo-stats').innerHTML =
          `<div class="sc" style="display:flex;align-items:center;gap:16px;padding:16px 20px;">
            <div style="background:var(--blue-bg);border-radius:12px;display:flex;align-items:center;justify-content:center;width:48px;height:48px;font-size:20px;flex-shrink:0"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="9" y="2" width="6" height="6" rx="1"/><rect x="2" y="16" width="6" height="6" rx="1"/><rect x="16" y="16" width="6" height="6" rx="1"/><path d="M12 8v4M5 16v-4h14v4"/></svg></div>
            <div style="min-width:0;flex:1">
              <div class="sl" style="font-size:11px;color:var(--text-muted);margin-bottom:2px;text-transform:uppercase;letter-spacing:0.5px">Nodos de Red</div>
              <div class="sv" style="font-size:24px;font-weight:700">${nodes.length}</div>
            </div>
          </div>
          <div class="sc" style="display:flex;align-items:center;gap:16px;padding:16px 20px;">
            <div style="background:var(--green-bg);border-radius:12px;display:flex;align-items:center;justify-content:center;width:48px;height:48px;font-size:20px;flex-shrink:0"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg></div>
            <div style="min-width:0;flex:1">
              <div class="sl" style="font-size:11px;color:var(--text-muted);margin-bottom:2px;text-transform:uppercase;letter-spacing:0.5px">Enlaces Detectados</div>
              <div class="sv" style="font-size:24px;font-weight:700">${links.length}</div>
            </div>
          </div>
          <div class="sc" style="display:flex;align-items:center;gap:16px;padding:16px 20px;">
            <div style="background:var(--accent-l);border-radius:12px;display:flex;align-items:center;justify-content:center;width:48px;height:48px;font-size:20px;flex-shrink:0"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/></svg></div>
            <div style="min-width:0;flex:1">
              <div class="sl" style="font-size:11px;color:var(--text-muted);margin-bottom:2px;text-transform:uppercase;letter-spacing:0.5px">Geo-localizados</div>
              <div class="sv" style="font-size:24px;font-weight:700">${nodes.filter(n => n.geo).length}</div>
            </div>
          </div>`;

        document.getElementById('topo-nodes').innerHTML = nodes.map(n =>
          `<div class="dc" style="gap:12px;cursor:default">
            <div style="display:flex;align-items:center;gap:12px;">
              <div style="background:var(--bg);width:32px;height:32px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:16px;border:1px solid var(--border-s)"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg></div>
              <div>
                <div style="font-weight:700;font-size:14px;color:var(--text);line-height:1.2">${esc(n.id)}</div>
                <div style="font-size:11px;color:var(--text-ter);font-family:var(--mono);margin-top:2px">${esc(n.type)}</div>
              </div>
            </div>
            <div style="display:flex;gap:8px;margin-top:4px;flex-wrap:wrap">
              <span class="badge" style="background:var(--surface);color:var(--text-secondary);font-size:10px">${esc(n.driver).toUpperCase()}</span>
              ${n.group ? `<span class="badge" style="background:var(--accent-l);color:var(--accent);font-size:10px">${esc(n.group)}</span>` : ''}
              ${n.geo ? `<span class="badge" style="background:var(--green-bg);color:var(--green);font-size:10px"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/></svg> ${esc(n.geo.city || n.geo.country || '')}</span>` : ''}
              ${n.ip ? `<span class="badge" style="background:var(--surface);color:var(--text-secondary);font-size:10px;font-family:var(--mono)">${esc(n.ip)}</span>` : ''}
            </div>
          </div>`).join('') || '<div class="empty" style="padding:40px;background:var(--elevated);border-radius:var(--radius-lg);border:1px dashed var(--border)">Sin dispositivos en la topología</div>';

        document.getElementById('topo-links-list').innerHTML = links.length ?
          `<table style="width:100%;border-collapse:collapse;text-align:left">
            <thead style="background:var(--surface);border-bottom:1px solid var(--border)">
              <tr>
                <th style="padding:12px 16px;font-size:11px;color:var(--text-muted);font-weight:600;text-transform:uppercase">Origen (Local)</th>
                <th style="padding:12px 16px;font-size:11px;color:var(--text-muted);font-weight:600;text-transform:uppercase">Puerto Local</th>
                <th style="padding:12px 16px;font-size:11px;color:var(--text-muted);font-weight:600;text-transform:uppercase"></th>
                <th style="padding:12px 16px;font-size:11px;color:var(--text-muted);font-weight:600;text-transform:uppercase">Destino (Remoto)</th>
                <th style="padding:12px 16px;font-size:11px;color:var(--text-muted);font-weight:600;text-transform:uppercase">Puerto Remoto</th>
              </tr>
            </thead>
            <tbody>
              ${links.map(l => `
              <tr style="border-bottom:1px solid var(--border-s);transition:background 0.2s" onmouseover="this.style.background='var(--surface-hover)'" onmouseout="this.style.background='transparent'">
                <td style="padding:12px 16px;font-size:13px;font-weight:600;color:var(--text)">${esc(l.source)}</td>
                <td style="padding:12px 16px;font-size:12px;color:var(--text-secondary);font-family:var(--mono)">${esc(l.local_port)}</td>
                <td style="padding:12px 16px;font-size:14px;color:var(--text-ter);text-align:center"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M5 12h14m-6-6 6 6-6 6"/></svg></td>
                <td style="padding:12px 16px;font-size:13px;font-weight:600;color:var(--text)">${esc(l.target)}</td>
                <td style="padding:12px 16px;font-size:12px;color:var(--text-secondary);font-family:var(--mono)">${esc(l.remote_port)}</td>
              </tr>`).join('')}
            </tbody>
          </table>` : '<div class="empty" style="padding:40px">Sin enlaces LLDP/CDP detectados</div>';

        buildTraceOrigins();
        topoTab(_topoTab);
        if (!_topoTraceData) renderTraceResult(null);
      } catch (e) { }
    }

    // ── Grafo interactivo (canvas, física, drag + zoom + hover) ──
    function _topoScreen2World(cv, ev) {
      const g = _topoGraph, rect = cv.getBoundingClientRect();
      const x = ev.clientX - rect.left, y = ev.clientY - rect.top;
      return { x: (x - g.view.x) / g.view.z, y: (y - g.view.y) / g.view.z };
    }

    function _topoResolveEdges() {
      const ids = new Map((_topoData.nodes || []).map(n => [String(n.id).toLowerCase(), n.id]));
      const resolve = name => {
        if (!name) return null;
        const s = String(name).toLowerCase();
        if (ids.has(s)) return ids.get(s);
        for (const [k, v] of ids) { if (k.includes(s) || s.includes(k)) return v; }
        return null;
      };
      const list = [];
      const seen = new Set();
      for (const l of _topoData.links || []) {
        const a = resolve(l.source), b = resolve(l.target);
        if (!a || !b || a === b) continue;
        const key = [a, b].sort().join('~');
        if (seen.has(key)) continue;
        seen.add(key);
        list.push({ a, b, label: `${l.local_port || ''} → ${l.remote_port || ''}` });
      }
      return list;
    }

    function startTopoGraph() {
      const cv = document.getElementById('topo-canvas');
      if (!cv || _topoTab !== 'graph') return;
      if (_topoGraphRAF) cancelAnimationFrame(_topoGraphRAF);
      const dpr = window.devicePixelRatio || 1;
      cv.width = cv.clientWidth * dpr;
      cv.height = cv.clientHeight * dpr;
      const gw = cv.clientWidth, gh = cv.clientHeight;
      const ctx = cv.getContext('2d');
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const cs = getComputedStyle(document.documentElement);
      const cAccent = (cs.getPropertyValue('--accent') || '').trim() || '#3b82f6';
      const cText = (cs.getPropertyValue('--text') || '').trim() || '#e2e8f0';
      const cMut = (cs.getPropertyValue('--text-ter') || '').trim() || '#64748b';

      const edgesRaw = _topoResolveEdges();
      const known = (_topoData.nodes || []).map(n => ({
        id: n.id, type: n.type || 'router', group: n.group || '', driver: n.driver || '?',
        ip: n.ip || '', geo: n.geo, phantom: false,
      }));
      const knownSet = new Set(known.map(k => String(k.id)));
      // Vecinos LLDP que no están en el inventario → nodos "fantasma" de referencia
      for (const e of edgesRaw) {
        for (const end of [e.a, e.b]) {
          if (!knownSet.has(end)) {
            knownSet.add(end);
            known.push({ id: end, type: 'neighbor', group: '', driver: 'lldp', ip: '', geo: null, phantom: true });
          }
        }
      }
      if (!known.length) {
        ctx.clearRect(0, 0, gw, gh);
        ctx.fillStyle = cMut;
        ctx.font = '13px system-ui';
        ctx.textAlign = 'center';
        ctx.fillText('Sin dispositivos en la topología', gw / 2, gh / 2);
        return;
      }

      const cx = gw / 2, cy = gh / 2, R = Math.min(gw, gh) * 0.34;
      const ns = known.map((n, i) => {
        const ang = (i / Math.max(1, known.length)) * Math.PI * 2;
        return { ...n, x: cx + Math.cos(ang) * R, y: cy + Math.sin(ang) * R, vx: 0, vy: 0 };
      });
      const byId = new Map(ns.map(n => [String(n.id), n]));
      const edges = edgesRaw.map(e => ({ ...e, ea: byId.get(e.a), eb: byId.get(e.b) }));
      const palette = ['#3b82f6', '#22c55e', '#f59e0b', '#a855f7', '#ec4899', '#06b6d4', '#ef4444', '#84cc16'];
      const groupColors = {};
      let gi = 0;
      for (const n of ns) {
        const g = n.group || n.type || 'net';
        if (!(g in groupColors)) groupColors[g] = palette[gi++ % palette.length];
      }
      _topoGraph = { ns, edges, view: { x: 0, y: 0, z: 1 }, hover: null, drag: null, gw, gh, cx, cy };
      const g = _topoGraph;

      function step() {
        // Repulsión O(n²) — inventarios NOC chicos
        for (let i = 0; i < g.ns.length; i++) {
          const na = g.ns[i];
          for (let j = i + 1; j < g.ns.length; j++) {
            const nb = g.ns[j];
            let dx = nb.x - na.x, dy = nb.y - na.y;
            const d2 = dx * dx + dy * dy || 1;
            if (d2 < 200000) {
              const f = 5000 / d2, d = Math.sqrt(d2);
              dx /= d; dy /= d;
              na.vx -= dx * f; na.vy -= dy * f;
              nb.vx += dx * f; nb.vy += dy * f;
            }
          }
          na.vx += (g.cx - na.x) * 0.0035; na.vy += (g.cy - na.y) * 0.0035;
        }
        for (const e of g.edges) {
          const dx = e.eb.x - e.ea.x, dy = e.eb.y - e.ea.y;
          const d = Math.max(1, Math.hypot(dx, dy));
          const f = (d - 130) * 0.02;
          const ux = dx / d, uy = dy / d;
          e.ea.vx += ux * f; e.ea.vy += uy * f;
          e.eb.vx -= ux * f; e.eb.vy -= uy * f;
        }
        for (const n of g.ns) {
          if (n === g.drag) { n.vx = n.vy = 0; continue; }
          n.vx *= 0.85; n.vy *= 0.85;
          n.x += Math.max(-6, Math.min(6, n.vx));
          n.y += Math.max(-6, Math.min(6, n.vy));
        }
      }

      function draw() {
        ctx.clearRect(0, 0, gw, gh);
        ctx.save();
        ctx.translate(g.view.x, g.view.y);
        ctx.scale(g.view.z, g.view.z);
        // Enlaces
        for (const e of g.edges) {
          const hl = g.hover && (g.hover === e.ea || g.hover === e.eb);
          ctx.beginPath();
          ctx.moveTo(e.ea.x, e.ea.y);
          ctx.lineTo(e.eb.x, e.eb.y);
          ctx.strokeStyle = hl ? cAccent : 'rgba(148,163,184,0.35)';
          ctx.lineWidth = hl ? 2.2 : 1.2;
          ctx.stroke();
          if (hl && g.view.z > 0.5) {
            const mx = (e.ea.x + e.eb.x) / 2, my = (e.ea.y + e.eb.y) / 2;
            const label = e.label, pad = 4;
            ctx.font = '10px var(--mono), ui-monospace, monospace';
            const w = ctx.measureText(label).width;
            ctx.fillStyle = 'rgba(10,14,20,0.85)';
            ctx.fillRect(mx - w / 2 - pad, my - 9, w + pad * 2, 15);
            ctx.fillStyle = cText;
            ctx.textAlign = 'center';
            ctx.fillText(label, mx, my + 2);
          }
        }
        // Nodos
        for (const n of g.ns) {
          const r = n.phantom ? 9 : 15;
          ctx.beginPath();
          ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
          ctx.fillStyle = n.phantom ? 'rgba(100,116,139,0.35)' : (groupColors[n.group] || groupColors[n.type] || cAccent);
          ctx.globalAlpha = n === g.hover ? 1 : 0.92;
          ctx.fill();
          ctx.globalAlpha = 1;
          if (n === g.hover) {
            ctx.strokeStyle = '#ffffff';
            ctx.lineWidth = 2;
            ctx.stroke();
          }
          ctx.beginPath();
          ctx.arc(n.x, n.y, r - 4.5, 0, Math.PI * 2);
          ctx.fillStyle = 'rgba(8,12,18,0.55)';
          ctx.fill();
          ctx.font = `${n.phantom ? 9 : 11}px system-ui`;
          ctx.textAlign = 'center';
          ctx.fillStyle = g.hover === n ? cText : (n.phantom ? cMut : 'rgba(226,232,240,0.85)');
          ctx.fillText(String(n.id), n.x, n.y + r + 12);
        }
        // Tooltip fijo
        if (g.hover) {
          const n = g.hover;
          const lines = [
            `${n.id}`,
            `type: ${n.type}   group: ${n.group || '—'}`,
            `driver: ${n.driver.toUpperCase()}${n.ip ? `   ip: ${n.ip}` : ''}`,
            `${n.geo ? `${n.geo.city || ''}${n.geo.city && n.geo.country ? ', ' : ''}${n.geo.country || ''}` : ''}`,
          ].filter(Boolean);
          const bx = 14, by = 14, lw = Math.max(...lines.map(t => ctx.measureText(t).width));
          ctx.fillStyle = 'rgba(10,14,20,0.92)';
          ctx.fillRect(bx, by, lw + 20, lines.length * 15 + 10);
          ctx.strokeStyle = 'rgba(148,163,184,0.4)';
          ctx.lineWidth = 1;
          ctx.strokeRect(bx, by, lw + 20, lines.length * 15 + 10);
          ctx.textAlign = 'left';
          ctx.font = '11px system-ui';
          lines.forEach((t, i) => {
            ctx.fillStyle = i === 0 ? cAccent : cText;
            ctx.fillText(t, bx + 10, by + 19 + i * 15);
          });
        }
        ctx.restore();
      }

      function loop() {
        if (_topoGraph !== g) return;
        step();
        draw();
        _topoGraphRAF = requestAnimationFrame(loop);
      }
      loop();

      cv.onmousedown = ev => {
        const p = _topoScreen2World(cv, ev);
        const hit = g.ns.find(n => Math.hypot(n.x - p.x, n.y - p.y) <= 18);
        if (hit) { g.drag = hit; cv.style.cursor = 'grabbing'; }
      };
      cv.onmousemove = ev => {
        const p = _topoScreen2World(cv, ev);
        if (g.drag) { g.drag.x = p.x; g.drag.y = p.y; g.drag.vx = 0; g.drag.vy = 0; return; }
        const hit = g.ns.find(n => Math.hypot(n.x - p.x, n.y - p.y) <= 18);
        g.hover = hit || null;
        cv.style.cursor = hit ? 'pointer' : 'grab';
      };
      const endDrag = () => { g.drag = null; cv.style.cursor = 'grab'; };
      cv.onmouseup = endDrag;
      cv.onmouseleave = () => { g.hover = null; endDrag(); };
      cv.onwheel = ev => {
        ev.preventDefault();
        const rect = cv.getBoundingClientRect();
        const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
        const z0 = g.view.z;
        const z = Math.max(0.35, Math.min(3.2, z0 * (ev.deltaY < 0 ? 1.12 : 1 / 1.12)));
        g.view.x = mx - ((mx - g.view.x) * z) / z0;
        g.view.y = my - ((my - g.view.y) * z) / z0;
        g.view.z = z;
      };
      cv.ondblclick = () => { g.view = { x: 0, y: 0, z: 1 }; };
    }

    // ── Mapa Leaflet ─────────────────────────────────────────
    function ensureTopoMap() {
      if (_topoMap) { _topoMap.invalidateSize(); return; }
      const el = document.getElementById('topo-map');
      if (!el || typeof L === 'undefined') return;
      _topoMap = L.map(el, { worldCopyJump: true }).setView([20, 0], 2);
      L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: '© OpenStreetMap',
      }).addTo(_topoMap);
      _topoDevLayer = L.layerGroup().addTo(_topoMap);
      _topoTraceLayer = L.layerGroup().addTo(_topoMap);
    }

    function updateTopoMapDevices() {
      if (!_topoMap || !_topoDevLayer) return;
      _topoDevLayer.clearLayers();
      let withoutGeo = 0;
      for (const n of _topoData.nodes || []) {
        if (n.geo && n.geo.lat && n.geo.lon) {
          L.circleMarker([n.geo.lat, n.geo.lon], {
            radius: 7, color: '#22c55e', weight: 2, fillColor: '#14532d', fillOpacity: 0.85,
          }).bindPopup(
            `<b><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg> ${esc(n.id)}</b><br>${esc(n.ip || '')}<br><span style="color:#22c55e"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/></svg> ${esc(n.geo.city || '')}${n.geo.city && n.geo.country ? ', ' : ''}${esc(n.geo.country || '')}</span>`
          ).addTo(_topoDevLayer);
        } else {
          withoutGeo++;
        }
      }
      const legend = document.getElementById('topo-map-legend');
      if (legend) {
        const geo = _topoTraceData;
        legend.innerHTML =
          `<span style="color:#22c55e">●</span> Equipos NetPulse geolocalizados — ` +
          `<span style="color:#3b82f6"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/></svg></span> Saltos del${geo ? ' último' : ''} traceroute` +
          `${withoutGeo ? `<br><span style="color:var(--text-ter)">${esc(_t('topo_nodes_no_geo'))} ${withoutGeo} (${esc(_t('topo_geo_off'))})</span>` : ''}`;
      }
    }

    function redrawTopoTrace() {
      if (!_topoMap || !_topoTraceLayer) return;
      _topoTraceLayer.clearLayers();
      const hops = (_topoTraceData && _topoTraceData.hops) || [];
      const ll = [];
      for (const h of hops) {
        if (h.geo && h.geo.lat && h.geo.lon) {
          const pos = [h.geo.lat, h.geo.lon];
          ll.push(pos);
          L.circleMarker(pos, {
            radius: 9, color: '#3b82f6', weight: 2.5, fillColor: '#1d4ed8', fillOpacity: 0.9,
          }).bindPopup(
            `<b>Salto ${h.hop}</b><br>${esc(h.hostname || h.ip || '?')}<br>` +
            `${esc(h.ip || '')}${h.rtt_ms != null ? ` · ${h.rtt_ms} ms` : ''}<br>` +
            `<span style="color:#3b82f6"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/></svg> ${esc(h.geo.city || '')}${h.geo.city && h.geo.country ? ', ' : ''}${esc(h.geo.country || '')}</span>` +
            `${h.device_id ? `<br><b style="color:#22c55e">Equipo NetPulse: ${esc(h.device_id)}</b>` : ''}`
          ).bindTooltip(`#${h.hop} ${h.hostname || h.ip || '*'}`, { direction: 'top' }).addTo(_topoTraceLayer);
        }
      }
      if (ll.length >= 2) {
        L.polyline(ll, { color: '#3b82f6', weight: 2.5, dashArray: '7 7', opacity: 0.8 }).addTo(_topoTraceLayer);
        _topoMap.fitBounds(L.latLngBounds(ll).pad(0.35), { maxZoom: 10 });
      } else if (_topoData.nodes.some(n => n.geo)) {
        const b = L.latLngBounds((_topoData.nodes.filter(n => n.geo)).map(n => [n.geo.lat, n.geo.lon]));
        _topoMap.fitBounds(b.pad(0.4), { maxZoom: 8 });
      }
    }

    // ── Traceroute interactivo ───────────────────────────────
    async function runTopoTrace() {
      const destEl = document.getElementById('topo-trace-dest');
      const origin = (document.getElementById('topo-trace-origin') || {}).value || 'noc';
      const dest = (destEl.value || '').trim();
      const btn = document.getElementById('topo-trace-btn');
      const msg = document.getElementById('topo-trace-msg');
      if (!dest) {
        setIconText(msg, '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m12 3 10 18H2Z"/><path d="M12 9v4M12 17h.01"/></svg> ' + _t('topo_tr_dest'));
        return;
      }
      btn.disabled = true;
      setIconText(msg, '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M5 2h14M5 22h14M7 2v4l10 12v4M17 2v4L7 18v4"/></svg> ' + _t('topo_tr_running'));
      document.getElementById('topo-trace-result').innerHTML = '';
      try {
        const r = await fetch(`${A}/topology/traceroute`, {
          method: 'POST',
          headers: { ...h(), 'Content-Type': 'application/json' },
          body: JSON.stringify({ origin, destination: dest }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) {
          const det = typeof d.detail === 'string' ? d.detail : (d.detail && JSON.stringify(d.detail)) || `HTTP ${r.status}`;
          throw new Error(det);
        }
        _topoTraceData = d;
        setIconText(msg, `<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg> ${d.hops.length} salto(s) · ${_t('topo_tr_from')} ${esc(d.origin.label)} → ${esc(dest)}`);
        renderTraceResult(d);
        topoTab('map');
        updateTopoMapDevices();
        redrawTopoTrace();
      } catch (e) {
        setIconText(msg, '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> ' + _t('topo_tr_err_pre') + e.message);
      } finally {
        btn.disabled = false;
      }
    }

    function clearTopoTrace() {
      _topoTraceData = null;
      document.getElementById('topo-trace-result').innerHTML = '';
      document.getElementById('topo-trace-msg').textContent = '';
      if (_topoTraceLayer) _topoTraceLayer.clearLayers();
      updateTopoMapDevices();
      renderTraceResult(null);
    }

    function renderTraceResult(d) {
      const box = document.getElementById('topo-trace-result');
      if (!box) return;
      if (!d) {
        box.innerHTML = `<div class="empty" style="padding:20px;background:var(--elevated);border-radius:var(--radius-lg);border:1px dashed var(--border)">${esc(_t('topo_tr_empty'))}</div>`;
        return;
      }
      const rows = d.hops.map(h => `
        <tr style="border-bottom:1px solid var(--border-s)">
          <td style="padding:8px 12px;font-size:12px;font-family:var(--mono);color:var(--text-ter)">${h.hop}</td>
          <td style="padding:8px 12px;font-size:13px;font-weight:600;color:var(--text)">${h.hostname ? esc(h.hostname) : h.ip ? esc(h.ip) : icon('timer') + ' *'}</td>
          <td style="padding:8px 12px;font-size:12px;color:var(--text-secondary);font-family:var(--mono)">${esc(h.ip || '—')}</td>
          <td style="padding:8px 12px;font-size:12px;color:var(--text-secondary);font-family:var(--mono)">${h.rtt_ms != null ? h.rtt_ms + ' ms' : '—'}</td>
          <td style="padding:8px 12px;font-size:12px;color:var(--text-secondary)">${h.geo ? `<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/></svg> ${esc(h.geo.city || '')}${h.geo.city && h.geo.country ? ', ' : ''}${esc(h.geo.country || '')}` : `<span title="${esc(_t('topo_tr_private'))}"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a17 17 0 0 1 0 20 17 17 0 0 1 0-20"/></svg> <svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></span>`}</td>
          <td style="padding:8px 12px;font-size:12px">${h.device_id ? `<span class="badge" style="background:var(--green-bg);color:var(--green)">${esc(h.device_id)}</span>` : ''}</td>
        </tr>`).join('');
      box.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;gap:8px;flex-wrap:wrap">
          <h3 style="font-size:11px;color:var(--text-ter)">
            SALTO POR SALTO · ${esc(d.origin.label)} <svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M5 12h14m-6-6 6 6-6 6"/></svg> ${esc(d.destination)}
          </h3>
          <button class="btn btn-o btn-sm" onclick="clearTopoTrace()">${esc(_t('topo_tr_clear'))}</button>
        </div>
        ${d.geoip_available === false ? `<div style="font-size:11px;color:var(--text-ter);margin-bottom:8px"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg> ${esc(_t('topo_geo_off'))}</div>` : ''}
        <table style="width:100%;border-collapse:collapse;text-align:left;border:1px solid var(--border-s);border-radius:8px">
          <thead style="background:var(--surface);border-bottom:1px solid var(--border)">
            <tr>
              <th style="padding:10px 12px;font-size:11px;color:var(--text-muted);font-weight:600;text-transform:uppercase">${esc(_t('topo_th_hop'))}</th>
              <th style="padding:10px 12px;font-size:11px;color:var(--text-muted);font-weight:600;text-transform:uppercase">${esc(_t('topo_th_host'))}</th>
              <th style="padding:10px 12px;font-size:11px;color:var(--text-muted);font-weight:600;text-transform:uppercase">${esc(_t('topo_th_ip'))}</th>
              <th style="padding:10px 12px;font-size:11px;color:var(--text-muted);font-weight:600;text-transform:uppercase">${esc(_t('topo_th_rtt'))}</th>
              <th style="padding:10px 12px;font-size:11px;color:var(--text-muted);font-weight:600;text-transform:uppercase">${esc(_t('topo_th_geo'))}</th>
              <th style="padding:10px 12px;font-size:11px;color:var(--text-muted);font-weight:600;text-transform:uppercase">${esc(_t('topo_th_dev'))}</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
        ${d.raw_output ? `<details style="margin-top:8px"><summary style="font-size:11px;color:var(--text-ter);cursor:pointer">Salida raw</summary><pre style="font-size:11px;color:var(--text-ter);white-space:pre-wrap;max-height:220px;overflow:auto;background:var(--bg);padding:10px;border-radius:8px">${esc(d.raw_output)}</pre></details>` : ''}`;
    }

    window.addEventListener('resize', () => {
      if (_topoTab === 'graph' && _topoGraph) startTopoGraph();
      if (_topoMap) _topoMap.invalidateSize();
    });

    // ── HEALTH SCORES ─────────────────────────────────────────
    async function loadHealth() {
      try {
        const r = await fetch(`${A}/ops/health/all`, { headers: h() });
        if (!r.ok) return;
        const rows = await r.json();
        document.getElementById('health-list').innerHTML =
          `<table><tr><th>Dispositivo</th><th>Score</th><th>CPU</th><th>Mem</th><th>Disco</th><th>Estado</th></tr>` +
          (rows || []).map(x => {
            const color = x.score >= 80 ? 'var(--green)' : x.score >= 50 ? 'var(--yellow)' : 'var(--red)';
            return `<tr><td style="font-weight:500">${esc(x.device_id)}</td>
              <td><span style="color:${color};font-weight:700;font-size:14px">${x.score}</span>/100</td>
              <td>${x.cpu ?? '—'}%</td><td>${x.memory_pct ?? '—'}%</td><td>${x.disk_pct ?? '—'}%</td>
              <td><span class="badge ${x.status === 'healthy' ? 'badge-g' : x.status === 'degraded' ? 'badge-b' : 'badge-r'}">${esc(x.status)}</span></td></tr>`;
          }).join('') || '<div class="empty">Sin datos</div>';
      } catch (e) { }
    }
