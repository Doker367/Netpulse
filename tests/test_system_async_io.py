"""Tests de I/O bloqueante en handlers async (tarjeta 13B).

Cubren:

- ``docker_stats_svc``: Docker presente/ausente, caché, timeout y estado
  honesto (nunca excepción hacia el router).
- Endpoints ``/api/system/*``: misma forma de datos que antes, respuesta 200
  con Docker caído, y event loop libre mientras corre el I/O.
- ``/api/metrics``: la recolección de psutil ya no bloquea el loop.
"""

import asyncio
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.main import app
from app.services import docker_stats_svc, host_metrics_svc
from app.services.collectors import metrics_poller

VIEWER = {"Authorization": "Bearer " + create_access_token({"sub": "viewer", "role": "viewer"})}


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Aísla cachés y variables de entorno entre tests."""
    for var in (
        "NETPULSE_DOCKER_STATS_TTL",
        "NETPULSE_DOCKER_STATS_ERROR_TTL",
        "NETPULSE_DOCKER_STATS_TIMEOUT",
        "NETPULSE_HOST_METRICS_CACHE_TTL",
        "NETPULSE_HOST_METRICS_TIMEOUT",
    ):
        monkeypatch.delenv(var, raising=False)
    docker_stats_svc.reset_cache()
    host_metrics_svc.reset_cache()
    yield
    docker_stats_svc.reset_cache()
    host_metrics_svc.reset_cache()


def _fake_docker(tmp_path, body: str) -> str:
    """Crea un ``docker`` falso ejecutable y devuelve su PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "docker"
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(0o755)
    return str(bin_dir)


# ── docker_stats_svc ─────────────────────────────────────────


def test_docker_present_parsea_netio(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "PATH",
        _fake_docker(
            tmp_path,
            "printf 'netpulse-lab1\\t1.2MB / 3.4MB\\n'\n"
            "printf 'netpulse-lab2\\t0.5MB / 0.1MB\\n'\n",
        ),
    )
    snap = docker_stats_svc.snapshot(refresh=True)
    assert snap["available"] is True
    assert snap["reason"] is None
    assert snap["containers"]["netpulse-lab1"] == {"rx": "1.2MB", "tx": "3.4MB"}
    # Alias corto para el consumidor histórico.
    assert snap["containers"]["lab1"] == {"rx": "1.2MB", "tx": "3.4MB"}
    assert snap["count"] == 2
    assert snap["cached"] is False


def test_docker_ausente_es_estado_honesto(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # sin binario docker
    snap = docker_stats_svc.snapshot(refresh=True)
    assert snap["available"] is False
    assert snap["reason"] == "docker_no_disponible"
    assert "PATH" in snap["detail"]
    assert snap["containers"] == {}
    # Nunca lanza: el router puede devolver 200 con available=false.
    assert snap["ttl_seconds"] == 8.0


def test_docker_daemon_caido_no_lanza(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "PATH",
        _fake_docker(tmp_path, "echo 'Cannot connect to the Docker daemon' >&2\nexit 1\n"),
    )
    snap = docker_stats_svc.snapshot(refresh=True)
    assert snap["available"] is False
    assert snap["reason"] == "docker_no_disponible"
    assert "Docker daemon" in snap["detail"]


def test_docker_cache_evita_relanzar_comando(tmp_path, monkeypatch):
    counter = tmp_path / "runs.txt"
    monkeypatch.setenv(
        "PATH",
        _fake_docker(
            tmp_path,
            f"echo run >> {counter}\n"
            "printf 'netpulse-lab1\\t1MB / 2MB\\n'\n",
        ),
    )
    docker_stats_svc.snapshot(refresh=True)
    assert counter.read_text().count("run") == 1

    snap = docker_stats_svc.snapshot()  # dentro del TTL
    assert snap["cached"] is True
    assert snap["age_seconds"] >= 0
    assert counter.read_text().count("run") == 1

    docker_stats_svc.snapshot(refresh=True)
    assert counter.read_text().count("run") == 2


def test_docker_cache_configurable_a_cero(tmp_path, monkeypatch):
    counter = tmp_path / "runs.txt"
    monkeypatch.setenv(
        "PATH",
        _fake_docker(
            tmp_path,
            f"echo run >> {counter}\nprintf 'netpulse-lab1\\t1MB / 2MB\\n'\n",
        ),
    )
    monkeypatch.setenv("NETPULSE_DOCKER_STATS_TTL", "0")
    docker_stats_svc.snapshot(refresh=True)
    docker_stats_svc.snapshot()
    assert counter.read_text().count("run") == 2


def test_docker_timeout_devuelve_estado_no_excepcion(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "PATH",
        _fake_docker(tmp_path, "exec /bin/sleep 2\n")
        + ":/usr/bin:/bin",  # /bin/sleep sigue disponible en Linux y macOS
    )
    monkeypatch.setenv("NETPULSE_DOCKER_STATS_TIMEOUT", "0.2")
    started = time.monotonic()
    snap = docker_stats_svc.snapshot(refresh=True)
    elapsed = time.monotonic() - started
    assert snap["available"] is False
    assert snap["reason"] == "timeout"
    assert elapsed < 1.5


def test_docker_error_cache_es_mas_corta(tmp_path, monkeypatch):
    counter = tmp_path / "runs.txt"
    monkeypatch.setenv(
        "PATH",
        _fake_docker(tmp_path, f"echo run >> {counter}\nexit 1\n"),
    )
    monkeypatch.setenv("NETPULSE_DOCKER_STATS_ERROR_TTL", "0")
    docker_stats_svc.snapshot(refresh=True)
    docker_stats_svc.snapshot()
    assert counter.read_text().count("run") == 2


def test_docker_traffic_compatibilidad(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "PATH", _fake_docker(tmp_path, "printf 'netpulse-lab1\\t1MB / 2MB\\n'\n")
    )
    traffic = docker_stats_svc.traffic(refresh=True)
    assert traffic["netpulse-lab1"] == ("1MB", "2MB")
    assert traffic["lab1"] == ("1MB", "2MB")


@pytest.mark.asyncio
async def test_docker_async_no_bloquea_el_loop(monkeypatch):
    """El comando tarda 200 ms; el loop debe seguir latiendo cada 20 ms."""
    def slow_run():
        time.sleep(0.2)
        return {
            "available": True, "reason": None, "detail": None,
            "containers": {"netpulse-x": {"rx": "1", "tx": "2"}}, "count": 1,
            "cached": False, "age_seconds": 0.0, "ttl_seconds": 8.0,
            "checked_at": "2026-01-01T00:00:00+00:00",
        }

    monkeypatch.setattr(docker_stats_svc, "_run", slow_run)
    gaps = []

    async def heartbeat():
        while True:
            t0 = time.perf_counter()
            await asyncio.sleep(0.02)
            gaps.append((time.perf_counter() - t0 - 0.02) * 1000)

    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.05)
    snap = await docker_stats_svc.snapshot_async(refresh=True)
    await asyncio.sleep(0.05)
    beat.cancel()
    assert snap["available"] is True
    assert max(gaps) < 100, f"event loop bloqueado {max(gaps):.1f} ms"


def test_docker_endpoint_docker_ausente_200(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    r = TestClient(app).get("/api/system/docker", headers=VIEWER)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is False
    assert body["reason"] == "docker_no_disponible"
    assert body["containers"] == {}


def test_docker_endpoint_docker_presente_200(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "PATH", _fake_docker(tmp_path, "printf 'netpulse-lab1\\t1MB / 2MB\\n'\n")
    )
    r = TestClient(app).get("/api/system/docker?refresh=true", headers=VIEWER)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is True
    assert body["containers"]["netpulse-lab1"] == {"rx": "1MB", "tx": "2MB"}


# ── endpoints de sistema: contrato y no bloqueo ──────────────


def test_host_metrics_contrato_intacto():
    r = TestClient(app).get("/api/system/host", headers=VIEWER)
    assert r.status_code == 200, r.text
    body = r.json()
    for key in (
        "hostname", "platform", "system", "release", "arch", "python",
        "boot_time", "uptime_seconds", "cpu", "memory", "disks", "network",
        "process", "processes_total", "connections_total", "timestamp",
    ):
        assert key in body
    assert set(body["cpu"]) >= {"percent", "per_cpu", "count_logical", "load1"}
    assert set(body["memory"]) >= {"total", "used", "percent", "swap_percent"}


def test_overview_logica_de_datos_intacta(monkeypatch):
    snapshot = {
        "running": True,
        "interval": 60,
        "last_run": "2026-01-01T00:00:00+00:00",
        "duration_ms": 12.5,
        "host": {"cpu": {"percent": 10}},
        "devices": {
            "a": {"online": True, "cpu": 20, "memory_percent": 40,
                  "disk_percent": 60, "latency_ms": 5},
            "b": {"online": True, "cpu": 40, "memory_percent": 60,
                  "disk_percent": 80, "latency_ms": 15},
            "c": {"online": False, "cpu": 99, "memory_percent": 99,
                  "disk_percent": 99, "latency_ms": 99},
        },
    }
    monkeypatch.setattr(metrics_poller, "snapshot", lambda: snapshot)
    r = TestClient(app).get("/api/system/overview", headers=VIEWER)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["host"] == {"cpu": {"percent": 10}}
    assert body["poller"]["running"] is True
    assert body["poller"]["duration_ms"] == 12.5
    assert body["devices_summary"] == {
        "total": 3,
        "online": 2,
        "offline": 1,
        "avg_cpu": 30.0,
        "avg_memory_percent": 50.0,
        "avg_disk_percent": 70.0,
        "avg_latency_ms": 10.0,
    }


def test_host_cache_single_flight(monkeypatch):
    calls = {"n": 0}
    sentinel = {"hostname": "cache-host", "cpu": {"percent": 1}}

    def slow_collect():
        calls["n"] += 1
        time.sleep(0.05)
        return sentinel

    monkeypatch.setattr(host_metrics_svc, "collect", slow_collect)
    host_metrics_svc.reset_cache()

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: host_metrics_svc.collect_cached(), range(8)))
    assert all(r is sentinel for r in results)
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_host_collect_async_timeout(monkeypatch):
    monkeypatch.setattr(host_metrics_svc, "collect", lambda: time.sleep(0.2))
    host_metrics_svc.reset_cache()
    with pytest.raises(asyncio.TimeoutError):
        await host_metrics_svc.collect_async(cached=False, timeout=0.05)


@pytest.mark.asyncio
async def test_host_endpoint_no_bloquea_el_loop(monkeypatch):
    slow_payload = {
        "hostname": "slow", "cpu": {}, "memory": {}, "disks": [],
        "network": [], "process": {}, "processes_total": 0,
        "connections_total": 0, "timestamp": "2026-01-01T00:00:00+00:00",
    }

    def slow_collect():
        time.sleep(0.25)
        return slow_payload

    monkeypatch.setattr(host_metrics_svc, "collect", slow_collect)
    host_metrics_svc.reset_cache()

    gaps = []

    async def heartbeat():
        while True:
            t0 = time.perf_counter()
            await asyncio.sleep(0.02)
            gaps.append((time.perf_counter() - t0 - 0.02) * 1000)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        beat = asyncio.create_task(heartbeat())
        await asyncio.sleep(0.05)
        r = await client.get("/api/system/host", headers=VIEWER)
        await asyncio.sleep(0.05)
        beat.cancel()

    assert r.status_code == 200
    assert r.json()["hostname"] == "slow"
    assert max(gaps) < 100, f"event loop bloqueado {max(gaps):.1f} ms"


@pytest.mark.asyncio
async def test_metrics_endpoint_no_bloquea_el_loop(monkeypatch):
    """psutil (100 ms) ya no se ejecuta en línea dentro de /api/metrics."""
    def slow_collect():
        time.sleep(0.25)
        return {"cpu": {"percent": 5}, "memory": {}, "disks": [], "network": [],
                "process": {}, "processes_total": 1, "connections_total": 0,
                "uptime_seconds": 1}

    monkeypatch.setattr(host_metrics_svc, "collect", slow_collect)

    gaps = []

    async def heartbeat():
        while True:
            t0 = time.perf_counter()
            await asyncio.sleep(0.02)
            gaps.append((time.perf_counter() - t0 - 0.02) * 1000)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        beat = asyncio.create_task(heartbeat())
        await asyncio.sleep(0.05)
        r = await client.get("/api/metrics")
        await asyncio.sleep(0.05)
        beat.cancel()

    assert r.status_code == 200
    assert "netpulse_host_cpu_percent" in r.text
    assert max(gaps) < 100, f"event loop bloqueado {max(gaps):.1f} ms"


# ── regresión: monitoring y poller ───────────────────────────


def test_monitoring_overview_sigue_respondiendo():
    r = TestClient(app).get("/api/system/overview", headers=VIEWER)
    assert r.status_code == 200
    assert "devices_summary" in r.json()


def test_system_requires_auth():
    client = TestClient(app)
    for path in ("/api/system/host", "/api/system/overview", "/api/system/docker"):
        assert client.get(path).status_code in (401, 403)


def test_system_devices_shape():
    r = TestClient(app).get("/api/system/devices", headers=VIEWER)
    assert r.status_code == 200
    body = r.json()
    assert "devices" in body and "summary" in body
    assert set(body["summary"]) == {
        "total", "online", "offline", "avg_cpu", "avg_memory_percent",
        "avg_disk_percent", "avg_latency_ms",
    }
