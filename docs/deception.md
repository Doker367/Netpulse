# NetPulse — Red señuelo (Deception / Honeypot)

Subsistema que simula una red con dispositivos falsos (PCs, servidores,
cámaras, switches, firewalls y routers) para atraer atacantes, registrar
todo lo que hacen y convertirlo en telemetría accionable.

> **Estado:** Fases A–F, G (parcial) y H implementadas. Incluye motor
> nativo, data plane en Go con modo paquete, honeypots reales, alertas,
> blocklist/IOCs, panels realistas, editor de dispositivos en el dashboard
> y sync con NetBox.

## Habilitación (por defecto desactivado)

```bash
NETPULSE_DECEPTION_ENABLED=true
NETPULSE_DECEPTION_INGEST_TOKEN=<token-compartido-con-el-data-plane>
NETPULSE_DECEPTION_IFACE=deception0          # engine Go en modo paquete
NETPULSE_DECEPTION_BIND_HOST=                # vacío = usar la IP de cada dispositivo
```

Con la flag en `false` no se abre **ningún** puerto señuelo.

## Arquitectura

```
config/deception/network.yaml ──► topology_svc ──► engine_svc (asyncio)
        ▲                                             │
        │ CRUD / editor / NetBox                      ├─ listeners nativos
                                                      │   telnet · http · ssh ·
                                                      │   rtsp/smb · snmp(udp)
                                                      ▼
                          telemetry_svc ──► deception.db (SQLite append-only)
                                        ├─► Prometheus (netpulse_deception_events_total)
                                        └─► Loki (vía syslog_svc)
```

## Topología (`config/deception/network.yaml`)

Define `segments` (VLANs/subredes) y `devices` con su `persona`
(vendor/OS/MAC/hostname) y `services` (puerto, proto, engine, banner).

Motores por servicio (`engine`): `native`, `cowrie`, `opencanary`,
`dionaea`, `go`. En Fase B solo se levantan los `native`.

> **Regla de oro:** usar SIEMPRE credenciales señuelo. Nunca reutilizar
> las de `devices.yaml` ni exponer esta red a la red de gestión.

## API (`/api/deception`)

| Método | Ruta | Rol | Descripción |
|---|---|---|---|
| GET | `/status` | viewer | Estado + resumen de topología |
| GET | `/network` | viewer | Segmentos + dispositivos |
| GET/POST/PUT/DELETE | `/devices[/{id}]` | viewer/admin | CRUD de dispositivos señuelo |
| GET/POST/DELETE | `/segments[/{id}]` | viewer/admin | CRUD de segmentos |
| GET | `/events` | viewer | Eventos (filtros: device_id, src_ip, event_type) |
| GET | `/sessions` | viewer | Sesiones agregadas por (dispositivo, atacante) |
| GET | `/alerts` | viewer | Alertas de deception disparadas (auditoría) |
| GET | `/timeline` | viewer | Eventos por minuto (gráfico) |
| GET | `/events.csv` | viewer | Export CSV de eventos |
| POST | `/simulate` | admin | Genera actividad de atacante simulada |
| GET/POST/DELETE | `/blocklist[/{ip}]` | viewer/admin | IPs atacantes bloqueadas |
| GET | `/iocs` \| `/iocs.csv` | viewer | Indicadores de compromiso |
| POST | `/sync-netbox` | admin | Importa dispositivos de NetBox como señuelos |
| GET | `/stats` | viewer | Estadísticas agregadas |
| POST | `/ingest` | token/JWT admin | Contrato de telemetría del data plane |
| GET | `/engine/status` | viewer | Listeners activos |
| POST | `/engine/start` \| `/engine/stop` | admin | Arranque/parada del motor nativo |
| GET | `/integrations/status` | viewer | Estado del tailer de honeypots reales |
| POST | `/integrations/start` \| `/integrations/stop` | admin | Arranque/parada del tailer |

## Contrato de telemetría

```jsonc
POST /api/deception/ingest        // header X-Deception-Token
{
  "device_id": "cam-entrada-01",
  "src_ip": "203.0.113.9",
  "src_port": 45123,
  "proto": "http",
  "type": "login_attempt",
  "username": "admin",
  "success": false,
  "detail": {"path": "/login", "password": "1234"}
}
```

Tipos de evento: `connect`, `login_attempt`, `login_success`, `command`,
`file_download`, `port_scan`, `snmp_query`, `http_request`,
`rtsp_request`, `other`.

## Listeners nativos (Fase B)

- **telnet** — banner + login + shell falsa que registra cada comando.
- **http/https** — panel de administración por **fabricante** (Hikvision,
  Cisco, FortiGate, MikroTik, IIS/Apache) con login y dashboard falsos;
  captura credenciales POST y cabecera `Server` realista.
- **ssh** (paramiko) — acepta cualquier contraseña y captura comandos.
- **ftp** — banner, login `USER`/`PASS` y comandos básicos.
- **mysql** — handshake v10 falso, captura usuario y responde `OK`.
- **rtsp / smb** — registra la interacción; RTSP responde `200 OK`.
- **snmp** (UDP) — registra las consultas.

> Nota: `https` se atiende como HTTP plano en Fase B; TLS queda para
> hardening. El SNMP nativo no devuelve un PDU válido (lo hará el engine
> en Go con `gopacket`).

## Data plane en Go (Fases E/F)

Motor alternativo (y más rápido) escrito en Go, en `deception-engine/`.
Lee la **misma** `network.yaml` y reporta a NetPulse vía `/ingest` con
`X-Deception-Token`. Soporta SSH (`x/crypto/ssh`), HTTP/HTTPS, Telnet,
RTSP/SMB y SNMP.

```bash
# compilar
cd deception-engine && go build -o bin/engine ./cmd/engine

# ejecutar contra NetPulse (motor Python apagado para no chocar puertos)
NETPULSE_DECEPTION_INGEST_TOKEN=demo-token scripts/run_deception_engine.sh
```

**Modo paquete** (fidelidad de red: responde ARP, TCP SYN→SYN-ACK e ICMP
para que `nmap` vea los puertos abiertos). Requiere `libpcap`, root y una
interfaz dedicada:

```bash
go build -tags packet -o bin/engine-packet ./cmd/engine
sudo ./bin/engine-packet -config config/deception/network.yaml \
  -ingest http://127.0.0.1:8082/api/deception/ingest -token $TOKEN \
  -packet -iface deception0
```

> El motor Python queda como supervisor/referencia; el de Go se puede
> desplegar igual (imagen en `deception-engine/Dockerfile`).

## Honeypots reales (Fase C)

Además de los listeners nativos, se pueden levantar honeypots reales en
un overlay aislado (red `internal: true`, sin pivote a gestión/Internet):

```bash
docker compose -f docker-compose.deception.yml up -d --build
# arrancar el tailer que normaliza sus logs:
curl -X POST -H "Authorization: Bearer $TOKEN" localhost:8082/api/deception/integrations/start
```

- **Cowrie** (SSH/Telnet): `docker/deception/cowrie/cowrie.cfg` → log JSON.
- **OpenCanary** (multi-protocolo): `docker/deception/opencanary/`.

Los logs quedan en `deception/logs/<sensor>/` y el **tailer**
(`integrations/tailer_svc.py`) los parsea (`cowrie_svc`/`opencanary_svc`)
y los inyecta en la misma telemetría. Config (env):

```
NETPULSE_DECEPTION_COWRIE_LOG=deception/logs/cowrie/cowrie.json
NETPULSE_DECEPTION_COWRIE_DEVICE=srv-files-01
NETPULSE_DECEPTION_OPENCANARY_LOG=deception/logs/opencanary/opencanary.log
NETPULSE_DECEPTION_OPENCANARY_DEVICE=sw-access-01
```

### Nota macOS / Colima

En macOS los bind mounts (virtiofs) presentan un problema de permisos con
Cowrie: el contenedor corre como uid 999 y el `cowrie.json` queda con
modo `000` en el host, de modo que el tailer no puede leerlo. **OpenCanary
sí funciona** en macOS (verificado end-to-end). En **Linux** el mismo
compose funciona sin ajustes. Alternativas para Cowrie en macOS:

- Correr NetPulse en Linux (recomendado).
- Usar Promtail→Loki sobre el stdout de Cowrie (ya presente en el stack
  de monitoring).

También podés ajustar el dueño del bind mount: `chown -R 999 deception/logs/cowrie`.

## Alertas (Fase H)

`alert_svc.py` evalúa cada evento y dispara alertas ante actividad de
alto valor, con silencio configurable por (dispositivo, atacante, regla):

| Regla | Severidad | Disparador |
|---|---|---|
| `login_success` | high | Login exitoso en un señuelo |
| `file_download` | critical | Descarga de archivo desde un señuelo |
| `port_scan` | high | Escaneo de puertos |
| `brute_force` | high | ≥ N intentos de login en una ventana |

Cada alerta se registra en la auditoría (`action=deception_alert`), se
reenvía a webhooks/Telegram/Slack/Email (`event_type=deception_alert`) y
se cuenta en Prometheus (`netpulse_deception_alerts_total`). El envío se
despacha en un executor para no bloquear a los listeners.

Config (env):

```
NETPULSE_DECEPTION_ALERTS_ENABLED=true
NETPULSE_DECEPTION_ALERT_COOLDOWN=60          # segundos de silencio
NETPULSE_DECEPTION_BRUTE_FORCE_THRESHOLD=5
NETPULSE_DECEPTION_BRUTE_FORCE_WINDOW=60
```

## Dashboard (Fase D)

Sección **DECEPTION** en el sidebar: estado del motor, botones de
arranque/parada, tarjetas (eventos, atacantes únicos, dispositivos,
sesiones), panel de **alertas recientes**, top atacantes/dispositivos,
tabla de sesiones y últimos eventos. Al hacer clic en un dispositivo se
abre su **detalle con configuración** (persona, servicios, segmento) y
sus últimos eventos. Se recarga con `loadDeception()`.

Sección **MONITORING** (unificada): estado de Prometheus/Loki/Grafana/
Alertmanager, tarjetas de métricas, gráficos de host (CPU/RAM/disco),
paneles espejo de Grafana (eventos por tipo, alertas por severidad),
targets de Prometheus y el **dashboard de Grafana incrustado**. Backend:
`/api/monitoring/overview` y `/api/monitoring/series`.

Iconografía con **SVG** (sin emojis) y diseño responsive.

## Reducción de falsos positivos

Alertar por cada interacción genera ruido. Para que sea profesional:

- **Allowlist** (`allowlist_svc.py`, `deception/allowlist.json`): IPs o
  rangos CIDR de confianza (red de gestión, escáneres propios) que **no**
  generan alertas. `POST /api/deception/allowlist`.
- **Clasificación de origen** en los IOCs: `public` / `internal` / `lab`
  (loopback). El origen `lab` es siempre `info` (no amenaza).
- **Puntuación de amenaza** por evidencia (eventos + login + comandos
  sensibles) en vez de marcar `high` ante cualquier interacción.
- **Supresión de loopback** (127.0.0.0/8) y de eventos simulados:

```
NETPULSE_DECEPTION_ALERT_IGNORE_SIMULATED=true
NETPULSE_DECEPTION_ALERT_IGNORE_LOOPBACK=true
NETPULSE_DECEPTION_ALERT_IGNORE_PRIVATE=false
```

Herramientas complementarias para producción (integración futura):
**GeoLite2** (reputación/geo offline), **Suricata/Wazuh** (IDS),
**fail2ban** (bloqueo por umbral), **MISP** (compartir IOCs).

## GeoIP, mapa global y firewall (Fase G/H)

- **GeoIP offline** (`geoip_svc.py`): país/ciudad/coordenadas con una base
  MaxMind local. Por defecto usa la incluida en `maxminddb-geolite2`
  (sin servicios externos); se puede apuntar otra con `NETPULSE_GEOIP_DB`.
- **Mapa global** en el dashboard: puntos **rojos** = origen de los
  ataques (geolocalizados, tamaño por nº de eventos), punto **verde** =
  centro de datos objetivo y **líneas de trayectoria animadas** desde cada
  atacante al objetivo. **Zoom con rueda / botones y arrastre (pan)**, y
  tabla de orígenes con **país, estado/región, ciudad**, eventos y acción
  de bloqueo. Mapa vectorial propio
  (`app/static/world.geo.json`, Natural Earth, dominio público), sin tiles
  externos. Datos: `GET /api/deception/geo`.
- **Simulación multi-país**: `POST /api/deception/simulate?country=US`
  (códigos: US, DE, GB, FR, JP, BR, IN, CN, RU, AU, CA, MX, NL, SG, ZA,
  KR). El botón **Global** del dashboard simula ataques desde todos.
- **Firewall** (`firewall_svc.py`): traduce la IP a una regla del firewall
  local (pf en macOS, nftables/iptables en Linux, netsh en Windows). Por
  defecto es **dry-run** (registra en blocklist y devuelve el comando);
  con `NETPULSE_FIREWALL_ENABLED=true` aplica la regla (requiere permisos).
  Endpoints: `POST /api/deception/firewall/block`, `/firewall/unblock`,
  `GET /api/deception/firewall/status`.

## Blocklist e IOCs (Fase G/H)

- **Blocklist** (`blocklist_svc.py`, `deception/blocklist.json`): IPs
  atacantes bloqueadas. Se puede bloquear a mano desde el dashboard (🚫)
  o automáticamente ante alertas `critical`
  (`NETPULSE_DECEPTION_AUTOBLOCK_CRITICAL=true`). El data plane/firewall
  puede consumirla.
- **IOCs** (`ioc_svc.py`): agrega atacantes (IP, eventos, dispositivos,
  comandos, rango temporal, amenaza y estado) y exporta a CSV.

## Editor y NetBox

- En la vista Deception: botón ➕ para **agregar dispositivos** señuelo y
  ✕ en cada tarjeta para eliminarlos (persiste en `network.yaml`).
- `POST /api/deception/sync-netbox` importa dispositivos de NetBox y los
  mapea a roles/señuelos por defecto.

## Aislamiento

- Red Docker dedicada con `internal: true` (sin pivote a gestión/Internet).
- Sin publicar puertos al LAN real por defecto.
- El modo paquete del engine Go debe apuntar a la interfaz de deception
  (`NETPULSE_DECEPTION_IFACE`), nunca a `eth0`/gestión.

## Pruebas

```bash
pytest tests/test_deception.py tests/test_deception_engine.py -q
```

## Prueba rápida (quickstart)

```bash
# 0) entorno (una vez)
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

# 1) smoke test end-to-end SIN Docker: arranca, ataca y resume
make smoke

# 2) usuarios de prueba con contraseñas conocidas
make seed          # ver config/.initial_credentials

# 3) arrancar la API + motor + dashboard (primer plano)
make run           # http://localhost:8082  (sección DECEPTION)

# 4) honeypots reales + monitoreo (opcional, requiere Docker)
make docker-up
```

Atajos disponibles con `make help`: `test`, `lint`, `go-build`,
`docker-up`, `down`, `clean`.

Para generar actividad y verla entrar en vivo:

```bash
# ataque a los dispositivos simulados
.venv/bin/python scripts/deception_attack_demo.py

# o desde el dashboard: botón "🧪 Simular ataque", auto-refresh y filtros
```

## Solución de problemas

- **No puedo iniciar sesión**: corré `make seed` (crea usuarios de prueba
  en `config/users.yaml` y muestra las contraseñas).
- **`make smoke` falla por puerto 8082 ocupado**: `make down` o pasá
  `PORT=8090 make smoke`.
- **Docker no está**: Colima vía `brew install colima docker docker-compose`
  + `colima start`. En macOS/Cowrie ver la nota de permisos de bind mount.

