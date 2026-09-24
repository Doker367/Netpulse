# NetPulse — Guía de Operaciones y Arranque

Fecha: 2026-08-05 (actualizado 2026-08-06)
Proyecto: /data/doker/GreenAlgorithm/NetPulse

================================================================
1) ARQUITECTURA DEL STACK
================================================================

El proyecto se compone de 4 piezas:

| Pieza              | Archivo                          | Servicios                                       | Puertos        |
|--------------------|----------------------------------|-------------------------------------------------|----------------|
| Lab de routers     | docker-compose.lab.yml           | cisco, juniper, mikrotik, arista, switch        | 2222-2226, 443, 8728, 8291 |
| Monitoring         | docker-compose.monitoring.yml    | prometheus, loki, promtail                      | 9090, 3100     |
| NetBox (DCIM)      | docker-compose.netbox.yml        | netbox, netbox-db, netbox-redis                 | 8000           |
| API FastAPI        | (no docker) — uvicorn local      | app.main:app                                    | 8082           |

NOTA: Grafana fue RETIRADO del stack (2026-08-06). Las métricas se
muestran en la web del dashboard de NetPulse; los logs van a Loki
vía Promtail y se consultan por API (o futuro panel web).

Redes Docker:
- netpulse_lab-net       → 172.30.0.0/24
- netpulse_monitoring    → bridge (auto)
- netpulse_netbox-net    → 172.31.0.0/24

================================================================
1b) LOKI + PROMTAIL (logs centralizados)
================================================================

Flujo de logs:
  contenedores docker ──► promtail (scrape /var/run/docker.sock)
        ──► loki (3100) ──► consultas por API REST

Promtail descubre automáticamente todos los contenedores Docker
(config/promtail-config.yml, docker_sd) y los etiqueta con:
  container, compose_service, compose_project, container_number

QUERY ÚTILES (API de Loki, sin Grafana):
  # Listar labels conocidas
  curl -s http://localhost:3100/loki/api/v1/labels

  # Logs de un contenedor en la última hora
  curl -s -G http://localhost:3100/loki/api/v1/query_range \
    --data-urlencode 'query={container="netpulse-netbox"}' \
    --data-urlencode "start=$(( $(date +%s) - 3600 ))000000000" \
    --data-urlencode "end=$(date +%s)000000000" \
    --data-urlencode 'limit=50'

  # Contar líneas por contenedor (última hora)
  curl -s -G http://localhost:3100/loki/api/v1/query_range \
    --data-urlencode 'query=sum by (container) (count_over_time({compose_project="netpulse"}[1h]))' \
    --data-urlencode "start=$(( $(date +%s) - 3600 ))000000000" \
    --data-urlencode "end=$(date +%s)000000000"

  # Buscar texto (ej: errores de autenticación)
  --data-urlencode 'query={compose_project="netpulse"} |= "auth"' 

NOTA IMPORTANTE: el selector NO es {job="docker"} — Promtail docker_sd
no genera label "job". Usar {container=...} o {compose_service=...}.

PITFALL: Loki se auto-apaga si el disco supera 90% (ingester
"shutting down", HTTP 500 en push). Fijado umbral a 0.98 en
config/loki-config.yml. Si el disco del host está >98%, Loki volverá
a fallar — vigilar con `df -h /`.

================================================================
2) ARRANCAR TODO EL PROYECTO (FRÍO / DESDE CERO)
================================================================

Los 3 stacks docker se levantan con docker compose. La API corre
con el venv local del proyecto (NO está dockerizada).

PASO 1 — Levantar el stack de lab (routers + switches simulados):

    cd /data/doker/GreenAlgorithm/NetPulse
    docker compose -f docker-compose.lab.yml up -d

PASO 2 — Levantar el stack de monitoreo:

    docker compose -f docker-compose.monitoring.yml up -d

PASO 3 — Levantar NetBox:

    docker compose -f docker-compose.netbox.yml up -d

PASO 4 — Iniciar la API FastAPI (en background):

    cd /data/doker/GreenAlgorithm/NetPulse
    .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8082 &

    # Verificar arranque:
    curl -s -o /dev/null -w "%{http_code}" http://localhost:8082/docs
    # Esperar "Application startup complete" en el log

NOTA: los 3 docker-compose se pueden combinar en un solo comando:

    docker compose \
      -f docker-compose.lab.yml \
      -f docker-compose.monitoring.yml \
      -f docker-compose.netbox.yml \
      up -d

PERO cuidado: cada `-f` define un "proyecto" compose distinto si los
ejecutas por separado; docker los trata como proyectos independientes
(netpulse-lab, netpulse-monitoring, netpulse-netbox) y avisa de
"orphan containers". Es normal, no es un error.

================================================================
3) LEVANTAR SOLO LOS SWITCHES
================================================================

Los switches del lab son 2 servicios del docker-compose.lab.yml:

  a) netpulse-switch   — FRRouting capa 2 (hostname: switch-access-01)
     SSH en 127.0.0.1:2224
     Driver NAPALM: ios (CLI Cisco-like de FRR)

  b) netpulse-arista   — Arista vEOS 4.27.3F (actúa como switch/leaf)
     SSH en 127.0.0.1:2226, eAPI HTTPS en 127.0.0.1:443
     Credenciales: vrnetlab / <password>
     Driver NAPALM: eos (eAPI)

Levantarlos solos:

    cd /data/doker/GreenAlgorithm/NetPulse
    docker compose -f docker-compose.lab.yml up -d switch-l2 router-arista

Verificar:

    docker ps --filter name=netpulse-switch --filter name=netpulse-arista
    # Esperar "healthy" en netpulse-arista (tarda 1-2 min en bootear vEOS)

Probar SSH (desde el host):

    ssh -p 2224 admin@127.0.0.1      # FRR switch (password: admin o según config)
    ssh -p 2226 vrnetlab@127.0.0.1   # Arista vEOS (password: <password>)

NOTA: hay 3 contenedores Arista adicionales que NO están en el
compose actual y aparecen como Exited:
  netpulse-arista-switch, netpulse-arista-core2, netpulse-arista-edge
Estos se crearon antes con docker run manual (puertos 8443/9443/10443).
Si se necesitan, hay que recrearlos con docker run o agregarlos al
compose — no se levantan con los comandos de arriba.

================================================================
4) INVENTARIO DE DISPOSITIVOS (config/devices.yaml)
================================================================

El inventario vive en config/devices.yaml. Dispositivos registrados:

  arista-core-01   127.0.0.1:443    eos   vrnetlab/<password>   router Core
  arista-core-02   127.0.0.1:9443   eos   vrnetlab/<password>   router Core   (requiere contenedor extra)
  arista-edge-01   127.0.0.1:8443   eos   vrnetlab/<password>   router Edge   (requiere contenedor extra)
  arista-switch-01 127.0.0.1:10443  eos   vrnetlab/<password>   switch Access (requiere contenedor extra)
  mikrotik-edge-01 127.0.0.1:8728   ros   admin/(vacío)         router Edge

Los passwords se guardan cifrados (crypto_svc) — el archivo YAML
contiene el texto cifrado.

================================================================
5) VERIFICACIÓN DE SALUD POST-ARRANQUE
================================================================

    # API
    curl -s http://localhost:8082/                  → 200
    curl -s http://localhost:8082/docs              → Swagger UI

    # Endpoints principales
    curl -s http://localhost:8082/api/devices        → inventario
    curl -s http://localhost:8082/api/facts/arista-core-01
    curl -s http://localhost:8082/api/interfaces/arista-core-01
    curl -s http://localhost:8082/api/metrics        → métricas Prometheus

    # Docker health
    docker ps --format "table {{.Names}}\t{{.Status}}"

    # Loki (logs centralizados; 200 = OK)
    curl -s http://localhost:3100/ready
    curl -s http://localhost:3100/loki/api/v1/labels          # labels de logs

    # NetBox (healthcheck puede marcar "starting" aunque funcione:
    # el healthcheck usa /api/ que devuelve 403 sin auth — falso negativo)
    curl -sL -o /dev/null -w "%{http_code}" http://localhost:8000/   → 200

================================================================
6) DETENER TODO
================================================================

    cd /data/doker/GreenAlgorithm/NetPulse
    docker compose -f docker-compose.lab.yml -f docker-compose.monitoring.yml -f docker-compose.netbox.yml down

    # Matar la API (si corre en foreground/background):
    pkill -f "uvicorn app.main:app"

    # Para borrar también volúmenes (cuidado: pierde datos de NetBox):
    docker compose ... down -v

================================================================
7) RECOMENDACIONES PARA AMBIENTE PROFESIONAL (PENDIENTES)
================================================================

Antes de conectar dispositivos físicos reales (prioridad alta):

1. Soportar enable_password / enable secret en el modelo DeviceCreate
   y pasarlo a NAPALM via optional_args — los Cisco reales lo piden.
2. Timeout y retry configurables POR dispositivo (hoy global de 60s,
   retry fijo 2x2s; producción: 30s default, exponential backoff,
   NO reintentar operaciones de escritura).
3. Límite de concurrencia por device_id (semáforo/cola) — evita que
   2 jobs pisen la misma sesión SSH.
4. Soporte de jump host / bastion (ProxyCommand) — producción casi
   nunca es alcanzable directo.
5. Credenciales AAA/TACACS+/RADIUS por grupo (no solo usuario local).
6. Validar drivers reales: ios, iosxr, nxos, junos, fortios con
   get_facts en un dispositivo de prueba antes del parque completo.
7. TLS/HTTPS para la API (hoy 0.0.0.0:8082 sin TLS) y rate limiting
   ya existe (slowapi, 100/min por IP).

Funcionalidades recomendadas:
- Endpoints operativos: get_interfaces_counters, get_environment
  (temperatura/fans/PSU), get_arp_table, get_ntp_servers,
  get_lldp_neighbors (topología).
- Colector periódico de métricas de dispositivo hacia Prometheus
  (hoy las métricas solo cubren la API y operaciones NAPALM).
- Alertas por umbral (CPU > 80%, interfaz down > 5 min, potencia
  óptica degradada) vía notifications_svc + webhooks.
- Soporte SNMP (Telegraf/SNMP exporter) para equipos sin driver NAPALM.
- Ventanas de mantenimiento para deploys de config.
- Drift detection periódica (running-config vs baseline).

Métricas por dispositivo que faltan (estándar NOC):
- Por interfaz: bits/s RX/TX, errores, discards, CRC, estado up/down,
  duplex, speed negociada.
- Dispositivo: CPU %, memoria, temperatura, fans, power supplies,
  uptime, rutas v4/v6, entradas ARP.
- Control plane: BGP estado/prefijos/flaps por sesión, vecinos OSPF,
  NTP sync/offset.
- Óptica (fibra): potencia TX/RX dBm, temperatura SFP, bias current.
- Umbrales típicos óptica: RX < -20 dBm (10G), < -14 dBm (1G).
