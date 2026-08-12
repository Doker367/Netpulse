"""NetPulse — Detección de Drift de Configuración.

Compara la running-config actual contra un baseline guardado en
config/baselines/<device_id>.cfg. Cuando hay drift, la configuración
nueva se archiva en config/drift_history/<device_id>_<timestamp>.cfg.
"""

import hashlib
import logging
from datetime import datetime, timezone

from app.core.settings import CONFIG_DIR
from app.services import inventory_svc, napalm_svc

logger = logging.getLogger(__name__)

BASELINES_DIR = CONFIG_DIR / "baselines"
DRIFT_HISTORY_DIR = CONFIG_DIR / "drift_history"


def _config_text(result) -> str:
    """Extrae el texto de config de un NapalmResult (dict o string)."""
    data = result.data
    if isinstance(data, dict):
        text = data.get("running") or ""
        if not text:
            text = str(data)
    else:
        text = str(data or "")
    return text


def _sha256(text: str) -> str:
    """Hash sha256 de un texto de configuración."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def check_drift(device_id: str) -> dict:
    """Compara la config actual contra el baseline del dispositivo.

    - Si no existe baseline, lo crea con la config actual (drift=False).
    - Si el hash sha256 difiere, archiva la config nueva en
      drift_history y devuelve drift=True con la ruta del diff.
    - Si coinciden, devuelve drift=False.
    """
    result = napalm_svc.get_config(device_id)
    if not result.success or not result.data:
        logger.warning("[%s] check_drift: no se pudo obtener config: %s",
                       device_id, result.error)
        return {
            "device_id": device_id,
            "drift": False,
            "error": result.error or "No se pudo obtener la configuración",
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    current = _config_text(result)
    current_hash = _sha256(current)
    checked_at = datetime.now(timezone.utc)

    BASELINES_DIR.mkdir(parents=True, exist_ok=True)
    baseline_path = BASELINES_DIR / f"{device_id}.cfg"

    # Sin baseline → crear uno con la config actual
    if not baseline_path.exists():
        baseline_path.write_text(current, encoding="utf-8")
        logger.info("[%s] Baseline creado: %s", device_id, baseline_path)
        return {
            "device_id": device_id,
            "drift": False,
            "baseline_created": True,
            "baseline_hash": current_hash,
            "baseline_path": str(baseline_path),
            "checked_at": checked_at.isoformat(),
        }

    baseline_hash = _sha256(baseline_path.read_text(encoding="utf-8"))

    if baseline_hash == current_hash:
        return {
            "device_id": device_id,
            "drift": False,
            "checked_at": checked_at.isoformat(),
        }

    # Drift detectado → archivar la config actual
    DRIFT_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    ts = checked_at.strftime("%Y%m%d_%H%M%S")
    diff_path = DRIFT_HISTORY_DIR / f"{device_id}_{ts}.cfg"
    diff_path.write_text(current, encoding="utf-8")
    logger.warning("[%s] Drift detectado → %s", device_id, diff_path)

    return {
        "device_id": device_id,
        "drift": True,
        "baseline_hash": baseline_hash,
        "current_hash": current_hash,
        "changed_at": checked_at.isoformat(),
        "diff_path": str(diff_path),
    }


def run_drift_check_all() -> list[dict]:
    """Ejecuta check_drift sobre todos los dispositivos del inventario."""
    results = []
    for device in inventory_svc.list_devices():
        device_id = device["id"]
        try:
            results.append(check_drift(device_id))
        except Exception as e:
            logger.exception("[%s] Error en check_drift: %s", device_id, e)
            results.append({
                "device_id": device_id,
                "drift": False,
                "error": str(e),
                "checked_at": datetime.now(timezone.utc).isoformat(),
            })
    return results
