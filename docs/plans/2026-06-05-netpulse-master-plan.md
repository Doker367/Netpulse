# Proyecto NetPulse — Plan Maestro de Arquitectura

> **Fecha:** 5 de junio 2026
> **Versión:** 1.0
> **Autor:** Larcad Maestro / Doker
> **Contexto:** Centro de datos — gestión multivendor con IA local

---

## Objetivo General

Diseñar y desarrollar una plataforma **Open Source** para la gestión centralizada, automatización,
observabilidad y análisis inteligente de infraestructura de red multivendor en entornos de centro
de datos.

La plataforma deberá permitir administrar dispositivos de distintos fabricantes mediante una única
interfaz segura, auditable y escalable, eliminando procesos manuales, reduciendo errores operativos
y mejorando la visibilidad de la infraestructura.

---

## 1. Alcance del Proyecto

La solución deberá proporcionar:

1. Inventario centralizado de infraestructura
2. Gestión de dispositivos multivendor
3. Automatización de configuraciones
4. Respaldo automático de configuraciones
5. Rollback automático
6. Auditoría completa de operaciones
7. Gestión segura de credenciales
8. Control de acceso basado en roles (RBAC)
9. Integración con inteligencia artificial local
10. Monitoreo y observabilidad
11. Cumplimiento y validación de configuraciones

---

## 2. Restricciones

### Tecnológicas

- Exclusivamente tecnologías **Open Source**
- **NO** servicios SaaS externos
- **NO** dependencias de pago
- **NO** procesamiento de información sensible fuera del centro de datos
- Toda la información debe permanecer dentro de la infraestructura local

---

## 3. Arquitectura Objetivo

Arquitectura de microservicios desacoplados.

### 3.1 Gestión de Identidad — Keycloak

- MFA
- RBAC
- OAuth2 / OpenID Connect

### 3.2 Inventario — NetBox

Fuente única de verdad para:
- Dispositivos
- Sitios
- VLANs
- Racks
- Direccionamiento IP
- Cableado

### 3.3 Automatización — Nornir (orquestador principal)

Integraciones:
- NAPALM
- Netmiko
- Scrapli

Vendors soportados:
- Cisco (IOS, IOS-XR, NX-OS)
- Juniper (JunOS)
- Arista (EOS)
- Huawei
- MikroTik
- Fortinet
- Palo Alto
- Linux (servidores)

### 3.4 Gestión de Secretos — HashiCorp Vault Community Edition

- Credenciales cifradas (AES-256-GCM)
- Rotación de secretos automática
- Auditoría de accesos
- **PROHIBIDO** almacenar contraseñas en archivos YAML, .env o código fuente

### 3.5 Base de Datos — PostgreSQL

Información almacenada:
- Inventario operacional
- Auditoría inmutable
- Historial de cambios
- Jobs ejecutados
- Reportes

### 3.6 Cache — Redis

- Cache de consultas
- Rate limiting
- Gestión de sesiones

### 3.7 Inteligencia Artificial — Local (Ollama)

Modelos candidatos: Qwen, DeepSeek, Llama

**Restricciones de la IA:**
- ❌ NO puede ejecutar cambios directamente sobre la infraestructura
- ✅ Analizar configuraciones
- ✅ Detectar anomalías
- ✅ Generar recomendaciones
- ✅ Consultar documentación mediante RAG
- ✅ Generar propuestas de configuración
- ✅ Toda modificación requiere **aprobación humana**

### 3.8 Observabilidad

- **Prometheus** — métricas
- **Grafana** — dashboards
- **Loki** — logs centralizados

Monitorear: API, DB, Vault, automatizaciones, infraestructura gestionada

### 3.9 Detección y Respuesta — Wazuh

- Detección de vulnerabilidades
- Monitoreo de integridad de archivos
- Eventos de seguridad
- Alertas operativas

---

## 4. Seguridad

- TLS 1.3 en todas las comunicaciones
- mTLS entre servicios internos
- Segmentación de red (VLAN de gestión dedicada)
- Rate limiting
- Validación estricta de entradas
- Verificación de host keys SSH
- Auditoría inmutable (logs solo INSERT, nunca DELETE/UPDATE)

---

## 5. Flujo Operacional (Change Management)

```
Solicitud de cambio
        ↓
    Validación
        ↓
    Aprobación
        ↓
  Respaldo automático
        ↓
    Despliegue
        ↓
 Verificación posterior
        ↓
Rollback automático si falla
        ↓
  Registro de auditoría
```

**Ninguna operación omite auditoría.**

---

## 6. Ambientes

```
DEV → QA → PREPRODUCCIÓN → PRODUCCIÓN
```

Ningún cambio llega a producción sin validación previa en todos los ambientes anteriores.

---

## 7. Alta Disponibilidad

- API redundante (mínimo 2 instancias)
- PostgreSQL redundante (replicación)
- Vault redundante (cluster)
- Monitoreo redundante
- **Sin puntos únicos de falla**

---

## 8. Entregables Esperados

1. ✅ Plan Maestro de Arquitectura (este documento)
2. ⬜ Diagramas técnicos
3. ⬜ Diseño de base de datos
4. ⬜ Diseño de APIs
5. ⬜ Diseño de seguridad
6. ⬜ Diseño de automatización
7. ⬜ Plan de pruebas
8. ⬜ Plan de despliegue
9. ⬜ Plan de recuperación ante desastres (DRP)
10. ⬜ Roadmap de implementación por fases

---

## 9. Roadmap de Implementación por Fases

### Fase 0 — PoC y Laboratorio (2-3 días)
- Setup del entorno de desarrollo (venv, Docker)
- Simuladores de red (Cisco IOSv, Juniper vJunOS, Arista vEOS)
- Pruebas NAPALM + Nornir contra simuladores
- Validación de conectividad multi-vendor

### Fase 1 — Core Automation (1-2 semanas)
- API FastAPI base con operaciones NAPALM
- Integración Nornir como orquestador
- Inventario inicial (YAML → luego migrar a NetBox)
- Backups automáticos de configuración

### Fase 2 — Seguridad (1-2 semanas)
- Vault para secrets management
- Keycloak para identidad (OAuth2/OIDC)
- RBAC con roles granular
- Auditoría inmutable PostgreSQL
- TLS/mTLS en todas las capas

### Fase 3 — NetBox + Inventario (1 semana)
- Despliegue NetBox
- Migración de inventario YAML → NetBox
- Sincronización bidireccional
- API de consulta de inventario

### Fase 4 — Observabilidad (1 semana)
- Prometheus + Grafana + Loki
- Dashboards de infraestructura
- Alertas configuradas
- Health checks de todos los servicios

### Fase 5 — IA Local (1 semana)
- Ollama con modelo optimizado para GTX 1650
- RAG con documentación de red
- Análisis de configuraciones
- Detección de anomalías
- Recomendaciones (sin ejecución directa)

### Fase 6 — Wazuh + Hardening (1 semana)
- Despliegue Wazuh
- Reglas de detección
- Monitoreo de integridad
- Integración con alertas

### Fase 7 — Alta Disponibilidad (1-2 semanas)
- Réplicas de servicios
- PostgreSQL replicación
- Vault cluster
- Balanceo de carga
- Pruebas de failover

### Fase 8 — Pipeline CI/CD + Pruebas (1 semana)
- Ambientes DEV/QA/PREPROD/PROD
- Pruebas automatizadas
- Validación pre-deploy
- Documentación final
