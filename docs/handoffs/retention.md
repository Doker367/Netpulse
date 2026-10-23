# Retención / housekeeping — contrato UI futuro

Backend únicamente. Bearer JWT en `Authorization`; `viewer`, `operator` y `admin` pueden leer; **solo `admin`** puede ejecutar o previsualizar una purga. Fechas ISO 8601 UTC (`+00:00`); `fecha` de backup significa **última modificación** del archivo, no fecha probada de creación. Prometheus se consulta en `/api/v1/status/flags` usando la URL del módulo de monitoreo (`http://localhost:9090` actualmente): los flags `storage.tsdb.retention.time` y `storage.tsdb.retention.size` son los valores reportados por Prometheus, **no** una política editable en NetPulse. `settings.py` no declara ninguna retención TSDB ni ruta de auditoría; ésta procede de `audit_svc.AUDIT_DB` (`audit/audit.db`). Los backups locales proceden de `settings.BACKUP_DIR` (`backups/`), compartido por el servicio de backups de configuración y el autobackup. No se miden backups offsite ni subdirectorios; los symlinks no cuentan. La carpeta se crea al importar settings; una carpeta presente pero vacía se reporta como no configurada. Una ruta existente que sea archivo o symlink produce 503, no una lista vacía.

## GET `/api/retention/inventory`

Sin body ni query; requiere `viewer` o superior. `200` (ejemplo **ilustrativo**, nunca datos de producción):

```json
{
  "prometheus":{"retention_time":"15d","retention_size":null,"configurado":true,"estado":"con datos"},
  "audit":{"registros":2,"mas_viejo":"2026-10-01T12:00:00+00:00","estado":"con datos"},
  "backups":{"archivos":1,"uso_bytes":2048,"mas_viejo":"2026-10-01T12:00:00+00:00","ruta":"/ruta/netpulse/backups","estado":"con datos"}
}
```

Si Prometheus falla o devuelve status inválido: `retention_time:null`, `retention_size:null`, `configurado:false`, `estado:"no disponible: <causa>"`; **mostrar ese texto literal**, no usar default de 15 días, ni extrapolar de otro endpoint. Si responde sin flags medibles: `null`, `false`, `estado:"sin medir"`. Los flags son valores efectivos que anuncia Prometheus; no hay prueba de si proceden de argumentos explícitos o defaults del propio Prometheus.

Auditoría inexistente: `registros:null`, `mas_viejo:null`, `estado:"sin medir"`. Error de lectura: ambos `null`, `estado:"no disponible: <causa>"`. Tabla legible vacía: `registros:0`, `mas_viejo:null`, `estado:"sin medir"` (cero comprobado, no falta de medición). Backups sin carpeta o sin archivos: `archivos:0`, `uso_bytes:0`, `mas_viejo:null`, `ruta:null`, `estado:"backups no configurados"`. El cero representa una lectura comprobada de la carpeta, **no** un backup exitoso. En el panel, mostrar «sin medir» para campos `null`, y el badge «backups no configurados» con prioridad sobre los ceros. Ninguna lista vacía indica que se haya realizado un respaldo.

Errores: `401` sin/invalid token (`detail:"Token de autorización requerido"` con Bearer ausente o `"Token inválido o expirado"` si es inválido); `403` rol no autorizado (`detail:"Acceso denegado — se requiere rol 'viewer' o superior"`); `503` si no se puede leer la carpeta de backups (`detail:"Backups no disponibles: <causa>"`). Problemas parciales de Prometheus o auditoría permanecen en sus `estado` respectivos con HTTP 200, no disfrazados de datos.

## GET `/api/retention/backups`

Sin body ni query; requiere `viewer` o superior. `200` usa el mismo objeto `backups` anterior y agrega la lista `backups` de archivos regulares del nivel superior, ordenados por `fecha` ascendente y después `nombre`:

```json
{"archivos":1,"uso_bytes":2048,"mas_viejo":"2026-10-01T12:00:00+00:00","ruta":"/ruta/netpulse/backups","estado":"con datos","backups":[{"nombre":"node.cfg","tamano_bytes":2048,"fecha":"2026-10-01T12:00:00+00:00"}]}
```

Sin carpeta o vacía: `200`, `{"archivos":0,"uso_bytes":0,"mas_viejo":null,"ruta":null,"estado":"backups no configurados","backups":[]}`. Mostrar literalmente «backups no configurados», no una tabla vacía como éxito. Errores: `401`/`403` con los mismos `detail` de inventario, o `503` `{"detail":"Backups no disponibles: <causa>"}` por error de lectura.

## POST `/api/retention/audit-prune`

Requiere `admin`. JSON: `{"dias":90,"dry_run":true}`; `dias` entero JSON estrictamente positivo (no booleano, float ni string); `dry_run` booleano **obligatorio y explícito**. `dias=90` elimina eventos con `ts` estrictamente **anterior** a hora UTC actual menos 90 días; los eventos exactamente en el límite se conservan. Antes de cualquier borrado enviar `dry_run:true` para previsualizar: `200` `{"borraria":2,"muestras":[{"timestamp":"2026-01-01T00:00:00+00:00","action":"api_get","device_id":null,"username":"operador","details":"GET /api/audit","ip_address":"","success":true,"session_id":"...","path":"/api/audit","method":"GET","response_status":200}]}`. `muestras` contiene **hasta cinco** registros ordenados del más viejo al más reciente, con el contrato de evento de `audit_svc`; puede ser `[]` solo cuando `borraria:0`. El dry-run no inserta, borra ni altera filas en el servicio; el middleware general puede registrar la propia petición HTTP en una instancia real.

Después de confirmar inline con el operador, mandar **un segundo POST** `{"dias":90,"dry_run":false}`: `200` `{"borrados":2,"kept":11}`. `kept` cuenta todos los registros restantes **incluido** el evento `action:"audit_prune"` que se inserta transaccionalmente junto con el borrado en el audit trail usando el usuario autenticado; el middleware general también podría añadir posteriormente el evento `api_post`, fuera de este conteo. Volver a cargar la vista previa tras la purga: no asumir que el conteo previo sea idéntico en presencia de otros writers.

Errores: `401` sin Bearer `detail:"Token de autorización requerido"` (token inválido: `"Token inválido o expirado"`); `403` viewer/operator `detail:"Acceso denegado — se requiere rol 'admin' o superior"`; `422` sin `dry_run` incluye literalmente `"se requiere dry_run: obtén primero la lista de lo que se borraría"` en `detail` de validación Pydantic; body `null` devuelve `detail:"Field required"` (FastAPI). `422` con `dias<=0` o no entero incluye `"dias debe ser un entero positivo"` en `detail`; `dias` positivo fuera del rango soportado por fecha devuelve `detail:"dias fuera de rango"`; `dry_run` no booleano incluye un error de tipo booleano de Pydantic. `503` si no hay base de auditoría: `detail:"Auditoría sin medir: base de datos no disponible"`; si falla SQLite: `detail:"Auditoría no disponible: <causa>"` y se revierte toda la transacción. UI: mostrar literalmente los errores, nunca marcar una purga fallida como hecha.

## Mock del panel sugerido (no implementado)

- Tarjeta «Retención»: tres filas Prometheus, auditoría y backups; flags, conteos y fecha de archivo más viejo solo si medidos; `null` como **«sin medir»** y estados «no disponible: <causa>» / «backups no configurados» destacados. No pintar verde un cero inferido.
- Tabla de backups `Nombre | Tamaño (bytes) | Última modificación UTC` más suma total; estado vacío explícito encima de la tabla. Mostrar ruta solo si existe en la respuesta.
- Acción admin «Purgar auditoría» con panel de confirmación **inline estilo bulk-bar**: elegir días, botón «Previsualizar» manda `dry_run:true`, mostrar `borraria` y hasta cinco muestras con fecha/acción/usuario; botón «Ejecutar purga» se habilita tras previsualizar, manda `dry_run:false` con mismos días y pide confirmación explícita. Si cambian días, invalidar la vista previa. Viewer ve inventario y tabla, nunca el botón. Deshabilitar ante 503, diferenciar «sin medir» de «0 observados» y reflejar 401/403/422 con textos literales.

## Gen-2 documentado, **NO implementado**

Política TSDB configurable desde UI exigiría modificar argumentos de arranque/configuración de Prometheus y **reiniciar Prometheus**, con riesgo de pérdida de métricas o interrupción de monitorización. Requiere diseño de permisos, persistencia, validación y procedimiento operativo/rollback antes de implementar. Ningún endpoint actual edita esa política ni purga backups o TSDB.
