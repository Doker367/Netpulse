"""Validación estricta del YAML y resolución de la API key del colector."""

from pathlib import Path

import pytest
import yaml

from app.remote_collector.config import ConfigError, load_config, resolve_api_key


def base(tmp_path: Path) -> dict:
    return {
        "schema_version": 1,
        "site_id": "site-lima",
        "collector_id": "collector-lima-01",
        "central": {
            "base_url": "https://noc.example.net/",
            "api_key_env": "NETPULSE_COLLECTOR_API_KEY",
            "timeout_s": 10,
        },
        "probe": {"interval_s": 60, "timeout_ms": 1500, "workers": 4, "icmp_count": 1},
        "buffer": {
            "path": str(tmp_path / "buffer.sqlite3"),
            "max_rows": 500000,
            "max_age_hours": 72,
            "synced_retention_hours": 6,
            "min_free_mb": 200,
        },
        "sync": {
            "interval_s": 30,
            "heartbeat_s": 60,
            "batch_max_samples": 200,
            "backoff_base_s": 5,
            "backoff_max_s": 300,
        },
        "devices": [
            {"id": "sw-access-01", "hostname": "10.0.0.11", "port": 22,
             "protocol": "ssh", "driver": "ios"},
            {"id": "fw-edge-01", "hostname": "10.0.0.1", "port": 161,
             "protocol": "snmp"},
        ],
    }


def write(tmp_path: Path, data: dict, name: str = "collector.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_valid_config(tmp_path):
    config = load_config(write(tmp_path, base(tmp_path)))
    assert config.site_id == "site-lima"
    assert config.collector_id == "collector-lima-01"
    assert config.central.base_url == "https://noc.example.net"
    assert [d.id for d in config.devices] == ["sw-access-01", "fw-edge-01"]
    assert config.buffer.path == tmp_path / "buffer.sqlite3"


def test_unknown_root_key_rejected(tmp_path):
    data = base(tmp_path)
    data["typo"] = True
    with pytest.raises(ConfigError, match="claves desconocidas"):
        load_config(write(tmp_path, data))


def test_unknown_device_key_rejected(tmp_path):
    data = base(tmp_path)
    data["devices"][0]["password"] = "no-debe-ir-aqui"
    with pytest.raises(ConfigError, match="claves desconocidas"):
        load_config(write(tmp_path, data))


def test_devices_and_inventory_file_are_exclusive(tmp_path):
    data = base(tmp_path)
    data["inventory_file"] = "inventory.yaml"
    with pytest.raises(ConfigError, match="no ambos"):
        load_config(write(tmp_path, data))


def test_inventory_file(tmp_path):
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(yaml.safe_dump({
        "devices": [{"id": "r1", "hostname": "10.1.1.1", "port": 22, "protocol": "ssh"}]
    }), encoding="utf-8")
    data = base(tmp_path)
    del data["devices"]
    data["inventory_file"] = "inventory.yaml"
    config = load_config(write(tmp_path, data))
    assert [d.id for d in config.devices] == ["r1"]


def test_invalid_interval(tmp_path):
    data = base(tmp_path)
    data["probe"]["interval_s"] = 5
    with pytest.raises(ConfigError, match="probe.interval_s"):
        load_config(write(tmp_path, data))


def test_icmp_count_must_be_one(tmp_path):
    data = base(tmp_path)
    data["probe"]["icmp_count"] = 3
    with pytest.raises(ConfigError, match="probe.icmp_count"):
        load_config(write(tmp_path, data))


def test_duplicate_device_id(tmp_path):
    data = base(tmp_path)
    data["devices"].append(dict(data["devices"][0]))
    with pytest.raises(ConfigError, match="duplicado"):
        load_config(write(tmp_path, data))


def test_api_key_env_and_file(tmp_path, monkeypatch):
    config = load_config(write(tmp_path, base(tmp_path)))
    monkeypatch.setenv("NETPULSE_COLLECTOR_API_KEY", "np_abc")
    assert resolve_api_key(config) == "np_abc"

    monkeypatch.delenv("NETPULSE_COLLECTOR_API_KEY")
    key_file = tmp_path / "collector.key"
    key_file.write_text("np_from_file\n", encoding="utf-8")
    data = base(tmp_path)
    data["central"]["api_key_file"] = str(key_file)
    config = load_config(write(tmp_path, data))
    assert resolve_api_key(config, environ={}) == "np_from_file"


def test_missing_api_key(tmp_path):
    config = load_config(write(tmp_path, base(tmp_path)))
    with pytest.raises(ConfigError, match="falta la API key"):
        resolve_api_key(config, environ={})


def test_api_key_format(tmp_path):
    config = load_config(write(tmp_path, base(tmp_path)))
    with pytest.raises(ConfigError, match="np_"):
        resolve_api_key(config, environ={"NETPULSE_COLLECTOR_API_KEY": "token-malo"})


def test_config_repr_does_not_hold_key(tmp_path):
    config = load_config(write(tmp_path, base(tmp_path)))
    assert "np_" not in repr(config)
