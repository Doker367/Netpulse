"""Regresión de la tarjeta 13A: salud honesta y escrituras de inventario.

Cubre:

* health con caché/TTL: no repite sondas frescas y **nunca** devuelve verde
  con datos caducados (``online=null`` + ``stale=true``).
* histéresis: un fallo aislado es desconocido; dos fallos, offline.
* presupuesto incremental: una petición no espera al inventario completo.
* contrato de la ruta (``online``/``latency_ms``/``rx``/``tx``/``simple``).
* PUT: conserva credenciales, actualiza desde caché sin reparsear y no deja
  el YAML a medias si el volcado falla.
* concurrencia: dos actualizaciones simultáneas no se pisan entre sí.
"""

import asyncio
import concurrent.futures
import threading
import time
from datetime import datetime

import pytest
import yaml
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.main import app
from app.services import device_health, inventory_svc

ADMIN = {"Authorization": "Bearer " + create_access_token({"sub": "superadmin", "role": "admin"})}


@pytest.fixture(autouse=True)
def _health_isolation(monkeypatch):
    """Sin task de fondo: cada test controla cuándo se sondea."""
    monkeypatch.setenv("NETPULSE_HEALTH_BACKGROUND", "false")
    device_health.reset_state()
    yield
    device_health.reset_state()


@pytest.fixture()
def device_file(tmp_path, monkeypatch):
    f = tmp_path / "devices.yaml"
    f.write_text("devices: []\n")
    monkeypatch.setattr(inventory_svc, "DEVICES_FILE", f)
    inventory_svc.invalidate_cache()
    return f


@pytest.fixture()
def client():
    return TestClient(app)


def _dev(dev_id: str, hostname: str = "127.0.0.1", port: int = 1, **extra) -> dict:
    base = {
        "id": dev_id,
        "hostname": hostname,
        "port": port,
        "driver": "ios",
        "protocol": "ssh",
        "type": "switch",
        "group": "Bench",
        "tags": [],
        "description": "",
        "credentials": {
            "username": "admin",
            "password": "gAAAAA-cifrado",
            "community": "public",
        },
    }
    base.update(extra)
    return base


def _write_devices(path, devices: list[dict]) -> None:
    path.write_text(
        yaml.safe_dump({"devices": devices, "options": {}}, default_flow_style=False)
    )
    inventory_svc.invalidate_cache()


def _seed_known(dev_id: str, online: bool, age_s: float = 0.0) -> None:
    """Inserta un estado conocido con la antigüedad indicada."""
    device_health._apply(dev_id, device_health.ProbeResult(online, 1.5), device_health._generation)
    with device_health._state_lock:
        entry = device_health._state[dev_id]
        entry.last_probe_mono = time.monotonic() - age_s
        entry.checked_at = time.time() - age_s
        entry.rx, entry.tx = ("1 Mb/s", "2 Mb/s")


# ── Health: TTL y honestidad ────────────────────────────────


def test_health_cache_no_reprobe_dentro_del_ttl(monkeypatch):
    monkeypatch.setenv("NETPULSE_HEALTH_TTL", "30")
    calls = []

    async def probe(dev, timeout=None):
        calls.append(dev["id"])
        return device_health.ProbeResult(True, 2.0)

    monkeypatch.setattr(device_health, "probe_device", probe)
    devices = [_dev("d1"), _dev("d2")]

    first = asyncio.run(device_health.collect(devices))
    second = asyncio.run(device_health.collect(devices))

    assert calls == ["d1", "d2"]
    assert first["d1"]["online"] is True
    assert second["d1"]["online"] is True
    assert second["d1"]["stale"] is False
    assert second["d1"]["latency_ms"] == 2.0
    assert second["d1"]["checked_at"] is not None


def test_dato_caducado_es_desconocido_nunca_verde(monkeypatch):
    monkeypatch.setenv("NETPULSE_HEALTH_TTL", "30")
    monkeypatch.setenv("NETPULSE_HEALTH_INLINE_BUDGET", "0")
    _seed_known("viejo", online=True, age_s=31)

    result = asyncio.run(device_health.collect([_dev("viejo")]))

    assert result["viejo"]["online"] is None, "un verde caducado es un falso verde"
    assert result["viejo"]["latency_ms"] is None
    assert result["viejo"]["stale"] is True
    assert result["viejo"]["checked_at"] is not None  # se informa cuándo se midió


def test_ttl_expirado_se_refresca_si_hay_presupuesto(monkeypatch):
    monkeypatch.setenv("NETPULSE_HEALTH_TTL", "5")
    _seed_known("d1", online=True, age_s=10)

    async def probe(dev, timeout=None):
        return device_health.ProbeResult(True, 3.0)

    monkeypatch.setattr(device_health, "probe_device", probe)
    result = asyncio.run(device_health.collect([_dev("d1")]))

    assert result["d1"]["online"] is True
    assert result["d1"]["latency_ms"] == 3.0
    assert result["d1"]["stale"] is False


def test_un_fallo_no_es_verde_ni_offline(monkeypatch):
    _seed_known("d1", online=True, age_s=1)

    async def fail(dev, timeout=None):
        return device_health.ProbeResult(False)

    monkeypatch.setattr(device_health, "probe_device", fail)
    monkeypatch.setenv("NETPULSE_HEALTH_TTL", "0.05")
    monkeypatch.setenv("NETPULSE_HEALTH_UNKNOWN_RETRY", "0")

    first = asyncio.run(device_health.collect([_dev("d1")]))
    assert first["d1"]["online"] is None, "tras 1 fallo no se puede afirmar verde"
    second = asyncio.run(device_health.collect([_dev("d1")]))
    assert second["d1"]["online"] is False, "2 fallos consecutivos = offline"


def test_no_medible_es_desconocido_y_simple_nunca_verde(monkeypatch):
    async def unknown(dev, timeout=None):
        return device_health.ProbeResult(None, error="DNS")

    monkeypatch.setattr(device_health, "probe_device", unknown)

    rich = asyncio.run(device_health.collect([_dev("d1")]))
    assert rich["d1"]["online"] is None
    assert rich["d1"]["stale"] is True

    simple = asyncio.run(device_health.collect([_dev("d1")], simple=True))
    assert simple == {"d1": False}


def test_simple_sin_estado_es_false_no_crashea(monkeypatch):
    monkeypatch.setenv("NETPULSE_HEALTH_INLINE_BUDGET", "0")
    assert asyncio.run(device_health.collect([_dev("d1")], simple=True)) == {"d1": False}


def test_presupuesto_incremental_acota_la_espera(monkeypatch):
    monkeypatch.setenv("NETPULSE_HEALTH_INLINE_BUDGET", "5")
    monkeypatch.setenv("NETPULSE_HEALTH_INLINE_DEADLINE", "0.1")
    calls = []

    async def slow(dev, timeout=None):
        calls.append(dev["id"])
        await asyncio.sleep(5)
        return device_health.ProbeResult(True, 1.0)

    monkeypatch.setattr(device_health, "probe_device", slow)
    devices = [_dev(f"d{i}") for i in range(100)]

    started = time.perf_counter()
    result = asyncio.run(device_health.collect(devices))
    elapsed = time.perf_counter() - started

    assert len(calls) == 5
    assert elapsed < 0.5, "la petición no debe esperar al inventario completo"
    assert all(v["online"] is None for v in result.values())


def test_endpoint_health_contrato(device_file, client, monkeypatch):
    monkeypatch.setenv("NETPULSE_HEALTH_TTL", "30")
    _write_devices(device_file, [_dev("d1")])

    async def probe(dev, timeout=None):
        return device_health.ProbeResult(True, 4.2)

    monkeypatch.setattr(device_health, "probe_device", probe)

    body = client.get("/api/devices/health").json()
    assert set(body["d1"]) >= {"online", "latency_ms", "rx", "tx"}
    assert body["d1"]["online"] is True
    assert body["d1"]["latency_ms"] == 4.2
    assert body["d1"]["rx"] == "0.0 Mb/s"
    assert body["d1"]["tx"] == "0.0 Mb/s"
    assert body["d1"]["stale"] is False
    datetime.fromisoformat(body["d1"]["checked_at"])

    simple = client.get("/api/devices/health?simple=true").json()
    assert simple == {"d1": True}


def test_endpoint_health_filtro_y_404(device_file, client):
    _write_devices(device_file, [_dev("d1")])
    assert client.get("/api/devices/health?device_id=d1").status_code == 200
    assert client.get("/api/devices/health?device_id=nope").status_code == 404


# ── PUT: rendimiento sin perder credenciales ni atomicidad ──


def test_put_conserva_credenciales_y_no_reparsea(device_file, client, monkeypatch):
    _write_devices(device_file, [_dev("d1")])
    before = yaml.safe_load(device_file.read_text())["devices"][0]

    r = client.put("/api/devices/d1", json={"description": "cambiada"}, headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["description"] == "cambiada"

    after = yaml.safe_load(device_file.read_text())["devices"][0]
    assert after["credentials"] == before["credentials"]
    assert len(after) == len(before)

    # La caché quedó apuntando al archivo recién escrito: leer no reparsea.
    def boom(*a, **k):
        raise AssertionError("get_device no debe volver a parsear el YAML tras un PUT")

    monkeypatch.setattr(inventory_svc.yaml, "load", boom)
    assert inventory_svc.get_device("d1")["description"] == "cambiada"


def test_put_fallido_no_deja_yaml_a_medias(device_file, client, monkeypatch):
    _write_devices(device_file, [_dev("d1"), _dev("d2")])
    original = device_file.read_text()

    def boom(*a, **k):
        raise RuntimeError("disco lleno")

    monkeypatch.setattr(inventory_svc.yaml, "dump", boom)
    with pytest.raises(RuntimeError):
        client.put("/api/devices/d1", json={"description": "x"}, headers=ADMIN)

    assert device_file.read_text() == original
    assert [d["id"] for d in yaml.safe_load(device_file.read_text())["devices"]] == ["d1", "d2"]
    assert not list(device_file.parent.glob("*.tmp"))


def test_updates_concurrentes_no_pierden_equipos(device_file):
    _write_devices(device_file, [_dev("d1"), _dev("d2")])

    barrier = threading.Barrier(2)

    def updater(dev_id, desc):
        barrier.wait()
        for _ in range(15):
            assert inventory_svc.update_device(dev_id, {"description": desc})

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(updater, "d1", "uno"),
            pool.submit(updater, "d2", "dos"),
        ]
        for f in futures:
            f.result()

    raw = yaml.safe_load(device_file.read_text())["devices"]
    assert {d["id"]: d["description"] for d in raw} == {"d1": "uno", "d2": "dos"}


def test_adds_concurrentes_no_pierden_equipos(device_file):
    _write_devices(device_file, [])

    def adder(i):
        inventory_svc.add_device(
            {
                "id": f"n{i}",
                "hostname": f"10.0.0.{i}",
                "driver": "ios",
                "username": "u",
                "password": "p",
            }
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(adder, range(20)))

    raw = yaml.safe_load(device_file.read_text())["devices"]
    assert len(raw) == 20
    assert {d["id"] for d in raw} == {f"n{i}" for i in range(20)}
