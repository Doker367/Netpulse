"""CLI del colector remoto.

Uso principal:
    python -m app.remote_collector --config /etc/netpulse/collector.yaml

Subcomandos de diagnóstico: ``--check``, ``--once``, ``--status`` y
``--dry-run``. El proceso normal corre dos hilos (sonda y sync) y escribe
``status.json`` de forma atómica.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import timedelta
from typing import Any

from app.remote_collector import __version__
from app.remote_collector.buffer import Buffer, BufferError
from app.remote_collector.config import (
    CollectorConfig,
    ConfigError,
    DeviceSpec,
    load_config,
    resolve_api_key,
)
from app.remote_collector.logging_setup import setup_logging
from app.remote_collector.status import is_stale, read_status, status_path, write_status
from app.remote_collector.sync import (
    AuthError,
    ContractError,
    PayloadTooLarge,
    SyncClient,
    SyncError,
    next_delay,
    sync_once,
)
from app.remote_collector.timeutil import iso_now, utcnow
from app.services.collectors import probe_svc

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.remote_collector",
        description="Colector remoto de latencia/disponibilidad de NetPulse",
    )
    parser.add_argument(
        "--config",
        default=os.getenv("NETPULSE_COLLECTOR_CONFIG", "/etc/netpulse/collector.yaml"),
        help="Ruta del YAML de configuración",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="Valida config/clave y whoami")
    group.add_argument("--once", action="store_true", help="Un ciclo de sonda + sync")
    group.add_argument("--status", action="store_true", help="Imprime status.json")
    group.add_argument("--dry-run", action="store_true", help="Sonda sin buffer ni HTTP")
    parser.add_argument("--log-level", default=None, help="DEBUG, INFO, WARNING...")
    return parser


def _load(path: str) -> CollectorConfig | None:
    try:
        return load_config(path)
    except ConfigError as exc:
        print(f"config inválida: {exc}", file=sys.stderr)
        return None


def _json_print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _probe_one(device: DeviceSpec, config: CollectorConfig) -> dict[str, Any]:
    result = probe_svc.probe(
        device.hostname,
        port=device.port,
        protocol=device.protocol,
        driver=device.driver,
        timeout_ms=config.probe.timeout_ms,
    )
    return {
        "device_id": device.id,
        "ok": result.ok,
        "reachable": result.reachable,
        "latency_ms": result.latency_ms,
        "packet_loss_pct": result.packet_loss_pct,
        "probe_kind": result.probe_kind,
        "error": result.error,
    }


def cmd_dry_run(config: CollectorConfig) -> int:
    results = []
    with ThreadPoolExecutor(max_workers=config.probe.workers) as pool:
        futures = {
            pool.submit(_probe_one, device, config): device
            for device in config.devices
        }
        for future in as_completed(futures):
            device = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001 - una sonda no debe tumbar el CLI
                results.append({
                    "device_id": device.id, "ok": False, "reachable": None,
                    "latency_ms": None, "packet_loss_pct": None,
                    "probe_kind": "tcp", "error": f"error interno: {exc}",
                })
    results.sort(key=lambda item: item["device_id"])
    _json_print({
        "schema_version": 1,
        "site_id": config.site_id,
        "collector_id": config.collector_id,
        "collector_version": __version__,
        "dry_run": True,
        "results": results,
    })
    return 0


def cmd_status(config: CollectorConfig) -> int:
    path = status_path(config.buffer.path)
    try:
        payload = read_status(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"sin status: {exc}", file=sys.stderr)
        return 3
    payload["state"] = "stale" if is_stale(payload, config.probe.interval_s) else "fresh"
    _json_print(payload)
    return 0


def cmd_check(config: CollectorConfig) -> int:
    try:
        key = resolve_api_key(config)
    except ConfigError as exc:
        print(f"config inválida: {exc}", file=sys.stderr)
        return 2
    client = SyncClient(config, key)
    try:
        info = client.whoami()
    except SyncError as exc:
        print(f"check falló: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()
    if info.get("site_id") != config.site_id or info.get("collector_id") != config.collector_id:
        print(
            "check falló: la clave pertenece a "
            f"site={info.get('site_id')!r} collector={info.get('collector_id')!r}, "
            f"pero el YAML declara site={config.site_id!r} collector={config.collector_id!r}",
            file=sys.stderr,
        )
        return 1
    _json_print({"ok": True, **info})
    return 0


def _run_cycle(buffer: Buffer, config: CollectorConfig) -> dict[str, Any]:
    started = iso_now()
    started_monotonic = time.monotonic()
    results: list[tuple[DeviceSpec, probe_svc.ProbeResult]] = []
    with ThreadPoolExecutor(max_workers=config.probe.workers) as pool:
        futures = {
            pool.submit(
                probe_svc.probe,
                device.hostname,
                device.port,
                device.protocol,
                device.driver,
                config.probe.timeout_ms,
            ): device
            for device in config.devices
        }
        for future in as_completed(futures):
            device = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                result = probe_svc.ProbeResult(
                    False, None, None, None, "tcp", f"error interno: {exc}"
                )
            results.append((device, result))
    results.sort(key=lambda item: item[0].id)
    ts = started
    inserted = buffer.insert_probe(
        ts, results, started_at=started, finished_at=iso_now()
    )
    reachable = sum(1 for _, r in results if r.ok and r.reachable)
    unreachable = sum(1 for _, r in results if r.ok and r.reachable is False)
    unverifiable = sum(1 for _, r in results if not r.ok)
    return {
        "ts": ts,
        "duration_ms": int((time.monotonic() - started_monotonic) * 1000),
        "devices": len(results),
        "reachable": reachable,
        "unreachable": unreachable,
        "unverifiable": unverifiable,
        "inserted": inserted,
    }


class Service:
    """Proceso de larga duración: hilo de sonda + hilo de sync."""

    def __init__(self, config: CollectorConfig, config_path: str, api_key: str):
        self.config = config
        self.config_path = config_path
        self.buffer = Buffer(config.buffer)
        self.client = SyncClient(config, api_key)
        self.started_at = iso_now()
        self.stop_event = threading.Event()
        self.reload_event = threading.Event()
        self._status_lock = threading.Lock()
        self._exit_code = 0
        self._last_cycle: dict[str, Any] | None = None
        self._next_attempt_at: str | None = None
        self._last_status_code: int | None = None
        self._cycles = 0
        self._batch_limit = min(config.sync.batch_max_samples, 500)

    # ── control ─────────────────────────────────────────────

    def request_stop(self, code: int = 0) -> None:
        self._exit_code = max(self._exit_code, code)
        self.stop_event.set()

    def request_reload(self) -> None:
        self.reload_event.set()

    def _reload(self) -> None:
        try:
            new = load_config(self.config_path)
        except ConfigError as exc:
            logger.error("[reload] config inválida, se conserva la anterior: %s", exc)
            return
        if (
            new.buffer.path != self.config.buffer.path
            or new.central.base_url != self.config.central.base_url
            or new.sync != self.config.sync
            or new.site_id != self.config.site_id
            or new.collector_id != self.config.collector_id
        ):
            logger.warning(
                "[reload] cambios en buffer/central/sync/site/collector requieren "
                "reinicio; solo se aplican probe y devices"
            )
        self.config = replace(self.config, probe=new.probe, devices=new.devices)
        logger.info(
            "[reload] aplicado: devices=%s interval_s=%s",
            len(self.config.devices), self.config.probe.interval_s,
        )

    # ── status ──────────────────────────────────────────────

    def _write_status(self) -> None:
        with self._status_lock:
            try:
                summary = self.buffer.summary()
            except (BufferError, sqlite3.Error) as exc:
                logger.warning("[status] buffer no disponible: %s", exc)
                return
            payload = {
                "schema_version": 1,
                "site_id": self.config.site_id,
                "collector_id": self.config.collector_id,
                "collector_version": __version__,
                "pid": os.getpid(),
                "started_at": self.started_at,
                "updated_at": iso_now(),
                "probe": {
                    "interval_s": self.config.probe.interval_s,
                    "last_cycle_at": (self._last_cycle or {}).get("ts"),
                    "last_cycle_duration_ms": (self._last_cycle or {}).get("duration_ms"),
                    "devices": (self._last_cycle or {}).get("devices", 0),
                    "reachable": (self._last_cycle or {}).get("reachable", 0),
                    "unreachable": (self._last_cycle or {}).get("unreachable", 0),
                    "unverifiable": (self._last_cycle or {}).get("unverifiable", 0),
                },
                "buffer": summary,
                "sync": {
                    **self.buffer.sync_state(),
                    "next_attempt_at": self._next_attempt_at,
                },
                "central": {
                    "base_url": self.config.central.base_url,
                    "last_status_code": self._last_status_code,
                },
            }
            try:
                write_status(status_path(self.buffer.path), payload)
            except OSError as exc:
                logger.warning("[status] no se pudo escribir: %s", exc)

    # ── hilos ───────────────────────────────────────────────

    def _probe_loop(self) -> None:
        logger.info(
            "[probe] inicio site=%s collector=%s devices=%s interval_s=%s",
            self.config.site_id, self.config.collector_id,
            len(self.config.devices), self.config.probe.interval_s,
        )
        while not self.stop_event.is_set():
            if self.reload_event.is_set():
                self.reload_event.clear()
                self._reload()
            try:
                paused = self.buffer.check_disk()
                if paused:
                    logger.warning("[probe] pausada: %s", paused)
                else:
                    cycle = _run_cycle(self.buffer, self.config)
                    self._last_cycle = cycle
                    self._cycles += 1
                    logger.info(
                        "[probe] cycle devices=%s reachable=%s unreachable=%s "
                        "unverifiable=%s inserted=%s duration_ms=%s",
                        cycle["devices"], cycle["reachable"], cycle["unreachable"],
                        cycle["unverifiable"], cycle["inserted"], cycle["duration_ms"],
                    )
                    pending = self.buffer.counts()["pending"]
                    if self._cycles % 10 == 0 or pending > self.config.buffer.max_rows * 0.8:
                        stats = self.buffer.prune()
                        if any(stats.values()):
                            logger.info("[buffer] prune %s", stats)
                self._write_status()
            except (BufferError, sqlite3.Error) as exc:
                logger.critical("[probe] buffer fatal: %s", exc)
                self.request_stop(1)
                break
            except Exception as exc:  # noqa: BLE001 - el colector no debe morir por un ciclo
                logger.exception("[probe] ciclo falló: %s", exc)
                self._write_status()
            self.stop_event.wait(self.config.probe.interval_s)

    def _sync_loop(self) -> None:
        logger.info("[sync] inicio interval_s=%s", self.config.sync.interval_s)
        failures = int(self.buffer.get_state("consecutive_failures", "0") or 0)
        while not self.stop_event.is_set():
            try:
                sync_config = replace(
                    self.config,
                    sync=replace(
                        self.config.sync,
                        batch_max_samples=self._batch_limit,
                    ),
                )
                response = sync_once(self.buffer, self.client, sync_config)
                if response is not None:
                    self._last_status_code = 200
                    failures = 0
                    self._next_attempt_at = None
                    logger.info(
                        "[sync] batch accepted=%s duplicates=%s rejected=%s "
                        "marked_synced=%s marked_dead=%s",
                        response.get("accepted"), response.get("duplicates"),
                        response.get("rejected"), response.get("marked_synced"),
                        response.get("marked_dead"),
                    )
                self._write_status()
                delay = self.config.sync.interval_s
            except PayloadTooLarge as exc:
                failures += 1
                self._last_status_code = 413
                self._batch_limit = max(25, self._batch_limit // 2)
                logger.warning(
                    "[sync] lote demasiado grande, batch_limit=%s: %s",
                    self._batch_limit, exc,
                )
                delay = next_delay(
                    failures, self.config.sync.backoff_base_s, self.config.sync.backoff_max_s
                )
            except AuthError as exc:
                failures += 1
                self._last_status_code = 401
                logger.error("[sync] auth_error: %s", exc)
                delay = next_delay(
                    max(failures, 4),
                    self.config.sync.backoff_base_s,
                    self.config.sync.backoff_max_s,
                )
            except (ContractError, SyncError) as exc:
                failures += 1
                self._last_status_code = 422 if isinstance(exc, ContractError) else None
                logger.warning("[sync] error attempt=%s: %s", failures, exc)
                delay = next_delay(
                    failures, self.config.sync.backoff_base_s, self.config.sync.backoff_max_s
                )
            except Exception as exc:  # noqa: BLE001
                failures += 1
                logger.exception("[sync] error inesperado: %s", exc)
                delay = next_delay(
                    failures, self.config.sync.backoff_base_s, self.config.sync.backoff_max_s
                )
            self._next_attempt_at = (
                utcnow() + timedelta(seconds=delay)
            ).isoformat(timespec="microseconds")
            self._write_status()
            self.stop_event.wait(delay)
        logger.info("[sync] detenido")

    # ── run ─────────────────────────────────────────────────

    def run(self) -> int:
        probe_thread = threading.Thread(
            target=self._probe_loop, name="netpulse-collector-probe", daemon=True
        )
        sync_thread = threading.Thread(
            target=self._sync_loop, name="netpulse-collector-sync", daemon=True
        )
        probe_thread.start()
        sync_thread.start()
        self._write_status()
        try:
            while not self.stop_event.wait(1.0):
                pass
        except KeyboardInterrupt:
            self.request_stop(0)
        probe_thread.join(timeout=15)
        sync_thread.join(timeout=15)
        self.client.close()
        self._write_status()
        return self._exit_code


def cmd_once(config: CollectorConfig) -> int:
    try:
        key = resolve_api_key(config)
    except ConfigError as exc:
        print(f"config inválida: {exc}", file=sys.stderr)
        return 2
    try:
        buffer = Buffer(config.buffer)
    except BufferError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    cycle = _run_cycle(buffer, config)
    client = SyncClient(config, key)
    sync_result: dict[str, Any] | None = None
    sync_error: str | None = None
    try:
        sync_result = sync_once(buffer, client, config, force_heartbeat=True)
    except SyncError as exc:
        sync_error = str(exc)
    finally:
        client.close()
    _json_print({
        "site_id": config.site_id,
        "collector_id": config.collector_id,
        "cycle": cycle,
        "sync": sync_result,
        "sync_error": sync_error,
        "buffer": buffer.summary(),
    })
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.log_level or "INFO")
    config = _load(args.config)
    if config is None:
        return 2

    if args.status:
        return cmd_status(config)
    if args.dry_run:
        return cmd_dry_run(config)
    if args.check:
        return cmd_check(config)
    if args.once:
        return cmd_once(config)

    try:
        key = resolve_api_key(config)
    except ConfigError as exc:
        print(f"config inválida: {exc}", file=sys.stderr)
        return 2
    try:
        service = Service(config, args.config, key)
    except BufferError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    def _stop(_signum, _frame):
        service.request_stop(0)

    def _reload(_signum, _frame):
        service.request_reload()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, _reload)
    return service.run()
