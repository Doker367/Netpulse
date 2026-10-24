# Prompts de la Ola 2

Copiar **un bloque por ventana**. Cada worktree ya está creado, con `.venv`
enlazado, `.env` y `config/` copiados, y en `67d3422`.

| Ventana | Directorio | Rama | Depende de |
|---|---|---|---|
| B — alertas backend | `../netpulse-alerts` | `feat/alerts-admin` | — |
| C — retención | `../netpulse-retention` | `feat/retention` | — |
| A — UI de alertas | `../netpulse-alerts-ui` | `feat/alerts-ui` | contrato de B |

Arrancar **B y C en paralelo**. **A solo cuando** `docs/handoffs/alerts_ui.md`
esté commiteado y `feat/alerts-admin` mergeado en `main` (o al menos el contrato
presente en el worktree de A).

---

## Ventana B — Alertas: silencios y reglas (backend)

```text
Eres la ventana B de la Ola 2 de NetPulse NOC (panel NOC de red, FastAPI + HTML).

DIRECTORIO: /Users/doker/netpulse-alerts
RAMA: feat/alerts-admin (worktree aislado). No cambies de rama ni de directorio.
No hagas merge a main. `git log --oneline -1` debe ser 67d3422.

Antes de escribir código lee:
- docs/handoffs/README.md  (sección "Ola 2", hechos verificados)
- PRODUCT.md               (estados honestos: gris = sin datos/no verificable; prohibido simular)
- app/routers/ops.py       (patrón AUTH / ADMIN y manejo de errores)
- app/routers/alerts.py    (lo existente; NO lo modifiques)

OBJETIVO
Añadir al backend la operación de alertas: silencios de Alertmanager y lectura
de reglas de Prometheus, más el contrato que consumirá la UI.

ZONA EXCLUSIVA — solo puedes crear/tocar:
- app/services/alerts_admin_svc.py   (nuevo)
- app/routers/alerts_admin.py        (nuevo)
- tests/test_alerts_admin.py         (nuevo)
- docs/handoffs/alerts_ui.md         (nuevo, contrato para la ventana A)
- app/main.py                        (SOLO una línea include_router)

NO toques: app/routers/alerts.py, app/core/settings.py, app/static/dashboard.html,
config/, requirements*.txt, otros routers/servicios.

HECHOS VERIFICADOS (no los re-inventes, ya comprobados en el entorno real)
1. Alertmanager 0.34.1 en http://127.0.0.1:9093.
   - GET  /api/v2/status              → 200
   - GET  /api/v2/silences            → 200 (hoy [])
   - POST /api/v2/silences            → {"silenceID": "..."}
   - DELETE /api/v2/silence/{id}      → 200; 404 si no existe
   - POST recibe: {"matchers":[{"name":"alertname","value":"DeviceDown",
     "isRegex":false,"isEqual":true}], "startsAt":"...","endsAt":"...",
     "createdBy":"...","comment":"..."}  (fechas RFC3339)
   - La respuesta de cada silencio trae: id, matchers[], startsAt, endsAt,
     createdBy, comment, updatedAt, status.state ∈ active|pending|expired.
2. Las REGLAS no viven en Alertmanager: están en Prometheus
   GET http://127.0.0.1:9090/api/v1/rules?type=alert → 200.
   Forma: data.groups[].rules[] con name, state, health, labels{},
   annotations{}, activeAt, value. Hay 14 reglas en config/prometheus-rules.yml.
3. No existe ALERTMANAGER_URL en app/core/settings.py (solo ALERTMANAGER_TOKEN).
   Lee la URL con os.getenv("NETPULSE_ALERTMANAGER_URL", "http://127.0.0.1:9093").
   Prometheus: os.getenv("NETPULSE_PROMETHEUS_URL", "http://127.0.0.1:9090").
   Timeout configurable: NETPULSE_ALERTS_TIMEOUT (default 5 s).
4. httpx 0.28 está en runtime; NO hay respx en dev-deps. Para tests usa
   httpx.MockTransport inyectado o monkeypatch de una factoría `_client()`.
5. /api/alerts/active (existente) lee Prometheus y lo usa el encabezado del
   panel. No lo cambies ni lo dupliques.

DISEÑO OBLIGATORIO

Router nuevo con prefix /api/alerts (convive con alerts.py; no colisiones):

  GET    /api/alerts/silences        AUTH  (viewer+)
  POST   /api/alerts/silences        ADMIN
  DELETE /api/alerts/silences/{id}   ADMIN
  GET    /api/alerts/rules           AUTH  (viewer+)

AUTH  = [Depends(JWTBearer()), Depends(requires_role("viewer"))]
ADMIN = [Depends(JWTBearer()), Depends(requires_role("admin"))]
(copia el patrón de app/routers/ops.py)

Lecturas: NUNCA devuelvas 500 por upstream caído. Devuelve 200 con disponibilidad
explícita, porque la UI debe poder decir "no verificable" sin inventar cero:

  GET /silences → 200
  {
    "disponible": true,
    "silencios": [
      {
        "id": "uuid",
        "estado": "activo" | "pendiente" | "vencido" | "desconocido",
        "creado_por": "superadmin",
        "comentario": "Mantenimiento",
        "inicia": "2026-10-23T06:00:00Z",
        "termina": "2026-10-23T08:00:00Z",
        "matchers": [
          {"nombre": "alertname", "valor": "DeviceDown", "regex": false, "igual": true}
        ],
        "actualizado": "2026-10-23T06:00:05Z"
      }
    ],
    "consultado_en": "2026-10-23T06:30:00Z"
  }

  Si Alertmanager no responde:
  {"disponible": false, "silencios": [], "motivo": "Alertmanager no disponible: ...",
   "consultado_en": "..."}

  GET /rules → 200
  {
    "disponible": true,
    "grupos": 2,
    "reglas": [
      {
        "nombre": "DeviceDown",
        "grupo": "netpulse",
        "estado": "firing" | "pending" | "inactive",
        "salud": "ok" | "err" | "unknown",
        "severidad": "critical",
        "resumen": "...",
        "descripcion": "...",
        "desde": "2026-10-23T06:00:00Z",
        "labels": {"alertname": "DeviceDown", "...": "..."}
      }
    ],
    "consultado_en": "..."
  }
  Si Prometheus no responde: disponible=false, reglas=[], grupos=0, motivo.

Escrituras: fallan de verdad.
  POST /silences:
    - body: {"matchers":[{"name","value","isRegex"?,"isEqual"?}],
             "startsAt"?: RFC3339, "endsAt": RFC3339, "comment": str}
    - createdBy = usuario del JWT (get_current_user), no lo aceptes del body.
    - startsAt por defecto = ahora (UTC). endsAt obligatorio y > startsAt.
    - valida: matchers no vacío; name y value no vacíos; fechas con zona;
      si no → 422 con mensaje en español.
    - 201 con el silencio normalizado (mismo shape que la lista).
    - Alertmanager caído → 503 {"detail": "Alertmanager no disponible: ..."}.
  DELETE /silences/{id}:
    - 204 si Alertmanager devuelve 200; 404 si devuelve 404;
      503 si no se puede contactar. No inventes éxito.
  Auditoría (audit_svc.log_event) en create/delete:
    action "alert_silence_create" / "alert_silence_delete",
    username del JWT, details con id y matchers. Si la auditoría falla, loguea
    warning pero no rompas la operación.

CONTRATO docs/handoffs/alerts_ui.md (obligatorio, lo consume la ventana A)
Debe incluir, sin ambigüedad:
1. Tabla de endpoints: método, ruta, rol mínimo, códigos y cuerpo.
2. Ejemplos JSON completos de: lista con datos, lista vacía disponible,
   fuente no disponible, reglas con datos, reglas caídas.
3. Semántica de estados: disponible=false ⇒ la UI muestra gris "no verificable";
   silencios=[] con disponible=true ⇒ "sin silencios", nunca "0" ni verde.
4. Permisos: viewer ve listas; admin ve crear/borrar. La UI no debe mostrar
   botones de escritura a viewer/operator.
5. Errores y copys sugeridos en español (503, 404, 422).
6. Ejemplos curl reproducibles contra 127.0.0.1:8082 y 9093/9090.
7. Qué NO hace este backend (no crea reglas, no silencia desde ventanas de
   mantenimiento; eso queda documentado como mejora futura).

TESTS (tests/test_alerts_admin.py, sin red real)
Mínimo 12:
- normalización de silencios y cálculo activo/pendiente/vencido/desconocido;
- POST arma el payload correcto (matchers, isEqual por defecto, fechas UTC) y 201;
- POST rechaza endsAt <= startsAt y matchers vacíos con 422 sin llamar upstream;
- DELETE 204 / 404 / 503;
- upstream caído en GET: 200 con disponible=false y motivo (silencio y reglas);
- reglas: normalización, solo reglas de alerta, disponible=false;
- RBAC: viewer 403 en POST/DELETE; sin token 401/403; viewer 200 en GET.
Usa TestClient(app) como en tests/test_auth_security.py y monkeypatch del
cliente httpx. Los tests deben pasar sin Alertmanager ni Prometheus levantados.

VERIFICACIÓN antes de commitear
  .venv/bin/python -m pytest tests/ -q          # 134 actuales + nuevos, todo verde
  .venv/bin/ruff check app tests
  .venv/bin/python -c "import app.main"         # sin errores de import

COMMIT (uno o dos, en feat/alerts-admin):
  feat(alerts): silencios y reglas con contrato de UI
  (el contrato puede ir en el mismo commit o en uno previo)

Al terminar responde con: archivos creados, endpoints, nº de tests, salida de
pytest/ruff y la ruta del contrato. No hagas merge ni toques main.
```

---

## Ventana C — Retención y housekeeping

```text
Eres la ventana C de la Ola 2 de NetPulse NOC (FastAPI + HTML).

DIRECTORIO: /Users/doker/netpulse-retention
RAMA: feat/retention (worktree aislado). No cambies de rama ni de directorio.
No hagas merge a main. `git log --oneline -1` debe ser 67d3422.

Antes de escribir código lee:
- docs/handoffs/README.md (Ola 2)
- PRODUCT.md (estados honestos; prohibido simular)
- app/services/audit_svc.py (retain ya existe)
- app/services/ops/maintenance_svc.py (ventanas de mantenimiento)
- app/routers/ops.py (patrón AUTH/ADMIN)

OBJETIVO
Retención real y segura de los datos que hoy crecen sin límite, con inventario
previo y modo simulación por defecto.

ZONA EXCLUSIVA — solo puedes crear/tocar:
- app/services/retention_svc.py      (nuevo)
- app/routers/retention.py           (nuevo)
- tests/test_retention.py            (nuevo)
- docs/handoffs/retention.md         (nuevo)
- app/main.py                        (SOLO una línea include_router)
- app/services/ops/maintenance_svc.py (OPCIONAL: solo añadir purge_expired(days))

NO toques: dashboard.html, config/ (salvo el JSON que la purga modifique en
runtime), requirements*, otros servicios/routers.

HECHOS VERIFICADOS (rutas y tamaños reales)
1. audit/audit.db (SQLite, ~5,1 MB): audit_svc.retain(days=90) YA existe y
   devuelve nº de eventos borrados, pero NADIE lo llama.
2. backups/ (BACKUP_DIR, hoy vacío): napalm_svc guarda
   <device_id>_<timestamp>.json y .cfg (configs y facts).
3. reports/ (REPORT_DIR, hoy vacío): PDFs/CSV generados por reports/pdf_report.py.
4. config/maintenance_windows.json: las ventanas vencidas nunca se podan;
   maintenance_svc tiene list_windows/create_window/delete_window.
5. config/metrics_history.json está acotado por deque (1440 muestras): NO lo
   borres ni lo incluyas como objetivo de purga; solo muéstralo en el inventario
   como referencia (política "n/a: acotado por diseño").
6. config/scheduler_jobs.json pertenece al scheduler: no lo toques.
7. No hay cron en el repo. NO añadas jobs al scheduler (fuera de zona):
   expón el endpoint y documenta cómo programarlo con cron/systemd.

DISEÑO OBLIGATORIO

Servicio retention_svc.py:
- POLÍTICAS por env con defaults conservadores:
    NETPULSE_RETENTION_AUDIT_DAYS=90
    NETPULSE_RETENTION_BACKUPS_DAYS=30
    NETPULSE_RETENTION_BACKUPS_KEEP=20      # conservar los N más nuevos por equipo
    NETPULSE_RETENTION_REPORTS_DAYS=90
    NETPULSE_RETENTION_WINDOWS_DAYS=30      # ventanas terminadas hace > N días
- inventario() → lista de categorías, sin lanzar excepción si falta la ruta:
    {"categoria": "auditoria"|"backups"|"reportes"|"ventanas",
     "ruta": "...", "archivos": n, "bytes": n, "mas_antiguo": iso|null,
     "politica_dias": n|null, "candidatos": n, "bytes_liberables": n,
     "nota": "acotado por diseño" | ""}
- ejecutar(dry_run=True) → por categoría:
    {"categoria","eliminados","bytes_liberados","errores":[...]}
  y un resumen {"dry_run": bool, "total_eliminados","total_bytes","ejecutado_en"}.
- Reglas de seguridad:
  * Toda ruta debe resolver dentro de BASE_DIR; si no, error y no borrar.
  * No seguir symlinks al borrar (comprobar is_symlink y resolver el padre).
  * Backups: borrar solo archivos que casen <device>_<ts>.json|.cfg y que no
    estén entre los K más nuevos del equipo; nunca el directorio.
  * Reportes: solo *.pdf, *.csv, *.json.
  * Auditoría: delegar en audit_svc.retain(days) (no borrar el .db a mano).
  * Ventanas: solo terminadas hace más de N días; usa maintenance_svc.
    Si añades purge_expired(days) en maintenance_svc, hazlo con _load/_save y
    documéntalo; no reescribas el archivo desde retention_svc.
  * Si una categoría falla, las demás continúan; devuelve errores por categoría.
- dry_run=True por defecto SIEMPRE. Nada se borra sin confirmación explícita.

Router retention.py, prefix /api/retention:
  GET  /inventory   AUTH (viewer+)
  POST /run         ADMIN
    body {"dry_run": true}  (default true si se omite)
    - dry_run=true  → 200 con lo que se borraría, sin tocar nada.
    - dry_run=false → ejecuta y devuelve el resumen real.
    - audita con audit_svc.log_event(action="retention_run", username=...,
      details=json con dry_run y totales).

docs/handoffs/retention.md: endpoints, ejemplos JSON, políticas y defaults,
qué se borra y qué nunca, cómo programarlo (cron cada día 03:30 con curl o
python -c llamando al servicio), y advertencia de ejecutar dry-run primero.

TESTS (tests/test_retention.py, con tmp_path)
Mínimo 12:
- inventario con rutas inexistentes no explota y devuelve ceros;
- detecta candidatos por antigüedad (mtime) sin borrar en dry_run;
- dry_run=false borra solo los candidatos y respeta BACKUPS_KEEP por equipo;
- no borra archivos recientes; no borra metrics_history.json;
- no sale de BASE_DIR (ruta con ../ o symlink) y reporta error;
- ventanas vencidas se podan y las futuras/activas no;
- auditoría usa retain y devuelve el conteo;
- RBAC: viewer 403 en /run, 200 en /inventory; sin token 401/403;
- errores por categoría no abortan las demás.
Monkeypatch de las constantes de ruta del módulo (no toques archivos reales).

VERIFICACIÓN
  .venv/bin/python -m pytest tests/ -q
  .venv/bin/ruff check app tests
  .venv/bin/python -c "import app.main"

COMMIT en feat/retention:
  feat(retention): inventario y purga con dry-run

Al terminar responde con: archivos, endpoints, políticas, nº de tests, salida de
pytest/ruff. No hagas merge ni toques main.
```

---

## Ventana A — UI de alertas

**No lanzar hasta que `docs/handoffs/alerts_ui.md` exista en el worktree.**
Si B aún no ha terminado, espera; no inventes el contrato.

```text
Eres la ventana A de la Ola 2 de NetPulse NOC. Única dueña de dashboard.html.

DIRECTORIO: /Users/doker/netpulse-alerts-ui
RAMA: feat/alerts-ui (worktree aislado). No cambies de rama ni de directorio.
No hagas merge a main.
PRIMERO: comprueba que existe docs/handoffs/alerts_ui.md. Si no existe, DETENTE
y repórtalo; no inventes endpoints ni campos.

Lee:
- docs/handoffs/alerts_ui.md   (contrato; es la verdad)
- docs/handoffs/README.md      (Ola 2)
- PRODUCT.md                   (estados honestos, prohibido simular datos)
- app/static/dashboard.html    (vista #v-alerts, loadAlerts(), estilos y helpers)

ZONA EXCLUSIVA:
- app/static/dashboard.html
- tests/test_dashboard_routes.py (solo AÑADIR tests al final)
No toques backend, config, requirements ni otros tests.

OBJETIVO
Convertir #v-alerts en una vista operable: alertas activas (lo que ya hay) +
silencios (listar; crear/borrar solo admin) + reglas (solo lectura), con estados
honestos cuando Alertmanager/Prometheus no responden.

REQUISITOS DE UI
1. Conserva intacto el bloque actual de loadAlerts() que agrega alertas de
   Prometheus y recursos del host. Añade, no reemplaces.
2. En #v-alerts, debajo de #alert-list, añade dos secciones con ids estables:
   #alert-silences y #alert-rules (los tests las buscarán).
3. Carga en paralelo con Promise.allSettled:
   GET /api/alerts/silences y GET /api/alerts/rules (usa A y h() existentes).
4. Silencios:
   - disponible=false → gris con npGray(): "No verificable: <motivo>".
     Nunca muestres "0 silencios" ni un check verde.
   - disponible=true y lista vacía → "Sin silencios activos".
   - Tabla/tarjetas: estado (activo/pendiente/vencido/desconocido) con color +
     texto (el color no es el único indicador), matchers como clave=valor,
     inicia/termina en hora local, comentario, creado_por.
   - Botón "Nuevo silencio" SOLO si __isAdmin(); si no, no lo renderices.
   - Formulario: matcher nombre/valor (por defecto alertname / DeviceDown),
     duración 1 h / 4 h / 24 h, comentario obligatorio. endsAt = ahora + duración.
     POST /api/alerts/silences; al 201 recarga la lista; al 503 toast honesto
     ("Alertmanager no disponible; el silencio NO se creó"); al 422 muestra el
     detalle. Nada de inserción optimista.
   - Borrar SOLO admin, con confirmación; 204 → recarga; 404 → "ya no existía"
     y recarga; 503 → toast de error sin decir que se borró.
5. Reglas:
   - disponible=false → gris no verificable con motivo.
   - Lista: nombre, grupo, estado (firing/pending/inactive) con color + texto,
     severidad, resumen. Solo lectura; no inventes acciones.
6. Estados de carga: spinner mientras consulta; nunca dejes la sección vacía sin
   explicación. Si una fuente falla y la otra no, cada sección mantiene su estado.
7. Al abrir la vista (swView('alerts') o el onclick del sidebar) debe refrescarse;
   el botón "Actualizar" recarga todo. No añadas polling agresivo.
8. Español por defecto (el panel tiene ES/EN). Si usas data-i18n, añade la clave
   en AMBOS diccionarios y no dejes claves huérfanas; si no, usa el patrón
   `spanish ? '...' : '...'` ya presente en el archivo.
9. Accesibilidad: botones con aria-label, formulario con <label>, foco visible,
   Enter envía, Escape cierra. Sigue los estilos/clases existentes; no metas
   dependencias ni frameworks.

TESTS (añadir a tests/test_dashboard_routes.py)
- El HTML servido contiene id="alert-silences" y id="alert-rules".
- Contiene los textos/aria de crear silencio y la llamada a /api/alerts/silences.
- No se rompen los tests existentes de la vista de alertas.

VERIFICACIÓN
  python3 - <<'EOF'
  import re, pathlib
  s = pathlib.Path('app/static/dashboard.html').read_text()
  js = "\n".join(m for m in re.findall(r'<script[^>]*>(.*?)</script>', s, re.S) if m.strip())
  pathlib.Path('/tmp/dash_inline.js').write_text(js)
  EOF
  node --check /tmp/dash_inline.js
  .venv/bin/python -m pytest tests/ -q
  .venv/bin/ruff check app tests

PRUEBA MANUAL (con la API en :8082)
- operator: ve listas y NO ve botones de crear/borrar.
- superadmin (NETPULSE_ADMIN_PASSWORD del .env): crea un silencio de 1 h,
  aparece como activo, se puede borrar.
- Con Alertmanager parado (docker stop netpulse-alertmanager): la sección queda
  gris "no verificable", sin errores rojos ni datos inventados.

COMMIT en feat/alerts-ui:
  feat(ui): alertas operables con silencios y reglas

Al terminar responde con: cambios, ids nuevos, capturas o descripción de los
tres estados (con datos / vacío / no verificable), tests y node --check.
```

---

## Después de las tres ventanas

```bash
cd /Users/doker/netpulse-noc

# 1. Backends primero
git merge --no-ff feat/alerts-admin
git merge --no-ff feat/retention
# resolver app/main.py a mano: conservar todas las líneas include_router

# 2. UI al final
git merge --no-ff feat/alerts-ui

# 3. Verificación completa
make test
make lint
SCAN=1 make smoke

# 4. Reiniciar la API con el código mergeado
# (parar la instancia :8082 y volver a `make run`)
```

No mergear A antes que B: el contrato y los endpoints deben existir.
