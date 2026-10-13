"""NetPulse — Bloqueo en firewall.

Traduce una IP atacante a la regla del firewall local (pf en macOS,
nftables/iptables en Linux, netsh en Windows) y, si está habilitado,
la aplica. Por defecto funciona en modo dry-run: registra la IP en la
blocklist y devuelve los comandos a ejecutar (sin requerir privilegios).
"""

import logging
import platform
import shutil
import subprocess

from app.core.settings import FIREWALL_ENABLED
from app.services.deception import blocklist_svc

logger = logging.getLogger(__name__)


def _commands(ip: str) -> list[list[str]]:
    system = platform.system()
    if system == "Darwin":
        # pf: administrar la tabla netpulse_block (el ancla debe estar creada).
        return [["sudo", "pfctl", "-t", "netpulse_block", "-T", "add", ip]]
    if system == "Linux":
        if shutil.which("nft"):
            return [["sudo", "nft", "add", "element", "inet", "filter",
                     "netpulse_block", "{", ip, "}"]]
        return [["sudo", "iptables", "-I", "INPUT", "-s", ip, "-j", "DROP"]]
    if system == "Windows":
        return [["netsh", "advfirewall", "firewall", "add", "rule",
                 f"name=NetPulse Block {ip}", "dir=in", "action=block",
                 f"remoteip={ip}"]]
    return []


def block(ip: str, reason: str = "") -> dict:
    """Registra la IP y (opcionalmente) aplica la regla de firewall."""
    entry = blocklist_svc.add(ip, reason or "firewall")
    cmds = _commands(ip)
    applied = False
    output = ""

    if FIREWALL_ENABLED and cmds:
        for cmd in cmds:
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                output += (proc.stdout or "") + (proc.stderr or "")
                applied = applied or proc.returncode == 0
            except Exception as exc:  # noqa: BLE001
                output += f"{type(exc).__name__}: {exc}"
                logger.warning("[firewall] fallo aplicando regla: %s", exc)

    return {
        "ip": ip,
        "blocked": True,
        "enabled": FIREWALL_ENABLED,
        "applied": applied,
        "hits": entry.get("hits", 1),
        "commands": [" ".join(c) for c in cmds],
        "output": output.strip(),
    }


def unblock(ip: str) -> dict:
    """Quita la IP de la blocklist (las reglas se revierten manualmente)."""
    removed = blocklist_svc.remove(ip)
    reverse = ""
    system = platform.system()
    if system == "Darwin":
        reverse = f"sudo pfctl -t netpulse_block -T delete {ip}"
    elif system == "Linux":
        reverse = f"sudo iptables -D INPUT -s {ip} -j DROP"
    return {"ip": ip, "removed": removed, "reverse_command": reverse}


def status() -> dict:
    """Estado del firewall y de la blocklist."""
    return {
        "enabled": FIREWALL_ENABLED,
        "platform": platform.system(),
        "preview": _commands("203.0.113.9"),
        "blocked": blocklist_svc.list_blocked(),
    }
