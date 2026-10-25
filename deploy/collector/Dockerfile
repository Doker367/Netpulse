# syntax=docker/dockerfile:1
#
# NetPulse — colector remoto por site (imagen OCI).
#
# Contexto de build: la RAÍZ del repositorio (no este directorio), porque el
# colector importa una única pieza compartida de la app:
#
#     app/remote_collector/cli.py  ->  app.services.collectors.probe_svc
#
# Copiamos solo el paquete del colector y ese módulo de sonda (stdlib puro).
# No se copia routers, modelos, ni el resto del backend central.
#
# ICMP: el colector NO abre sockets raw desde Python. `probe_svc._icmp_probe()`
# ejecuta el binario `ping` del sistema y solo lo usa para devices con
# protocol: snmp (o puerto 161); los devices ssh/telnet se miden con un
# `connect()` TCP. Por eso la imagen instala `iputils-ping` y el contenedor
# recibe CAP_NET_RAW: así `ping` funciona como uid 10002 (sin setuid root) y
# también en el fallback a socket raw si el kernel no permite ICMP datagram
# para el gid del usuario (hosts endurecidos; Docker suele dejar
# net.ipv4.ping_group_range abierto y entonces el ICMP datagram basta).
#
# Build:
#   docker build -f deploy/collector/Dockerfile -t netpulse-collector:0.1.0 .
#
# Ejecución normal (config y clave montadas en solo-lectura):
#   docker run --rm --network host --cap-drop ALL --cap-add NET_RAW \
#     --read-only --tmpfs /tmp --user 10002:10002 \
#     -v netpulse-collector-buffer:/var/lib/netpulse-collector \
#     -v /etc/netpulse/collector.yaml:/etc/netpulse/collector.yaml:ro \
#     --env-file /etc/netpulse/collector.env \
#     netpulse-collector:0.1.0

ARG PYTHON_IMAGE=python:3.12-slim

FROM ${PYTHON_IMAGE}

LABEL org.opencontainers.image.title="netpulse-collector" \
      org.opencontainers.image.description="NetPulse remote collector (per-site latency/availability probe)" \
      org.opencontainers.image.source="https://github.com/netpulse-noc/netpulse-noc" \
      org.opencontainers.image.licenses="Proprietary"

# iputils-ping: sonda ICMP de devices SNMP (ver nota de cabecera).
# ca-certificates: TLS contra el central con verify_tls: true.
# No se instala compilador, curl, ssh ni napalm/netmiko: la sonda es stdlib.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        iputils-ping \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    HOME=/tmp

# Usuario sin privilegios, uid/gid fijos para que el volumen del buffer y los
# bind mounts del site tengan un propietario predecible.
RUN groupadd --gid 10002 netpulse \
    && useradd --uid 10002 --gid 10002 --no-create-home \
        --home-dir /var/lib/netpulse-collector --shell /usr/sbin/nologin netpulse \
    && install -d -o 10002 -g 10002 -m 0750 /var/lib/netpulse-collector \
    && install -d -m 0755 /etc/netpulse

WORKDIR /app

# Dependencias primero para aprovechar la caché de capas.
COPY requirements-collector.txt /app/requirements-collector.txt
RUN python -m pip install --no-cache-dir --disable-pip-version-check \
        -r /app/requirements-collector.txt

# Solo el colector + la primitiva de sonda compartida (stdlib).
COPY app/__init__.py /app/app/__init__.py
COPY app/remote_collector/ /app/app/remote_collector/
COPY app/services/__init__.py /app/app/services/__init__.py
COPY app/services/collectors/__init__.py /app/app/services/collectors/__init__.py
COPY app/services/collectors/probe_svc.py /app/app/services/collectors/probe_svc.py

# La imagen no necesita escribir en el rootfs: buffer y status.json van al
# volumen /var/lib/netpulse-collector; /tmp es tmpfs en el compose.
USER 10002:10002

STOPSIGNAL SIGTERM

# --status es local (lee status.json junto al buffer) y no necesita API key.
# Salidas del CLI: 0 = estado legible; 3 = aún no hay status (p. ej. durante
# el primer ciclo); 2 = config inválida; 1 = error de lectura.
#
# Decisión: un 3 durante el arranque NO debe marcar el contenedor como
# unhealthy; por eso start-period 90 s (> probe.interval_s por defecto = 60 s
# + margen). Docker no reinicia contenedores unhealthy por sí solo: el
# healthcheck es informativo para el operador y para orquestadores externos.
# Si el proceso muere, `restart: unless-stopped` lo reinicia; la salud real
# se confirma con `--check` (whoami) y con `--status`.
HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
    CMD ["python", "-m", "app.remote_collector", "--config", "/etc/netpulse/collector.yaml", "--status"]

ENTRYPOINT ["python", "-m", "app.remote_collector"]
CMD ["--config", "/etc/netpulse/collector.yaml"]
