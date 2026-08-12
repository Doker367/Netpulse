# NetPulse — Endpoints Operativos y Seguridad (2026-08-10)

## Endpoints operativos nuevos (datacenter)

Tablas y vecinos (requieren JWT):
- GET /api/facts/{id}/arp           → tabla ARP (IP→MAC)
- GET /api/facts/{id}/mac-table     → tabla MAC (FDB del switch)
- GET /api/facts/{id}/lldp          → vecinos LLDP (topología física)
- GET /api/facts/{id}/ntp           → servidores NTP configurados
- GET /api/facts/{id}/environment   → temp/ventiladores/PSU (si el driver lo soporta)

## Alertas (Prometheus rules en config/prometheus-rules.yml)

- DeviceDown: dispositivo caído >5min (critical)
- HighNapalmErrorRate: >0.5 errores/s en 15min (warning)
- HighNapalmLatency: p95 >30s (warning)
- HighHttpErrorRate: >2 5xx/s por path (warning)
- HighApiLatency: p95 >5s (warning)
- BruteForceAttempt: >1 fallo auth/s en 5min (critical)

Webhook de Alertmanager:
- POST /api/alerts/webhook (Bearer) → distribuye alertas a webhooks registrados
- Formato estándar de Alertmanager

## Seguridad (2026-08-10)

Headers HTTP (SecurityHeadersMiddleware):
- X-Content-Type-Options: nosniff
- X-Frame-Options: DENY
- X-XSS-Protection: 1; mode=block
- Strict-Transport-Security: max-age=1y (HSTS)
- Referrer-Policy: strict-origin-when-cross-origin
- Permissions-Policy: geolocation=(), camera=(), microphone=()
- Content-Security-Policy: ajustada para API y dashboard

Autenticación:
- Rate limit login: 5 intentos/min por IP (anti fuerza bruta → 429)
- JWT expiración configurable: NETPULSE_TOKEN_EXPIRE (default 30 min)
- En producción (NETPULSE_ENV=production) se REQUIERE NETPULSE_JWT_SECRET
  (la API NO arranca con el secret por defecto)

HTTPS (opcional pero recomendado en producción):
    export NETPULSE_SSL_CERTFILE=/ruta/cert.pem
    export NETPULSE_SSL_KEYFILE=/ruta/key.pem
    .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8082 \
        --ssl-certfile=$NETPULSE_SSL_CERTFILE --ssl-keyfile=$NETPULSE_SSL_KEYFILE
  Alternativa: reverse proxy (Caddy/nginx) terminando TLS.

## Drivers HP (datacenter)

- procurve, comware, hpe → mapean a driver ios (CLI Cisco-like)
- enable_password en devices.yaml → optional_args["secret"] (enable mode)
- Ejemplo devices.yaml:
    - id: hp-core-01
      hostname: 10.10.0.1
      port: 22
      driver: procurve
      enable_password: <secret>
      credentials: {username: netadmin, password: <cifrado>}
