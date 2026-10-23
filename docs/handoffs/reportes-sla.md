# Reportes SLA — contrato UI Ola 3

Origen: histórico nativo (`metrics_history_svc.overview`), **no** Prometheus `up`. Cada `devices.<id>.online` observado vale una muestra; no se imputan huecos. Retención: hasta 1440 muestras (24 h a intervalo 60 s), configurable por `NETPULSE_METRICS_HISTORY`; aunque el API acepta ventanas de hasta 31 días, las horas sin muestras **no** son SLA. Con `NETPULSE_METRICS_ENABLED=false` el histórico puede estar vacío.

## Rutas y permisos

- `POST /api/reports/availability`: `operator` o `admin`, recalcula y sustituye el reporte del mismo hash.
- `GET /api/reports/availability/cached?desde=…&hasta=…&device_ids=sw1&device_ids=sw2&group=Core`: `viewer`, `operator`, `admin`; solo lee el último reporte del proceso para **los mismos parámetros**. No genera nada. `device_ids` repetido como query param; omitirlo para inventario completo. Las listas se normalizan sin duplicados y en orden estable para el hash.
- JWT en `Authorization: Bearer <token>`; 401 sin token, 403 rol insuficiente; 404 en cache miss; 422 parámetros inválidos.

## Solicitud POST

```json
{"desde":"2026-10-22T00:00:00Z","hasta":"2026-10-22T06:00:00Z","device_ids":["sw-core-01"],"group":"Core"}
```

`desde` y `hasta` obligatorios: ISO 8601 con zona horaria, `desde < hasta`, `hasta` no futuro, duración máxima 31 días. `device_ids` y `group` opcionales; `device_ids: []` selecciona cero equipos, omitirlo selecciona inventario completo. `group` intersecta con los IDs seleccionados; no se incluye un equipo fuera del grupo. El histórico actualmente expone como máximo 168 h vía `overview(hours=…)`; rangos anteriores quedan sin datos, nunca inventados.

## Respuesta 200 (ejemplo ilustrativo de contrato, **no** datos reales)

```json
{
  "report_id":"sha256-hex-del-contenido-normalizado",
  "desde":"2026-10-22T00:00:00Z", "hasta":"2026-10-22T06:00:00Z",
  "device_ids":["sw-core-01"], "group":"Core",
  "estado":"con_datos", "mensaje":"Disponibilidad calculada solo sobre muestras observadas; revisar cobertura parcial.",
  "recoleccion_activa":true,
  "equipos":[{
    "device_id":"sw-core-01", "uptime_percent":75.0,
    "cobertura":{"first_ts":"2026-10-22T01:00:00Z","last_ts":"2026-10-22T02:00:00Z",
      "n_muestras":4,"horas_observadas":1.0,"horas_cubiertas_estimadas":0.067,
      "horas_solicitadas":6.0,"parcial":true,"descripcion":"cobertura estimada 0.07 h de 6.00 h"},
    "incidentes":[{"inicio":"2026-10-22T01:20:00Z","fin":"2026-10-22T01:20:00Z","duracion_segundos":0.0}]
  }], "excluidos":[]
}
```

`uptime_percent = 100 × muestras online / muestras con online booleano` (0–100), **no** uptime continuo de toda la ventana. `first_ts/last_ts` son la primera y última observación válidas; `horas_observadas` es su separación, **no** cobertura continua. `horas_cubiertas_estimadas` limita cada intervalo al período nominal de muestreo; huecos internos no cuentan, el tramo posterior a la última muestra es estimado y se indica como tal. Nunca mostrar 75 % sin su texto de cobertura. Un incidente agrupa muestras `down` consecutivas; fin es la **última muestra down**, no el instante de recuperación. Una única muestra down tiene duración observada 0 s: no inferir cuánto duró realmente. La colección `excluidos` lleva `{device_id,reason}` para cada equipo sin serie `online` válida dentro de la ventana.

Sin muestras: `estado:"sin_datos"`, `equipos:[]`, `excluidos` con razones y `mensaje:"Sin datos en la ventana / recolección desactivada."` si el poller está apagado. **No** pintar 0 % ni 100 %. Caché en memoria por proceso; se pierde tras reinicio y no se comparte entre workers.

## Mock UI Ola 3 (estructura, sin datos falsos en producción)

- Filtro superior: fechas ISO con zona, selector de grupo y multiselección de equipos; botones «Consultar último» (GET) y «Regenerar» (POST, solo operator/admin). Validar ventana antes de enviar.
- Tarjeta por equipo: nombre/ID, «75 % de 4 muestras observadas» + aviso «cobertura estimada 0.07 h de 6 h», timestamps primero/último, badge «cobertura parcial». No colorear verde un valor con cobertura parcial como si fuera SLA certificado.
- Tabla de incidentes por tarjeta: `Inicio down | Última observación down | Duración observada`; explicar que 0 s indica una sola muestra y no ausencia de caída.
- Bloque gris para `sin_datos` con `mensaje` exacto; mostrar `excluidos` y razones. Caché 404: «No existe reporte previo para estos filtros», ofrecer regeneración solo a roles autorizados. 422: explicar ventana/fecha inválida; 401/403: acceso o permiso.
