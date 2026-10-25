# Matriz de validación en hardware real

Fecha: 2026-10-25 · Estado: plan de aceptación; no sustituye evidencia de campo

Esta matriz cierra la parte que `scripts/predeploy_check.sh` no puede probar:
compatibilidad real de NetPulse con sistemas operativos, SSH/KEX, prompts y
subsistemas de transferencia de cada fabricante. **No declara hardware probado
cuando solo existe emulación, un parser o un test automatizado.**

## Procedimiento común y límites de seguridad

### Preparación

1. Abrir una ventana aprobada con contacto del NOC y plan de cancelación. Aunque
   las acciones son de lectura, SSH/API pueden consumir sesiones o CPU del equipo.
2. Registrar fabricante, modelo, número de serie anonimizado si la política lo
   exige, versión exacta del OS, arquitectura/supervisor y fecha/hora con zona.
3. Crear en el **equipo** un usuario temporal estrictamente read-only. No usar una
   cuenta con `enable`, `configure`, `write`, API write ni acceso a secretos. En
   NetPulse se requiere un usuario `operator` con scope al equipo para config raw
   y terminal; ese rol de la aplicación no cambia los permisos read-only del
   usuario configurado en el dispositivo.
4. Restringir el origen al colector/central, cargar la host key esperada según la
   política del sitio y confirmar puertos: API/NETCONF y SSH/SFTP pueden ser
   distintos (`port`, `cli_port`, `ssh_port`).
5. Seleccionar un equipo no crítico o canary. Capturar estado previo de CPU,
   sesiones y alarmas. Definir timeouts; punto inicial recomendado: conexión 30 s,
   operación 60 s y sesión terminal 2 min.

### Lo que no se debe ejecutar

- Ningún endpoint/botón `apply`, `deploy`, `commit`, `save`, `write memory`,
  `rollback`, upload SFTP, borrado, reboot o cambio de interfaz.
- Ningún comando que entre a modo configuración: `configure terminal`,
  `configure`, `configure session`, `/system … set`, `config system …`, ni sus
  equivalentes.
- No aceptar automáticamente prompts de confirmación ni elevar privilegios.
- No descargar ni adjuntar configuración sin redacción; config raw puede contener
  comunidades, hashes, direcciones, banners y claves aunque la cuenta sea de solo
  lectura.

Si la prueba intenta modificar el equipo, detenerla, conservar logs redactados y
abrir hallazgo. Un dry-run que envía candidato al equipo tampoco se considera de
solo lectura sin aprobación específica.

### Secuencia y evidencia mínima

Obtener un JWT de un usuario NetPulse con scope al equipo, sin guardarlo en el
acta. Para cada llamada medir tiempo de pared en tres repeticiones y anotar
`mín/mediana/máx`, código HTTP, tamaño de respuesta y criterio. Ejemplo:

```bash
# $TOKEN y credenciales no se escriben en stdout ni en el acta.
curl -sS -o /tmp/facts.json -w 'http=%{http_code} total=%{time_total}s bytes=%{size_download}\n' \
  -H "Authorization: Bearer $TOKEN" \
  "https://noc.empresa.com/api/facts/$DEVICE"

curl -sS -o /tmp/interfaces.json -w 'http=%{http_code} total=%{time_total}s bytes=%{size_download}\n' \
  -H "Authorization: Bearer $TOKEN" \
  "https://noc.empresa.com/api/facts/$DEVICE/interfaces"

curl -sS -o /tmp/running.txt -w 'http=%{http_code} total=%{time_total}s bytes=%{size_download}\n' \
  -H "Authorization: Bearer $TOKEN" \
  "https://noc.empresa.com/api/config/$DEVICE/raw"

curl -sS -o /tmp/sftp.json -w 'http=%{http_code} total=%{time_total}s bytes=%{size_download}\n' \
  -H "Authorization: Bearer $TOKEN" \
  "https://noc.empresa.com/api/sftp/$DEVICE/list?path=/"
```

Para terminal use la UI o el cliente JSON del kit predeploy: debe observarse
`connected`, prompt estable, un comando read-only de la tabla y
`closed(code=operator)` tras cerrar. Nunca copie el JWT de la URL a capturas o
logs. Compare facts, interfaces y configuración con una sesión directa aprobada;
NetPulse no debe inventar `Unknown`, cero o una lista vacía como si fueran datos
reales.

Anotar siempre:

- versión OS y modelo; driver/`device_type` seleccionado;
- transporte y puerto; algoritmos KEX, host-key y cipher finalmente negociados
  (sin claves); prompt exacto y si cambia al paginar;
- timeout configurado, cada tiempo medido y número de reintentos;
- códigos HTTP/eventos WS, clasificación del error y si quedaron sesiones vivas;
- SFTP soportado, no soportado o bloqueado por política;
- discrepancia entre NetPulse y el comando nativo de referencia.

### Criterio común de cierre

Una fila queda **PASS** solo con evidencia fechada del hardware indicado. `501`
en SFTP es aceptable únicamente cuando el equipo o la cuenta realmente no ofrece
el subsistema y NetPulse devuelve el motivo honesto; una lista vacía fabricada no
lo reemplaza. Un timeout, dato parcial no declarado, traceback, credencial en log
o sesión huérfana es FAIL.

Para cerrar un hallazgo: asignar dueño y severidad, conservar salida redactada y
versión exacta, reproducir tras el cambio, repetir las seis filas del fabricante
y añadir enlace a la evidencia. No cambiar “no probado” a “probado” por una
prueba unitaria o por otro modelo/versión del mismo fabricante.

## Cisco IOS / IOS-XE — driver `ios`

| Prueba | Acción read-only y evidencia | Criterio PASS |
|---|---|---|
| Facts | `GET /api/facts/{id}`; contrastar con `show version` y `show inventory`. | Hostname, IOS/IOS-XE, modelo, serie, uptime e interfaces coherentes; campos no disponibles se declaran, no se rellenan. Tres tiempos anotados. |
| Interfaces | `GET /api/facts/{id}/interfaces`; contrastar con `show interfaces status` o `show interfaces`. | Conteo y estado admin/operativo de una muestra crítica coinciden; velocidad/MTU ausentes quedan explícitos. |
| Config raw | `GET /api/config/{id}/raw`; contraste visual con `show running-config` usando salida paginada desactivada solo si la política permite el comando (`terminal length 0` es de sesión, no persistente). | HTTP 200, texto no vacío, sin mensaje `Config no disponible`; inicio/fin y secciones muestreadas coinciden. |
| SFTP | Listar `/` o un directorio permitido mediante `/api/sftp/{id}/list`. | 200 con entradas reales, o 501 documentado si IOS/servidor no tiene subsistema SFTP habilitado. Nunca probar upload. |
| Terminal | Esperar `connected`; ejecutar `show clock` y `show version`; cerrar desde cliente. | Prompt IOS correcto, salida completa, `closed(operator)` y ninguna sesión VTY huérfana. |
| Tiempos | Registrar SSH connect/KEX/auth, facts, interfaces, raw, SFTP y terminal en tres corridas. | Cada operación queda bajo el timeout acordado y sin crecimiento progresivo; cualquier excepción se clasifica. |

## Juniper Junos — driver `junos`

| Prueba | Acción read-only y evidencia | Criterio PASS |
|---|---|---|
| Facts | API facts; contrastar con `show version` y `show chassis hardware`. Registrar si NAPALM usa NETCONF/830. | Hostname, modelo, serie, versión Junos y uptime coherentes. NETCONF hello y capabilities completan dentro del timeout. |
| Interfaces | API interfaces; contrastar con `show interfaces terse`. | Nombres Junos y estados admin/link coinciden, incluidas unidades lógicas sin duplicados engañosos. |
| Config raw | API raw; contrastar con `show configuration | display set` o formato jerárquico acordado. | Config legible, no vacía y semánticamente equivalente; el formato devuelto queda anotado. |
| SFTP | Listado de un directorio permitido de la cuenta. | 200 real o 501 honesto si el servicio/política lo deshabilita; sin intentar SCP/upload como sustituto. |
| Terminal | `show system uptime` y `show version`; no entrar a `configure`. | Prompt operativo (`>`), nunca prompt de configuración (`#` tras `configure`); cierre limpio y sesión liberada. |
| Tiempos | Medir TCP/SSH o NETCONF, RPCs, SFTP y WS. Anotar KEX y host-key. | Tres muestras dentro del timeout; no hay espera por paginación/prompts interactivos. |

## Arista EOS — driver `eos`

| Prueba | Acción read-only y evidencia | Criterio PASS |
|---|---|---|
| Facts | API facts por eAPI; contrastar con `show version`. Registrar puerto eAPI y validación TLS. | Modelo, serial, EOS, hostname y uptime coinciden; certificado eAPI se valida según política. |
| Interfaces | API interfaces; contrastar con `show interfaces status`. | Conteo/estado/velocidad de Ethernet, Port-Channel y Management coherentes, sin confundir interfaces lógicas. |
| Config raw | API raw; contrastar con `show running-config`. | 200 y texto real completo; no contiene envoltorio/error de eAPI presentado como configuración. |
| SFTP | Confirmar que usa `cli_port`/`ssh_port` (normalmente 22), no el `port` eAPI 443. | 200 real o 501 esperado; evidencia demuestra que nunca intentó SFTP contra 443. |
| Terminal | Confirmar que SSH usa 22/override; ejecutar `show clock` y `show version`. | `connected.port` es SSH, prompt EOS estable y cierre limpio. |
| Tiempos | Separar latencia eAPI HTTPS de SSH/KEX y registrar tres muestras. | Ambas rutas quedan bajo su timeout; no se “arregla” TLS con verificación deshabilitada. |

## MikroTik RouterOS — drivers `ros` / `mikrotik`

| Prueba | Acción read-only y evidencia | Criterio PASS |
|---|---|---|
| Facts | API facts; contrastar con `/system resource print` y `/system routerboard print`. Registrar si se usó NAPALM o fallback `librouteros`. | Versión, board/modelo, uptime e identidad coinciden; fallback se declara y no oculta campos no medidos. |
| Interfaces | API interfaces; contrastar con `/interface print detail`. | Interfaces y flags running/disabled coinciden; bridges/VLANs no se duplican ni se marcan activas por defecto. |
| Config raw | API raw; contrastar con `/export show-sensitive=no` (o equivalente aprobado). | Export no vacío y sin secretos; si la versión no soporta el filtro, no capturar config hasta acordar redacción. |
| SFTP | Debe usar `cli_port`/`ssh_port` (normalmente 22), nunca API 8728/8729. | 200 real o 501 honesto. Fallo de KEX/auth es FAIL diagnosticado, no “SFTP no soportado”. |
| Terminal | SSH read-only; ejecutar `/system clock print` y `/system resource print`; no usar rutas `set/add/remove`. | `connected` llega dentro del timeout, prompt RouterOS correcto, puerto no es 8728/8729 y cierre limpio. |
| Tiempos | Medir por separado API RouterOS y SSH; KEX lento debe quedar cuantificado. | Tres muestras bajo límites acordados; no ampliar timeout sin registrar causa y máximo observado. |

## HP/Aruba — drivers `procurve`, `comware`, `hpe`, `aruba`

| Prueba | Acción read-only y evidencia | Criterio PASS |
|---|---|---|
| Facts | API facts; contrastar con `show system`, `show version` y comando de inventario propio de la familia. Anotar ProCurve/ArubaOS-Switch vs Comware. | Driver/device_type corresponde a la familia real; modelo, serie, OS y uptime coinciden, sin fallback Cisco silencioso. |
| Interfaces | API interfaces; contrastar con `show interfaces brief`/`display interface brief`. | Nombres, estado, velocidad y VLAN/lag muestreados coinciden con la sintaxis de esa familia. |
| Config raw | API raw; contrastar con `show running-config` (ProCurve/Aruba) o `display current-configuration` (Comware). | Config real no vacía; el parser no mezcla prompts/comandos ni devuelve error como contenido. |
| SFTP | Listar solo un directorio permitido; confirmar soporte de la versión/modelo. | 200 real o 501 honesto y justificado. No habilitar SFTP durante la prueba sin cambio aprobado. |
| Terminal | Ejecutar reloj y versión con comandos de la familia; nunca `system-view`/`configure`. | Prompt y paginación manejados sin elevar privilegios; cierre limpio y sesión liberada. |
| Tiempos | Registrar familia, firmware, KEX/cipher y tres tiempos por operación. | Bajo timeout, sin fallback/telnet inesperado. Si solo Telnet funciona, abrir hallazgo de seguridad; no aprobarlo como equivalente. |

## Fortinet FortiOS — driver `fortios`

| Prueba | Acción read-only y evidencia | Criterio PASS |
|---|---|---|
| Facts | API facts si está soportada y `/api/firewall/{id}/status`; contrastar con `get system status`. | Modelo, serial, FortiOS, hostname/HA y uptime coherentes; VDOM utilizado queda anotado. |
| Interfaces | `/api/firewall/{id}/interfaces` y/o API facts interfaces; contrastar con `get system interface physical`. | Interfaces y estado coinciden en el VDOM correcto; HA no duplica datos sin identificar miembro. |
| Config raw | `/api/firewall/{id}/config` o endpoint raw aplicable; contrastar con `show full-configuration` solo si la política permite capturarlo. | Texto no vacío, del VDOM/ámbito correcto y redactado; tokens/API keys nunca aparecen en evidencia. |
| SFTP | Confirmar si el appliance y la política ofrecen subsistema SFTP a la cuenta. | 200 real o 501 esperado; una denegación 401/403/502 se investiga y no se reclasifica como 501. |
| Terminal | SSH read-only; `get system status` y `get system performance status`; no ejecutar `config`, `execute` mutante ni `diagnose` invasivo. | Prompt/VDOM correctos, salida completa y cierre limpio sin sesión admin residual. |
| Tiempos | Separar REST HTTPS y SSH; registrar certificado, versión API, VDOM, KEX y tres muestras. | Operaciones bajo timeout y sin desactivar TLS. Rate limit o HA failover observado abre hallazgo. |

## Estado actual por driver

“Lab” significa únicamente la evidencia documentada existente en el repositorio;
no afirma que se haya repetido durante esta entrega. “Hardware” exige un chasis o
appliance físico identificado y evidencia de la matriz completa.

| Driver | Estado de lab conocido | Hardware físico | Estado honesto / pendiente |
|---|---|---|---|
| `ios` | **Probado parcialmente en lab FRRouting Cisco-like**: terminal/SFTP y rutas básicas; FRR no reproduce facts de IOS. | **No probado.** | Ejecutar matriz completa en IOS y al menos una versión IOS-XE; no promover datos FRR como Cisco real. |
| `junos` | **Lab sintético FRR etiquetado Juniper**, no Junos/NETCONF real. | **No probado.** | Validar NETCONF 830, RPCs, config y terminal en Junos real. |
| `eos` | Imagen vEOS documentada, pero el lab actual en Apple Silicon figura offline/no verificado de extremo a extremo. | **No probado.** | Repetir en vEOS funcional y luego hardware; verificar separación eAPI 443/SSH 22. |
| `ros` / `mikrotik` | **Probado parcialmente en RouterOS emulado**: fallback API; SSH/KEX/SFTP presenta limitaciones documentadas. | **No probado.** | Validar RouterOS físico, KEX, `cli_port`, export sin secretos y cierre terminal. |
| `procurve` / `comware` / `hpe` / `aruba` | Parsers/mapeos presentes; **sin lab de fabricante acreditado**. | **No probado.** | Probar por separado ArubaOS-Switch/ProCurve y Comware; no tratarlos como una sola CLI. |
| `fortios` | Servicio/router REST implementado; **sin FortiGate de lab acreditado**. | **No probado.** | Validar appliance/VM autorizado, VDOM, HA, TLS API, config y SSH read-only. |

Hasta completar estas filas, la aceptación central puede ser PASS mientras la
compatibilidad de hardware permanece **pendiente**, nunca “verde por inferencia”.
