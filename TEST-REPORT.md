# Informe de pruebas

Suite propia de netpulse-noc (12 archivos).

## Resultado de la última ejecución

```
cd /Users/doker/netpulse-noc
NETPULSE_METRICS_ENABLED=false NETPULSE_SYSLOG_ENABLED=false \
NETPULSE_FIREWALL_ENABLED=false NETPULSE_AI_ENABLED=false \
  <python> -m pytest
```

- **93 passed, 0 failed** (4 s), intérprete del entorno de desarrollo, sin
  instalaciones ni descargas durante la prueba.

Evidencia de frontera (prueba `tests/test_noc_boundary.py`, 5/5 PASS):

- 127 rutas HTTP registradas (`app.openapi()`), **ninguna** bajo
  `/api/deception` ni `/api/vms`.
- Único WebSocket registrado: `/ws/terminal/{device_id}` (terminal SSH real);
  no existe `/ws/vm/*`.
- Ausentes: `app/services/deception/`, `app/routers/deception.py`,
  `app/models/deception.py`, `app/routers/vms.py`, `deception-engine/`,
  `docker-compose.deception.yml`.
- `app.core.settings` ya no expone `DECEPTION_ENABLED`, `VMS_ENABLED` ni
  `DECEPTION_INGEST_TOKEN`.
- Capacidades NOC presentes: `/api/devices`, `/api/facts`, `/api/config`,
  `/api/sftp`, `/api/firewall`, `/api/netbox`, `/api/monitoring`, `/api/ai`.

| Ámbito | Estado |
|---|---|
| Sintaxis Python (`ast.parse`, 0 errores) | PASS |
| Suite completa de pruebas | PASS (93) |
| Frontera sin deception/VMs | PASS |
| Auth/RBAC, inventario, NAPALM, Netmiko, SFTP, NetBox, FortiOS | PASS (con mocks) |
| Laya (stub/decisiones) | PASS |
| Colectores deshabilitados por defecto | PASS |
| SSH/Telnet/SNMP contra equipos reales | NO_EJECUTADA (prohibido por política) |
| Backups/drift/scheduler con equipo real | NO_EJECUTADA (requiere fixtures/equipos autorizados) |

## Nota sobre el entorno

No hay `.venv` en este repositorio. Puede ejecutar la suite de dos formas:

1. Rápido (sin instalar nada), usando un intérprete que ya tenga las
   dependencias del proyecto.
2. Recomendado para el servidor: crear un entorno propio
   (`python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`)
   y ejecutar `.venv/bin/python -m pytest`.

No se contactó ningún equipo real ni se abrieron listeners.
