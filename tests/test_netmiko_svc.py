"""Tests del motor netmiko multi-familia (legacy/telnet).

No requieren red: se simula netmiko con objetos fake.
"""

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import netmiko_svc as n


# ── Resolución de device_type ─────────────────────────────────────────────

def test_telnet_por_puerto():
    dev = {"driver": "procurve", "port": 23}
    assert n.is_telnet_device(dev)
    assert n._device_type(dev) == "hp_procurve_telnet"


def test_telnet_por_protocolo():
    dev = {"driver": "ios", "protocol": "telnet", "port": 99}
    assert n.is_telnet_device(dev)
    assert n._device_type(dev) == "cisco_ios_telnet"


def test_ssh_por_defecto():
    dev = {"driver": "ios", "port": 22}
    assert not n.is_telnet_device(dev)
    assert n._device_type(dev) == "cisco_ios"


def test_override_netmiko_device_type():
    dev = {"driver": "ios", "netmiko_device_type": "cisco_s300", "port": 23}
    assert n._device_type(dev) == "cisco_s300_telnet"


def test_override_sin_cambiar():
    dev = {"driver": "procurve", "netmiko_device_type": "cisco_s300", "port": 22}
    assert n._device_type(dev) == "cisco_s300"


def test_alcatel_aos_telnet_registrado():
    from netmiko.ssh_dispatcher import CLASS_MAPPER
    assert "alcatel_aos_telnet" in CLASS_MAPPER
    assert n._device_type({"driver": "alcatel_aos", "port": 23}) == "alcatel_aos_telnet"


def test_sros_telnet():
    assert n._device_type({"driver": "alcatel_sros", "port": 23}) == "nokia_sros_telnet"


# ── Parseo de ping ────────────────────────────────────────────────────────

def test_parse_ping_cisco():
    out = """Type escape sequence to abort.
!!!!!
Success rate is 100 percent (5/5), round-trip min/avg/max = 1/2/3 ms"""
    stats = n._parse_ping(out)
    assert stats is not None
    assert stats["probes_sent"] == 5
    assert stats["packet_loss"] == 0
    assert stats["rtt_min"] == 1.0
    assert stats["rtt_avg"] == 2.0
    assert stats["rtt_max"] == 3.0


def test_parse_ping_perdida_total():
    out = "Success rate is 0 percent (0/5)"
    stats = n._parse_ping(out)
    assert stats["packet_loss"] == 100


def test_parse_ping_linux():
    out = "5 packets transmitted, 5 received, 0% packet loss, time 4000ms rtt min/avg/max/mdev = 10/12/15/1 ms"
    stats = n._parse_ping(out)
    assert stats["probes_sent"] == 5
    assert stats["packet_loss"] == 0
    assert stats["rtt_avg"] == 12.0


def test_ping_devuelve_success_shape(monkeypatch):
    dev = {"id": "legacy", "driver": "procurve", "protocol": "telnet", "port": 23,
           "hostname": "10.0.0.1", "username": "manager", "password": "x"}

    def fake_run(d, cmd, read_timeout=30):
        assert "ping" in cmd
        return "Reply from 10.0.0.2 ... 5 packets transmitted, 5 received"

    monkeypatch.setattr(n, "_run", fake_run)
    res = n.ping(dev, "10.0.0.2")
    assert "success" in res
    assert res["success"]["packet_loss"] < 100


# ── Ejecución / configuración con sesión fake ────────────────────────────

class FakeConn:
    def __init__(self, outputs):
        self._o = outputs
        self.sent = []

    def send_command(self, command, read_timeout=30):
        self.sent.append(command)
        return self._o.get(command, self._o.get("default", ""))

    def send_config_set(self, lines, read_timeout=40):
        self.sent.extend(lines)
        return "\n".join(lines)

    def save_config(self):
        self.sent.append("__save__")

    def enable(self):
        pass

    def check_enable_mode(self):
        return True

    def disconnect(self):
        pass


def test_get_running_config_procurve(monkeypatch):
    dev = {"id": "sw1", "driver": "procurve", "protocol": "telnet", "port": 23,
           "hostname": "10.0.0.9", "username": "manager", "password": "x"}
    cfg = "hostname sw1\ninterface 1\n name uplink\n"
    conn = FakeConn({"show running-config": cfg})

    def fake_connect(d):
        return conn

    monkeypatch.setattr(n, "_connect", fake_connect)
    assert n.get_running_config(dev) == cfg


def test_execute_command(monkeypatch):
    dev = {"id": "sw1", "driver": "procurve", "protocol": "telnet", "port": 23,
           "hostname": "10.0.0.9", "username": "manager", "password": "x"}
    conn = FakeConn({"show version": "HP J4905A Switch 3400cl-24G"})
    monkeypatch.setattr(n, "_connect", lambda d: conn)
    res = n.execute_command(dev, "show version")
    assert res["error"] is None
    assert "3400cl" in res["output"]


def test_send_config_lines(monkeypatch):
    dev = {"id": "sw1", "driver": "ios", "protocol": "telnet", "port": 23,
           "hostname": "10.0.0.9", "username": "admin", "password": "x"}
    conn = FakeConn({})
    monkeypatch.setattr(n, "_connect", lambda d: conn)
    res = n.send_config_lines(dev, "! comentario\ninterface Gi0/1\n description uplink\n")
    assert res["ok"] is True
    assert "interface Gi0/1" in conn.sent
    assert "__save__" in conn.sent
