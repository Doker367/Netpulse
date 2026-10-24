"""Tests del router NetBox: resumen de sync y checklist de estado.

Todo está mockeado con un ``httpx.AsyncClient`` falso: **no se abre ninguna
conexión real** ni se toca ``config/devices.yaml`` (se redirige a ``tmp_path``).
"""

import copy

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.main import app
from app.routers import netbox as netbox_router

ADMIN = {"Authorization": "Bearer " + create_access_token({"sub": "admin", "role": "admin"})}
OPERATOR = {"Authorization": "Bearer " + create_access_token({"sub": "op", "role": "operator"})}

LOCAL_DEVICE = {
    "id": "core-1",
    "hostname": "10.0.0.99",
    "port": 2225,
    "driver": "ios",
    "type": "switch",
    "tags": ["manual"],
    "description": "dado de alta a mano",
    "netbox_id": 1,
    "netbox_name": "core-1",
    "credentials": {"username": "admin", "password": "enc:secreto"},
}

LOCAL_OPTIONS = {"collect_interval": 30}


# ── Mocks de red ──────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://netbox.test/api/")
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=request,
                response=httpx.Response(self.status_code, text=self.text, request=request),
            )

    def json(self):
        return self._payload


def _install_netbox(monkeypatch, handler):
    """Sustituye ``httpx.AsyncClient`` por un cliente falso y registra llamadas."""
    calls: list[tuple] = []

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def get(self, url, headers=None, params=None):
            calls.append(("GET", url, params))
            return handler(url, params)

        async def post(self, url, headers=None, json=None):
            calls.append(("POST", url, json))
            return handler(url, None)

    monkeypatch.setattr(netbox_router.httpx, "AsyncClient", _FakeAsyncClient)
    return calls


def _devices_handler(devices):
    """Handler que responde a /api/dcim/devices/ con la lista dada."""

    def handler(url, params):
        if "/dcim/devices/" not in url:
            raise AssertionError(f"llamada inesperada a {url}")
        return _FakeResponse(
            {"count": len(devices), "next": None, "results": devices}
        )

    return handler


def _nb_device(
    nb_id,
    name=None,
    address="10.0.0.10/24",
    manufacturer="Cisco",
    model="C9300",
    role="Switch",
    site="DC1",
    comments="",
    **extra,
):
    device = {
        "id": nb_id,
        "name": name,
        "device_type": {"model": model, "manufacturer": {"name": manufacturer}},
        "role": {"name": role, "slug": role.lower()},
        "site": {"name": site, "slug": site.lower()},
        "status": {"value": "active"},
        "primary_ip": {"address": address} if address is not None else None,
        "comments": comments,
    }
    device.update(extra)
    return device


# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture()
def inventory(tmp_path, monkeypatch):
    path = tmp_path / "devices.yaml"
    path.write_text(
        yaml.safe_dump(
            {"devices": [copy.deepcopy(LOCAL_DEVICE)], "options": copy.deepcopy(LOCAL_OPTIONS)},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(netbox_router, "DEVICES_FILE", path)
    monkeypatch.setattr(netbox_router, "NETBOX_URL", "http://netbox.test:8000")
    monkeypatch.setattr(netbox_router, "NETBOX_TOKEN", "nbt_test.token")
    return path


@pytest.fixture()
def client():
    # Sin context manager: no se ejecuta el lifespan (ni syslog ni seeds).
    return TestClient(app)


def _read(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ── Sync feliz: creados / actualizados / sin_cambios ─────────


def test_sync_feliz_resumen_estable(inventory, client, monkeypatch):
    devices = [
        _nb_device(200, "edge-200", address="10.0.0.200/24"),
        # Renombrado no: actualiza el equipo local 'core-1' por nombre.
        _nb_device(1, "core-1", address="10.0.0.1/24", role="Router", site="DC2", comments="desde NetBox"),
        _nb_device(100, "edge-100", address="10.0.0.100/24"),
    ]
    _install_netbox(monkeypatch, _devices_handler(devices))

    res = client.post("/api/netbox/sync", headers=ADMIN)

    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) >= {
        "status", "solicitados", "creados", "actualizados", "sin_cambios",
        "saltados", "errores", "inventario_total", "netbox_total",
    }
    assert body["status"] == "ok"
    assert body["solicitados"] == 3
    assert body["netbox_total"] == 3
    # Determinista: ordenados por device_id aunque NetBox llegue desordenado.
    assert body["creados"] == ["netbox-100", "netbox-200"]
    assert body["actualizados"] == ["core-1"]  # se conserva el id local
    assert body["sin_cambios"] == 0
    assert body["saltados"] == []
    assert body["errores"] == []
    assert body["inventario_total"] == 3
    # Compatibilidad con la UI actual.
    assert body["created"] == 2
    assert body["updated"] == 1
    assert body["skipped"] == 0

    saved = _read(inventory)
    by_id = {d["id"]: d for d in saved["devices"]}
    assert set(by_id) == {"core-1", "netbox-100", "netbox-200"}
    # Se actualizan los campos importados...
    core = by_id["core-1"]
    assert core["hostname"] == "10.0.0.1"
    assert core["type"] == "router"
    assert core["netbox_name"] == "core-1"
    assert core["netbox_url"] == "http://netbox.test:8000/dcim/devices/1/"
    # ...sin tocar credenciales, puerto ni id local.
    assert core["credentials"] == {"username": "admin", "password": "enc:secreto"}
    assert core["port"] == 2225
    assert core["id"] == "core-1"
    # Nuevos: puerto por defecto 22.
    assert by_id["netbox-100"]["port"] == 22
    assert by_id["netbox-100"]["driver"] == "ios"
    # El resto del YAML se conserva.
    assert saved["options"] == LOCAL_OPTIONS


def test_sync_segunda_pasada_es_sin_cambios(inventory, client, monkeypatch):
    devices = [
        _nb_device(1, "core-1", address="10.0.0.1/24", role="Router", site="DC2", comments="desde NetBox"),
        _nb_device(100, "edge-100", address="10.0.0.100/24"),
    ]
    _install_netbox(monkeypatch, _devices_handler(devices))
    assert client.post("/api/netbox/sync", headers=ADMIN).status_code == 200
    before = inventory.read_bytes()

    res = client.post("/api/netbox/sync", headers=ADMIN)

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ok"
    assert body["creados"] == []
    assert body["actualizados"] == []
    assert body["sin_cambios"] == 2
    assert body["inventario_total"] == 2
    # Sin cambios reales, no se reescribe el archivo.
    assert inventory.read_bytes() == before


# ── Saltados y errores con motivo ─────────────────────────────


def test_sync_saltados_y_errores_con_motivo(inventory, client, monkeypatch):
    devices = [
        _nb_device(20, "sin-ip-20", address=None),
        _nb_device(10, "sin-ip-10", address=None),
        # Sin 'id': no se puede construir la clave local → error por equipo.
        {"name": "roto", "primary_ip": {"address": "10.0.0.9/24"}},
    ]
    _install_netbox(monkeypatch, _devices_handler(devices))
    before = inventory.read_bytes()

    res = client.post("/api/netbox/sync", headers=ADMIN)

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "partial"
    assert body["solicitados"] == 3
    assert body["creados"] == []
    assert body["actualizados"] == []
    assert body["inventario_total"] == 1
    assert [s["device_id"] for s in body["saltados"]] == ["netbox-10", "netbox-20"]
    assert all(s["motivo"] for s in body["saltados"])
    assert "primary_ip" in body["saltados"][0]["motivo"]
    assert len(body["errores"]) == 1
    assert body["errores"][0]["device_id"] == "netbox-?"
    assert body["errores"][0]["detalle"]
    # Nada se escribió: no se inventó 127.0.0.1 y el inventario quedó intacto.
    assert inventory.read_bytes() == before


# ── NetBox no disponible / no configurado ─────────────────────


def test_sync_netbox_caido_devuelve_503(inventory, client, monkeypatch):
    def handler(url, params):
        raise httpx.ConnectError("Connection refused")

    _install_netbox(monkeypatch, handler)
    before = inventory.read_bytes()

    res = client.post("/api/netbox/sync", headers=ADMIN)

    assert res.status_code == 503
    assert res.json()["detail"] == "NetBox no disponible: conexión rechazada"
    assert inventory.read_bytes() == before


@pytest.mark.parametrize("exc", [httpx.ReadTimeout("timed out"), httpx.ConnectTimeout("timed out")])
def test_sync_netbox_timeout_devuelve_503(inventory, client, monkeypatch, exc):
    def handler(url, params):
        raise exc

    _install_netbox(monkeypatch, handler)

    res = client.post("/api/netbox/sync", headers=ADMIN)

    assert res.status_code == 503
    assert res.json()["detail"] == "NetBox no disponible: tiempo de espera agotado"


def test_sync_netbox_5xx_se_traduce_a_503(inventory, client, monkeypatch):
    def handler(url, params):
        return _FakeResponse(status_code=502, text="Bad Gateway")

    _install_netbox(monkeypatch, handler)

    res = client.post("/api/netbox/sync", headers=ADMIN)

    assert res.status_code == 503
    assert res.json()["detail"] == "NetBox no disponible: error 502 de NetBox"


def test_sync_sin_configurar_devuelve_503_sin_llamar_netbox(inventory, client, monkeypatch):
    monkeypatch.setattr(netbox_router, "NETBOX_TOKEN", "")

    def handler(url, params):
        raise AssertionError("no debe llamarse a NetBox sin configuración")

    _install_netbox(monkeypatch, handler)

    res = client.post("/api/netbox/sync", headers=ADMIN)

    assert res.status_code == 503
    assert res.json()["detail"] == netbox_router.NETBOX_SIN_CONFIGURAR
    assert res.json()["detail"].startswith("NetBox no configurado: define NETBOX_URL y NETBOX_TOKEN")


def test_sync_sin_url_devuelve_503(inventory, client, monkeypatch):
    monkeypatch.setattr(netbox_router, "NETBOX_URL", "")
    _install_netbox(monkeypatch, lambda url, params: (_ for _ in ()).throw(AssertionError("sin red")))

    res = client.post("/api/netbox/sync", headers=ADMIN)

    assert res.status_code == 503
    assert res.json()["detail"] == netbox_router.NETBOX_SIN_CONFIGURAR


# ── Inventario local: lectura y escritura ─────────────────────


def test_sync_escritura_fallida_no_deja_a_medias(inventory, client, monkeypatch):
    before = inventory.read_bytes()
    _install_netbox(monkeypatch, _devices_handler([_nb_device(7, "nuevo-7")]))

    def boom(*args, **kwargs):
        raise OSError("espacio insuficiente en disco")

    monkeypatch.setattr(netbox_router.tempfile, "mkstemp", boom)

    res = client.post("/api/netbox/sync", headers=ADMIN)

    assert res.status_code == 500
    detail = res.json()["detail"]
    assert detail.startswith("No se pudo escribir el inventario local: ")
    assert "No se aplicó ningún cambio." in detail
    # Escritura atómica: el YAML anterior sigue intacto.
    assert inventory.read_bytes() == before


def test_sync_crea_si_yaml_no_tiene_clave_devices(inventory, client, monkeypatch):
    inventory.write_text("options: {collect_interval: 60}\n", encoding="utf-8")
    _install_netbox(monkeypatch, _devices_handler([_nb_device(7, "nuevo-7")]))

    res = client.post("/api/netbox/sync", headers=ADMIN)

    assert res.status_code == 200, res.text
    saved = _read(inventory)
    assert [d["id"] for d in saved["devices"]] == ["netbox-7"]
    assert saved["options"] == {"collect_interval": 60}


def test_sync_yaml_corrupto_devuelve_500_claro(inventory, client, monkeypatch):
    inventory.write_text("devices: [\n", encoding="utf-8")
    _install_netbox(monkeypatch, _devices_handler([_nb_device(7, "nuevo-7")]))

    res = client.post("/api/netbox/sync", headers=ADMIN)

    assert res.status_code == 500
    assert res.json()["detail"].startswith("No se pudo leer el inventario local: ")


def test_sync_no_borra_equipos_locales(inventory, client, monkeypatch):
    _install_netbox(monkeypatch, _devices_handler([]))

    res = client.post("/api/netbox/sync", headers=ADMIN)

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ok"
    assert body["inventario_total"] == 1
    saved = _read(inventory)
    assert [d["id"] for d in saved["devices"]] == ["core-1"]
    assert saved["devices"][0]["credentials"]["password"] == "enc:secreto"


# ── GET /api/netbox/status ────────────────────────────────────


def test_status_configurado_no_contacta_netbox(inventory, client):
    # Sin mock de AsyncClient: si el endpoint tocara la red, fallaría la petición.
    res = client.get("/api/netbox/status", headers=ADMIN)

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["configurado"] is True
    assert body["tiene_token"] is True
    assert body["url_enmascarada"] == "http://netbox.test:8000"
    assert "dispositivos" in body["alcance"].lower()
    assert any(texto.startswith("no borra") for texto in body["no_hace"])
    assert body["mensaje"] == ""


def test_status_sin_configurar_es_honesto(inventory, client, monkeypatch):
    monkeypatch.setattr(netbox_router, "NETBOX_TOKEN", "")

    res = client.get("/api/netbox/status", headers=ADMIN)

    assert res.status_code == 200  # checklist, no error
    body = res.json()
    assert body["configurado"] is False
    assert body["tiene_token"] is False
    assert body["mensaje"] == netbox_router.NETBOX_SIN_CONFIGURAR


def test_status_enmascara_credenciales_de_la_url(inventory, client, monkeypatch):
    monkeypatch.setattr(
        netbox_router, "NETBOX_URL", "https://usuario:secreto@netbox.example:8443"
    )

    body = client.get("/api/netbox/status", headers=ADMIN).json()

    assert body["url_enmascarada"] == "https://***@netbox.example:8443"
    assert "secreto" not in body["url_enmascarada"]
    assert "usuario" not in body["url_enmascarada"]


# ── RBAC y regresión de endpoints existentes ──────────────────


def test_sync_y_status_requieren_admin(inventory, client):
    assert client.post("/api/netbox/sync", headers=OPERATOR).status_code == 403
    assert client.get("/api/netbox/status", headers=OPERATOR).status_code == 403


def test_list_devices_sigue_funcionando_con_paginacion(inventory, client, monkeypatch):
    def handler(url, params):
        assert "/dcim/devices/" in url
        if "offset=200" in url:
            return _FakeResponse(
                {"results": [_nb_device(2, "edge-2", address="10.0.0.2/24")], "next": None}
            )
        return _FakeResponse(
            {
                "results": [_nb_device(1, "core-1", address="10.0.0.1/24")],
                "next": "http://netbox.test:8000/api/dcim/devices/?limit=200&offset=200",
            }
        )

    _install_netbox(monkeypatch, handler)

    res = client.get("/api/netbox/devices", headers=ADMIN)

    assert res.status_code == 200, res.text
    body = res.json()
    assert [d["id"] for d in body] == [1, 2]
    assert body[0] == {
        "id": 1,
        "name": "core-1",
        "device_type": "C9300",
        "role": "Switch",
        "site": "DC1",
        "status": "active",
        "primary_ip": "10.0.0.1/24",
    }
