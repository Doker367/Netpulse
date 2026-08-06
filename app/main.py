"""NetPulse — FastAPI Application."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app.core.settings import API_TITLE, API_VERSION
from app.middleware.audit import AuditMiddleware
from app.middleware.metrics import MetricsMiddleware
from app.middleware.security import SecurityHeadersMiddleware
from app.models.schemas import HealthResponse
from app.routers import devices, facts, config, bulk, audit, auth, netbox, command, metrics as metrics_router, templates, compliance, notifications, groups, export, scheduler, admin_users, prometheus_proxy
from app.services.inventory_svc import list_devices

# ── Rate Limiter ─────────────────────────────────────────────

from slowapi import Limiter

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["100/minute"],  # 100 requests per minute per IP
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown."""
    # Inicializar inventario si está vacío
    if not list_devices():
        from app.services.inventory_svc import add_lab_devices
        add_lab_devices()
        print("📦 Lab inventory initialized with 5 devices")
    yield


app = FastAPI(
    title=API_TITLE,
    version=API_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── Metrics Middleware (outermost — captures ALL requests) ──

app.add_middleware(MetricsMiddleware)

# ── Audit Middleware (second — captures ALL requests) ────────

app.add_middleware(AuditMiddleware)

# ── Rate Limiting Middleware ─────────────────────────────────

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# ── Security Headers Middleware ──────────────────────────────

app.add_middleware(SecurityHeadersMiddleware)

# ── CORS Middleware ──────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

# ── Static files (Dashboard UI) ──────────────────────────────

@app.get("/", response_class=FileResponse)
async def dashboard():
    return FileResponse("app/static/dashboard.html")

@app.get("/favicon.svg", response_class=FileResponse)
async def favicon():
    return FileResponse("app/static/favicon.svg")

# Mount static files directory AFTER route handlers
app.mount("/static", StaticFiles(directory="app/static"), name="static")

