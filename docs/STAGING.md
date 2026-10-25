# Staging Linux local (macOS)

## Estado y elección

Lima con Virtualization.framework (VZ), Ubuntu 24.04, 4 vCPU, 8 GiB y disco
persistente de 40 GiB. Es automatizable por CLI, permite rsync/SSH y ejecuta
Docker **dentro de Linux**, sin compartir el socket Docker ni montar el host.
UTM requiere más configuración manual; OrbStack añade otro gestor de runtime.
No se crea un stack alternativo: se usa el Compose de producción con el perfil
`monitoring`. En esta rama aún no existe ese Compose ni docs/DOCKER.md.
`staging_up.sh` sale **2**, antes de modificar nada, cuando falta producción.

## Requisitos y contrato de integración

- macOS 13+ con VZ, 8 GiB libres para la VM, disco e Internet para Ubuntu/apt/imágenes.
- `brew install lima mkcert`; rsync, git, curl, openssl y Python del proyecto con
  sus dependencias (incluyendo PyYAML, hashing de contraseñas y websockets).
- Compose v2 en la VM (instalado con apt), compatible con `config --format json`
  y `up --wait`. Las imágenes deben publicar la arquitectura de la VM.
- Puerto local 443 libre. Lima debe poder reenviar ese puerto privilegiado:
  depende de la versión/permisos de macOS. Si no puede, `up` falla en HTTPS;
  resolver el permiso de Lima, **no** usar otro puerto simulando la URL pedida.
- Compose y fuentes deben estar versionados en esta rama: rsync usa `git ls-files`
  y copia el contenido actual, sin `.env`, inventarios, usuarios ni datos locales.
- El Compose real debe montar el directorio `config` del repo en los servicios
  que leen/escriben inventario y usuarios. No se aceptan configuraciones embebidas
  en imagen ni volúmenes anónimos de config sin adaptar y revisar el contrato.
- Un único servicio debe montar el Caddyfile real en `/etc/caddy/Caddyfile`.
  Su bloque de sitio debe ser una línea `{$DOMAIN} {`, sin directiva `tls` previa.
  Para otro host/token: `export STAGING_CADDY_HOST_TOKEN='noc.example.com'`.
  Se conserva todo el Caddyfile (upstreams, cabeceras, rutas); solo se cambia el
  host y se añade el certificado mkcert mediante un override de mounts.
  Formatos no reconocidos fallan explícitamente, no se adivinan upstreams.
- Revisar el Compose real antes del primer arranque: no debe contener credenciales
  reales ni servicios de lab; sus binds de datos deben quedar dentro de esta VM.
  El override no altera servicios, redes ni healthchecks de producción.

## Preparación (una vez)

Desde la raíz del worktree:

```bash
brew install lima mkcert
export NETPULSE_PYTHON=/Users/doker/netpulse-noc/.venv/bin/python
mkdir -p "$HOME/.local/state/netpulse-staging"
chmod 700 "$HOME/.local/state/netpulse-staging"
# Editar con sudo y añadir UNA vez, sin duplicar entradas:
sudo nano /etc/hosts
# 127.0.0.1 noc.staging.local
sudo dscacheutil -flushcache
```

Se usa resolución local por hosts (no un servidor DNS público). `.local` también
es mDNS: comprobar con `dscacheutil -q host -a name noc.staging.local` que resuelve
127.0.0.1. No modificar DNS de producción.

Crear `$HOME/.local/state/netpulse-staging/production.env` con las variables
**requeridas por el Compose real**, siguiendo su futura guía Docker. Generar
JWT, Fernet y otros secretos independientes; no copiar el `.env` de producción.
Fijar el dominio a `noc.staging.local` y las imágenes al tag/digest a probar.
No se proporciona un env ficticio porque el contrato aún no existe.
Aplicar `chmod 600` al archivo. Se transfiere a la VM, nunca al repo.

## Ciclo completo

```bash
bash scripts/staging_up.sh
bash scripts/staging_seed.sh
bash scripts/staging_smoke.sh
bash scripts/staging_down.sh
```

`up` prepara seed inicial automáticamente para evitar arrancar con usuarios
reales, crea/reanuda la VM, sincroniza fuentes, valida Compose, construye/arranca
con monitoring y espera healthchecks y HTTPS. Imprime URL, certificado y CA solo
si termina correctamente. mkcert instala una CA local (puede pedir autorización
al llavero). La clave de CA **no** se copia a la VM. Los certificados se renuevan
si quedan menos de siete días. No se usa `curl -k`.

`seed` utiliza las plantillas JSON versionadas; guarda contraseñas aleatorias
fuera del repo en `$HOME/.local/state/netpulse-staging/credentials.json` (0600).
Crea un admin para smoke, un operator con scope de grupo/site `Staging` y un
viewer con scope exclusivamente de site `StagingOther`. En el backend actual
los sites de RBAC son nombres de grupo (unión, no intersección de scopes).
Inventario sintético TEST-NET, sin contraseñas de dispositivos ni lab. No se
espera que esos equipos respondan. Solo crea archivos ausentes: repetir seed/up
no pisa usuarios/inventario ya modificados en Linux ni rota contraseñas.
El estado autoritativo tras arrancar es la VM; la copia local es el seed inicial.
Si cambias la contraseña del admin en la UI, actualiza el archivo privado para
smoke. No guardar ese archivo en git ni publicar su contenido.

`smoke` ejecuta **sin modificaciones** scripts/smoke_full.sh con HTTPS,
CA explícita para curl/Python y el admin de staging; propaga su código de salida.
**Hoy smoke_full exige equipos del lab, SFTP y terminal reales:** con este
inventario sin lab debe fallar esas comprobaciones. Un fallo no se transforma en
SKIP ni en éxito. Un smoke totalmente verde queda pendiente de un modo sin lab
implementado por el dueño de ese script, fuera del alcance de esta tarjeta.

`down` ejecuta `compose stop` y apaga Lima; nunca `down -v` ni elimina VM/disco.
Es reejecutable si la VM no existe o ya está apagada. Si falla detener Compose,
intenta apagar igualmente la VM y devuelve el error. No quita hosts ni la CA.

## Upgrade N → N+1 y rollback

1. Con N funcionando, guardar tag/digest, `git rev-parse HEAD`, env y resultado
   del smoke (incluidos fallos conocidos). No guardar secretos en el repositorio.
2. Detener servicios y respaldar **en frío**: ejecutar `compose stop` desde la VM
   usando el comando inferior; respaldar config, bases/binds y todos los volúmenes
   nombrados según la guía de producción. Un backup solo de config NO basta.
   Validar una restauración antes de cambiar de versión. No ejecutar seed para
   restaurar una base.
3. Si producción usa imágenes: cambiar en el env privado el tag N por N+1 según
   el nombre real de la variable del Compose; hacer `compose pull`, luego `up`.
   Si usa `build`, cambiar al tag fuente en este worktree con árbol limpio y
   ejecutar `up` (incluye `--build`). No operar en el repo principal.
4. Ejecutar seed (no destructivo) y smoke. Revisar migraciones, logs y permisos
   Linux; comparar con N. La prueba no se considera aprobada si hay fallos nuevos.
5. Rollback: detener, volver al env/tag N y ejecutar `up`; si hubo migración no
   reversible, restaurar **también** los datos del respaldo N antes del arranque.
   Volver a ejecutar smoke. Nunca asumir compatibilidad descendente de la BD.

Comando Compose para diagnóstico, pull, stop y backup específico del stack:

```bash
limactl shell netpulse-staging -- bash -c '
  cd /home/lima/netpulse-staging/repo &&
  docker compose --project-name netpulse-staging --env-file ../production.env \
    -f deploy/docker-compose.prod.yml -f ../override.json --profile monitoring ps
'
# Sustituir ps por pull, stop o logs --tail 100 según el paso.
```

Rsync no borra archivos remotos: si un upgrade de fuentes elimina/renombra archivos,
no ensayar sobre fuentes residuales. Con servicios parados, renombrar manualmente
el directorio remoto `repo` a una copia de respaldo **después de verificar su ruta
absoluta**, volver a ejecutar up y restaurar config/binds desde ese respaldo antes
del arranque definitivo. Para upgrades repetibles se prefieren imágenes por digest
sin builds. Este paso depende del layout de datos del Compose aún pendiente.

## Límites y validación honesta

- ARM en Apple Silicon no equivale a x86_64 del servidor: arquitectura, wheels y
  extensiones nativas pueden diferir. Repetir en Linux x86_64 real antes de desplegar.
- 4 vCPU/8 GiB sobre macOS no es un benchmark del servidor; disco y red virtuales
  cambian latencia, carga y capacidad. Ubuntu/apt e imágenes móviles no fijan bits:
  para reproducción estricta fijar digests y snapshot de paquetes.
- ICMP, raw sockets, SNMP y descubrimiento dependen de capabilities/rutas/firewall.
  NAT de Lima no reproduce VLANs, broadcast ni acceso físico a dispositivos.
- Certificado emitido por CA local, no CA pública: solo confían los clientes donde
  se instaló esa CA. No prueba ACME, renovación pública ni DNS externo.
- El forwarding explícito expone solo HTTPS en loopback del Mac; comprobar con
  `lsof -nP -iTCP:443 -sTCP:LISTEN`. Revisar exposición real si cambia Lima.
- No se han ejecutado VM, Docker, TLS ni upgrade/rollback end-to-end en esta entrega:
  faltan Compose/Caddyfile de producción. La validación sintáctica no los sustituye.

Validación local lista para repetir:

```bash
for f in scripts/staging_*.sh; do bash -n "$f"; done
bash scripts/staging_up.sh  # mientras falte producción: código 2 esperado
PYTHONPATH=. /Users/doker/netpulse-noc/.venv/bin/pytest tests/ -q
```
