# Auditoría de seguridad local — pre-piloto

**Fecha:** 2026-10-26
**Alcance:** repositorio `netpulse-noc` (rama `feat/security-scan`, base `main`), auditoría estática y de configuración **local**. No es un pentest.
**Ejecutado por:** escaneo automatizado `scripts/security_scan.sh` + triaje manual de cada hallazgo.
**Entorno de ejecución:** macOS 26.6.2 arm64; intérprete base Python 3.12.14 (el del `.venv` del proyecto); `pip-audit 2.10.1` y `bandit 1.9.4` instalados en un venv temporal.

> Este informe distingue **"no encontrado"** de **"no comprobado"**. Todo lo que no se pudo
> ejecutar o verificar aparece en la sección de limitaciones. Los WARN no hacen fallar el
> escáner; los FAIL de severidad alta sí (exit code 1).

---

## 1. Resumen ejecutivo

Resultado real de la última ejecución:

| Métrica | Valor |
|---|---|
| PASS | 10 |
| WARN | 26 |
| FAIL | 3 |
| **FAIL de severidad ALTA** | **3** |
| Vulnerabilidades de dependencias (únicas) | 2 (2 paquetes, sin fix disponible) |
| Hallazgos bandit media/alta | 3 alta + 23 media |
| Secretos reales versionados | 0 encontrados |

**Los 3 FAIL de severidad alta son el mismo patrón:** Paramiko con `AutoAddPolicy`
(no se verifica la clave de host SSH) en tres rutas de la aplicación. Es un riesgo real
de intermediario (MITM) en un NOC que envía credenciales a equipos de red, y requiere una
decisión de producto antes del piloto: verificar huellas de host o aceptar el riesgo por escrito.

No se encontraron secretos reales versionados ni archivos sensibles trackeados. El guard de
producción del JWT, CORS, headers de seguridad y ausencia de debug están correctos.

El escáner termina con **exit 1** de forma intencionada mientras SEC-001 no esté mitigado.

---

## 2. Método y reproducibilidad

```bash
# En un clon con .venv (usa Python 3.12 del proyecto):
scripts/security_scan.sh

# Si el worktree no tiene .venv:
NETPULSE_PYTHON=/ruta/al/.venv/bin/python scripts/security_scan.sh

# Conservar JSON/logs temporales para evidencia:
NP_SECURITY_KEEP=1 scripts/security_scan.sh
```

El script:

1. Crea un venv temporal en `${TMPDIR}` (no toca el `.venv` del repo) e instala
   `pip-audit` + `bandit`. Sin red, avisa y marca esos bloques como **NO COMPROBADO**.
2. Ejecuta `pip-audit -r requirements.txt -r requirements-collector.txt` y resume
   paquete/versión/CVE/fix. `pip-audit` no incluye CVSS en su JSON; la severidad de los
   dos hallazgos de este informe se consultó manualmente en OSV.
3. Ejecuta `bandit -r app -ll` y lista hallazgos medios/altos con archivo:línea.
4. Busca secretos **solo en archivos trackeados** (`git grep`), nunca en `.git`, `.venv`,
   `config/users.yaml`, `config/devices.yaml` ni `.env`. No imprime valores: solo
   fichero:línea.
5. Comprueba que `.env`, `config/.fernet_key*`, `config/api_keys.json`,
   `config/users.yaml`, `config/devices.yaml` y material PEM/KEY **no** estén en
   `git ls-files`, y que `.gitignore` los cubra.
6. Revisa `.env.example`, permisos de secretos locales, guard de producción del JWT,
   CORS, headers, debug/reload y la superficie del compose de monitorización.

Idempotente: cada ejecución usa un directorio temporal nuevo y no modifica el repositorio.

---

## 3. Hallazgos

Severidad: **Alta** = bloquea piloto o requiere aceptación formal; **Media** = corregir antes
de exposición a cliente; **Baja** = endurecimiento; **Info** = falso positivo validado o nota.

| ID | Severidad | Categoría | Evidencia | Validación | Recomendación |
|---|---|---|---|---|---|
| SEC-001 | **Alta** | SSH sin verificación de host key (MITM) | `app/routers/sftp.py:511`, `app/routers/terminal.py:357`, `app/services/napalm_svc.py:1099` — `paramiko.AutoAddPolicy()` | **True positive.** Bandit B507 ×3. Las tres rutas envían usuario/contraseña del inventario por SSH. | Ver SEC-001 abajo. Requiere cambio de código + migración de inventario. |
| SEC-002 | **Crítica (advisory) / riesgo efectivo bajo-medio** | Dependencia vulnerable sin fix | `requirements.txt:10` — `python-jose[cryptography]==3.5.0`; PYSEC-2026-4298 / CVE-2026-85394 / GHSA-3qf3-8w2g-rqmx (CVSS 3.1 `AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:N`, 9.1) | **No hay versión corregida** (afecta hasta 3.5.0). Mitigado en esta app: `jwt.decode(..., algorithms=["HS256"])` en `app/core/security.py:74` y secreto simétrico, no clave pública. | Planificar migración a PyJWT 2.x en tarjeta aparte (cambio mayor, requiere suite completa). Mientras: mantener allowlist HS256 y monitorizar upstream. |
| SEC-003 | **Alta (advisory) / riesgo efectivo bajo** | Dependencia vulnerable sin fix | `ecdsa==0.19.2` (transitiva de `python-jose[cryptography]`); PYSEC-2026-1325 / CVE-2024-23342 / GHSA-wj6h-64fc-37mp (CVSS 3.1 7.4) | **No hay fix.** Ataque de timing Minerva sobre P-256. La app firma/valida solo HS256, por lo que la ruta ECDSA no se usa hoy. | No urgente mientras no se use ES256/ECDSA. Reevaluar si se añaden algoritmos asimétricos. |
| SEC-004 | **Media** | Credencial por defecto + exposición | `docker-compose.monitoring.yml:75` (puerto `3000:3000` en todas las interfaces), `:78` `GF_SECURITY_ADMIN_PASSWORD: admin`, `:82-84` anónimo activo y `SameSite` desactivado | **True positive en compose legacy.** `docs/OPERACIONES.md:19` indica que Grafana fue retirado y `deploy/docker-compose.prod.yml` no lo incluye. Riesgo solo si se levanta ese compose en el host del cliente. | Ver SEC-004 abajo. Preferible eliminar el servicio del compose legacy; si se mantiene, password desde entorno, bind `127.0.0.1:3000:3000`, anónimo off. |
| SEC-005 | Baja | Bind a todas las interfaces | `app/core/settings.py:91` (`0.0.0.0`), `:144` (`0.0.0.0`), `app/services/collectors/syslog_svc.py:38` | Bandit B104 ×3. En producción el compose no publica puertos de la API y Caddy hace reverse proxy; el servicio systemd escucha `0.0.0.0:8082` y confía en firewall/proxy local. | Aceptado con control compensatorio (sin puertos publicados + firewall). En instalaciones systemd, valorar `127.0.0.1` si el proxy es local. |
| SEC-006 | Baja / FP | `urlopen` con esquemas permitidos | 15 ubicaciones B310: `app/routers/alerts.py:71`, `monitoring.py:47,55`, `prometheus_proxy.py:17,33`, `traffic.py:27`, `alerts_admin_svc.py:93`, `syslog_svc.py:137`, `loki_proxy.py:32`, `netbox_sync.py:52`, `slack.py:43,83`, `telegram.py:45`, `capacity_svc.py:25`, `retention_svc.py:25` | Triaje: URLs fijas (`localhost`), configuración de entorno o webhooks configurados; no se acepta una URL arbitraria del usuario en esos puntos. | Centralizar salidas HTTP con allowlist de esquema/host y timeouts; no urgente. |
| SEC-007 | Info / FP | SQL construido con f-strings | `app/remote_collector/buffer.py:259,283,447` | **Falso positivo validado:** el único fragmento interpolado son marcadores `?` generados por longitud (`",".join("?" for _ in chunk)`); los valores van parametrizados en la tupla. | Sin acción. Añadir `# nosec B608` justificado si se quiere silenciar. |
| SEC-008 | Info / FP | `yaml.load` aparentemente inseguro | `app/services/inventory_svc.py:113` | **Falso positivo validado:** `_Loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)` en `inventory_svc.py:50`. Bandit no resuelve la asignación dinámica. | Sin acción. |
| SEC-009 | Baja / FP | Posible inyección de shell vía Paramiko | `app/services/napalm_svc.py:1103` | Triaje: `exec_command` de Paramiko no usa shell; en el único caller con dato de usuario (`traceroute`) el destino se valida con `_DEST_RE` (`app/routers/traceroute.py:30,225`) y requiere rol operator+. | Mantener validación; añadir test de regresión del regex. |
| SEC-010 | Baja | Community SNMP por defecto `public` | `app/core/settings.py:150`; `app/services/collectors/snmp_svc.py:43` | Revisión manual (no la detecta bandit). Si falta la variable, se usa `public`. | En producción exigir `NETPULSE_SNMP_COMMUNITY` no vacío (fail-fast) y documentar SNMPv3 donde sea posible. |
| SEC-011 | Baja | Documentación OpenAPI pública | `app/main.py:107-108` (`/docs`, `/redoc` sin condición de entorno) | Revisión manual. Expone esquema de API a cualquiera que alcance el host. | Deshabilitar en producción (`docs_url=None`, `redoc_url=None`) o proteger tras autenticación/reverse proxy. |
| SEC-012 | Info | Placeholders débiles en `.env.example` | `.env.example`: `NETBOX_SUPERUSER_PASSWORD=cambiame`, `NETBOX_DB_PASSWORD=cambiame`, `NETPULSE_LAB_PASSWORD=cambiame` | El escáner los trata como placeholders (PASS: no son secretos reales), pero un operador podría copiarlos sin cambiarlos. | Dejar valores vacíos y comentario `obligatorio`; el compose ya exige `${...:?}` para NetBox. |
| SEC-013 | Baja | Guard de producción sensible al literal exacto | `app/core/security.py:26` — `_IS_PRODUCTION = os.getenv("NETPULSE_ENV", "development").lower() == "production"` | Revisión manual. `NETPULSE_ENV=prod` o `Production ` (espacio) **no** activan el guard y se usaría el secreto de desarrollo. | Normalizar (`strip().lower()` + aceptar `prod`) o fallar si el valor no es `development`/`production`. Añadir test. |
| SEC-014 | Info | JWT en `localStorage` | `app/static/js/core.js:38-41`, `ui-common.js:207-212` | Revisión manual; el CSP limita scripts, pero cualquier XSS podría leer el token. | Valorar cookie `HttpOnly`/`SameSite` en una tarjeta de arquitectura; no bloquea el piloto. |

### 3.1. SEC-001 — SSH sin verificación de host key (Alta)

**Evidencia**

```
app/routers/sftp.py:511      client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
app/routers/terminal.py:357  client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
app/services/napalm_svc.py:1099 client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
```

**Por qué importa:** `AutoAddPolicy` acepta cualquier clave de host. Un atacante en la ruta
(DNS/ARP/segmento de gestión comprometido) puede suplantar el equipo y capturar las
credenciales del inventario, que se envían en cada conexión. El NOC administra equipos
reales, por lo que el impacto es alto aunque la probabilidad dependa de la segmentación.

**Parche propuesto (no aplicado: requiere decisión operativa y migración de inventario)**

1. Añadir al inventario una huella de host key por dispositivo (`ssh_host_key` o
   `ssh_host_key_sha256`), capturada en una ventana controlada.
2. Sustituir `AutoAddPolicy` por una política que compare la huella; si no hay huella,
   **rechazar** y mostrar un error accionable (no aceptar silenciosamente).
3. Alternativa intermedia: `client.load_system_host_keys()` + `paramiko.RejectPolicy()`
   con un `known_hosts` gestionado por el NOC.
4. Mantener `AutoAddPolicy` solo detrás de un flag explícito de laboratorio
   (`NETPULSE_SSH_TRUST_UNKNOWN=false` por defecto).
5. Tests con Paramiko mockeado: huella correcta conecta, huella distinta falla, huella
   ausente falla salvo flag de laboratorio.

Mientras no se implemente, el piloto debe asumir el riesgo por escrito y limitar el
segmento de gestión (VLAN dedicada, sin acceso de usuarios), que **reduce** pero no elimina
el MITM.

### 3.2. SEC-004 — Grafana legacy con `admin/admin` (Media)

`docker-compose.monitoring.yml` no forma parte del stack de producción
(`deploy/docker-compose.prod.yml` termina en Promtail y no incluye Grafana), y la
documentación lo da por retirado. Aun así, es un fichero versionado que un operador puede
levantar en el host del cliente: publica el puerto 3000 en todas las interfaces, con
`admin/admin`, registro anónimo de solo lectura y `SameSite` desactivado.

Parche propuesto (fuera de la zona de este cambio: no se toca deploy/Docker):

```yaml
ports:
  - "127.0.0.1:3000:3000"
environment:
  GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD:?definir en .env}
  GF_AUTH_ANONYMOUS_ENABLED: "false"
  GF_SECURITY_COOKIE_SAMESITE: lax
```

Mejor aún: eliminar el servicio del compose legacy y dejar solo Prometheus/Loki/Promtail.

---

## 4. Dependencias (pip-audit)

| Paquete | Versión | Advisory | Fix | Severidad OSV | Riesgo efectivo en NetPulse |
|---|---|---|---|---|---|
| `python-jose` | 3.5.0 | PYSEC-2026-4298 / CVE-2026-85394 / GHSA-3qf3-8w2g-rqmx | **Ninguno** (afecta hasta 3.5.0) | Crítica (9.1) | Bajo-medio: solo HS256 con secreto simétrico; no se aceptan claves públicas como secreto HMAC |
| `ecdsa` | 0.19.2 | PYSEC-2026-1325 / CVE-2024-23342 / GHSA-wj6h-64fc-37mp | **Ninguno** | Alta (7.4) | Bajo: ruta ECDSA no usada (HS256) |

No se actualizó ningún pin: no existe versión corregida y sustituir `python-jose` es un
cambio mayor que exige tests y ventana de cambio. La suite de tests se ejecutó sin cambios
(ver sección 7).

---

## 5. Secretos y configuración

Comprobaciones que pasaron:

- Ningún `.env`, `.fernet_key`, `api_keys.json`, `users.yaml`, `devices.yaml`,
  `.netbox_token`, `.initial_credentials` ni material PEM/KEY está trackeado.
- `.gitignore` cubre esos archivos.
- El JWT por defecto (`netpulse-dev-secret-change-me`) solo aparece en
  `app/core/security.py` y documentación; existe guard que impide arrancar en producción
  con ese valor (`app/core/security.py:27-31`).
- `.env.example` no contiene secretos reales (solo vacíos/placeholders).
- Permisos de secretos locales: no hay ficheros sensibles en este clon; el instalador
  (`scripts/prepare_host.sh:147-161`) fija `0600/0640` para `.env`/`netpulse.env`.
- CORS con credenciales limitado a orígenes explícitos (`app/main.py:118-124`), sin `*`.
- `SecurityHeadersMiddleware` registrado (`app/main.py:128`) y Caddy añade HSTS y oculta
  `Server` (`deploy/Caddyfile:24-28`).
- Sin `--reload`, `reload=True` ni `debug=True` en `app/`, `deploy/` ni Dockerfiles.

Advertencias:

- `docker-compose.monitoring.yml` contiene una contraseña real por defecto (`admin`) y
  activa anónimo (SEC-004). Es el único WARN de la búsqueda de credenciales en YAML/JSON.
- El guard de producción es útil pero depende del literal exacto `production` (SEC-013).
- El `NETPULSE_ENV=production` real no se pudo comprobar en este clon porque no hay `.env`;
  `deploy/docker-compose.prod.yml:41` sí lo fuerza en el contenedor, y el guard de
  `security.py` está presente.

---

## 6. Plan de remediación priorizado

### P0 — antes de exponer el NOC a un cliente

1. **SEC-001 (Alta):** implementar verificación de host key SSH (huella en inventario o
   `known_hosts` gestionado). Es el único hallazgo que bloquea el piloto. Añadir tests.
2. **SEC-002 (Crítica advisory):** abrir tarjeta de migración `python-jose` → `PyJWT 2.x`,
   con pruebas de login/refresh/websocket y compatibilidad de tokens. Mientras tanto,
   dejar por escrito la aceptación del riesgo residual y la mitigación (HS256 explícito).
3. **SEC-004 (Media):** asegurar que el compose legacy de Grafana **no** se despliega en
   el host del cliente; si se usa, aplicar el parche de la sección 3.2.

### P1 — primera semana de piloto

4. **SEC-013:** normalizar `NETPULSE_ENV` y añadir test del guard.
5. **SEC-010:** exigir `NETPULSE_SNMP_COMMUNITY` explícito en producción.
6. **SEC-011:** deshabilitar `/docs` y `/redoc` en producción o protegerlos.
7. **SEC-003:** mantener bajo observación; sin acción mientras no se use ECDSA.
8. **SEC-012:** vaciar placeholders `cambiame` de `.env.example`.

### P2 — endurecimiento continuo

9. **SEC-005:** revisar bindings por instalación (systemd vs Docker) y firewall.
10. **SEC-006:** centralizar HTTP saliente con allowlist de esquema/host.
11. **SEC-014:** evaluar cookies `HttpOnly` para el JWT.
12. Integrar `scripts/security_scan.sh` en la checklist de release (necesita red para
    pip-audit; bandit y las comprobaciones locales funcionan sin ella).

---

## 7. Verificación

- `scripts/security_scan.sh` ejecutado de verdad el 2026-10-26 (no es un informe teórico).
- Suite de tests: `PYTHONPATH=. /Users/doker/netpulse-noc/.venv/bin/pytest tests/ -q`
  → **446 passed** (sin cambios de código de aplicación).
- No se modificó `app/` ni `deploy/` ni Docker en este cambio.
- El escáner devuelve `exit 1` por los 3 FAIL de severidad alta (SEC-001), como está
  diseñado.

---

## 8. Qué NO cubre este informe

- **Pentest / explotación real** ni pruebas de intrusión sobre la API o la red.
- **TLS real**: no se validan handshake, cadena, renovación ni configuración efectiva de
  Caddy/Let's Encrypt; solo se revisa el fichero.
- **Imágenes Docker y sistema operativo**: no se escanean capas, paquetes del host ni CVEs
  de la imagen base (haría falta Trivy/Grype y acceso al registro).
- **Hardware, firmware, SNMP/SSH de los equipos gestionados, segmentación/VLAN y firewall**:
  fuera del repositorio.
- **Revisión manual completa de lógica**: autorización por endpoint (IDOR), revocación de
  sesión, CSRF, seguridad de WebSockets, integridad de auditoría, lógica de negocio y
  criptografía más allá de lo citado. Bandit no sustituye una revisión de código.
- **Histórico de git**: la búsqueda de secretos cubre el árbol actual, no `git log -p` ni
  variables/secretos de CI.
- **Entorno real de producción**: no se comprobaron permisos, propietarios ni variables
  del host del cliente (no hay `.env` en este clon).
- **Rate limiting y DoS**: no se probaron límites ni comportamiento bajo carga.
- **Backups, cifrado en reposo y restauración**: no auditados aquí.
- **Severidad de dependencias**: pip-audit no expone CVSS; las dos severidades de la tabla
  se consultaron manualmente en OSV el 2026-10-26. Un CVE sin severidad publicada no se
  puede clasificar automáticamente.

**Conclusión honesta:** no hay "0 vulnerabilidades". Hay 2 advisories de dependencia sin
fix (una crítica, con explotabilidad condicionada en esta configuración), 3 rutas SSH sin
verificación de host key (alto) y varios hallazgos medios/bajos. El repositorio no tiene
secretos versionados conocidos. La decisión de iniciar el piloto debe pasar por resolver o
aceptar formalmente SEC-001.
