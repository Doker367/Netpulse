"""NetPulse — Agente SNMP v2c mínimo para señuelos.

Responde a GET/GETNEXT sobre un MIB reducido (system group) con valores
coherentes con la persona del dispositivo (sysDescr/sysName/sysObjectID).
Así ``snmpwalk``/``snmpget`` ven un equipo real en vez de un puerto filtrado.

Solo implementa codificación BER a mano (sin dependencias) para el subconjunto
necesario. No soporta SET ni SNMPv3.
"""

import time
from typing import Optional

_ASN1_INTEGER = 0x02
_ASN1_OCTET = 0x04
_ASN1_NULL = 0x05
_ASN1_OID = 0x06
_ASN1_SEQUENCE = 0x30
_TIMETICKS = 0x43
_PDU_GET = 0xA0
_PDU_GETNEXT = 0xA1
_PDU_RESPONSE = 0xA2
_EXC_NO_SUCH_OBJECT = 0x80
_EXC_END_OF_MIB = 0x82

_BOOT = time.monotonic()


# ── BER (codificación) ───────────────────────────────────────


def _enc_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    out = b""
    while n > 0:
        out = bytes([n & 0xFF]) + out
        n >>= 8
    return bytes([0x80 | len(out)]) + out


def _tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + _enc_len(len(value)) + value


def _enc_uint(n: int) -> bytes:
    if n == 0:
        return b"\x00"
    n = int(n)
    out = b""
    while n > 0:
        out = bytes([n & 0xFF]) + out
        n >>= 8
    if out[0] & 0x80:
        out = b"\x00" + out
    return out


def _enc_oid(oid) -> bytes:
    if isinstance(oid, str):
        parts = [int(x) for x in oid.split(".") if x != ""]
    else:
        parts = list(oid)
    if len(parts) < 2:
        return b""
    out = [40 * parts[0] + parts[1]]
    for p in parts[2:]:
        sub = [p & 0x7F]
        p >>= 7
        while p > 0:
            sub.append((p & 0x7F) | 0x80)
            p >>= 7
        out.extend(reversed(sub))
    return bytes(out)


# ── BER (decodificación) ─────────────────────────────────────


def _parse_len(data: bytes, i: int):
    first = data[i]
    i += 1
    if first < 0x80:
        return first, i
    n = first & 0x7F
    return int.from_bytes(data[i:i + n], "big"), i + n


def _parse_tlv(data: bytes, i: int):
    tag = data[i]
    i += 1
    length, i = _parse_len(data, i)
    return tag, data[i:i + length], i + length


def _dec_int(b: bytes) -> int:
    return int.from_bytes(b, "big", signed=True) if b else 0


def _dec_oid(b: bytes) -> tuple:
    if not b:
        return ()
    parts = [b[0] // 40, b[0] % 40]
    val = 0
    for byte in b[1:]:
        val = (val << 7) | (byte & 0x7F)
        if not byte & 0x80:
            parts.append(val)
            val = 0
    return tuple(parts)


# ── MIB reducido ─────────────────────────────────────────────


def _build_table(identity: dict, uptime_ticks: int) -> list:
    return [
        ((1, 3, 6, 1, 2, 1, 1, 1, 0), (_ASN1_OCTET, identity.get("sysdescr", "").encode("utf-8", "replace"))),
        ((1, 3, 6, 1, 2, 1, 1, 2, 0), (_ASN1_OID, _enc_oid(identity.get("sysobjectid", "1.3.6.1.4.1.0")))),
        ((1, 3, 6, 1, 2, 1, 1, 3, 0), (_TIMETICKS, _enc_uint(uptime_ticks))),
        ((1, 3, 6, 1, 2, 1, 1, 4, 0), (_ASN1_OCTET, b"")),
        ((1, 3, 6, 1, 2, 1, 1, 5, 0), (_ASN1_OCTET, identity.get("sysname", "decoy").encode("utf-8", "replace"))),
        ((1, 3, 6, 1, 2, 1, 1, 6, 0), (_ASN1_OCTET, b"")),
        ((1, 3, 6, 1, 2, 1, 2, 1, 0), (_ASN1_INTEGER, _enc_uint(3))),
    ]


def _lookup(table: list, oid: tuple):
    for entry_oid, value in table:
        if entry_oid == oid:
            return value
    return ("exc", _EXC_NO_SUCH_OBJECT)


def _next_oid(table: list, oid: tuple):
    for entry_oid, value in table:
        if entry_oid > oid:
            return entry_oid, value
    return None


def _varbind(oid: tuple, value) -> bytes:
    oid_enc = _tlv(_ASN1_OID, _enc_oid(oid))
    if value[0] == "exc":
        val_enc = _tlv(value[1], b"")
    else:
        tag, val = value
        val_enc = _tlv(tag, val)
    return _tlv(_ASN1_SEQUENCE, oid_enc + val_enc)


# ── API ──────────────────────────────────────────────────────


def uptime_ticks() -> int:
    """Ticks (1/100 s) desde que arrancó el proceso señuelo."""
    return int((time.monotonic() - _BOOT) * 100)


def handle_datagram(data: bytes, identity: dict) -> Optional[bytes]:
    """Procesa un datagrama SNMP v2c y devuelve la respuesta (o None)."""
    try:
        tag, msg, _ = _parse_tlv(data, 0)
        if tag != _ASN1_SEQUENCE:
            return None
        i = 0
        _, ver_b, i = _parse_tlv(msg, i)
        version = _dec_int(ver_b)
        _, community, i = _parse_tlv(msg, i)
        pdu_tag, pdu, _ = _parse_tlv(msg, i)
        if pdu_tag not in (_PDU_GET, _PDU_GETNEXT):
            return None

        j = 0
        _, rid_b, j = _parse_tlv(pdu, j)
        request_id = _dec_int(rid_b)
        _, _, j = _parse_tlv(pdu, j)  # error-status
        _, _, j = _parse_tlv(pdu, j)  # error-index
        _, vbl, j = _parse_tlv(pdu, j)

        requested = []
        k = 0
        while k < len(vbl):
            _, vb, k = _parse_tlv(vbl, k)
            m = 0
            _, oid_b, m = _parse_tlv(vb, m)
            requested.append(_dec_oid(oid_b))

        table = _build_table(identity, uptime_ticks())
        resp_vbs = b""
        for oid in requested:
            if pdu_tag == _PDU_GET:
                resp_vbs += _varbind(oid, _lookup(table, oid))
            else:
                nxt = _next_oid(table, oid)
                if nxt is None:
                    resp_vbs += _varbind(oid, ("exc", _EXC_END_OF_MIB))
                else:
                    resp_vbs += _varbind(nxt[0], nxt[1])

        body = (
            _tlv(_ASN1_INTEGER, _enc_uint(request_id))
            + _tlv(_ASN1_INTEGER, b"\x00")
            + _tlv(_ASN1_INTEGER, b"\x00")
            + _tlv(_ASN1_SEQUENCE, resp_vbs)
        )
        pdu_out = _tlv(_PDU_RESPONSE, body)
        return _tlv(
            _ASN1_SEQUENCE,
            _tlv(_ASN1_INTEGER, _enc_uint(version)) + _tlv(_ASN1_OCTET, community) + pdu_out,
        )
    except Exception:  # noqa: BLE001
        return None
