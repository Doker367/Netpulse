"""Regresión de la tarjeta 13C: últimos I/O bloqueantes del event loop.

Cubre, con la red **mockeada** (paramiko, SMTP/Slack/webhooks, SQLite,
devices.yaml y list_devices), que:

- SFTP (listar/subir/descargar), el webhook de Alertmanager, la ingesta de
  colectores, el sync de NetBox y ``/api/health`` no bloquean el event loop;
- un fallo o un timeout de esas operaciones no deja el loop secuestrado ni
  cambia el contrato de respuesta (códigos y ``detail``);
- ``run_blocking`` distingue su propio tope de un ``socket.timeout`` real
  (clasificación de errores intacta).

El método de medida es el mismo que en 13B: un latido ``asyncio.sleep(20 ms)``
compartiendo el loop; si un handler hace I/O en línea, el hueco entre ticks
crece hasta la duración del I/O. Se exige menos de 100 ms.
"""

from __future__ import annotations

import asyncio
import socket
import stat
import time
from contextlib import suppress
from unittest.mock import MagicMock, patch

import httpx
import paramiko  # noqa: F401 — precarga el import para no medirlo en el hilo
import pytest

from app.core.security import create_access_token
from app.main import app, reset_health_cache
from app.routers import netbox as netbox_router
from app.routers import remote as remote_router
from app.services import napalm_svc, notifications_svc
from app.services.ops import maintenance_svc
from app.services.remote import ingest_svc, store
from app.utils.async_io import BlockingCallTimeout, env_timeout, run_blocking

ADMIN = {"Authorization": "Bearer " + create_access_token({"sub": "admin", "role": "admin"})}
LOOP_BUDGET_MS = 100.0

DEVICE = {
    "id": "mock_switch",
    "hostname": "192.0.2.10",
    "protocol": "ssh",
    "driver": "ios",
    "port": 22,
    "credentials": {"username": "admin", "password": "secret"},
}


# ── utilidades ───────────────────────────────────────────────


async def _loop_gaps_while(awaitable, interval: float = 0.02):
    """Espera ``awaitable`` midiendo el hueco máximo entre ticks del loop."""
    gaps: list[float] = []

    async def heartbeat():
        while True:
            t0 = time.perf_counter()
            await asyncio.sleep(interval)
            gaps.append((time.perf_counter() - t0 - interval) * 1000)

    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.05)
    try:
        result = await awaitable
    finally:
        await asyncio.sleep(0.03)
        beat.cancel()
        with suppress(asyncio.CancelledError):
            await beat
    return result, max(gaps or [0.0])


def _client() -> httpx.AsyncClient:
    """Cliente ASGI directo: el loop del test es el loop de la aplicación."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _ssh(sftp):
    ssh = MagicMock()
    ssh.open_sftp.return_value = sftp
    return ssh


def _sftp(*, putfo=None, listdir=None, stat_open=None):
    """SFTP falso; cada operación puede tardar/fallar lo que pida el test."""
    sftp = MagicMock()
    sftp.putfo.side_effect = putfo or (lambda fileobj, path: None)
    sftp.listdir_attr.side_effect = listdir or (lambda path: [])
    sftp.stat.side_effect = stat_open or (lambda path: _attr())
    return sftp


def _attr(size=5, mode=stat.S_IFREG | 0o644):
    attr = MagicMock()
    attr.filename = "bench.cfg"
    attr.st_mode = mode
    attr.st_size = size
    attr.st_mtime = 0
    return attr


def _patched_sftp(ssh):
    return (
        patch.object(napalm_svc, "_find_device", lambda device_id: dict(DEVICE)),
        patch("paramiko.SSHClient", return_value=ssh),
    )


def _assert_loop_free(gaps_max: float, what: str) -> None:
    assert gaps_max < LOOP_BUDGET_MS, f"{what} bloqueó el loop {gaps_max:.1f} ms"


# ── helper run_blocking ─────────────────────────────────────


@pytest.mark.asyncio
async def test_run_blocking_ejecuta_fuera_del_loop():
    def slow():
        time.sleep(0.2)
        return 42

    result, gaps = await _loop_gaps_while(run_blocking(slow, timeout=5))
    assert result == 42
    _assert_loop_free(gaps, "run_blocking")


@pytest.mark.asyncio
async def test_run_blocking_timeout_es_excepcion_propia():
    def slow():
        time.sleep(0.3)

    with pytest.raises(BlockingCallTimeout) as exc:
        await run_blocking(slow, timeout=0.05, label="trabajo de prueba")
    assert "trabajo de prueba" in str(exc.value)


@pytest.mark.asyncio
async def test_run_blocking_no_enmascara_un_timeout_de_la_libreria():
    """``socket.timeout`` es ``TimeoutError``: no debe convertirse en el tope."""
    def raises():
        raise socket.timeout("timed out")

    with pytest.raises(TimeoutError) as exc:
        await run_blocking(raises, timeout=5)
    assert not isinstance(exc.value, BlockingCallTimeout)


def test_env_timeout_ignora_valores_invalidos(monkeypatch):
    assert env_timeout("NETPULSE_TEST_TIMEOUT_X", 7.0) == 7.0
    monkeypatch.setenv("NETPULSE_TEST_TIMEOUT_X", "basura")
    assert env_timeout("NETPULSE_TEST_TIMEOUT_X", 7.0) == 7.0
    monkeypatch.setenv("NETPULSE_TEST_TIMEOUT_X", "0")
    assert env_timeout("NETPULSE_TEST_TIMEOUT_X", 7.0) == 7.0
    monkeypatch.setenv("NETPULSE_TEST_TIMEOUT_X", "1.5")
    assert env_timeout("NETPULSE_TEST_TIMEOUT_X", 7.0) == 1.5


@pytest.mark.asyncio
async def test_run_blocking_libera_recursos_que_llegan_tarde():
    """Con ``cleanup_on_timeout`` el resultado tardío se cierra, no se filtra."""
    closed: list[tuple] = []

    def slow_session():
        time.sleep(0.15)
        return ("cliente-ssh", "sftp")

    with pytest.raises(BlockingCallTimeout):
        await run_blocking(
            slow_session,
            timeout=0.05,
            cleanup_on_timeout=lambda result: closed.append(result),
        )

    for _ in range(50):
        if closed:
            break
        await asyncio.sleep(0.02)
    assert closed == [("cliente-ssh", "sftp")]


# ── SFTP ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sftp_upload_no_bloquea_el_loop():
    """putfo tarda 300 ms: la subida espera, el loop no."""
    sftp = _sftp(putfo=lambda fileobj, path: time.sleep(0.3))
    ssh = _ssh(sftp)

    async with _client() as client:
        patches = _patched_sftp(ssh)
        with patches[0], patches[1]:
            # calentamiento (executor + import dentro del hilo)
            await client.post("/api/sftp/mock_switch/upload?filename=w.cfg", headers=ADMIN, content=b"x")
            response, gaps = await _loop_gaps_while(
                client.post(
                    "/api/sftp/mock_switch/upload?filename=bench.cfg",
                    headers=ADMIN,
                    content=b"hostname bench\n",
                )
            )

    assert response.status_code == 200, response.text
    assert response.json()["remote_path"] == "/bench.cfg"
    assert response.json()["size_bytes"] == len(b"hostname bench\n")
    _assert_loop_free(gaps, "la subida SFTP")


@pytest.mark.asyncio
async def test_sftp_upload_timeout_devuelve_504_honesto(monkeypatch):
    """La operación completa (kex + transferencia) tiene tope duro."""
    monkeypatch.setenv("NETPULSE_SFTP_CONNECT_TIMEOUT_S", "0.1")
    monkeypatch.setenv("NETPULSE_SFTP_TRANSFER_TIMEOUT_S", "0.05")
    sftp = _sftp(putfo=lambda fileobj, path: time.sleep(0.5))
    ssh = _ssh(sftp)

    async with _client() as client:
        patches = _patched_sftp(ssh)
        with patches[0], patches[1]:
            response = await client.post(
                "/api/sftp/mock_switch/upload?filename=slow.cfg",
                headers=ADMIN,
                content=b"x",
            )

    assert response.status_code == 504
    assert "NETPULSE_SFTP_TRANSFER_TIMEOUT_S" in response.json()["detail"]


@pytest.mark.asyncio
async def test_sftp_upload_clasifica_el_error_y_no_bloquea():
    """Un EACCES lento sigue siendo 403 (no 504) y el loop queda libre."""

    def denied(fileobj, path):
        time.sleep(0.2)
        raise PermissionError(13, "Permission denied")

    sftp = _sftp(putfo=denied)
    ssh = _ssh(sftp)

    async with _client() as client:
        patches = _patched_sftp(ssh)
        with patches[0], patches[1]:
            response, gaps = await _loop_gaps_while(
                client.post(
                    "/api/sftp/mock_switch/upload?filename=x.cfg",
                    headers=ADMIN,
                    content=b"x",
                )
            )

    assert response.status_code == 403
    assert response.json()["detail"] == "Permiso denegado al escribir /x.cfg en mock_switch"
    _assert_loop_free(gaps, "el error de subida SFTP")


@pytest.mark.asyncio
async def test_sftp_list_no_bloquea_el_loop():
    sftp = _sftp(listdir=lambda path: (time.sleep(0.25), [_attr()])[1])
    ssh = _ssh(sftp)

    async with _client() as client:
        patches = _patched_sftp(ssh)
        with patches[0], patches[1]:
            response, gaps = await _loop_gaps_while(
                client.get("/api/sftp/mock_switch/list?path=/", headers=ADMIN)
            )

    assert response.status_code == 200
    assert response.json()["items"][0]["name"] == "bench.cfg"
    _assert_loop_free(gaps, "el listado SFTP")


@pytest.mark.asyncio
async def test_sftp_download_no_bloquea_el_loop():
    remote_file = MagicMock()
    remote_file.read.side_effect = [b"hostname bench\n", b""]
    sftp = _sftp(stat_open=lambda path: (time.sleep(0.2), _attr(size=15))[1])
    sftp.open.return_value.__enter__.return_value = remote_file
    ssh = _ssh(sftp)

    async with _client() as client:
        patches = _patched_sftp(ssh)
        with patches[0], patches[1]:
            response, gaps = await _loop_gaps_while(
                client.get(
                    "/api/sftp/mock_switch/download?remote_path=/bench.cfg",
                    headers=ADMIN,
                )
            )

    assert response.status_code == 200
    assert response.content == b"hostname bench\n"
    _assert_loop_free(gaps, "la descarga SFTP")


# ── Alertmanager webhook ────────────────────────────────────

WEBHOOK = {
    "version": "4",
    "status": "firing",
    "alerts": [
        {
            "status": "firing",
            "labels": {"alertname": "DeviceDown", "severity": "critical", "device_id": "sw-1"},
            "annotations": {"summary": "Equipo caído", "description": "sin respuesta"},
        }
    ],
}


@pytest.mark.asyncio
async def test_webhook_no_bloquea_el_loop(monkeypatch):
    calls: list[str] = []

    def slow_notify(event_type, data):
        calls.append(event_type)
        time.sleep(0.3)

    monkeypatch.setattr(maintenance_svc, "active_windows", lambda: [])
    monkeypatch.setattr(notifications_svc, "notify_event", slow_notify)

    async with _client() as client:
        response, gaps = await _loop_gaps_while(
            client.post("/api/alerts/webhook", json=WEBHOOK, headers=ADMIN)
        )

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "received": 1, "delivered": 1, "suppressed": 0}
    assert calls == ["alert_critical_firing"]
    _assert_loop_free(gaps, "el webhook")


@pytest.mark.asyncio
async def test_webhook_fallo_de_notificacion_no_rompe_la_respuesta(monkeypatch):
    def boom(event_type, data):
        raise RuntimeError("SMTP no disponible")

    monkeypatch.setattr(maintenance_svc, "active_windows", lambda: [])
    monkeypatch.setattr(notifications_svc, "notify_event", boom)

    async with _client() as client:
        response = await client.post("/api/alerts/webhook", json=WEBHOOK, headers=ADMIN)

    assert response.status_code == 200
    assert response.json()["delivered"] == 0
    assert response.json()["received"] == 1


@pytest.mark.asyncio
async def test_webhook_ventanas_lentas_degradan_sin_romper(monkeypatch):
    """Si el disco tarda, se entrega igual (mejor duplicar que silenciar)."""
    monkeypatch.setenv("NETPULSE_ALERT_WINDOWS_TIMEOUT_S", "0.05")

    def slow_windows():
        time.sleep(0.3)
        return [{"device_id": "sw-1"}]

    delivered: list[str] = []
    monkeypatch.setattr(maintenance_svc, "active_windows", slow_windows)
    monkeypatch.setattr(
        notifications_svc, "notify_event", lambda event_type, data: delivered.append(event_type)
    )

    async with _client() as client:
        response, gaps = await _loop_gaps_while(
            client.post("/api/alerts/webhook", json=WEBHOOK, headers=ADMIN)
        )

    assert response.status_code == 200
    assert response.json()["suppressed"] == 0
    assert response.json()["delivered"] == 1
    assert delivered == ["alert_critical_firing"]
    _assert_loop_free(gaps, "la lectura de ventanas")


@pytest.mark.asyncio
async def test_webhook_suprime_en_mantenimiento(monkeypatch):
    """La ventana activa se sigue respetando (semántica intacta)."""
    called: list[str] = []
    monkeypatch.setattr(maintenance_svc, "active_windows", lambda: [{"device_id": "sw-1"}])
    monkeypatch.setattr(
        notifications_svc, "notify_event", lambda event_type, data: called.append(event_type)
    )

    async with _client() as client:
        response = await client.post("/api/alerts/webhook", json=WEBHOOK, headers=ADMIN)

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "received": 1, "delivered": 0, "suppressed": 1}
    assert called == []


# ── Ingesta de colectores remotos ───────────────────────────

INGEST_PAYLOAD = {
    "schema_version": 1,
    "collector_version": "0.1.0",
    "probe_interval_s": 60,
    "sync_batch_id": "batch-bench",
    "sent_at": "2026-10-23T10:00:00+00:00",
    "buffer": {
        "buffer_epoch": "epoch-1",
        "pending": 1,
        "oldest_pending_ts": None,
        "dropped_total": 0,
        "gap_since": None,
        "paused_reason": None,
    },
    "samples": [
        {
            "device_id": "sw-1",
            "ts": "2026-10-23T10:00:00+00:00",
            "metric": "reachable",
            "value_bool": True,
            "value_num": None,
            "ok": True,
            "probe_kind": "tcp",
            "error": None,
        }
    ],
}


@pytest.fixture()
def collector_override():
    """Evita depender de api_keys.json: la auth del colector ya tiene sus tests."""
    app.dependency_overrides[remote_router.collector_auth] = lambda: {
        "site_id": "site-bench",
        "collector_id": "collector-bench",
        "name": "bench",
        "key_id": 1,
    }
    yield
    app.dependency_overrides.pop(remote_router.collector_auth, None)


@pytest.mark.asyncio
async def test_ingest_no_bloquea_el_loop(monkeypatch, collector_override):
    def slow_ingest(site_id, collector_id, payload, remote_addr=None):
        time.sleep(0.25)
        return {"accepted": 1}

    monkeypatch.setattr(ingest_svc, "ingest", slow_ingest)

    async with _client() as client:
        response, gaps = await _loop_gaps_while(
            client.post("/api/remote/ingest", json=INGEST_PAYLOAD, headers=ADMIN)
        )

    assert response.status_code == 200
    assert response.json() == {"accepted": 1}
    _assert_loop_free(gaps, "la ingesta")


@pytest.mark.asyncio
async def test_ingest_almacen_caido_sigue_siendo_503(monkeypatch, collector_override):
    def unavailable(site_id, collector_id, payload, remote_addr=None):
        raise store.RemoteStoreUnavailable("database is locked")

    monkeypatch.setattr(ingest_svc, "ingest", unavailable)

    async with _client() as client:
        response = await client.post("/api/remote/ingest", json=INGEST_PAYLOAD, headers=ADMIN)

    assert response.status_code == 503
    assert "Almacén remoto no disponible" in response.json()["detail"]


@pytest.mark.asyncio
async def test_ingest_timeout_devuelve_504(monkeypatch, collector_override):
    monkeypatch.setenv("NETPULSE_REMOTE_INGEST_TIMEOUT_S", "0.05")

    def slow_ingest(site_id, collector_id, payload, remote_addr=None):
        time.sleep(0.4)
        return {"accepted": 1}

    monkeypatch.setattr(ingest_svc, "ingest", slow_ingest)

    async with _client() as client:
        response = await client.post("/api/remote/ingest", json=INGEST_PAYLOAD, headers=ADMIN)

    assert response.status_code == 504
    assert "idempotente" in response.json()["detail"]


# ── NetBox sync ─────────────────────────────────────────────

NB_DEVICE = {
    "id": 7,
    "name": "edge-7",
    "primary_ip": {"address": "10.0.0.7/24"},
    "device_type": {"model": "C9300", "manufacturer": {"name": "Cisco"}},
    "role": {"name": "Switch", "slug": "switch"},
    "site": {"name": "DC1", "slug": "dc1"},
    "status": {"value": "active"},
    "comments": "",
}


@pytest.fixture()
def netbox_configured(monkeypatch):
    monkeypatch.setattr(netbox_router, "NETBOX_URL", "http://netbox.test:8000")
    monkeypatch.setattr(netbox_router, "NETBOX_TOKEN", "nbt_bench.token")

    async def fake_paginated(path, params=None):
        return [dict(NB_DEVICE)] if path == "/dcim/devices/" else []

    monkeypatch.setattr(netbox_router, "_netbox_paginated", fake_paginated)


@pytest.mark.asyncio
async def test_netbox_sync_lee_yaml_fuera_del_loop(monkeypatch, netbox_configured):
    def slow_read():
        time.sleep(0.25)
        return {"devices": [], "options": {}}

    monkeypatch.setattr(netbox_router, "_read_devices_yaml", slow_read)
    monkeypatch.setattr(netbox_router, "_write_devices_yaml", lambda data: None)

    async with _client() as client:
        response, gaps = await _loop_gaps_while(
            client.post("/api/netbox/sync", headers=ADMIN)
        )

    assert response.status_code == 200, response.text
    assert response.json()["creados"] == ["netbox-7"]
    _assert_loop_free(gaps, "la lectura de devices.yaml")


@pytest.mark.asyncio
async def test_netbox_sync_escribe_yaml_fuera_del_loop(monkeypatch, netbox_configured):
    written: list[dict] = []

    def slow_write(data):
        time.sleep(0.25)
        written.append(data)

    monkeypatch.setattr(netbox_router, "_read_devices_yaml", lambda: {"devices": [], "options": {}})
    monkeypatch.setattr(netbox_router, "_write_devices_yaml", slow_write)

    async with _client() as client:
        response, gaps = await _loop_gaps_while(
            client.post("/api/netbox/sync", headers=ADMIN)
        )

    assert response.status_code == 200, response.text
    assert len(written) == 1  # se escribió una sola vez (atómico, igual que antes)
    _assert_loop_free(gaps, "la escritura de devices.yaml")


@pytest.mark.asyncio
async def test_netbox_sync_timeout_de_lectura_504(monkeypatch, netbox_configured):
    monkeypatch.setenv("NETPULSE_INVENTORY_IO_TIMEOUT_S", "0.05")

    def slow_read():
        time.sleep(0.3)
        return {"devices": [], "options": {}}

    monkeypatch.setattr(netbox_router, "_read_devices_yaml", slow_read)
    monkeypatch.setattr(netbox_router, "_write_devices_yaml", lambda data: None)

    async with _client() as client:
        response = await client.post("/api/netbox/sync", headers=ADMIN)

    assert response.status_code == 504
    assert "NETPULSE_INVENTORY_IO_TIMEOUT_S" in response.json()["detail"]


# ── /api/health ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_no_bloquea_y_caliente_es_rapido(monkeypatch):
    """La lectura lenta del inventario no congela el loop y la caché la evita."""
    reads: list[int] = []

    def slow_list_devices():
        reads.append(1)
        time.sleep(0.25)
        return [{"id": f"d{i}"} for i in range(7)]

    monkeypatch.setattr("app.main.list_devices", slow_list_devices)
    reset_health_cache()
    headers = {"X-Forwarded-For": "10.13.0.1"}

    async with _client() as client:
        response, gaps = await _loop_gaps_while(client.get("/api/health", headers=headers))
        assert response.status_code == 200
        assert response.json()["devices_configured"] == 7
        _assert_loop_free(gaps, "la primera lectura del health")

        # Segunda llamada: dentro del TTL no se vuelve a leer.
        t0 = time.perf_counter()
        warm = await client.get("/api/health", headers=headers)
        elapsed_ms = (time.perf_counter() - t0) * 1000

    assert warm.status_code == 200
    assert warm.json()["devices_configured"] == 7
    assert elapsed_ms < 100, f"health caliente tardó {elapsed_ms:.1f} ms"
    assert len(reads) == 1

    reset_health_cache()


@pytest.mark.asyncio
async def test_health_inventario_roto_no_rompe_ni_bloquea(monkeypatch):
    def broken():
        time.sleep(0.2)
        raise RuntimeError("devices.yaml corrupto")

    monkeypatch.setattr("app.main.list_devices", broken)
    reset_health_cache()

    async with _client() as client:
        response, gaps = await _loop_gaps_while(
            client.get("/api/health", headers={"X-Forwarded-For": "10.13.0.2"})
        )

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert response.json()["devices_configured"] == 0
    _assert_loop_free(gaps, "el inventario roto")
    reset_health_cache()
