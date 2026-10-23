# Handoffs — contratos y reglas entre agentes

Carpeta de coordinación de las olas de trabajo. Un archivo por tarjeta:
`discovery.md`, `reportes-sla.md`, `dormant-modules-ui.md`, etc.

## Ventanas y ramas (Ola 1)

| Ventana | Tarjeta | Directorio | Rama |
|---|---|---|---|
| A | UI módulos dormidos | `../netpulse-ui` | `feat/dormant-modules-ui` |
| B | Auto-discovery | `../netpulse-discovery` | `feat/discovery` |
| C | Reportes SLA | `../netpulse-reportes` | `feat/reportes-sla` |

`app/static/dashboard.html` pertenece **solo** a la ventana A en esta ola.
`app/main.py` es casi-sagrado: cada backend añade **solo su línea** de
`include_router`. El merge de esas líneas se hace a mano.

## Preparación de cada worktree

Los worktrees ya traen `.venv` enlazado y una copia local (ignorada por git) de
`.env` y `config/`. Para verificar:

```bash
cd ../netpulse-discovery
.venv/bin/python -m pytest tests/ -q          # debe salir EXIT=0
```

Node para el JS inline del panel:

```bash
# extrae los <script> a un .js temporal y valida
node --check /ruta/al/extraido.js
```

## Reglas

- Solo se escribe dentro de la zona de la tarjeta. Lo que falte fuera se pide
  en el handoff, no se edita.
- Ningún fetch de UI sin contrato previo en `docs/handoffs/`.
- Nada de datos simulados ni estados verdes sin verificar: si el origen no
  responde, «sin datos / no verificable» en gris.
- Commits en la rama propia: `feat|fix|chore: resumen`.
- Verificación antes de entregar: pytest verde, `node --check` del JS, ruff.

## Hechos verificados hoy (2026-10-22) que corrigen los briefs

Estos datos salen de la API real corriendo y del código en `main` (`33d03c6`):

1. **Compliance ya tiene UI y está cableada.** `#v-compliance`,
   `loadCompliance()`, `checkCompliance()`, item de sidebar y claves i18n
   existen en `dashboard.html`. No hay que crearla: hay que verificarla.
2. **`/api/compliance/*` es solo admin.** Con token `operator` devuelve 403
   (`baselines`, `report`, `check`). La vista debe ocultarse o mostrar «requiere
   rol admin», no un error genérico.
3. **`/api/groups` sí funciona para `viewer`**: `GET /api/groups` → 200 con
   `[{name, description, device_count}]` (hoy Core=2, Edge=2, Access=1…).
   `POST` y `GET /{group}/devices` según `groups.py`. **Cero fetch** en el panel:
   este sí es un módulo dormido.
4. **`/api/netbox/*` es solo admin** y el panel no lo referencia nunca
   (0 apariciones de «netbox»). `POST /api/netbox/sync` y `/sync/push` son las
   operaciones de escritura; el resto es lectura.
5. **No existe una serie `up` por dispositivo en Prometheus.** Solo se
   scrapean `job=prometheus` y `job=netpulse-api`; el `/api/metrics` de la API
   exporta contadores HTTP (`netpulse_requests_total`), no estado por equipo.
   Para disponibilidad real usar `app/services/metrics_history_svc.py`
   (cada muestra trae `devices.<id>.online`), expuesto en
   `GET /api/system/history?hours=<0.1..168>&device_id=…`.
6. **El histórico nativo está vacío porque `NETPULSE_METRICS_ENABLED=false`.**
   Con el poller apagado no se acumulan muestras: un reporte SLA debe decir
   «sin datos en la ventana», nunca 0 % ni 100 %. Retención del deque: 1440
   muestras (24 h a 60 s).
7. **`napalm_svc.get_lldp_neighbors()` ya existe** (con fallback `lldpcli` por
   SSH cuando NAPALM no soporta LLDP). Discovery debe reutilizarlo.

## Merge por ola

1. Backends primero: `feat/discovery` → `feat/reportes-sla`.
2. UI al final: `feat/dormant-modules-ui` (dueña de `dashboard.html`).
3. Resolver `app/main.py` a mano (líneas aditivas de `include_router`).
4. `pytest` completo + `node --check` + ruff. Recién entonces abrir la Ola 2.

---

# Ola 2 — alertas operables + retención

| Ventana | Tarjeta | Directorio | Rama |
|---|---|---|---|
| B | Alertas: silencios y reglas (backend) | `../netpulse-alerts` | `feat/alerts-admin` |
| C | Retención y housekeeping | `../netpulse-retention` | `feat/retention` |
| A | UI de alertas | `../netpulse-alerts-ui` | `feat/alerts-ui` |

Orden: **B entrega el contrato** (`docs/handoffs/alerts_ui.md`) y C puede correr
en paralelo. **A no empieza** hasta que el contrato esté commiteado en su rama.
`app/static/dashboard.html` pertenece solo a la ventana A en esta ola.

## Hechos verificados (2026-10-23)

1. **Alertmanager v2 responde en `http://127.0.0.1:9093`**: `/api/v2/status` y
   `/api/v2/silences` devuelven 200. Silencios: `GET/POST /api/v2/silences`,
   `DELETE /api/v2/silence/{id}`.
2. **Las reglas viven en Prometheus, no en Alertmanager**:
   `GET http://127.0.0.1:9090/api/v1/rules` (200) devuelve las 14 alertas de
   `config/prometheus-rules.yml`. Alertmanager solo enruta.
3. **No existe `ALERTMANAGER_URL` en settings**: `app/core/settings.py` solo
   tiene `ALERTMANAGER_TOKEN`. El servicio nuevo debe leer
   `NETPULSE_ALERTMANAGER_URL` con default `http://127.0.0.1:9093` sin editar
   `settings.py` (fuera de zona).
4. **`/api/alerts/active` lee Prometheus**, no Alertmanager (`alerts.py:52`).
   No lo cambies: añade un router nuevo.
5. **Ventanas de mantenimiento ya existen**: `app/services/ops/maintenance_svc.py`
   y `GET/POST/DELETE /api/ops/maintenance` (lectura AUTH, escritura ADMIN).
   Un silencio creado desde una ventana es una mejora opcional; si se complica,
   documéntalo en el handoff y no lo fuerces.
6. **No hay `respx` en dev-deps**: los tests mockean con `httpx.MockTransport`,
   `unittest.mock` o monkeypatch (httpx 0.28 está en runtime).

## Verificación extra

```bash
# Alertmanager vivo
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9093/api/v2/silences
# Reglas de Prometheus
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9090/api/v1/rules
```

