"""NetPulse — Compliance Validation Service.

Validación de configuración contra líneas base (baselines)
almacenadas en config/baselines/ (un archivo por grupo).
"""

import difflib
import logging
from pathlib import Path
from typing import Optional

from app.core.settings import CONFIG_DIR
from app.services import napalm_svc
from app.services.inventory_svc import list_devices

logger = logging.getLogger(__name__)

BASELINES_DIR = CONFIG_DIR / "baselines"


# ── Helpers ──────────────────────────────────────────────────


def _ensure_baselines_dir():
    """Crea config/baselines/ si no existe."""
    BASELINES_DIR.mkdir(parents=True, exist_ok=True)


def _baseline_path(group: str) -> Path:
    """Retorna la ruta al archivo baseline de un grupo."""
    return BASELINES_DIR / f"{group}.cfg"


# ── Baseline CRUD ────────────────────────────────────────────


def get_baseline(group: str) -> Optional[str]:
    """Obtiene la configuración baseline de un grupo de dispositivos.

    Args:
        group: Nombre del grupo (tag) de dispositivos.

    Returns:
        Configuración baseline como string, o None si no existe.
    """
    path = _baseline_path(group)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def set_baseline(group: str, config: str) -> str:
    """Establece la configuración baseline para un grupo.

    Args:
        group: Nombre del grupo (tag).
        config: Configuración baseline (texto plano).

    Returns:
        La configuración almacenada.
    """
    _ensure_baselines_dir()
    path = _baseline_path(group)
    path.write_text(config, encoding="utf-8")
    logger.info("Baseline actualizado para grupo '%s' (%d bytes)", group, len(config))
    return config


def list_baseline_groups() -> list[str]:
    """Lista los grupos que tienen baseline configurado."""
    _ensure_baselines_dir()
    return sorted([p.stem for p in BASELINES_DIR.glob("*.cfg")])


# ── Compliance Checking ──────────────────────────────────────


def _get_device_group(device_id: str) -> Optional[str]:
    """Obtiene el primer grupo (tag) del dispositivo para buscar baseline."""
    devices = list_devices()
    for d in devices:
        if d["id"] == device_id:
            tags = d.get("tags", [])
            if tags:
                return tags[0]  # primer tag como grupo
            return "default"
    return None


def check_compliance(device_id: str, baseline: Optional[str] = None) -> dict:
    """Verifica compliance de un dispositivo contra su baseline.

    Args:
        device_id: ID del dispositivo.
        baseline: Config baseline (si es None, se busca por grupo).

    Returns:
        Dict con:
            device_id: str
            compliant: bool
            violations: list[{"line": int, "expected": str, "got": str}]
            baseline_group: str | None
    """
    result = {
        "device_id": device_id,
        "compliant": False,
        "violations": [],
        "baseline_group": None,
    }

    # Obtener baseline si no se proporcionó
    if baseline is None:
        group = _get_device_group(device_id)
        if not group:
            result["violations"].append({
                "line": 0,
                "expected": "Asignar dispositivo a un grupo (tag)",
                "got": "Sin grupo",
            })
            return result
        result["baseline_group"] = group
        baseline = get_baseline(group)
        if baseline is None:
            result["violations"].append({
                "line": 0,
                "expected": f"Baseline para grupo '{group}' no configurado",
                "got": "Sin baseline",
            })
            return result

    # Obtener running config del dispositivo
    cfg_result = napalm_svc.get_config(device_id, retrieve="running")
    if not cfg_result.success:
        result["violations"].append({
            "line": 0,
            "expected": "Poder obtener running-config",
            "got": cfg_result.error or "Error de conexión",
        })
        return result

    running = ""
    if isinstance(cfg_result.data, dict):
        running = cfg_result.data.get("running", "")
    else:
        running = str(cfg_result.data) if cfg_result.data else ""

    expected_lines = baseline.strip().splitlines()
    actual_lines = running.strip().splitlines()

    violations = []
    max_lines = max(len(expected_lines), len(actual_lines))

    for i in range(max_lines):
        expected = expected_lines[i] if i < len(expected_lines) else ""
        got = actual_lines[i] if i < len(actual_lines) else ""
        if expected.rstrip() != got.rstrip():
            violations.append({
                "line": i + 1,
                "expected": expected,
                "got": got,
            })

    result["compliant"] = len(violations) == 0
    result["violations"] = violations

    logger.info(
        "Compliance para '%s': %s (%d violaciones)",
        device_id,
        "COMPLIANT" if result["compliant"] else "NON-COMPLIANT",
        len(violations),
    )
    return result


def generate_report(device_ids: Optional[list[str]] = None) -> dict:
    """Genera un reporte completo de compliance para todos (o algunos) dispositivos.

    Args:
        device_ids: Lista opcional de device_ids. Si es None, todos.

    Returns:
        Dict con summary + resultados por dispositivo.
    """
    devices = list_devices()
    if device_ids:
        devices = [d for d in devices if d["id"] in device_ids]

    results = {}
    total = len(devices)
    compliant_count = 0
    non_compliant_count = 0
    error_count = 0

    for d in devices:
        dev_id = d["id"]
        try:
            dev_result = check_compliance(dev_id)
            results[dev_id] = dev_result
            if dev_result.get("error"):
                error_count += 1
            elif dev_result["compliant"]:
                compliant_count += 1
            else:
                non_compliant_count += 1
        except Exception as e:
            error_count += 1
            results[dev_id] = {
                "device_id": dev_id,
                "compliant": False,
                "violations": [{"line": 0, "expected": "Sin errores", "got": str(e)}],
                "error": str(e),
            }

    return {
        "summary": {
            "total": total,
            "compliant": compliant_count,
            "non_compliant": non_compliant_count,
            "errors": error_count,
        },
        "devices": results,
    }
