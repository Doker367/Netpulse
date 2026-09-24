# NetPulse — Resumen Ejecutivo

> Plataforma Open Source para gestión centralizada de infraestructura de red multivendor en datacenters.

---

## 🏗️ Tecnologías integradas

| Tecnología | Versión | Rol | Puerto |
|-----------|---------|-----|--------|
| **FastAPI** | 0.136 | API REST + async | :8082 |
| **Uvicorn** | 0.49 | Servidor ASGI | — |
| **NAPALM** | 5.1.0 | Abstracción multi-vendor (SSH/API) | — |
| **Netmiko** | 4.7.0 | SSH a dispositivos de red | — |
| **Nornir** | 3.5.0 | Orquestador paralelo (ThreadedRunner) | — |
| **Pydantic** | 2.13 | Validación de datos | — |
| **python-jose** | — | JWT (HS256) | — |
| **passlib[bcrypt]** | — | Hash de contraseñas | — |
| **cryptography (Fernet)** | — | Encriptación AES de credenciales | — |
| **slowapi** | — | Rate limiting | — |
| **prometheus-client** | — | Métricas para Prometheus | — |
| **httpx** | — | Cliente HTTP async (NetBox) | — |
| **Jinja2** | — | Templates de configuración | — |
| **librouteros** | — | Fallback MikroTik API | — |
| **PyYAML** | — | Configuración de inventario | — |

---

## 🐳 Infraestructura Docker (12 contenedores)

| Contenedor | Imagen | Puerto | Función |
|-----------|--------|--------|---------|
| `netpulse-cisco` | frrouting/frr | :2222 | Router Cisco-like (FRR + vtysh) |
| `netpulse-juniper` | frrouting/frr | :2223 | Router Juniper-like (FRR + vtysh) |
| `netpulse-switch` | frrouting/frr | :2224 | Switch L2 (FRR + vtysh) |
| `netpulse-mikrotik` | vaerh/routeros | :8728 | MikroTik RouterOS 7.22 |
| `netpulse-arista` | cminsg/vr-veos | :443 | Arista vEOS 4.27.3F |
| `netpulse-netbox` | netboxcommunity/netbox | :8000 | NetBox 4.6 DCIM |
| `netpulse-netbox-db` | postgres:15 | interno | PostgreSQL para NetBox |
| `netpulse-netbox-redis` | redis:7 | interno | Redis para NetBox |
| `netpulse-prometheus` | prom/prometheus | :9090 | Métricas + scraping |
| `netpulse-grafana` | grafana/grafana | :3001 | Dashboards |
| `netpulse-loki` | grafana/loki | :3100 | Logs centralizados |

---

## 🔧 NAPALM — Cómo funciona

NAPALM es el corazón. Unifica 50+ fabricantes bajo una misma API:

```python
from napalm import get_network_driver

# Mismo código para Cisco, Juniper, Arista, MikroTik...
driver = get_network_driver('ios')  # o 'eos', 'junos', 'ros'

with driver('192.168.1.1', 'admin', 'password') as device:
    device.open()
    
    facts      = device.get_facts()        # Hostname, OS, modelo, uptime
    interfaces = device.get_interfaces()   # Estado UP/DOWN, MAC, speed
    config     = device.get_config()       # Running config
    bgp        = device.get_bgp_neighbors()# Peers BGP
    
    # Deploy seguro
    device.load_merge_candidate(config=candidate)
    diff = device.compare_config()         # Dry-run → diff
    device.commit_config()                 # Aplicar
    device.rollback()                      # Revertir
    
    device.close()
```

### Drivers activos en NetPulse

| Driver | Vendors | Transporte |
|--------|---------|-----------|
| `ios` | Cisco IOS, FRRouting | SSH :22 |
| `eos` | Arista EOS | eAPI HTTPS :443 |
| `ros` | MikroTik RouterOS | API :8728 |
| `junos` | Juniper JunOS | NETCONF/SSH |
| `nxos` | Cisco Nexus | SSH |
| `iosxr` | Cisco IOS-XR | SSH |

---

## 🔐 Seguridad

| Capa | Implementación |
|------|---------------|
| **Autenticación** | JWT (HS256, 30 min) — login con usuario/contraseña |
| **Autorización** | RBAC 3 roles: viewer, operator, admin |
| **Credenciales** | Encriptadas con Fernet (AES-128-CBC) en reposo |
| **Rate limiting** | 100 req/min por IP, 5 login/min |
| **Auditoría** | JSONL inmutable (append-only, sin delete/update) |
| **Comandos** | Filtro de seguridad: solo show/ping/traceroute, bloquea configure/delete/reload |

### Roles

| Rol | Lectura | Config | Deploy | Admin |
|-----|---------|--------|--------|-------|
| `viewer` / `viewer123` | ✅ | ❌ | ❌ | ❌ |
| `operator` / `operator123` | ✅ | ✅ | ❌ | ❌ |
| `admin` / `<password>` | ✅ | ✅ | ✅ | ✅ |

---

## 📡 API REST — 30+ endpoints

### Inventario
```
GET    /api/devices              Listar dispositivos
GET    /api/devices/{id}         Ver uno
POST   /api/devices              Agregar
PUT    /api/devices/{id}         Actualizar
DELETE /api/devices/{id}         Eliminar
GET    /api/devices/health       Estado TCP (público)
```

### Operaciones NAPALM
```
GET    /api/facts/{id}           Facts (hostname, OS, modelo)
GET    /api/facts/{id}/interfaces Interfaces
GET    /api/facts/{id}/bgp       Peers BGP
POST   /api/facts/{id}/ping      Ping desde dispositivo
```

### Configuración
```
GET    /api/config/{id}          Running config (JSON)
GET    /api/config/{id}/raw      Running config (texto)
POST   /api/config/{id}/backup   Backup a disco
POST   /api/config/{id}/diff     Comparar configs
POST   /api/config/{id}/deploy/dry-run  Vista previa
POST   /api/config/{id}/deploy   Aplicar (backup+compare+commit)
POST   /api/config/{id}/rollback Revertir
```

### Comandos
```
POST   /api/command/{id}         Ejecutar comandos seguros
POST   /api/command/bulk         Comandos en lote
```

### Templates
```
GET    /api/templates            Listar templates
GET    /api/templates/{name}     Ver template
POST   /api/templates/{name}/render   Vista previa
POST   /api/templates/{name}/apply    Dry-run → diff
POST   /api/templates/{name}/apply/confirm  Commit
```

### Bulk (Nornir paralelo)
```
GET    /api/bulk/status          Estado de todos
POST   /api/bulk/facts           Facts en paralelo
POST   /api/bulk/backup          Backup masivo
```

### Seguridad
```
POST   /api/auth/login           Login → JWT
GET    /api/auth/me              Usuario actual
GET    /api/audit                Eventos de auditoría
GET    /api/audit/stats          Estadísticas
```

### NetBox
```
GET    /api/netbox/devices       Inventario NetBox
GET    /api/netbox/sites         Sites
GET    /api/netbox/ipam/*        IPAM
POST   /api/netbox/sync          Sync NetBox → devices.yaml
POST   /api/netbox/sync/push     Push → NetBox
```

### Monitoreo
```
GET    /api/metrics              Métricas Prometheus (público)
GET    /api/health               Health check (público)
```

---

## 📝 Templates de configuración

| Template | Parámetros | Drivers |
|----------|-----------|---------|
| `vlan` | vlan_id, vlan_name | ios, junos, eos |
| `interface` | interface, ip, mask, description | ios, junos, eos |
| `bgp_peer` | local_as, peer_ip, remote_as, description | ios, junos, eos |
| `acl` | acl_number, action, source, dest | ios, eos |
| `ospf` | process_id, network, wildcard, area | ios, eos |

### Flujo de aplicación

```
1. POST /templates/vlan/render   → Vista previa: "vlan 100\n name IoT"
2. POST /templates/vlan/apply    → Dry-run: diff mostrando cambios
3. (Operador revisa el diff)     → Aprobación humana
4. POST /templates/.../confirm   → Commit + backup + auditoría
5. Si falla → POST /config/rollback → Revertir
```

---

## 📊 Observabilidad

### Prometheus (:9090)
- Scrapea `/api/metrics` cada 15s
- Métricas: requests, latencia, dispositivos up/down, auditoría

### Grafana (:3001)
- Dashboard "NetPulse — Monitoreo de Red" con 8 paneles
- Requests/s, latencia p50/p95/p99, estado de devices, auditoría

### Loki (:3100)
- Logs centralizados (configurado, sin promtail todavía)

---

## 🖥️ Interfaces de usuario

| URL | Interfaz |
|-----|----------|
| `http://localhost:8082` | Dashboard de gestión (HTML5) |
| `http://localhost:8082/docs` | Swagger UI (probar API) |
| `http://localhost:3001` | Grafana (admin/admin) |
| `http://localhost:8000` | NetBox (admin/admin) |
| `http://localhost:9090` | Prometheus |

---

## 🚀 Arranque rápido

```bash
cd /home/doker/GreenAlgorithm/NetPulse
source .venv/bin/activate

# Contenedores
docker start netpulse-cisco netpulse-juniper netpulse-switch \
              netpulse-mikrotik netpulse-arista \
              netpulse-prometheus netpulse-grafana netpulse-loki \
              netpulse-netbox netpulse-netbox-db netpulse-netbox-redis

# API
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8082 --reload

# Abrir dashboard
firefox http://localhost:8082
```

---

## 📂 Estructura del proyecto

```
NetPulse/
├── app/
│   ├── main.py                  # FastAPI app
│   ├── core/                    # Settings, security (JWT)
│   ├── models/schemas.py        # Pydantic (30+ modelos)
│   ├── services/                # Lógica de negocio
│   │   ├── napalm_svc.py        # NAPALM wrapper + SSL fix Arista
│   │   ├── nornir_svc.py        # Orquestador paralelo
│   │   ├── inventory_svc.py     # CRUD devices.yaml
│   │   ├── audit_svc.py         # Auditoría inmutable
│   │   ├── crypto_svc.py        # Fernet encryption
│   │   ├── metrics_svc.py       # Prometheus metrics
│   │   ├── templates_svc.py     # Jinja2 templates
│   │   └── netbox_svc.py        # NetBox API client
│   ├── routers/                 # 10 routers
│   │   ├── devices.py, facts.py, config.py  # Core
│   │   ├── command.py, templates.py         # Gestión
│   │   ├── bulk.py, audit.py, auth.py       # Operaciones
│   │   ├── netbox.py, metrics.py            # Integraciones
│   ├── middleware/              # 5 middlewares
│   │   ├── auth.py, rbac.py    # JWT + roles
│   │   ├── audit.py, metrics.py, security.py
│   └── static/dashboard.html   # Dashboard UI
├── config/
│   ├── devices.yaml            # Inventario (creds encriptadas)
│   ├── users.yaml              # Usuarios + bcrypt
│   ├── templates/              # 5 templates YAML
│   ├── prometheus.yml          # Scrape config
│   └── .netbox_token           # Token NetBox
├── docker-compose.lab.yml      # Lab (5 dispositivos)
├── docker-compose.monitoring.yml # Prometheus + Grafana + Loki
├── docker-compose.netbox.yml   # NetBox + PostgreSQL + Redis
├── audit/                      # Audit JSONL inmutable
├── backups/                    # Backups de config
├── docs/                       # Documentación por fase
└── scripts/                    # Utilidades
```
