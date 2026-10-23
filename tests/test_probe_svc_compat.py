"""Regresión del adaptador del poller central sobre probe_svc."""

from app.services.collectors import metrics_poller, probe_svc
from app.services.collectors.probe_svc import ProbeResult


def test_empty_device_legacy_contract():
    assert metrics_poller._probe_latency(None) == (0.0, 100.0)
    assert metrics_poller._probe_latency({}) == (0.0, 100.0)


def test_icmp_success_legacy_contract(monkeypatch):
    monkeypatch.setattr(
        metrics_poller.probe_svc,
        "probe",
        lambda *a, **k: ProbeResult(True, True, 1.25, 0.0, "icmp"),
    )
    assert metrics_poller._probe_latency(
        {"hostname": "10.0.0.1", "port": 161, "protocol": "snmp"}
    ) == (1.25, 0.0)


def test_icmp_failure_legacy_contract(monkeypatch):
    monkeypatch.setattr(
        metrics_poller.probe_svc,
        "probe",
        lambda *a, **k: ProbeResult(True, False, None, 100.0, "icmp", "sin respuesta ICMP"),
    )
    assert metrics_poller._probe_latency(
        {"hostname": "10.0.0.1", "port": 161, "protocol": "snmp"}
    ) == (0.0, 100.0)


def test_tcp_success_legacy_contract(monkeypatch):
    monkeypatch.setattr(
        metrics_poller.probe_svc,
        "probe",
        lambda *a, **k: ProbeResult(True, True, 2.5, None, "tcp"),
    )
    assert metrics_poller._probe_latency(
        {"hostname": "10.0.0.2", "port": 22, "protocol": "ssh"}
    ) == (2.5, 0.0)


def test_tcp_unreachable_is_loss_100_legacy(monkeypatch):
    monkeypatch.setattr(
        metrics_poller.probe_svc,
        "probe",
        lambda *a, **k: ProbeResult(True, False, None, None, "tcp", "timeout TCP"),
    )
    assert metrics_poller._probe_latency(
        {"hostname": "10.0.0.2", "port": 22, "protocol": "ssh"}
    ) == (0.0, 100.0)


def test_unmeasured_is_loss_100_legacy(monkeypatch):
    monkeypatch.setattr(
        metrics_poller.probe_svc,
        "probe",
        lambda *a, **k: ProbeResult(False, None, None, None, "tcp", "DNS: fallo"),
    )
    assert metrics_poller._probe_latency(
        {"hostname": "no-existe", "port": 22, "protocol": "ssh"}
    ) == (0.0, 100.0)


def _completed(returncode=0, stdout="", stderr=""):
    import subprocess

    return subprocess.CompletedProcess(args=["ping"], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


def test_icmp_probe_success(monkeypatch):
    monkeypatch.setattr(
        probe_svc.subprocess, "run",
        lambda *a, **k: _completed(0, "64 bytes from 10.0.0.1: time=1.23 ms"),
    )
    result = probe_svc.probe("10.0.0.1", 161, "snmp", timeout_ms=1000)
    assert result.ok is True
    assert result.reachable is True
    assert result.latency_ms == 1.23
    assert result.packet_loss_pct == 0.0
    assert result.probe_kind == "icmp"


def test_icmp_probe_no_reply(monkeypatch):
    monkeypatch.setattr(probe_svc.subprocess, "run", lambda *a, **k: _completed(1))
    result = probe_svc.probe("10.0.0.1", 161, "snmp", timeout_ms=1000)
    assert result.ok is True
    assert result.reachable is False
    assert result.latency_ms is None
    assert result.packet_loss_pct == 100.0
    assert "sin respuesta" in result.error


def test_icmp_probe_no_binary(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(probe_svc.subprocess, "run", boom)
    result = probe_svc.probe("10.0.0.1", 161, "snmp", timeout_ms=1000)
    assert result.ok is False
    assert result.reachable is None
    assert "ping no disponible" in result.error


def test_icmp_probe_permission(monkeypatch):
    monkeypatch.setattr(
        probe_svc.subprocess, "run",
        lambda *a, **k: _completed(2, stderr="ping: socket: Operation not permitted"),
    )
    result = probe_svc.probe("10.0.0.1", 161, "snmp", timeout_ms=1000)
    assert result.ok is False
    assert result.reachable is None
    assert "permiso" in result.error


class _FakeSocket:
    def close(self):
        return None


def test_tcp_probe_success(monkeypatch):
    monkeypatch.setattr(probe_svc.socket, "create_connection", lambda *a, **k: _FakeSocket())
    result = probe_svc.probe("10.0.0.2", 22, "ssh", timeout_ms=1000)
    assert result.ok is True
    assert result.reachable is True
    assert result.latency_ms is not None
    assert result.packet_loss_pct is None
    assert result.probe_kind == "tcp"


def test_tcp_probe_refused(monkeypatch):
    def refused(*a, **k):
        raise ConnectionRefusedError()

    monkeypatch.setattr(probe_svc.socket, "create_connection", refused)
    result = probe_svc.probe("10.0.0.2", 22, "ssh", timeout_ms=1000)
    assert result.ok is True
    assert result.reachable is False
    assert result.error == "conexión rechazada"


def test_tcp_probe_timeout(monkeypatch):
    def timeout(*a, **k):
        raise TimeoutError()

    monkeypatch.setattr(probe_svc.socket, "create_connection", timeout)
    result = probe_svc.probe("10.0.0.2", 22, "ssh", timeout_ms=1000)
    assert result.ok is True
    assert result.reachable is False
    assert result.error == "timeout TCP"


def test_tcp_probe_dns(monkeypatch):
    import socket

    def dns(*a, **k):
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(probe_svc.socket, "create_connection", dns)
    result = probe_svc.probe("no-existe.invalid", 22, "ssh", timeout_ms=1000)
    assert result.ok is False
    assert result.reachable is None
    assert result.error.startswith("DNS:")


def test_empty_host_is_not_measured():
    result = probe_svc.probe("", 22, "ssh")
    assert result.ok is False
    assert result.reachable is None
    assert result.error == "host vacío"
