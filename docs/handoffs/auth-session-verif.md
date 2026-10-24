# Tarjeta 5D — Verificación en vivo de sesión y refresh

Fecha: 2026-10-24, 01:03:49 America/Mexico_City (07:03:49 UTC).
Rama: `feat/auth-session-verif`. Base verificada: `6652bfd`.

**Resultado: API validada en vivo sin mocks; sesión persistente en navegador NO validada. No se afirma un end-to-end completo.**

## Preparación y alcance

- Worktree exclusivo `/Users/doker/np-w5v`; sin merges, push ni cambios de aplicación.
- Leído [PRODUCT.md](../../PRODUCT.md): no se presentan resultados simulados como reales.
- [Contrato backend](auth-session.md): renovación stateless con el mismo access token; no existe refresh token separado ni revocación del anterior.
- `config/users.yaml` no existía (lectura inicial: `not found`). No había archivo que respaldar. Se registró su ausencia antes de crear `demo5`, admin temporal con `scope_group=verif5` y `scope_sites=[verif5-site]`; hash generado con `app.core.security.hash_password`. Al terminar se eliminó únicamente el archivo temporal, restaurando la ausencia original.
- Contraseña temporal usada solo en la ejecución local, no conservada en esta entrega. JWT completos redactados; se conservan payloads sin firma para evidencia.
- API real: `PYTHONPATH=. /Users/doker/netpulse-noc/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8095`.
- PID registrado **4879**, job `bash-39`. Peticiones mediante `urllib.request` contra TCP localhost, sin TestClient, mocks ni alteración del reloj.
- No se arrancó Docker. El startup de la aplicación imprimió `Lab inventory initialized with 5 devices`: es inventario de arranque, no evidencia de equipos conectados ni lab ejecutándose.

## Tabla de resultados

| Prueba | Esperado | Real | Evidencia |
|---|---|---|---|
| Login real admin | 200 y access token | PASÓ con demo5 temporal | E1 |
| Refresh feliz | 200, JWT nuevo y mismos sub/role/scope_* | PASÓ; exp aumentó un segundo; ambos scopes presentes e iguales | E2 |
| /me con JWT nuevo | 200, admin demo5 | PASÓ | E3 |
| Refresh sin Bearer | 401 con detalle honesto | PASÓ: Token de autorización requerido | E4 |
| Refresh con basura | 401 con detalle honesto | PASÓ: Token inválido o expirado | E5 |
| Token anterior después de refresh | Sigue aceptado mientras esté vigente | PASÓ inmediatamente después del refresh; no se esperaron 30 minutos para observar su expiración | E6; comportamiento stateless esperado, no bug |
| UI login → dashboard → F5 y hash conservado | Sesión y vista persisten | **NO VALIDADO** | No hay herramienta de browser disponible |
| UI logout | Login y np_token eliminado | **NO VALIDADO** | No hay herramienta de browser disponible |
| UI 401 forzado | Login, mensaje de sesión expirada, sin bucles | **NO VALIDADO** | No hay herramienta de browser disponible |
| Expiración real y refresh automático (opcional) | Renovar antes de expirar | **NO VALIDADO** | Intervalo fijo de 25 minutos, no 25 segundos; no se arrancó segunda instancia |
| Limpieza | API cerrada, puerto vacío, configuración restaurada | PASÓ | E7 |
| Suite completa | Todos los tests verdes | PASÓ: 262 tests, exit 0; una advertencia de deprecación | E8 |

## Evidencia HTTP real

Inicio del cliente: `2026-10-24T07:03:49.331093+00:00`.
Todos los endpoints siguientes son relativos a `http://127.0.0.1:8095`.

```text
E1 POST /api/auth/login
Content-Type: application/json
Body: {"username":"demo5","password":"<redactada>"}
200 {"access_token":"<redactado>","token_type":"bearer"}

E2 POST /api/auth/refresh
Authorization: Bearer <token de E1>
Body vacío
200 {"access_token":"<redactado>","token_type":"bearer"}

Payload E1 (base64url, sin verificar firma):
{"sub":"demo5","role":"admin","scope_group":"verif5","scope_sites":["verif5-site"],"exp":1792827229}
Payload E2 (base64url, sin verificar firma):
{"sub":"demo5","role":"admin","scope_group":"verif5","scope_sites":["verif5-site"],"exp":1792827230}
JWT diferente; exp aumentado; sub/role/scope_group/scope_sites idénticos: PASS

E3 GET /api/auth/me
Authorization: Bearer <token de E2>
200 {"username":"demo5","role":"admin"}

E4 POST /api/auth/refresh (sin Authorization, body vacío)
401 {"detail":"Token de autorización requerido"}

E5 POST /api/auth/refresh (Authorization: Bearer basura, body vacío)
401 {"detail":"Token inválido o expirado"}

E6 GET /api/auth/me
Authorization: Bearer <token original de E1>
200 {"username":"demo5","role":"admin"}
```

Se esperaron 1,2 segundos entre login y refresh: `exp` se serializa en segundos y una emisión en el mismo segundo puede producir un JWT idéntico. Las comprobaciones del cliente usaron assertions de status, diferencia entre tokens, aumento de exp e igualdad de los cuatro claims. Todas pasaron. La firma del payload no se verificó en la decodificación diagnóstica; la aceptación de ambos JWT se comprobó contra el backend real con `/me`.

## Navegador y limitaciones

No se dispone de herramienta nativa de navegador en esta sesión. Detección local:

```text
command -v playwright → sin resultado
Python: {'playwright': False, 'selenium': False}
Node: playwright unavailable
Node: puppeteer unavailable
Node: @playwright/test unavailable
```

No se instalaron dependencias ni se sustituyó una prueba de navegador por requests contra la SPA. F5, dashboard visible, conservación de hash, localStorage, logout y ausencia de popups requieren una ejecución posterior en navegador real.

El código de [dashboard.html:3358–3362](../../app/static/dashboard.html#L3358-L3362) fija `NP_REFRESH_MS = 25 * 60 * 1000`. El ajuste backend `NETPULSE_TOKEN_EXPIRE=1` no cambia ese temporizador: no permite validar renovación automática a los ~25 segundos. Esto es una limitación de la prueba opcional, no un fallo reproducido en navegador. Propuesta futura: programar según `exp` con margen y permitir intervalos cortos de prueba; no aplicada.

No se reprodujo un bug de aplicación en las pruebas HTTP realizadas. No se alteró código de aplicación. Tampoco se validaron revocación, cambio de permisos en caliente, fallos de red ni expiración efectiva del JWT viejo: no se deben inferir de esta ejecución.

## E7 — Limpieza

```text
kill -TERM 4879
INFO: Shutting down
INFO: Waiting for application shutdown.
INFO: Application shutdown complete.
INFO: Finished server process [4879]
Restaurado estado original: config/users.yaml ausente
lsof -nP -i :8095 → sin salida, exit 1 (ningún proceso)
git status --short → sin salida antes de crear este handoff
```

El proceso fue recolectado con `job_output`; terminado por SIGTERM solicitado, no fallo inesperado. No quedan procesos API iniciados por esta tarjeta.

## E8 — Suite completa

Comando desde el worktree:

```sh
PYTHONPATH=. /Users/doker/netpulse-noc/.venv/bin/pytest tests/ -q
```

Resultado real (job `bash-43`, recolectado, exit code 0):

```text
........................................................................ [ 27%]
........................................................................ [ 54%]
........................................................................ [ 82%]
..............................................                           [100%]
```

262 tests verdes (72 + 72 + 72 + 46). Una advertencia: `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead.` No fallos. Esta suite es evidencia adicional y no sustituye las peticiones en vivo ni las pruebas pendientes de navegador.

## Pendientes explícitos

1. Ejecutar en navegador real login/dashboard/F5 con hash, logout y 401 forzado con mensaje y sin bucles. Es el bloqueo para afirmar sesión persistente end-to-end.
2. Validar refresh automático y expiración real con un temporizador adecuado o una espera de 25 minutos; no ejecutado aquí.
3. Observar el rechazo del JWT original después de su `exp` si se requiere evidencia temporal completa; aquí solo se comprobó su aceptación tras refresh.

## Verificación en navegador real (orquestador, 2026-10-24, Chrome CDP)

| Prueba | Resultado |
|---|---|
| Login → dashboard con np_token persistido | ✅ |
| F5 en #sites: sesión persiste, sin login screen | ✅ |
| F5: vista conservada por location.hash | ✅ |
| 401 forzado en fetch (token corrupto): vuelve al login una vez, mensaje "Sesión expirada — vuelve a entrar." | ✅ |
| F5 con np_token corrupto: login + mismo mensaje | ✅ |
| logout: np_token eliminado y login visible | ✅ |

Pendiente honesto: renovación automática a los 25 min no validada en vivo por
tiempo de espera (intervalo fijo); el endpoint refresh sí está verificado por
API y la lógica de temporizador por código. Sin afirmar E2E de expiración real.
