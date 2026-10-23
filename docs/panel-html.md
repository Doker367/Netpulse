# Panel HTML de NetPulse NOC

## Rutas y funcionamiento

La API FastAPI, sus permisos y el motor de inventario/monitorización permanecen. El panel HTML en `app/static/dashboard.html` es la única interfaz web.

| URL | Contenido |
|---|---|
| `/` | Panel HTML |
| `/legacy` | Mismo panel, alias para enlaces existentes |
| `/api/*` | API existente |

## Vista principal

El resumen funciona como mesa de control: el estado de la red ocupa el primer plano, a su lado aparecen los equipos efectivamente desconectados con acceso a sus detalles, y debajo están los recuentos. El porcentaje en línea es la proporción **actual** de equipos que responden, no disponibilidad histórica ni un SLA. Si falla la lectura de salud o Prometheus, aparece «sin verificar» o «—» en vez de inventar un estado favorable. Topología, alta de equipos, actualización, detalle y selección múltiple siguen disponibles.

El CSS específico vive en `app/static/overview.css`; las demás vistas conservan sus estilos. El trazo del hero es una textura estática inspirada en el nombre NetPulse, no una gráfica de telemetría en vivo. Su tono acompaña el estado real sin sustituir el texto. Las capturas verificadas están en `docs/img/legacy-overview-desktop.png`, `docs/img/legacy-overview-tablet.png` y `docs/img/legacy-overview-mobile.png` (los equipos del laboratorio aparecen caídos porque están apagados).

## Desarrollo y comprobaciones

Desde la raíz del proyecto:

```bash
make run   # API y panel en http://127.0.0.1:8082/
make lint  # ruff
make test  # pytest
```

En `tests/test_dashboard_routes.py` se comprueban las rutas de la interfaz y los estados veraces. El login distingue credenciales inválidas (401), demasiados intentos (429) y fallos de conexión; las alertas activas provienen de Prometheus y se indican como desconocidas si esa fuente no responde.
