"""M-macro2 zone-edge tests (no Docker, no live LLM)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.models.event_graph import ZonaEdgeDecision
from app.pipeline.event_graph.zona_edges import (
    SOGLIA_CONFIDENZA,
    ArcoZona,
    collega_zone,
)
from app.pipeline.event_graph.zona_segmentation import Zona

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "event_graph"
EDGES_PATH = PACKAGE_DIR / "zona_edges.py"
MODELS_PATH = Path(__file__).resolve().parents[1] / "app" / "models" / "event_graph.py"


def _zona(
    testo: str,
    ordinale: int,
    *,
    riassunto: str = "",
    entita_principali: list[str] | None = None,
    **overrides,
) -> Zona:
    payload = {
        "id": f"z-{ordinale}",
        "documento": "doc-macro2",
        "offset_inizio": ordinale * 100,
        "offset_fine": ordinale * 100 + len(testo),
        "ordinale": ordinale,
        "testo": testo,
        "riassunto": riassunto,
        "entita_principali": list(entita_principali or []),
    }
    payload.update(overrides)
    return Zona(**payload)


def _decision(**overrides) -> ZonaEdgeDecision:
    payload = {
        "relazione_segnale": "causa_esplicita",
        "orientamento": "coordinata",
        "confidenza": 0.91,
    }
    payload.update(overrides)
    return ZonaEdgeDecision(**payload)


def _install_stub(monkeypatch, handler):
    async def stub(
        system_prompt,
        user_prompt,
        response_model,
        temperature=0,
        job_id=None,
    ):
        return await handler(
            system_prompt, user_prompt, response_model, temperature, job_id
        )

    monkeypatch.setattr(
        "app.pipeline.event_graph.zona_edges.call_structured", stub
    )


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


@pytest.mark.asyncio
async def test_adjacent_causa_esplicita_is_macro_unverified(monkeypatch):
    calls: list[object] = []

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        calls.append((response_model, user_prompt, temperature))
        assert temperature == 0
        assert response_model is ZonaEdgeDecision
        assert "Mario arrivò in città" in user_prompt
        assert "Anna partì" in user_prompt
        return _decision(relazione_segnale="causa_esplicita", confidenza=0.91)

    _install_stub(monkeypatch, handler)
    zone = [
        _zona(
            "Mario arrivò.",
            0,
            riassunto="Mario arrivò in città.",
        ),
        _zona(
            "Quindi Anna partì.",
            1,
            riassunto="Anna partì dalla stazione.",
        ),
    ]
    archi = await collega_zone(zone)
    assert len(calls) == 1
    assert len(archi) == 1
    arco = archi[0]
    assert isinstance(arco, ArcoZona)
    assert arco.tipo == "CAUSA"
    assert arco.da_id == "z-0"
    assert arco.a_id == "z-1"
    assert arco.livello == "macro"
    assert arco.verificato is None
    assert arco.confidenza == 0.91
    assert arco.segnale == "Quindi"
    assert arco.su_via_principale == []


@pytest.mark.asyncio
async def test_adjacent_invented_causa_without_connective_is_sequenza(monkeypatch):
    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        return _decision(relazione_segnale="causa_esplicita", confidenza=0.91)

    _install_stub(monkeypatch, handler)
    zone = [
        _zona("Il Vento soffiò.", 0, riassunto="Il Vento soffiò con forza."),
        _zona("Il Sole scalda.", 1, riassunto="Il Sole scalda l'aria."),
    ]
    archi = await collega_zone(zone)
    assert len(archi) == 1
    assert archi[0].tipo == "SEQUENZA"
    assert archi[0].tipo != "CAUSA"


@pytest.mark.asyncio
async def test_adjacent_low_confidence_emits_collegato_not_causa(monkeypatch):
    assert 0.2 < SOGLIA_CONFIDENZA

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        assert "No explicit connective" in user_prompt or "Assente" in user_prompt
        return _decision(relazione_segnale="causa_esplicita", confidenza=0.2)

    _install_stub(monkeypatch, handler)
    zone = [
        _zona("Mario arrivò.", 0, riassunto="Mario arrivò."),
        _zona("Anna partì.", 1, riassunto="Anna partì."),
    ]
    archi = await collega_zone(zone)
    assert len(archi) == 1
    arco = archi[0]
    assert arco.tipo == "COLLEGATO"
    assert arco.segnale == "implicito"
    assert arco.confidenza == 0.2
    assert arco.livello == "macro"
    assert arco.verificato is None


@pytest.mark.asyncio
async def test_nonadjacent_disjoint_entities_skips_llm(monkeypatch):
    prompts: list[str] = []

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        prompts.append(user_prompt)
        return _decision(relazione_segnale="nessuno", confidenza=0.7)

    _install_stub(monkeypatch, handler)
    zone = [
        _zona(
            "Mario arrivò.",
            0,
            riassunto="Mario arrivò.",
            entita_principali=["Mario"],
        ),
        _zona(
            "Pioveva forte.",
            1,
            riassunto="Pioveva forte.",
            entita_principali=["pioggia"],
        ),
        _zona(
            "Paolo dormì.",
            2,
            riassunto="Paolo dormì.",
            entita_principali=["Paolo"],
        ),
    ]
    archi = await collega_zone(zone)
    assert len(prompts) == 2
    assert not any("Mario arrivò." in p and "Paolo dormì." in p for p in prompts)
    assert all(arco.livello == "macro" for arco in archi)
    assert all(arco.verificato is None for arco in archi)


@pytest.mark.asyncio
async def test_nonadjacent_shared_entity_calls_classifier(monkeypatch):
    prompts: list[str] = []

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        prompts.append(user_prompt)
        return _decision(relazione_segnale="posteriorita", confidenza=0.8)

    _install_stub(monkeypatch, handler)
    zone = [
        _zona(
            "Marco arrivò.",
            0,
            riassunto="Marco arrivò in stazione.",
            entita_principali=["Marco"],
        ),
        _zona(
            "Pioveva.",
            1,
            riassunto="Pioveva tutta la sera.",
            entita_principali=["pioggia"],
        ),
        _zona(
            "Marco partì.",
            2,
            riassunto="Marco partì all'alba.",
            entita_principali=["Marco"],
        ),
    ]
    archi = await collega_zone(zone)
    assert len(prompts) == 3
    nonadj = [p for p in prompts if "Marco arrivò in stazione." in p and "Marco partì" in p]
    assert len(nonadj) == 1
    assert any(arco.da_id == "z-0" and arco.a_id == "z-2" for arco in archi)
    typed = [arco for arco in archi if {arco.da_id, arco.a_id} == {"z-0", "z-2"}]
    assert len(typed) == 1
    assert typed[0].tipo == "PRECEDE"
    assert typed[0].livello == "macro"
    assert typed[0].verificato is None


@pytest.mark.asyncio
async def test_llm_error_returns_list_without_raising(monkeypatch):
    async def handler(*args, **kwargs):
        raise RuntimeError("llm down")

    _install_stub(monkeypatch, handler)
    zone = [
        _zona("Mario arrivò.", 0, riassunto="Mario arrivò."),
        _zona("Quindi Anna partì.", 1, riassunto="Anna partì."),
    ]
    archi = await collega_zone(zone)
    assert archi == []


@pytest.mark.asyncio
async def test_llm_error_on_one_pair_keeps_others(monkeypatch):
    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        if "Anna" in user_prompt:
            raise RuntimeError("llm down")
        return _decision(relazione_segnale="contrasto", confidenza=0.7)

    _install_stub(monkeypatch, handler)
    zone = [
        _zona("Prima zona.", 0, riassunto="Prima zona senza Anna."),
        _zona("Seconda zona.", 1, riassunto="Anna compare qui."),
        _zona("Terza zona.", 2, riassunto="Terza zona senza nome."),
    ]
    archi = await collega_zone(zone)
    assert all(isinstance(arco, ArcoZona) for arco in archi)
    assert all(arco.verificato is None for arco in archi)


def test_zona_edges_isolation_ast():
    source = EDGES_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(EDGES_PATH))
    modules = _import_modules(tree)
    violations = [module for module in modules if _is_forbidden_import(module)]
    assert violations == []
    assert any(
        module == "app.pipeline.event_graph.infra.llm"
        or module.startswith("app.pipeline.event_graph.infra.llm.")
        for module in modules
    )
    assert any(
        module == "app.pipeline.event_graph.event_edges"
        or module.startswith("app.pipeline.event_graph.event_edges.")
        for module in modules
    )
    assert all("app.core" not in module for module in modules)
    assert "openai" not in modules

    models_src = MODELS_PATH.read_text(encoding="utf-8")
    models_tree = ast.parse(models_src, filename=str(MODELS_PATH))
    model_modules = _import_modules(models_tree)
    assert all(not _is_forbidden_import(module) for module in model_modules)
    assert "class ZonaEdgeDecision" in models_src
    assert "class ZonaSummary" in models_src
