# NetPulse — Fase 3: NetBox + Inventario

> **Fecha:** 9 de junio 2026
> **Estado:** ✅ COMPLETADO (token requiere setup manual)

---

## Componentes desplegados

| Componente | Puerto | Estado |
|-----------|--------|--------|
| **NetBox 4.6** | :8000 | 🟢 Running |
| **PostgreSQL** | interno | 🟢 Healthy |
| **Redis** | interno | 🟢 Healthy |
| **NetBox Admin** | http://localhost:8000 | 🟢 Login: admin/admin |

---

## Endpoints NetBox (en NetPulse API :8082)

| Endpoint | Método | Descripción |
|----------|--------|-------------|
| `/api/netbox/devices` | GET | Lista dispositivos de NetBox |
| `/api/netbox/devices/{id}` | GET | Dispositivo individual |
| `/api/netbox/sites` | GET | Sites del datacenter |
| `/api/netbox/ipam/prefixes` | GET | Prefijos IP |
| `/api/netbox/ipam/ip-addresses` | GET | Direcciones IP |
| `/api/netbox/vlans` | GET | VLANs |
| `/api/netbox/sync` | POST | Sync NetBox → devices.yaml |
| `/api/netbox/sync/push` | POST | Push devices.yaml → NetBox |

Todos requieren rol `admin`.

---

## Setup del token NetBox

```bash
# 1. Abrí el navegador
firefox http://localhost:8000

# 2. Login: admin / admin

# 3. Click en tu usuario → "API Tokens" → "Add a token"
#    Marcá "Write enabled"

# 4. Guardá el token
echo "TU-TOKEN" > config/.netbox_token
```

---

## Docker Compose

```bash
# Arrancar todo el stack
docker compose -f docker-compose.lab.yml up -d      # Lab (5 devices)
docker compose -f docker-compose.netbox.yml up -d   # NetBox + DB + Redis
```
