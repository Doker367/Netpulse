"""NetPulse — IOCs (Indicators of Compromise) de la red señuelo.

Agrega los atacantes observados en un resumen exportable. Para reducir
ruido: excluye eventos simulados, clasifica el origen (público / interno
/ laboratorio) y puntúa la amenaza por peso de evidencias en vez de
marcar "high" ante cualquier interacción.
"""

import csv
import io
import ipaddress
from typing import Optional

from app.services.deception import allowlist_svc, blocklist_svc, store_svc

_SENSITIVE = ("passwd", "shadow", "id_rsa", "wget", "curl", "chmod", "rm -rf", "nc ", "nc -")


def _classify(ip: str) -> str:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "unknown"
    if addr.is_loopback:
        return "lab"
    if addr.is_private or addr.is_link_local:
        return "internal"
    return "public"


def _threat(score: int, kind: str, success: bool, suspicious: bool) -> str:
    if kind == "lab":
        return "info"
    if score >= 20 or (success and suspicious):
        return "high"
    if score >= 8:
        return "medium"
    return "low"


def summarize(limit: int = 5000) -> list[dict]:
    """Resumen de atacantes (IOCs) ordenado por severidad y actividad."""
    events = store_svc.get_events(limit=limit)
    aggr: dict[str, dict] = {}

    for e in events:
        ip = e.get("src_ip")
        if not ip:
            continue
        detail = e.get("detail") or {}
        if detail.get("simulated"):
            continue  # actividad de demo: no es un atacante real
        a = aggr.setdefault(ip, {
            "ip": ip, "events": 0, "devices": set(), "protos": set(),
            "commands": [], "success": False,
            "first_seen": e.get("timestamp"), "last_seen": e.get("timestamp"),
        })
        a["events"] += 1
        if e.get("device_id"):
            a["devices"].add(e["device_id"])
        if e.get("proto"):
            a["protos"].add(e["proto"])
        if e.get("success"):
            a["success"] = True
        if detail.get("command"):
            a["commands"].append(detail["command"])
        ts = e.get("timestamp")
        if ts:
            if not a["first_seen"] or ts < a["first_seen"]:
                a["first_seen"] = ts
            if not a["last_seen"] or ts > a["last_seen"]:
                a["last_seen"] = ts

    blocked = {b["ip"] for b in blocklist_svc.list_blocked()}
    result = []
    for ip, a in aggr.items():
        commands = a["commands"]
        suspicious = [c for c in commands if any(s in c.lower() for s in _SENSITIVE)]
        score = a["events"] + (5 if a["success"] else 0) + len(suspicious) * 10
        kind = _classify(ip)
        result.append({
            "ip": ip,
            "kind": kind,                       # public | internal | lab
            "score": score,
            "events": a["events"],
            "devices": sorted(a["devices"]),
            "protos": sorted(a["protos"]),
            "commands": len(commands),
            "suspicious_commands": suspicious[:5],
            "success": a["success"],
            "first_seen": a["first_seen"],
            "last_seen": a["last_seen"],
            "threat": _threat(score, kind, a["success"], bool(suspicious)),
            "blocked": ip in blocked,
            "allowlisted": allowlist_svc.is_allowed(ip),
        })

    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    result.sort(key=lambda r: (order.get(r["threat"], 9), -r["score"]))
    return result


def to_csv(limit: int = 5000) -> str:
    """Exporta los IOCs a CSV."""
    rows = summarize(limit=limit)
    buffer = io.StringIO()
    headers = ["ip", "kind", "threat", "score", "events", "blocked", "allowlisted",
               "success", "devices", "protos", "commands", "first_seen", "last_seen"]
    writer = csv.DictWriter(buffer, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    for r in rows:
        writer.writerow({
            "ip": r["ip"], "kind": r["kind"], "threat": r["threat"], "score": r["score"],
            "events": r["events"], "blocked": r["blocked"], "allowlisted": r["allowlisted"],
            "success": r["success"], "devices": "|".join(r["devices"]),
            "protos": "|".join(r["protos"]), "commands": r["commands"],
            "first_seen": r["first_seen"], "last_seen": r["last_seen"],
        })
    return buffer.getvalue()


def find(ip: str) -> Optional[dict]:
    """Devuelve el IOC de una IP concreta."""
    for row in summarize():
        if row["ip"] == ip:
            return row
    return None
