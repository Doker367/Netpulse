"""Parser de eventos de OpenCanary (honeypot multi-protocolo ligero).

OpenCanary emite líneas JSON con ``dst_port``, ``src_host``, ``msg`` y
``logtype``. Se mapea por puerto de destino para deducir el protocolo y
el tipo de evento.
"""

from typing import Optional

# Puerto destino → protocolo
_PORT_PROTO = {
    21: "ftp", 22: "ssh", 23: "telnet", 25: "smtp", 80: "http",
    110: "pop3", 143: "imap", 161: "snmp", 443: "https", 445: "smb",
    1433: "mssql", 3306: "mysql", 3389: "rdp", 5432: "postgresql",
    5900: "vnc", 6379: "redis", 8080: "http",
}


def parse(raw: dict, default_device: str = "") -> Optional[dict]:
    """Traduce un evento OpenCanary a kwargs de ``telemetry_svc.emit``.

    Returns:
        Dict de kwargs, o None si la línea no es un evento válido.
    """
    src_ip = raw.get("src_host") or raw.get("src_ip")
    if not src_ip:
        return None

    try:
        dst_port = int(raw.get("dst_port", 0) or 0)
    except (TypeError, ValueError):
        dst_port = 0
    proto = _PORT_PROTO.get(dst_port, "tcp")
    if dst_port == 161:
        proto = "snmp"

    msg = str(raw.get("msg", ""))
    low = msg.lower()
    if "login" in low or "password" in low or "credential" in low:
        etype = "login_attempt"
    elif dst_port in (161,):
        etype = "snmp_query"
    else:
        etype = "connect"

    return {
        "device_id": default_device or raw.get("node_id") or "opencanary",
        "src_ip": src_ip,
        "src_port": raw.get("src_port"),
        "proto": proto,
        "event_type": etype,
        "detail": {"sensor": "opencanary", "logtype": raw.get("logtype"),
                   "dst_port": dst_port, "msg": msg},
        "username": raw.get("username"),
        "success": False,
        "ts": raw.get("utc_time") or raw.get("local_time"),
    }
