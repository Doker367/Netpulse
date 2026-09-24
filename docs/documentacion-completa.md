# NETPULSE — Documentación Técnica Completa

> Plataforma Open Source para gestión centralizada de infraestructura de red multivendor en centros de datos.
> Versión: 1.0 | Stack: FastAPI + NAPALM + Nornir + NetBox + Prometheus/Grafana

---

## ÍNDICE

1. [VISIÓN GENERAL](#1-visión-general)
2. [ARQUITECTURA DEL SISTEMA](#2-arquitectura-del-sistema)
3. [STACK TECNOLÓGICO](#3-stack-tecnológico)
4. [COMPONENTES DEL SISTEMA](#4-componentes-del-sistema)
5. [FLUJO DE DATOS](#5-flujo-de-datos)
6. [SEGURIDAD](#6-seguridad)
7. [API REST — ENDPOINTS](#7-api-rest--endpoints)
8. [INTEGRACIONES CON FABRICANTES](#8-integraciones-con-fabricantes)
9. [INFRAESTRUCTURA DOCKER](#9-infraestructura-docker)
10. [INTERFACES DE USUARIO](#10-interfaces-de-usuario)
11. [CASOS DE USO](#11-casos-de-uso)
12. [CICLO DE CAMBIOS](#12-ciclo-de-cambios)
13. [DIAGRAMA DE ARQUITECTURA](#13-diagrama-de-arquitectura)

---

## 1. VISIÓN GENERAL

### 1.1 ¿Qué es NetPulse?

NetPulse es una plataforma que **unifica la gestión de dispositivos de red de diferentes fabricantes** en un solo lugar. En lugar de conectarse por separado a cada switch, router o firewall usando diferentes comandos y protocolos, el operador usa una **API REST centralizada** o un **dashboard web** para administrar toda la infraestructura.

### 1.2 Problema que resuelve

En un centro de datos típico:

- Hay dispositivos de **múltiples fabricantes** (Cisco, Juniper, Arista, MikroTik, Fortinet, Palo Alto)
- Cada fabricante tiene su **propio sistema de comandos** (IOS, JunOS, EOS, RouterOS)
- Los operadores deben **memorizar comandos diferentes** para cada fabricante
- Los cambios se hacen **manualmente**, uno por uno
- **No hay trazabilidad** de quién hizo qué cambio
- **No hay respaldo automático** antes de modificar configuraciones
- **Recuperación ante fallos** es lenta y manual

### 1.3 Solución

Una **plataforma unificada** donde:

- Un solo comando o clic puede consultar o modificar **cualquier dispositivo, cualquier fabricante**
- **Todas las operaciones quedan registradas** en una auditoría inmutable
- **Backup automático** antes de cada cambio
- **Rollback** automático si algo falla
- **Dashboards** con métricas en tiempo real
- **Templates** para tareas recurrentes (VLAN, BGP, interfaces)
- **Control de acceso** por roles (quién puede ver, quién puede modificar)

---

## 2. ARQUITECTURA DEL SISTEMA

### 2.1 Diagrama de capas

```
┌────────────────────────────────────────────────────────────────────┐
│                    CAPA DE PRESENTACIÓN                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │  Dashboard    │  │   Swagger    │  │   Grafana    │              │
│  │  Web (HTML5)  │  │   UI (/docs) │  │  Dashboards  │              │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘              │
│         │                  │                  │                      │
├─────────┼──────────────────┼──────────────────┼────────────────────┤
│         │         HTTP     │       HTTP       │       HTTP          │
│         ▼                  ▼                  ▼                      │
│                    CAPA DE API (FastAPI :8082)                       │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Routers: devices, facts, config, command, templates,        │  │
│  │  bulk, auth, audit, netbox, metrics                          │  │
│  │  Middleware: auth → rbac → audit → metrics → rate-limit      │  │
│  └──────┬───────────────────────────────────────────────┬───────┘  │
│         │                                                │          │
├─────────┼────────────────────────────────────────────────┼────────┤
│         ▼                                                ▼          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │  NAPALM Svc  │  │  Nornir Svc │  │ Templates    │              │
│  │  (SSH+eAPI)  │  │  (Paralelo) │  │ (Jinja2)     │              │
│  └──────┬───────┘  └─────────────┘  └──────────────┘              │
│         │                                                          │
│         ▼                                                          │
│  ┌──────────────────────────────────────────────────────┐          │
│  │  DISPOSITIVOS DE RED                                  │          │
│  │  [Cisco IOS] [Juniper] [Arista EOS] [MikroTik] [FRR] │          │
│  └──────────────────────────────────────────────────────┘          │
│                                                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │  NetBox      │  │  Prometheus  │  │  Loki        │              │
│  │  (DCIM :8000)│  │  (Métricas)  │  │  (Logs)      │              │
│  └──────────────┘  └──────┬───────┘  └──────────────┘              │
│                            │                                        │
│                       ┌────┴────┐                                  │
│                       │ Grafana │                                  │
│                       │ (:3001) │                                  │
│                       └─────────┘                                  │
└────────────────────────────────────────────────────────────────────┘
```

### 2.2 Flujo de una solicitud típica

```
USUARIO (Dashboard Web)
  │
  │ POST /api/templates/vlan/apply
  │ {"device_id": "arista-core-01", "params": {"vlan_id": 100, "vlan_name": "IoT"}}
  │
  ▼
MIDDLEWARE AUTH (JWT)
  │ Valida token JWT
  │ Verifica rol: admin → puede continuar
  ▼
MIDDLEWARE RBAC
  │ Verifica que admin tenga permiso para "apply template"
  ▼
ROUTER TEMPLATES
  │ 1. Carga template "vlan" desde config/templates/vlan.yaml
  │ 2. Detecta driver del dispositivo: "eos"
  │ 3. Renderiza con Jinja2: "vlan 100\n name IoT"
  │ 4. Llama a napalm_svc para dry-run
  │
  ▼
NAPALM SERVICE
  │ 1. Conecta a arista-core-01:443 (eAPI HTTPS)
  │ 2. Obtiene running-config actual
  │ 3. Carga candidate (merge): "vlan 100\n name IoT"
  │ 4. Ejecuta compare_config() → genera DIFF
  │ 5. Desconecta
  │ 6. Retorna: {diff: "--- a/... +++ b/...", session_token}
  │
  ▼
AUDIT SERVICE
  │ Registra evento: usuario=admin, acción=template_apply
  │ dispositivo=arista-core-01, resultado=dry_run_ok
  │
  ▼
RESPUESTA AL USUARIO
  > {
  >   "diff": "--- actual\n+++ nuevo\n+ vlan 100\n+  name IoT\n",
  >   "dry_run_ok": true,
  >   "session_token": "abc-123"
  > }
  │
  │ (USUARIO REVISA EL DIFF)
  │
  │ POST /api/templates/vlan/apply/confirm
  │ {"session_token": "abc-123"}
  │
  ▼
NAPALM SERVICE (CONFIRMAR)
  │ 1. Conecta al dispositivo
  │ 2. Backup automático de la config actual
  │ 3. Ejecuta commit_config()
  │ 4. Verifica que el cambio sea exitoso
  │ 5. Desconecta
  │ 6. Si falla → rollback automático
  │
  ▼
AUDIT SERVICE
  │ Registra evento: commit exitoso
  │ Guarda backup en backups/
```

---

## 3. STACK TECNOLÓGICO

### 3.1 Lenguajes

| Lenguaje | Uso |
|----------|-----|
| **Python 3.14** | Backend completo (API, servicios, automatización) |
| **HTML5 + CSS3 + JavaScript** | Dashboard web frontend |
| **YAML** | Configuración (inventario, templates, Prometheus) |
| **SQL** | Consultas a base de datos (NetBox, auditoría) |

### 3.2 Librerías Python (backbone)

| Librería | Versión | Función |
|----------|---------|---------|
| **fastapi** | 0.136 | Framework web async para la API REST |
| **uvicorn** | 0.49 | Servidor ASGI de alto rendimiento |
| **napalm** | 5.1.0 | Abstracción multi-vendor para dispositivos de red |
| **netmiko** | 4.7.0 | SSH a dispositivos de red (base de NAPALM) |
| **nornir** | 3.5.0 | Orquestador de operaciones paralelas |
| **pydantic** | 2.13 | Validación de datos y schemas |
| **python-jose** | — | Generación y validación de tokens JWT |
| **passlib[bcrypt]** | — | Hashing seguro de contraseñas |
| **cryptography** | — | Encriptación Fernet (AES) de credenciales |
| **prometheus-client** | — | Exportación de métricas en formato Prometheus |
| **httpx** | — | Cliente HTTP async para comunicarse con NetBox |
| **Jinja2** | — | Motor de templates para generar configuraciones |
| **PyYAML** | — | Parseo de archivos YAML |
| **slowapi** | — | Rate limiting por IP/usuario |
| **librouteros** | — | API directa a MikroTik RouterOS (fallback) |
| **python-docx** | — | Generación de documentos Word |

### 3.3 Infraestructura

| Componente | Tecnología | Propósito |
|-----------|-----------|-----------|
| **Contenedores** | Docker 29.5 | Aislamiento y despliegue de servicios |
| **Orquestación** | Docker Compose | Gestión de stacks multi-contenedor |
| **Base de datos** | PostgreSQL 15 | NetBox, auditoría futura |
| **Caché** | Redis 7 | Sesiones, rate limiting |
| **DCIM** | NetBox 4.6 | Inventario de dispositivos |
| **Métricas** | Prometheus | Recolección de métricas |
| **Dashboards** | Grafana 13 | Visualización de métricas |
| **Logs** | Loki | Centralización de logs |

---

## 4. COMPONENTES DEL SISTEMA

### 4.1 app/main.py — Punto de entrada

Archivo principal de FastAPI. Aquí se:

1. Configura la aplicación FastAPI con título, versión y lifespan
2. Registran los middlewares en orden:
   - **Metrics** (1ro — captura TODAS las requests)
   - **SecurityHeaders** (2do — headers de seguridad HTTP)
   - **CORS** (3ro — permitir origen del dashboard)
   - **SlowAPI** (4to — rate limiting)
   - **Audit** (5to — registro de eventos)
3. Registran los 10 routers (devices, facts, config, command, templates, bulk, auth, audit, netbox, metrics)
4. Sirve el dashboard estático en la ruta raíz `/`

### 4.2 Routers (10 módulos)

| Router | Endpoints | Rol requerido | Función |
|--------|-----------|---------------|---------|
| **devices.py** | 7 | viewer+ / admin | CRUD inventario + health check |
| **facts.py** | 5 | viewer+ | Facts, interfaces, BGP, ping |
| **config.py** | 7 | operator+ / admin | Config, backup, diff, deploy, rollback |
| **command.py** | 2 | operator+ | Ejecución de comandos seguros |
| **templates.py** | 5 | admin | Templates + dry-run + confirm |
| **bulk.py** | 4 | operator+ / admin | Operaciones paralelas (Nornir) |
| **auth.py** | 2 | público | Login, información del usuario |
| **audit.py** | 2 | admin | Consulta de auditoría |
| **netbox.py** | 8 | admin | NetBox (inventario, IPAM, sync) |
| **metrics.py** | 1 | público | Exportación de métricas Prometheus |
| **Total** | **~38** | | |

### 4.3 Services (7 módulos)

| Service | Función principal |
|---------|------------------|
| **napalm_svc.py** | Capa de abstracción NAPALM. Conexión SSH/eAPI a dispositivos. Operaciones: facts, interfaces, BGP, config, deploy, rollback, comandos. Incluye workaround para MikroTik ROS 7.22 y SSL fix para Arista vEOS. |
| **nornir_svc.py** | Orquestador Nornir. Ejecuta operaciones en paralelo en múltiples dispositivos usando ThreadedRunner (hasta 10 workers). Fallback serial si Nornir falla. |
| **audit_svc.py** | Auditoría inmutable. Escribe eventos en `audit/audit.jsonl` (append-only, sin delete). Thread-safe con locks. |
| **inventory_svc.py** | CRUD del inventario YAML (`config/devices.yaml`). Migración de lab devices. |
| **templates_svc.py** | Motor de templates Jinja2. Renderiza configuraciones multi-driver. Gestiona dry-run → confirm con session tokens. |
| **crypto_svc.py** | Encriptación Fernet (AES). Cifra/descifra credenciales en devices.yaml. Genera y gestiona clave en `config/.fernet_key`. |
| **metrics_svc.py** | Métricas Prometheus. Define counters, histograms, gauges. |

### 4.4 Middleware (5 módulos)

| Middleware | Orden | Función |
|-----------|-------|---------|
| **metrics.py** | 1ro | Cuenta requests, mide latencia |
| **security.py** | 2do | Headers: X-Content-Type-Options, HSTS, etc. |
| **auth.py** | — | Dependencia JWTBearer para endpoints protegidos |
| **rbac.py** | — | Dependencia requires_role(role) |
| **audit.py** | 5to | ASGI puro, registra cada request en audit.jsonl |

---

## 5. FLUJO DE DATOS

### 5.1 Datos que fluyen por el sistema

```
Configuración de dispositivos (texto plano)
        |
        ▼
devices.yaml ──→ cripto_svc (encripta) ──→ devices.yaml (cifrado)
        │
        ▼
inventory_svc ──→ napalm_svc ──→ NAPALM ──→ SSH/eAPI ──→ Dispositivo
        │                                              │
        ▼                                              ▼
NetBox (DCIM)                                    Config (respuesta)
        │                                              │
        ▼                                              ▼
netbox_svc ←── sincronización bidireccional      audit_svc (JSONL)
                                                      │
                                                      ▼
                                                audit/audit.jsonl
```

### 5.2 Flujo de autenticación

```
USUARIO                    API                        users.yaml
  │                         │                            │
  │ POST /api/auth/login    │                            │
  │ {user: admin, pass} ────┤                            │
  │                         ├── buscar usuario ──────────┤
  │                         │ ←── hash + rol ────────────┤
  │                         │                            │
  │                         ├── verify_password()        │
  │                         ├── create_access_token()    │
  │                         │                            │
  │ ←── {access_token} ─────┤                            │
  │                         │                            │
  │ GET /api/devices        │                            │
  │ Authorization: Bearer   ─┤                           │
  │                         ├── verify_token()           │
  │                         ├── get_current_user()       │
  │                         ├── requires_role("viewer")  │
  │                         ├── (ejecuta operación)      │
  │ ←── {dispositivos} ────┤                            │
```

### 5.3 Flujo de NAPALM (conexión a dispositivo)

```
napalm_svc._execute()
  │
  ├─ 1. get_network_driver(driver)  → "ios", "eos", "ros"
  │
  ├─ 2. driver(hostname, username, password, optional_args)
  │     └─ optional_args: {port: 2222, transport: "https", ...}
  │
  ├─ 3. dev.open()
  │     └─ SSH/Telnet/eAPI/API según el driver
  │
  ├─ 4. fn = getattr(dev, operation)
  │     └─ get_facts(), get_interfaces(), get_config(), cli(), ...
  │
  ├─ 5. result.data = fn(*args)
  │
  ├─ 6. dev.close()
  │
  ├─ 7. Retorna NapalmResult(device_id, success, data, error, error_type, duration_ms)
  │
  └─ En caso de error:
       ├─ Clasifica: connection_error, auth_error, timeout, command_error, driver_error
       └─ Retorna NapalmResult con error y error_type
```

---

## 6. SEGURIDAD

### 6.1 Esquema de autenticación

| Componente | Implementación |
|-----------|---------------|
| **Algoritmo** | HS256 (HMAC-SHA256) |
| **Secreto** | Variable de entorno `NETPULSE_JWT_SECRET` |
| **Expiración** | 30 minutos access token |
| **Almacenamiento** | Sesión stateless (sin base de datos) |
| **Usuarios** | `config/users.yaml` con contraseñas hasheadas (bcrypt) |

### 6.2 Roles y permisos

```
ROLES:
  viewer  → GET /api/devices, GET /api/facts/*, GET /api/health
  operator→ viewer + GET /api/config/*, POST /api/command/*, bulk/status
  admin   → operator + POST/PUT/DELETE /api/devices/*, deploy, rollback,
             templates, audit, netbox

MATRIZ DE PERMISOS:
┌─────────────────────┬─────────┬──────────┬─────────┐
│ Endpoint            │ viewer  │ operator │ admin   │
├─────────────────────┼─────────┼──────────┼─────────┤
│ GET /api/devices    │ ✅      │ ✅       │ ✅      │
│ POST /api/devices   │ ❌      │ ❌       │ ✅      │
│ DELETE /api/devices │ ❌      │ ❌       │ ✅      │
│ GET /api/facts/*    │ ✅      │ ✅       │ ✅      │
│ GET /api/config/*   │ ❌      │ ✅       │ ✅      │
│ POST /api/config/*  │ ❌      │ ❌       │ ✅      │
│ POST /api/command/* │ ❌      │ ✅       │ ✅      │
│ POST /api/deploy/*  │ ❌      │ ❌       │ ✅      │
│ GET /api/templates  │ ❌      │ ✅       │ ✅      │
│ POST /api/templates │ ❌      │ ❌       │ ✅      │
│ GET /api/audit      │ ❌      │ ❌       │ ✅      │
└─────────────────────┴─────────┴──────────┴─────────┘
```

### 6.3 Protección de credenciales de red

```
Estado original:
  devices.yaml:
    credentials:
      password: "<password>"             ← Texto plano (INSEGURO)

Después de crypt_svc:
  devices.yaml:
    credentials:
      password: "gAAAAA...Sg=="      ← Encriptado (Fernet AES)
                                        Solo descifrable con la clave
                                        en config/.fernet_key
```

### 6.4 Seguridad en comandos

Filtro estricto de comandos. Solo se permiten:

**Permitidos:** `show`, `display`, `get`, `ping`, `traceroute`, `trace`

**Bloqueados:** `configure`, `conf t`, `write`, `copy`, `delete`, `erase`, `reload`, `format`, `rm`, `commit`, `restart`

### 6.5 Auditoría inmutable

Formato: `audit/audit.jsonl` (JSON Lines, append-only)

```json
{"timestamp": "2026-06-09T02:29:52", "action": "api_request",
 "method": "GET", "path": "/api/devices", "status": 200,
 "username": "admin", "ip": "172.17.0.1", "success": true,
 "duration_ms": 45}
```

- **Inmutable:** no se puede modificar ni eliminar entradas existentes
- **Thread-safe:** usa locks para evitar corrupción en escrituras concurrentes
- **Filtrable:** consultable por dispositivo, usuario, acción, rango de fechas

### 6.6 Rate limiting

| Límite | Alcance |
|--------|---------|
| 100 requests/minuto | Por IP (global) |
| 5 requests/minuto | Por IP (login) |
| 10 requests/minuto | Health check |

---

## 7. API REST — ENDPOINTS

### 7.1 Listado completo

#### 📟 Inventario (devices)

| Método | Ruta | Descripción | Rol |
|--------|------|-------------|-----|
| `GET` | `/api/devices` | Listar dispositivos | viewer+ |
| `GET` | `/api/devices/{id}` | Dispositivo individual | viewer+ |
| `POST` | `/api/devices` | Agregar dispositivo | admin |
| `PUT` | `/api/devices/{id}` | Actualizar dispositivo | admin |
| `DELETE` | `/api/devices/{id}` | Eliminar dispositivo | admin |
| `GET` | `/api/devices/health` | Estado online/offline (TCP) | **público** |
| `POST` | `/api/devices/lab/init` | Inicializar lab (5 devices) | admin |

#### ℹ️ Facts (facts)

| Método | Ruta | Descripción | Rol |
|--------|------|-------------|-----|
| `GET` | `/api/facts/{id}` | Facts del dispositivo | viewer+ |
| `GET` | `/api/facts/{id}/interfaces` | Interfaces | viewer+ |
| `GET` | `/api/facts/{id}/bgp` | Peers BGP | viewer+ |
| `POST` | `/api/facts/{id}/ping` | Ping desde dispositivo | viewer+ |

#### ⚙️ Configuración (config)

| Método | Ruta | Descripción | Rol |
|--------|------|-------------|-----|
| `GET` | `/api/config/{id}` | Running config JSON | operator+ |
| `GET` | `/api/config/{id}/raw` | Running config texto | operator+ |
| `POST` | `/api/config/{id}/backup` | Backup a disco | admin |
| `POST` | `/api/config/{id}/diff` | Comparar configs | operator+ |
| `POST` | `/api/config/{id}/deploy/dry-run` | Vista previa de cambios | operator+ |
| `POST` | `/api/config/{id}/deploy` | Aplicar cambios (commit) | admin |
| `POST` | `/api/config/{id}/rollback` | Revertir cambios | admin |

#### 💻 Comandos (command)

| Método | Ruta | Descripción | Rol |
|--------|------|-------------|-----|
| `POST` | `/api/command/{id}` | Ejecutar comando seguro | operator+ |
| `POST` | `/api/command/bulk` | Comando en lote | operator+ |

#### 📝 Templates (templates)

| Método | Ruta | Descripción | Rol |
|--------|------|-------------|-----|
| `GET` | `/api/templates` | Listar templates | operator+ |
| `GET` | `/api/templates/{name}` | Ver template | operator+ |
| `POST` | `/api/templates/{name}/render` | Vista previa del template | operator+ |
| `POST` | `/api/templates/{name}/apply` | Dry-run + diff | admin |
| `POST` | `/api/templates/{name}/apply/confirm` | Confirmar y aplicar | admin |

#### 🔄 Bulk (operaciones paralelas)

| Método | Ruta | Descripción | Rol |
|--------|------|-------------|-----|
| `GET` | `/api/bulk/status` | Estado de todos | operator+ |
| `POST` | `/api/bulk/status` | Estado filtrado | operator+ |
| `POST` | `/api/bulk/facts` | Facts en paralelo | operator+ |
| `POST` | `/api/bulk/backup` | Backup masivo | admin |

#### 🔐 Autenticación (auth)

| Método | Ruta | Descripción | Rol |
|--------|------|-------------|-----|
| `POST` | `/api/auth/login` | Login → JWT token | **público** |
| `GET` | `/api/auth/me` | Información del usuario | viewer+ |

#### 📋 Auditoría (audit)

| Método | Ruta | Descripción | Rol |
|--------|------|-------------|-----|
| `GET` | `/api/audit` | Log de eventos (filtrable) | admin |
| `GET` | `/api/audit/stats` | Estadísticas de auditoría | admin |

#### 📦 NetBox

| Método | Ruta | Descripción | Rol |
|--------|------|-------------|-----|
| `GET` | `/api/netbox/devices` | Dispositivos desde NetBox | admin |
| `GET` | `/api/netbox/devices/{id}` | Dispositivo individual | admin |
| `GET` | `/api/netbox/sites` | Sites del datacenter | admin |
| `GET` | `/api/netbox/ipam/prefixes` | Prefijos IP | admin |
| `GET` | `/api/netbox/ipam/ip-addresses` | Direcciones IP | admin |
| `GET` | `/api/netbox/vlans` | VLANs | admin |
| `POST` | `/api/netbox/sync` | Sync NetBox → devices.yaml | admin |
| `POST` | `/api/netbox/sync/push` | Push devices.yaml → NetBox | admin |

#### 📊 Monitoreo (metrics)

| Método | Ruta | Descripción | Rol |
|--------|------|-------------|-----|
| `GET` | `/api/metrics` | Métricas Prometheus | **público** |
| `GET` | `/api/health` | Health check | **público** |

### 7.2 Formatos de request/response

#### Login
```bash
POST /api/auth/login
Request:  {"username": "admin", "password": "<password>"}
Response: {"access_token": "eyJ...", "token_type": "bearer"}
```

#### Facts
```bash
GET /api/facts/arista-core-01
Response: {
  "device_id": "arista-core-01",
  "hostname": "vr-xrv9k",
  "os_version": "4.27.3F-26379303.4273F",
  "model": "vEOS-lab",
  "vendor": "Arista",
  "serial_number": "",
  "uptime": 2301.9,
  "interface_count": 1
}
```

#### Command
```bash
POST /api/command/cisco-core-01
Request:  {"commands": ["show version", "show ip route"]}
Response: {
  "device_id": "cisco-core-01",
  "results": [
    {"command": "show version", "output": "frr version 8.4_git\n...", "error": null},
    {"command": "show ip route", "output": "Codes: K - kernel route...", "error": null}
  ]
}
```

#### Deploy (dry-run)
```bash
POST /api/config/cisco-core-01/deploy
Request:  {"candidate_config": "hostname cisco-nuevo\n!"}
Response: {
  "device_id": "cisco-core-01",
  "diff": "--- cisco-core-01-running\n+++ cisco-core-01-candidate\n@@ -1 +1 @@\n-hostname cisco-core-01\n+hostname cisco-nuevo\n",
  "committed": true,
  "backup_file": "backups/cisco-core-01_20260609_023000.cfg",
  "rollback_available": true
}
```

#### Template
```bash
POST /api/templates/vlan/apply
Request:  {"device_id": "arista-core-01", "params": {"vlan_id": "100", "vlan_name": "IoT"}}
Response: {
  "device_id": "arista-core-01",
  "template": "vlan",
  "driver": "eos",
  "diff": "...",
  "dry_run_ok": true,
  "session_token": "44844401-f881-42ec-b87a-c2c2a6ef11c5"
}

POST /api/templates/vlan/apply/confirm
Request:  {"session_token": "44844401-f881-42ec-b87a-c2c2a6ef11c5"}
Response: {"success": true, "committed": true}
```

---

## 8. INTEGRACIONES CON FABRICANTES

### 8.1 Dispositivos soportados (actualmente en laboratorio)

| Dispositivo | Fabricante | Driver NAPALM | Transporte | Puerto |
|-----------|-----------|---------------|------------|--------|
| cisco-core-01 | FRRouting | `ios` | SSH | :2222 |
| juniper-edge-01 | FRRouting | `ios` | SSH | :2223 |
| switch-access-01 | FRRouting | `ios` | SSH | :2224 |
| arista-core-01 | Arista vEOS | `eos` | eAPI HTTPS | :443 |
| mikrotik-edge-01 | MikroTik RouterOS | `ros` | API RouterOS | :8728 |

### 8.2 Drivers NAPALM disponibles

| Driver | Fabricantes | Puerto típico | Notas |
|--------|------------|---------------|-------|
| `ios` | Cisco IOS, FRRouting | 22 | SSH. FRR tiene soporte limitado (facts incompletos) |
| `iosxr` | Cisco IOS-XR | 22 | SSH. |
| `nxos` | Cisco NX-OS (Nexus) | 22 | SSH. |
| `junos` | Juniper JunOS | 830 | NETCONF sobre SSH. |
| `eos` | Arista EOS | 443 | eAPI HTTPS. Requiere cipher legacy fix |
| `ros` | MikroTik RouterOS | 8728 | API RouterOS. ROS 7.x parcial |
| `fortios` | Fortinet FortiGate | 22/443 | Requiere driver adicional |
| `huawei` | Huawei VRP | 22 | Requiere driver adicional |

### 8.3 Cómo NetPulse maneja cada fabricante

#### Cisco IOS / FRRouting
- **Driver:** `ios`
- **Transporte:** SSH (puerto 22)
- **Config:** NAPALM usa `show running-config` y `configure terminal`
- **Limitaciones FRR:** Facts devuelven "Unknown" (FRR no es Cisco real)
- **BGP:** Fallback a `get_bgp_config()` cuando `get_bgp_neighbors()` falla

#### Arista EOS
- **Driver:** `eos`
- **Transporte:** eAPI (HTTPS sobre puerto 443)
- **Config:** API REST nativa de Arista
- **SSL:** Requiere monkey-patch para ciphers legacy (AES256-SHA)
- **Facts:** Completos (hostname, versión, modelo, uptime)

#### MikroTik RouterOS
- **Driver:** `ros`
- **Transporte:** API RouterOS (puerto 8728)
- **Config:** API binaria de MikroTik
- **Workaround:** `napalm-ros` incompatible con ROS 7.22 → fallback a `librouteros`
- **Facts:** Parciales (hostname, modelo, versión)
- **Comandos:** No soporta CLI show — usar endpoints `/api/facts/`

#### Juniper JunOS
- **Driver:** `junos`
- **Transporte:** NETCONF (SSH puerto 830)
- **Config:** XML sobre NETCONF
- **Nota:** Requiere `junos-eznc` (incluido en venv)

#### Cisco NX-OS (Nexus)
- **Driver:** `nxos`
- **Transporte:** SSH
- **Compatibilidad:** Total con NAPALM

### 8.4 Matriz de operaciones por fabricante

| Operación | Cisco IOS | Arista EOS | MikroTik | FRRouting |
|-----------|-----------|------------|----------|-----------|
| get_facts | ✅ | ✅ | ⚠️ Parcial | ⚠️ Básico |
| get_interfaces | ✅ | ✅ | ✅ | ✅ |
| get_config | ✅ | ✅ | — | ✅ |
| get_bgp_neighbors | ✅ | ✅ | — | ⚠️ Fallback |
| compare_config | ✅ | ✅ | — | Fallback difflib |
| commit_config | ✅ | ✅ | — | Fallback |
| rollback | ✅ | ✅ | — | ❌ |
| cli (show) | ✅ | ✅ | ❌ API | ✅ |

---

## 9. INFRAESTRUCTURA DOCKER

### 9.1 Stacks de contenedores

#### Stack 1: Laboratorio de Red — `docker-compose.lab.yml`
```yaml
Servicios:
  - router-cisco     (frrouting/frr, :2222)
  - router-juniper   (frrouting/frr, :2223)
  - switch-l2        (frrouting/frr, :2224)
  - router-mikrotik  (vaerh/routeros, :2225, :8728)
  - router-arista    (cminsg/vr-veos, :2226, :443)
```

#### Stack 2: NetBox — `docker-compose.netbox.yml`
```yaml
Servicios:
  - netbox           (netboxcommunity/netbox, :8000)
  - postgres         (postgres:15, interno)
  - redis            (redis:7, interno)
```

#### Stack 3: Monitoreo — `docker-compose.monitoring.yml`
```yaml
Servicios:
  - prometheus       (prom/prometheus, :9090)
  - grafana          (grafana/grafana, :3001)
  - loki             (grafana/loki, :3100)
```

### 9.2 Resumen de puertos

| Puerto | Servicio | Contenedor |
|--------|---------|------------|
| **2222** | SSH Cisco | netpulse-cisco |
| **2223** | SSH Juniper | netpulse-juniper |
| **2224** | SSH Switch | netpulse-switch |
| **2225** | SSH MikroTik | netpulse-mikrotik |
| **2226** | SSH Arista | netpulse-arista |
| **443** | eAPI Arista | netpulse-arista |
| **8728** | API MikroTik | netpulse-mikrotik |
| **8000** | Web NetBox | netpulse-netbox |
| **8082** | API NetPulse | Host directo |
| **9090** | Web Prometheus | netpulse-prometheus |
| **3001** | Web Grafana | netpulse-grafana |
| **3100** | API Loki | netpulse-loki |

---

## 10. INTERFACES DE USUARIO

### 10.1 Dashboard Web (:8082)

Interfaz principal para operación diaria:

```
┌─────────────────────────────────────────────────────┐
│ ⚡ NetPulse — Gestión de Red           [admin ✅]   │
├─────────────────────────────────────────────────────┤
│ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐        │
│ │ 5 Disp │ │ 5 UP   │ │ 0 DOWN │ │ Sesión │        │
│ └────────┘ └────────┘ └────────┘ └────────┘        │
├─────────────────────────────────────────────────────┤
│ 📡 DISPOSITIVOS                                      │
│ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ │
│ │ cisco-core-01│ │arista-core-01│ │mikrotik-edge │ │
│ │ IOS·router   │ │ EOS·router   │ │ ROS·router   │ │
│ │ 🟢 Online    │ │ 🟢 Online    │ │ 🟢 Online    │ │
│ └──────────────┘ └──────────────┘ └──────────────┘ │
├─────────────────────────────────────────────────────┤
│ 🔧 cisco-core-01                                     │
│ ┌──────┬──────┬───────┬────────┬────────┐           │
│ │Facts│Ifaces│Config│Comandos│Templates│           │
│ └──────┴──────┴───────┴────────┴────────┘           │
│                                                      │
│ Terminal: cisco-core-01$ show version                │
│ FRRouting 8.4_git on Linux                           │
│ ...                                                  │
└─────────────────────────────────────────────────────┘
```

**Funcionalidades:**
- Cards de dispositivos con estado online/offline (actualiza cada 15s)
- Click en dispositivo → panel detalle con 5 pestañas
- Facts, interfaces, config
- Terminal de comandos (show, ping)
- Templates con dry-run → confirm

### 10.2 Swagger UI (:8082/docs)

Documentación interactiva de la API. Permite probar cada endpoint con autenticación JWT.

### 10.3 Grafana (:3001)

Dashboards de monitoreo con:
- Request rate (req/s)
- Latencia p50/p95/p99
- Dispositivos UP (gauge)
- Estado histórico de dispositivos
- Eventos de auditoría

### 10.4 NetBox (:8000)

INVENTARIO DCIM completo:
- Sites (ubicaciones físicas)
- Racks
- Dispositivos con fabricante, modelo, rol
- IPAM (IP Address Management)
- VLANs
- Cableado

---

## 11. CASOS DE USO

### Caso 1: Diagnóstico — Verificar si un switch está online

```bash
curl http://localhost:8082/api/devices/health
# → {"cisco-core-01": true, "switch-access-01": true, ...}

curl -H "Authorization: Bearer <token>" \
  http://localhost:8082/api/facts/switch-access-01
# → {hostname, os_version, uptime, interfaces...}

curl -H "Authorization: Bearer <token>" \
  http://localhost:8082/api/facts/switch-access-01/interfaces
# → {interfaces: [{name: "eth0", is_up: true}, ...]}
```

### Caso 2: Cambio controlado — Agregar VLAN 100

```bash
# 1. Ver template
curl -H "Authorization: Bearer <token>" \
  http://localhost:8082/api/templates/vlan

# 2. Vista previa del comando que se va a ejecutar
curl -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -X POST http://localhost:8082/api/templates/vlan/render \
  -d '{"driver": "ios", "params": {"vlan_id": "100", "vlan_name": "IoT"}}'
# → {"rendered_config": "vlan 100\n name IoT"}

# 3. Dry-run contra el switch
curl -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -X POST http://localhost:8082/api/templates/vlan/apply \
  -d '{"device_id": "switch-access-01", "params": {"vlan_id": "100", "vlan_name": "IoT"}}'
# → {"diff": "...", "dry_run_ok": true, "session_token": "xxx"}

# 4. (REVISAR EL DIFF)

# 5. Confirmar
curl -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -X POST http://localhost:8082/api/templates/vlan/apply/confirm \
  -d '{"session_token": "xxx"}'
# → {"success": true, "committed": true}
```

### Caso 3: Ejecución de comandos — Diagnóstico

```bash
# Comando show desde el dashboard o curl
curl -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -X POST http://localhost:8082/api/command/arista-core-01 \
  -d '{"commands": ["show version", "show lldp neighbors"]}'
# → Resultado de ambos comandos
```

### Caso 4: Backup y rollback

```bash
# Backup manual
curl -H "Authorization: Bearer <token>" \
  -X POST http://localhost:8082/api/config/cisco-core-01/backup
# → {"file": "backups/cisco-core-01_20260609.cfg", "size": 559}

# Rollback al último backup
curl -H "Authorization: Bearer <token>" \
  -X POST http://localhost:8082/api/config/cisco-core-01/rollback
# → {"rolled_back": true}
```

### Caso 5: Operación en lote (múltiples dispositivos)

```bash
# Facts de todos los dispositivos en paralelo (Nornir)
curl -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -X POST http://localhost:8082/api/bulk/facts \
  -d '["cisco-core-01", "arista-core-01", "switch-access-01"]'
# → Resultados en paralelo (~3s en vez de ~15s secuencial)
```

---

## 12. CICLO DE CAMBIOS

El flujo completo para hacer un cambio en la red sigue este proceso, alineado con ITIL/Change Management:

```
┌─────────────────────────────────────────────────────────────┐
│              CICLO DE CAMBIO CONTROLADO                      │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  1. IDENTIFICAR NECESIDAD                                    │
│     "Necesito crear VLAN 100 en el switch acceso"           │
│                                                              │
│  2. SELECCIONAR TEMPLATE                                     │
│     GET /api/templates → vlan                                │
│     POST /api/templates/vlan/render → vista previa           │
│                                                              │
│  3. DRY-RUN (sin riesgo)                                     │
│     POST /api/templates/vlan/apply → diff                   │
│     → Esto NO modifica el dispositivo                       │
│     → Solo muestra qué cambiaría                             │
│                                                              │
│  4. REVISIÓN HUMANA                                          │
│     El operador/admin revisa el diff                         │
│     ¿El cambio es correcto? → Sí → Continuar                 │
│     ¿El cambio es incorrecto? → Descartar                    │
│                                                              │
│  5. BACKUP AUTOMÁTICO                                        │
│     Antes de aplicar, se guarda la config actual             │
│     → backups/dispositivo_YYYYMMDD_HHMMSS.cfg               │
│                                                              │
│  6. APLICAR CAMBIO                                           │
│     POST /api/templates/vlan/apply/confirm                  │
│     → commit_config() en el dispositivo                     │
│                                                              │
│  7. VERIFICACIÓN POST-CAMBIO                                 │
│     GET /api/facts/{id} → ¿Los facts cambiaron?             │
│     GET /api/config/{id}/raw → ¿La config se actualizó?     │
│                                                              │
│  8. REGISTRO DE AUDITORÍA                                    │
│     Todo queda en audit.jsonl                                │
│     GET /api/audit → "admin applied vlan template to         │
│                       switch-access-01 at 2026-06-09..."     │
│                                                              │
│  9. ROLLBACK (SI FALLA)                                      │
│     POST /api/config/{id}/rollback                           │
│     → Restaura backup pre-cambio                             │
│     → Dispositivo vuelve al estado anterior                  │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**Principios que garantiza este flujo:**

1. **Nada se aplica sin revisión humana** — siempre dry-run primero
2. **Nada se aplica sin backup** — backup automático antes de cada commit
3. **Nada se aplica sin auditoría** — todas las operaciones quedan registradas
4. **Todo cambio es reversible** — rollback disponible siempre
5. **Solo personal autorizado** — RBAC garantiza que solo admin haga cambios

---

## 13. DIAGRAMA DE ARQUITECTURA

### 13.1 Vista general

```
                    ┌──────────────────┐
                    │   INTERNET       │
                    └────────┬─────────┘
                             │
                    ┌────────┴─────────┐
                    │   BALANCEADOR    │
                    │   (futuro nginx) │
                    └────────┬─────────┘
                             │
          ┌──────────────────┼──────────────────┐
          │                  │                  │
    ┌─────┴─────┐    ┌──────┴──────┐    ┌──────┴──────┐
    │ DASHBOARD │    │  SWAGGER UI │    │   GRAFANA   │
    │  WEB UI   │    │   /docs     │    │  :3001      │
    └─────┬─────┘    └──────┬──────┘    └──────┬──────┘
          │                 │                  │
          └─────────────────┼──────────────────┘
                            │
                    ┌───────┴────────┐
                    │   FastAPI API   │
                    │    :8082        │
                    └───────┬────────┘
                            │
          ┌─────────────────┼──────────────────┐
          │                 │                  │
    ┌─────┴─────┐    ┌──────┴──────┐    ┌──────┴──────┐
    │  NAPALM   │    │   Nornir    │    │   NetBox    │
    │  (SSH)    │    │  (paralelo) │    │   :8000     │
    └─────┬─────┘    └─────────────┘    └─────────────┘
          │
    ┌─────┴─────┐    ┌───────────┐    ┌───────────┐
    │  Cisco    │    │  Arista   │    │  MikroTik │
    │  :2222    │    │  :443     │    │  :8728    │
    └───────────┘    └───────────┘    └───────────┘

    ┌──────────────────────────────────────────┐
    │  PROMETHEUS ←── GRAFANA ──→ LOKI        │
    │  :9090            :3001       :3100      │
    └──────────────────────────────────────────┘
```

### 13.2 Resumen visual

```
        USUARIO
           │
           ▼
    ┌──────────────┐    ┌──────────────┐
    │  DASHBOARD   │    │   SWAGGER    │
    │  :8082       │    │  :8082/docs  │
    └──────┬───────┘    └──────┬───────┘
           │                   │
           └────────┬──────────┘
                    │
              ┌─────▼─────┐
              │  FastAPI  │ ←── JWT Auth + RBAC + Rate Limit
              └─────┬─────┘
                    │
         ┌──────────┼──────────┐
         │          │          │
    ┌────▼────┐ ┌───▼────┐ ┌──▼──────┐
    │ NAPALM  │ │ Nornir │ │ NetBox  │
    │ .:.8683 │ │ .:.p2p │ │ :8000   │
    └────┬────┘ └────────┘ └─────────┘
         │
    ┌────┴──────────────────┐
    │  SSH/eAPI/API         │
    │  [Cisco] [Arista]     │
    │  [Juniper] [MikroTik] │
    └───────────────────────┘

    ┌────────────────────────────┐
    │ Prometheus ←→ Grafana     │
    │ Loki ← audit.jsonl        │
    └────────────────────────────┘
```

---

## Fin del documento

NetPulse v1.0 — Junio 2026
