"""RBAC fase 2: scope en las mutaciones restantes.

Cubre SFTP upload, baseline de compliance, plantillas apply/confirm y sync
NetBox, además de dejar fijadas las lecturas que siguen globales por decisión
de producto (SFTP list/download, facts, compliance check/report).
"""

from unittest.mock import MagicMock

import pytest
import yaml
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.main import app
from app.routers import facts as facts_router
from app.routers import netbox as netbox_router
from app.routers import sftp as sftp_router
from app.services import compliance_svc, inventory_svc, templates_svc


def headers(role="admin", **claims):
    token = create_access_token({"sub": "tester", "role": role, **claims})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    path = tmp_path / "devices.yaml"
    path.write_text(yaml.safe_dump({"devices": [
        {"id": "core-1", "hostname": "10.0.0.1", "driver": "ios", "group": "Core"},
        {"id": "dmz-1", "hostname": "10.0.0.2", "driver": "ios", "group": "DMZ"},
        {"id": "none-1", "hostname": "10.0.0.3", "driver": "ios"},
    ]}))
    monkeypatch.setattr(inventory_svc, "DEVICES_FILE", path)
    inventory_svc.invalidate_cache()
    yield path
    inventory_svc.invalidate_cache()


@pytest.fixture
def client():
    return TestClient(app)


# ── SFTP: upload es mutación; list/download siguen globales ────


def _fake_sftp():
    sftp = MagicMock()
    sftp.listdir_attr.return_value = []
    client = MagicMock()
    return client, sftp


def test_sftp_upload_fuera_de_scope_no_abre_ssh(inventory, client, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("no debe abrir sesión SSH fuera de scope")

    monkeypatch.setattr(sftp_router, "_get_ssh_client_and_sftp", boom)

    res = client.post(
        "/api/sftp/core-1/upload?filename=cfg.txt",
        content=b"x",
        headers=headers(scope_group="dmz"),
    )

    assert res.status_code == 403
    assert res.json()["detail"] == "device fuera de tu scope: Core"


def test_sftp_upload_autorizado(inventory, client, monkeypatch):
    fake_client, fake_sftp = _fake_sftp()
    monkeypatch.setattr(
        sftp_router, "_get_ssh_client_and_sftp", lambda device_id: (fake_client, fake_sftp)
    )

    res = client.post(
        "/api/sftp/dmz-1/upload?filename=cfg.txt&remote_dir=/configs",
        content=b"hostname dmz",
        headers=headers(scope_sites=["DMZ"]),
    )

    assert res.status_code == 200, res.text
    assert res.json()["remote_path"] == "/configs/cfg.txt"
    assert fake_sftp.putfo.called


def test_sftp_upload_legacy_sin_claims_intacto(inventory, client, monkeypatch):
    fake_client, fake_sftp = _fake_sftp()
    monkeypatch.setattr(
        sftp_router, "_get_ssh_client_and_sftp", lambda device_id: (fake_client, fake_sftp)
    )

    res = client.post(
        "/api/sftp/core-1/upload?filename=cfg.txt",
        content=b"x",
        headers=headers(),
    )

    assert res.status_code == 200, res.text
    assert fake_sftp.putfo.called


def test_sftp_list_sigue_siendo_lectura_global(inventory, client, monkeypatch):
    fake_client, fake_sftp = _fake_sftp()
    monkeypatch.setattr(
        sftp_router, "_get_ssh_client_and_sftp", lambda device_id: (fake_client, fake_sftp)
    )

    res = client.get("/api/sftp/core-1/list?path=/", headers=headers(scope_group="dmz"))

    assert res.status_code == 200, res.text
    assert res.json()["items"] == []


# ── Facts: decisión de fase 2 = lectura global ────────────────


def test_facts_siguen_siendo_lectura_global(inventory, client, monkeypatch):
    from app.services.napalm_svc import NapalmResult

    result = NapalmResult("core-1")
    result.success = True
    result.data = {
        "hostname": "core-1", "os_version": "17.9", "model": "C9300",
        "vendor": "Cisco", "serial_number": "ABC123", "uptime": 100.0,
        "interface_list": ["Gi0/0"],
    }
    monkeypatch.setattr(facts_router.napalm_svc, "get_facts", lambda device_id: result)

    res = client.get(
        "/api/facts/core-1", headers=headers(role="viewer", scope_group="dmz")
    )

    assert res.status_code == 200, res.text
    assert res.json()["hostname"] == "core-1"


# ── Compliance: baseline es mutación de grupo; check/report lectura ──


def test_compliance_baseline_fuera_de_scope_no_escribe(inventory, client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        compliance_svc, "set_baseline", lambda group, config: calls.append(group) or config
    )

    res = client.post(
        "/api/compliance/baseline",
        json={"group": "Core", "config": "hostname core\n"},
        headers=headers(scope_group="dmz"),
    )

    assert res.status_code == 403
    assert res.json()["detail"] == "device fuera de tu scope: Core"
    assert calls == []


def test_compliance_baseline_autorizada(inventory, client, monkeypatch):
    monkeypatch.setattr(compliance_svc, "set_baseline", lambda group, config: config)

    res = client.post(
        "/api/compliance/baseline",
        json={"group": "DMZ", "config": "hostname dmz\n"},
        headers=headers(scope_group="dmz"),
    )

    assert res.status_code == 200, res.text
    assert res.json()["group"] == "DMZ"


def test_compliance_baseline_legacy_sin_claims_intacto(inventory, client, monkeypatch):
    monkeypatch.setattr(compliance_svc, "set_baseline", lambda group, config: config)

    res = client.post(
        "/api/compliance/baseline",
        json={"group": "Core", "config": "hostname core\n"},
        headers=headers(),
    )

    assert res.status_code == 200, res.text


def test_compliance_check_sigue_siendo_lectura_global(inventory, client, monkeypatch):
    monkeypatch.setattr(
        compliance_svc, "check_compliance",
        lambda device_id, baseline=None: {
            "device_id": device_id, "compliant": True,
            "violations": [], "baseline_group": "Core",
        },
    )

    res = client.post(
        "/api/compliance/check",
        json={"device_id": "core-1"},
        headers=headers(scope_group="dmz"),
    )

    assert res.status_code == 200, res.text
    assert res.json()["device_id"] == "core-1"


# ── Plantillas: apply y confirm validan el device real ─────────


def _apply_result(device_id):
    return {
        "device_id": device_id, "template": "vlan", "driver": "ios",
        "diff": "+ vlan 100", "dry_run_ok": True, "error": None,
        "session_token": "tok-1",
    }


def test_template_apply_fuera_de_scope_no_renderiza(inventory, client, monkeypatch):
    monkeypatch.setattr(
        templates_svc, "apply_template",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("no debe renderizar")),
    )

    res = client.post(
        "/api/templates/vlan/apply",
        json={"device_id": "core-1", "params": {"vlan_id": 100}},
        headers=headers(scope_group="dmz"),
    )

    assert res.status_code == 403
    assert res.json()["detail"] == "device fuera de tu scope: Core"


def test_template_apply_autorizado(inventory, client, monkeypatch):
    monkeypatch.setattr(
        templates_svc, "apply_template", lambda **kwargs: _apply_result(kwargs["device_id"])
    )

    res = client.post(
        "/api/templates/vlan/apply",
        json={"device_id": "dmz-1", "params": {"vlan_id": 100}},
        headers=headers(scope_group="dmz"),
    )

    assert res.status_code == 200, res.text
    assert res.json()["device_id"] == "dmz-1"
    assert res.json()["session_token"] == "tok-1"


def test_template_apply_legacy_sin_claims_intacto(inventory, client, monkeypatch):
    monkeypatch.setattr(
        templates_svc, "apply_template", lambda **kwargs: _apply_result(kwargs["device_id"])
    )

    res = client.post(
        "/api/templates/vlan/apply",
        json={"device_id": "core-1", "params": {"vlan_id": 100}},
        headers=headers(),
    )

    assert res.status_code == 200, res.text


def test_template_confirm_fuera_de_scope_no_commitea(inventory, client, monkeypatch):
    sessions = {"tok-core": {"device_id": "core-1", "template_name": "vlan"}}
    monkeypatch.setattr(templates_svc, "_pending_sessions", sessions)
    monkeypatch.setattr(
        templates_svc, "confirm_apply",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("no debe commitear")),
    )

    res = client.post(
        "/api/templates/vlan/apply/confirm",
        json={"session_token": "tok-core"},
        headers=headers(scope_group="dmz"),
    )

    assert res.status_code == 403
    assert res.json()["detail"] == "device fuera de tu scope: Core"
    # El rechazo no consume la sesión pendiente.
    assert "tok-core" in sessions


def test_template_confirm_autorizado(inventory, client, monkeypatch):
    sessions = {"tok-dmz": {"device_id": "dmz-1", "template_name": "vlan"}}
    monkeypatch.setattr(templates_svc, "_pending_sessions", sessions)
    monkeypatch.setattr(
        templates_svc, "confirm_apply",
        lambda **kwargs: {
            "device_id": "dmz-1", "template": "vlan",
            "committed": True, "diff": "+ vlan 100", "error": None,
        },
    )

    res = client.post(
        "/api/templates/vlan/apply/confirm",
        json={"session_token": "tok-dmz"},
        headers=headers(scope_group="dmz"),
    )

    assert res.status_code == 200, res.text
    assert res.json()["committed"] is True


def test_template_confirm_legacy_sin_claims_intacto(inventory, client, monkeypatch):
    sessions = {"tok-core": {"device_id": "core-1", "template_name": "vlan"}}
    monkeypatch.setattr(templates_svc, "_pending_sessions", sessions)
    monkeypatch.setattr(
        templates_svc, "confirm_apply",
        lambda **kwargs: {
            "device_id": "core-1", "template": "vlan",
            "committed": True, "diff": "", "error": None,
        },
    )

    res = client.post(
        "/api/templates/vlan/apply/confirm",
        json={"session_token": "tok-core"},
        headers=headers(),
    )

    assert res.status_code == 200, res.text
    assert res.json()["committed"] is True


# ── NetBox sync: importa autorizados y salta el resto ─────────


class _FakeResponse:
    def __init__(self, payload=None):
        self._payload = payload or {}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _install_netbox(monkeypatch, devices):
    calls = []

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def get(self, url, headers=None, params=None):
            calls.append(("GET", url))
            return _FakeResponse({"count": len(devices), "next": None, "results": devices})

    monkeypatch.setattr(netbox_router.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(netbox_router, "NETBOX_URL", "http://netbox.test:8000")
    monkeypatch.setattr(netbox_router, "NETBOX_TOKEN", "nbt_test.token")
    return calls


def _nb_device(nb_id, name=None, address="10.0.0.10/24", **extra):
    device = {
        "id": nb_id,
        "name": name,
        "device_type": {"model": "C9300", "manufacturer": {"name": "Cisco"}},
        "role": {"name": "Switch", "slug": "switch"},
        "site": {"name": "DC1", "slug": "dc1"},
        "status": {"value": "active"},
        "primary_ip": {"address": address} if address is not None else None,
        "comments": "",
    }
    device.update(extra)
    return device


def _read(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_netbox_sync_importa_autorizados_y_salta_fuera_de_scope(
    inventory, client, monkeypatch
):
    inventory.write_text(yaml.safe_dump({"devices": [
        {"id": "core-1", "hostname": "10.0.0.1", "driver": "ios", "group": "Core"},
        {"id": "dmz-1", "hostname": "10.0.0.2", "driver": "ios", "group": "DMZ"},
    ]}))
    inventory_svc.invalidate_cache()
    monkeypatch.setattr(netbox_router, "DEVICES_FILE", inventory)
    devices = [
        _nb_device(1, "core-1", address="10.0.0.99/24"),
        _nb_device(2, "dmz-1", address="10.0.0.98/24"),
        # NetBox puede entregar 'group' (campo no estándar): se respeta.
        _nb_device(100, "nuevo-dmz", group="DMZ"),
        # Sin grupo explícito: nace sin grupo y un token restringido no puede.
        _nb_device(200, "nuevo-sin-grupo"),
    ]
    _install_netbox(monkeypatch, devices)

    res = client.post("/api/netbox/sync", headers=headers(scope_sites=["DMZ"]))

    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) >= {
        "status", "solicitados", "creados", "actualizados", "sin_cambios",
        "saltados", "errores", "inventario_total", "netbox_total",
    }
    assert body["status"] == "partial"
    assert body["creados"] == ["netbox-100"]
    assert body["actualizados"] == ["dmz-1"]
    assert [s["device_id"] for s in body["saltados"]] == ["core-1", "netbox-200"]
    assert [s["motivo"] for s in body["saltados"]] == [
        "device fuera de tu scope: Core",
        "device fuera de tu scope: sin grupo",
    ]
    assert body["errores"] == []
    assert body["created"] == 1 and body["updated"] == 1 and body["skipped"] == 2

    saved = {d["id"]: d for d in _read(inventory)["devices"]}
    # core-1 no se tocó: sigue con su hostname y su grupo.
    assert saved["core-1"]["hostname"] == "10.0.0.1"
    assert saved["core-1"]["group"] == "Core"
    # dmz-1 sí se actualiza y conserva su grupo.
    assert saved["dmz-1"]["hostname"] == "10.0.0.98"
    assert saved["dmz-1"]["group"] == "DMZ"
    # El creado toma el grupo que vino de NetBox.
    assert saved["netbox-100"]["group"] == "DMZ"
    # El que estaba fuera de scope no se crea.
    assert "netbox-200" not in saved


def test_netbox_sync_todos_fuera_de_scope_es_parcial_sin_503(
    inventory, client, monkeypatch
):
    inventory.write_text(yaml.safe_dump({"devices": [
        {"id": "core-1", "hostname": "10.0.0.1", "driver": "ios", "group": "Core"},
    ]}))
    inventory_svc.invalidate_cache()
    monkeypatch.setattr(netbox_router, "DEVICES_FILE", inventory)
    _install_netbox(monkeypatch, [
        _nb_device(1, "core-1", address="10.0.0.99/24"),
        _nb_device(300, "nuevo-sin-grupo"),
    ])
    before = inventory.read_bytes()

    res = client.post("/api/netbox/sync", headers=headers(scope_group="dmz"))

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "partial"
    assert body["creados"] == [] and body["actualizados"] == []
    assert [s["device_id"] for s in body["saltados"]] == ["core-1", "netbox-300"]
    # No se reescribe el inventario cuando no hay nada autorizado.
    assert inventory.read_bytes() == before


def test_netbox_sync_legacy_sin_claims_intacto(inventory, client, monkeypatch):
    monkeypatch.setattr(netbox_router, "DEVICES_FILE", inventory)
    _install_netbox(monkeypatch, [
        _nb_device(1, "core-1", address="10.0.0.99/24"),
        _nb_device(200, "nuevo-sin-grupo"),
    ])

    res = client.post("/api/netbox/sync", headers=headers())

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["actualizados"] == ["core-1"]
    assert body["creados"] == ["netbox-200"]
    assert body["saltados"] == []
    saved = {d["id"]: d for d in _read(inventory)["devices"]}
    assert saved["core-1"]["hostname"] == "10.0.0.99"
    assert saved["core-1"]["group"] == "Core"
