"""MT10 / Parte C — document-level free relations (no Docker, no live LLM)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.models.event_graph import (
    ArcoEvento,
    EventoRisolto,
    LivelloRelazioniResult,
    RelazioneLibera,
)
from app.pipeline.event_graph.livello_temporale import LIVELLO_MAX_EVENTI_PER_CHIAMATA
from app.pipeline.event_graph.livello_relazioni import (
    INTESTAZIONE_EVENTI,
    INTESTAZIONE_TABELLA,
    INTESTAZIONE_TESTO,
    INTESTAZIONE_TIPI,
    estrai_livello_relazioni,
    relazioni_causa_ciclo,
    user_livello_relazioni,
)

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "event_graph"
LIVELLO_PATH = PACKAGE_DIR / "livello_relazioni.py"


def _evento(
    i: int,
    *,
    span: str | None = None,
    lemma: str = "x",
    chunk_id: str | None = None,
) -> EventoRisolto:
    return EventoRisolto(
        id=f"e-{i}",
        lemma=lemma,
        span=span if span is not None else f"evento {i}",
        posizione_doc=i,
        chunk_id=chunk_id,
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
        "app.pipeline.event_graph.livello_relazioni.call_structured",
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
    return False


@pytest.mark.asyncio
async def test_c_u1_causa_cross_zone(monkeypatch):
    eventi = [
        _evento(0, span="il vento spezzò il ramo", chunk_id="zona-a"),
        _evento(1, span="il ramo cadde", chunk_id="zona-b"),
    ]
    assert eventi[0].chunk_id != eventi[1].chunk_id

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        assert temperature == 0
        assert response_model is LivelloRelazioniResult
        assert "non ricevi l'ordine di esposizione" in system_prompt.casefold()
        assert "numerati" not in system_prompt.casefold()
        assert "anche fra eventi consecutivi" not in system_prompt.casefold()
        assert "il vento spezzò il ramo" in user_prompt
        assert INTESTAZIONE_TESTO in user_prompt
        assert INTESTAZIONE_EVENTI in user_prompt
        assert INTESTAZIONE_TIPI in user_prompt
        assert user_prompt.index(INTESTAZIONE_TESTO) < user_prompt.index(
            INTESTAZIONE_EVENTI
        )
        assert user_prompt.index(INTESTAZIONE_EVENTI) < user_prompt.index(
            INTESTAZIONE_TIPI
        )
        assert user_prompt.index(INTESTAZIONE_TIPI) < user_prompt.index(
            INTESTAZIONE_TABELLA
        )
        assert "- e-0 |" in user_prompt
        assert "- e-1 |" in user_prompt
        assert "1. e-0" not in user_prompt
        assert "CAUSA" in user_prompt
        return LivelloRelazioniResult(
            relazioni=[
                RelazioneLibera(
                    da_id="e-0",
                    a_id="e-1",
                    tipo="CAUSA",
                    spiegazione="il vento spezza il ramo e quindi cade",
                )
            ]
        )

    _install_stub(monkeypatch, handler)
    result = await estrai_livello_relazioni(
        eventi,
        job_id="job-r",
        testo="il vento spezzò il ramo; il ramo cadde",
    )
    assert result is not None
    assert len(result.relazioni) == 1
    rel = result.relazioni[0]
    assert rel.da_id == "e-0"
    assert rel.a_id == "e-1"
    assert rel.tipo == "CAUSA"


@pytest.mark.asyncio
async def test_c_u2_unknown_tipo_discarded(monkeypatch):
    eventi = [_evento(0), _evento(1)]

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        return {
            "relazioni": [
                {
                    "da_id": "e-0",
                    "a_id": "e-1",
                    "tipo": "SEQUENZA",
                    "spiegazione": "ordine",
                },
                {
                    "da_id": "e-1",
                    "a_id": "e-0",
                    "tipo": "FOO",
                    "spiegazione": "inventato",
                },
                {
                    "da_id": "e-0",
                    "a_id": "e-1",
                    "tipo": "CONTRASTO",
                    "spiegazione": "si oppongono",
                },
            ]
        }

    _install_stub(monkeypatch, handler)
    result = await estrai_livello_relazioni(eventi)
    assert result is not None
    tipi = [rel.tipo for rel in result.relazioni]
    assert "SEQUENZA" not in tipi
    assert "FOO" not in tipi
    assert "CONTRASTO" in tipi
    assert len(result.relazioni) == 1


@pytest.mark.asyncio
async def test_c_u3_causa_cycle_keeps_first_drops_second(monkeypatch):
    eventi = [_evento(0), _evento(1)]

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        return LivelloRelazioniResult(
            relazioni=[
                RelazioneLibera(
                    da_id="e-0",
                    a_id="e-1",
                    tipo="CAUSA",
                    spiegazione="A provoca B",
                ),
                RelazioneLibera(
                    da_id="e-1",
                    a_id="e-0",
                    tipo="CAUSA",
                    spiegazione="B provocherebbe A",
                ),
            ]
        )

    _install_stub(monkeypatch, handler)
    result = await estrai_livello_relazioni(eventi)
    assert result is not None
    causa = [(r.da_id, r.a_id) for r in result.relazioni if r.tipo == "CAUSA"]
    assert causa == [("e-0", "e-1")]
    dropped = relazioni_causa_ciclo()
    assert ("e-1", "e-0", "CAUSA") in dropped


@pytest.mark.asyncio
async def test_c_u4_invented_ids_dropped(monkeypatch):
    eventi = [_evento(0), _evento(1)]

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        return LivelloRelazioniResult(
            relazioni=[
                RelazioneLibera(
                    da_id="ghost-99",
                    a_id="e-1",
                    tipo="CAUSA",
                    spiegazione="fantasma",
                ),
                RelazioneLibera(
                    da_id="e-0",
                    a_id="inesistente",
                    tipo="SCOPO",
                    spiegazione="fantasma",
                ),
                RelazioneLibera(
                    da_id="e-0",
                    a_id="e-1",
                    tipo="LIMITE",
                    spiegazione="valido",
                ),
            ]
        )

    _install_stub(monkeypatch, handler)
    result = await estrai_livello_relazioni(eventi)
    assert result is not None
    pairs = [(r.da_id, r.a_id, r.tipo) for r in result.relazioni]
    assert ("e-0", "e-1", "LIMITE") in pairs
    assert all(r.da_id != "ghost-99" and r.a_id != "inesistente" for r in result.relazioni)


@pytest.mark.asyncio
async def test_empty_eventi_returns_empty_result_not_none(monkeypatch):
    called = False

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        nonlocal called
        called = True
        raise RuntimeError("should not be called")

    _install_stub(monkeypatch, handler)
    result = await estrai_livello_relazioni([])
    assert result is not None
    assert result.relazioni == []
    assert called is False


@pytest.mark.asyncio
async def test_all_windows_fail_returns_none(monkeypatch):
    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        raise RuntimeError("llm down")

    _install_stub(monkeypatch, handler)
    result = await estrai_livello_relazioni([_evento(0)])
    assert result is None


@pytest.mark.asyncio
async def test_windows_61_events_two_plus_calls(monkeypatch):
    assert LIVELLO_MAX_EVENTI_PER_CHIAMATA == 60
    eventi = [_evento(i) for i in range(61)]
    calls: list[str] = []

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        calls.append(user_prompt)
        n = len(calls)
        if n == 1:
            da, a = "e-0", "e-1"
        else:
            da, a = "e-60", "e-0"
        return LivelloRelazioniResult(
            relazioni=[
                RelazioneLibera(
                    da_id=da,
                    a_id=a,
                    tipo="CONTRASTO",
                    spiegazione=f"finestra {n}",
                )
            ]
        )

    _install_stub(monkeypatch, handler)
    result = await estrai_livello_relazioni(eventi, testo="storia di sessantuno eventi")
    assert len(calls) >= 2
    assert INTESTAZIONE_TESTO in calls[0]
    assert INTESTAZIONE_EVENTI in calls[0]
    assert INTESTAZIONE_TIPI in calls[0]
    assert "storia di sessantuno eventi" in calls[0]
    assert "storia di sessantuno eventi" in calls[1]
    assert "- e-0 |" in calls[0]
    assert "- e-59 |" in calls[0]
    assert "- e-60 |" not in calls[0]
    assert "- e-60 |" in calls[1]
    assert "1. e-" not in calls[0]
    assert "ORDINE DI ESPOSIZIONE" not in calls[0]
    assert result is not None
    pairs = {(r.da_id, r.a_id, r.tipo) for r in result.relazioni}
    assert ("e-0", "e-1", "CONTRASTO") in pairs
    assert ("e-60", "e-0", "CONTRASTO") in pairs


@pytest.mark.asyncio
async def test_archi_causa_esistenti_block_cycle(monkeypatch):
    eventi = [_evento(0), _evento(1)]

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        return LivelloRelazioniResult(
            relazioni=[
                RelazioneLibera(
                    da_id="e-1",
                    a_id="e-0",
                    tipo="CAUSA",
                    spiegazione="chiuderebbe il ciclo",
                )
            ]
        )

    _install_stub(monkeypatch, handler)
    existing = [ArcoEvento(tipo="CAUSA", da_id="e-0", a_id="e-1")]
    result = await estrai_livello_relazioni(
        eventi, archi_causa_esistenti=existing
    )
    assert result is not None
    assert result.relazioni == []
    assert ("e-1", "e-0", "CAUSA") in relazioni_causa_ciclo()


def test_user_prompt_is_text_events_types_not_exposition():
    late = EventoRisolto(id="e-late", lemma="partì", span="partì", posizione_doc=2)
    early = EventoRisolto(
        id="e-early", lemma="arrivò", ancora="nel bosco", span="", posizione_doc=0
    )
    prompt = user_livello_relazioni(
        [late, early], testo="arrivò nel bosco e poi partì"
    )
    assert "arrivò nel bosco e poi partì" in prompt
    assert prompt.index(INTESTAZIONE_TESTO) < prompt.index(INTESTAZIONE_EVENTI)
    assert prompt.index(INTESTAZIONE_EVENTI) < prompt.index(INTESTAZIONE_TIPI)
    assert prompt.index(INTESTAZIONE_TIPI) < prompt.index(INTESTAZIONE_TABELLA)
    assert "- e-early |" in prompt
    assert "- e-late |" in prompt
    assert "1. e-early" not in prompt
    assert "ORDINE DI ESPOSIZIONE" not in prompt
    assert "arrivò|nel bosco" in prompt
    assert "CAUSA" in prompt
    assert "ghost" not in prompt


def test_user_prompt_ignores_sequenza_arcs_and_does_not_number_events():
    first = _evento(0, span="compare prima in lista")
    second = _evento(5, span="compare dopo in lista")
    prompt = user_livello_relazioni([second, first], tutti=None)
    assert "- e-0 |" in prompt
    assert "- e-5 |" in prompt
    assert "1. e-0" not in prompt
    assert "1. e-5" not in prompt
    assert "archi sequenza" not in prompt.casefold()
    assert "ORDINE DI ESPOSIZIONE" not in prompt


def test_user_prompt_skips_fused_and_does_not_number_events():
    fused = _evento(1, span="duplicato fuso")
    fused.fuso_in = "e-0"
    prompt = user_livello_relazioni(
        [_evento(2, span="dopo"), fused, _evento(0, span="prima")]
    )
    assert "e-1 |" not in prompt
    assert "- e-0 |" in prompt
    assert "- e-2 |" in prompt
    assert "1. e-0" not in prompt
    assert "ORDINE DI ESPOSIZIONE" not in prompt


def test_window_prompt_lists_only_window_events():
    tutti = [_evento(i) for i in range(3)]
    prompt = user_livello_relazioni(tutti[:2], tutti=tutti, testo="abc")
    assert "abc" in prompt
    assert "- e-0 |" in prompt
    assert "- e-1 |" in prompt
    assert "e-2 |" not in prompt
    assert "1. e-0" not in prompt
    assert "ORDINE DI ESPOSIZIONE" not in prompt


def test_livello_relazioni_isolation_ast():
    source = LIVELLO_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(LIVELLO_PATH))
    modules = _import_modules(tree)
    violations = [module for module in modules if _is_forbidden_import(module)]
    assert violations == []
    assert any(
        module == "app.pipeline.event_graph.infra.llm"
        or module.startswith("app.pipeline.event_graph.infra.llm.")
        for module in modules
    )
    assert all("app.core" not in module for module in modules)
    assert "openai" not in modules
    assert "sentence_pair_linking" not in source
    assert "ORDINE DI ESPOSIZIONE" not in source
