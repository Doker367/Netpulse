# syntax=docker/dockerfile:1
# ============================================================
# NetPulse NOC — API image (multi-stage, non-root, reproducible)
# ============================================================
# Etapa 1: compilar/descargar wheels con todas las dependencias.
# Etapa 2: runtime slim SIN compiladores ni caché de pip.
#
# La API escucha en 8082 (HTTP plano). TLS lo termina Caddy, que es
# el único servicio que publica puertos al host (ver deploy/).
# ============================================================

FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

# Compiladores solo en el builder: si algún día no hay wheel para la
# arquitectura, pip puede construirlo aquí. Nunca llegan a la imagen final.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
        libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt ./
RUN python -m pip wheel --wheel-dir /wheels -r requirements.txt


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    # HOME escribible (tmpfs): paramiko/napalm pueden tocar ~/.ssh
    HOME=/tmp

# Utilidades del sistema que la app invoca por subprocess (ping/traceroute
# desde el servidor NOC). No son compiladores; pesan poco.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        iputils-ping \
        traceroute \
    && rm -rf /var/lib/apt/lists/*

# Usuario sin privilegios con uid/gid fijos (bind mounts predecibles).
RUN groupadd --gid 10001 netpulse \
    && useradd --uid 10001 --gid 10001 --no-create-home \
        --home-dir /app --shell /usr/sbin/nologin netpulse

WORKDIR /app

# Dependencias: solo desde las wheels de la etapa builder.
COPY --from=builder /wheels /wheels
COPY requirements.txt ./
RUN python -m pip install --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

# Código de la aplicación y estáticos (app/static lo sirve FastAPI).
COPY --chown=10001:10001 app ./app

# Directorios de estado persistente. En produccion se montan como bind
# (deploy/docker-compose.prod.yml); se crean aquí para que la imagen
# también pueda arrancar sin montajes (solo lectura, sin persistencia).
RUN mkdir -p /app/config /app/runtime /app/audit /app/backups /app/reports \
    && chown -R 10001:10001 /app

USER 10001:10001

EXPOSE 8082

# Healthcheck sin curl: usa el Python de la propia imagen.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=5 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8082/api/health', timeout=4).status == 200 else 1)"]

# workers=1 deliberado: SQLite (audit/remote) y el poller viven en el
# proceso; varios workers duplicarían escritores y collectors.
# --proxy-headers + --forwarded-allow-ips=*: la API solo es alcanzable
# por la red interna de Docker; Caddy es el único cliente externo.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8082", \
     "--workers", "1", \
     "--proxy-headers", \
     "--forwarded-allow-ips=*"]
