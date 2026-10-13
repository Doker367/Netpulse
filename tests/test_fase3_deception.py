"""Tests de la Fase 3 (hyperrealismo deception)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.deception import personas, snmp_agent
from app.services.deception.listeners import ssh_listener


# ── 3.3 Respuestas de shell por vendor ────────────────────────

def test_shell_response_cisco_desconocido():
    out = personas.shell_response("switch", "cisco", "foobar")
    assert out == "% Invalid input detected at '^' marker."


def test_shell_response_mikrotik_desconocido():
    out = personas.shell_response("router", "mikrotik", "show version")
    assert "bad command name" in out


def test_shell_response_linux_desconocido():
    out = personas.shell_response("server", "linux", "foobar")
    assert out == "bash: foobar: command not found"


def test_shell_response_fortios_desconocido():
    out = personas.shell_response("firewall", "fortinet", "foobar")
    assert out == "Unknown action 0"


def test_shell_response_cisco_conocido():
    out = personas.shell_response("switch", "cisco", "show version")
    assert "Cisco IOS Software" in out


# ── 3.1 Persistencia de host key SSH ─────────────────────────

def test_host_key_persistente(tmp_path, monkeypatch):
    keyfile = tmp_path / "ssh_host_rsa_key"
    monkeypatch.setattr(ssh_listener, "DECEPTION_SSH_HOST_KEY", keyfile)
    monkeypatch.setattr(ssh_listener, "_host_key", None)
    k1 = ssh_listener._get_host_key()
    assert keyfile.exists()
    # Segunda llamada: mismo objeto/fingerprint (no se regenera).
    monkeypatch.setattr(ssh_listener, "_host_key", None)
    k2 = ssh_listener._get_host_key()
    assert k1.get_fingerprint() == k2.get_fingerprint()


# ── 3.2 Autenticación realista ────────────────────────────────

class _FakeListener:
    device_id = "sw-dev-01"
    role = "switch"
    ATTEMPTS_BEFORE_ACCEPT = 2


def _server(monkeypatch):
    import paramiko
    monkeypatch.setattr(ssh_listener.telemetry_svc, "emit_sync", lambda *a, **k: None)
    return paramiko, ssh_listener._SSHServer(_FakeListener(), "203.0.113.9", 1234)


def test_auth_credenciales_debiles_primer_intento(monkeypatch):
    paramiko, server = _server(monkeypatch)
    # ("admin","admin") está en las credenciales del rol switch.
    assert server.check_auth_password("admin", "admin") == paramiko.AUTH_SUCCESSFUL


def test_auth_rechaza_y_luego_acepta(monkeypatch):
    paramiko, server = _server(monkeypatch)
    assert server.check_auth_password("admin", "nope1") == paramiko.AUTH_FAILED
    assert server.check_auth_password("admin", "nope2") == paramiko.AUTH_FAILED
    assert server.check_auth_password("admin", "nope3") == paramiko.AUTH_SUCCESSFUL


# ── 3.4 SNMP sysDescr ─────────────────────────────────────────

def _build_get(oid, community=b"public", rid=4242):
    varbind = snmp_agent._tlv(0x30, snmp_agent._tlv(0x06, snmp_agent._enc_oid(oid)) + snmp_agent._tlv(0x05, b""))
    vbl = snmp_agent._tlv(0x30, varbind)  # varbindlist = SEQUENCE OF VarBind
    body = (snmp_agent._tlv(0x02, snmp_agent._enc_uint(rid))
            + snmp_agent._tlv(0x02, b"\x00")
            + snmp_agent._tlv(0x02, b"\x00")
            + vbl)
    pdu = snmp_agent._tlv(0xA0, body)
    return snmp_agent._tlv(0x30, snmp_agent._tlv(0x02, snmp_agent._enc_uint(1))
                           + snmp_agent._tlv(0x04, community) + pdu)


def _parse_response(resp):
    _, msg, _ = snmp_agent._parse_tlv(resp, 0)
    i = 0
    _, _, i = snmp_agent._parse_tlv(msg, i)          # version
    _, comm, i = snmp_agent._parse_tlv(msg, i)       # community
    pdu_tag, pdu, _ = snmp_agent._parse_tlv(msg, i)
    j = 0
    _, rid_b, j = snmp_agent._parse_tlv(pdu, j)
    _, _, j = snmp_agent._parse_tlv(pdu, j)
    _, _, j = snmp_agent._parse_tlv(pdu, j)
    _, vbl, j = snmp_agent._parse_tlv(pdu, j)
    _, vb, _ = snmp_agent._parse_tlv(vbl, 0)
    m = 0
    _, oid_b, m = snmp_agent._parse_tlv(vb, m)
    vtag, val, _ = snmp_agent._parse_tlv(vb, m)
    return pdu_tag, comm, snmp_agent._dec_oid(oid_b), vtag, val


def test_snmp_get_sysdescr():
    identity = personas.snmp_identity("switch", "cisco", "sw-dev-01")
    resp = snmp_agent.handle_datagram(_build_get((1, 3, 6, 1, 2, 1, 1, 1, 0)), identity)
    assert resp is not None
    pdu_tag, community, oid, vtag, val = _parse_response(resp)
    assert pdu_tag == 0xA2
    assert community == b"public"
    assert oid == (1, 3, 6, 1, 2, 1, 1, 1, 0)
    assert vtag == 0x04
    assert val.decode() == identity["sysdescr"]
    assert "Cisco IOS" in val.decode()


def test_snmp_getnext_sysname():
    identity = personas.snmp_identity("switch", "cisco", "sw-dev-01")
    # GETNEXT de sysContact (…1.4.0) → devuelve sysName (…1.5.0)
    varbind = snmp_agent._tlv(0x30, snmp_agent._tlv(0x06, snmp_agent._enc_oid((1, 3, 6, 1, 2, 1, 1, 4, 0))) + snmp_agent._tlv(0x05, b""))
    vbl = snmp_agent._tlv(0x30, varbind)
    body = (snmp_agent._tlv(0x02, snmp_agent._enc_uint(1)) + snmp_agent._tlv(0x02, b"\x00")
            + snmp_agent._tlv(0x02, b"\x00") + vbl)
    msg = snmp_agent._tlv(0x30, snmp_agent._tlv(0x02, snmp_agent._enc_uint(1))
                          + snmp_agent._tlv(0x04, b"public") + snmp_agent._tlv(0xA1, body))
    resp = snmp_agent.handle_datagram(msg, identity)
    _, _, oid, _, val = _parse_response(resp)
    assert oid == (1, 3, 6, 1, 2, 1, 1, 5, 0)
    assert val.decode() == "sw-dev-01"


def test_oid_roundtrip():
    oid = (1, 3, 6, 1, 4, 1, 9, 1, 716)
    assert snmp_agent._dec_oid(snmp_agent._enc_oid(oid)) == oid
