"""Carga y validación del YAML del colector remoto.

Reglas deliberadamente estrictas: una clave desconocida o un typo debe fallar
en ``--check``/arranque, no dejar equipos sin monitorizar en silencio. La API
key nunca forma parte de este objeto ni de su ``repr``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

SITE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
DEVICE_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
PROTOCOLS = {"ssh", "telnet", "snmp"}

_TOP_KEYS = {
    "schema_version", "site_id", "collector_id", "central", "probe",
    "buffer", "sync", "devices", "inventory_file",
}
_CENTRAL_KEYS = {"base_url", "api_key_env", "api_key_file", "verify_tls", "ca_file", "timeout_s"}
_PROBE_KEYS = {"interval_s", "timeout_ms", "workers", "icmp_count"}
_BUFFER_KEYS = {"path", "max_rows", "max_age_hours", "synced_retention_hours", "min_free_mb"}
_SYNC_KEYS = {
    "interval_s", "heartbeat_s", "batch_max_samples", "backoff_base_s", "backoff_max_s",
}
_DEVICE_KEYS = {"id", "hostname", "port", "protocol", "driver"}
_INVENTORY_KEYS = {"devices"}


class ConfigError(ValueError):
    """Configuración inválida; el CLI sale con código 2."""


@dataclass(frozen=True)
class CentralConfig:
    base_url: str
    api_key_env: str
    api_key_file: Path | None
    verify_tls: bool
    ca_file: Path | None
    timeout_s: float


@dataclass(frozen=True)
class ProbeConfig:
    interval_s: int
    timeout_ms: int
    workers: int
    icmp_count: int


@dataclass(frozen=True)
class BufferConfig:
    path: Path
    max_rows: int
    max_age_hours: int
    synced_retention_hours: int
    min_free_mb: int


@dataclass(frozen=True)
class SyncConfig:
    interval_s: int
    heartbeat_s: int
    batch_max_samples: int
    backoff_base_s: float
    backoff_max_s: float


@dataclass(frozen=True)
class DeviceSpec:
    id: str
    hostname: str
    port: int
    protocol: str
    driver: str | None = None


@dataclass(frozen=True)
class CollectorConfig:
    schema_version: int
    site_id: str
    collector_id: str
    central: CentralConfig
    probe: ProbeConfig
    buffer: BufferConfig
    sync: SyncConfig
    devices: tuple[DeviceSpec, ...]


def _mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{where} debe ser un objeto YAML")
    return value


def _check_keys(mapping: Mapping[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise ConfigError(f"{where}: claves desconocidas: {', '.join(unknown)}")


def _text(value: Any, where: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{where} debe ser texto")
    value = value.strip()
    if not value and not allow_empty:
        raise ConfigError(f"{where} no puede estar vacío")
    return value


def _integer(value: Any, where: str, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{where} debe ser un entero")
    if value < minimum or (maximum is not None and value > maximum):
        bound = f"{minimum}..{maximum}" if maximum is not None else f">= {minimum}"
        raise ConfigError(f"{where} fuera de rango ({bound})")
    return value


def _number(value: Any, where: str, minimum: float, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{where} debe ser numérico")
    value = float(value)
    if value < minimum or (maximum is not None and value > maximum):
        raise ConfigError(f"{where} fuera de rango")
    return value


def _boolean(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{where} debe ser booleano")
    return value


def _resolve_path(value: Any, where: str, base: Path) -> Path:
    raw = _text(value, where)
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = base / path
    return path


def _load_yaml(path: Path) -> Any:
    if not path.is_file():
        raise ConfigError(f"no existe el archivo: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"no se pudo leer {path}: {exc}") from exc
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML inválido en {path}: {exc}") from exc


def _parse_device(raw: Any, where: str) -> DeviceSpec:
    item = _mapping(raw, where)
    _check_keys(item, _DEVICE_KEYS, where)
    device_id = _text(item.get("id"), f"{where}.id")
    if not DEVICE_RE.match(device_id):
        raise ConfigError(f"{where}.id inválido (usa letras, números, . _ : -)")
    hostname = _text(item.get("hostname"), f"{where}.hostname")
    port = _integer(item.get("port", 22), f"{where}.port", 1, 65535)
    protocol = _text(item.get("protocol", "ssh"), f"{where}.protocol").lower()
    if protocol not in PROTOCOLS:
        raise ConfigError(f"{where}.protocol debe ser uno de: {', '.join(sorted(PROTOCOLS))}")
    driver = item.get("driver")
    if driver is not None:
        driver = _text(driver, f"{where}.driver").lower()
    return DeviceSpec(device_id, hostname, port, protocol, driver)


def _parse_devices(raw: Any, where: str) -> tuple[DeviceSpec, ...]:
    if not isinstance(raw, list):
        raise ConfigError(f"{where} debe ser una lista")
    devices = tuple(_parse_device(item, f"{where}[{index}]") for index, item in enumerate(raw))
    if not devices:
        raise ConfigError(f"{where} no puede estar vacío")
    seen: set[str] = set()
    for device in devices:
        if device.id in seen:
            raise ConfigError(f"{where}: id duplicado: {device.id}")
        seen.add(device.id)
    return devices


def load_config(path: str | Path) -> CollectorConfig:
    """Carga, valida y normaliza el YAML del colector."""
    config_path = Path(path).expanduser()
    data = _mapping(_load_yaml(config_path), "config")
    _check_keys(data, _TOP_KEYS, "config")
    base = config_path.parent

    schema_version = _integer(data.get("schema_version", 1), "schema_version", 1, 1)
    site_id = _text(data.get("site_id"), "site_id")
    collector_id = _text(data.get("collector_id"), "collector_id")
    if not SITE_RE.match(site_id):
        raise ConfigError("site_id inválido (minúsculas, números, . _ -, máx. 64)")
    if not SITE_RE.match(collector_id):
        raise ConfigError("collector_id inválido (minúsculas, números, . _ -, máx. 64)")

    central_raw = _mapping(data.get("central"), "central")
    _check_keys(central_raw, _CENTRAL_KEYS, "central")
    base_url = _text(central_raw.get("base_url"), "central.base_url").rstrip("/")
    if not (base_url.startswith("https://") or base_url.startswith("http://")):
        raise ConfigError("central.base_url debe empezar por https:// o http://")
    api_key_env = _text(
        central_raw.get("api_key_env", "NETPULSE_COLLECTOR_API_KEY"),
        "central.api_key_env",
    )
    api_key_file_raw = central_raw.get("api_key_file")
    api_key_file = (
        _resolve_path(api_key_file_raw, "central.api_key_file", base)
        if api_key_file_raw is not None else None
    )
    if api_key_file is not None and not api_key_file.is_file():
        raise ConfigError(f"central.api_key_file no existe: {api_key_file}")
    verify_tls = _boolean(central_raw.get("verify_tls", True), "central.verify_tls")
    ca_file_raw = central_raw.get("ca_file")
    ca_file = _resolve_path(ca_file_raw, "central.ca_file", base) if ca_file_raw is not None else None
    if ca_file is not None and not ca_file.is_file():
        raise ConfigError(f"central.ca_file no existe: {ca_file}")
    timeout_s = _number(central_raw.get("timeout_s", 10), "central.timeout_s", 1, 60)

    probe_raw = _mapping(data.get("probe"), "probe")
    _check_keys(probe_raw, _PROBE_KEYS, "probe")
    interval_s = _integer(probe_raw.get("interval_s", 60), "probe.interval_s", 10, 3600)
    timeout_ms = _integer(probe_raw.get("timeout_ms", 1500), "probe.timeout_ms", 100, 10000)
    workers = _integer(probe_raw.get("workers", 8), "probe.workers", 1, 32)
    icmp_count = _integer(probe_raw.get("icmp_count", 1), "probe.icmp_count", 1, 1)

    buffer_raw = _mapping(data.get("buffer"), "buffer")
    _check_keys(buffer_raw, _BUFFER_KEYS, "buffer")
    buffer_path = _resolve_path(buffer_raw.get("path"), "buffer.path", base)
    max_rows = _integer(buffer_raw.get("max_rows", 500000), "buffer.max_rows", 1000)
    max_age_hours = _integer(buffer_raw.get("max_age_hours", 72), "buffer.max_age_hours", 1)
    synced_retention_hours = _integer(
        buffer_raw.get("synced_retention_hours", 6),
        "buffer.synced_retention_hours", 1,
    )
    min_free_mb = _integer(buffer_raw.get("min_free_mb", 200), "buffer.min_free_mb", 10)

    sync_raw = _mapping(data.get("sync"), "sync")
    _check_keys(sync_raw, _SYNC_KEYS, "sync")
    sync_interval_s = _integer(sync_raw.get("interval_s", 30), "sync.interval_s", 1, 3600)
    heartbeat_s = _integer(sync_raw.get("heartbeat_s", 60), "sync.heartbeat_s", 1, 3600)
    batch_max = _integer(sync_raw.get("batch_max_samples", 200), "sync.batch_max_samples", 1, 500)
    backoff_base = _number(sync_raw.get("backoff_base_s", 5), "sync.backoff_base_s", 0.1, 3600)
    backoff_max = _number(sync_raw.get("backoff_max_s", 300), "sync.backoff_max_s", 1, 86400)
    if backoff_max < backoff_base:
        raise ConfigError("sync.backoff_max_s debe ser >= sync.backoff_base_s")

    inline_devices = data.get("devices")
    inventory_file_raw = data.get("inventory_file")
    if inline_devices is not None and inventory_file_raw is not None:
        raise ConfigError("usa 'devices' o 'inventory_file', no ambos")
    if inventory_file_raw is not None:
        inventory_path = _resolve_path(inventory_file_raw, "inventory_file", base)
        inventory_data = _mapping(_load_yaml(inventory_path), "inventory_file")
        _check_keys(inventory_data, _INVENTORY_KEYS, "inventory_file")
        devices = _parse_devices(inventory_data.get("devices"), "inventory_file.devices")
    else:
        devices = _parse_devices(inline_devices, "devices")

    return CollectorConfig(
        schema_version=schema_version,
        site_id=site_id,
        collector_id=collector_id,
        central=CentralConfig(
            base_url=base_url,
            api_key_env=api_key_env,
            api_key_file=api_key_file,
            verify_tls=verify_tls,
            ca_file=ca_file,
            timeout_s=timeout_s,
        ),
        probe=ProbeConfig(interval_s, timeout_ms, workers, icmp_count),
        buffer=BufferConfig(
            buffer_path, max_rows, max_age_hours, synced_retention_hours, min_free_mb,
        ),
        sync=SyncConfig(
            sync_interval_s, heartbeat_s, batch_max, backoff_base, backoff_max,
        ),
        devices=devices,
    )


def resolve_api_key(config: CollectorConfig, environ: Mapping[str, str] | None = None) -> str:
    """Resuelve la API key por entorno, con archivo como alternativa.

    El valor nunca se incluye en mensajes de error ni en ``repr``.
    """
    env = environ if environ is not None else os.environ
    value = (env.get(config.central.api_key_env) or "").strip()
    if not value and config.central.api_key_file is not None:
        try:
            value = config.central.api_key_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConfigError(
                f"no se pudo leer central.api_key_file ({config.central.api_key_file}): {exc}"
            ) from exc
    if not value:
        raise ConfigError(
            f"falta la API key: define {config.central.api_key_env} o central.api_key_file"
        )
    if not value.startswith("np_"):
        raise ConfigError("la API key debe empezar por np_")
    return value
