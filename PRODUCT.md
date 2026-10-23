# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Operadores y administradores NOC que supervisan infraestructura de red multivendor. Los roles actuales del backend son viewer, operator y admin. La prioridad operativa exacta entre tareas no está fijada por el usuario; el primer alcance acordado es panel, equipos, monitoreo, alertas y administración.

## Product Purpose

NetPulse permite consultar el estado de equipos, servicios y alertas y operar infraestructura desde una consola web alimentada por FastAPI.

## Operating Context

El backend existente integra inventario, monitorización, Prometheus, NetBox, terminal y otras funciones. Tras probar otra interfaz, el usuario eligió el panel HTML/JS como única portada y pidió retirar la ruta y los recursos alternativos, sin perder sus funciones.

## Capabilities and Constraints

- Mantener todos los endpoints, autenticación JWT y permisos RBAC del motor FastAPI.
- El panel HTML es la única interfaz en `/`; `/legacy` permanece como alias para enlaces existentes.
- Retirar la ruta y los recursos de la interfaz alternativa sin desactivar el login, la navegación ni las herramientas del panel HTML.
- No presentar datos simulados como datos de la infraestructura real.

## Brand Commitments

El nombre de producto es NetPulse NOC. No se pidió eliminar su identidad ni cambiar el backend.

## Evidence on Hand

`app/static/dashboard.html` es el panel anterior. `app/main.py` publica `/api/*` y sirve la interfaz; `app/routers/monitoring.py`, `app/routers/devices.py`, `app/routers/alerts.py` y `app/routers/admin_users.py` exponen los flujos de la primera fase.

## Product Principles

1. Los problemas accionables tienen prioridad sobre los números decorativos.
2. El panel HTML utiliza el motor FastAPI existente; no crea otro motor.
3. Las mejoras del panel no dejan herramientas inaccesibles.
4. Los estados sin datos o con servicios caídos deben explicarse con honestidad.
