"""M3 extraction + segmentation tests (no Docker, no live LLM)."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from app.models.event_graph import (
    ArgomentoGrezzo,
    ChunkFactsheet,
    EventoGrezzo,
    FraseFactsheet,
)
from app.pipeline.event_graph import RULESET_VERSION
from app.pipeline.event_graph.chunking_periods import PeriodChunk
from app.pipeline.event_graph.extraction import (
    estrai,
)
from app.pipeline.event_graph.ids import eg_chunk_id, evento_id, menzione_id
from app.pipeline.event_graph.segmentation import risolvi

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "event_graph"
EXTRACTION_PATH = PACKAGE_DIR / "extraction.py"
SEGMENTATION_PATH = PACKAGE_DIR / "segmentation.py"


class FakeSession:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.runs = []

    async def run(self, query, parameters=None, **params):
        if parameters is not None and isinstance(parameters, dict):
            merged = {**parameters, **params}
        else:
            merged = dict(params)
        self.runs.append((query, merged))

        class R:
            def single(self_inner):
                return self.rows[0] if self.rows else None

            def data(self_inner):
                return self.rows

        return R()


def _chunk(
    testo: str = "Mario arrivò alle tre.",
    doc_id: str = "doc-m3",
    ordinale: int = 0,
) -> PeriodChunk:
    return PeriodChunk(
        id=eg_chunk_id(doc_id, ordinale, testo),
        doc_id=doc_id,
        ordinale=ordinale,
        testo=testo,
    )


def _phase1_event(
    indice: int = 0,
    lemma: str = "arrivare",
    span: str = "arrivò",
    **overrides,
) -> EventoGrezzo:
    payload = {
        "indice": indice,
        "lemma": lemma,
        "span": span,
        "tempo": "passato",
        "segmentazione": "principale_finita",
        "polarita_negata": False,
        "modalizzato": False,
        "iterativo": False,
        "sogg_speciale": "nessuno",
        "ruolo_se": "nessuno",
        "finale": False,
        "frase_tipo": "dichiarativa",
        "completiva_di": None,
        "classe_verbo_reggente": "nessuna",
        "marca_dialogo": False,
        "frase_indice": 0,
        "avverbio_temporale_esplicito": False,
        "connettivo_sequenziale_esplicito": False,
        "argomenti": [_sogg()],
        "e_testa": True,
        "modalita": "fattuale",
    }
    payload.update(overrides)
    return EventoGrezzo(**payload)


def _sogg(forma: str = "Mario", tipo: str = "nome_proprio") -> ArgomentoGrezzo:
    return ArgomentoGrezzo(
        ruolo="SOGG",
        forma=forma,
        tipo_superficiale=tipo,  # type: ignore[arg-type]
        span=forma,
    )


def _ogg(forma: str = "la lettera") -> ArgomentoGrezzo:
    return ArgomentoGrezzo(
        ruolo="OGG",
        forma=forma,
        tipo_superficiale="sn_comune",
        span=forma,
    )


def _grezzo(
    indice: int,
    lemma: str,
    span: str,
    argomenti: list[ArgomentoGrezzo] | None = None,
    **overrides,
) -> EventoGrezzo:
    payload = {
        "indice": indice,
        "lemma": lemma,
        "span": span,
        "tempo": "passato",
        "segmentazione": "principale_finita",
        "polarita_negata": False,
        "modalizzato": False,
        "iterativo": False,
        "ruolo_se": "nessuno",
        "completiva_di": None,
        "classe_verbo_reggente": "nessuna",
        "finale": False,
        "frase_tipo": "dichiarativa",
        "marca_dialogo": False,
        "frase_indice": 0,
        "avverbio_temporale_esplicito": False,
        "connettivo_sequenziale_esplicito": False,
        "argomenti": argomenti if argomenti is not None else [_sogg()],
        "sogg_speciale": "nessuno",
    }
    payload.update(overrides)
    return EventoGrezzo(**payload)


def _valid_sheet() -> ChunkFactsheet:
    return ChunkFactsheet(
        eventi=[
            _grezzo(0, "arrivare", "arrivò", [_sogg("Mario")]),
        ],
        archi=[],
        quarantena=[],
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
        "app.pipeline.event_graph.extraction.call_structured", stub
    )


@pytest.mark.asyncio
async def test_phase_cap_at_most_two_calls_when_checklist_ok(monkeypatch):
    calls: list[object] = []

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        calls.append(response_model)
        assert temperature == 0
        assert response_model is FraseFactsheet
        return FraseFactsheet(eventi=[_phase1_event()], archi=[], quarantena=[])

    _install_stub(monkeypatch, handler)
    outcome = await estrai(_chunk())
    assert outcome.llm_calls == 1
    assert len(calls) == 1
    assert outcome.reused is False
    assert len(outcome.factsheet.eventi) == 1
    assert outcome.factsheet.eventi[0].argomenti[0].ruolo == "SOGG"


@pytest.mark.asyncio
async def test_checklist_fix_at_most_two_calls_per_sentence(monkeypatch):
    calls: list[object] = []

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        calls.append(response_model)
        assert response_model is FraseFactsheet
        if len(calls) == 1:
            return FraseFactsheet(
                eventi=[_phase1_event(argomenti=[_ogg()], sogg_speciale="nessuno")],
                archi=[],
                quarantena=[],
            )
        return FraseFactsheet(
            eventi=[
                _grezzo(
                    0,
                    "arrivare",
                    "arrivò",
                    [_ogg()],
                    sogg_speciale="IGNOTO",
                )
            ],
            archi=[],
            quarantena=[],
        )

    _install_stub(monkeypatch, handler)
    outcome = await estrai(_chunk())
    assert outcome.llm_calls == 2
    assert len(calls) == 2
    assert outcome.factsheet.eventi[0].sogg_speciale == "IGNOTO"
    assert outcome.quarantena == []
    assert len(calls) <= 2


@pytest.mark.asyncio
async def test_llm_failure_goes_to_quarantena(monkeypatch):
    async def handler(*args, **kwargs):
        raise RuntimeError("llm down")

    _install_stub(monkeypatch, handler)
    chunk = _chunk()
    outcome = await estrai(chunk)
    assert outcome.reused is False
    assert outcome.factsheet.eventi == []
    assert outcome.quarantena
    assert all(item.motivo == "estrazione fallita" for item in outcome.quarantena)
    assert outcome.quarantena[0].frammento == chunk.testo
    assert outcome.quarantena[0].versione_regole == RULESET_VERSION

    resolved = risolvi(outcome.factsheet, chunk)
    assert resolved.eventi == []
    assert resolved.menzioni == []


@pytest.mark.asyncio
async def test_reuse_skips_llm_unless_version_differs(monkeypatch):
    calls: list[object] = []

    async def handler(*args, **kwargs):
        calls.append(args)
        return FraseFactsheet(eventi=[_phase1_event()], archi=[], quarantena=[])

    _install_stub(monkeypatch, handler)
    sheet = _valid_sheet()
    chunk = _chunk()
    session = FakeSession(
        rows=[
            {
                "c.factsheet_json": sheet.model_dump_json(),
                "c.factsheet_versione": RULESET_VERSION,
            }
        ]
    )
    outcome = await estrai(chunk, session=session)
    assert outcome.reused is True
    assert outcome.llm_calls == 0
    assert calls == []
    assert outcome.factsheet.eventi[0].lemma == "arrivare"

    stale = FakeSession(
        rows=[
            {
                "c.factsheet_json": sheet.model_dump_json(),
                "c.factsheet_versione": "0.9.0",
            }
        ]
    )
    stale_outcome = await estrai(chunk, session=stale)
    assert stale_outcome.reused is False
    assert stale_outcome.llm_calls >= 1
    assert calls


@pytest.mark.asyncio
async def test_persist_merge_writes_factsheet(monkeypatch):
    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        assert response_model is FraseFactsheet
        return FraseFactsheet(eventi=[_phase1_event()], archi=[], quarantena=[])

    _install_stub(monkeypatch, handler)
    chunk = _chunk()
    session = FakeSession()
    outcome = await estrai(chunk, session=session)
    merge_runs = [
        (query, params)
        for query, params in session.runs
        if "MERGE" in query and ":EgChunk" in query
    ]
    assert merge_runs
    query, params = merge_runs[0]
    assert params["id"] == chunk.id
    assert params["doc_id"] == chunk.doc_id
    assert params["ordinale"] == chunk.ordinale
    assert params["ver"] == RULESET_VERSION
    raw = params["json"]
    payload = json.loads(raw) if isinstance(raw, str) else raw
    assert "eventi" in payload
    assert payload["eventi"]
    assert outcome.factsheet.eventi


def test_segmentation_positions_follow_span_order_not_llm_indice():
    testo = "Anna partì ieri. Mario arrivò oggi."
    chunk = _chunk(testo=testo)
    factsheet = ChunkFactsheet(
        eventi=[
            _grezzo(0, "arrivare", "Mario arrivò", [_sogg("Mario")]),
            _grezzo(1, "partire", "Anna partì", [_sogg("Anna")]),
        ],
        archi=[],
        quarantena=[],
    )
    result = risolvi(factsheet, chunk)
    assert [event.lemma for event in result.eventi] == ["partire", "arrivare"]
    assert [event.posizione_chunk for event in result.eventi] == [0, 1]
    assert [event.indice_chunk for event in result.eventi] == [0, 1]
    assert result.eventi[0].posizione_doc == chunk.ordinale
    assert result.eventi[0].id == evento_id(chunk.doc_id, chunk.testo, 0)
    assert result.eventi[1].id == evento_id(chunk.doc_id, chunk.testo, 1)
    assert result.eventi[0].id != evento_id(chunk.doc_id, chunk.testo, 1)
    assert result.eventi[0].fattualita is None
    assert result.eventi[0].piano is None
    assert result.eventi[0].polarita == "affermata"
    assert result.eventi[0].regola == "segmentation.risolvi"
    assert result.eventi[0].versione_regole == RULESET_VERSION
    assert result.eventi[0].chunk_id == chunk.id


def test_mentions_nome_proprio_vs_sn_comune():
    testo = "Mario scrisse la lettera."
    chunk = _chunk(testo=testo)
    factsheet = ChunkFactsheet(
        eventi=[
            _grezzo(
                0,
                "scrivere",
                "scrisse",
                [_sogg("Mario", "nome_proprio"), _ogg("la lettera")],
            )
        ],
        archi=[],
        quarantena=[],
    )
    result = risolvi(factsheet, chunk)
    assert len(result.menzioni) == 2
    proprio = result.menzioni[0]
    comune = result.menzioni[1]
    expected_proprio = menzione_id("Mario", "nome_proprio", chunk.doc_id, chunk.id, 0)
    expected_comune = menzione_id("la lettera", "sn_comune", chunk.doc_id, chunk.id, 1)
    assert proprio.non_risolto is False
    assert proprio.non_risolto == expected_proprio.non_risolto
    assert proprio.id == expected_proprio.id
    assert comune.non_risolto is False
    assert comune.non_risolto == expected_comune.non_risolto
    assert comune.id == expected_comune.id
    assert result.eventi[0].argomenti[0].menzione_id == proprio.id
    assert result.eventi[0].argomenti[1].menzione_id == comune.id


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


def test_extraction_and_segmentation_isolation_ast():
    violations: list[str] = []
    for path in (EXTRACTION_PATH, SEGMENTATION_PATH):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for module in _import_modules(tree):
            if _is_forbidden_import(module):
                violations.append(f"{path}: {module}")
    assert violations == []


def test_prompts_contain_bilingual_rubric():
    from app.pipeline.event_graph.extraction_prompts import (
        BILINGUAL_RUBRIC,
        SYSTEM_FRASE,
    )

    for blob in (BILINGUAL_RUBRIC, SYSTEM_FRASE):
        assert "polarita_negata" in blob
        assert "modalizzato" in blob
        assert "modalita" in blob
        assert "iterativo" in blob
        assert "relazione_segnale" in blob
        assert "causa_esplicita" in blob
        assert "avverbio_temporale_esplicito" in blob
        assert "connettivo_sequenziale_esplicito" in blob
        assert "because" in blob.lower() or "not" in blob.lower()
        assert "perché" in blob or "perche" in blob or "non" in blob
    assert "e_testa" in SYSTEM_FRASE
    assert "trapassato" in SYSTEM_FRASE
