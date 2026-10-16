# Informe de pruebas

Suite propia de netpulse-noc (12 archivos):

| Archivo | Ámbito |
|---|---|
| `tests/test_noc_boundary.py` | Frontera: sin rutas/módulos de deception o VMs; capacidades NOC presentes; collectors off |
| `tests/test_auth_security.py` | Auth / RBAC / usuarios |
| `tests/test_fase1_hardening.py` | Endurecimiento (rate limit, auditoría, alertas) |
| `tests/test_fase2.py` | Auditoría, concurrencia, inventario, NAPALM |
| `tests/test_fase2_noc.py` | Rutas y WebSocket del NOC |
| `tests/test_fase4_performance.py` | Rendimiento de colectores (syslog, colas) |
| `tests/test_inventory_api.py` | CRUD de inventario |
| `tests/test_legacy_routing.py` | Enrutado legacy NAPALM/Netmiko |
| `tests/test_monitoring_api.py` | API de monitoring del NOC |
| `tests/test_netmiko_svc.py` | Netmiko / CLI |
| `tests/test_sftp_api.py` | SFTP |
| `tests/test_ai_decision_svc.py` | Laya (motor de decisiones) |

| Prueba | Estado | Evidencia |
|---|---|---|
| Sintaxis Python completa | PASS | `ast.parse`, 0 errores |
| Git independiente/limpio/fsck | PASS | repo propio, historial nuevo |
| Imports/rutas backend deception/VM | PASS | retirados de main/terminal |
| Laya backend/stub/router/tests | PASS (estático) | archivos presentes; fallback conservado |
| Secretos/runtime por nombres versionados | PASS | sin coincidencias |
| pytest funcional | NO_EJECUTADA | falta entorno virtual; instalar dependencias en cada repo |
| SSH/Telnet/SNMP/NAPALM/Netmiko/HP/dual | NO_EJECUTADA | no se permite contactar equipos; requiere fixtures/mocks |
| Backups/drift/scheduler/terminal/SFTP/FortiOS/NetBox | NO_EJECUTADA | validación dinámica con datos sintéticos |

No se realizó ninguna conexión de red ni se contactó ningún equipo real.
