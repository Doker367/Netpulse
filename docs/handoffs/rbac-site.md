# Handoff — RBAC por site/grupo (Ola 4)

## Modelo y compatibilidad

`site` se representa con el campo `group` del inventario existente (`/api/groups` y `/api/devices`); no hay otro catálogo. Se comparan nombres de grupo sin distinguir mayúsculas/minúsculas, pero se conserva el nombre original en respuestas y errores. `scope_group` autoriza un grupo; `scope_sites` autoriza una lista adicional de grupos (unión). Dispositivos sin `group` no están autorizados para usuarios restringidos. `scope_sites: []` explícito sin `scope_group` restringe a **ningún** dispositivo. Ambos campos ausentes o `null` significan acceso completo legado, sin alterar jerarquía de roles. El login emite `sub`, `role` y los claims `scope_group: str` y/o `scope_sites: [str]` solo cuando el valor no es `null`; un token sin los dos claims mantiene acceso completo. Un token ya emitido conserva sus permisos hasta caducar (JWT de 30 minutos por defecto); después de reasignar scopes, pedir nuevo login. API keys no incluyen scopes y siguen su comportamiento anterior.

**Decisión de producto:** la lectura sigue global en esta fase: un operador de site puede ver contexto y estado de toda la red para diagnosticar incidencias, pero solo operar sobre su grupo. No presentar esa visibilidad como segregación de información; restringir lectura y otras herramientas operativas es trabajo posterior.

## Endpoints y body

- `POST /api/admin/users` (admin, 201): body existente `{username, password, role, name}` acepta opcionalmente `scope_group: string|null`, `scope_sites: string[]|null`.
- `PUT /api/admin/users/{username}` (admin, 200): acepta ambos campos opcionales junto al body existente. Campo omitido conserva valor anterior; `null` lo quita; `[]` explícito deniega todos los groups si el otro campo está vacío. `GET /api/admin/users` y `GET /api/admin/users/{username}` devuelven ambos campos sin credenciales. Solo admin asigna o cambia scopes; viewer/operator reciben 403 por rol.
- `POST /api/auth/login`: body y response siguen iguales; claims del JWT reflejan scopes persistidos en `config/users.yaml`. `GET /api/auth/me` mantiene su response anterior.
- `POST /api/command/{device_id}` y `POST /api/command/bulk`: validan el grupo real del inventario antes de contactar dispositivo. En bulk, `errors` contiene entradas `{device_id, error}` y `failed_count` incluye los rechazados, sin 403 global.
- `POST /api/config/{device_id}/save`, `/backup`, `/diff`, `/deploy/dry-run`, `/deploy`, `/rollback`: protegen operaciones POST; GET permanece global. `POST /api/config/offsite/export?device_id=...` valida ese equipo; sin id conserva respuesta legada para tokens sin scope, pero para tokens restringidos devuelve `{total, results: [{device_id, result}], errors: [{device_id, error}]}` y solo exporta por id autorizado.
- `POST /api/bulk/deploy`: cada `results[]` rechazado lleva `{device_id, success:false, committed:false, diff:"", error}`; `failed` los cuenta. `POST /api/bulk/backup` acepta el array JSON de IDs del endpoint actual; en tokens restringidos, `errors[]` indica los rechazados y `total/failed` los incluyen. Body omitido sobre un token restringido enumera inventario y nunca pasa `None` al motor Nornir. `POST /api/bulk/facts`, `POST /api/bulk/status` y `GET /api/bulk/status` siguen siendo diagnósticos de lectura, no restringidos.
- `POST /api/devices`, `PUT /api/devices/{device_id}`, `DELETE /api/devices/{device_id}`: crear exige grupo permitido; actualizar exige grupo actual **y** grupo destino si cambia; borrar exige grupo actual. `POST /api/devices/lab/init` crea varios grupos implícitamente y por eso se reserva a admins sin scope (403 para restringidos).

**Errores literales:** 403 `{"detail":"device fuera de tu scope: Core"}` (grupo real), 403 `{"detail":"device fuera de tu scope: sin grupo"}` (sin grupo), 404 `{"detail":"Dispositivo 'id' no encontrado"}` para ID inexistente en token restringido. Crear o mover un dispositivo hacia grupo prohibido usa `{"detail":"device fuera de tu scope: <grupo solicitado>"}`. Init lab: `{"detail":"Inicialización global del laboratorio requiere acceso sin scope"}`. Fuera de scope se rechaza ANTES de invocar NAPALM o modificar inventario.

## Mock del flujo UI para Ola 4 (solo documentación)

1. En ADMINISTRATION > Usuarios, admin consulta `GET /api/admin/users` y `GET /api/groups` para mostrar rol y scopes actuales y proponer nombres existentes; un grupo sin equipos puede existir en `groups.yaml` aunque el listado actual solo refleje grupos de inventario.
2. Formulario «Alcance operativo»: seleccionar un grupo principal (`scope_group`) y opcionalmente grupos adicionales (`scope_sites`), con confirmación explícita «Acceso global» que envía ambos campos `null`, o «Sin grupos autorizados» que envía `scope_sites: []`. Nunca habilitar asignación al viewer; el backend aplica admin.
3. Guardar mediante `PUT /api/admin/users/{username}` con body de ejemplo `{"scope_group":"DMZ","scope_sites":["Edge"]}`; si es usuario nuevo, enviar campos en `POST /api/admin/users`. Actualizar estado desde response 200/201 y explicar que se requiere nuevo login para emitir JWT actualizado.
4. Si 403, mostrar el `detail` real y el `device_id` correspondiente en operaciones masivas, con éxito parcial explícito. No presentar los dispositivos fuera de scope como realizados.

## Pendientes y límites explícitos

- Esta ola solo impone scopes en los routers autorizados command/config/bulk/devices. Otros routers operativos (terminal, scheduler, discovery, etc.) no quedan protegidos por scope; se necesita inventario completo de rutas y política uniforme antes de afirmar aislamiento total.
- Lectura de configuraciones, inventario y telemetría sigue sin filtros; backups y offsite pueden incluir artefactos cuyos IDs ya no están en inventario: para token restringido no se exportan sin ID autorizado.
- Los cambios de scopes no revocan tokens emitidos; esperar vencimiento o planificar revocación/rotación de sesión. API keys son globales según rol legado; migrar gestión de scopes de API keys en otra fase.
- Grupo/site no tiene jerarquía ni asignación por tags, y un device sin grupo requiere admin global para ser operado/asignado. `groups.yaml` solo guarda metadatos y su listado depende de inventario actual.
