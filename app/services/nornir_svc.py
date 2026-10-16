"""NetPulse — Nornir Parallel Operations Service.

Wrapper around Nornir 3.x para ejecutar operaciones NAPALM
en paralelo sobre múltiples dispositivos. Si Nornir falla,
hace fallback a ejecución serial vía napalm_svc.
"""

from datetime import datetime
from typing import Optional

from nornir.core import Nornir
from nornir.core.configuration import Config
from nornir.core.inventory import Inventory, Host, Hosts, Groups, Defaults
from nornir.core.state import GlobalState
from nornir.plugins.runners import ThreadedRunner

from app.core.settings import BACKUP_DIR, NAPALM_TIMEOUT
from app.services.napalm_svc import NapalmResult, _load_devices


# ── Helpers ──────────────────────────────────────────────────

def _filter_valid_devices(raw_devices: list[dict]) -> list[dict]:
    """Filtra entradas inválidas/template del inventario YAML."""
    valid = []
    for d in raw_devices:
        dev_id = d.get("id", "")
        driver = d.get("driver", "")
        if not isinstance(dev_id, str) or not isinstance(driver, str):
            continue
        if dev_id in ("string", "", None) or driver in ("string", "", None):
            continue
        if not d.get("hostname") or d["hostname"] in ("string", ""):
            continue
        valid.append(d)
    return valid


def _build_nornir(device_ids: Optional[list[str]] = None) -> Nornir:
    """Construye objeto Nornir con inventario dinámico desde devices.yaml."""
    raw = _load_devices()
    devices = _filter_valid_devices(raw)
    if device_ids:
        devices = [d for d in devices if d["id"] in device_ids]

    hosts = Hosts()
    for d in devices:
        creds = d.get("credentials", {})
        hosts[d["id"]] = Host(
            name=d["id"],
            hostname=d["hostname"],
            username=creds.get("username", ""),
            password=creds.get("password", ""),
            port=d.get("port", 22),
            data={
                "driver": d["driver"],
                "device_type": d.get("type", "router"),
            },
        )

    inventory = Inventory(hosts=hosts, groups=Groups(), defaults=Defaults())
    pool_size = 10
    runner = ThreadedRunner(num_workers=min(pool_size, len(hosts) or 1))
    config = Config()
    data = GlobalState(dry_run=False)

    return Nornir(inventory=inventory, runner=runner, config=config, data=data)


# ── Nornir Tasks ─────────────────────────────────────────────

def _task_get_facts(task):
    """Nornir task: obtiene facts vía NAPALM."""
    from napalm import get_network_driver

    device_id = task.host.name
    result = NapalmResult(device_id)

    try:
        driver_name = task.host.data.get("driver", "ios")
        driver_cls = get_network_driver(driver_name)
        opts = {"timeout": NAPALM_TIMEOUT}
        if driver_name == "eos":
            opts["transport"] = "https"

        with driver_cls(
            hostname=task.host.hostname,
            username=task.host.username,
            password=task.host.password,
            optional_args=opts,
            timeout=NAPALM_TIMEOUT,
        ) as dev:
            dev.open()
            result.data = dev.get_facts()
            dev.close()
            result.success = True
    except Exception as e:
        result.error = str(e)

    return result


def _task_backup(task):
    """Nornir task: backup de running config vía NAPALM."""
    from napalm import get_network_driver

    device_id = task.host.name
    result = NapalmResult(device_id)

    try:
        driver_name = task.host.data.get("driver", "ios")
        driver_cls = get_network_driver(driver_name)
        opts = {"timeout": NAPALM_TIMEOUT}
        if driver_name == "eos":
            opts["transport"] = "https"

        with driver_cls(
            hostname=task.host.hostname,
            username=task.host.username,
            password=task.host.password,
            optional_args=opts,
            timeout=NAPALM_TIMEOUT,
        ) as dev:
            dev.open()
            config = dev.get_config(retrieve="running")
            dev.close()

        if config and isinstance(config, dict):
            running = config.get("running", "")
        else:
            running = str(config) if config else ""

        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        fname = BACKUP_DIR / f"{device_id}_{ts}.cfg"
        fname.write_text(running)
        result.data = {"file": str(fname), "size": len(running), "timestamp": ts}
        result.success = True
    except Exception as e:
        result.error = str(e)

    return result


def _task_check_status(task):
    """Nornir task: verifica conectividad básica (get_facts ligero)."""
    from napalm import get_network_driver

    device_id = task.host.name
    result = {
        "device_id": device_id,
        "online": False,
        "driver": task.host.data.get("driver", "unknown"),
        "hostname": task.host.hostname,
        "os_version": "",
        "error": None,
    }

    try:
        driver_name = task.host.data.get("driver", "ios")
        driver_cls = get_network_driver(driver_name)
        opts = {"timeout": NAPALM_TIMEOUT}
        if driver_name == "eos":
            opts["transport"] = "https"

        with driver_cls(
            hostname=task.host.hostname,
            username=task.host.username,
            password=task.host.password,
            optional_args=opts,
            timeout=NAPALM_TIMEOUT,
        ) as dev:
            dev.open()
            facts = dev.get_facts()
            dev.close()

        result["online"] = True
        result["os_version"] = facts.get("os_version", "") if facts else ""
    except Exception as e:
        result["error"] = str(e)

    return result


# ── Public API ───────────────────────────────────────────────

def bulk_get_facts(device_ids: Optional[list[str]] = None) -> list[NapalmResult]:
    """Ejecuta get_facts en paralelo vía Nornir. Fallback serial."""
    try:
        nr = _build_nornir(device_ids)
        agg = nr.run(task=_task_get_facts)
        results = []
        for host_name, multi_result in agg.items():
            if multi_result.failed:
                if multi_result:
                    results.append(multi_result[0].result)
                else:
                    r = NapalmResult(host_name)
                    r.error = (
                        str(multi_result.exception)
                        if multi_result.exception
                        else "Unknown Nornir error"
                    )
                    results.append(r)
            else:
                results.append(multi_result[0].result)
        return results
    except Exception:
        from app.services import napalm_svc
        return napalm_svc.bulk_get_facts(device_ids)


def bulk_backup(device_ids: Optional[list[str]] = None) -> list[NapalmResult]:
    """Backup de config en paralelo vía Nornir. Fallback serial."""
    try:
        nr = _build_nornir(device_ids)
        agg = nr.run(task=_task_backup)
        results = []
        for host_name, multi_result in agg.items():
            if multi_result.failed:
                if multi_result:
                    results.append(multi_result[0].result)
                else:
                    r = NapalmResult(host_name)
                    r.error = (
                        str(multi_result.exception)
                        if multi_result.exception
                        else "Unknown Nornir error"
                    )
                    results.append(r)
            else:
                results.append(multi_result[0].result)
        return results
    except Exception:
        from app.services import napalm_svc

        if device_ids is None:
            raw = _load_devices()
            valid = _filter_valid_devices(raw)
            device_ids = [d["id"] for d in valid]
        return [napalm_svc.backup_config(did) for did in device_ids]


def bulk_check_status(device_ids: Optional[list[str]] = None) -> list[dict]:
    """Verifica estado online/offline en paralelo vía Nornir. Fallback serial."""
    try:
        nr = _build_nornir(device_ids)
        agg = nr.run(task=_task_check_status)
        results = []
        for host_name, multi_result in agg.items():
            if multi_result.failed:
                if multi_result:
                    results.append(multi_result[0].result)
                else:
                    results.append(
                        {
                            "device_id": host_name,
                            "online": False,
                            "driver": "unknown",
                            "hostname": host_name,
                            "os_version": "",
                            "error": (
                                str(multi_result.exception)
                                if multi_result.exception
                                else "Unknown Nornir error"
                            ),
                        }
                    )
            else:
                results.append(multi_result[0].result)
        return results
    except Exception:
        from app.services import napalm_svc

        if device_ids is None:
            raw = _load_devices()
            valid = _filter_valid_devices(raw)
            device_ids = [d["id"] for d in valid]

        results = []
        for did in device_ids:
            r = napalm_svc.check_device(did)
            status = {
                "device_id": did,
                "online": r.success,
                "driver": "unknown",
                "hostname": did,
                "os_version": "",
                "error": r.error if not r.success else None,
            }
            results.append(status)
        return results
