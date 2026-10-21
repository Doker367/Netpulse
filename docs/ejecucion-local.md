# Ejecución local completa (stack arrancado)

Registro de la puesta en marcha de netpulse-noc de punta a punta en macOS
(Apple Silicon), con evidencia verificada.

## 1. Puesta en marcha

```bash
# 1) Entorno Python
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

# 2) Secretos (NO se versionan)
cp .env.example .env         # completar secretos; ver avisos más abajo
./scripts/seed_users.py      # requiere NETPULSE_ADMIN_PASSWORD y NETPULSE_OPERATOR_PASSWORD

# 3) Runtime Docker (Colima) y stacks
colima start --memory 8 --cpu 4
docker compose -f docker-compose.monitoring.yml up -d
docker compose -f docker-compose.netbox.yml up -d
docker compose -f docker-compose.ai.yml up -d --build
docker compose -f docker-compose.lab.yml up -d --build

# 4) API
make run                     # .venv/bin/python -m uvicorn app.main:app --port 8082
```

Atajos equivalentes: `make seed`, `make run`, `make smoke`, `make test`,
`make docker-up`, `make stop`.

## 2. Servicios y puertos

| Servicio | URL | Estado verificado |
|---|---|---|
| API NetPulse (OpenAPI + dashboard) | http://127.0.0.1:8082 (`/docs`, `/`) | 200, 127 rutas |
| Prometheus | http://localhost:9090 | target `netpulse-api` **up**, 14 reglas |
| Alertmanager | http://localhost:9093 | `/-/healthy` 200 |
| Grafana | http://localhost:3000 | 200 (admin/admin), datasources Loki + Prometheus |
| Loki | http://localhost:3100 | `/ready` 200, ingesta activa |
| NetBox | http://localhost:8000 | `healthy`; token v2 en `.env` |
| Laya (IA) | http://localhost:8090 | `healthy`, modelo `multilingual` cargado |
| Lab FRR: cisco / juniper / switch | 127.0.0.1:2222 / 2223 / 2224 (SSH) | `online: true` |
| Lab MikroTik | 127.0.0.1:2225 SSH, 8728 API | `online: true` |
| Lab Arista vEOS | 127.0.0.1:2226 | **offline** (perfil `arista`; requiere x86_64) |

## 3. Cuentas

| Usuario | Rol | Contraseña |
|---|---|---|
| `superadmin` | admin | en `.env` → `NETPULSE_ADMIN_PASSWORD` |
| `operator` | operator | en `.env` → `NETPULSE_OPERATOR_PASSWORD` |
| `doker` | admin | `Doker367` (creado vía `POST /api/admin/users`) |

Los hashes bcrypt viven en `config/users.yaml`, que **está en `.gitignore`** (no
se versiona). `scripts/seed_users.py` solo reescribe `superadmin` y `operator`,
así que `doker` persiste aunque se vuelva a sembrar. Para rotar contraseñas:
`PUT /api/admin/users/{username}` o volver a ejecutar el seed con variables
nuevas.

> El seed exige ≥12 caracteres; el API acepta desde 6 (`UserCreate.password`).
> `Doker367` (8) es válido para el API, pero por debajo de la recomendación del
> proyecto.

## 4. Evidencia
```
$ .venv/bin/python -m pytest        # 93 passed
$ .venv/bin/ruff check app tests scripts   # All checks passed!
```

- `/api/health` → `{"status":"healthy","devices_configured":5}`
- `/api/devices/health` → 4 de 5 equipos del lab `online` (arista offline, esperado)
- Netmiko contra el lab: sesión SSH real y `show version` → `FRRouting 8.4_git (cisco-core-01)`
- `/api/monitoring/overview` → prometheus/loki/grafana `up: true`
- `/api/netbox/devices|sites|ipam/prefixes|vlans` → 200 con token v2
- `/api/ai/decide` → 200, `{"model":"laya-rl-agent","answers":{"is_benign":{"type":"noul","noul":0.0504}}}`
  (segunda llamada `cached: true`)
- `/api/alerts/active` → alerta `HostDiskFull` en estado `firing` (regla de Prometheus evaluada)
- Prometheus scrapea `http://host.docker.internal:8082/api/metrics` → `up`

## 5. Panel de administración (dashboard recuperado)

El commit `3e2b564` había borrado el `dashboard.html` de 7.858 líneas y dejado
un stub de 725 bytes en `/`. Se recuperó desde el historial y se limpió:

- **Eliminado**: vista y sección *Deception*, vista *VMs* (QEMU/ISO/noVNC),
  consola serie WebSocket `/ws/vm/*`, modal de detalle de señuelos, CSS `.dcn-*`
  y las claves i18n asociadas. Las 23 llamadas a `/api/deception/*` y `/api/vms`
  (endpoints que ya no existen) desaparecieron.
- **Conservado**: 20 entradas de menú en 7 secciones (Overview, Infrastructure,
  Observability, Operations, Security, Administration, System), i18n ES/EN,
  temas, terminal WebSSH, SFTP, topología, compliance, auditoría, usuarios.
- **Reconstruido**: los helpers de monitoreo (`monNum`, `renderMonSpark`,
  `loadMonitoring`, `monAutoToggle`, `_monTimer`) vivían intercalados dentro del
  bloque de deception y se restauraron sin sus tarjetas de señuelos. La tarjeta
  "Eventos de deception" se sustituyó por **Alertas activas** alimentada desde
  `/api/alerts/active`.
- **Corregido**: el selector de equipos del terminal quedaba vacío si se abría
  la Consola antes de que terminara `loadDev()`; ahora se repuebla.

Verificación en Chrome real (CDP, sin mocks):

| Comprobación | Resultado |
|---|---|
| Login `superadmin` | OK; 21 vistas, 20 items de menú, 0 secciones Deception/VMs |
| Recorrido de 11 vistas | todas renderizan, **0 errores de consola**, 0 recursos 404 |
| Inventario | 5 equipos del laboratorio listados |
| Monitoreo | servicios (Prometheus/Loki/Grafana/Alertmanager), host CPU/RAM/Disco, `4/5` up, alertas `DeviceDown` (pending) y `HostDiskFull` (firing), targets `up` |
| Terminal SSH | WebSocket `/ws/terminal/cisco-core-01` → banner FRR 8.4 y prompt `cisco-core-01#` |
| Sintaxis JS (`node --check`) | OK (3.089 líneas de script) |

Capturas en `docs/img/`: `dashboard-monitoreo.png`, `dashboard-inventario.png`,
`dashboard-terminal-ssh.png`.

> El token JWT vive en memoria (no en `localStorage`): al recargar la página hay
> que volver a iniciar sesión. Es el comportamiento del dashboard original.


## 6. Requisitos de secretos (aprendidos en esta ejecución)

`.env.example` no documentaba varias restricciones que **impiden arrancar**:

- `NETBOX_SECRET_KEY` y `NETBOX_API_TOKEN_PEPPER_1`: **≥50 caracteres**
  (`openssl rand -hex 32`). Si son cortos, NetBox entra en bucle
  `⏳ Waiting on DB...` aunque PostgreSQL esté sano: el error real
  (`ImproperlyConfigured`) queda oculto en el log.
- `NETBOX_API_TOKEN`: token en claro que crea el bootstrap. Los tokens v2 de
  NetBox **no se guardan legibles** en la BD, así que este valor es la única
  copia; debe guardarse en el gestor de secretos.
- `NETBOX_API_KEY`: **máximo 12 caracteres** (`Token.key` es `varchar(12)`).
  Un valor más largo rompe la creación del superusuario con
  `StringDataRightTruncation`.
- `NETBOX_TOKEN` (lo que lee NetPulse): para tokens v2 es la credencial
  completa `nbt_<key>.<token>`.

## 7. Limitaciones observadas (no bloquean el arranque)

- **NAPALM + FRR**: la conexión SSH funciona, pero el driver `ios` no parsea
  la salida de FRR (`get_facts` devuelve `Unknown`, `interfaces` vacío). Es una
  limitación de fidelidad del simulador, no un fallo de NetPulse.
- **`POST /api/netbox/sync/push`**: devuelve `errors: 5` porque envía
  `device_type`/`role`/`site` a `null` y NetBox 4.6 los exige. El código lo
  reconoce en un comentario; falta mapear el inventario local a esos objetos.
- **Grafana sin dashboards**: `docker-compose.monitoring.yml` monta
  `config/grafana/dashboards/`, que está vacío (solo se aprovisionan
  datasources). Por eso el panel "DASHBOARD GRAFANA (incrustado)" del dashboard
  aparece en blanco: apunta a `/d/netpulse-noc/netpulse-noc?kiosk`, que aún no
  existe. El resto de la vista de Monitoreo usa métricas propias y sí funciona.
- **Claves i18n sin traducir**: 12 claves (`nav_terminal`, `stat_*`, `btn_*`,
  `m_loading`…) ya faltaban en el diccionario del dashboard original. El
  aplicador solo sobrescribe si la clave existe, así que se muestra el texto por
  defecto; en español quedan esos rótulos en inglés.
- **Arista vEOS**: imagen x86_64; no arranca en Apple Silicon (documentado en
  `docker-compose.lab.yml`).
- **Memoria de Colima**: con el perfil por defecto (2 CPU / 4 GB) el modelo de
  Laya moría al cargar; con `--memory 8 --cpu 4` funciona.
