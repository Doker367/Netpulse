# Sesión, refresh y revocación de token JWT — contrato para la UI

Rama original: `feat/auth-session` · Actualización 12B: `feat/session-revoke` · Base: `main`
Fecha: 2026-10-25
Zona tocada: `app/routers/auth.py`, `app/routers/admin_users.py`,
`app/middleware/auth.py`, `tests/test_auth_refresh.py`, `tests/test_auth_revoke.py`,
este documento.
Estado: **implementado y probado**. Sin cambios en esquemas ni frontend.

---

## 1. Qué resuelve y qué no

Problema real: la UI guarda el JWT solo en memoria y, al recargar la página, pierde la
sesión aunque el token siga vigente. Este cambio añade una vía explícita de renovación
para que la UI pueda mantener la sesión sin volver a pedir credenciales mientras el
usuario siga activo.

Es **rotación stateless con revocación por versión**, no gestión de sesiones en servidor:

- No hay refresh tokens separados ni blacklist en memoria.
- Cada JWT de usuario lleva el claim `tv` (versión de sesión). Si no coincide con el
  `token_version` actual del usuario en `config/users.yaml`, el middleware responde 401.
- Revocar todas las sesiones es incrementar `token_version`; no hay que esperar al `exp`.
- El refresh no autentica por sí solo: exige un JWT de usuario **todavía válido** y no
  puede resucitar uno revocado.
- Sin una revocación explícita, el token anterior a un refresh sigue válido hasta su `exp`
  (la rotación no lo invalida).

La UI **no debe** guardar el token en `localStorage` como parte de esta tarjeta; el
contrato sirve igual con el token en memoria.

---

## 2. Decisión: el refresh re-lee el usuario actual

`POST /api/auth/refresh` no copia los claims del token recibido. Resuelve `sub` contra
`config/users.yaml` (vía `auth._get_users()`) y reconstruye los claims con
`_token_claims()`, la misma función que usa el login.

Consecuencia: si un administrador cambió el rol o el scope del usuario desde que se emitió
el token, **el token renovado refleja el valor actual**, no el antiguo. Si el usuario fue
eliminado de la configuración, el refresh responde 401.

`_token_claims()` incluye además `tv` con el `token_version` actual. El middleware ya
bloquea un token revocado antes de llegar al endpoint, así que el refresh no puede emitir
uno nuevo con una versión vieja.

Por qué:

- Es lo más seguro sin añadir estado: el refresh se convierte en el punto donde se
  re-evalúan privilegios, en lugar de prorrogar indefinidamente un rol ya retirado.
- Es simple: `_load_users`/`_get_users` ya existen y el login ya construía estos claims.
- La alternativa (preservar claims viejos) habría permitido que un token emitido antes de
  un recorte de scope conservara el acceso viejo durante renovaciones sucesivas, sin
  límite de tiempo.

Matiz operativo: `_get_users()` usa el caché en memoria de `app/routers/auth.py`. Ese caché
se invalida cuando se modifica un usuario por la API de administración
(`admin_users._invalidate_auth_cache()`); una edición manual de `config/users.yaml` no
invalida el caché y no se verá hasta reiniciar/reload del proceso. El refresh refleja,
por tanto, el estado que el motor tiene cargado, no necesariamente el archivo en disco.

---

## 3. Contrato del endpoint

### `POST /api/auth/refresh`

- **Auth**: header `Authorization: Bearer <JWT>` de usuario.
- **Body**: ninguno (no enviar JSON; un body se ignora).
- **Respuesta 200** — mismo esquema `TokenResponse` que el login:

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer"
}
```

Claims del token nuevo (idénticos en forma a los del login):

| claim | presente | valor |
|---|---|---|
| `sub` | siempre | username de `config/users.yaml` |
| `role` | siempre | rol actual del usuario (`viewer`/`operator`/`admin`) |
| `tv` | siempre | `token_version` actual del usuario (0 si el campo no existe) |
| `scope_group` | solo si está definido | grupo/site actual |
| `scope_sites` | solo si está definido | lista actual de sites |
| `exp` | siempre | ahora + `NETPULSE_TOKEN_EXPIRE` minutos (default 30) |

El token nuevo recibe la **expiración completa**, no la que le quedaba al anterior. `tv`
es el único claim nuevo respecto al contrato anterior: no cambia el significado de
`sub`/`role` y los consumidores que no lo conozcan pueden ignorarlo.

### Errores (detail literal)

Todos con `WWW-Authenticate: Bearer` salvo donde se indique:

| Situación | HTTP | `detail` |
|---|---|---|
| Sin header `Authorization` | 401 | `Token de autorización requerido` |
| Esquema distinto de Bearer | 403 | `Esquema de autenticación inválido (se requiere Bearer)` |
| JWT inválido, expirado o con firma manipulada | 401 | `Token inválido o expirado` |
| JWT válido pero `tv` no coincide con el usuario (revocado) | 401 | `Sesión revocada; vuelve a entrar` |
| JWT nuevo (`tv`) de un usuario eliminado de la configuración | 401 | `Sesión revocada; vuelve a entrar` |
| API key `np_...` (válida o no) usada para renovar | 401 | `La renovación de sesión solo acepta tokens JWT de usuario` (o el error de API key del middleware si la clave no es válida) |
| `sub` sin usuario en la configuración cargada (token legado sin `tv`) | 401 | `Usuario no encontrado para renovar la sesión` |

No hay `422`: el endpoint no recibe body. El rate limit es el global de la app
(100 req/min por IP); el refresh no añade un límite propio ni consume el cupo de 5/min
del login.

`GET /api/auth/me` no cambia: sigue devolviendo `{"username": ..., "role": ...}` y sigue
exigiendo `Bearer` válido.

---

## 4. Revocación de sesiones (`token_version`)

### Semántica

`config/users.yaml` admite un entero opcional `token_version` por usuario. Si falta, vale
**0**: no hay migración manual ni cambio obligatorio de los archivos existentes.

1. Login y refresh emiten el JWT con `tv = token_version` del usuario.
2. Cualquier dependencia `JWTBearer` compara `tv` del token con el valor cargado del
   usuario. Si difieren, responde **401** con `Sesión revocada; vuelve a entrar` y
   `WWW-Authenticate: Bearer`. Aplica a `/api/auth/me`, `/api/auth/refresh` y a todas
   las rutas protegidas.
3. Revocar es incrementar `token_version`. Todos los tokens emitidos antes de ese
   incremento dejan de valer de inmediato; los emitidos después (nuevo login o refresh)
   llevan la versión nueva y funcionan.
4. Es por usuario: revocar A no toca la versión ni los tokens de B.
5. Las API keys `np_` no son JWT y no se ven afectadas.

Compatibilidad: un token **legado sin `tv`** se interpreta como versión 0. Sigue válido
mientras el usuario esté en versión 0, y queda revocado en cuanto se incrementa. Un token
nuevo (con `tv`) de un usuario ya eliminado también recibe 401, porque el middleware no
encuentra al usuario; los tokens legados sin `tv` de un `sub` ausente conservan el
comportamiento anterior hasta su `exp` (no había forma de distinguirlos de un usuario
válido sin estado adicional).

### Endpoint de administración

`POST /api/admin/users/{username}/revoke-sessions`

- **Auth**: `Authorization: Bearer <JWT>` con rol `admin` (el rol máximo actual; no hay
  un rol separado llamado `superadmin`).
- **Body**: ninguno.
- **200**:

```json
{"username": "operador", "token_version": 3}
```

- **401** sin token o con token no válido/revocado; **403** con rol insuficiente;
  **404** si el usuario no existe (`Usuario '<username>' no encontrado`).
- Limpia el caché de usuarios (`auth._users_cache.clear()`), igual que el resto del CRUD,
  para que la versión nueva se lea de inmediato.
- **Auditoría**: evento `user_revoke_sessions` con el actor en `username`, el objetivo y
  la transición en `details` (`target=<usuario> token_version=<viejo>-><nuevo>`), además
  de IP, ruta y método.
- No hay botón en la UI todavía; se invoca por API (ver `docs/GUIA-OPERADOR.md`).

### CRUD de usuarios

- `POST /api/admin/users` crea con `token_version: 0`.
- `PUT /api/admin/users/{username}` **no toca** `token_version`: cambiar nombre, rol,
  contraseña o scope no resucita sesiones revocadas.
- `DELETE /api/admin/users/{username}` elimina al usuario; sus tokens nuevos dejan de
  validar en la siguiente petición (ver compatibilidad arriba).

---

## 5. Guía de integración para la UI

1. Tras un login correcto, guardar `access_token` en memoria (como hoy) y anotar el
   instante de recepción.
2. Programar la renovación a los **~25 minutos** con el default de 30 minutos
   (`NETPULSE_TOKEN_EXPIRE`). Si el operador cambia esa variable, usar
   `expiración - 5 min` como margen; no hardcodear 25 min si se puede leer la config.
3. Llamar `POST /api/auth/refresh` con el token actual. Si responde 200, sustituir el
   token en memoria y reprogramar la siguiente renovación.
4. Si la llamada falla por red/timeout, **no cerrar sesión**: reintentar con espera
   (p. ej. 30 s) mientras el token siga vigente. Un fallo de red no es un 401.
5. Si responde **401**, la sesión ya no es renovable (expiró o un admin la revocó):
   - borrar el token en memoria y volver al login;
   - mostrar un mensaje honesto, p. ej. «Sesión expirada. Inicia sesión de nuevo.»; si el
     `detail` es `Sesión revocada; vuelve a entrar`, puedes mostrarlo tal cual;
   - **conservar `location.hash`** (y el estado de vista) para volver a la misma vista
     tras autenticarse. El hash no se envía al servidor y hoy es la vía de navegación
     del panel.
6. Si responde 403 por esquema, es un error de cliente: revisar que se envía
   `Authorization: Bearer ...`.
7. No usar el refresh con API keys `np_`: están pensadas para automatización y no se
   convierten en JWT de sesión.

Ejemplo mínimo (token en memoria):

```js
async function renovarSesion() {
  const r = await fetch('/api/auth/refresh', {
    method: 'POST',
    headers: { Authorization: `Bearer ${tokenEnMemoria}` },
  });
  if (r.ok) {
    const { access_token } = await r.json();
    tokenEnMemoria = access_token;
    programarRenovacion();
    return true;
  }
  if (r.status === 401) {
    tokenEnMemoria = null;
    volverAlLogin('Sesión expirada. Inicia sesión de nuevo.'); // conserva location.hash
    return false;
  }
  return null; // error transitorio: reintentar sin cerrar sesión
}
```

---

## 6. Limitaciones explícitas (mejoras futuras, no implementadas)

1. **El refresh no rota/revoca el token anterior.** Sin una revocación explícita, tras
   renovar conviven el token anterior y el nuevo durante la ventana restante del viejo.
   Si el token anterior se filtró, sigue sirviendo hasta su `exp` salvo que un admin
   revoque todas las sesiones del usuario.
2. **La revocación no cubre el WebSocket de terminal.** Ese endpoint valida el JWT con
   `verify_token` directamente, fuera de `JWTBearer` (zona no modificada en esta
   tarjeta). Un token revocado puede abrir o mantener una sesión de terminal hasta su
   `exp`; cerrar shells ya abiertos no está cubierto. La API HTTP sí queda revocada.
3. **La revocación no cambia la contraseña ni el rol.** Solo invalida sesiones emitidas;
   si el usuario vuelve a entrar con sus credenciales actuales, obtiene un token nuevo
   con la versión vigente.
4. **Sesión deslizante sin límite absoluto.** Mientras el usuario siga en la config y
   renueve a tiempo, la sesión puede extenderse indefinidamente. No hay edad máxima de
   sesión ni contador de renovaciones.
5. **Sin refresh token separado.** El mismo token de acceso sirve para llamar a la API y
   para renovarse. Si se quiere separar la superficie de robo, haría falta un refresh
   token con almacenamiento/rotación y revocación server-side.
6. **Dependencia del caché de usuarios.** Ediciones manuales de `config/users.yaml`
   requieren reinicio/reload del proceso para que el login, el refresh y la validación de
   `tv` las vean (ver §2). La API de administración sí limpia el caché.
7. **Sin límite específico de refresh ni de revocación.** Solo aplica el global de
   100/min por IP.

---

## 7. Verificación

- `tests/test_auth_refresh.py` (9 tests): refresh feliz conserva `sub`/`role`/scopes y
  renueva `exp`; usuario sin scopes no los inventa; cambio de rol/scope en config se
  refleja; usuario eliminado → 401; sin token → 401; inválido/expirado/firma manipulada
  → 401 con el detalle literal; login y `/me` siguen intactos y el token del login
  también se puede renovar.
- `tests/test_auth_revoke.py` (8 tests): login→revocar→401 en `/me` y en ruta protegida;
  refresh revocado → 401 sin emitir token; revocar A no afecta a B; usuario sin
  `token_version` (y token legado sin `tv`) sigue funcionando hasta que se revoca;
  auditoría `user_revoke_sessions`; `PUT` no pierde `token_version`; usuario eliminado con
  token nuevo → 401; 404 y 403 del endpoint.
- El login real de los tests usa `X-Forwarded-For` propio para no consumir el cupo de
  5/min del rate limiter compartido.
- Suite completa: **366 tests verdes** (358 previos + 8 nuevos).
