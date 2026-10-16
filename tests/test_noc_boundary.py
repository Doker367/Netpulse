"""Frontera de netpulse-noc: sin superficie de deception ni de VMs señuelo.

Esta prueba es estática/sin red: solo inspecciona el enrutado registrado y el
árbol de archivos. No abre sockets ni contacta equipos.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Prefijos que pertenecen a Dystopia y no deben existir en el NOC.
FORBIDDEN_PREFIXES = ("/api/deception", "/api/vms", "/ws/vm")


def _route_paths() -> set[str]:
    from app.main import app

    return {getattr(route, "path", "") for route in app.routes}


def test_no_deception_or_vm_routes():
    paths = _route_paths()
    leaked = sorted(
        path for path in paths if any(path.startswith(p) for p in FORBIDDEN_PREFIXES)
    )
    assert leaked == [], f"rutas de deception/VMs en el NOC: {leaked}"


def test_noc_capabilities_still_registered():
    paths = _route_paths()
    for prefix in (
        "/api/devices",
        "/api/facts",
        "/api/config",
        "/api/sftp",
        "/api/firewall",
        "/api/netbox",
        "/api/monitoring",
    ):
        assert any(p.startswith(prefix) for p in paths), f"falta {prefix}"
    assert any(p.startswith("/ws/terminal") for p in paths), "falta el terminal SSH"


def test_deception_artifacts_removed():
    assert not (ROOT / "app" / "services" / "deception").exists()
    assert not (ROOT / "app" / "routers" / "deception.py").exists()
    assert not (ROOT / "app" / "models" / "deception.py").exists()
    assert not (ROOT / "app" / "routers" / "vms.py").exists()
    assert not (ROOT / "deception-engine").exists()
    assert not (ROOT / "docker-compose.deception.yml").exists()


def test_collectors_disabled_by_default():
    from app.core import settings

    assert settings.SYSLOG_ENABLED is False
    assert settings.METRICS_ENABLED is False
    assert settings.API_HOST == "127.0.0.1"
