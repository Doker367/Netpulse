UNIVERSIDAD AUTÓNOMA DE CHIAPAS (UNACH)
FACULTAD DE INGENIERÍA

INFORME PARCIAL DE PRÁCTICAS PROFESIONALES


NETPULSE — PLATAFORMA OPEN SOURCE PARA LA GESTIÓN
CENTRALIZADA DE INFRAESTRUCTURA DE RED MULTIVENDOR
EN CENTRO DE DATOS


Nombre del estudiante: [NOMBRE COMPLETO]
Matrícula: [MATRÍCULA]
Carrera: Ingeniería en Sistemas Computacionales / Redes
Periodo: [FECHA INICIO] – [FECHA FIN]
Unidad Académica: Facultad de Ingeniería
Docente Asesor: [NOMBRE DOCENTE ASESOR]
Asesor Empresarial: [NOMBRE ASESOR EMPRESARIAL]

Lugar y fecha: [CIUDAD], [FECHA DE ENTREGA]

---

INTRODUCCIÓN

El presente informe parcial tiene como propósito documentar el avance alcanzado
durante la primera mitad del periodo de prácticas profesionales en el desarrollo del
proyecto NetPulse: una plataforma Open Source diseñada para unificar la gestión y
administración de dispositivos de red en centros de datos.

Hasta este momento se ha completado la arquitectura base del sistema, implementando
una API REST con FastAPI, integración con NAPALM para automatización multi-vendor,
un sistema de autenticación con roles (RBAC), monitoreo en tiempo real mediante
Prometheus y Grafana, y un dashboard web para la gestión visual de dispositivos de red.

Se reporta un avance del 50% del proyecto total, con 4 de las 8 fases del plan de trabajo
completadas exitosamente. El laboratorio de pruebas cuenta con 5 dispositivos de red
funcionales (4 routers Arista vEOS, 1 router MikroTik) que responden a comandos vía
NAPALM.

---

I. DATOS GENERALES DE LA PRÁCTICA

Nombre de la empresa/institución: [NOMBRE EMPRESA / INSTITUCIÓN]
Periodo reportado: [FECHA INICIO] al [FECHA ACTUAL]
Horas acumuladas al momento: 120 horas (aproximadamente)
Total de horas del proyecto: 240 horas

---

II. DESARROLLO DE ACTIVIDADES (AVANCE)

Objetivos particulares alcanzados:

1. Investigación y selección de tecnologías Open Source para automatización de redes
   Cumplido al 100%. Se investigaron y seleccionaron NAPALM, Nornir, Netmiko,
   FastAPI, Prometheus, Grafana y NetBox como stack tecnológico.

2. Diseño de arquitectura con seguridad, escalabilidad y disponibilidad
   Cumplido al 100%. Se diseñó un plan maestro de 8 fases documentado en
   docs/plans/2026-06-05-netpulse-master-plan.md.

3. Implementación de API REST con FastAPI + NAPALM (Fase 1)
   Cumplido al 100%. Se implementaron 14 endpoints REST funcionales para gestión
   de dispositivos: facts, interfaces, configuración, backup, BGP, ping, comandos.

4. Sistema de autenticación JWT + RBAC para seguridad del datacenter (Fase 2)
   Cumplido al 100%. Se implementaron 3 roles (admin, operator, viewer) con control
   de acceso granular por endpoint.

5. Integración de NetBox como inventario DCIM (Fase 3)
   Cumplido al 80%. NetBox desplegado, endpoints creados. Pendiente creación de
   token definitivo.

6. Monitoreo con Prometheus + Grafana (Fase 4)
   Cumplido al 100%. Dashboards de monitoreo con métricas de dispositivos en
   tiempo real: requests/segundo, latencia API, dispositivos UP/DOWN.

Actividades realizadas (Descripción detallada):

Actividad 1 — Investigación y selección de tecnologías (10 horas)
Se realizó una investigación exhaustiva de tecnologías Open Source para
automatización de redes, seleccionando NAPALM como capa de abstracción
multi-vendor, Nornir como orquestador, FastAPI como framework web,
Prometheus+Grafana para monitoreo, y NetBox como inventario DCIM.
Herramientas: Python, Docker, documentación oficial de NAPALM, Google Scholar.

Actividad 2 — Despliegue de laboratorio de pruebas (15 horas)
Se creó un entorno de laboratorio con 5 dispositivos de red virtualizados mediante
Docker: 4 routers Arista vEOS y 1 router MikroTik RouterOS. Se configuraron
conexiones SSH y API para cada dispositivo, verificando conectividad con NAPALM.
Herramientas: Docker, Docker Compose, FRRouting, Arista vEOS, MikroTik RouterOS.

Actividad 3 — Prueba de Concepto (PoC) NAPALM (8 horas)
Se desarrolló un script de verificación multi-vendor (lab_check.py) que prueba
conectividad contra los 5 dispositivos usando diferentes drivers de NAPALM
(ios, eos, ros). Se documentaron resultados y lecciones aprendidas.
Herramientas: Python 3.14, NAPALM 5.1.0, napalm-ros, librouteros.

Actividad 4 — Implementación de API REST (20 horas)
Se implementó la API REST completa con FastAPI, incluyendo 14 endpoints para
gestión de dispositivos. Se integró Nornir para operaciones en paralelo (bulk).
Se documentó en docs/fase1-core-api.md.
Herramientas: FastAPI, Pydantic, Nornir, uvicorn.

Actividad 5 — Sistema de seguridad y RBAC (15 horas)
Se implementó autenticación JWT, control de acceso basado en roles (admin,
operator, viewer), encriptación de credenciales con Fernet, y registro de auditoría
inmutable en JSONL. Se documentó en docs/fase2-security.md.
Herramientas: PyJWT, bcrypt, cryptography (Fernet), Python-Jose.

Actividad 6 — Despliegue de NetBox (10 horas)
Se desplegó NetBox como sistema DCIM para inventario de dispositivos, con
PostgreSQL y Redis. Se crearon endpoints de sincronización bidireccional con
el inventario local. NetBox accesible en http://localhost:8000.
Herramientas: NetBox 4.6, PostgreSQL 15, Redis 7, Docker.

Actividad 7 — Monitoreo y Observabilidad (12 horas)
Se configuraron Prometheus y Grafana para monitoreo en tiempo real de la API
y los dispositivos de red. Se implementaron métricas personalizadas y un dashboard
con 6 paneles mostrando estado de dispositivos, latencia y operaciones NAPALM.
Herramientas: Prometheus, Grafana, prometheus_client (Python).

Actividad 8 — Dashboard web de gestión (18 horas)
Se desarrolló una interfaz web completa (HTML5/CSS3/JavaScript vanilla) para la
gestión visual de todos los dispositivos desde un solo panel. Incluye: login con JWT,
gráfico de contribuciones estilo GitHub, ejecución de comandos, aplicación de
templates de configuración, compliance, backups programados, exportación, webhooks,
y administración de usuarios con control de roles.
Herramientas: HTML5, CSS3, JavaScript (vanilla), diseño Linear-inspired.

Actividad 9 — Optimización de drivers y compatibilidad (12 horas)
Se resolvieron problemas de compatibilidad con MikroTik RouterOS 7.22 (fallback
librouteros), Arista vEOS (SSL legacy ciphers en Python 3.14), y se implementó
ejecución de comandos vía API librouteros para dispositivos ROS.
Herramientas: librouteros, paramiko, ssl, subprocess.

Problemáticas detectadas:

1. Incompatibilidad Python 3.14 con drivers comunitarios NAPALM
   Problema: Python 3.14 eliminó pkg_resources, causando fallos en napalm-ros y
   napalm-vyos. Solución: Se implementó fallback con librouteros para MikroTik y se
   mantuvo compatibilidad con los drivers core (ios, eos, junos, nxos).

2. SSL en Arista vEOS con Python 3.14
   Problema: Arista vEOS usa ciphers legacy (AES256-SHA) rechazados por Python 3.14.
   Solución: Se implementó monkey-patching del SSLContext en napalm_svc.py para
   permitir conexiones HTTPS con ciphers legacy.

3. Docker bridge network inconsistente
   Problema: En múltiples ocasiones, Docker perdió la red bridge, impidiendo la
   creación de contenedores. Solución: Se migró a network_mode: host para servicios
   de monitoreo y se documentaron procedimientos de reconstrucción.

4. NAPALM ROS driver incompatible con RouterOS 7.22
   Problema: El comando system routerboard print fue removido en ROS 7.22.
   Solución: Se implementó fallback automático a librouteros que provee datos
   equivalentes vía la API nativa de MikroTik.

---

III. AVANCE DE PRODUCTOS O RESULTADOS

Producto principal: Plataforma NetPulse v1.0

Entregables completados:

1. Código fuente completo (Python 3.14 + HTML/CSS/JS)
   Ubicación: /data/doker/GreenAlgorithm/NetPulse/
   Estructura modular: app/routers/ (7 routers), app/services/ (5 servicios),
   app/middleware/ (5 middlewares), app/models/ (schemas Pydantic)

2. API REST documentada (Swagger)
   URL: http://localhost:8082/docs
   Endpoints: 19 rutas enterprise (devices, facts, config, bulk, templates,
   compliance, scheduler, export, notifications, admin, groups)

3. Dashboard web de gestión
   URL: http://localhost:8082
   Funcionalidades: Login JWT, gestión de dispositivos, comandos, templates,
   compliance, backups, exportación, webhooks, administración de usuarios

4. Dashboards de monitoreo (Grafana)
   URL: http://localhost:3001/d/netpulse-devices
   Paneles: Requests/segundo, Dispositivos UP, Latencia API, Operaciones NAPALM,
   Estado de Dispositivos

5. Laboratorio de pruebas
   5 dispositivos funcionales: 4x Arista vEOS 4.27.3F, 1x MikroTik RouterOS 7.22
   Topología: Core (2 routers), Edge (1 router, 1 MikroTik), Access (1 switch)

6. Documentación técnica
   - Plan maestro: docs/plans/2026-06-05-netpulse-master-plan.md
   - Fase 0 (PoC): docs/fase0-poc-resultados.md
   - Fase 1 (Core API): docs/fase1-core-api.md
   - Fase 3 (NetBox): docs/fase3-netbox.md
   - Documentación completa: docs/documentacion-completa.md
   - Resumen ejecutivo: docs/resumen-ejecutivo.md

---

IV. CONCLUSIONES PARCIALES

Reflexión sobre lo aprendido:

Durante esta primera etapa de prácticas profesionales, se adquirieron conocimientos
significativos en automatización de redes utilizando Python y tecnologías Open Source.
La implementación de NAPALM como capa de abstracción multi-vendor permitió
comprender cómo los grandes proveedores de servicios gestionan infraestructura
heterogénea desde un solo punto de control.

El desarrollo de una API REST con FastAPI reforzó conceptos de arquitectura de
software, diseño de APIs, y patrones de seguridad como JWT y RBAC. La integración
con Prometheus y Grafana proporcionó experiencia práctica en monitoreo de
infraestructura, una habilidad altamente demandada en operaciones de TI.

Dificultades generales y cómo se superaron:

- La compatibilidad entre versiones de Python (3.14) y librerías de red (NAPALM)
  fue el mayor desafío técnico. Se resolvió mediante investigación de issues en
  GitHub de los proyectos y la implementación de workarounds.

- La inestabilidad de Docker en el entorno de desarrollo requirió reconstruir el
  laboratorio en múltiples ocasiones, lo que reforzó la importancia de la
  automatización y la infraestructura como código.

- La ausencia de imágenes gratuitas de dispositivos de red (Cisco, Juniper)
  limitó el alcance del laboratorio. Se optó por Arista vEOS y MikroTik como
  alternativas viables que demuestran la funcionalidad multi-vendor.

Expectativas para la segunda mitad:

- Implementar las Fases 5-8 del plan maestro: IA con Ollama local, Wazuh para
  detección de intrusiones, alta disponibilidad, y CI/CD.
- Migrar el inventario de YAML a NetBox como fuente única de verdad.
- Realizar pruebas de rendimiento y seguridad en un entorno más cercano a
  producción.
- Completar la documentación para entrega final.

---

V. ANEXOS

Anexo 1 — Plan de Trabajo de Prácticas Profesionales
(Ver documento: plan_trabajo_practicas_profesionales.docx)

Anexo 2 — Plan Maestro del Proyecto
(Ver: docs/plans/2026-06-05-netpulse-master-plan.md)

Anexo 3 — Capturas de pantalla del sistema en funcionamiento
- Dashboard principal con 5 dispositivos: http://localhost:8082
- Grafana con métricas en tiempo real: http://localhost:3001
- API Swagger documentada: http://localhost:8082/docs
- Panel de administración de usuarios
- Terminal de comandos en MikroTik mostrando /system/resource/print

Anexo 4 — Tecnologías utilizadas
- Backend: Python 3.14, FastAPI, NAPALM 5.1.0, Nornir, Pydantic
- Seguridad: JWT, bcrypt, Fernet, RBAC
- Monitoreo: Prometheus, Grafana
- Infraestructura: Docker, PostgreSQL, Redis
- Frontend: HTML5, CSS3, JavaScript (vanilla)
- Dispositivos: Arista vEOS 4.27.3F, MikroTik RouterOS 7.22
