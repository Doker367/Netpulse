"""Tests de GeoIP, mapa global de ataques y firewall (dry-run)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.deception import DeceptionEventIn, DeceptionEventType
from app.services.deception import (
    blocklist_svc,
    firewall_svc,
    geo_svc,
    geoip_svc,
    store_svc,
)


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    monkeypatch.setattr(store_svc, "DB_PATH", tmp_path / "deception.db")


@pytest.fixture()
def block_file(tmp_path, monkeypatch):
    monkeypatch.setattr(blocklist_svc, "BLOCKLIST_FILE", tmp_path / "blocklist.json")


# ── GeoIP ────────────────────────────────────────────────────


def test_geoip_privadas_none():
    assert geoip_svc.lookup("127.0.0.1") is None
    assert geoip_svc.lookup("10.0.0.5") is None
    assert geoip_svc.lookup("no-es-ip") is None


@pytest.mark.skipif(not geoip_svc.available(), reason="sin base GeoIP")
def test_geoip_publica():
    geo = geoip_svc.lookup("8.8.8.8")
    assert geo is not None
    assert geo["country_code"] == "US"
    assert geo["lat"] is not None


# ── Mapa global ──────────────────────────────────────────────


def test_attack_map(db_path):
    store_svc.log_event(DeceptionEventIn(
        device_id="srv-01", src_ip="8.8.8.8", proto="ssh",
        type=DeceptionEventType.LOGIN_ATTEMPT,
    ))
    # IP privada: no debe aparecer en el mapa
    store_svc.log_event(DeceptionEventIn(
        device_id="srv-01", src_ip="10.0.0.9", proto="ssh",
        type=DeceptionEventType.CONNECT,
    ))
    data = geo_svc.attack_map()
    assert "target" in data and "points" in data
    if geoip_svc.available():
        assert any(p["ip"] == "8.8.8.8" for p in data["points"])
        assert all(p["ip"] != "10.0.0.9" for p in data["points"])


# ── Firewall ─────────────────────────────────────────────────


def test_firewall_dry_run(block_file, monkeypatch):
    monkeypatch.setattr(firewall_svc, "FIREWALL_ENABLED", False)
    res = firewall_svc.block("203.0.113.9", "test")
    assert res["blocked"] is True
    assert res["applied"] is False
    assert res["commands"]
    assert blocklist_svc.is_blocked("203.0.113.9")


def test_firewall_status(block_file, monkeypatch):
    monkeypatch.setattr(firewall_svc, "FIREWALL_ENABLED", False)
    st = firewall_svc.status()
    assert "platform" in st and "preview" in st
