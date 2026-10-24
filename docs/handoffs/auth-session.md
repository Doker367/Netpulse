# Sesión y refresh de token JWT — contrato para la UI

Rama: `feat/auth-session` · Base: `main` (253 tests verdes antes de esta tarjeta) · Fecha: 2026-10-24
Zona tocada: `app/routers/auth.py`, `tests/test_auth_refresh.py`, este documento.
Estado: **implementado y probado**. Sin cambios en middleware, esquemas ni frontend.

---

## 1. Qué resuelve y qué no

Problema real: la UI guarda el JWT solo en memoria y, al recargar la página, pierde la
sesión aunque el token siga vigente. Este cambio añade una vía explícita de renovación
para que la UI pueda mantener la sesión sin volver a pedir credenciales mientras el
usuario siga activo.

Es **rotación stateless**, no gestión de sesiones en servidor:

- No hay refresh tokens separados ni almacenamiento nuevo.
- No hay blacklist ni revocación. El token anterior sigue siendo válido hasta su `exp`.
- El refresh no autentica por sí solo: exige un JWT de usuario **todavía válido**.

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
| `scope_group` | solo si está definido | grupo/site actual |
| `scope_sites` | solo si está definido | lista actual de sites |
| `exp` | siempre | ahora + `NETPULSE_TOKEN_EXPIRE` minutos (default 30) |

El token nuevo recibe la **expiración completa**, no la que le quedaba al anterior. No se
añaden claims nuevos ni se cambia el payload del login.

### Errores (detail literal)

Todos con `WWW-Authenticate: Bearer` salvo donde se indique:

| Situación | HTTP | `detail` |
|---|---|---|
| Sin header `Authorization` | 401 | `Token de autorización requerido` |
| Esquema distinto de Bearer | 403 | `Esquema de autenticación inválido (se requiere Bearer)` |
| JWT inválido, expirado o con firma manipulada | 401 | `Token inválido o expirado` |
| API key `np_...` (válida o no) usada para renovar | 401 | `La renovación de sesión solo acepta tokens JWT de usuario` (o el error de API key del middleware si la clave no es válida) |
| `sub` sin usuario en la configuración cargada | 401 | `Usuario no encontrado para renovar la sesión` |

No hay `422`: el endpoint no recibe body. El rate limit es el global de la app
(100 req/min por IP); el refresh no añade un límite propio ni consume el cupo de 5/min
del login.

`GET /api/auth/me` no cambia: sigue devolviendo `{"username": ..., "role": ...}` y sigue
exigiendo `Bearer` válido.

---

## 4. Guía de integración para la UI

1. Tras un login correcto, guardar `access_token` en memoria (como hoy) y anotar el
   instante de recepción.
2. Programar la renovación a los **~25 minutos** con el default de 30 minutos
   (`NETPULSE_TOKEN_EXPIRE`). Si el operador cambia esa variable, usar
   `expiración - 5 min` como margen; no hardcodear 25 min si se puede leer la config.
3. Llamar `POST /api/auth/refresh` con el token actual. Si responde 200, sustituir el
   token en memoria y reprogramar la siguiente renovación.
4. Si la llamada falla por red/timeout, **no cerrar sesión**: reintentar con espera
   (p. ej. 30 s) mientras el token siga vigente. Un fallo de red no es un 401.
5. Si responde **401**, la sesión ya no es renovable:
   - borrar el token en memoria y volver al login;
   - mostrar un mensaje honesto, p. ej. «Sesión expirada. Inicia sesión de nuevo.»;
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

## 5. Limitaciones explícitas (mejoras futuras, no implementadas)

1. **El token viejo sigue válido hasta expirar.** Al no existir estado en servidor, tras
   renovar conviven el token anterior y el nuevo durante la ventana restante del viejo.
   Si el token anterior se filtró, sigue sirviendo hasta su `exp`.
2. **No hay revocación real.** Cambiar la contraseña, despedir a un usuario o retirarle un
   scope no invalida tokens ya emitidos; como mucho, el siguiente refresh refleja el
   cambio de config y un usuario eliminado ya no puede renovar. Un token robado puede
   seguir usándose hasta expirar.
3. **Sesión deslizante sin límite absoluto.** Mientras el usuario siga en la config y
   renueve a tiempo, la sesión puede extenderse indefinidamente. No hay edad máxima de
   sesión ni contador de renovaciones.
4. **Sin refresh token separado.** El mismo token de acceso sirve para llamar a la API y
   para renovarse. Si se quiere separar la superficie de robo, haría falta un refresh
   token con almacenamiento/rotación y revocación server-side.
5. **Dependencia del caché de usuarios.** Ediciones manuales de `config/users.yaml`
   requieren reinicio/reload del proceso para que el refresh las vea (ver §2).
6. **Sin límite específico de refresh.** Solo aplica el global de 100/min por IP; no se
   añadió un límite dedicado para no interferir con el latido normal de la UI.

---

## 6. Verificación

- `tests/test_auth_refresh.py` (9 tests): refresh feliz conserva `sub`/`role`/scopes y
  renueva `exp`; usuario sin scopes no los inventa; cambio de rol/scope en config se
  refleja; usuario eliminado → 401; sin token → 401; inválido/expirado/firma manipulada
  → 401 con el detalle literal; login y `/me` siguen intactos y el token del login
  también se puede renovar.
- El login real del test usa `X-Forwarded-For` propio para no consumir el cupo de
  5/min del rate limiter compartido.
- Suite completa: pendiente de anotar el conteo final en el reporte de la tarjeta
  (253 previos + 9 nuevos).
