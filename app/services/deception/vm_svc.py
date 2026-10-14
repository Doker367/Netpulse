"""NetPulse — Máquinas señuelo (VMs con QEMU).

Permite crear VMs señuelo, subirles una ISO y arrancarlas con QEMU. El
registro vive en ``config/deception/vms.json``; las ISO en
``config/deception/isos/`` y los discos en ``deception/vms/``. La consola
gráfica se expone por VNC (``vnc://<host>:590N``).

Requiere QEMU en el host (``brew install qemu`` / ``apt install qemu-system``).
Si no está instalado, ``start`` devuelve un error claro.
"""

import json
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from app.core.settings import (
    VMS_DIR,
    VMS_ENABLED,
    VMS_ISO_DIR,
    VMS_REGISTRY,
    VMS_SERIAL_BASE_PORT,
    VMS_VNC_BASE_PORT,
)

logger = logging.getLogger(__name__)
_lock = threading.Lock()

_ARCH_BIN = {
    "x86_64": "qemu-system-x86_64",
    "aarch64": "qemu-system-aarch64",
}


def qemu_available() -> dict:
    """Qué binarios de QEMU están disponibles en el host."""
    return {arch: bool(shutil.which(bin_)) for arch, bin_ in _ARCH_BIN.items()}


def is_enabled() -> bool:
    return VMS_ENABLED


# ── Registro ─────────────────────────────────────────────────


def _read() -> list[dict]:
    if not VMS_REGISTRY.exists():
        return []
    try:
        return json.loads(VMS_REGISTRY.read_text(encoding="utf-8")).get("vms", [])
    except (json.JSONDecodeError, OSError):
        return []


def _write(vms: list[dict]) -> None:
    VMS_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    tmp = VMS_REGISTRY.with_suffix(".tmp")
    tmp.write_text(json.dumps({"vms": vms}, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(VMS_REGISTRY)


def _pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _decorate(vm: dict) -> dict:
    running = _pid_alive(vm.get("pid"))
    out = dict(vm)
    out["running"] = running
    out["status"] = "running" if running else "stopped"
    out["vnc"] = f"vnc://127.0.0.1:{vm.get('vnc_port')}" if vm.get("vnc_port") else None
    return out


def _emit_event(action: str, vm: dict) -> None:
    """Registra un evento de ciclo de vida de la VM en la telemetría de deception."""
    try:
        from app.services.deception import telemetry_svc
        telemetry_svc.emit_sync(
            f"vm-{(vm.get('id') or '').strip()}",
            "127.0.0.1",
            "vm",
            "other",
            detail={"action": action, "name": vm.get("name"), "os": vm.get("os"),
                    "arch": vm.get("arch"), "vnc": vm.get("vnc_port"),
                    "serial": vm.get("serial_port")},
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[vms] no se pudo emitir evento %s: %s", action, exc)


def list_vms() -> list[dict]:
    return [_decorate(vm) for vm in _read()]


def get_vm(vm_id: str) -> Optional[dict]:
    for vm in _read():
        if vm["id"] == vm_id:
            return _decorate(vm)
    return None


# ── ISO ──────────────────────────────────────────────────────


def list_isos() -> list[dict]:
    VMS_ISO_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for f in sorted(VMS_ISO_DIR.glob("*.iso")) + sorted(VMS_ISO_DIR.glob("*.img")):
        out.append({"filename": f.name, "size": f.stat().st_size,
                    "path": str(f)})
    return out


def save_iso(filename: str, data: bytes) -> dict:
    """Guarda una ISO subida (nombre saneado) en VMS_ISO_DIR."""
    safe = os.path.basename(filename).replace(" ", "_")
    if not safe.lower().endswith((".iso", ".img")):
        safe += ".iso"
    VMS_ISO_DIR.mkdir(parents=True, exist_ok=True)
    dest = VMS_ISO_DIR / safe
    dest.write_bytes(data)
    return {"filename": safe, "size": dest.stat().st_size}


def _resolve_iso(iso: Optional[str]) -> Optional[str]:
    if not iso:
        return None
    candidate = Path(iso)
    if not candidate.is_absolute():
        candidate = VMS_ISO_DIR / os.path.basename(iso)
    if candidate.exists():
        return str(candidate)
    return None


# ── CRUD ─────────────────────────────────────────────────────


def create_vm(name: str, os_name: str = "linux", arch: str = "x86_64",
              ram_mb: int = 1024, cpus: int = 1, iso: Optional[str] = None) -> dict:
    """Crea una VM señuelo (sin arrancarla)."""
    arch = arch if arch in _ARCH_BIN else "x86_64"
    with _lock:
        vms = _read()
        used_vnc = {vm.get("vnc_port") for vm in vms}
        used_ser = {vm.get("serial_port") for vm in vms}
        vnc = VMS_VNC_BASE_PORT
        while vnc in used_vnc:
            vnc += 1
        serial = VMS_SERIAL_BASE_PORT
        while serial in used_ser:
            serial += 1
        vm = {
            "id": uuid.uuid4().hex[:8],
            "name": name.strip() or f"vm-{len(vms)+1}",
            "os": os_name,
            "arch": arch,
            "ram_mb": max(256, int(ram_mb)),
            "cpus": max(1, int(cpus)),
            "iso": iso,
            "vnc_port": vnc,
            "serial_port": serial,
            "pid": None,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        vms.append(vm)
        _write(vms)
    _emit_event("create", vm)
    return _decorate(vm)


def _disk_path(vm_id: str) -> Path:
    VMS_DIR.mkdir(parents=True, exist_ok=True)
    return VMS_DIR / f"{vm_id}.qcow2"


def _ensure_disk(vm_id: str, size_gb: int = 8) -> Optional[str]:
    """Crea un disco qcow2 si existe qemu-img; si no, None (boot live)."""
    disk = _disk_path(vm_id)
    if disk.exists():
        return str(disk)
    qemu_img = shutil.which("qemu-img")
    if not qemu_img:
        return None
    try:
        subprocess.run([qemu_img, "create", "-f", "qcow2", str(disk), f"{size_gb}G"],
                       capture_output=True, timeout=30, check=True)
        return str(disk)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[vms] no se pudo crear disco: %s", exc)
        return None


def start_vm(vm_id: str) -> dict:
    """Arranca una VM con QEMU (daemonizada, consola por VNC)."""
    vm = get_vm(vm_id)
    if not vm:
        return {"success": False, "error": "VM no encontrada"}
    if vm["running"]:
        return {"success": True, "already": True, "vnc": vm["vnc"]}

    arch = vm.get("arch", "x86_64")
    binary = _ARCH_BIN.get(arch, "qemu-system-x86_64")
    exe = shutil.which(binary)
    if not exe:
        return {"success": False, "error": f"QEMU no instalado ({binary}). Instala con 'brew install qemu'."}

    iso = _resolve_iso(vm.get("iso"))
    if not iso:
        return {"success": False, "error": "ISO no encontrada; sube una ISO y asígnala a la VM"}

    disk = _ensure_disk(vm_id)
    pidfile = VMS_DIR / f"{vm_id}.pid"
    vnc_display = f":{int(vm['vnc_port']) - 5900}"

    cmd = [exe, "-name", vm["name"], "-m", str(vm["ram_mb"]), "-smp", str(vm["cpus"]),
           "-cdrom", iso, "-boot", "d", "-vnc", vnc_display, "-display", "none",
           "-daemonize", "-pidfile", str(pidfile)]
    if vm.get("serial_port"):
        cmd += ["-serial", f"telnet:127.0.0.1:{vm['serial_port']},server,nowait"]
    if arch == "aarch64":
        cmd += ["-machine", "virt"]
    if disk:
        cmd += ["-drive", f"file={disk},if=virtio,format=qcow2"]

    try:
        VMS_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(cmd, capture_output=True, timeout=30, check=True)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode("utf-8", "replace")[:300]
        return {"success": False, "error": f"QEMU falló: {detail or exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}

    with _lock:
        vms = _read()
        for v in vms:
            if v["id"] == vm_id:
                v["pid"] = int(pidfile.read_text().strip()) if pidfile.exists() else None
        _write(vms)
    vm_after = get_vm(vm_id)
    _emit_event("start", vm_after or vm)
    return {"success": True, "vnc": vm["vnc"], "pid": vm_after.get("pid") if vm_after else None}


def stop_vm(vm_id: str) -> dict:
    """Detiene una VM (SIGTERM al proceso QEMU)."""
    vm = get_vm(vm_id)
    if not vm:
        return {"success": False, "error": "VM no encontrada"}
    pid = vm.get("pid")
    if _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.5)
        except OSError as exc:
            return {"success": False, "error": str(exc)}
    with _lock:
        vms = _read()
        for v in vms:
            if v["id"] == vm_id:
                v["pid"] = None
        _write(vms)
    _emit_event("stop", vm)
    return {"success": True}


def delete_vm(vm_id: str) -> bool:
    """Detiene y elimina una VM (registro + disco)."""
    vm = get_vm(vm_id)
    if not vm:
        return False
    if vm.get("running"):
        stop_vm(vm_id)
    with _lock:
        vms = [v for v in _read() if v["id"] != vm_id]
        _write(vms)
    for p in (_disk_path(vm_id), VMS_DIR / f"{vm_id}.pid"):
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass
    _emit_event("delete", vm)
    return True
