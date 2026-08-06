# NetPulse — Fase 0: PoC Resultados

> **Fecha:** 5 de junio 2026
> **Estado:** ✅ COMPLETADO

---

## Resumen

**3/3 dispositivos conectados exitosamente** usando NAPALM 5.1.0 con driver IOS
contra simuladores FRRouting en Docker.

---

## Dispositivos Probados

| Dispositivo       | Puerto | Driver | Conectado | Config Backup |
|-------------------|--------|--------|-----------|---------------|
| cisco-core-01     | 2222   | ios    | ✅        | ✅ 32 líneas  |
| juniper-edge-01   | 2223   | ios    | ✅        | ✅ 29 líneas  |
| switch-access-01  | 2224   | ios    | ✅        | ✅ 16 líneas  |

---

## Operaciones NAPALM Probadas

| Operación          | Resultado                                  |
|--------------------|--------------------------------------------|
| `open()`           | ✅ Conexión SSH establecida                 |
| `get_facts()`      | ⚠️ Parcial (FRR no es Cisco real)          |
| `get_interfaces()` | ⚠️ Solo detecta 1 interfaz (loopback)      |
| `get_config()`     | ✅ Backup de running config funcional       |
| `get_bgp_neighbors()`| ⚠️ Error en parseo (afi key)             |
| `close()`          | ✅ Cierre limpio                            |

---

## Lecciones Aprendidas

### Lo que funcionó
1. NAPALM + Netmiko se instalan sin problemas en venv Python 3.14
2. FRRouting en Docker es un excelente simulador para desarrollo
3. El driver `ios` de NAPALM es suficientemente compatible con FRR
4. Config backup funciona perfecto en los 3 dispositivos

### Problemas encontrados (y soluciones)
1. **FRR no trae SSH** → instalar `openssh-server` en el contenedor
2. **vtysh requiere root** → `chmod u+s /usr/bin/vtysh`
3. **Facts muestran "Unknown"** → FRR no es Cisco real; para tests reales usar Cisco IOSv
4. **Solo detecta 1 interfaz** → FRR solo expone eth0; en routers reales hay muchas más

### Drivers disponibles (NAPALM 5.1.0)
✅ ios, iosxr, nxos, junos, eos
❌ huawei, ros (MikroTik), fortios — requieren librerías adicionales

---

## Próximos pasos (Fase 1)
- Implementar API FastAPI con endpoints NAPALM
- Integrar Nornir como orquestador
- Gestionar inventario YAML
- Sistema de backups automáticos
