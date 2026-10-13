# NetPulse — Fase 4: Rendimiento y Arquitectura

## 1. Batching de syslog (`app/services/collectors/syslog_svc.py`)
Los datagramas ya no se envían a Loki uno a uno (lo que colapsaba con una
tormenta de logs). Ahora se **encolan** y un flusher los agrupa por stream y
los empuja cada `NETPULSE_SYSLOG_BATCH_INTERVAL` (0.5 s).
- Cola acotada (`NETPULSE_SYSLOG_QUEUE_MAX`): si se llena, descarta y cuenta.
- `stats()` expone `queued/pushed/dropped/loki_errors/queue_size`.
- Flush final al detener el colector.

## 2. Sondeo de métricas en paralelo (`metrics_poller.py`)
`poll_all()` consulta todos los dispositivos con un `ThreadPoolExecutor`
(`NETPULSE_POLL_WORKERS`, default 8). El ciclo queda acotado por el equipo
más lento, no por la suma. Medido en el lab: **~53 s → ~28 s**.

## 3. Semáforo por dispositivo (`app/services/concurrency.py`)
El límite global (8) se sustituye por un **semáforo por dispositivo**
(`NETPULSE_PER_DEVICE_CONCURRENCY`, default 2): un switch lento no congela
los cupos de los demás. `limited` toma el `id` del primer argumento (dict
del dispositivo); si no aplica, usa un tope global de respaldo
(`NETPULSE_MAX_DEVICE_CONCURRENCY`, default 16).

## 4. OUI/MAC spoofing (engine Go, `deception-engine/cmd/engine/`)
En modo paquete (`-tags packet`), cada IP señuelo responde ARP/ICMP/TCP con
la **MAC de su persona** (prefijo OUI real del fabricante) en vez de la MAC
del host, para no delatar la interfaz. `decoy_mac.go` sintetiza la MAC
cuando la persona no la define. Test: `go test ./cmd/engine`.

## 5. VLANs 802.1Q reales (`scripts/deception_vlans.sh`)
Crea subinterfaces etiquetadas (`eth0.10`, `eth0.20`, …) sobre un bridge,
para que los segmentos de `network.yaml` existan en el kernel (requiere
Linux + root). Uso: `sudo ./scripts/deception_vlans.sh up eth0 br-deception`.

## Pendiente (4.4)
Modularización del `dashboard.html` (6.5k líneas vanilla) en módulos ES6.
Se deja para una fase aparte por el alto riesgo de regresión: el JS comparte
un gran conjunto de globales (`A`, `h()`, `devices`, `t`, `esc`, …) y una
extracción a medias rompería toda la UI. Plan recomendado: extraer de a una
vista por vez con pruebas manuales de humo, empezando por las vistas más
aisladas (export, apikeys, 2fa) y dejando devices/deception al final.
