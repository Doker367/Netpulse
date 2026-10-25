/**
 * NetPulse NOC — vista Commands: centro de comandos global
 * (views/commands.js).
 *
 * Extraída del <script> inline de app/static/dashboard.html (Tarjeta 10D-E,
 * lote E) sin cambio de comportamiento: execGlobalCmd y runQuickCmd, líneas
 * en el mismo orden relativo. El comentario de sección `── GLOBAL COMMAND
 * CENTER ──` queda en el inline porque la función que encabezaba el bloque,
 * `populateCmdDevices`, NO viajó: la llama también `loadBk` (vista Backups,
 * inline residual) en cada latido de 60 s y tras cada backup — regla 2
 * anti-compartidas: queda en el inline, igual que `__isAdmin`/`npGray` se
 * quedaron en su bloque original pese a ser de Groups (Lote A).
 *
 * Lo que NO viajó (verificado por grep, regla 2):
 * - `populateCmdDevices` — el ÚNICO consumidor que no es esta vista (o no era)
 *   es `loadBk`: escribir `#cmd-dev` es un efecto colateral del latido de
 *   Backups. Moverla dejaría el inline dependiente de esta vista a la
 *   evaluación si algún día se ejecutara top-level; se queda.
 * - `devices`, `A`, `h`, `t`, `toast`, `esc` — core/estado compartido;
 *   resuelven en tiempo de llamada.
 *
 * Depende (en tiempo de llamada): core.js (A, h, toast), inline (esc,
 * devices, populateCmdDevices). Handlers por window: textarea y botón de
 * #v-commands (`execGlobalCmd`), botones de atajos (`runQuickCmd`, onclick
 * generados en el markup estático). Script clásico sin defer/async;
 * top-level sin ejecución de lógica (solo declaraciones `function`).
 */

    async function execGlobalCmd() {
      const id = document.getElementById('cmd-dev')?.value;
      const input = document.getElementById('cmd-input')?.value;
      const out = document.getElementById('cmd-output');
      const status = document.getElementById('cmd-status');
      if (!id || !input?.trim()) { toast('Selecciona un dispositivo y escribe un comando', 'error'); return; }
      const cmds = input.split('\n').map(c => c.trim()).filter(c => c);
      status.textContent = 'Ejecutando...'; status.style.color = 'var(--yellow)';
      let html = '';
      for (const cmd of cmds) {
        html += `<span style="color:var(--accent)">${id}$</span> ${esc(cmd)}\n`;
        try {
          const r = await fetch(`${A}/command/${id}`, { method: 'POST', headers: h(), body: JSON.stringify({ commands: [cmd] }) });
          const d = await r.json();
          const txt = d.results?.[0]?.output || d.results?.[0]?.error || '';
          html += txt ? `${esc(txt)}\n` : `<span style="color:var(--orange)"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m12 3 10 18H2Z"/><path d="M12 9v4M12 17h.01"/></svg> Sin output</span>\n`;
        } catch (e) { html += `<span style="color:var(--red)"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> ${e.message}</span>\n`; }
      }
      out.innerHTML = html; out.scrollTop = out.scrollHeight;
      status.textContent = 'Completado'; status.style.color = 'var(--green)';
      setTimeout(() => { status.textContent = 'Ready'; status.style.color = 'var(--text-ter)'; }, 3000);
    }
    function runQuickCmd(cmd) {
      const input = document.getElementById('cmd-input');
      if (input) { input.value = cmd; execGlobalCmd(); }
    }
