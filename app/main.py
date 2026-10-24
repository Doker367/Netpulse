"""NetPulse — FastAPI Application."""

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
from app.core.settings import API_TITLE, API_VERSION
from app.middleware.audit import AuditMiddleware
from app.middleware.metrics import MetricsMiddleware
from app.middleware.security import SecurityHeadersMiddleware
from app.models.schemas import HealthResponse
from app.routers import devices, facts, config, bulk, audit, auth, netbox, command, metrics as metrics_router, templates, compliance, notifications, groups, export, scheduler, admin_users, prometheus_proxy, alerts, alerts_admin, topology, traceroute, traffic, ops, security_ext, integrations, collectors, system, monitoring, ai, terminal, firewall, sftp
from app.routers import discovery, reports, remote
from app.services.inventory_svc import list_devices

# ── Rate Limiter (definido en app/core/limiter.py) ──────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown."""
    # Inicializar inventario si está vacío
    if not list_devices():
        from app.services.inventory_svc import add_lab_devices
        add_lab_devices()
        print("📦 Lab inventory initialized with 5 devices")

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

    yield

    # Shutdown: detener collectors
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


@app.get("/api/health", response_model=HealthResponse, tags=["Health"])
@limiter.limit("10/minute")  # Stricter limit for health checks
async def health(request: Request):
    return HealthResponse(
        status="healthy",
        devices_configured=len(list_devices()),
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

