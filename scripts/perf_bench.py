#!/usr/bin/env python3
"""Banco reproducible de rendimiento HTTP para NetPulse NOC.

Sin ``--base-url`` prepara un inventario/usuario temporales en ESTE worktree,
arranca Uvicorn en un puerto libre y restaura todos los archivos tocados al
terminar. Con ``--base-url`` no modifica configuración local y usa las
credenciales indicadas o NETPULSE_SMOKE_USER/NETPULSE_SMOKE_PASSWORD.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    import requests
except ImportError as exc:  # dependencia normal del venv de NetPulse
    raise SystemExit("perf_bench.py requiere requests (ya incluido en el venv del proyecto)") from exc

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO / "docs" / "perf" / "baseline.json"
HOT_REPETITIONS = 20
COLD_REPETITIONS = 5
REQUEST_TIMEOUT_S = 15.0
START_TIMEOUT_S = 35.0


def percentile(values: list[float], pct: float) -> float:
    """Percentil nearest-rank, estable también para muestras pequeñas."""
    ordered = sorted(values)
    if not ordered:
        raise ValueError("no hay muestras")
    rank = max(1, math.ceil(pct / 100.0 * len(ordered)))
    return ordered[rank - 1]


def stats(values: list[float], statuses: dict[str, int] | None = None, **extra: Any) -> dict:
    result: dict[str, Any] = {
        "estado": "OK",
        "n": len(values),
        "p50_ms": round(percentile(values, 50), 3),
        "p95_ms": round(percentile(values, 95), 3),
        "p99_ms": round(percentile(values, 99), 3),
        "min_ms": round(min(values), 3),
        "max_ms": round(max(values), 3),
    }
    if statuses is not None:
        result["http_status"] = statuses
    result.update(extra)
    return result


def skipped(reason: str, **extra: Any) -> dict:
    return {"estado": "SKIP", "n": 0, "motivo": reason, **extra}


def failed(exc: Exception | str, **extra: Any) -> dict:
    message = str(exc)
    if not isinstance(exc, str):
        message = f"{type(exc).__name__}: {exc}"
    return {"estado": "ERROR", "n": 0, "error": message, **extra}


def status_key(code: int) -> str:
    return str(code)


def timed_request(
    session: requests.Session,
    method: str,
    url: str,
    *,
    expected: set[int] | None = None,
    timeout: float = REQUEST_TIMEOUT_S,
    **kwargs: Any,
) -> tuple[float, requests.Response]:
    started = time.perf_counter_ns()
    response = session.request(method, url, timeout=timeout, **kwargs)
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
    accepted = expected or {200}
    if response.status_code not in accepted:
        body = response.text.replace("\n", " ")[:240]
        raise RuntimeError(f"{method} {url}: HTTP {response.status_code}: {body}")
    return elapsed_ms, response


def repeated_get(
    session: requests.Session,
    url: str,
    repetitions: int = HOT_REPETITIONS,
    *,
    warmup: bool = True,
) -> dict:
    if warmup:
        timed_request(session, "GET", url)
    samples: list[float] = []
    codes: dict[str, int] = {}
    for _ in range(repetitions):
        elapsed, response = timed_request(session, "GET", url)
        samples.append(elapsed)
        key = status_key(response.status_code)
        codes[key] = codes.get(key, 0) + 1
    return stats(samples, codes, warmup=warmup)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def git_info() -> tuple[str | None, bool | None, list[str]]:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True, stderr=subprocess.DEVNULL
        ).strip()
        raw = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=REPO, text=True, stderr=subprocess.DEVNULL
        )
        paths = [line[3:] for line in raw.splitlines() if line]
        return commit, bool(paths), paths
    except (OSError, subprocess.CalledProcessError):
        return None, None, []


def machine_info() -> dict[str, Any]:
    cpu = platform.processor() or None
    model = platform.machine()
    ram_bytes: int | None = None
    if sys.platform == "darwin":
        try:
            cpu = subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], text=True).strip()
        except (OSError, subprocess.CalledProcessError):
            pass
        try:
            model = subprocess.check_output(["sysctl", "-n", "hw.model"], text=True).strip()
        except (OSError, subprocess.CalledProcessError):
            pass
        try:
            ram_bytes = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip())
        except (OSError, ValueError, subprocess.CalledProcessError):
            pass
    elif hasattr(os, "sysconf"):
        try:
            ram_bytes = int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
        except (OSError, ValueError):
            pass
    return {
        "hostname": socket.gethostname(),
        "os": platform.platform(),
        "modelo": model,
        "cpu": cpu,
        "ram_gib": round(ram_bytes / 1024**3, 2) if ram_bytes else None,
        "python": platform.python_version(),
    }


class WorkspaceSandbox(AbstractContextManager["WorkspaceSandbox"]):
    """Respalda/restaura configuración y la DB de auditoría del worktree."""

    def __init__(self, devices: int, closed_port: int, username: str, password: str):
        self.devices = devices
        self.closed_port = closed_port
        self.username = username
        self.password = password
        self.backup_dir = Path(tempfile.mkdtemp(prefix="np-perf-backup-"))
        self.paths = [
            REPO / "config" / "devices.yaml",
            REPO / "config" / "users.yaml",
            REPO / "config" / "metrics_history.json",
            REPO / "audit" / "audit.db",
            REPO / "audit" / "audit.db-wal",
            REPO / "audit" / "audit.db-shm",
        ]
        self.existed: dict[Path, bool] = {}

    def _backup(self) -> None:
        for index, path in enumerate(self.paths):
            self.existed[path] = path.exists()
            if path.exists():
                shutil.copy2(path, self.backup_dir / str(index))

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        tmp = Path(raw_tmp)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass

    def _write_fixture(self) -> None:
        lines = ["devices:"]
        for index in range(self.devices):
            lines.extend(
                [
                    f"  - id: perf-{index:04d}",
                    "    hostname: 127.0.0.1",
                    f"    port: {self.closed_port}",
                    "    driver: ios",
                    "    protocol: ssh",
                    "    type: router",
                    "    group: Perf",
                    "    tags: [benchmark]",
                    f"    description: Equipo temporal de rendimiento {index:04d}",
                    "    credentials:",
                    "      username: benchmark",
                    "      password: ''",
                ]
            )
        lines.extend(
            [
                "options:",
                "  timeout: 2",
                "  max_retries: 0",
                "  pool_size: 50",
                "  backup_dir: backups",
            ]
        )
        users = (
            "users:\n"
            f"  - username: {self.username}\n"
            f"    plain_password: {self.password}\n"
            "    role: admin\n"
            "    token_version: 0\n"
        )
        self._atomic_write(REPO / "config" / "devices.yaml", "\n".join(lines) + "\n")
        self._atomic_write(REPO / "config" / "users.yaml", users)

    def __enter__(self) -> "WorkspaceSandbox":
        self._backup()
        self._write_fixture()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        for index, path in enumerate(self.paths):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            if self.existed.get(path):
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.backup_dir / str(index), path)
        shutil.rmtree(self.backup_dir, ignore_errors=True)


class LocalServer:
    def __init__(self, port: int):
        self.port = port
        self.base_url = f"http://127.0.0.1:{port}"
        self.process: subprocess.Popen[str] | None = None
        self.log_file = tempfile.NamedTemporaryFile(
            prefix="np-perf-uvicorn-", suffix=".log", mode="w+", encoding="utf-8", delete=False
        )
        self.log_path = Path(self.log_file.name)

    def start(self) -> None:
        env = os.environ.copy()
        env.update(
            {
                "PYTHONPATH": str(REPO),
                "NETPULSE_METRICS_ENABLED": "false",
                "NETPULSE_SYSLOG_ENABLED": "false",
                "NETPULSE_SITE_WATCHDOG_ENABLED": "false",
                "NETPULSE_JWT_SECRET": secrets.token_hex(32),
                "NETPULSE_ENV": "development",
            }
        )
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--log-level",
                "warning",
                "--no-access-log",
            ],
            cwd=REPO,
            env=env,
            stdout=self.log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.monotonic() + START_TIMEOUT_S
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                break
            try:
                response = requests.get(
                    f"{self.base_url}/api/health",
                    headers={"X-Forwarded-For": "198.18.255.254"},
                    timeout=0.5,
                )
                if response.status_code == 200:
                    return
            except requests.RequestException:
                pass
            time.sleep(0.05)
        self.log_file.flush()
        detail = self.log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
        self.stop()
        raise RuntimeError(f"Uvicorn no quedó listo en {START_TIMEOUT_S}s: {detail}")

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        self.log_file.close()
        try:
            self.log_path.unlink()
        except FileNotFoundError:
            pass

    def __enter__(self) -> "LocalServer":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.stop()


def login(base_url: str, username: str, password: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": "netpulse-perf-bench/1"})
    _, response = timed_request(
        session,
        "POST",
        f"{base_url}/api/auth/login",
        json={"username": username, "password": password},
    )
    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("login no devolvió access_token")
    session.headers.update({"Authorization": f"Bearer {token}"})
    return session


def measure_health(base_url: str, simple: bool) -> tuple[dict, dict]:
    """Mide una candidata fría y el tramo caliente (modo URL externa)."""
    session = requests.Session()
    suffix = "?simple=true" if simple else ""
    url = f"{base_url}/api/devices/health{suffix}"
    cold_ms, response = timed_request(session, "GET", url)
    cold = stats(
        [cold_ms],
        {status_key(response.status_code): 1},
        cache="no controlada en instancia externa",
    )
    hot = repeated_get(session, url, warmup=True)
    hot["cache"] = "caliente"
    return cold, hot


def measure_local_health(simple: bool) -> tuple[dict, dict]:
    """Repite la muestra fría en procesos nuevos; calienta el último."""
    suffix = "?simple=true" if simple else ""
    samples: list[float] = []
    codes: dict[str, int] = {}
    hot: dict | None = None
    for index in range(COLD_REPETITIONS):
        with LocalServer(free_port()) as server:
            session = requests.Session()
            url = f"{server.base_url}/api/devices/health{suffix}"
            elapsed, response = timed_request(session, "GET", url)
            samples.append(elapsed)
            key = status_key(response.status_code)
            codes[key] = codes.get(key, 0) + 1
            if index == COLD_REPETITIONS - 1:
                hot = repeated_get(session, url, warmup=True)
    cold = stats(
        samples,
        codes,
        cache="fría; un proceso Uvicorn nuevo por muestra",
        instancias=COLD_REPETITIONS,
    )
    assert hot is not None
    hot["cache"] = "caliente"
    return cold, hot


def measure_puts(session: requests.Session, base_url: str, devices: list[dict]) -> dict:
    if len(devices) < 10:
        return skipped(f"se requieren 10 equipos; el inventario devolvió {len(devices)}")
    # Warm-up no contabilizado; mandar el mismo valor evita un cambio lógico.
    warm = devices[0]
    timed_request(
        session,
        "PUT",
        f"{base_url}/api/devices/{warm['id']}",
        json={"description": warm.get("description", "")},
    )
    samples: list[float] = []
    codes: dict[str, int] = {}
    ids: list[str] = []
    for device in devices[:10]:
        elapsed, response = timed_request(
            session,
            "PUT",
            f"{base_url}/api/devices/{device['id']}",
            json={"description": device.get("description", "")},
        )
        samples.append(elapsed)
        key = status_key(response.status_code)
        codes[key] = codes.get(key, 0) + 1
        ids.append(device["id"])
    return stats(samples, codes, warmup=True, equipos=ids, cambio_logico=False)


def docker_available() -> tuple[bool, str]:
    if shutil.which("docker") is None:
        return False, "CLI docker no instalada"
    try:
        proc = subprocess.run(
            ["docker", "info"], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"docker info no disponible: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        return False, f"daemon Docker no disponible: {detail[-1] if detail else 'sin detalle'}"
    return True, "disponible"


def measure_event_loop(
    session: requests.Session,
    base_url: str,
    devices: list[dict],
    server_port: int,
    closed_port: int,
) -> dict:
    available, reason = docker_available()
    if not available:
        return skipped(reason, trigger="GET /api/devices/health (docker stats)")
    if not devices:
        return skipped("inventario vacío", trigger="GET /api/devices/health (docker stats)")

    target = devices[0]
    # Una conexión TCP exitosa hace que el endpoint ejecute _get_device_traffic()
    # (docker stats). Se restaura el puerto cerrado incluso ante error.
    timed_request(
        session,
        "PUT",
        f"{base_url}/api/devices/{target['id']}",
        json={"port": server_port},
    )
    trigger_result: dict[str, Any] = {}
    trigger_started = threading.Event()

    def trigger() -> None:
        trigger_started.set()
        elapsed, response = timed_request(
            session,
            "GET",
            f"{base_url}/api/devices/health",
            timeout=20,
        )
        trigger_result.update(ms=elapsed, status=response.status_code)

    def health(index: int) -> tuple[float, int]:
        # IPs de benchmark reservadas (RFC 2544): aíslan el rate-limit para que
        # las 20 peticiones midan el loop, no respuestas 429.
        local = requests.Session()
        elapsed, response = timed_request(
            local,
            "GET",
            f"{base_url}/api/health",
            headers={"X-Forwarded-For": f"198.18.0.{index + 1}"},
            timeout=20,
        )
        return elapsed, response.status_code

    samples: list[float] = []
    codes: dict[str, int] = {}
    error: Exception | None = None
    try:
        with ThreadPoolExecutor(max_workers=21) as pool:
            blocker = pool.submit(trigger)
            trigger_started.wait(timeout=1)
            # Solo da precedencia al request bloqueante; no simula carga.
            time.sleep(0.01)
            futures = [pool.submit(health, index) for index in range(20)]
            for future in as_completed(futures):
                elapsed, code = future.result()
                samples.append(elapsed)
                key = status_key(code)
                codes[key] = codes.get(key, 0) + 1
            blocker.result()
    except Exception as exc:  # restaurar el inventario antes de reportar
        error = exc
    finally:
        try:
            timed_request(
                session,
                "PUT",
                f"{base_url}/api/devices/{target['id']}",
                json={"port": closed_port},
            )
        except Exception as restore_exc:
            if error is None:
                error = restore_exc
    if error is not None:
        return failed(error, trigger="GET /api/devices/health (docker stats)")
    return stats(
        samples,
        codes,
        concurrentes=20,
        trigger="GET /api/devices/health (docker stats)",
        trigger_ms=round(float(trigger_result["ms"]), 3),
        trigger_status=trigger_result["status"],
        rate_limit="aislado con IPs RFC 2544 en X-Forwarded-For",
    )


def safe_metric(name: str, metrics: dict[str, dict], fn: Callable[[], dict]) -> None:
    try:
        metrics[name] = fn()
    except Exception as exc:
        metrics[name] = failed(exc)


def compare_reports(current: dict, baseline: dict) -> tuple[list[dict], list[str]]:
    regressions: list[dict] = []
    notes: list[str] = []
    old_metrics = baseline.get("métricas") or baseline.get("metricas") or {}
    for name, metric in current["métricas"].items():
        old = old_metrics.get(name)
        if not isinstance(old, dict):
            notes.append(f"{name}: ausente en referencia")
            continue
        if metric.get("estado") != "OK" or old.get("estado") != "OK":
            notes.append(f"{name}: no comparable ({old.get('estado')} → {metric.get('estado')})")
            continue
        before = old.get("p95_ms")
        after = metric.get("p95_ms")
        if not isinstance(before, (int, float)) or not isinstance(after, (int, float)) or before <= 0:
            notes.append(f"{name}: p95 no comparable")
            continue
        change_pct = (after / before - 1) * 100
        # Comparar valores evita que 30.0% exacto se vuelva 30.000000000000004.
        if after > before * 1.30:
            regressions.append(
                {
                    "métrica": name,
                    "p95_base_ms": before,
                    "p95_actual_ms": after,
                    "cambio_pct": round(change_pct, 1),
                    "umbral_pct": 30.0,
                }
            )
    if baseline.get("N") != current.get("N"):
        notes.append(f"N distinto: referencia={baseline.get('N')}, actual={current.get('N')}")
    return regressions, notes


def print_table(report: dict) -> None:
    print(f"\nNetPulse perf · {report.get('label') or 'sin etiqueta'} · N={report['N']}")
    print(f"commit: {report['commit'] or 'desconocido'}")
    print("+--------------------------------------+--------+-----+-----------+-----------+-----------+")
    print("| Métrica                              | Estado |   n | p50 ms    | p95 ms    | p99 ms    |")
    print("+--------------------------------------+--------+-----+-----------+-----------+-----------+")
    for name, metric in report["métricas"].items():
        state = str(metric.get("estado", "?"))
        n = str(metric.get("n", 0))
        values = [metric.get(key) for key in ("p50_ms", "p95_ms", "p99_ms")]
        shown = [f"{value:.3f}" if isinstance(value, (int, float)) else "—" for value in values]
        print(f"| {name[:36]:<36} | {state:<6} | {n:>3} | {shown[0]:>9} | {shown[1]:>9} | {shown[2]:>9} |")
    print("+--------------------------------------+--------+-----+-----------+-----------+-----------+")
    for name, metric in report["métricas"].items():
        if metric.get("estado") == "SKIP":
            print(f"SKIP {name}: {metric.get('motivo')}")
        elif metric.get("estado") == "ERROR":
            print(f"ERROR {name}: {metric.get('error')}")
    for regression in report.get("regresiones", []):
        print(
            f"REGRESIÓN {regression['métrica']}: p95 {regression['p95_base_ms']:.3f} → "
            f"{regression['p95_actual_ms']:.3f} ms ({regression['cambio_pct']:+.1f}%)"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", help="instancia existente; si se omite, arranca una temporal")
    parser.add_argument("--devices", type=int, default=500, help="equipos temporales (default: 500)")
    parser.add_argument("--user", default=os.getenv("NETPULSE_SMOKE_USER"), help="usuario admin")
    parser.add_argument(
        "--password", default=os.getenv("NETPULSE_SMOKE_PASSWORD"), help="password admin"
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="JSON de salida")
    parser.add_argument("--label", default="baseline", help="etiqueta del run")
    parser.add_argument("--compare", type=Path, help="JSON de referencia; exit 1 si p95 empeora >30%%")
    args = parser.parse_args()
    if args.devices < 10:
        parser.error("--devices debe ser >= 10 (el benchmark PUT usa 10 equipos)")
    if bool(args.user) != bool(args.password):
        parser.error("indica ambos --user y --password (o ninguno en modo temporal)")
    if args.base_url and not (args.user and args.password):
        parser.error("--base-url requiere --user/--password o NETPULSE_SMOKE_USER/PASSWORD")
    return args


def benchmark_server(
    base_url: str,
    username: str,
    password: str,
    metrics: dict[str, dict],
) -> None:
    session = login(base_url, username, password)
    devices_response = timed_request(session, "GET", f"{base_url}/api/devices")[1]
    devices = devices_response.json()

    safe_metric("devices_list", metrics, lambda: repeated_get(session, f"{base_url}/api/devices"))
    safe_metric("device_put_10", metrics, lambda: measure_puts(session, base_url, devices))
    safe_metric("dashboard", metrics, lambda: repeated_get(session, f"{base_url}/"))
    safe_metric("core_js", metrics, lambda: repeated_get(session, f"{base_url}/static/js/core.js"))


def main() -> int:
    args = parse_args()
    compare_data: dict | None = None
    if args.compare:
        try:
            compare_data = json.loads(args.compare.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"no se pudo leer --compare {args.compare}: {exc}") from exc

    commit, dirty, dirty_paths = git_info()
    metrics: dict[str, dict] = {}
    mode = "externa" if args.base_url else "uvicorn temporal"
    controlled_cold = not bool(args.base_url)

    if args.base_url:
        base_url = args.base_url.rstrip("/")
        try:
            cold, hot = measure_health(base_url, simple=True)
            cold["cache"] = "no controlada en instancia externa"
            metrics["health_simple_cold"] = cold
            metrics["health_simple_hot"] = hot
            cold, hot = measure_health(base_url, simple=False)
            cold["cache"] = "no controlada en instancia externa"
            metrics["health_full_cold"] = cold
            metrics["health_full_hot"] = hot
            benchmark_server(base_url, args.user, args.password, metrics)
            available, reason = docker_available()
            if not available:
                metrics["event_loop_health_during_docker_stats"] = skipped(
                    reason, trigger="GET /api/devices/health (docker stats)"
                )
            else:
                metrics["event_loop_health_during_docker_stats"] = skipped(
                    "modo --base-url no altera un equipo para forzar de forma segura la ruta docker stats",
                    trigger="GET /api/devices/health (docker stats)",
                )
        except Exception as exc:
            metrics.setdefault("setup", failed(exc))
    else:
        username = args.user or f"perf-{secrets.token_hex(4)}"
        password = args.password or secrets.token_urlsafe(24)
        closed_port = free_port()
        with WorkspaceSandbox(args.devices, closed_port, username, password):
            # Cada muestra fría usa un proceso nuevo: no hay esperas para vencer el TTL.
            try:
                cold, hot = measure_local_health(simple=True)
                metrics["health_simple_cold"] = cold
                metrics["health_simple_hot"] = hot
            except Exception as exc:
                metrics["health_simple_cold"] = failed(exc)
                metrics["health_simple_hot"] = failed(exc)
            try:
                cold, hot = measure_local_health(simple=False)
                metrics["health_full_cold"] = cold
                metrics["health_full_hot"] = hot
            except Exception as exc:
                metrics["health_full_cold"] = failed(exc)
                metrics["health_full_hot"] = failed(exc)
            try:
                with LocalServer(free_port()) as server:
                    benchmark_server(server.base_url, username, password, metrics)
            except Exception as exc:
                metrics.setdefault("setup", failed(exc))
            available, reason = docker_available()
            if not available:
                metrics["event_loop_health_during_docker_stats"] = skipped(
                    reason, trigger="GET /api/devices/health (docker stats)"
                )
            else:
                # Instancia nueva: ninguna entrada del health está cacheada.
                try:
                    with LocalServer(free_port()) as server:
                        session = login(server.base_url, username, password)
                        devices = timed_request(
                            session, "GET", f"{server.base_url}/api/devices"
                        )[1].json()
                        metrics["event_loop_health_during_docker_stats"] = measure_event_loop(
                            session, server.base_url, devices, server.port, closed_port
                        )
                except Exception as exc:
                    metrics["event_loop_health_during_docker_stats"] = failed(
                        exc, trigger="GET /api/devices/health (docker stats)"
                    )

    report: dict[str, Any] = {
        "schema": 1,
        "label": args.label,
        "commit": commit,
        "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "máquina": machine_info(),
        "N": args.devices,
        "métricas": metrics,
        "configuración": {
            "modo": mode,
            "base_url": args.base_url.rstrip("/") if args.base_url else "temporal (eliminada)",
            "hot_repetitions": HOT_REPETITIONS,
            "cold_repetitions": COLD_REPETITIONS if controlled_cold else 1,
            "request_timeout_s": REQUEST_TIMEOUT_S,
            "cold_cache_controlada": controlled_cold,
            "working_tree_dirty_al_medir": dirty,
            "dirty_paths_al_medir": dirty_paths,
        },
        "regresiones": [],
        "notas_comparación": [],
    }
    if compare_data is not None:
        regressions, notes = compare_reports(report, compare_data)
        report["regresiones"] = regressions
        report["notas_comparación"] = notes

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print_table(report)
    print(f"JSON: {args.out}")

    if report["regresiones"]:
        return 1
    errors = [name for name, metric in metrics.items() if metric.get("estado") == "ERROR"]
    return 2 if errors else 0


if __name__ == "__main__":
    # SIGTERM también pasa por finally/context managers para restaurar el worktree.
    signal.signal(signal.SIGTERM, lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("benchmark interrumpido; limpieza aplicada", file=sys.stderr)
        raise SystemExit(130)
