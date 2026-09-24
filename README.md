# NetPulse — Centro de Operaciones de Red

Plataforma de monitoreo de red con API FastAPI + NAPALM, visualización
en dashboard web, logs centralizados en Loki y métricas en Prometheus.

## Requisitos

- Docker + Docker Compose v2
- Python 3.11+ con venv
- 8GB RAM mínimo (16GB recomendado)
- Disco: 10GB libres

## Arranque rápido

```bash
# 1. Levantar contenedores (lab + monitoring + netbox)
docker compose -f docker-compose.lab.yml \
               -f docker-compose.monitoring.yml \
               -f docker-compose.netbox.yml up -d

# 2. Activar venv e instalar dependencias
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Iniciar API
uvicorn app.main:app --host 0.0.0.0 --port 8082
```

Dashboard: http://localhost:8082

## Credenciales

> Las contraseñas **no se versionan**. Se guardan localmente en
> `config/.initial_credentials` (ignorado por git) o se definen por variables
> de entorno. Cambialas tras el primer arranque.

### API Dashboard
- **superadmin** — acceso total (contraseña en `config/.initial_credentials`)
- **operator** — solo lectura (contraseña en `config/.initial_credentials`)

### NetBox (DCIM)
- URL: http://localhost:8000
- Usuario y contraseña: definir en `.env` (ver `.env.example`)

### Dispositivos de Laboratorio

| Dispositivo     | Driver | Conexión             | Credenciales                          |
|-----------------|--------|----------------------|---------------------------------------|
| MikroTik Edge   | ros    | 127.0.0.1:8728 (API)| admin / (sin contraseña)              |
| Arista vEOS     | eos    | 127.0.0.1:443 (eAPI)| vrnetlab / `NETPULSE_LAB_PASSWORD`    |
| Cisco (FRR)     | ios    | 127.0.0.1:2222 (SSH)| admin / admin                         |
| Juniper (FRR)   | ios    | 127.0.0.1:2223 (SSH)| admin / admin                         |
| Switch L2 (FRR) | ios    | 127.0.0.1:2224 (SSH)| admin / admin                         |

## Arquitectura

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  Dashboard   │────▶│  FastAPI API  │────▶│  NAPALM     │
│  (HTML/JS)   │     │  :8082        │     │  (multi-    │
└─────────────┘     └──────┬───────┘     │   vendor)    │
                           │              └──────┬──────┘
                    ┌──────▼───────┐              │
                    │  Prometheus   │         ┌────▼────┐
                    │  :9090        │         │Devices  │
                    └──────┬───────┘         └─────────┘
                           │
                    ┌──────▼───────┐
                    │    Loki       │
                    │  :3100        │
                    └──────────────┘
                           ▲
                    ┌──────┴───────┐
                    │  Promtail    │
                    │  (logs)      │
                    └──────────────┘
```

## Puerto

| Servicio      | Puerto |
|---------------|--------|
| API/Dashboard | 8082   |
| Prometheus    | 9090   |
| Loki          | 3100   |
| NetBox        | 8000   |
| MikroTik SSH  | 2225   |
| MikroTik API  | 8728   |
| Arista SSH    | 2226   |
| Arista eAPI   | 443    |

## Variables de entorno (opcional)

```bash
NETPULSE_HOST=0.0.0.0
NETPULSE_PORT=8082
NAPALM_TIMEOUT=60
NETBOX_URL=http://localhost:8000
NETBOX_TOKEN=<tu-token>
NETPULSE_METRICS_ENABLED=true
NETPULSE_METRICS_INTERVAL=60
```

## API Endpoints principales

- `GET /api/devices` — inventario
- `GET /api/facts/{id}` — facts del dispositivo
- `GET /api/facts/{id}/interfaces` — interfaces
- `POST /api/facts/{id}/ping` — ping
- `GET /api/config/{id}/raw` — configuración
- `GET /api/metrics` — métricas Prometheus
- `GET /api/prometheus/v1/query` — proxy Prometheus
- `GET /api/system/host` — métricas del servidor NetPulse (CPU/RAM/disco/red/proceso)
- `GET /api/system/devices` — recursos y latencia de todos los dispositivos
- `GET /api/system/overview` — resumen consolidado (host + dispositivos + poller)
- `POST /api/system/poll` — fuerza un ciclo de recolección (admin)

## Licencia

Proyecto privado — solo para uso educativo.
