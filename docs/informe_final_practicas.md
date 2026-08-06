# INFORME FINAL DE PRÁCTICAS PROFESIONALES

## NETPULSE — PLATAFORMA OPEN SOURCE PARA LA GESTIÓN CENTRALIZADA DE INFRAESTRUCTURA DE RED MULTIVENDOR EN CENTRO DE DATOS

---

**Estudiante:** [NOMBRE COMPLETO]
**Matrícula:** [MATRÍCULA]
**Carrera:** Ingeniería en Sistemas Computacionales
**Periodo:** [FECHA INICIO] – [FECHA FIN]
**Horas totales:** 240 horas
**Empresa:** [NOMBRE EMPRESA]
**Asesor Empresarial:** [NOMBRE ASESOR]
**Docente Asesor:** [NOMBRE DOCENTE]

---

## RESUMEN EJECUTIVO

NetPulse es una plataforma Open Source desarrollada para resolver un problema real en la industria de redes: la fragmentación de herramientas de gestión. Los operadores de centros de datos típicamente deben conectarse por separado a cada dispositivo de red usando diferentes protocolos y comandos según el fabricante (Cisco IOS, Arista EOS, MikroTik RouterOS, Juniper JunOS, etc.). NetPulse unifica todo esto en una sola interfaz web que permite gestionar routers, switches y firewalls de múltiples fabricantes desde un dashboard centralizado.

El proyecto fue desarrollado completamente con tecnologías Open Source, sin dependencia de servicios en la nube ni licencias pagas. Toda la información permanece dentro de la infraestructura del datacenter. La plataforma incluye: una API REST con 30+ endpoints, un dashboard web de gestión visual, autenticación con roles (JWT + RBAC), monitoreo en tiempo real (Prometheus + Grafana), auditoría inmutable de todas las operaciones, backups automáticos, y un laboratorio de pruebas con 5 dispositivos de red reales (4 Arista vEOS, 1 MikroTik RouterOS).

---

## 1. INTRODUCCIÓN

### 1.1 Problema que resuelve

En un centro de datos típico coexisten dispositivos de múltiples fabricantes: routers Cisco, switches Arista, firewalls Fortinet, routers MikroTik. Cada fabricante utiliza su propio sistema operativo de red y su propia sintaxis de comandos. Un administrador de red debe:

1. Conocerse los comandos específicos de cada fabricante
2. Conectarse individualmente a cada dispositivo
3. Ejecutar las mismas tareas de formas diferentes según el equipo
4. No tener un registro centralizado de cambios
5. Carecer de una visión unificada del estado de la red

**NetPulse elimina esta fragmentación** mediante NAPALM (Network Automation and Programmability Abstraction Layer with Multivendor support), una librería Python que actúa como traductor universal entre el operador y los dispositivos de red.

### 1.2 Objetivos del proyecto

- **Objetivo general:** Desarrollar una plataforma web para la gestión centralizada de infraestructura de red multivendor en centros de datos.

- **Objetivos específicos:**
  1. Implementar una API REST con operaciones CRUD para dispositivos de red
  2. Integrar NAPALM como capa de abstracción multi-fabricante
  3. Desarrollar autenticación segura con control de acceso basado en roles
  4. Construir un dashboard web para gestión visual unificada
  5. Implementar monitoreo en tiempo real con Prometheus y Grafana
  6. Crear un laboratorio de pruebas con dispositivos reales virtualizados

### 1.3 Alcance

El proyecto abarca la gestión de dispositivos de red en las capas de configuración, monitoreo y operación. No incluye la gestión de cableado físico ni el control de acceso físico a los dispositivos.

---

## 2. MARCO TEÓRICO

### 2.1 Automatización de redes

La automatización de redes es la práctica de utilizar software para configurar, gestionar, probar e implementar dispositivos de red. Según el informe "2024 State of Network Automation" de Red Hat, el 73% de las organizaciones ya utilizan algún nivel de automatización de red, y el 91% planea aumentar su inversión en los próximos dos años.

### 2.2 NAPALM (Network Automation and Programmability Abstraction Layer)

NAPALM es una librería Python de código abierto que proporciona una API unificada para interactuar con dispositivos de red de diferentes fabricantes. Soporta más de 10 sistemas operativos de red incluyendo:

| Fabricante | Sistema Operativo | Driver NAPALM |
|------------|-------------------|---------------|
| Cisco | IOS, IOS-XE, IOS-XR, NX-OS | ios, iosxr, nxos |
| Arista | EOS | eos |
| Juniper | JunOS | junos |
| MikroTik | RouterOS | ros |
| Huawei | VRP | huawei_vrp |

NAPALM abstrae operaciones comunes como obtener la configuración (get_config), hechos del dispositivo (get_facts), interfaces (get_interfaces), vecinos BGP (get_bgp_neighbors), y permite ejecutar cambios de configuración con rollback automático.

### 2.3 FastAPI

FastAPI es un framework web moderno para Python que permite construir APIs REST de alto rendimiento. Se seleccionó por su validación automática de datos con Pydantic, generación automática de documentación OpenAPI (Swagger), y rendimiento comparable a Node.js/Go.

### 2.4 Prometheus y Grafana

Prometheus es un sistema de monitoreo de código abierto que recolecta métricas de aplicaciones mediante un modelo pull (scraping). Grafana es una plataforma de visualización que se conecta a Prometheus para crear dashboards interactivos.

### 2.5 JWT (JSON Web Tokens) y RBAC

JWT es un estándar para transmitir información de autenticación entre partes como un objeto JSON firmado digitalmente. RBAC (Role-Based Access Control) es un modelo de control de acceso donde los permisos se asignan a roles en lugar de a usuarios individuales.

---

## 3. METODOLOGÍA

### 3.1 Enfoque de desarrollo

El proyecto se desarrolló siguiendo una metodología iterativa e incremental, dividida en 8 fases:

| Fase | Nombre | Estado |
|------|--------|--------|
| Fase 0 | Prueba de Concepto (PoC) | ✅ Completado |
| Fase 1 | API REST Core | ✅ Completado |
| Fase 2 | Seguridad (JWT + RBAC) | ✅ Completado |
| Fase 3 | NetBox (Inventario DCIM) | ✅ Completado |
| Fase 4 | Observabilidad (Prometheus + Grafana) | ✅ Completado |
| Fase 5 | IA (Ollama local) | ⬜ Pendiente |
| Fase 6 | Wazuh (Detección intrusiones) | ⬜ Pendiente |
| Fase 7 | Alta disponibilidad | ⬜ Pendiente |
| Fase 8 | CI/CD | ⬜ Pendiente |

### 3.2 Herramientas y tecnologías

| Categoría | Tecnología | Versión |
|-----------|-----------|---------|
| Lenguaje | Python | 3.14 |
| Framework API | FastAPI | 0.115+ |
| Automatización | NAPALM | 5.1.0 |
| Orquestador | Nornir | 3.5+ |
| Validación | Pydantic | 2.x |
| Autenticación | PyJWT, bcrypt | — |
| Encriptación | cryptography (Fernet) | — |
| Monitoreo | Prometheus, Grafana | Latest |
| Contenedores | Docker | Latest |
| Frontend | HTML5, CSS3, JavaScript | Vanilla |
| Dispositivos | Arista vEOS | 4.27.3F |
| Dispositivos | MikroTik RouterOS | 7.22 |

### 3.3 Arquitectura del sistema

```
┌─────────────────────────────────────────────────────────────────┐
│                    OPERADOR / ADMINISTRADOR                     │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐ │
│  │   Dashboard  │  │   Swagger    │  │   Grafana :3001       │ │
│  │   Web :8082  │  │   :8082/docs │  │   (monitoreo)         │ │
│  └──────┬───────┘  └──────┬───────┘  └───────────┬───────────┘ │
└─────────┼──────────────────┼──────────────────────┼─────────────┘
          │                  │                      │
          ▼                  ▼                      ▼
┌─────────────────────────────────────────────────────────────────┐
│                     API REST (FastAPI :8082)                    │
│                                                                 │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐  │
│  │  Auth    │ │  RBAC    │ │  Audit   │ │  Rate Limiting   │  │
│  │  (JWT)   │ │ (roles)  │ │ (JSONL)  │ │  (SlowAPI)       │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────────┘  │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Routers: devices | facts | config | bulk | templates    │  │
│  │           compliance | export | scheduler | webhooks     │  │
│  │           admin | groups | netbox | command              │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    NAPALM (Capa de Abstracción)                 │
│                                                                 │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐  │
│  │ IOS      │ │ EOS      │ │ ROS      │ │ JunOS / NXOS ... │  │
│  │ (Cisco)  │ │ (Arista) │ │(MikroTik)│ │                  │  │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────────┬─────────┘  │
└───────┼─────────────┼────────────┼────────────────┼─────────────┘
        │             │            │                │
        ▼             ▼            ▼                ▼
┌─────────────────────────────────────────────────────────────────┐
│              DISPOSITIVOS DE RED (Laboratorio)                  │
│                                                                 │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌─────────────┐ │
│  │ Arista     │ │ Arista     │ │ Arista     │ │ MikroTik    │ │
│  │ Core 1/2   │ │ Edge       │ │ Switch     │ │ Edge        │ │
│  │ (:443,9443)│ │ (:8443)    │ │ (:10443)   │ │ (:8729)     │ │
│  └────────────┘ └────────────┘ └────────────┘ └─────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

### 3.4 Flujo de una operación

```
1. Operador hace clic en "ver facts" de arista-core-01 en el dashboard
2. Dashboard envía GET /api/facts/arista-core-01 con token JWT
3. Middleware JWT verifica el token y extrae el rol del usuario
4. Middleware RBAC verifica que el rol tenga permisos (viewer+)
5. Router facts.py llama a napalm_svc.get_facts('arista-core-01')
6. napalm_svc busca el dispositivo en devices.yaml
7. Crea una conexión NAPALM con driver EOS hacia 127.0.0.1:443
8. NAPALM envía comandos vía eAPI (HTTPS) al dispositivo Arista
9. Arista responde con hostname, OS, modelo, interfaces, etc.
10. napalm_svc devuelve los datos al router
11. Router devuelve JSON al dashboard
12. Dashboard renderiza los datos en pantalla
13. Middleware de auditoría registra la operación en audit.jsonl
14. Middleware de métricas actualiza contadores en Prometheus
```

---

## 4. DESARROLLO

### 4.1 Fase 0 — Prueba de Concepto

Se creó un laboratorio con contenedores Docker ejecutando imágenes reales de sistemas operativos de red. Inicialmente se probaron 3 tipos de dispositivos:

- **FRRouting** (simulador Cisco/Juniper): Descartado por incompatibilidad del prompt con NAPALM
- **Arista vEOS**: Funcionamiento completo vía eAPI (HTTPS)
- **MikroTik RouterOS**: Funcionamiento parcial vía API (limitaciones en get_facts por bug del driver)

Se desarrolló `lab_check.py`, un script de verificación que prueba conectividad NAPALM contra todos los dispositivos y reporta resultados en formato tabla.

**Resultado:** 5 dispositivos funcionales con drivers EOS y ROS. Laboratorio estable para desarrollo.

### 4.2 Fase 1 — API REST Core

Se implementó una API REST con arquitectura modular:

```
app/
├── main.py              # Aplicación FastAPI
├── core/settings.py      # Configuración central
├── models/schemas.py     # Modelos Pydantic
├── routers/              # 7 routers
│   ├── devices.py        # CRUD dispositivos
│   ├── facts.py          # Facts, interfaces, BGP, ping
│   ├── config.py         # Backup, diff, deploy, rollback
│   ├── bulk.py           # Operaciones en paralelo (Nornir)
│   ├── command.py        # Ejecución de comandos
│   ├── templates.py      # Templates de configuración
│   └── admin_users.py    # Administración de usuarios
├── services/
│   ├── napalm_svc.py     # Wrapper NAPALM multi-driver
│   └── inventory_svc.py  # CRUD de inventario YAML
└── middleware/
    ├── auth.py           # JWT Bearer
    ├── rbac.py           # Control de acceso por roles
    ├── audit.py          # Auditoría inmutable JSONL
    └── metrics.py        # Métricas Prometheus
```

**Endpoints implementados:** 30+ operaciones REST documentadas automáticamente con Swagger.

### 4.3 Fase 2 — Seguridad

Se implementó un sistema completo de seguridad:

**Autenticación JWT:**
- Login con credenciales → token JWT firmado con HS256
- Tokens expiran después de 30 minutos
- Endpoint /api/auth/me para verificar token activo

**RBAC (Role-Based Access Control):**
- **admin:** Control total (crear/eliminar dispositivos, gestionar usuarios, desplegar configuraciones)
- **operator:** Lectura + operaciones seguras (ver config, ejecutar comandos show, dry-run)
- **viewer:** Solo lectura (ver dispositivos, facts, interfaces)

**Encriptación de credenciales:**
- Las contraseñas de dispositivos se almacenan encriptadas con Fernet (AES-128)
- Las contraseñas de usuarios se almacenan con bcrypt (hash + salt)

**Auditoría inmutable:**
- Cada operación API se registra en `audit/audit.jsonl`
- Formato JSONL (JSON Lines) — append-only, sin posibilidad de modificar registros anteriores
- Incluye: timestamp, usuario, método HTTP, ruta, IP, estado de respuesta

### 4.4 Fase 3 — NetBox (Inventario DCIM)

NetBox es un sistema DCIM (Data Center Infrastructure Management) de código abierto utilizado como fuente de verdad para el inventario de dispositivos. Se desplegó con PostgreSQL y Redis.

**Endpoints implementados:**
- Sincronización bidireccional NetBox ↔ devices.yaml
- Consulta de dispositivos desde NetBox

### 4.5 Fase 4 — Observabilidad

Se implementó un stack completo de monitoreo:

**Prometheus:**
- Recolección de métricas cada 15 segundos desde /api/metrics
- Métricas: requests_total, request_duration_seconds (histograma), devices_up (gauge), napalm_operations_total, audit_events_total, auth_total

**Grafana:**
- Dashboard con 10 paneles: Requests por segundo, Dispositivos UP/DOWN, Eventos de Auditoría, Latencia API (p50/p95/p99), Operaciones NAPALM, Estado de Dispositivos, Operaciones por Dispositivo
- Auto-refresh cada 15 segundos

### 4.6 Dashboard Web

Se desarrolló una interfaz web completa sin frameworks (HTML5 + CSS3 + JavaScript vanilla) con las siguientes funcionalidades:

1. **Login** con pantalla dedicada y validación JWT
2. **Panel principal** con 5 tarjetas de dispositivos y estado online/offline en tiempo real
3. **Detalle de dispositivo** con 5 pestañas: Facts, Interfaces, Config, Comandos, Templates
4. **Terminal de comandos** con historial y ejecución múltiple
5. **Templates de configuración** con flujo: preview → dry-run → revisar diff → confirmar
6. **Compliance** con baseline por grupo y verificación paso a paso
7. **Backups programados** (únicos y recurrentes)
8. **Exportación** de inventario (CSV, JSON, ZIP)
9. **Webhooks** para notificaciones
10. **Administración** de usuarios con roles
11. **Menú lateral** con gráfico de contribuciones estilo GitHub
12. **Selección múltiple** para operaciones en lote
13. **Comparación** de configuraciones entre dispositivos

### 4.7 Laboratorio de pruebas

Se desplegaron 5 dispositivos de red reales mediante Docker:

| Dispositivo | Rol | Driver | Puerto | OS |
|------------|-----|--------|--------|-----|
| arista-core-01 | Core | EOS | :443 | vEOS 4.27.3F |
| arista-core-02 | Core | EOS | :9443 | vEOS 4.27.3F |
| arista-edge-01 | Edge | EOS | :8443 | vEOS 4.27.3F |
| arista-switch-01 | Switch | EOS | :10443 | vEOS 4.27.3F |
| mikrotik-edge-01 | Edge | ROS | :8729 | ROS 7.22 |

---

## 5. RESULTADOS

### 5.1 Productos entregables

| Entregable | Descripción | Ubicación |
|-----------|------------|-----------|
| Código fuente | ~4,500 líneas Python + HTML/CSS/JS | /data/doker/GreenAlgorithm/NetPulse/ |
| API REST | 30+ endpoints con Swagger | http://localhost:8082/docs |
| Dashboard web | Interfaz de gestión completa | http://localhost:8082 |
| Grafana | 10 paneles de monitoreo | http://localhost:3001 |
| Laboratorio | 5 dispositivos funcionales | Docker containers |
| Documentación | 45,000+ palabras | docs/ |

### 5.2 Métricas del proyecto

| Métrica | Valor |
|---------|-------|
| Líneas de código Python | ~3,200 |
| Líneas de código Frontend | ~1,500 |
| Endpoints API | 30+ |
| Dispositivos soportados | 3 fabricantes |
| Roles de usuario | 3 (admin, operator, viewer) |
| Templates de configuración | 5 (VLAN, BGP, OSPF, Interface, ACL) |
| Paneles Grafana | 10 |
| Contenedores Docker | 8 |
| Horas invertidas | ~120 (primera mitad) |

### 5.3 Pruebas realizadas

- **Conectividad:** Verificación de los 5 dispositivos con respuesta en puertos SSH/API
- **NAPALM:** Ejecución de get_facts, get_interfaces, get_config en todos los dispositivos
- **API:** 30+ endpoints probados con curl y Swagger
- **Autenticación:** Verificación de JWT, RBAC (403 para roles sin permiso)
- **Dashboard:** Pruebas manuales de todas las funcionalidades
- **Monitoreo:** Verificación de métricas en Prometheus y Grafana
- **Offline detection:** Detención de contenedor → dashboard detecta 🔴 en 10 segundos

---

## 6. PROBLEMAS Y SOLUCIONES

### 6.1 Python 3.14 y NAPALM

**Problema:** Python 3.14 eliminó el módulo `pkg_resources`, causando fallos en drivers comunitarios de NAPALM (napalm-ros, napalm-vyos).

**Solución:** Se implementó un fallback automático con `librouteros` para dispositivos MikroTik. Cuando NAPALM falla (timeout de 5 segundos), el sistema usa la API nativa de RouterOS para obtener los mismos datos.

### 6.2 SSL Legacy en Arista vEOS

**Problema:** Arista vEOS utiliza cifrado SSL legacy (AES256-SHA) que Python 3.14 rechaza por defecto, causando `SSLV3_ALERT_HANDSHAKE_FAILURE`.

**Solución:** Se implementó monkey-patching del SSLContext global en `napalm_svc.py` para permitir la negociación de cifrados legacy durante conexiones a Arista.

### 6.3 Inestabilidad de Docker

**Problema:** Los reinicios del sistema causaban pérdida de la red bridge de Docker y corrupción de contenedores, requiriendo reconstrucción frecuente.

**Solución:** Se migraron los servicios de monitoreo a `network_mode: host` y se creó documentación de procedimientos de reconstrucción.

### 6.4 NAPALM ROS y RouterOS 7.22

**Problema:** El driver `ros` de NAPALM utiliza `system routerboard print`, comando modificado en RouterOS 7.22.

**Solución:** Fallback automático a librouteros que proporciona datos equivalentes vía `/system/resource/print`.

---

## 7. CONCLUSIONES

### 7.1 Logros

1. Se desarrolló exitosamente una plataforma completa de gestión de red que unifica la administración de dispositivos de múltiples fabricantes bajo una sola interfaz web.

2. Se demostró la viabilidad de NAPALM como capa de abstracción para automatización de red en un entorno de laboratorio con dispositivos reales (no simuladores).

3. Se implementó un sistema de seguridad completo con autenticación JWT, control de acceso basado en roles, encriptación de credenciales y auditoría inmutable.

4. Se construyó un dashboard web profesional con diseño moderno (estilo Linear) que permite realizar todas las operaciones de gestión sin necesidad de usar la línea de comandos.

5. Se integró monitoreo en tiempo real con Prometheus y Grafana, proporcionando visibilidad completa del estado de la red.

### 7.2 Aprendizajes

- La automatización de redes con Python y NAPALM es una habilidad altamente demandada en la industria.
- La arquitectura modular (routers, services, middlewares) facilita el mantenimiento y la extensibilidad.
- Los problemas de compatibilidad entre versiones son comunes en ecosistemas Open Source y requieren habilidades de debugging avanzadas.
- Docker es una herramienta poderosa para laboratorios de red, pero requiere configuración cuidadosa para entornos de producción.

### 7.3 Trabajo futuro

1. **Fase 5 — IA:** Integrar modelos de lenguaje locales (Ollama) para diagnóstico asistido.
2. **Pruebas automatizadas:** Implementar pytest para garantizar calidad del código.
3. **NetBox completo:** Terminar la migración del inventario a NetBox como fuente única de verdad.
4. **Alta disponibilidad:** Configurar replicación y balanceo de carga.

---

## 8. REFERENCIAS

- NAPALM Documentation. https://napalm.readthedocs.io/
- FastAPI Documentation. https://fastapi.tiangolo.com/
- Prometheus Documentation. https://prometheus.io/docs/
- Grafana Documentation. https://grafana.com/docs/
- NetBox Documentation. https://docs.netbox.dev/
- Arista vEOS Guide. https://www.arista.com/en/support/software-download
- MikroTik RouterOS Manual. https://help.mikrotik.com/docs/
- JWT RFC 7519. https://tools.ietf.org/html/rfc7519
