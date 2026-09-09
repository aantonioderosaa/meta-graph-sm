"""M-nonadj shared-entity candidates + MICRO stage 4 (no Docker, no live LLM)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.models.event_graph import (
    ArgomentoRisolto,
    ArcoEvento,
    EventoRisolto,
    PairEdgeDecision,
    SottoGrafo,
)
from app.pipeline.event_graph.candidati_entita import (
    condividono_entita,
    mention_ids,
    seleziona_per_entita,
)
from app.pipeline.event_graph.chunking_periods import UnitaTesto
from app.pipeline.event_graph.dedup import DedupResult
from app.pipeline.event_graph.sentence_pair_linking import (
    SOGLIA_CONFIDENZA,
    collega_non_adiacenti,
)
from app.pipeline.event_graph.temporal_placement import seleziona_candidati

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "event_graph"
CANDIDATI_PATH = PACKAGE_DIR / "candidati_entita.py"
PAIR_PATH = PACKAGE_DIR / "sentence_pair_linking.py"
MODELS_PATH = Path(__file__).resolve().parents[1] / "app" / "models" / "event_graph.py"


def _unita(
    testo: str,
    indice: int,
    offset_inizio: int,
    *,
    tipo: str = "narrativa",
) -> UnitaTesto:
    return UnitaTesto(
        testo=testo,
        offset_inizio=offset_inizio,
        offset_fine=offset_inizio + len(testo),
        tipo=tipo,  # type: ignore[arg-type]
        connettivo_confine=None,
        zona_id="z-nonadj",
        indice=indice,
    )


def _sogg(menzione_id: str) -> ArgomentoRisolto:
    return ArgomentoRisolto(ruolo="SOGG", menzione_id=menzione_id)


def _evento(
    event_id: str,
    offset_inizio: int,
    offset_fine: int,
    *,
    e_testa: bool = True,
    lemma: str = "arrivare",
    posizione_doc: int | None = None,
    menzione_sogg: str = "m-mario",
    argomenti: list[ArgomentoRisolto] | None = None,
    tempo_assoluto: str | dict | None = None,
    span: str | None = None,
    fuso_in: str | None = None,
) -> EventoRisolto:
    if argomenti is None:
        argomenti = [_sogg(menzione_sogg)]
    return EventoRisolto(
        id=event_id,
        lemma=lemma,
        e_testa=e_testa,
        offset_inizio=offset_inizio,
        offset_fine=offset_fine,
        posizione_doc=posizione_doc,
        fuso_in=fuso_in,
        span=span,
        tempo_assoluto=tempo_assoluto,
        segmentazione="principale_finita",
        argomenti=argomenti,
    )


def _decision(**overrides) -> PairEdgeDecision:
    payload = {
        "relazione_segnale": "posteriorita",
        "orientamento": "coordinata",
        "confidenza": 0.88,
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
    if "allen" in lowered:
        return True
    if "ponte" in lowered:
        return True
    if lowered.endswith(".persistence") or "persistence" in lowered:
        return True
    return False


@pytest.mark.asyncio
async def test_shared_sogg_nonadjacent_classifies_once(monkeypatch):
    calls: list[str] = []

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        calls.append(user_prompt)
        assert temperature == 0
        assert response_model is PairEdgeDecision
        assert "Mario arrivò." in user_prompt
        assert "Mario partì." in user_prompt
        assert "arrivare" not in user_prompt
        assert "partire" not in user_prompt
        return _decision(relazione_segnale="posteriorita", confidenza=0.88)

    _install_stub(monkeypatch, handler)
    units = [
        _unita("Mario arrivò.", 0, 0),
        _unita("Pioveva.", 1, 20),
        _unita("Mario partì.", 2, 40),
    ]
    events = [
        _evento("ev-0", units[0].offset_inizio, units[0].offset_fine, lemma="arrivare"),
        _evento(
            "ev-1",
            units[1].offset_inizio,
            units[1].offset_fine,
            lemma="piovere",
            menzione_sogg="m-pioggia",
            posizione_doc=1,
        ),
        _evento(
            "ev-2",
            units[2].offset_inizio,
            units[2].offset_fine,
            lemma="partire",
            posizione_doc=2,
        ),
    ]
    result = await collega_non_adiacenti(_dedup(units, events))
    assert len(calls) == 1
    typed = [arco for arco in result.archi if {arco.da_id, arco.a_id} == {"ev-0", "ev-2"}]
    assert len(typed) == 1
    assert typed[0].tipo == "PRECEDE"
    assert typed[0].da_id == "ev-0"
    assert typed[0].a_id == "ev-2"
    assert typed[0].props["confidenza"] == 0.88
    assert typed[0].props["livello"] == "micro"


@pytest.mark.asyncio
async def test_adjacent_pair_not_classified_by_stage4(monkeypatch):
    calls: list[str] = []

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        calls.append(user_prompt)
        return _decision()

    _install_stub(monkeypatch, handler)
    u0 = _unita("Mario arrivò.", 0, 0)
    u1 = _unita("Mario salutò.", 1, 20)
    e0 = _evento("ev-0", u0.offset_inizio, u0.offset_fine, lemma="arrivare")
    e1 = _evento("ev-1", u1.offset_inizio, u1.offset_fine, lemma="salutare")
    result = await collega_non_adiacenti(_dedup([u0, u1], [e0, e1]))
    assert calls == []
    assert result.archi == []


@pytest.mark.asyncio
async def test_disjoint_entities_zero_llm_calls(monkeypatch):
    calls: list[str] = []

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        calls.append(user_prompt)
        return _decision()

    _install_stub(monkeypatch, handler)
    units = [
        _unita("Mario arrivò.", 0, 0),
        _unita("Pioveva.", 1, 20),
        _unita("Paolo dormì.", 2, 40),
    ]
    events = [
        _evento("ev-0", units[0].offset_inizio, units[0].offset_fine, menzione_sogg="m-mario"),
        _evento(
            "ev-1",
            units[1].offset_inizio,
            units[1].offset_fine,
            lemma="piovere",
            menzione_sogg="m-pioggia",
            posizione_doc=1,
        ),
        _evento(
            "ev-2",
            units[2].offset_inizio,
            units[2].offset_fine,
            lemma="dormire",
            menzione_sogg="m-paolo",
            posizione_doc=2,
        ),
    ]
    result = await collega_non_adiacenti(_dedup(units, events))
    assert calls == []
    assert result.archi == []


@pytest.mark.asyncio
async def test_low_confidenza_emits_no_arc(monkeypatch):
    assert 0.2 < SOGLIA_CONFIDENZA

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        return _decision(relazione_segnale="causa_esplicita", confidenza=0.2)

    _install_stub(monkeypatch, handler)
    units = [
        _unita("Mario arrivò.", 0, 0),
        _unita("Pioveva.", 1, 20),
        _unita("Mario partì.", 2, 40),
    ]
    events = [
        _evento("ev-0", units[0].offset_inizio, units[0].offset_fine),
        _evento(
            "ev-1",
            units[1].offset_inizio,
            units[1].offset_fine,
            lemma="piovere",
            menzione_sogg="m-pioggia",
            posizione_doc=1,
        ),
        _evento("ev-2", units[2].offset_inizio, units[2].offset_fine, lemma="partire", posizione_doc=2),
    ]
    result = await collega_non_adiacenti(_dedup(units, events))
    assert result.archi == []
    assert all(arco.tipo != "COLLEGATO" for arco in result.archi)
    assert all(arco.tipo != "CAUSA" for arco in result.archi)


def test_seleziona_candidati_ranks_shared_mention_first():
    nuovo = EventoRisolto(
        id="ev-new",
        lemma="arrivare",
        argomenti=[_sogg("m-shared")],
        tempo_assoluto="1995",
        span="dopo ev-ref partire",
        posizione_doc=99,
    )
    shared = EventoRisolto(
        id="ev-shared",
        lemma="parlare",
        argomenti=[_sogg("m-shared")],
        posizione_doc=0,
    )
    near = EventoRisolto(
        id="ev-near",
        lemma="guardare",
        argomenti=[_sogg("m-other")],
        tempo_assoluto="1994",
        posizione_doc=1,
    )
    textual = EventoRisolto(
        id="ev-ref",
        lemma="partire",
        argomenti=[_sogg("m-zzz")],
        posizione_doc=2,
    )
    unrelated = EventoRisolto(
        id="ev-none",
        lemma="dormire",
        argomenti=[_sogg("m-qqq")],
        posizione_doc=3,
    )
    selected = seleziona_candidati(
        nuovo, [near, textual, shared, unrelated], max_n=3
    )
    assert [event.id for event in selected] == ["ev-shared", "ev-near", "ev-ref"]
    assert condividono_entita(nuovo, shared)
    assert "m-shared" in mention_ids(nuovo)
    by_entity = seleziona_per_entita(nuovo, [near, textual, shared, unrelated])
    assert [event.id for event in by_entity] == ["ev-shared"]


def test_isolation_ast():
    for path in (CANDIDATI_PATH, PAIR_PATH):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        modules = _import_modules(tree)
        violations = [module for module in modules if _is_forbidden_import(module)]
        assert violations == []
        assert all("app.core" not in module for module in modules)
        assert all("allen" not in module.lower() for module in modules)
        assert "openai" not in modules
        assert "sentence_transformers" not in source
        assert "from app.pipeline.event_graph.persistence" not in source.lower()

    candidati_src = CANDIDATI_PATH.read_text(encoding="utf-8")
    candidati_tree = ast.parse(candidati_src, filename=str(CANDIDATI_PATH))
    candidati_modules = _import_modules(candidati_tree)
    assert all("temporal_placement" not in module for module in candidati_modules)
    assert "temporal_placement" not in candidati_src
    assert "def mention_ids" in candidati_src
    assert "def condividono_entita" in candidati_src
    assert "def seleziona_per_entita" in candidati_src

    pair_src = PAIR_PATH.read_text(encoding="utf-8")
    pair_tree = ast.parse(pair_src, filename=str(PAIR_PATH))
    pair_modules = _import_modules(pair_tree)
    assert all("temporal_placement" not in module for module in pair_modules)
    assert "temporal_placement" not in pair_src
    assert any(
        module == "app.pipeline.event_graph.candidati_entita"
        or module.startswith("app.pipeline.event_graph.candidati_entita.")
        for module in pair_modules
    )
    assert "def collega_non_adiacenti" in pair_src
    assert "def collega_inter_frase" in pair_src

    models_src = MODELS_PATH.read_text(encoding="utf-8")
    models_tree = ast.parse(models_src, filename=str(MODELS_PATH))
    model_modules = _import_modules(models_tree)
    assert all(not _is_forbidden_import(module) for module in model_modules)
    assert all("app.core" not in module for module in model_modules)
