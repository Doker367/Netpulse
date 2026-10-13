"""NetPulse — Paneles web falsos por fabricante.

Genera HTML realista para los servicios HTTP/HTTPS de los dispositivos
señuelo: páginas de login con branding del vendor, un panel de
administración falso tras "autenticar" y cabeceras ``Server`` acordes.
El objetivo es que, al navegar, el atacante vea un dispositivo creíble.
"""

from typing import Optional

# Cabecera Server por rol/vendor
_SERVER_HEADER = {
    "camera": "webs",
    "switch": "Cisco-IOS/15.2(4)E",
    "firewall": "FortiGate",
    "router": "RouterOS 7.11 httpd",
    "server": "Apache/2.4.41 (Ubuntu)",
    "pc": "Microsoft-IIS/10.0",
}

_BRAND = {
    "camera": ("Hikvision", "#0b5cab"),
    "switch": ("Cisco", "#1b6ca8"),
    "firewall": ("Fortinet", "#d32f2f"),
    "router": ("MikroTik", "#293b5f"),
    "server": ("Apache2 Ubuntu", "#772953"),
    "pc": ("Microsoft IIS", "#0078d4"),
}


def server_header(role: str) -> str:
    """Cabecera HTTP Server realista para el rol."""
    return _SERVER_HEADER.get(role, "nginx/1.18.0 (Ubuntu)")


def _brand(role: str) -> tuple[str, str]:
    return _BRAND.get(role, ("Web Admin", "#334155"))


def login_page(role: str, vendor: Optional[str] = None) -> str:
    """Página de login con branding del fabricante."""
    name, color = _brand(role)
    if vendor:
        name = vendor.split("/")[0].split()[0] or name
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name} - Login</title>
<style>
 body{{margin:0;font-family:'Segoe UI',Arial,sans-serif;background:#eef1f5;display:flex;
      align-items:center;justify-content:center;height:100vh}}
 .card{{background:#fff;border-radius:8px;box-shadow:0 6px 24px rgba(0,0,0,.15);
       width:360px;overflow:hidden}}
 .hdr{{background:{color};color:#fff;padding:18px 22px;font-size:18px;font-weight:600}}
 .body{{padding:22px}}
 label{{display:block;font-size:12px;color:#555;margin:10px 0 4px}}
 input{{width:100%;box-sizing:border-box;padding:9px;border:1px solid #ccc;border-radius:4px;font-size:13px}}
 button{{margin-top:16px;width:100%;background:{color};color:#fff;border:none;
        padding:11px;border-radius:4px;font-size:14px;cursor:pointer}}
 .foot{{padding:12px 22px;font-size:11px;color:#888;border-top:1px solid #eee}}
 .warn{{color:#b45309;font-size:11px;margin-top:10px}}
</style></head>
<body><div class="card">
 <div class="hdr">{name} Device Management</div>
 <form class="body" method="post" action="/login">
   <label>Username</label><input name="username" autocomplete="off">
   <label>Password</label><input name="password" type="password" autocomplete="off">
   <button type="submit">Log In</button>
   <div class="warn">Unauthorized access prohibited</div>
 </form>
 <div class="foot">&copy; {name}. Firmware 1.0.0 &middot; <a href="/doc/index.html">Help</a></div>
</div></body></html>"""


def dashboard_page(role: str, user: str = "admin", vendor: Optional[str] = None) -> str:
    """Panel de administración falso tras un login 'exitoso'."""
    name, color = _brand(role)
    if vendor:
        name = vendor.split("/")[0].split()[0] or name

    widgets = {
        "camera": ("Live View", "4 cameras online", "Storage 78% used"),
        "switch": ("Interface Status", "24 ports up / 0 down", "CPU 12%"),
        "firewall": ("System Status", "Threats blocked: 1,204", "Sessions: 3,512"),
        "router": ("Interfaces", "WAN: up · LAN: up", "Uptime 41d 3h"),
        "server": ("Server Status", "Load 0.42 · 8 vCPU", "Services: 12 running"),
        "pc": ("Welcome", "Windows 10 Pro", "Last login: today"),
    }.get(role, ("Dashboard", "OK", "OK"))
    rows = "".join(
        f'<div class="tile"><div class="t">{t}</div><div class="v">{v}</div>'
        f'<div class="s">{s}</div></div>'
        for t, v, s in [widgets]
    )
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{name} Administration</title>
<style>
 body{{margin:0;font-family:'Segoe UI',Arial,sans-serif;background:#f4f6f8}}
 .top{{background:{color};color:#fff;padding:14px 22px;display:flex;justify-content:space-between}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px;padding:22px}}
 .tile{{background:#fff;border-radius:8px;padding:18px;box-shadow:0 2px 10px rgba(0,0,0,.08)}}
 .t{{font-size:12px;color:#777;text-transform:uppercase}}
 .v{{font-size:22px;font-weight:700;margin:6px 0}}
 .s{{font-size:12px;color:#999}}
</style></head>
<body>
 <div class="top"><b>{name} Administration</b><span>Signed in as {user} · Logout</span></div>
 <div class="grid">{rows}
   <div class="tile"><div class="t">Users</div><div class="v">admin</div><div class="s">Last login 0 days ago</div></div>
   <div class="tile"><div class="t">Logs</div><div class="v">View</div><div class="s">System event log</div></div>
 </div>
</body></html>"""


def not_found_page() -> str:
    """Página 404 genérica."""
    return ("<!DOCTYPE html><html><head><title>404 Not Found</title></head>"
            "<body><h1>404 Not Found</h1><hr><address>Apache/2.4.41 (Ubuntu) Server</address>"
            "</body></html>")
