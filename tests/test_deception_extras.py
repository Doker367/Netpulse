"""Tests de extras de deception: blocklist, IOCs, paneles, FTP/MySQL y NetBox."""

import asyncio
import struct
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.deception import DeceptionEventIn, DeceptionEventType
from app.services.deception import (
    alert_svc,
    allowlist_svc,
    blocklist_svc,
    ioc_svc,
    store_svc,
    topology_svc,
)
from app.services.deception.listeners import FtpListener, MysqlListener
from app.services.deception import panels


# ── Blocklist ────────────────────────────────────────────────


@pytest.fixture()
def block_file(tmp_path, monkeypatch):
    monkeypatch.setattr(blocklist_svc, "BLOCKLIST_FILE", tmp_path / "blocklist.json")


def test_blocklist_crud(block_file):
    assert blocklist_svc.list_blocked() == []
    blocklist_svc.add("203.0.113.9", "manual")
    assert blocklist_svc.is_blocked("203.0.113.9")
    assert blocklist_svc.list_blocked()[0]["hits"] == 1
    blocklist_svc.add("203.0.113.9", "manual")  # incrementa hits
    assert blocklist_svc.list_blocked()[0]["hits"] == 2
    assert blocklist_svc.remove("203.0.113.9") is True
    assert not blocklist_svc.is_blocked("203.0.113.9")


# ── IOCs ─────────────────────────────────────────────────────


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    monkeypatch.setattr(store_svc, "DB_PATH", tmp_path / "deception.db")


def test_iocs_clasifica_amenaza(db_path, block_file):
    store_svc.log_event(DeceptionEventIn(
        device_id="srv-01", src_ip="198.51.100.7", proto="ssh",
        type=DeceptionEventType.LOGIN_SUCCESS, success=True,
    ))
    store_svc.log_event(DeceptionEventIn(
        device_id="srv-01", src_ip="198.51.100.7", proto="ssh",
        type=DeceptionEventType.COMMAND, detail={"command": "cat /etc/passwd"},
    ))
    blocklist_svc.add("198.51.100.7", "test")

    iocs = ioc_svc.summarize()
    assert len(iocs) == 1
    assert iocs[0]["threat"] == "high"
    assert iocs[0]["blocked"] is True
    assert iocs[0]["commands"] == 1
    assert "198.51.100.7" in ioc_svc.to_csv()


# ── Paneles ──────────────────────────────────────────────────


def test_paneles_por_vendor():
    cam = panels.login_page("camera", "Hikvision-Webs")
    assert "Hikvision" in cam and "Device Management" in cam
    assert panels.server_header("firewall") == "FortiGate"
    assert "Fortinet" in panels.dashboard_page("firewall", "admin")


# ── FTP / MySQL ──────────────────────────────────────────────


def test_ftp_login(db_path):
    async def scenario():
        listener = FtpListener("srv-01", "server", "127.0.0.1", 0)
        await listener.start()
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", listener.port)
            await asyncio.wait_for(reader.readline(), timeout=3)  # 220
            writer.write(b"USER admin\r\n")
            await writer.drain()
            await asyncio.wait_for(reader.readline(), timeout=3)  # 331
            writer.write(b"PASS 1234\r\n")
            await writer.drain()
            await asyncio.wait_for(reader.readline(), timeout=3)  # 230
            writer.write(b"QUIT\r\n")
            await writer.drain()
            writer.close()
        finally:
            await listener.stop()

    asyncio.run(scenario())
    types = [e["type"] for e in store_svc.get_events()]
    assert "login_attempt" in types
    assert "login_success" in types


def _mysql_packet(seq: int, payload: bytes) -> bytes:
    return struct.pack("<I", len(payload))[:3] + bytes([seq]) + payload


def test_mysql_handshake_y_login(db_path):
    async def scenario():
        listener = MysqlListener("db-01", "server", "127.0.0.1", 0)
        await listener.start()
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", listener.port)
            header = await asyncio.wait_for(reader.readexactly(4), timeout=3)
            length = header[0] | (header[1] << 8) | (header[2] << 16)
            await asyncio.wait_for(reader.readexactly(length), timeout=3)  # handshake
            # HandshakeResponse41 con usuario "root"
            payload = b"\x00" * 32 + b"root\x00" + b"\x00" * 20
            writer.write(_mysql_packet(1, payload))
            await writer.drain()
            await asyncio.wait_for(reader.readexactly(4 + 7), timeout=3)  # OK
            writer.write(_mysql_packet(0, b"\x01"))  # COM_QUIT
            await writer.drain()
            writer.close()
        finally:
            await listener.stop()

    asyncio.run(scenario())
    events = store_svc.get_events()
    assert any(e["type"] == "login_attempt" and e["username"] == "root" for e in events)
    assert any(e["type"] == "login_success" for e in events)


# ── NetBox sync ──────────────────────────────────────────────


def test_sync_from_netbox(tmp_path, monkeypatch):
    f = tmp_path / "network.yaml"
    f.write_text(yaml.safe_dump({"segments": [], "devices": []}))
    monkeypatch.setattr(topology_svc, "NETWORK_FILE", f)
    topology_svc.invalidate_cache()

    from app.services.integrations import netbox_sync
    monkeypatch.setattr(netbox_sync, "fetch_netbox_devices", lambda: [
        {"name": "FW-01", "model": "FortiGate 60F", "status": "active"},
        {"name": "SW-01", "model": "Catalyst 2960", "status": "active"},
    ])

    result = topology_svc.sync_from_netbox()
    assert result["added"] == 2
    roles = {d["id"]: d["role"] for d in topology_svc.list_devices()}
    assert roles["fw-01"] == "firewall"
    assert roles["sw-01"] == "switch"


# ── Anti falsos positivos ────────────────────────────────────


@pytest.fixture()
def allow_file(tmp_path, monkeypatch):
    monkeypatch.setattr(allowlist_svc, "ALLOWLIST_FILE", tmp_path / "allowlist.json")


def test_allowlist_cidr(allow_file):
    allowlist_svc.add("10.0.0.0/8", "gestion")
    assert allowlist_svc.is_allowed("10.1.2.3")
    assert not allowlist_svc.is_allowed("203.0.113.9")
    assert allowlist_svc.remove("10.0.0.0/8")


def test_alert_ignora_simulado(db_path, allow_file, monkeypatch):
    monkeypatch.setattr(alert_svc, "DECEPTION_ALERTS_ENABLED", True)
    monkeypatch.setattr(alert_svc, "DECEPTION_ALERT_IGNORE_SIMULATED", True)
    rec = store_svc.log_event(DeceptionEventIn(
        device_id="srv-01", src_ip="203.0.113.9", proto="ssh",
        type=DeceptionEventType.LOGIN_SUCCESS, success=True,
        detail={"simulated": True},
    ))
    assert alert_svc.evaluate(rec) == []


def test_alert_ignora_loopback(db_path, allow_file, monkeypatch):
    monkeypatch.setattr(alert_svc, "DECEPTION_ALERTS_ENABLED", True)
    monkeypatch.setattr(alert_svc, "DECEPTION_ALERT_IGNORE_LOOPBACK", True)
    rec = store_svc.log_event(DeceptionEventIn(
        device_id="srv-01", src_ip="127.0.0.1", proto="ssh",
        type=DeceptionEventType.LOGIN_SUCCESS, success=True,
    ))
    assert alert_svc.evaluate(rec) == []


def test_alert_ignora_allowlisted(db_path, allow_file, monkeypatch):
    monkeypatch.setattr(alert_svc, "DECEPTION_ALERTS_ENABLED", True)
    monkeypatch.setattr(alert_svc, "DECEPTION_ALERT_IGNORE_SIMULATED", False)
    monkeypatch.setattr(alert_svc, "DECEPTION_ALERT_IGNORE_LOOPBACK", False)
    allowlist_svc.add("203.0.113.0/24", "scanner propio")
    rec = store_svc.log_event(DeceptionEventIn(
        device_id="srv-01", src_ip="203.0.113.9", proto="ssh",
        type=DeceptionEventType.LOGIN_SUCCESS, success=True,
    ))
    assert alert_svc.evaluate(rec) == []


def test_ioc_clasifica_loopback_como_lab(db_path, block_file, allow_file):
    store_svc.log_event(DeceptionEventIn(
        device_id="srv-01", src_ip="127.0.0.1", proto="ssh",
        type=DeceptionEventType.LOGIN_SUCCESS, success=True,
    ))
    store_svc.log_event(DeceptionEventIn(
        device_id="srv-01", src_ip="198.51.100.7", proto="ssh",
        type=DeceptionEventType.COMMAND, detail={"command": "cat /etc/passwd"},
    ))
    store_svc.log_event(DeceptionEventIn(
        device_id="srv-01", src_ip="45.155.205.233", proto="ssh",
        type=DeceptionEventType.LOGIN_SUCCESS, success=True, detail={"simulated": True},
    ))
    iocs = {r["ip"]: r for r in ioc_svc.summarize()}
    assert iocs["127.0.0.1"]["threat"] == "info"
    assert iocs["127.0.0.1"]["kind"] == "lab"
    # El simulado no aparece
    assert "45.155.205.233" not in iocs
    # El público con comando sensible (sin login) queda en medium
    assert iocs["198.51.100.7"]["threat"] == "medium"
    assert iocs["198.51.100.7"]["score"] >= 10
