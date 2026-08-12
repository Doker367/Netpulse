"""NetPulse — Scheduler Service.

Programa backups únicos y recurrentes usando threading.Timer.
Los trabajos se persisten en scheduler_jobs.json.
"""

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.services.napalm_svc import backup_config

logger = logging.getLogger(__name__)

# Ruta al archivo de persistencia de trabajos
JOBS_FILE = Path(__file__).resolve().parent.parent.parent / "config" / "scheduler_jobs.json"

# Almacén en memoria de timers activos (threading.Timer objects)
_active_timers: dict[str, threading.Timer] = {}

# Lock para acceso concurrente a _active_timers y JOBS_FILE
_lock = threading.Lock()


def _load_jobs() -> list[dict]:
    """Carga trabajos desde el archivo JSON."""
    if not JOBS_FILE.exists():
        return []
    try:
        with open(JOBS_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Error loading scheduler_jobs.json: %s", e)
        return []


def _save_jobs(jobs: list[dict]):
    """Guarda trabajos al archivo JSON."""
    JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(JOBS_FILE, "w") as f:
        json.dump(jobs, f, indent=2, ensure_ascii=False)


def _generate_job_id() -> str:
    """Genera un ID único para un trabajo."""
    return f"job-{uuid.uuid4().hex[:12]}"


def _run_backup(device_id: str, job_id: str):
    """Ejecuta el backup y actualiza el estado del trabajo."""
    logger.info("[scheduler] Running backup for device %s (job %s)", device_id, job_id)
    with _lock:
        jobs = _load_jobs()
        for j in jobs:
            if j["job_id"] == job_id:
                j["status"] = "running"
                _save_jobs(jobs)
                break

    try:
        result = backup_config(device_id)
        with _lock:
            jobs = _load_jobs()
            for j in jobs:
                if j["job_id"] == job_id:
                    j["status"] = "completed" if result.success else "failed"
                    j["error"] = result.error
                    _save_jobs(jobs)
                    break
        if result.success:
            logger.info("[scheduler] Backup completed for %s (job %s)", device_id, job_id)
        else:
            logger.warning("[scheduler] Backup failed for %s (job %s): %s", device_id, job_id, result.error)
    except Exception as e:
        logger.error("[scheduler] Backup exception for %s (job %s): %s", device_id, job_id, e)
        with _lock:
            jobs = _load_jobs()
            for j in jobs:
                if j["job_id"] == job_id:
                    j["status"] = "failed"
                    j["error"] = str(e)
                    _save_jobs(jobs)
                    break
    finally:
        with _lock:
            _active_timers.pop(job_id, None)


def _run_recurring_backup(device_id: str, job_id: str, interval_minutes: int):
    """Ejecuta backup recurrente y reprograma."""
    _run_backup(device_id, job_id)
    # Si el trabajo sigue activo (no fue cancelado), reprogramar
    with _lock:
        if job_id in _active_timers:
            timer = threading.Timer(
                interval_minutes * 60.0,
                _run_recurring_backup,
                args=(device_id, job_id, interval_minutes),
            )
            timer.daemon = True
            timer.start()
            _active_timers[job_id] = timer
            logger.info(
                "[scheduler] Rescheduled recurring backup for %s (job %s, interval=%d min)",
                device_id, job_id, interval_minutes,
            )


def schedule_backup(device_id: str, delay_minutes: int) -> str:
    """Programa un backup único.

    Args:
        device_id: ID del dispositivo.
        delay_minutes: Minutos de espera antes del backup.

    Returns:
        job_id del trabajo creado.
    """
    job_id = _generate_job_id()
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    job = {
        "job_id": job_id,
        "device_id": device_id,
        "job_type": "backup",
        "delay_minutes": delay_minutes,
        "interval_minutes": None,
        "created_at": created_at,
        "status": "scheduled",
        "error": None,
    }

    with _lock:
        jobs = _load_jobs()
        jobs.append(job)
        _save_jobs(jobs)

        timer = threading.Timer(
            delay_minutes * 60.0,
            _run_backup,
            args=(device_id, job_id),
        )
        timer.daemon = True
        timer.start()
        _active_timers[job_id] = timer

    logger.info("[scheduler] Scheduled backup for %s in %d min (job %s)", device_id, delay_minutes, job_id)
    return job_id


def schedule_recurring(device_id: str, interval_minutes: int) -> str:
    """Programa backups recurrentes.

    Args:
        device_id: ID del dispositivo.
        interval_minutes: Intervalo en minutos entre backups.

    Returns:
        job_id del trabajo creado.
    """
    job_id = _generate_job_id()
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    job = {
        "job_id": job_id,
        "device_id": device_id,
        "job_type": "recurring",
        "delay_minutes": None,
        "interval_minutes": interval_minutes,
        "created_at": created_at,
        "status": "scheduled",
        "error": None,
    }

    with _lock:
        jobs = _load_jobs()
        jobs.append(job)
        _save_jobs(jobs)

        timer = threading.Timer(
            interval_minutes * 60.0,
            _run_recurring_backup,
            args=(device_id, job_id, interval_minutes),
        )
        timer.daemon = True
        timer.start()
        _active_timers[job_id] = timer

    logger.info(
        "[scheduler] Scheduled recurring backup for %s every %d min (job %s)",
        device_id, interval_minutes, job_id,
    )
    return job_id


# ── Reportes programados ─────────────────────────────────────

def _run_report(job_id: str):
    """Genera los reportes CSV (inventario + health) y actualiza el job."""
    logger.info("[scheduler] Generating reports (job %s)", job_id)
    with _lock:
        jobs = _load_jobs()
        for j in jobs:
            if j["job_id"] == job_id:
                j["status"] = "running"
                _save_jobs(jobs)
                break

    try:
        from datetime import datetime as _dt
        from app.core.settings import REPORT_DIR
        from app.services.reports import csv_report

        ts = _dt.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        # Inventario
        inv_path = REPORT_DIR / f"inventory_{ts}.csv"
        inv_path.write_text(csv_report.generate_inventory_csv(), encoding="utf-8")

        # Health scores
        health_path = REPORT_DIR / f"health_{ts}.csv"
        try:
            from app.services.ops import health_svc
            rows = health_svc.health_all()
            health_path.write_text(csv_report.generate_devices_csv(rows), encoding="utf-8")
        except Exception as e:
            logger.warning("[scheduler] Health report falló: %s", e)

        with _lock:
            jobs = _load_jobs()
            for j in jobs:
                if j["job_id"] == job_id:
                    j["status"] = "completed"
                    j["error"] = None
                    _save_jobs(jobs)
                    break
        logger.info("[scheduler] Reports generados: %s", inv_path)
    except Exception as e:
        logger.error("[scheduler] Report job %s falló: %s", job_id, e)
        with _lock:
            jobs = _load_jobs()
            for j in jobs:
                if j["job_id"] == job_id:
                    j["status"] = "failed"
                    j["error"] = str(e)
                    _save_jobs(jobs)
                    break
    finally:
        with _lock:
            _active_timers.pop(job_id, None)


def schedule_report(delay_minutes: int = 1, interval_minutes: Optional[int] = None) -> str:
    """Programa generación de reportes CSV.

    Args:
        delay_minutes: Minutos hasta la primera ejecución.
        interval_minutes: Si se da, repite cada N minutos; si no, una sola vez.

    Returns:
        job_id del trabajo creado.
    """
    job_id = _generate_job_id()
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    job = {
        "job_id": job_id,
        "device_id": "all",
        "job_type": "report",
        "delay_minutes": delay_minutes,
        "interval_minutes": interval_minutes,
        "created_at": created_at,
        "status": "scheduled",
        "error": None,
    }

    def _run_once():
        _run_report(job_id)
        if interval_minutes and job_id in _active_timers:
            with _lock:
                if job_id in _active_timers:
                    timer = threading.Timer(interval_minutes * 60.0, _run_once)
                    timer.daemon = True
                    timer.start()
                    _active_timers[job_id] = timer

    with _lock:
        jobs = _load_jobs()
        jobs.append(job)
        _save_jobs(jobs)

        timer = threading.Timer(delay_minutes * 60.0, _run_once)
        timer.daemon = True
        timer.start()
        _active_timers[job_id] = timer

    logger.info("[scheduler] Report programado en %d min (job %s, interval=%s)",
                delay_minutes, job_id, interval_minutes)
    return job_id


def cancel_job(job_id: str) -> bool:
    """Cancela un trabajo programado.

    Args:
        job_id: ID del trabajo a cancelar.

    Returns:
        True si se canceló exitosamente, False si no se encontró.
    """
    with _lock:
        # Cancelar timer si está activo
        timer = _active_timers.pop(job_id, None)
        if timer:
            timer.cancel()

        # Actualizar estado en persistencia
        jobs = _load_jobs()
        found = False
        for j in jobs:
            if j["job_id"] == job_id:
                j["status"] = "cancelled"
                found = True
                break

        if found:
            _save_jobs(jobs)
            logger.info("[scheduler] Cancelled job %s", job_id)
            return True

    logger.warning("[scheduler] Job %s not found for cancellation", job_id)
    return False


def list_jobs() -> list[dict]:
    """Lista todos los trabajos programados.

    Returns:
        Lista de trabajos con su estado actual.
    """
    with _lock:
        return _load_jobs()
