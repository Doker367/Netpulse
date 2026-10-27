# Backup offsite — contrato real y drill local

Estado: **verificado contra S3 y SFTP reales en local** (2026-10-26).
Servicio: `app/services/backup_offsite_svc.py`.
Router: `app/routers/config.py` (`/api/config/offsite/*`).
Drill reproducible: `scripts/offsite_drill.sh`.

La tarjeta 13O encontró que el servicio existía pero nunca se había probado
contra un endpoint real y **no tenía restauración**. Este documento fija lo
que realmente se publica, cómo se verifica, cómo se restaura y qué sigue sin
estar implementado.

---

## 1. Qué se sube

Solo archivos regulares del **nivel superior** de `settings.BACKUP_DIR`
(`backups/`), nunca symlinks ni subdirectorios:

| Patrón | Origen |
|---|---|
| `*.cfg` | backups de configuración de dispositivos (NAPALM/Nornir) |
| `*.json` | snapshots de dispositivo |
| `netpulse_self_*.tar.gz` | self-backup de NetPulse (`POST /api/ops/backup`) |

El manifiesto se llama `_netpulse_manifest.json` y **no** cuenta como backup.
`export_all(device_id=...)` filtra por el prefijo saneado
`<device_id>_` (espacios y caracteres raros → `_`), igual que el resto de
endpoints de backups. Los self-backup no pertenecen a ningún dispositivo y
solo se incluyen en la exportación global (`device_id` ausente).

## 2. Naming y destino

El nombre local se conserva tal cual, plano, sin carpetas por fecha:

- S3/MinIO: `s3://<bucket>/<NETPULSE_OFFSITE_S3_PREFIX>/<nombre>`
  (por defecto `netpulse/<nombre>`).
- SFTP: `<NETPULSE_OFFSITE_SFTP_DIR>/<nombre>`
  (por defecto `netpulse-backups/<nombre>`).
- Manifiesto: `<prefijo>/_netpulse_manifest.json` /
  `<dir>/_netpulse_manifest.json`.

Los backups ya llevan su timestamp en el nombre
(`<device>_<YYYYmmdd_HHMMSS>.cfg`); el destino no lo reinterpreta. Subir dos
veces el mismo nombre **sobrescribe** el objeto remoto.

## 3. Manifiesto

JSON versionado que se publica después de las subidas correctas:

```json
{
  "version": 1,
  "generated_at": "2026-10-26T17:57:24.123456+00:00",
  "files": [
    {"name": "core-1_20260101_120000.cfg", "size": 131,
     "sha256": "bf5b3986…", "uploaded_at": "2026-10-26T17:57:24.1+00:00"}
  ]
}
```

Se **fusiona** con el manifiesto previo (no borra entradas de backups
antiguos). Si no se puede leer el anterior, se parte de vacío y se avisa en
`warnings`. Un fallo al publicar el manifiesto deja `manifest:"failed"` y el
resultado global en `partial`, aunque los archivos sí se hayan subido: no se
declara `ok` una verificación que no se puede hacer.

## 4. Verificación tras subir

Por cada archivo:

1. `upload_file` / `sftp.put`.
2. `head_object` / `sftp.stat`: el tamaño remoto debe ser **exactamente** el
   local; si no, `integrity_mismatch`.
3. Hash:
   - S3: ETag MD5 cuando el objeto es de una sola parte y el backend lo
     expone. Si el ETag es multipart (`...-N`), solo se verifica tamaño.
   - SFTP: SHA-256 leyendo el objeto remoto (hasta
     `NETPULSE_OFFSITE_VERIFY_HASH_MAX_BYTES`, 16 MiB por defecto); por
     encima, solo tamaño.
4. El SHA-256 local queda siempre en `results[]` y en el manifiesto, así que
   la restauración puede verificar aunque el ETag no sea MD5.

`export_all()` devuelve `status`:

- `ok`: todos los archivos seleccionados subieron y verificaron.
- `partial`: al menos uno subió, pero hubo fallos/avisos.
- `error`: ninguno subió por un fallo del destino (credenciales, bucket,
  endpoint, timeout).
- `skipped`: offsite deshabilitado, sin destino o sin backups locales.

`local_intact` comprueba **después** de la operación que cada backup local
sigue existiendo, con el mismo tamaño y SHA-256. La exportación nunca borra
ni mueve archivos de `BACKUP_DIR`.

## 5. Restauración

`POST /api/config/offsite/restore` (solo admin) restaura a un directorio
**distinto** de `BACKUP_DIR`:

- Base: `NETPULSE_OFFSITE_RESTORE_DIR`, por defecto `backups/restored`.
- `dest` (query): subdirectorio relativo bajo esa base. Se rechazan rutas
  absolutas y cualquier componente `..`; también se resuelve el path final
  para impedir escapes por symlink. HTTP 400 si no cumple.
- `device_id` (query): restaura solo los nombres de ese dispositivo.
- `overwrite` (query, default `false`): si el archivo destino ya existe con
  **otro** contenido, el archivo se omite y se informa; con `true` se
  reemplaza. Nunca se borra un destino que no se vaya a reemplazar.
- Descarga a `.<nombre>.part`, comprueba tamaño y SHA-256 del manifiesto (o
  del backup local si existe), y solo entonces hace `os.replace`. Un archivo
  corrupto no llega nunca al destino final y el `.part` se elimina.

Salida: `{status, backend, restored, failed, dest, files[{name,size,sha256,
verified,dest}], errors, manifest}`. Si no hay objetos remotos:
`status:"empty"`, HTTP 404. Si falla la conexión: `error`, HTTP 502. Si unos
sí y otros no: `partial`, HTTP 200 con `failed>0`.

## 6. Estado honesto

`GET /api/config/offsite/status`:

- `configured` significa “hay destino configurado”, **no** “el destino
  responde”. Para comprobar de verdad: `?probe=true`, que lista objetos y
  devuelve `remote.reachable`, `remote.files`, `remote.manifest` y
  `remote.error` clasificado.
- `last_export` es el último `export_all` real de **este proceso** (se pierde
  al reiniciar la API). Si es `null`, no se ha exportado nada desde el
  arranque; no se inventa un estado verde.
- Nunca se devuelven secretos: solo bucket/endpoint/prefix, host/puerto/dir y
  el tipo de autenticación (`key`, `password`, `none`).

## 7. Errores y códigos HTTP

Los fallos remotos se clasifican; el router responde 502 con un `detail`
legible y el objeto estructurado, nunca un 500 genérico:

| `error.code` | Causa típica | HTTP |
|---|---|---|
| `auth_failed` | access key/secret o usuario/clave incorrectos | 502 |
| `bucket_missing` | bucket S3 inexistente | 502 |
| `endpoint_unreachable` | endpoint caído, DNS/SSH inaccesible | 502 |
| `timeout` | connect/read timeout agotado | 502 |
| `permission_denied` | no se puede crear el directorio SFTP | 502 |
| `host_key_mismatch` | clave de host SFTP distinta | 502 |
| `not_found` | objeto remoto inexistente (restore/verify) | 404/200 |
| `integrity_mismatch` | tamaño/SHA no coincide | 200 partial / 502 total |

Un fallo de conexión/credenciales es **fatal**: se deja de intentar el resto
de archivos, se devuelve el código real y `local_intact:true`. Un fallo de un
archivo concreto no impide intentar los demás (`partial`).

Timeouts configurables (leídos al importar el módulo; reiniciar la API):

| Variable | Default | Uso |
|---|---|---|
| `NETPULSE_OFFSITE_CONNECT_TIMEOUT` | `10` | conexión S3/SFTP |
| `NETPULSE_OFFSITE_READ_TIMEOUT` | `60` | lectura S3 |
| `NETPULSE_OFFSITE_SFTP_IO_TIMEOUT` | `60` | transferencias SFTP |
| `NETPULSE_OFFSITE_VERIFY_HASH_MAX_BYTES` | `16777216` | tope de hash remoto SFTP |
| `NETPULSE_OFFSITE_RESTORE_DIR` | `<repo>/backups/restored` | base de restauración |

S3 se crea con `retries.max_attempts=1` y addressing style `path` cuando hay
endpoint propio (MinIO/VersityGW), para no multiplicar la espera cuando el
endpoint está mal.

## 8. Retención: **NO implementada por NetPulse**

El servicio no borra objetos remotos ni aplica una política de días. Hoy:

- Volver a subir el mismo nombre reemplaza el objeto.
- Los backups antiguos permanecen indefinidamente.
- La limpieza debe hacerse en el destino: lifecycle de S3/MinIO
  (`mc ilm add`, AWS lifecycle) o un cron externo sobre el directorio SFTP.

No se debe documentar ni mostrar “retención configurada” desde NetPulse: no
existe tal configuración. Si se implementa, requiere decidir si la fuente de
verdad son fechas del nombre, `LastModified` remoto o manifiesto, y cómo
evitar borrar el único backup válido.

## 9. Drill local reproducible

```bash
NETPULSE_PYTHON=/ruta/al/.venv/bin/python scripts/offsite_drill.sh
```

Hace, contra la API real de este worktree:

1. Levanta S3 compatible y SFTP desechables en puertos **libres** (nunca
   9000/9001/2222 por defecto), publicados solo en `127.0.0.1`.
2. Crea el bucket/usuario y arranca la API con configs temporales.
3. Genera un self-backup real (`POST /api/ops/backup`) y dos fixtures de
   configuración, y guarda sus SHA-256.
4. Exporta, verifica los objetos **por consulta directa** al destino
   (tamaño + SHA-256), consulta `/status?probe=true`, ejecuta `verify` y
   restaura a un directorio temporal comparando SHA-256 con el original.
5. Repite para S3 y SFTP.
6. Fuerza fallos: credenciales malas, bucket inexistente y endpoint caído
   (S3), contraseña mala y endpoint caído (SFTP). Exige HTTP 502 + código
   clasificado + backups locales intactos + fallo rápido.
7. Limpia contenedores, API, archivos del drill y snapshots de `config/`
   incluso con SIGINT/SIGTERM. Sale `!=0` si cualquier comprobación falla.

Variables: `NETPULSE_PYTHON`, `NP_DRILL_KEEP=1`,
`NP_DRILL_S3_IMAGE` (default `versity/versitygw:latest`;
`minio/minio:latest` también soportado si está disponible),
`NP_DRILL_S3_PORT`, `NP_DRILL_SFTP_PORT`, `NP_DRILL_API_PORT`,
`NP_DRILL_TIMEOUT`. Opciones `--keep`, `--s3-only`, `--sftp-only`.

### Evidencia real (2026-10-26, macOS arm64, Docker 29.8.1)

```
[offsite] S3 listo: http://127.0.0.1:49757 bucket=netpulse-offsite-drill (s3gw)
[offsite] SFTP listo: 127.0.0.1:49758 user=npdrill dir=netpulse-backups
PASS status?probe: destino alcanzable y vacío
PASS export → todos subidos y verificados
PASS export informa verificación por hash
  S3 OK  netpulse/drill-core-01_…cfg size=131 sha256=bf5b39864d922b3b…
  S3 OK  netpulse/drill-core-01_…json size=153 sha256=dd9802a1c8b67451…
  S3 OK  netpulse/netpulse_self_…tar.gz size=995 sha256=4e6219a997782e0f…
PASS S3: objetos existen y SHA-256 remoto == local (consulta directa)
PASS verify → sin faltantes ni discrepancias
PASS restore → HTTP 200 y archivos restaurados
PASS contenido restaurado == original (SHA-256, 3 archivos)
PASS credenciales malas: export → HTTP 502, auth_failed
PASS bucket inexistente: export → HTTP 502, bucket_missing
PASS endpoint caído: export → HTTP 502, endpoint_unreachable
PASS endpoint caído: falla rápido (1s, sin cuelgue)
PASS SFTP export/verify/restore …
PASS SFTP contraseña mala: export → HTTP 502, auth_failed
PASS SFTP endpoint caído: export → HTTP 502, endpoint_unreachable

===== RESUMEN OFFSITE DRILL =====
PASS=40 FAIL=0
```

Interrupción limpia comprobada: SIGINT al grupo de procesos del drill →
`EXIT=130`, sin contenedores, sin directorio temporal y sin uvicorn
huérfano.

### Limitaciones de la evidencia

- **MinIO no se pudo usar**: el 2026-10-26 `docker pull minio/minio:latest`
  y `quay.io/minio/minio:latest` fueron rechazados (la imagen oficial ya no
  se publica en esos registros) y el binario de `dl.min.io` devolvió HTTP 410.
  El drill usa **VersityGW v1.10.0**, un gateway S3 real sobre POSIX
  (credenciales, bucket, ETag MD5, 403/404 reales), no un mock. El soporte
  para MinIO queda en el script si la imagen está disponible localmente.
- SFTP usa `atmoz/sftp`, que en Apple Silicon corre como amd64 emulado. La
  prueba valida el protocolo y los permisos, no el rendimiento.
- No se prueba TLS de S3/SFTP ni un bucket cloud real (AWS). La verificación
  de ETag multipart en AWS queda en “solo tamaño + SHA-256 del manifiesto”.
- `status.last_export` es memoria del proceso; tras reiniciar, `probe=true`
  demuestra el destino pero no hay historial persistente.

## 10. Pendientes explícitos

1. Persistir `last_export` (p. ej. `backups/offsite_last.json`) si se quiere
   historial tras reinicios.
2. Política de retención offsite y su UI; hoy no existe.
3. UI de `verify`/`restore`: el panel solo tiene el botón **Offsite**
   (export). Los endpoints nuevos están listos, pero no hay fetch ni vistas.
4. Prueba contra un bucket AWS real con multipart > 8 MiB y con TLS.
