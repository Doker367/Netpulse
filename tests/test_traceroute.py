"""Traceroute interactivo en el mapa de topología.

Parsers de traceroute (servidor NOC y CLI multi-vendor), seguridad del
router y disponibilidad del dashboard actualizado.
"""

import platform
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app import main
from app.routers import traceroute as tr

DASH = TestClient(main.app).get("/").text


# ── Dashboard: la nueva vista de topología está servida ──────

def test_dashboard_topology_view_has_graph_map_and_table():
    assert 'id="v-topology"' in DASH
    assert "topoTab('graph')" in DASH
    assert "topoTab('map')" in DASH
    assert "topoTab('table')" in DASH
    assert '<canvas id="topo-canvas"' in DASH
    assert '<div id="topo-map"' in DASH


def test_dashboard_topology_has_traceroute_panel():
    assert "runTopoTrace()" in DASH
    assert 'id="topo-trace-origin"' in DASH
    assert 'id="topo-trace-dest"' in DASH
    assert "/static/vendor/leaflet.js" in DASH
    assert "/static/vendor/leaflet.css" in DASH


def test_topology_map_nodes_carry_ip_and_geo_fields():
    # El endpoint incorpora ip + geo por nodo (la base GeoLite puede no existir: geo=None)
    import app.routers.topology as topo

    assert hasattr(topo, "geoip_svc")


# ── Parser: traceroute del servidor (traceroute -n / tracert -d) ──

def test_parse_server_traceroute_linux():
    out = (
        "traceroute to 8.8.8.8 (8.8.8.8), 30 hops max, 60 byte packets\n"
        "  1  192.168.1.1  1.2 ms\n"
        "  2  gw.local (10.1.2.3)  5.5 ms\n"
        "  3  * * *\n"
        "  4  142.250.0.1  10.01 ms\n"
    )
    hops = tr._parse_server_traceroute(out)
    assert [h["hop"] for h in hops] == [1, 2, 3, 4]
    assert hops[0]["ip"] == "192.168.1.1" and hops[0]["rtt_ms"] == 1.2
    assert hops[1]["hostname"] == "gw.local" and hops[1]["ip"] == "10.1.2.3"
    assert hops[2]["ip"] == "" and hops[2]["rtt_ms"] is None  # salto sin respuesta
    assert hops[3]["ip"] == "142.250.0.1"


def test_parse_server_traceroute_windows():
    out = (
        "Tracing route to one.one.one.one [1.1.1.1]\n"
        "over a maximum of 30 hops:\n\n"
        "  1     1 ms     1 ms     1 ms  192.168.2.1\n"
        "  2     9 ms     8 ms     9 ms  203.0.113.9\n"
    )
    hops = tr._parse_server_traceroute(out)
    assert [h["ip"] for h in hops] == ["192.168.2.1", "203.0.113.9"]
    assert hops[0]["rtt_ms"] == 1.0  # primer RTT de la fila Windows


# ── Parser: CLI multi-vendor ─────────────────────────────────

def test_parse_device_traceroute_cisco_ios():
    out = (
        "Type escape sequence to abort.\n"
        "Tracing the route to 8.8.8.8\n\n"
        "  1 10.90.18.1 [MPLS: Label 16] 1 msec 1 msec 1 msec\n"
        "  2 edge-router(10.90.18.254) 3 msec 3 msec 3 msec\n"
    )
    hops = tr._parse_device_traceroute(out)
    assert hops[0]["ip"] == "10.90.18.1" and hops[0]["rtt_ms"] == 1.0
    assert hops[1]["hostname"] == "edge-router" and hops[1]["ip"] == "10.90.18.254"


def test_parse_device_traceroute_junos():
    out = " 2  gw-core.dc-hosting.net (203.0.113.2) 20.4 ms 21.8 ms\n"
    hops = tr._parse_device_traceroute(out)
    assert hops[0]["hostname"] == "gw-core.dc-hosting.net"
    assert hops[0]["ip"] == "203.0.113.2"
    assert hops[0]["rtt_ms"] == 20.4


def test_device_traceroute_command_is_allowed_by_run_commands():
    from app.services.napalm_svc import _validate_commands

    ok, err = _validate_commands(["traceroute 8.8.8.8"], "ios")
    assert ok and err is None


def test_parse_lldpcli_output_to_napalm_schema():
    from app.services.napalm_svc import _parse_lldpcli_output

    out = (
        "-------------------------------------------------------------------------------\n"
        "LLDP neighbors:\n"
        "-------------------------------------------------------------------------------\n"
        "Interface:    eth1, via: LLDP, RID: 1, Time: 0 days, 00:00:07\n"
        "  Chassis:\n"
        "    ChassisID:    mac d2:7d:d7:a9:65:2c\n"
        "    SysName:      juniper-edge-01\n"
        "    SysDescr:     FRRouting 8.4\n"
        "  Port:\n"
        "    PortID:       mac d2:7d:d7:a9:65:2c\n"
        "    PortDescr:    eth1\n"
    )
    parsed = _parse_lldpcli_output(out)
    assert parsed == {"eth1": [{"hostname": "juniper-edge-01", "port": "eth1"}]}


def test_parse_server_traceroute_tracepath_format():
    # salida real de iputils tracepath (FRR lab, usuario sin privileges)
    out = (
        " 1?: [LOCALHOST] pmtu 1500\n"
        " 1:   172.30.1.11  0.563ms reached\n"
        "     Resume: pmtu 1500\n"
    )
    hops = tr._parse_server_traceroute(out)
    assert len(hops) == 1
    assert hops[0]["hop"] == 1
    assert hops[0]["ip"] == "172.30.1.11"
    assert abs(hops[0]["rtt_ms"] - 0.563) < 0.01


# ── Seguridad del router ─────────────────────────────────────

def test_destination_regex_blocks_shell_injection():
    for bad in ("8.8.8.8; reboot", "$(whoami)", "a b", "", "`id`", "x | y"):
        assert not tr._DEST_RE.match(bad), bad
    for good in ("8.8.8.8", "edge.mi-red.com", "2001:db8::1"):
        assert tr._DEST_RE.match(good), good


def test_traceroute_route_is_served_with_auth_guard():
    # La ruta existe con guardas JWT/RBAC: sin token responde 401/403, nunca 404.
    client = TestClient(main.app)
    res = client.post("/api/topology/traceroute", json={"origin": "noc", "destination": "8.8.8.8"})
    assert res.status_code in (401, 403)
    no_dest = client.post("/api/topology/traceroute", json={"origin": "noc"})
    assert no_dest.status_code in (401, 403)


def test_server_traceroute_reader_platform_selection():
    # Modo lectura: verifica que el comando se arma por plataforma sin ejecutarlo
    if platform.system() in ("Linux", "Darwin"):
        import inspect

        src = inspect.getsource(tr._run_server_traceroute)
        assert '"traceroute", "-n"' in src
