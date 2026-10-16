"""Frontera de netpulse-noc: sin superficie de deception ni de VMs señuelo.

Prueba estática/sin red: inspecciona el enrutado registrado y el árbol de
archivos. No abre sockets ni contacta equipos.

Nota: FastAPI 0.141 envuelve los routers incluidos en ``_IncludedRouter``, por
lo que ``app.routes`` no los aplana. Se usa ``app.openapi()`` para las rutas
HTTP y un recorrido recursivo para las rutas WebSocket.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Prefijos que pertenecen a Dystopia y no deben existir en el NOC.
FORBIDDEN_PREFIXES = ("/api/deception", "/api/vms", "/ws/vm")


def _http_paths() -> set[str]:
    from app.main import app

    return set(app.openapi().get("paths", {}))


def _iter_leaf_routes(routes):
    """Recorre routers incluidos de forma perezosa hasta las hojas.

    FastAPI 0.141 expone el router incluido como ``_IncludedRouter`` con el
    atributo ``original_router``.
    """
    for route in routes:
        nested = getattr(route, "original_router", None) or getattr(route, "router", None)
        if nested is not None:
            yield from _iter_leaf_routes(getattr(nested, "routes", []))
        else:
            yield route


def _websocket_paths() -> set[str]:
    from app.main import app

    return {
        getattr(route, "path", "")
        for route in _iter_leaf_routes(app.routes)
        if type(route).__name__ == "APIWebSocketRoute"
    }


def test_no_deception_or_vm_routes():
    leaked = sorted(
        path
        for path in _http_paths()
        if any(path.startswith(prefix) for prefix in FORBIDDEN_PREFIXES)
    )
    assert leaked == [], f"rutas de deception/VMs en el NOC: {leaked}"

    ws_leaked = sorted(
        path for path in _websocket_paths() if path.startswith("/ws/vm")
    )
    assert ws_leaked == [], f"WebSockets de VM en el NOC: {ws_leaked}"


def test_noc_capabilities_still_registered():
    paths = _http_paths()
    for prefix in (
        "/api/devices",
        "/api/facts",
        "/api/config",
        "/api/sftp",
        "/api/firewall",
        "/api/netbox",
        "/api/monitoring",
        "/api/ai",
    ):
        assert any(p.startswith(prefix) for p in paths), f"falta {prefix}"
    assert "/ws/terminal/{device_id}" in _websocket_paths(), "falta el terminal SSH"


def test_deception_artifacts_removed():
    assert not (ROOT / "app" / "services" / "deception").exists()
    assert not (ROOT / "app" / "routers" / "deception.py").exists()
    assert not (ROOT / "app" / "models" / "deception.py").exists()
    assert not (ROOT / "app" / "routers" / "vms.py").exists()
    assert not (ROOT / "deception-engine").exists()
    assert not (ROOT / "docker-compose.deception.yml").exists()


def test_deception_settings_are_gone():
    from app.core import settings

    for name in ("DECEPTION_ENABLED", "VMS_ENABLED", "DECEPTION_INGEST_TOKEN"):
        assert not hasattr(settings, name), f"settings de deception presente: {name}"


def test_collectors_disabled_by_default():
    from app.core import settings

    assert settings.SYSLOG_ENABLED is False
    assert settings.METRICS_ENABLED is False
    assert settings.FIREWALL_ENABLED is False
