# Verificación SFTP en vivo contra el laboratorio

Fecha: 2026-10-24 (America/Mexico_City)  
Rama: `feat/sftp-verif`  
API probada: `http://127.0.0.1:8096`

## Alcance y método

La verificación se ejecutó end-to-end, sin mocks, contra los contenedores reales del laboratorio que ya estaban activos. Se arrancó NetPulse desde este worktree con Uvicorn, se creó únicamente durante la prueba el usuario administrador `demo6`, se autenticaron llamadas HTTP reales con JWT y al terminar se retiraron el usuario, el inventario temporal y el proceso.

El worktree no tenía `config/users.yaml` ni `config/devices.yaml` al comenzar. Por eso se creó un inventario local temporal —sin leer la configuración del repositorio principal— con estos destinos del laboratorio:

- `cisco-core-01`: `127.0.0.1:2222`, usuario/contraseña `admin`.
- `switch-access-01`: `127.0.0.1:2224`, usuario/contraseña `admin`.
- `mikrotik-edge-01`: `port: 8728`, `cli_port: 2225`, usuario `admin`, contraseña vacía.

Para credenciales incorrectas y red se modificó solo esa copia local temporal, se hizo la petición y se restauró inmediatamente. No se modificó código de la aplicación.

## Resultados

| Caso | Esperado | Real | Evidencia |
|---|---|---|---|
| Prerrequisito backend | Clasificación nueva y `_API_PORTS_BY_DRIVER`. | **PASA.** `app/routers/sftp.py` contiene `_API_PORTS_BY_DRIVER`, resolución `cli_port` antes de `port` y el literal del error 501. | Historial: `67630df fix(sftp): puerto SSH correcto y errores honestos por causa`; versión integrada `d322dd8`. |
| 1. MikroTik, `/`, puerto SSH | Intentar `2225`, nunca `8728`; ante fallo, causa clara y sin traceback. | **PASA con fallo clasificado 502.** HTTP `502`; detail literal: `No se pudo conectar a mikrotik-edge-01 (127.0.0.1:2225): No existing session`. El log confirma `127.0.0.1:2225`, `driver=ros`, `api_ignored=None`. El socket TCP 2225 aceptó conexión; Paramiko no logró establecer sesión con RouterOS. No se intentó 8728. | `GET /api/sftp/mikrotik-edge-01/list?path=%2F`; respuesta JSON con `detail`, sin traceback. Coherente con la nota del laboratorio de que OpenSSH funciona con RouterOS y Paramiko no negocia su KEX. |
| 2a. `cisco-core-01` FRR | HTTP 501 y `SFTP no soportado por este equipo (cisco-core-01)…`; nunca 500 ni `[]`. | **FALLA CONTRA LA PREMISA DEL CASO.** HTTP `200`, `success: true`, ruta `/`, 18 elementos reales. El contenedor sí ofrece SFTP. | `GET /api/sftp/cisco-core-01/list?path=%2F`; aparecieron `bin`, `dev`, `etc`, …, `.dockerenv`. Dentro del contenedor: `Subsystem sftp /usr/lib/ssh/sftp-server` y el binario existe. |
| 2b. `switch-access-01` FRR | HTTP 501 y `SFTP no soportado por este equipo (switch-access-01)…`; nunca 500 ni `[]`. | **FALLA CONTRA LA PREMISA DEL CASO.** HTTP `200`, `success: true`, ruta `/`, 18 elementos reales. El contenedor sí ofrece SFTP. | `GET /api/sftp/switch-access-01/list?path=%2F`; listado real equivalente. Dentro del contenedor: `Subsystem sftp /usr/lib/ssh/sftp-server`. |
| 3. Credenciales incorrectas | HTTP 401, `Autenticación SSH fallida`. | **PASA.** HTTP `401`; detail literal: `Autenticación SSH fallida en cisco-core-01: revisa credenciales`. | En la copia temporal se cambió solo la contraseña de `cisco-core-01` a `incorrecta-demo6`; petición real y restauración inmediata. |
| 4. Fallo de red | HTTP 504/502 con detail honesto; jamás `[]` exitoso. | **PASA.** HTTP `502`; detail literal: `No se pudo conectar a cisco-core-01 (127.0.0.1:2299): [Errno None] Unable to connect to port 2299 on 127.0.0.1`. | En la copia temporal se cambió solo `cli_port` a `2299`, puerto cerrado; petición real y restauración inmediata. |
| Ruta `/` de un equipo que responde | Evidencia real de `/list?path=/`. | **PASA.** Cisco y switch respondieron HTTP 200 con `path: "/"` y 18 entradas reales cada uno. | Los cuerpos incluyeron directorios `bin`, `dev`, `etc`, `home`, … y archivo `.dockerenv`; no fue una respuesta simulada. |
| 5. Banner gris de UI para 501 | Ante un 501 real, mostrar banner gris `SFTP no soportado por este equipo`. | **NO VALIDADO en navegador.** No hubo herramienta de navegador disponible en esta sesión y, además, ningún equipo real probado devolvió 501. | Inspección estática solamente: la UI integrada contiene el texto y la rama `status === 501` con `tone: 'gray'`; esto no sustituye una prueba visual real. |

## Hallazgos y reproducción

### FRR del laboratorio sí soporta SFTP

La expectativa de que Cisco/switch FRR devolvieran 501 no coincide con los contenedores actualmente levantados. Ambos publican en `/etc/ssh/sshd_config`:

```text
Subsystem sftp /usr/lib/ssh/sftp-server
```

El binario `/usr/lib/ssh/sftp-server` está instalado y las peticiones reales listaron `/`. Por ello no es correcto forzar un 501 basándose en el driver o nombre del equipo: el backend detecta capacidad en vivo y el resultado 200 es honesto.

**Reproducción exacta:** con inventario apuntando a Cisco `127.0.0.1:2222` o switch `127.0.0.1:2224`, credenciales `admin/admin`, enviar `GET /api/sftp/<device>/list?path=/` con JWT de operador/admin. Resultado observado: HTTP 200 con 18 elementos.

**Fix propuesto, no aplicado:** si el laboratorio necesita validar el contrato 501, añadir un dispositivo/contenedor SSH real sin subsistema SFTP o construir una variante FRR de prueba que elimine/comente `Subsystem sftp` (y, si hace falta, retire el servidor SFTP). No clasificar FRR indiscriminadamente como “sin SFTP”, porque el laboratorio actual demuestra lo contrario.

### MikroTik usa 2225 pero Paramiko no completa la sesión

El backend respetó `cli_port: 2225`; el detail y el log demuestran que no usó el puerto API 8728. La conexión TCP a 2225 fue aceptada, pero Paramiko devolvió `No existing session`, clasificado como HTTP 502. Esto concuerda con la documentación local del lab: OpenSSH funciona con RouterOS y Paramiko no negocia su KEX.

**Fix propuesto, no aplicado:** si se requiere SFTP operativo con este RouterOS, validar primero que RouterOS exponga subsistema SFTP y luego habilitar explícitamente algoritmos compatibles en Paramiko o usar un cliente SSH compatible. Mantener el 502 actual mientras no exista sesión, pues no debe presentarse un listado simulado.

## NO validado

1. **Banner gris 501 en navegador:** no hubo herramienta de navegador disponible y los dispositivos reales FRR devolvieron 200, no 501. La presencia del código UI se inspeccionó, pero no se afirma funcionamiento visual.
2. **Respuesta 501 contra un equipo real sin SFTP:** ningún dispositivo probado carecía realmente del subsistema; los dos FRR lo tienen instalado. Hace falta un destino SSH real sin SFTP para validar esa clasificación end-to-end.

## Limpieza

- Uvicorn de la prueba detenido.
- Puerto TCP 8096 verificado sin proceso escuchando mediante `lsof`.
- `config/users.yaml` y `config/devices.yaml` temporales eliminados; el estado inicial era que ambos no existían en este worktree.
- Inventarios alterados para los casos 3 y 4 se restauraron antes de eliminar la copia temporal.
- No se cambió código de la aplicación ni se modificaron otros worktrees.
- Suite completa: `286 passed` (`PYTHONPATH=. /Users/doker/netpulse-noc/.venv/bin/pytest tests/ -q`). La tarjeta anticipaba 301, pero esta rama recolectó y pasó 286 pruebas; no se oculta la diferencia.
