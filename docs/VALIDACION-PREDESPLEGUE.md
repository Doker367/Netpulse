# Validación pre-despliegue

Fecha: 2026-10-25 · Alcance: central NetPulse, proxy, colector y estado operativo

`scripts/predeploy_check.sh` ejecuta diez comprobaciones observables contra un
central local, de staging o de producción. Cada fila termina en **PASS**,
**FAIL** o **SKIP** y el proceso devuelve un código distinto de cero si existe
al menos un FAIL. Un SKIP no se convierte en éxito: identifica exactamente qué
entrada o recurso faltó.

El script no imprime contraseñas, JWT, API keys ni la línea de log que pudiera
contener un token. La opción `--json` conserva la salida legible y añade al final
un objeto JSON compacto con las diez filas y los totales.

## Requisitos y uso

Requiere Bash, `curl`, `python3` y `openssl`. Docker Compose solo es necesario
para las comprobaciones 7 y 8. Las credenciales smoke son opcionales, pero sin
ellas la prueba WebSocket y la lectura autenticada del site quedan en SKIP.

```bash
export NETPULSE_SMOKE_USER='usuario-smoke'
export NETPULSE_SMOKE_PASSWORD='leer-desde-el-gestor-de-secretos'
# Recomendado para no elegir implícitamente el primer equipo visible:
export NETPULSE_SMOKE_DEVICE='router-autorizado-01'

scripts/predeploy_check.sh \
  --url https://noc.empresa.com \
  --domain noc.empresa.com \
  --expect-ip 203.0.113.10 \
  --collector-config /etc/netpulse/collector.yaml \
  --compose deploy/docker-compose.prod.yml \
  --json
```

Flags:

| Flag | Efecto |
|---|---|
| `--url URL` | Obligatorio. Base HTTP(S) del central; no admite credenciales embebidas. |
| `--domain nombre` | Habilita DNS y TLS con SNI/hostname; también sustituye al hostname de `--url` como destino de TCP 443. |
| `--expect-ip IP` | Opcional; exige que una de las respuestas A/AAAA sea exactamente esta IP. Requiere `--domain`. |
| `--collector-config ruta` | Ejecuta `python3 -m app.remote_collector … --check` de verdad y contrasta su `site_id` con `/api/remote/sites`. |
| `--compose ruta` | Inspecciona el contenedor `api` y los logs del servicio cuyo nombre contenga `caddy`, `nginx` o `proxy`. |
| `--json` | Añade un resumen JSON; no sustituye las líneas legibles. |

Códigos de salida: `0` si no hay FAIL (puede haber SKIP), `1` si una o más
comprobaciones fallan y `2` para uso inválido o dependencia obligatoria ausente.
Debe archivarse la salida completa y el código de salida, no solo el total.

## Las diez dudas y su evidencia

| # | Duda | Comprobación | Evidencia esperada | Cómo interpretar FAIL |
|---:|---|---|---|---|
| 1 | ¿El nombre apunta al central correcto? | Resolver `--domain`; si existe `--expect-ip`, comparar direcciones normalizadas. | Una o más A/AAAA y coincidencia con la IP esperada. | DNS roto, propagación incompleta o dominio apuntando a otro host. Sin IP esperada solo se demuestra resolución, y el detalle lo declara. |
| 2 | ¿Se llega por 443 y HTTP fuerza HTTPS? | TCP real al host de `--domain` o, si falta, al host de `--url`; petición HTTP sin seguir redirect y validación de `Location: https://…`. | Socket 443 abierto y 301/302/307/308 hacia HTTPS. | Firewall/listener caído o tráfico en claro servido sin redirección. En un lab HTTP deliberado, el FAIL es esperado y no se maquilla. |
| 3 | ¿TLS es confiable y vigente? | `openssl s_client` con SNI, validación de cadena y hostname; lectura de emisor y fechas. | Cadena válida, hostname correcto, certificado vigente, emisor y días restantes. Menos de 15 días produce un aviso dentro de PASS. | CA no confiable, cadena incompleta, SNI/hostname incorrecto, certificado vencido o 443 no accesible. |
| 4 | ¿El proxy entrega cabeceras mínimas? | GET `/` y revisión del último bloque de cabeceras tras redirects. | `Strict-Transport-Security`, `X-Content-Type-Options` y `Referrer-Policy`. | El proxy no aplica el hardening o la petición no llegó a la portada. |
| 5 | ¿API y estáticos básicos están desplegados? | GET `/api/health`, `/`, `/static/js/core.js`; además tamaño real del JS. | HTTP 200 en los tres y `core.js` mayor de 10 KiB. | API no saludable, portada/ruta rota o asset vacío/truncado/equivocado. |
| 6 | ¿El proxy y backend soportan terminal WebSocket real? | Login real, JWT recibido, selección de `NETPULSE_SMOKE_DEVICE` (o primer equipo visible), handshake `ws/wss`, evento `connected`, cierre solicitado y evento `closed(operator)` más close frame. | Sesión SSH/PTY real conectada y cierre limpio; el JWT nunca se imprime. | Credenciales, inventario, scope, proxy Upgrade, TLS WebSocket, SSH o limpieza de sesión fallaron. Sin usuario/password o sin equipo visible es SKIP, no PASS. |
| 7 | ¿El contenedor API está endurecido sin romper persistencia? | Con `--compose`: estado health, uid, `ReadonlyRootfs`, tipos de mounts y escritura temporal como el usuario del contenedor. | `healthy`, uid 10001, rootfs RO y `/app/{config,runtime,audit,backups}` como binds RW realmente escribibles. | Despliegue no saludable, root/uid incorrecto, raíz mutable o permisos/montajes que causarán errores 500 y pérdida de estado. |
| 8 | ¿El proxy evita registrar JWT de query string? | Lee las últimas 1000 líneas por servicio Caddy/nginx/proxy y cuenta `token=` sin mostrar coincidencias. | Cero coincidencias. | Hay riesgo de secreto persistido en logs. Rotar tokens potencialmente expuestos y corregir logging antes de continuar. |
| 9 | ¿El colector pertenece al site y reporta fresco? | `--check` real con su YAML/key y GET autenticado `/api/remote/sites`. | `--check` exit 0 y el `site_id` aparece con `contact: fresh`. | Config/clave/TLS/IDs incorrectos, colector sin sincronizar o contacto stale. Si falta JWT de usuario, se informa SKIP parcial aunque `--check` haya pasado. |
| 10 | ¿El host está operable hoy? | Offset NTP si `timedatectl` lo expone, disco libre, `PRAGMA integrity_check` de bases accesibles y edad del archivo de backup más reciente. | Skew `<5 s`, disco `>20 %`, SQLite `ok` y backup `<7 días`. | Reloj no sincronizado, riesgo de llenar disco, corrupción de BD o recuperación fuera del RPO. Los recursos ausentes/inaccesibles quedan SKIP; un directorio de backups accesible pero sin artefactos reconocidos es FAIL. |

## Mapa directo al script

| Check | Bloque de salida | Entradas que lo habilitan |
|---:|---|---|
| 1 | `DNS` | `--domain`, opcional `--expect-ip` |
| 2 | `Red y redirección` | `--url`; `--domain` sustituye el hostname de prueba |
| 3 | `TLS` | `--domain` |
| 4 | `Cabeceras HTTP` | `--url` |
| 5 | `Aplicación HTTP` | `--url` |
| 6 | `Terminal WebSocket` | variables smoke; opcional `NETPULSE_SMOKE_DEVICE` |
| 7 | `Contenedor y permisos` | `--compose` y daemon Docker disponible |
| 8 | `Logs del proxy` | `--compose` y daemon Docker disponible |
| 9 | `Colector remoto` | `--collector-config`; variables smoke para leer sites |
| 10 | `Operación` | host local; rutas `audit/`, `runtime/`, `backups/` si son accesibles |

Los checks 7, 8 y la parte de ficheros del 10 observan **el host donde se
lanza el comando**. Por ello deben ejecutarse en el nodo central, no desde una
laptop remota, cuando se busca evidencia de producción.

## Ejecución local con staging y mkcert

El staging descrito en `docs/STAGING.md` usa `noc.staging.local`, resolución
local y una CA de mkcert. Instale la CA en el trust store del cliente antes de
validar; no use `-k`. Si el proceso usa un bundle explícito, expóngalo a las
tres bibliotecas usadas por el kit:

```bash
export SSL_CERT_FILE="$(mkcert -CAROOT)/rootCA.pem"
export CURL_CA_BUNDLE="$SSL_CERT_FILE"
export NETPULSE_SMOKE_USER='admin-staging'
export NETPULSE_SMOKE_PASSWORD='…'
export NETPULSE_SMOKE_DEVICE='equipo-lab-autorizado'

scripts/predeploy_check.sh \
  --url https://noc.staging.local \
  --domain noc.staging.local \
  --expect-ip 127.0.0.1 \
  --compose deploy/docker-compose.prod.yml \
  --json
```

`--collector-config` solo se añade si existe un colector local con clave propia.
Sin equipo SSH real, el check 6 debe ser SKIP por ausencia de equipo o FAIL si
se eligió un equipo que no conecta; no se acepta un `connected` simulado.

Para el lab HTTP directo puede ejecutarse:

```bash
scripts/predeploy_check.sh --url http://127.0.0.1:8081 --json
```

Aquí DNS/TLS/Docker/colector pueden quedar SKIP y HTTP→HTTPS debe fallar si el
servidor deliberadamente sirve HTTP plano. Esa corrida valida el comportamiento
del kit y los endpoints, no equivale a aceptar un despliegue TLS.

### Evidencia de esta entrega

Corrida real del 2026-10-25 contra la URL solicitada
`http://127.0.0.1:8081`: **PASS=2, FAIL=1, SKIP=7**. Pasaron cabeceras y los
tres recursos HTTP (`core.js` midió 91 791 bytes); falló correctamente el check
2 porque `127.0.0.1:443` no estaba abierto y el HTTP 200 no redirigía. DNS, TLS,
WebSocket sin credenciales, Docker, colector y recursos operativos ausentes
quedaron SKIP con motivo.

El listener 127.0.0.1:8081 estaba iniciado desde el repo principal, que esta
tarjeta prohíbe modificar. Para comprobar login/WS sin tocarlo se creó un
`demo-predeploy-12v` aleatorio y un inventario Cisco de lab en un runtime aislado,
servido en `[::1]:8081`. Se respaldó/restauró el estado original de
`config/users.yaml` (ausente antes y después). Resultado: **PASS=3, FAIL=1,
SKIP=6**; el check 6 recibió `connected`, `closed(operator)` y close frame 1000.
El único FAIL volvió a ser el HTTP local sin TLS. Se detuvo el servidor y se
eliminaron usuario, runtime y artefactos temporales.

## Ejecución en staging Linux

Ejecutar desde la raíz sincronizada dentro de la VM/host Linux, después de que
Compose esté healthy:

```bash
export NETPULSE_SMOKE_USER='admin-staging'
read -rsp 'Password smoke: ' NETPULSE_SMOKE_PASSWORD; export NETPULSE_SMOKE_PASSWORD; echo
export NETPULSE_SMOKE_DEVICE='router-staging-01'

scripts/predeploy_check.sh \
  --url https://noc.staging.example.net \
  --domain noc.staging.example.net \
  --expect-ip 192.0.2.20 \
  --compose deploy/docker-compose.prod.yml \
  --collector-config /etc/netpulse/collector.yaml \
  --json | tee "predeploy-staging-$(date +%F-%H%M).log"
rc=${PIPESTATUS[0]}
unset NETPULSE_SMOKE_PASSWORD
exit "$rc"
```

No reutilizar claves ni inventario de producción. Revisar cada SKIP y adjuntar
su justificación al acta de staging.

## Ejecución en producción

1. Crear o habilitar temporalmente un usuario de smoke con el mínimo scope y un
   equipo expresamente autorizado. La credencial del **equipo** debe ser de solo
   lectura.
2. Ejecutar en una ventana acordada desde el nodo central para observar mounts,
   bases, disco, backup y logs locales.
3. Capturar salida y exit code; retirar el usuario smoke si era temporal.
4. No aprobar mientras exista un FAIL. Aceptar un SKIP solo con responsable,
   motivo y evidencia alternativa fechada.

```bash
umask 077
export NETPULSE_SMOKE_USER='predeploy-temporal'
read -rsp 'Password smoke: ' NETPULSE_SMOKE_PASSWORD; export NETPULSE_SMOKE_PASSWORD; echo
export NETPULSE_SMOKE_DEVICE='router-canary-01'

scripts/predeploy_check.sh \
  --url https://noc.empresa.com \
  --domain noc.empresa.com \
  --expect-ip 203.0.113.10 \
  --compose deploy/docker-compose.prod.yml \
  --collector-config /etc/netpulse/collector.yaml \
  --json | tee "predeploy-prod-$(date +%F-%H%M).log"
rc=${PIPESTATUS[0]}
unset NETPULSE_SMOKE_PASSWORD
exit "$rc"
```

No publicar el log sin revisarlo: el kit redacta secretos conocidos, pero
nombres de host, IP, emisor TLS y estado del site son datos operativos.

## Fuera de alcance

- **Hardware físico multivendor:** el script solo abre una terminal al equipo
  canary. Facts, interfaces, config y peculiaridades por fabricante requieren
  la matriz de `docs/MATRIZ-VALIDACION-HARDWARE.md`.
- **Carga y capacidad reales:** tres GET y una sesión no miden concurrencia,
  latencia bajo carga, crecimiento de SQLite ni siete días de estabilidad.
- **Restauración y offsite S3/MinIO:** se verifica edad del backup local, no que
  el archivo restaure ni que una copia offsite exista, sea íntegra o tenga sus
  claves disponibles. Debe realizarse un restore drill separado.
- **HA y failover:** el diseño actual es un central y un colector por site; este
  kit no crea redundancia.
- **ACME/renovación futura:** se valida el certificado servido hoy y se avisa a
  15 días; no fuerza ni demuestra una renovación automática.
