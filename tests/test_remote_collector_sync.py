"""Contrato de sync: lote, idempotencia, rechazos, auth, backoff y heartbeat."""

import json

import httpx
import pytest

from app.remote_collector.buffer import Buffer
from app.remote_collector.config import (
    BufferConfig,
    CentralConfig,
    CollectorConfig,
    DeviceSpec,
    ProbeConfig,
    SyncConfig,
)
from app.remote_collector.sync import (
    AuthError,
    ContractError,
    PayloadTooLarge,
    SyncClient,
    SyncError,
    next_delay,
    sync_once,
)
from app.remote_collector.timeutil import iso_now
from app.services.collectors.probe_svc import ProbeResult


def config(tmp_path):
    return CollectorConfig(
        schema_version=1,
        site_id="site-lima",
        collector_id="collector-lima-01",
        central=CentralConfig(
            "https://noc.example.net", "NETPULSE_COLLECTOR_API_KEY", None, True, None, 5,
        ),
        probe=ProbeConfig(60, 1500, 2, 1),
        buffer=BufferConfig(tmp_path / "buffer.sqlite3", 500000, 72, 6, 200),
        sync=SyncConfig(30, 60, 200, 5, 300),
        devices=(DeviceSpec("sw-1", "10.0.0.1", 22, "ssh", "ios"),),
    )


def fill(buf):
    buf.insert_probe(
        iso_now(),
        [(DeviceSpec("sw-1", "10.0.0.1", 22, "ssh", "ios"),
          ProbeResult(True, True, 1.2, None, "tcp"))],
    )
    rows = buf.peek_pending(10)
    triples = [(r["device_id"], r["ts"], r["metric"]) for r in rows]
    return rows, triples


def client(cfg, handler):
    return SyncClient(cfg, "np_test", transport=httpx.MockTransport(handler))


def test_success_marks_synced_and_sends_contract(tmp_path):
    cfg = config(tmp_path)
    buf = Buffer(cfg.buffer)
    rows, _ = fill(buf)
    captured = {}

    def handler(request):
        captured["path"] = request.url.path
        captured["auth"] = request.headers["authorization"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "status": "ok", "accepted": 2, "duplicates": 0, "rejected": 0,
            "rejected_samples": [], "out_of_order": 0,
        })

    response = sync_once(buf, client(cfg, handler), cfg)
    assert captured["path"] == "/api/remote/ingest"
    assert captured["auth"] == "Bearer np_test"
    body = captured["body"]
    assert body["schema_version"] == 1
    # El site y el collector se derivan de la clave, no viajan en el body.
    assert "site_id" not in body and "collector_id" not in body
    assert len(body["samples"]) == 2
    assert body["buffer"]["pending"] == 2
    assert set(body["sync"]) == {
        "last_error", "last_error_at", "consecutive_failures", "auth_state",
    }
    assert "last_success_at" not in body["sync"]  # local-only, forbidden by central
    assert response["marked_synced"] == 2
    assert buf.counts()["pending"] == 0


def test_retry_is_idempotent_on_client_side(tmp_path):
    cfg = config(tmp_path)
    buf = Buffer(cfg.buffer)
    fill(buf)
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={
            "status": "ok", "accepted": 0, "duplicates": 2, "rejected": 0,
            "rejected_samples": [],
        })

    client_obj = client(cfg, handler)
    assert sync_once(buf, client_obj, cfg)["marked_synced"] == 2
    assert buf.counts()["pending"] == 0
    # Si el proceso no hubiera marcado, el reenvío sería un duplicado del servidor.
    assert len(calls) == 1


def test_partial_reject_marks_dead(tmp_path):
    cfg = config(tmp_path)
    buf = Buffer(cfg.buffer)
    rows, triples = fill(buf)
    rejected = [{
        "device_id": triples[0][0], "ts": triples[0][1],
        "metric": triples[0][2], "reason": "valor inválido",
    }]

    def handler(request):
        return httpx.Response(200, json={
            "status": "partial", "accepted": 1, "duplicates": 0, "rejected": 1,
            "rejected_samples": rejected,
        })

    response = sync_once(buf, client(cfg, handler), cfg)
    assert response["marked_dead"] == 1
    assert response["marked_synced"] == 1
    assert buf.counts() == {"pending": 0, "synced": 1, "dead": 1, "total": 2}


def test_rejected_count_mismatch_keeps_pending(tmp_path):
    cfg = config(tmp_path)
    buf = Buffer(cfg.buffer)
    fill(buf)

    def handler(request):
        return httpx.Response(200, json={
            "status": "partial", "accepted": 1, "duplicates": 0, "rejected": 1,
            "rejected_samples": [],
        })

    with pytest.raises(ContractError):
        sync_once(buf, client(cfg, handler), cfg)
    assert buf.counts()["pending"] == 2
    assert buf.sync_state()["consecutive_failures"] == 1


def test_auth_error_keeps_pending_and_marks_auth(tmp_path):
    cfg = config(tmp_path)
    buf = Buffer(cfg.buffer)
    fill(buf)

    def handler(request):
        return httpx.Response(401, json={"detail": "API key inválida o revocada"})

    with pytest.raises(AuthError):
        sync_once(buf, client(cfg, handler), cfg)
    assert buf.counts()["pending"] == 2
    state = buf.sync_state()
    assert state["auth_state"] == "error"
    assert state["consecutive_failures"] == 1


def test_413_raises_payload_too_large(tmp_path):
    cfg = config(tmp_path)
    buf = Buffer(cfg.buffer)
    fill(buf)

    def handler(request):
        return httpx.Response(413, json={"detail": "demasiado grande"})

    with pytest.raises(PayloadTooLarge):
        sync_once(buf, client(cfg, handler), cfg)
    assert buf.counts()["pending"] == 2


def test_5xx_is_sync_error(tmp_path):
    cfg = config(tmp_path)
    buf = Buffer(cfg.buffer)
    fill(buf)

    def handler(request):
        return httpx.Response(503, json={"detail": "DB no disponible"})

    with pytest.raises(SyncError):
        sync_once(buf, client(cfg, handler), cfg)
    assert buf.counts()["pending"] == 2


def test_heartbeat_empty_batch(tmp_path):
    cfg = config(tmp_path)
    buf = Buffer(cfg.buffer)
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={
            "status": "ok", "accepted": 0, "duplicates": 0, "rejected": 0,
            "rejected_samples": [],
        })

    response = sync_once(buf, client(cfg, handler), cfg, force_heartbeat=True)
    assert response is not None
    assert sent[0]["samples"] == []
    # Un segundo intento inmediato no manda heartbeat (todavía no toca).
    assert sync_once(buf, client(cfg, handler), cfg) is None


def test_next_delay_is_bounded_and_jittered():
    assert next_delay(1, 5, 300, rng=lambda a, b: 1.0) == 10
    assert next_delay(10, 5, 300, rng=lambda a, b: 1.0) == 300
    assert next_delay(3, 5, 300, rng=lambda a, b: 0.5) == 20
    assert next_delay(3, 5, 300, rng=lambda a, b: 1.5) == 60
