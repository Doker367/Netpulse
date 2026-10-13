"""Tests de la Fase 4 (rendimiento): batching syslog, sondeo paralelo, semáforos."""

import queue
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import concurrency
from app.services.collectors import metrics_poller as mp
from app.services.collectors import syslog_svc as ss


# ── 4.1 Batching syslog ───────────────────────────────────────

def _drain():
    while not ss._queue.empty():
        try:
            ss._queue.get_nowait()
        except queue.Empty:
            break


def test_parse_rfc3164_basico():
    parsed = ss.parse_rfc3164(b"<134>Oct 13 10:00:00 r1 sshd[123]: login failed")
    assert parsed["severity"] == "info"
    assert parsed["host"] == "r1"
    assert "login failed" in parsed["content"]


def test_enqueue_y_flush_agrupa_por_stream(monkeypatch):
    _drain()
    captured = {}
    monkeypatch.setattr(ss, "_post_loki", lambda streams: captured.setdefault("streams", streams) or True)

    s1 = {"container": "syslog", "source": "r1", "severity": "info", "facility": "0"}
    s2 = {"container": "syslog", "source": "r2", "severity": "err", "facility": "0"}
    ss.enqueue(s1, "m1")
    ss.enqueue(s1, "m2")
    ss.enqueue(s2, "m3")

    total = ss._flush_once()
    assert total == 3
    # 2 streams agrupados, 3 valores en total
    assert len(captured["streams"]) == 2
    assert sum(len(x["values"]) for x in captured["streams"]) == 3


def test_enqueue_descarta_si_cola_llena(monkeypatch):
    monkeypatch.setattr(ss, "_queue", queue.Queue(maxsize=1))
    assert ss.enqueue({"container": "syslog"}, "a") is True
    assert ss.enqueue({"container": "syslog"}, "b") is False
    # contador de descartes
    assert ss.stats()["dropped"] >= 1


# ── 4.3 Semáforo por dispositivo ──────────────────────────────

def test_sem_por_dispositivo_es_estable():
    concurrency.reset_for_tests()
    a1 = concurrency._sem_for("devA")
    a2 = concurrency._sem_for("devA")
    b = concurrency._sem_for("devB")
    assert a1 is a2
    assert a1 is not b


def test_limited_usa_el_dispositivo():
    concurrency.reset_for_tests()

    @concurrency.limited
    def f(device):
        return device

    assert f({"id": "x"}) == {"id": "x"}
    assert f("sin-dict") == "sin-dict"  # cae al semáforo global


# ── 4.2 Sondeo de métricas en paralelo ────────────────────────

def test_poll_all_paralelo(monkeypatch):
    monkeypatch.setattr(mp.inventory_svc, "list_devices",
                        lambda: [{"id": "a"}, {"id": "b"}, {"id": "c"}])
    monkeypatch.setattr(mp, "collect_device",
                        lambda did: {"device_id": did, "online": True})
    monkeypatch.setattr(mp.host_metrics_svc, "update_prometheus", lambda: {})
    monkeypatch.setattr(mp, "_record_history", lambda *a, **k: None)

    snap = mp.poll_all()
    assert set(snap["devices"].keys()) == {"a", "b", "c"}
    assert all(v["online"] for v in snap["devices"].values())
