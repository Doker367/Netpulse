"""Tests de auto-discovery (sin red: NAPALM e inventario mockeados)."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.security import create_access_token
from app.main import app
from app.services import discovery_svc, inventory_svc, napalm_svc


def _headers(role: str) -> dict[str, str]:
    token = create_access_token({"sub": role, "role": role})
    return {"Authorization": f"Bearer {token}"}


def _scoped_headers(role: str = "operator", **claims) -> dict[str, str]:
    token = create_access_token({"sub": "scoped", "role": role, **claims})
    return {"Authorization": f"Bearer {token}"}


VIEWER = _headers("viewer")
OPERATOR = _headers("operator")


def _device(device_id: str, hostname: str, driver: str = "ios", protocol: str = "ssh") -> dict:
    return {
        "id": device_id,
        "hostname": hostname,
        "port": 22,
        "driver": driver,
        "protocol": protocol,
        "type": "switch",
        "group": "Core",
        "tags": [],
        "description": "",
    }


CORE = _device("core-01", "10.0.0.1")
EDGE = _device("edge-01", "10.0.0.11")
SNMP = _device("snmp-01", "10.0.0.21", driver="snmp", protocol="snmp")
EXISTING = _device("existing-01", "10.0.0.50")


@pytest.fixture(autouse=True)
def _clear_discovery_cache():
    discovery_svc.clear_cache()
    yield
    discovery_svc.clear_cache()


@pytest.fixture()
def client():
    return TestClient(app)


def _patch_inventory(monkeypatch, devices, add_device=None):
    """Sustituye el inventario en memoria; nunca toca config/devices.yaml."""
    monkeypatch.setattr(inventory_svc, "list_devices", lambda: [dict(d) for d in devices])
    monkeypatch.setattr(
        inventory_svc,
        "get_raw_devices",
        lambda: [
            {
                **d,
                "credentials": dict(
                    d.get("credentials") or {"username": "admin", "password": "secret"}
                ),
            }
            for d in devices
        ],
    )
    if add_device is not None:
        monkeypatch.setattr(inventory_svc, "add_device", add_device)


def _lldp_ok(device_id: str, neighbor: str = "juniper-edge-01") -> napalm_svc.NapalmResult:
    result = napalm_svc.NapalmResult(device_id)
    result.success = True
    result.data = {"Gi0/1": [{"hostname": neighbor, "port": "Gi0/2"}]}
    return result


def _patch_resolution(monkeypatch, mapping: dict[str, str]):
    monkeypatch.setattr(
        discovery_svc,
        "_resolve_management_ip",
        lambda name, _by_name: mapping.get(name),
    )


def _scan(client, source_ids=None, metodo="lldp"):
    body = {"metodo": metodo}
    if source_ids is not None:
        body["source_ids"] = source_ids
    return client.post("/api/discovery/scan", json=body, headers=VIEWER)


# ── Escaneo feliz ────────────────────────────────────────────


def test_scan_feliz(client, monkeypatch):
    _patch_inventory(monkeypatch, [CORE])
    _patch_resolution(monkeypatch, {"juniper-edge-01": "10.0.0.50"})
    monkeypatch.setattr(napalm_svc, "get_lldp_neighbors", _lldp_ok)

    response = _scan(client, ["core-01"])
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["metodo"] == "lldp"
    assert body["timestamp"]
    assert body["origenes"][0]["estado"] == "ok"
    assert body["origenes"][0]["vecinos"] == 1
    assert body["resumen"]["candidatos"] == 1

    candidate = body["candidatos"][0]
    assert candidate["ip"] == "10.0.0.50"
    assert candidate["origen"] == "core-01"
    assert candidate["nombre_sugerido"] == "juniper-edge-01"
    assert candidate["plataforma_sugerida"] == "junos"
    assert candidate["estado"] == "propuesto"
    assert candidate["interfaz_local"] == "Gi0/1"
    assert candidate["interfaz_remota"] == "Gi0/2"

    # GET /candidates devuelve el mismo último escaneo, con timestamp.
    cached = client.get("/api/discovery/candidates", headers=VIEWER)
    assert cached.status_code == 200
    assert cached.json()["timestamp"] == body["timestamp"]
    assert cached.json()["candidatos"][0]["ip"] == "10.0.0.50"


def test_candidates_sin_escaneo(client):
    response = client.get("/api/discovery/candidates", headers=VIEWER)
    assert response.status_code == 200
    body = response.json()
    assert body["timestamp"] is None
    assert body["candidatos"] == []
    assert "Sin escaneo" in body["mensaje"]


# ── Vecino duplicado ─────────────────────────────────────────


def test_vecino_duplicado_dedup_por_ip(client, monkeypatch):
    _patch_inventory(monkeypatch, [CORE, EDGE])
    _patch_resolution(monkeypatch, {"juniper-edge-01": "10.0.0.50"})

    def fake_lldp(device_id):
        return _lldp_ok(device_id, "juniper-edge-01")

    monkeypatch.setattr(napalm_svc, "get_lldp_neighbors", fake_lldp)

    response = _scan(client, ["core-01", "edge-01"])
    assert response.status_code == 200, response.text
    body = response.json()

    assert len(body["candidatos"]) == 1
    candidate = body["candidatos"][0]
    assert candidate["ip"] == "10.0.0.50"
    assert candidate["origen"] == "core-01"
    assert candidate["origenes"] == ["core-01", "edge-01"]
    assert candidate["estado"] == "propuesto"
    assert body["resumen"]["vecinos"] == 2
    assert body["resumen"]["candidatos"] == 1


def test_vecino_sin_ip_resoluble_se_omite(client, monkeypatch):
    _patch_inventory(monkeypatch, [CORE])
    _patch_resolution(monkeypatch, {})  # no resuelve
    monkeypatch.setattr(napalm_svc, "get_lldp_neighbors", _lldp_ok)

    response = _scan(client, ["core-01"])
    assert response.status_code == 200
    body = response.json()
    assert body["candidatos"] == []
    omitido = body["origenes"][0]["omitidos"][0]
    assert omitido["nombre"] == "juniper-edge-01"
    assert "Sin IP de gestión" in omitido["motivo"]


# ── Dispositivo sin soporte ──────────────────────────────────


def test_dispositivo_sin_soporte_no_es_500(client, monkeypatch):
    _patch_inventory(monkeypatch, [SNMP])

    def _must_not_call(_device_id):
        raise AssertionError("no se debe consultar LLDP en un equipo solo SNMP")

    monkeypatch.setattr(napalm_svc, "get_lldp_neighbors", _must_not_call)

    response = _scan(client, ["snmp-01"])
    assert response.status_code == 200, response.text
    body = response.json()
    origin = body["origenes"][0]
    assert origin["estado"] == "sin_soporte"
    assert "SNMP" in origin["motivo"]
    assert body["candidatos"] == []
    assert body["mensaje"]


def test_driver_sin_cdp_no_se_consulta(client, monkeypatch):
    device = _device("junos-01", "10.0.0.31", driver="junos")
    _patch_inventory(monkeypatch, [device])

    def _must_not_call(_device_id, _commands):
        raise AssertionError("CDP no aplica a junos")

    monkeypatch.setattr(napalm_svc, "run_commands", _must_not_call)

    response = _scan(client, ["junos-01"], metodo="cdp")
    assert response.status_code == 200
    assert response.json()["origenes"][0]["estado"] == "sin_soporte"


def test_error_de_conexion_por_dispositivo(client, monkeypatch):
    _patch_inventory(monkeypatch, [CORE])

    def fake_lldp(device_id):
        result = napalm_svc.NapalmResult(device_id)
        result.set_error("timeout", "Timed out")
        return result

    monkeypatch.setattr(napalm_svc, "get_lldp_neighbors", fake_lldp)

    response = _scan(client, ["core-01"])
    assert response.status_code == 200
    origin = response.json()["origenes"][0]
    assert origin["estado"] == "error"
    assert origin["error_type"] == "timeout"
    assert origin["motivo"]


# ── CDP ──────────────────────────────────────────────────────


def test_scan_cdp_parsea_salida(client, monkeypatch):
    _patch_inventory(monkeypatch, [CORE])
    output = (
        "----------------------------------------\n"
        "Device ID:SW2\n"
        "Entry address(es):\n"
        "  IP address: 10.0.0.77\n"
        "Platform: cisco WS-C3560-24TS,  Capabilities: Switch IGMP\n"
        "Interface: GigabitEthernet0/1,  Port ID (outgoing port): GigabitEthernet0/2\n"
    )

    def fake_run(device_id, commands):
        assert commands == ["show cdp neighbors detail"]
        result = napalm_svc.NapalmResult(device_id)
        result.success = True
        result.data = [{"command": commands[0], "output": output, "error": None}]
        return result

    monkeypatch.setattr(napalm_svc, "run_commands", fake_run)

    response = _scan(client, ["core-01"], metodo="cdp")
    assert response.status_code == 200, response.text
    candidate = response.json()["candidatos"][0]
    assert candidate["ip"] == "10.0.0.77"
    assert candidate["nombre_sugerido"] == "SW2"
    assert candidate["plataforma_sugerida"] == "ios"
    assert candidate["interfaz_local"] == "GigabitEthernet0/1"
    assert candidate["interfaz_remota"] == "GigabitEthernet0/2"


# ── Importación ──────────────────────────────────────────────


def test_import_equipo_ya_existente(client, monkeypatch):
    _patch_inventory(monkeypatch, [CORE, EXISTING])
    _patch_resolution(monkeypatch, {"juniper-edge-01": "10.0.0.50"})
    monkeypatch.setattr(napalm_svc, "get_lldp_neighbors", _lldp_ok)

    assert _scan(client, ["core-01"]).status_code == 200

    def _must_not_add(_device):
        raise AssertionError("no debe registrarse un equipo ya existente")

    monkeypatch.setattr(inventory_svc, "add_device", _must_not_add)

    response = client.post(
        "/api/discovery/import",
        json={"candidates": ["10.0.0.50"]},
        headers=OPERATOR,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["creados"] == []
    assert body["ya_existian"][0]["id"] == "existing-01"
    assert "Ya existe" in body["ya_existian"][0]["motivo"]
    assert body["resumen"] == {
        "solicitados": 1,
        "creados": 0,
        "ya_existian": 1,
        "fallidos": 0,
    }


def test_import_crea_con_driver_sugerido(client, monkeypatch):
    _patch_inventory(monkeypatch, [CORE])
    _patch_resolution(monkeypatch, {"juniper-edge-01": "10.0.0.50"})
    monkeypatch.setattr(napalm_svc, "get_lldp_neighbors", _lldp_ok)

    assert _scan(client, ["core-01"]).status_code == 200

    captured: dict = {}

    def fake_add(device):
        captured.update(device)
        return {**device, "id": device["id"]}

    monkeypatch.setattr(inventory_svc, "add_device", fake_add)

    response = client.post(
        "/api/discovery/import",
        json={
            "candidates": ["10.0.0.50"],
            "username": "netops",
            "password": "secreto",
            "group": "Access",
            "tipo": "switch",
        },
        headers=OPERATOR,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["resumen"]["creados"] == 1
    assert body["creados"][0]["id"] == "juniper-edge-01"
    assert body["creados"][0]["driver"] == "junos"

    assert captured["hostname"] == "10.0.0.50"
    assert captured["driver"] == "junos"
    assert captured["username"] == "netops"
    assert captured["password"] == "secreto"
    assert captured["group"] == "Access"
    assert captured["type"] == "switch"
    assert captured["tags"] == ["discovery"]
    assert "LLDP" in captured["description"]

    # El candidato cacheado sigue en 'propuesto': nunca se marca 'importado'.
    cached = client.get("/api/discovery/candidates", headers=VIEWER).json()
    assert cached["candidatos"][0]["estado"] == "propuesto"


def test_import_plataforma_desconocida_falla_sin_adivinar(client, monkeypatch):
    _patch_inventory(monkeypatch, [CORE])

    def fake_lldp(device_id):
        result = napalm_svc.NapalmResult(device_id)
        result.success = True
        result.data = {"Gi0/1": [{"hostname": "nodo-sin-pistas", "port": "Gi0/2"}]}
        return result

    monkeypatch.setattr(napalm_svc, "get_lldp_neighbors", fake_lldp)
    _patch_resolution(monkeypatch, {"nodo-sin-pistas": "10.0.0.88"})

    response = _scan(client, ["core-01"])
    assert response.status_code == 200
    assert "plataforma_sugerida" not in response.json()["candidatos"][0]

    def _must_not_add(_device):
        raise AssertionError("no debe registrarse sin driver conocido")

    monkeypatch.setattr(inventory_svc, "add_device", _must_not_add)

    imported = client.post(
        "/api/discovery/import",
        json={"candidates": ["10.0.0.88"]},
        headers=OPERATOR,
    )
    assert imported.status_code == 200
    body = imported.json()
    assert body["creados"] == []
    assert "Plataforma no identificada" in body["fallidos"][0]["motivo"]


def test_import_ip_fuera_del_escaneo(client, monkeypatch):
    _patch_inventory(monkeypatch, [CORE])
    response = client.post(
        "/api/discovery/import",
        json={"candidates": ["10.9.9.9"]},
        headers=OPERATOR,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["fallidos"][0]["ip"] == "10.9.9.9"
    assert "último escaneo" in body["fallidos"][0]["motivo"]


# ── RBAC ─────────────────────────────────────────────────────


def test_import_requiere_operator(client, monkeypatch):
    _patch_inventory(monkeypatch, [CORE])
    response = client.post(
        "/api/discovery/import",
        json={"candidates": ["10.0.0.50"]},
        headers=VIEWER,
    )
    assert response.status_code == 403


def test_scan_y_candidates_requieren_token(client):
    assert client.post("/api/discovery/scan", json={"metodo": "lldp"}).status_code in (401, 403)
    assert client.get("/api/discovery/candidates").status_code in (401, 403)


# ── RBAC por grupo en la importación ─────────────────────────


def _patch_lldp_dos_vecinos(monkeypatch):
    def fake_lldp(device_id):
        result = napalm_svc.NapalmResult(device_id)
        result.success = True
        result.data = {
            "Gi0/1": [{"hostname": "juniper-edge-01", "port": "Gi0/2"}],
            "Gi0/2": [{"hostname": "nuevo-edge", "port": "Gi0/3"}],
        }
        return result

    monkeypatch.setattr(napalm_svc, "get_lldp_neighbors", fake_lldp)
    _patch_resolution(
        monkeypatch,
        {"juniper-edge-01": "10.0.0.50", "nuevo-edge": "10.0.0.60"},
    )


def test_import_scope_grupo_autorizado_crea(client, monkeypatch):
    _patch_inventory(monkeypatch, [CORE])
    _patch_resolution(monkeypatch, {"juniper-edge-01": "10.0.0.50"})
    monkeypatch.setattr(napalm_svc, "get_lldp_neighbors", _lldp_ok)
    assert _scan(client, ["core-01"]).status_code == 200

    captured: dict = {}

    def fake_add(device):
        captured.update(device)
        return {**device, "id": device["id"]}

    monkeypatch.setattr(inventory_svc, "add_device", fake_add)

    response = client.post(
        "/api/discovery/import",
        json={"candidates": ["10.0.0.50"], "group": "core"},
        headers=_scoped_headers(scope_group="Core"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["resumen"]["creados"] == 1
    assert body["rechazados"] == []
    # Se escribe el grupo solicitado (mismo nombre), validado sin distinguir
    # mayúsculas contra el scope.
    assert captured["group"] == "core"


def test_import_scope_parcial_rechaza_por_dispositivo(client, monkeypatch):
    _patch_inventory(monkeypatch, [CORE, EXISTING])
    _patch_lldp_dos_vecinos(monkeypatch)
    assert _scan(client, ["core-01"]).status_code == 200

    def _must_not_add(_device):
        raise AssertionError("no debe registrarse nada fuera de scope")

    monkeypatch.setattr(inventory_svc, "add_device", _must_not_add)

    response = client.post(
        "/api/discovery/import",
        json={"candidates": ["10.0.0.50", "10.0.0.60"], "group": "Edge"},
        headers=_scoped_headers(scope_group="Core"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["creados"] == []
    assert body["ya_existian"][0]["id"] == "existing-01"
    assert body["rechazados"] == [{
        "device_id": "nuevo-edge",
        "ip": "10.0.0.60",
        "error": "device fuera de tu scope: Edge",
    }]
    assert body["resumen"]["solicitados"] == 2
    assert body["resumen"]["creados"] == 0


def test_import_scope_todos_fuera_devuelve_403(client, monkeypatch):
    _patch_inventory(monkeypatch, [CORE])
    _patch_resolution(monkeypatch, {"juniper-edge-01": "10.0.0.50"})
    monkeypatch.setattr(napalm_svc, "get_lldp_neighbors", _lldp_ok)
    assert _scan(client, ["core-01"]).status_code == 200

    def _must_not_add(_device):
        raise AssertionError("no debe registrarse fuera de scope")

    monkeypatch.setattr(inventory_svc, "add_device", _must_not_add)

    response = client.post(
        "/api/discovery/import",
        json={"candidates": ["10.0.0.50"], "group": "Edge"},
        headers=_scoped_headers(scope_group="Core"),
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "device fuera de tu scope: Edge"


def test_import_scope_sin_grupo_rechaza(client, monkeypatch):
    _patch_inventory(monkeypatch, [CORE])
    _patch_resolution(monkeypatch, {"juniper-edge-01": "10.0.0.50"})
    monkeypatch.setattr(napalm_svc, "get_lldp_neighbors", _lldp_ok)
    assert _scan(client, ["core-01"]).status_code == 200

    response = client.post(
        "/api/discovery/import",
        json={"candidates": ["10.0.0.50"]},
        headers=_scoped_headers(scope_group="Core"),
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "device fuera de tu scope: sin grupo"

    # La lectura sigue siendo global para el mismo token restringido.
    assert client.get("/api/discovery/candidates",
                      headers=_scoped_headers(role="viewer", scope_group="Core")).status_code == 200


def test_import_token_legado_conserva_acceso(client, monkeypatch):
    _patch_inventory(monkeypatch, [CORE])
    _patch_resolution(monkeypatch, {"juniper-edge-01": "10.0.0.50"})
    monkeypatch.setattr(napalm_svc, "get_lldp_neighbors", _lldp_ok)
    assert _scan(client, ["core-01"]).status_code == 200

    captured: dict = {}

    def fake_add(device):
        captured.update(device)
        return {**device, "id": device["id"]}

    monkeypatch.setattr(inventory_svc, "add_device", fake_add)

    response = client.post(
        "/api/discovery/import",
        json={"candidates": ["10.0.0.50"], "group": "Edge"},
        headers=OPERATOR,
    )
    assert response.status_code == 200, response.text
    assert response.json()["rechazados"] == []
    assert captured["group"] == "Edge"
