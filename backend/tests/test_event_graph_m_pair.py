"""M-pair adjacent sentence-head linking (no Docker, no live LLM)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.models.event_graph import (
    ArcoEvento,
    EventoRisolto,
    PairEdgeDecision,
    SottoGrafo,
)
from app.pipeline.event_graph.chunking_periods import UnitaTesto
from app.pipeline.event_graph.dedup import DedupResult
from app.pipeline.event_graph.sentence_pair_linking import (
    SOGLIA_CONFIDENZA,
    collega_adiacenti,
)
from app.pipeline.event_graph.sentence_pair_linking import (
    testa_della_unita as head_of_unit,
)

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "event_graph"
PAIR_PATH = PACKAGE_DIR / "sentence_pair_linking.py"
MODELS_PATH = Path(__file__).resolve().parents[1] / "app" / "models" / "event_graph.py"


def _unita(
    testo: str,
    indice: int,
    offset_inizio: int,
    *,
    tipo: str = "narrativa",
    connettivo_confine: str | None = None,
) -> UnitaTesto:
    return UnitaTesto(
        testo=testo,
        offset_inizio=offset_inizio,
        offset_fine=offset_inizio + len(testo),
        tipo=tipo,  # type: ignore[arg-type]
        connettivo_confine=connettivo_confine,
        zona_id="z-pair",
        indice=indice,
    )


def _evento(
    event_id: str,
    offset_inizio: int,
    offset_fine: int,
    *,
    e_testa: bool = True,
    lemma: str = "arrivare",
    posizione_doc: int | None = None,
) -> EventoRisolto:
    return EventoRisolto(
        id=event_id,
        lemma=lemma,
        e_testa=e_testa,
        offset_inizio=offset_inizio,
        offset_fine=offset_fine,
        posizione_doc=posizione_doc,
        segmentazione="principale_finita",
    )


def _decision(**overrides) -> PairEdgeDecision:
    payload = {
        "relazione_segnale": "causa_esplicita",
        "orientamento": "coordinata",
        "confidenza": 0.91,
    }
    payload.update(overrides)
    return PairEdgeDecision(**payload)


def _dedup(
    units: list[UnitaTesto],
    events: list[EventoRisolto],
    *,
    archi: list[ArcoEvento] | None = None,
) -> DedupResult:
    return DedupResult(
        sotto=SottoGrafo(eventi=list(events), archi=list(archi or [])),
        unita=list(units),
        esiti=[],
    )


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
        "app.pipeline.event_graph.sentence_pair_linking.call_structured",
        stub,
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
    lowered = module.lower()
    if "temporal_placement" in lowered:
        return True
    if "allen" in lowered:
        return True
    return False


@pytest.mark.asyncio
async def test_two_narrativa_causa_esplicita_links_heads(monkeypatch):
    u0 = _unita("Mario arrivò.", 0, 0)
    u1 = _unita("Anna partì.", 1, 20)
    e0 = _evento("ev-0", u0.offset_inizio, u0.offset_fine, lemma="arrivare")
    e1 = _evento("ev-1", u1.offset_inizio, u1.offset_fine, lemma="partire")

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        assert temperature == 0
        assert response_model is PairEdgeDecision
        assert "Mario arrivò." in user_prompt
        assert "Anna partì." in user_prompt
        assert "arrivare" not in user_prompt
        assert "partire" not in user_prompt
        return _decision(relazione_segnale="causa_esplicita", confidenza=0.91)

    _install_stub(monkeypatch, handler)
    result = await collega_adiacenti(_dedup([u0, u1], [e0, e1]))
    assert len(result.archi) == 1
    arco = result.archi[0]
    assert arco.tipo == "SEQUENZA"
    assert arco.tipo != "CAUSA"
    assert arco.da_id == "ev-0"
    assert arco.a_id == "ev-1"
    assert arco.props["confidenza"] == 0.91
    assert head_of_unit(u0, result) is e0
    assert head_of_unit(u1, result) is e1


@pytest.mark.asyncio
async def test_perche_connective_keeps_causa(monkeypatch):
    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        return _decision(relazione_segnale="causa_esplicita", confidenza=0.91)

    _install_stub(monkeypatch, handler)
    u0 = _unita("Mario arrivò.", 0, 0)
    u1 = _unita("Perché Anna partì.", 1, 20)
    e0 = _evento("ev-0", u0.offset_inizio, u0.offset_fine, lemma="arrivare")
    e1 = _evento("ev-1", u1.offset_inizio, u1.offset_fine, lemma="partire")
    result = await collega_adiacenti(_dedup([u0, u1], [e0, e1]))
    assert len(result.archi) == 1
    assert result.archi[0].tipo == "CAUSA"
    assert result.archi[0].props.get("segnale")


@pytest.mark.asyncio
async def test_every_boundary_visited_three_units_two_calls(monkeypatch):
    calls: list[str] = []

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        calls.append(user_prompt)
        assert "No explicit connective" in user_prompt or "Assente" in user_prompt
        return _decision(relazione_segnale="nessuno", confidenza=0.7)

    _install_stub(monkeypatch, handler)
    units = [
        _unita("Prima frase.", 0, 0),
        _unita("Seconda frase.", 1, 20),
        _unita("Terza frase.", 2, 40),
    ]
    events = [
        _evento(f"ev-{i}", unit.offset_inizio, unit.offset_fine, posizione_doc=i)
        for i, unit in enumerate(units)
    ]
    await collega_adiacenti(_dedup(units, events))
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_low_confidenza_without_connective_emits_collegato(monkeypatch):
    assert 0.2 < SOGLIA_CONFIDENZA

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        assert "No explicit connective" in user_prompt or "Assente" in user_prompt
        return _decision(relazione_segnale="causa_esplicita", confidenza=0.2)

    _install_stub(monkeypatch, handler)
    u0 = _unita("Mario arrivò.", 0, 0)
    u1 = _unita("Anna partì.", 1, 20)
    e0 = _evento("ev-0", u0.offset_inizio, u0.offset_fine)
    e1 = _evento("ev-1", u1.offset_inizio, u1.offset_fine)
    result = await collega_adiacenti(_dedup([u0, u1], [e0, e1]))
    assert len(result.archi) == 1
    arco = result.archi[0]
    assert arco.tipo == "COLLEGATO"
    assert arco.tipo != "CAUSA"
    assert arco.props["confidenza"] == 0.2
    assert arco.props["segnale"] == "implicito"


@pytest.mark.asyncio
async def test_mentre_connective_is_evidence_not_label(monkeypatch):
    prompts: list[str] = []

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        prompts.append(user_prompt)
        return _decision(relazione_segnale="contrasto", confidenza=0.88)

    _install_stub(monkeypatch, handler)
    u0 = _unita("Mario parlava.", 0, 0)
    u1 = _unita(
        "Mentre pioveva, Anna restò.",
        1,
        20,
        connettivo_confine="mentre",
    )
    e0 = _evento("ev-0", u0.offset_inizio, u0.offset_fine)
    e1 = _evento("ev-1", u1.offset_inizio, u1.offset_fine)
    result = await collega_adiacenti(_dedup([u0, u1], [e0, e1]))
    assert len(prompts) == 1
    prompt = prompts[0]
    assert "mentre" in prompt.lower()
    assert (
        "evidence" in prompt.lower()
        or "evidenza" in prompt.lower()
        or "Disambiguate" in prompt
    )
    assert "No explicit connective" not in prompt
    assert len(result.archi) == 1
    assert result.archi[0].tipo == "CONTRASTO"
    assert result.archi[0].props.get("segnale") == "mentre"


@pytest.mark.asyncio
async def test_dialogo_without_testa_walks_left_to_narrativa(monkeypatch):
    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        return _decision(relazione_segnale="posteriorita", confidenza=0.8)

    _install_stub(monkeypatch, handler)
    u0 = _unita("Mario arrivò.", 0, 0, tipo="narrativa")
    u1 = _unita('"Ciao", disse.', 1, 20, tipo="dialogo")
    u2 = _unita("Anna partì.", 2, 40, tipo="narrativa")
    e0 = _evento("ev-0", u0.offset_inizio, u0.offset_fine, posizione_doc=0)
    e2 = _evento("ev-2", u2.offset_inizio, u2.offset_fine, posizione_doc=2)
    result = await collega_adiacenti(_dedup([u0, u1, u2], [e0, e2]))
    assert head_of_unit(u1, result) is None
    assert len(result.archi) == 1
    arco = result.archi[0]
    assert arco.da_id == "ev-0"
    assert arco.a_id == "ev-2"


@pytest.mark.asyncio
async def test_llm_failure_skips_pair_without_raising(monkeypatch):
    async def handler(*args, **kwargs):
        raise RuntimeError("llm down")

    _install_stub(monkeypatch, handler)
    u0 = _unita("Mario arrivò.", 0, 0)
    u1 = _unita("Anna partì.", 1, 20)
    e0 = _evento("ev-0", u0.offset_inizio, u0.offset_fine)
    e1 = _evento("ev-1", u1.offset_inizio, u1.offset_fine)
    result = await collega_adiacenti(_dedup([u0, u1], [e0, e1]))
    assert result.archi == []


def test_isolation_ast():
    source = PAIR_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(PAIR_PATH))
    modules = _import_modules(tree)
    violations = [module for module in modules if _is_forbidden_import(module)]
    assert violations == []
    assert all("temporal_placement" not in module for module in modules)
    assert all("allen" not in module.lower() for module in modules)
    assert all("app.core" not in module for module in modules)
    assert "openai" not in modules
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
    assert any(
        module == "app.pipeline.event_graph.dedup"
        or module.startswith("app.pipeline.event_graph.dedup.")
        for module in modules
    )
    assert "temporal_placement" not in source
    assert "seleziona_candidati" not in source

    models_src = MODELS_PATH.read_text(encoding="utf-8")
    models_tree = ast.parse(models_src, filename=str(MODELS_PATH))
    model_modules = _import_modules(models_tree)
    assert all(not _is_forbidden_import(module) for module in model_modules)
    assert "class PairEdgeDecision" in models_src
