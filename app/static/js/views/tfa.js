/**
 * NetPulse NOC — vista 2FA / TOTP por usuario (views/tfa.js).
 *
 * Extraída del <script> inline de app/static/dashboard.html (Tarjeta 10D-B,
 * lote 2) sin cambio de comportamiento: setup2FA y verify2FA, mismas
 * líneas en el mismo orden relativo.
 *
 * NOTA: la vista #v-2fa no tiene loader en swView (solo botones); las
 * funciones se disparan por onclick del markup.
 *
 * Depende (en tiempo de llamada, no de carga): core.js (A, h). Handlers
 * que las resuelven por window: onclick del markup de #v-2fa →
 * setup2FA() / verify2FA(). Scripts clásicos sin defer/async ya cargaron
 * al momento del click (orden estático). Top-level sin ejecución.
 */

    // ── 2FA / TOTP ────────────────────────────────────────────
    async function setup2FA() {
      try {
        const r = await fetch(`${A}/security/2fa/setup`, { method: 'POST', headers: h() });
        if (!r.ok) return;
        const d = await r.json();
        document.getElementById('2fa-result').style.display = 'block';
        document.getElementById('2fa-secret').textContent = `Secreto: ${d.secret}`;
        if (d.qr_data_uri) {
          document.getElementById('2fa-secret').innerHTML += `<br><br><img src="${d.qr_data_uri}" alt="QR" style="width:180px;height:180px;background:#fff;border-radius:6px;padding:8px">`;
        }
      } catch (e) { }
    }
    async function verify2FA() {
      try {
        const r = await fetch(`${A}/security/2fa/verify`, {
          method: 'POST', headers: h(), body: JSON.stringify({
            secret: document.getElementById('2fa-secret').textContent.replace('Secreto: ', '').split('\n')[0].trim(),
            code: document.getElementById('2fa-code').value
          })
        });
        const d = await r.json();
        document.getElementById('2fa-result-msg').innerHTML = d.valid ?
          `<span style="color:var(--green)"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg> Código válido</span>` :
          `<span style="color:var(--red)"><svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> Código inválido</span>`;
      } catch (e) { }
    }
