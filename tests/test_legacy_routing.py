"""Tests de enrutamiento netmiko-directo en napalm_svc (legacy/telnet).

Sin red: se simula inventario y netmiko.
"""

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import napalm_svc as n
from app.services import netmiko_svc


LEGACY = {
    "id": "hp-sw-01",
    "hostname": "192.168.88.10",
    "port": 23,
    "driver": "procurve",
    "protocol": "telnet",
    "credentials": {"username": "manager", "password": "x"},
}


def _set_fake_device(dev):
    n._find_device = lambda device_id: dev if device_id == dev["id"] else None


# ── Regla de enrutamiento ─────────────────────────────────────────────────

def test_directo_por_telnet():
    assert n._netmiko_direct(LEGACY) is True


def test_no_directo_snmp():
    assert n._netmiko_direct({"protocol": "snmp", "port": 161, "driver": "snmp"}) is False


def test_no_directo_ssh_moderno():
    assert n._netmiko_direct({"protocol": "ssh", "port": 2222, "driver": "ios"}) is False


def test_directo_por_override():
    dev = {"driver": "ios", "netmiko_device_type": "cisco_s300", "port": 23}
    assert n._netmiko_direct(dev) is True


# ── run_commands vía netmiko ─────────────────────────────────────────────

def test_run_commands_netmiko(monkeypatch):
    _set_fake_device(LEGACY)
    monkeypatch.setattr(
        netmiko_svc, "execute_command",
        lambda dev, cmd: {"output": f"salida de {cmd}", "error": None},
    )
    res = n.run_commands("hp-sw-01", ["show version", "show interfaces"])
    assert res.success is True
    assert len(res.data) == 2
    assert res.data[0]["command"] == "show version"
    assert res.data[0]["error"] is None


def test_run_commands_netmiko_error_conexion(monkeypatch):
    _set_fake_device(LEGACY)
    monkeypatch.setattr(
        netmiko_svc, "execute_command",
        lambda dev, cmd: {"output": "", "error": "Connection timed out"},
    )
    res = n.run_commands("hp-sw-01", ["show version"])
    assert res.success is False
    assert res.error_type == "connection_error"


# ── compare/deploy dry-run vía netmiko (difflib, sin tocar el equipo) ────

def test_compare_config_netmiko(monkeypatch):
    _set_fake_device(LEGACY)
    running = "hostname old\ninterface 1\n name uplink\n"
    monkeypatch.setattr(netmiko_svc, "get_running_config", lambda dev: running)
    res = n.compare_config("hp-sw-01", "hostname new\ninterface 1\n name uplink\n")
    assert res.success is True
    assert "-hostname old" in res.data
    assert "+hostname new" in res.data
    assert hasattr(res, "rollback_mode")


def test_deploy_config_netmiko(monkeypatch):
    _set_fake_device(LEGACY)
    running = "hostname old\ninterface 1\n name uplink\n"
    monkeypatch.setattr(netmiko_svc, "get_running_config", lambda dev: running)
    monkeypatch.setattr(netmiko_svc, "send_config_lines",
                        lambda dev, cfg: {"ok": True, "output": cfg, "error": None})

    class FakeBackup:
        success = True
        data = {"file": "/backups/hp-sw-01_test.cfg", "success": True}

    monkeypatch.setattr(n, "backup_config", lambda device_id: FakeBackup())
    result = n.deploy_config("hp-sw-01", "interface 1\n name uplink\n")
    assert result["committed"] is True
    assert result["error"] is None


# ── backup_config por netmiko ─────────────────────────────────────────────

def test_backup_config_netmiko(tmp_path, monkeypatch):
    _set_fake_device(LEGACY)
    monkeypatch.setattr(n, "BACKUP_DIR", tmp_path)
    monkeypatch.setattr(netmiko_svc, "get_running_config", lambda dev: "hostname hp-sw-01\n")
    res = n.backup_config("hp-sw-01")
    assert res.success is True
    cfg_files = list(tmp_path.glob("hp-sw-01_*.cfg"))
    assert len(cfg_files) == 1
    assert "hostname hp-sw-01" in cfg_files[0].read_text()
