"""M-flash: MACRO+MICRO live pipeline, FLASH_MODE, /zone endpoints."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.models.event_graph import EventoRisolto, SottoGrafo
from app.pipeline.event_graph.config import EventGraphSettings
from app.pipeline.event_graph.dedup import DedupResult
from app.pipeline.event_graph.pipeline import (
    espandi_zona,
    zone_da_espandere_subito,
)
from app.pipeline.event_graph.via_principale import ViaPrincipale
from app.pipeline.event_graph.zona_edges import ArcoZona
from app.pipeline.event_graph.zona_segmentation import Zona

BACKEND = Path(__file__).resolve().parents[1]
PACKAGE_DIR = BACKEND / "app" / "pipeline" / "event_graph"
PIPELINE_PATH = PACKAGE_DIR / "pipeline.py"
API_PATH = BACKEND / "app" / "api" / "event_graph.py"
CONFIG_PATH = PACKAGE_DIR / "config.py"
PERSIST_PATH = PACKAGE_DIR / "persistence.py"


def _zona(ordinale: int, **overrides) -> Zona:
    payload = {
        "id": f"z-{ordinale}",
        "documento": "doc-flash",
        "offset_inizio": ordinale * 100,
        "offset_fine": ordinale * 100 + 80,
        "ordinale": ordinale,
        "testo": f"zona {ordinale} testo",
        "riassunto": f"riassunto {ordinale}",
        "espansa": False,
    }
    payload.update(overrides)
    return Zona(**payload)


def _via(nome: str, zona_ids: list[str]) -> ViaPrincipale:
    return ViaPrincipale(nome=nome, zona_ids=list(zona_ids), arco_keys=[])  # type: ignore[arg-type]


def _import_modules(tree: ast.AST) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            base = node.module or ""
            modules.append(base)
            for alias in node.names:
                if alias.name == "*":
                    continue
                modules.append(f"{base}.{alias.name}" if base else alias.name)
    return modules


def _is_forbidden_import(module: str) -> bool:
    if module == "app.core" or module.startswith("app.core."):
        return True
    if module == "app.pipeline":
        return True
    if module.startswith("app.pipeline.") and not module.startswith(
        "app.pipeline.event_graph"
    ):
        return True
    return False


def _iter_routes(routes) -> list:
    found: list = []
    for route in routes:
        found.append(route)
        nested = getattr(route, "routes", None)
        original = getattr(route, "original_router", None)
        if original is not None:
            nested = getattr(original, "routes", nested)
        if nested:
            found.extend(_iter_routes(nested))
    return found


def _route_entries() -> list[tuple[str, set[str]]]:
    from app.main import app

    entries: list[tuple[str, set[str]]] = []
    for route in _iter_routes(app.routes):
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if path:
            entries.append((path, set(methods or [])))
    return entries


def test_flash_mode_default_false():
    fields = EventGraphSettings.model_fields
    assert "EVENT_GRAPH_FLASH_MODE" in fields
    assert fields["EVENT_GRAPH_FLASH_MODE"].default is False


def test_zone_da_espandere_subito_flash_union_of_two_of_three():
    zone = [_zona(0), _zona(1), _zona(2)]
    vie = {
        "conseguenze": _via("conseguenze", ["z-0", "z-1"]),
        "protagonista": _via("protagonista", ["z-1"]),
        "cronologia": _via("cronologia", ["z-0"]),
    }
    chosen = zone_da_espandere_subito(zone, vie, True)
    assert [item.id for item in chosen] == ["z-0", "z-1"]


def test_zone_da_espandere_subito_flash_false_all_zones():
    zone = [_zona(0), _zona(1), _zona(2)]
    vie = {
        "conseguenze": _via("conseguenze", ["z-0"]),
        "protagonista": _via("protagonista", []),
        "cronologia": _via("cronologia", ["z-0"]),
    }
    chosen = zone_da_espandere_subito(zone, vie, False)
    assert [item.id for item in chosen] == ["z-0", "z-1", "z-2"]


@pytest.mark.asyncio
async def test_espandi_zona_stage_order(monkeypatch):
    order: list[str] = []
    zona = _zona(0)
    zone = [zona, _zona(1, espansa=True)]
    archi = [ArcoZona(da_id="z-0", a_id="z-1", tipo="CAUSA", livello="macro")]
    sotto_doc = SottoGrafo()

    async def spy_dedup(zona_arg, **kwargs):
        order.append("dedup")
        return DedupResult(sotto=SottoGrafo(), unita=[])

    async def spy_pair(dedup, **kwargs):
        order.append("pair")
        return dedup.sotto

    def spy_chiusura(sotto, unita=None):
        order.append("chiusura")
        return sotto

    def spy_ponte(zona_id, zone_arg, archi_macro, sotto):
        order.append("ponte")
        return archi_macro

    monkeypatch.setattr(
        "app.pipeline.event_graph.pipeline.espandi_zona_fino_dedup",
        spy_dedup,
    )
    monkeypatch.setattr(
        "app.pipeline.event_graph.pipeline.collega_inter_frase",
        spy_pair,
    )
    monkeypatch.setattr(
        "app.pipeline.event_graph.pipeline.chiusura_temporale",
        spy_chiusura,
    )
    monkeypatch.setattr(
        "app.pipeline.event_graph.pipeline.verifica_dopo_espansione",
        spy_ponte,
    )

    result = await espandi_zona(
        zona,
        document_text="zona 0 testo",
        zone=zone,
        archi_macro=archi,
        sotto_doc=sotto_doc,
        session=None,
    )
    assert order == ["dedup", "pair", "chiusura", "ponte"]
    assert result.zona.espansa is True
    assert zona.espansa is True


def test_get_zone_and_zone_id_registered():
    entries = _route_entries()
    paths = {path for path, _ in entries}
    assert "/event-graph/zone" in paths
    assert "/event-graph/zone/{id}" in paths
    get_list = [methods for path, methods in entries if path == "/event-graph/zone"]
    get_one = [
        methods for path, methods in entries if path == "/event-graph/zone/{id}"
    ]
    assert any("GET" in methods for methods in get_list)
    assert any("GET" in methods for methods in get_one)


def test_post_zone_espandi_registered():
    entries = _route_entries()
    matches = [
        methods
        for path, methods in entries
        if path == "/event-graph/zone/{id}/espandi"
    ]
    assert matches
    assert any("POST" in methods for methods in matches)


def test_pipeline_does_not_import_narrative_plane_or_backbone():
    source = PIPELINE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(PIPELINE_PATH))
    modules = _import_modules(tree)
    assert all("narrative_plane" not in module for module in modules)
    assert all("backbone" not in module for module in modules)
    assert "narrative_plane" not in source
    assert "backbone" not in source


def test_isolation_ast():
    violations: list[str] = []
    for path in (PIPELINE_PATH, API_PATH, CONFIG_PATH, PERSIST_PATH):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for module in _import_modules(tree):
            if _is_forbidden_import(module):
                violations.append(f"{path}: {module}")
        assert "app.core" not in source
    assert violations == []


@pytest.mark.asyncio
async def test_espandi_zona_merges_events_into_sotto_doc(monkeypatch):
    zona = _zona(0)
    event = EventoRisolto(id="e-0", lemma="arrivare", e_testa=True)
    local = SottoGrafo()
    local.aggiungi(eventi=[event])

    async def spy_dedup(zona_arg, **kwargs):
        return DedupResult(sotto=local, unita=[])

    async def spy_pair(dedup, **kwargs):
        return dedup.sotto

    def spy_chiusura(sotto, unita=None):
        return sotto

    def spy_ponte(*args, **kwargs):
        return args[2] if len(args) > 2 else []

    monkeypatch.setattr(
        "app.pipeline.event_graph.pipeline.espandi_zona_fino_dedup",
        spy_dedup,
    )
    monkeypatch.setattr(
        "app.pipeline.event_graph.pipeline.collega_inter_frase",
        spy_pair,
    )
    monkeypatch.setattr(
        "app.pipeline.event_graph.pipeline.chiusura_temporale",
        spy_chiusura,
    )
    monkeypatch.setattr(
        "app.pipeline.event_graph.pipeline.verifica_dopo_espansione",
        spy_ponte,
    )

    sotto_doc = SottoGrafo()
    result = await espandi_zona(
        zona,
        zone=[zona],
        archi_macro=[],
        sotto_doc=sotto_doc,
        session=None,
    )
    assert [item.id for item in sotto_doc.eventi] == ["e-0"]
    assert [item.id for item in result.sotto.eventi] == ["e-0"]
