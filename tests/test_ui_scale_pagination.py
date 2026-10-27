"""Escala 500 (Tarjeta 12E): paginación del inventario y límite de tarjetas.

Lo que se comprueba aquí es lo que la suite puede ver sin navegador: que la
página sirve las versiones nuevas de las vistas (cache-bump), que esos archivos
existen con los controles de paginación/recorte, que las claves i18n están en
es y en, y que el JavaScript parsea. La verificación funcional (500 equipos
reales, mediciones antes/después) vive en ``docs/capacidad.md``.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main

ROOT = Path(__file__).resolve().parent.parent
VIEWS = ROOT / "app" / "static" / "js" / "views"


def test_dashboard_serves_the_scale_versions_of_both_views():
    html = TestClient(main.app).get("/").text
    assert '/static/js/views/devices.js?v=2' in html
    assert '/static/js/views/dashboard.js?v=2' in html


def test_inventory_view_ships_client_side_pagination():
    client = TestClient(main.app)
    res = client.get("/static/js/views/devices.js?v=2")
    assert res.status_code == 200
    js = res.text
    for marker in (
        "INV_PAGE_SIZES = [50, 100]",   # tamaños de página
        "let _invRows",                # conjunto completo (no la página)
        "function _invMatches",        # búsqueda sobre todo el inventario
        "function invSearch",
        "function invGoPage",
        "function invNextPage",
        "function invPrevPage",
        "function invSetPageSize",
        "inv-table-wrap",
        "inv-pager",
        "inv-page-size",
        "inv-page-total",              # contador "página X de Y"
    ):
        assert marker in js, marker
    # i18n en español e inglés para los controles nuevos
    for key in ("inv_page_search", "inv_page_prev", "inv_page_next", "inv_page_devices"):
        assert js.count(key) >= 2, key
    assert "es" in js and "I18N.es" in js and "I18N.en" in js


def test_dashboard_caps_cards_at_100_with_honest_notice():
    client = TestClient(main.app)
    res = client.get("/static/js/views/dashboard.js?v=2")
    assert res.status_code == 200
    js = res.text
    for marker in (
        "const DASH_CARD_LIMIT = 100",
        "devices.slice(0, DASH_CARD_LIMIT)",
        "dash-limit-note",
        "function overLimitNoteTotals",
        "ov_limited_nohit",
        "ov_limited_open",
    ):
        assert marker in js, marker
    # La nota solo aparece cuando hay algo oculto; con ≤100 no se recorta.
    assert "matching > visible" in js
    for key in ("ov_limited_show", "ov_limited_of", "ov_limited_devices", "ov_limited_rest"):
        assert js.count(key) >= 2, key
    # El fan-out de facts se limita a las tarjetas realmente pintadas.
    assert "document.getElementById(`rcpu-${d.id}`)" in js


def test_le_100_path_is_the_previous_behaviour():
    """Con ≤100 equipos no se recorta ni se añade nota: mismo renderD de antes."""
    js = (VIEWS / "dashboard.js").read_text(encoding="utf-8")
    assert "const overLimit = devices.length > DASH_CARD_LIMIT;" in js
    assert "const shown = overLimit ? devices.slice(0, DASH_CARD_LIMIT) : devices;" in js


@pytest.mark.parametrize("name", ["devices.js", "dashboard.js"])
def test_view_js_parses_with_node(name):
    node = shutil.which("node")
    if not node:
        pytest.skip("node no disponible")
    subprocess.run([node, "--check", str(VIEWS / name)], check=True, capture_output=True)


def _bench_module():
    path = ROOT / "scripts" / "capacity_prune_bench.py"
    spec = importlib.util.spec_from_file_location("capacity_prune_bench", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prune_bench_refuses_to_touch_the_repo_database(tmp_path):
    bench = _bench_module()
    old = os.environ.get("NETPULSE_REMOTE_DB")
    try:
        with pytest.raises(SystemExit):
            bench._set_db(ROOT / "runtime" / "bench-should-not-exist.db")
        bench._set_db(tmp_path / "bench.db")
        assert os.environ["NETPULSE_REMOTE_DB"].endswith("bench.db")
    finally:
        if old is None:
            os.environ.pop("NETPULSE_REMOTE_DB", None)
        else:
            os.environ["NETPULSE_REMOTE_DB"] = old


def test_prune_bench_help_documents_the_real_function():
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "capacity_prune_bench.py"), "--help"],
        capture_output=True, text=True, cwd=ROOT, timeout=120,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    assert out.returncode == 0, out.stderr
    assert "DB temporal" in out.stdout
    assert "--devices" in out.stdout
    assert "--retention-days" in out.stdout
