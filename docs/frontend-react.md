# Interfaces de NetPulse NOC (panel anterior por defecto)

## Qué cambia y qué permanece

La API FastAPI, `/api/*`, los permisos y el motor de inventario/monitorización no se reemplazan. El panel HTML original en `app/static/dashboard.html` vuelve a ser el principal y recibe mejoras graduales de login, legibilidad y navegación sin quitar funciones. React + TypeScript + Vite se conserva en `frontend/` con su build en `app/static/react/` como alternativa opcional.

| URL | Modo por defecto (`legacy`) | `NETPULSE_FRONTEND=react` |
|---|---|---|
| `/` | Panel HTML anterior | React si hay build |
| `/modern` | React si hay build | React si hay build |
| `/legacy` | Panel HTML anterior | Panel HTML anterior |
| `/api/*` | Mismo backend | Mismo backend |

Si falta el build React, `/` y `/modern` sirven el panel HTML; no se necesita cambiar la API.

## Vista principal del panel HTML

El resumen se diseñó como una mesa de control: el estado de red ocupa el primer plano, a su lado aparecen equipos efectivamente desconectados con acceso a sus detalles, y debajo están los recuentos legibles. El porcentaje en línea es la proporción **actual** de equipos que responden, no una disponibilidad histórica ni un SLA. Si falla la lectura de salud o Prometheus, se muestra «sin verificar» o «—» en lugar de inventar un estado favorable. Las acciones existentes de topología, alta de equipos, actualización, detalle y selección múltiple siguen disponibles.

El CSS específico de esta vista vive en `app/static/overview.css`; las demás vistas conservan sus estilos. El trazo del hero es una textura estática inspirada en el nombre NetPulse, no una gráfica de telemetría en vivo; su tono acompaña el estado real sin sustituir el texto. El panel funciona sin Node, Tailwind ni dependencias de React. Las capturas verificadas están en `docs/img/legacy-overview-desktop.png`, `docs/img/legacy-overview-tablet.png` y `docs/img/legacy-overview-mobile.png` (los equipos del laboratorio aparecen caídos porque están apagados).

## Desarrollo y build

Prerequisitos: entorno Python de NetPulse, Node.js y pnpm. Desde la raíz:

```bash
make run            # panel HTML principal → http://127.0.0.1:8082/
make ui-install     # opcional: solo si quieres trabajar en React
make ui-build       # opcional: TypeScript + Vite → app/static/react/
```

Tras editar React hay que repetir `make ui-build` y recargar la página. Para desarrollar con HMR (requiere FastAPI corriendo en 8082), desde `frontend/` ejecuta `pnpm dev` y abre el URL que Vite muestre; Vite envía `/api` a FastAPI. **No** abre otro backend ni modifica el panel HTML heredado.

El build estático se conserva en `app/static/react/` para que FastAPI pueda servir React en `/modern` sin tener Node corriendo en producción. `frontend/node_modules/` y `.pnpm-store/` están ignorados por git.

## Alternar sin borrar interfaces

Por defecto `make run` abre el panel HTML anterior en `/`. Para probar React como portada, detén la API y reiníciala con:

```bash
NETPULSE_FRONTEND=react make run
```

Para volver al panel anterior usa `NETPULSE_FRONTEND=legacy make run`, o elimina la variable de `.env` y reinicia la API. No se borran datos ni código; `/modern` permite abrir React sin cambiar la portada. El modo se evalúa en cada petición, pero el cambio de variables de entorno en una API ya iniciada requiere reiniciar el proceso.

## Interfaz React opcional

En React funcionan con endpoints reales: login JWT, vista general con equipos/alertas/servicios, inventario con filtros y CRUD para admin, monitoreo con series de Prometheus, alertas activas y usuarios/roles para admin. Los formularios respetan el RBAC del backend. Navegación, estados de error/carga y disposición móvil están incluidos. El token de sesión vive solo en memoria; cada interfaz requiere su propio inicio de sesión, también al recargar.

Topología, comandos, WebSSH/SFTP, backups, cumplimiento y el resto de herramientas siguen disponibles mediante el enlace **Panel anterior** a `/legacy`, donde se requiere iniciar sesión de nuevo. Una segunda fase puede migrarlas sin retirar el fallback.

## Comprobaciones

```bash
make ui-check
make ui-build
make lint
make test
```

Los tests `tests/test_frontend_switch.py` verifican la portada HTML por defecto, el modo React explícito, ambas rutas permanentes y el fallback si falta el build. En el panel anterior el login distingue credenciales inválidas (401), demasiados intentos (429) y fallos de conexión. Su resumen de red distingue equipos caídos de salud sin verificar; las alertas activas vienen de Prometheus y se indican como desconocidas si esa fuente no responde. React tampoco presenta datos simulados y explica los servicios caídos.
