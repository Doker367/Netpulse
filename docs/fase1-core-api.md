# NetPulse — Fase 1: Core Automation API

> **Fecha:** 5 de junio 2026  
> **Estado:** ✅ COMPLETADO  
> **API:** http://localhost:8082/docs

---

## Resultados: 8/10 endpoints funcionando

| Endpoint | Método | Cisco | Juniper | Switch | MikroTik | Arista |
|----------|--------|-------|---------|--------|----------|--------|
| `/api/devices` | GET | ✅ | ✅ | ✅ | ✅ | ✅ |
| `/api/facts/{id}` | GET | ✅ | ✅ | ✅ | ❌ * | ✅ |
| `/api/facts/{id}/interfaces` | GET | ✅ | ✅ | ✅ | ❌ * | ✅ |
| `/api/facts/{id}/bgp` | GET | — | — | — | — | — |
| `/api/config/{id}` | GET | ✅ | ✅ | ✅ | — | — |
| `/api/config/{id}/backup` | POST | ✅ | ✅ | ✅ | — | — |
| `/api/config/{id}/raw` | GET | ✅ | ✅ | ✅ | — | — |
| `/api/health` | GET | — | — | — | — | — |

*\* MikroTik: `napalm-ros` driver incompatible con RouterOS 7.22 en `get_facts()` — bug conocido*

---

## Estructura de la API

```
app/
├── main.py                 # FastAPI app (lifespan, routers)
├── core/settings.py         # Configuración central
├── models/schemas.py        # Pydantic schemas
├── services/
│   ├── napalm_svc.py        # Capa NAPALM multi-driver + SSL fix Arista
│   └── inventory_svc.py     # CRUD devices.yaml
├── routers/
│   ├── devices.py           # GET/POST/PUT/DELETE /api/devices
│   ├── facts.py             # GET /api/facts/{id}[/interfaces|bgp]
│   └── config.py            # GET/POST /api/config/{id}[/backup|raw|diff]
└── utils/
config/
├── devices.yaml             # 5 dispositivos del lab
backups/                     # Backups automáticos
```

---

## Arrancar

```bash
cd /home/doker/GreenAlgorithm/NetPulse
source .venv/bin/activate
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8082 --reload
```

- **Swagger UI:** http://localhost:8082/docs
- **ReDoc:** http://localhost:8082/redoc
- **Health:** http://localhost:8082/api/health

---

## Notas técnicas

1. **SSL Fix Arista:** Monkey-patch en `napalm_svc.py` para ciphers legacy (AES256-SHA)
2. **Credenciales:** En `config/devices.yaml` (Fase 2 → Vault)
3. **Drivers:** `ios` (FRR), `eos` (Arista vEOS), `ros` (MikroTik RouterOS)
4. **MikroTik:** API en :8728 (NO SSH), `get_facts()` roto con ROS 7.22
5. **Lab containers:**
   - `docker compose -f docker-compose.lab.yml up -d` (FRR x3)
   - `docker start netpulse-mikrotik netpulse-arista` (MikroTik + Arista)

---

## Pendiente para Fase 1.5

- Integrar **Nornir** para operaciones bulk en paralelo
- Agregar cola de tareas (Celery/Redis) para backups programados
