# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Operadores y administradores NOC que supervisan infraestructura de red multivendor. Los roles actuales del backend son viewer, operator y admin. La prioridad operativa exacta entre tareas no está fijada por el usuario; el primer alcance acordado es panel, equipos, monitoreo, alertas y administración.

## Product Purpose

NetPulse permite consultar el estado de equipos, servicios y alertas y operar infraestructura desde una consola web alimentada por FastAPI.

## Operating Context

El backend existente integra inventario, monitorización, Prometheus, NetBox, terminal y otras funciones. Tras probar la primera fase React, el usuario eligió volver al panel HTML/JS anterior como portada y mejorarlo sin perder sus funciones. React queda como alternativa opcional.

## Capabilities and Constraints

- Mantener todos los endpoints, autenticación JWT y permisos RBAC del motor FastAPI.
- El panel HTML anterior es la portada por defecto y también está disponible en `/legacy`; mejorar su login y navegación sin desactivar funcionalidades.
- React sigue disponible en `/modern` y puede activarse explícitamente en `/` con `NETPULSE_FRONTEND=react`.
- Esta decisión reemplaza la preferencia anterior de usar React por defecto; no se borra la implementación React mientras pueda compararse o recuperarse.
- No presentar datos simulados como datos de la infraestructura real.

## Brand Commitments

El nombre de producto es NetPulse NOC. No se pidió eliminar su identidad ni cambiar el backend.

## Evidence on Hand

`app/static/dashboard.html` es el panel anterior. `app/main.py` publica `/api/*` y sirve la interfaz; `app/routers/monitoring.py`, `app/routers/devices.py`, `app/routers/alerts.py` y `app/routers/admin_users.py` exponen los flujos de la primera fase.

## Product Principles

1. Los problemas accionables tienen prioridad sobre los números decorativos.
2. La nueva interfaz es un cliente del motor actual, no otro motor.
3. Cada fase es reversible y no deja funciones inaccesibles.
4. Los estados sin datos o con servicios caídos deben explicarse con honestidad.
