"""NetPulse — FastAPI Application."""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.core.limiter import limiter
from app.core.logging_redaction import install_log_redaction
from app.core.settings import API_TITLE, API_VERSION
from app.middleware.audit import AuditMiddleware
from app.middleware.metrics import MetricsMiddleware
from app.middleware.security import SecurityHeadersMiddleware
from app.models.schemas import HealthResponse
from app.routers import devices, facts, config, bulk, audit, auth, netbox, command, metrics as metrics_router, templates, compliance, notifications, groups, export, scheduler, admin_users, prometheus_proxy, alerts, alerts_admin, topology, traceroute, traffic, ops, security_ext, integrations, collectors, system, monitoring, ai, terminal, firewall, sftp
from app.routers import discovery, reports, remote
from app.services.inventory_svc import list_devices
from app.utils.async_io import BlockingCallTimeout, env_timeout, run_blocking

logger = logging.getLogger(__name__)

# ── Redacción de secretos en logs ───────────────────────────
# Debe instalarse antes de que uvicorn registre el upgrade del WebSocket de
# terminal (que lleva ?token=<JWT> en la URL). Ver docs/handoffs/terminal.md.
install_log_redaction()

# ── Rate Limiter (definido en app/core/limiter.py) ──────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown."""
    # Inicializar inventario si está vacío
    if not list_devices():
        from app.services.inventory_svc import add_lab_devices
        add_lab_devices()
        print("📦 Lab inventory initialized with 5 devices")

    # Calentar el contador de /api/health: la primera petición no paga la
    # lectura del inventario (y el loop nunca la espera).
    try:
        await _devices_configured()
    except Exception as e:  # noqa: BLE001 — nunca impedir el arranque
        print(f"⚠️ No se pudo calentar el contador de health: {e}")

    # Arrancar collector syslog (si está habilitado)
    from app.core.settings import SYSLOG_ENABLED
    if SYSLOG_ENABLED:
        try:
            from app.services.collectors import syslog_svc
            ok = syslog_svc.start_syslog_collector()
            print(f"📡 Syslog collector {'iniciado' if ok else 'NO iniciado (puerto 514 requiere root?)'}")
        except Exception as e:
            print(f"⚠️ Syslog collector no arrancó: {e}")

    # Arrancar poller de métricas (host + dispositivos)
    from app.core.settings import METRICS_ENABLED, METRICS_INTERVAL
    if METRICS_ENABLED and METRICS_INTERVAL > 0:
        try:
            from app.services.collectors import metrics_poller
            metrics_poller.start(METRICS_INTERVAL)
            print(f"📊 Metrics poller iniciado (intervalo {METRICS_INTERVAL}s)")
        except Exception as e:
            print(f"⚠️ Metrics poller no arrancó: {e}")

    # Arrancar watchdog de sites remotos (alerta de colector enmudecido)
    try:
        from app.services.remote import watchdog_svc
        if await watchdog_svc.start():
            print(
                f"🐕 Site watchdog iniciado (intervalo {watchdog_svc.interval_s()}s, "
                f"stale_after {watchdog_svc.stale_after_s()}s)"
            )
        else:
            print("🐕 Site watchdog deshabilitado")
    except Exception as e:
        print(f"⚠️ Site watchdog no arrancó: {e}")

    yield

    # Shutdown: detener collectors
    try:
        from app.services.remote import watchdog_svc
        await watchdog_svc.stop()
    except Exception:
        pass

    try:
        from app.services.collectors import syslog_svc
        syslog_svc.stop_syslog_collector()
    except Exception:
        pass

    try:
        from app.services.collectors import metrics_poller
        metrics_poller.stop()
    except Exception:
        pass

    try:
        from app.services import metrics_history_svc
        metrics_history_svc.flush()
    except Exception:
        pass

    # Executor propio de SFTP (hilos de transferencia).
    try:
        sftp.shutdown_executor()
    except Exception:
        pass


app = FastAPI(
    title=API_TITLE,
    version=API_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── Middleware ───────────────────────────────────────────────
# En Starlette el ÚLTIMO middleware añadido queda como el MÁS EXTERNO.
# Por eso MetricsMiddleware se añade al final (captura todos los requests).

# CORS (más interno)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security headers
app.add_middleware(SecurityHeadersMiddleware)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# Audit (captura todos los requests)
app.add_middleware(AuditMiddleware)

# Metrics (OUTERMOST — captura TODO, incluso lo que no llega a un endpoint)
app.add_middleware(MetricsMiddleware)

# ── Health ──────────────────────────────────────────────────

# ``/api/health`` es el canario de todo el sistema (balanceadores, Prometheus,
# el propio banco de rendimiento). Antes llamaba ``list_devices()`` en línea:
# con la caché fría eso parsea devices.yaml (50–75 ms medidos con 500 equipos,
# y cientos de ms con inventarios mayores) y bloquea el loop justo cuando más
# se le consulta. Ahora:
#
# - la lectura corre en un hilo (``run_blocking``), nunca en el loop;
# - el resultado se cachea ``NETPULSE_HEALTH_COUNT_TTL_S`` segundos (5 por
#   defecto) con *single-flight*: N health concurrentes lanzan una lectura;
# - si el valor está vencido pero existe, se devuelve al instante y la
#   lectura se refresca en segundo plano.
_HEALTH_COUNT_TTL_ENV = "NETPULSE_HEALTH_COUNT_TTL_S"
_HEALTH_COUNT_TTL_S = 5.0
_HEALTH_COUNT_TIMEOUT_ENV = "NETPULSE_HEALTH_COUNT_TIMEOUT_S"
_HEALTH_COUNT_TIMEOUT_S = 2.0

_health_count: int | None = None
_health_count_at = 0.0
# ``(loop, task)``: el task se reutiliza solo en el mismo event loop (los
# tests crean un loop por TestClient; un task de otro loop no se puede await).
_health_count_task: tuple[asyncio.AbstractEventLoop, asyncio.Task] | None = None


def reset_health_cache() -> None:
    """Limpia la caché del contador de health (tests y banco de rendimiento)."""
    global _health_count, _health_count_at, _health_count_task
    _health_count = None
    _health_count_at = 0.0
    _health_count_task = None


async def _read_device_count() -> int:
    """Cuenta dispositivos fuera del loop; nunca lanza (health es vital)."""
    global _health_count, _health_count_at
    try:
        count = await run_blocking(
            lambda: len(list_devices()),
            timeout=env_timeout(_HEALTH_COUNT_TIMEOUT_ENV, _HEALTH_COUNT_TIMEOUT_S),
            label="contar dispositivos para /api/health",
        )
    except BlockingCallTimeout as exc:
        logger.warning("[health] inventario no leído a tiempo: %s", exc)
        return _health_count if _health_count is not None else 0
    except Exception as exc:  # noqa: BLE001 — el health no puede caerse
        logger.warning("[health] no se pudo leer el inventario: %s", exc)
        return _health_count if _health_count is not None else 0
    _health_count = count
    _health_count_at = time.monotonic()
    return count


async def _devices_configured() -> int:
    """Número de dispositivos para ``/api/health``, sin bloquear el loop."""
    global _health_count_task

    ttl = env_timeout(_HEALTH_COUNT_TTL_ENV, _HEALTH_COUNT_TTL_S)
    if _health_count is not None and time.monotonic() - _health_count_at <= ttl:
        return _health_count

    loop = asyncio.get_running_loop()
    current = _health_count_task
    if current is None or current[0] is not loop or current[1].done():
        _health_count_task = (loop, asyncio.create_task(_read_device_count()))
    task = _health_count_task[1]

    if _health_count is None:
        # Primera petición tras el arranque: hay que esperar la lectura
        # (fuera del loop, así que el resto de peticiones sigue vivo).
        return await asyncio.shield(task)

    # Valor vencido: responder ya con el último conocido y refrescar detrás.
    return _health_count


@app.get("/api/health", response_model=HealthResponse, tags=["Health"])
@limiter.limit("10/minute")  # Stricter limit for health checks
async def health(request: Request):
    return HealthResponse(
        status="healthy",
        devices_configured=await _devices_configured(),
    )


# ── Routers ─────────────────────────────────────────────────

app.include_router(devices.router)
app.include_router(facts.router)
app.include_router(config.router)
app.include_router(bulk.router)
app.include_router(audit.router)
app.include_router(auth.router)
app.include_router(netbox.router)
app.include_router(command.router)
app.include_router(metrics_router.router)
app.include_router(templates.router)
app.include_router(groups.router)
app.include_router(export.router)
app.include_router(scheduler.router)
app.include_router(compliance.router)
app.include_router(notifications.router)
app.include_router(admin_users.router)
app.include_router(prometheus_proxy.router)
app.include_router(alerts.router)
app.include_router(alerts_admin.router)
app.include_router(topology.router)
app.include_router(traceroute.router)
app.include_router(traffic.router)
app.include_router(ops.router)
app.include_router(security_ext.router)
app.include_router(integrations.router)
app.include_router(collectors.router)
app.include_router(system.router)
app.include_router(monitoring.router)
app.include_router(ai.router)
app.include_router(terminal.router)
app.include_router(sftp.router)
app.include_router(firewall.router)
app.include_router(discovery.router)
app.include_router(reports.router)
app.include_router(__import__("app.routers.retention", fromlist=["router"]).router)
app.include_router(remote.router)

# ── Static files (Dashboard UI) ──────────────────────────────
# El panel HTML es la única interfaz; /legacy sigue disponible para enlaces previos.
_STATIC = Path(__file__).resolve().parent / "static"
_DASHBOARD = _STATIC / "dashboard.html"


@app.get("/", response_class=FileResponse)
async def dashboard():
    return FileResponse(_DASHBOARD)


@app.get("/legacy", response_class=FileResponse)
async def legacy_dashboard():
    return FileResponse(_DASHBOARD)


@app.get("/favicon.svg", response_class=FileResponse)
async def favicon():
    return FileResponse(_STATIC / "favicon.svg")

# Mount static files directory AFTER route handlers
app.mount("/static", StaticFiles(directory="app/static"), name="static")

