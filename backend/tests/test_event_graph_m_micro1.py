"""M-micro1: one LLM call per sentence (1a–1d). No Docker, no live LLM."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import get_args

import pytest

from app.models.event_graph import (
    ArgomentoGrezzo,
    EventoGrezzo,
    EventoRisolto,
    FraseFactsheet,
    Modalita,
    TempoVerbale,
)
from app.pipeline.event_graph import RULESET_VERSION
from app.pipeline.event_graph.chunking_periods import UnitaTesto
from app.pipeline.event_graph.extraction import estrai_frase, estrai_unita_zona
from app.pipeline.event_graph.extraction import _prune_spurious_events
from app.pipeline.event_graph.extraction_prompts import SYSTEM_FRASE
from app.pipeline.event_graph.factuality import applica

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "event_graph"
EXTRACTION_PATH = PACKAGE_DIR / "extraction.py"
PROMPTS_PATH = PACKAGE_DIR / "extraction_prompts.py"
FACTUALITY_PATH = PACKAGE_DIR / "factuality.py"
MODELS_PATH = Path(__file__).resolve().parents[1] / "app" / "models" / "event_graph.py"


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


def _sogg(forma: str = "Mario") -> ArgomentoGrezzo:
    return ArgomentoGrezzo(
        ruolo="SOGG",
        forma=forma,
        tipo_superficiale="nome_proprio",
        span=forma,
    )


def _ogg(forma: str = "la lettera") -> ArgomentoGrezzo:
    return ArgomentoGrezzo(
        ruolo="OGG",
        forma=forma,
        tipo_superficiale="sn_comune",
        span=forma,
    )


def _grezzo(indice: int = 0, **overrides) -> EventoGrezzo:
    payload = {
        "indice": indice,
        "lemma": "arrivare",
        "span": "arrivò",
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
        "argomenti": [_sogg()],
        "sogg_speciale": "nessuno",
        "e_testa": True,
        "modalita": "fattuale",
    }
    payload.update(overrides)
    return EventoGrezzo(**payload)


def _unita(
    testo: str = "Mario arrivò alle tre.",
    tipo: str = "narrativa",
    *,
    offset_inizio: int = 0,
    offset_fine: int | None = None,
    zona_id: str | None = "z-micro1",
    indice: int = 0,
) -> UnitaTesto:
    return UnitaTesto(
        testo=testo,
        offset_inizio=offset_inizio,
        offset_fine=len(testo) if offset_fine is None else offset_fine,
        tipo=tipo,  # type: ignore[arg-type]
        connettivo_confine=None,
        zona_id=zona_id,
        indice=indice,
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
async def test_one_llm_call_for_one_narrativa_unit(monkeypatch):
    calls: list[object] = []

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        calls.append(response_model)
        assert temperature == 0
        assert response_model is FraseFactsheet
        return FraseFactsheet(eventi=[_grezzo()], archi=[], quarantena=[])

    _install_stub(monkeypatch, handler)
    sheet = await estrai_frase(_unita())
    assert len(calls) == 1
    assert len(sheet.eventi) == 1
    assert sheet.eventi[0].lemma == "arrivare"


@pytest.mark.asyncio
async def test_dialogo_unit_skips_llm_and_events(monkeypatch):
    calls: list[object] = []

    async def handler(*args, **kwargs):
        calls.append(args)
        raise AssertionError("dialogo must not call the LLM")

    _install_stub(monkeypatch, handler)
    sheet = await estrai_frase(_unita(testo="«Vado via.»", tipo="dialogo"))
    assert calls == []
    assert sheet.eventi == []
    assert sheet.archi == []
    assert sheet.quarantena == []


@pytest.mark.asyncio
async def test_e_testa_and_modalita_copied_from_stub(monkeypatch):
    async def handler(*args, **kwargs):
        return FraseFactsheet(
            eventi=[
                _grezzo(
                    e_testa=True,
                    modalita="volitivo",
                    modalizzato=False,
                    lemma="volere",
                )
            ],
            archi=[],
            quarantena=[],
        )

    _install_stub(monkeypatch, handler)
    sheet = await estrai_frase(_unita())
    event = sheet.eventi[0]
    assert event.e_testa is True
    assert event.modalita == "volitivo"
    assert event.modalizzato is True


@pytest.mark.asyncio
async def test_trapassato_accepted(monkeypatch):
    raw = _grezzo(tempo="trapassato", lemma="perdere", span="aveva perso")
    assert raw.tempo == "trapassato"
    assert "trapassato" in get_args(TempoVerbale)

    async def handler(*args, **kwargs):
        return FraseFactsheet(eventi=[raw], archi=[], quarantena=[])

    _install_stub(monkeypatch, handler)
    sheet = await estrai_frase(_unita(testo="Mario aveva perso il treno."))
    assert sheet.eventi[0].tempo == "trapassato"


@pytest.mark.asyncio
async def test_checklist_violation_second_call_then_quarantena(monkeypatch):
    calls: list[object] = []

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        calls.append(response_model)
        assert response_model is FraseFactsheet
        return FraseFactsheet(
            eventi=[
                _grezzo(
                    argomenti=[_ogg()],
                    sogg_speciale="nessuno",
                )
            ],
            archi=[],
            quarantena=[],
        )

    _install_stub(monkeypatch, handler)
    sheet = await estrai_frase(_unita())
    assert len(calls) == 2
    assert sheet.eventi == []
    assert sheet.quarantena
    assert any(item.motivo == "soggetto mancante" for item in sheet.quarantena)


@pytest.mark.asyncio
async def test_offsets_copied_from_unita(monkeypatch):
    async def handler(*args, **kwargs):
        return FraseFactsheet(
            eventi=[_grezzo(offset_inizio=None, offset_fine=None)],
            archi=[],
            quarantena=[],
        )

    _install_stub(monkeypatch, handler)
    unita = _unita(offset_inizio=10, offset_fine=40)
    sheet = await estrai_frase(unita)
    assert sheet.eventi[0].offset_inizio == 10
    assert sheet.eventi[0].offset_fine == 40


@pytest.mark.asyncio
async def test_estrai_unita_zona_skips_dialogo(monkeypatch):
    calls: list[object] = []

    async def handler(*args, **kwargs):
        calls.append(1)
        return FraseFactsheet(eventi=[_grezzo()], archi=[], quarantena=[])

    _install_stub(monkeypatch, handler)
    units = [
        _unita(tipo="narrativa", indice=0),
        _unita(testo="«Ciao.»", tipo="dialogo", indice=1),
        _unita(testo="Poi partì.", tipo="narrativa", indice=2),
    ]
    sheets = await estrai_unita_zona(units)
    assert len(sheets) == 3
    assert len(calls) == 2
    assert sheets[1].eventi == []
    assert sheets[0].eventi and sheets[2].eventi


@pytest.mark.asyncio
async def test_frase_cache_reuses_without_llm(monkeypatch):
    calls: list[object] = []

    async def handler(*args, **kwargs):
        calls.append(args)
        raise AssertionError("cache hit must skip LLM")

    _install_stub(monkeypatch, handler)
    cached = FraseFactsheet(eventi=[_grezzo(lemma="partire")], archi=[], quarantena=[])
    session = FakeSession(
        rows=[
            {
                "u.factsheet_json": cached.model_dump_json(),
                "u.factsheet_versione": RULESET_VERSION,
            }
        ]
    )
    sheet = await estrai_frase(_unita(), session=session)
    assert calls == []
    assert sheet.eventi[0].lemma == "partire"
    merge_runs = [query for query, _ in session.runs if "MERGE" in query]
    assert merge_runs


def test_modalita_feeds_factuality_table():
    ipotetico = EventoRisolto(id="i", lemma="partire", modalita="ipotetico")
    volitivo = EventoRisolto(id="v", lemma="volere", modalita="volitivo")
    deontico = EventoRisolto(id="d", lemma="dovere", modalita="deontico")
    old_bool = EventoRisolto(id="m", lemma="potere", modalizzato=True)
    applica([ipotetico, volitivo, deontico, old_bool])
    assert ipotetico.fattualita == "IPOTETICO"
    assert volitivo.fattualita == "NON_FATTUALE"
    assert deontico.fattualita == "NON_FATTUALE"
    assert old_bool.fattualita == "NON_FATTUALE"


def test_prompt_covers_1a_1d_and_governing_verbs():
    blob = SYSTEM_FRASE
    assert "e_testa" in blob
    assert "modalita" in blob
    assert "trapassato" in blob
    assert "intra-sentence" in blob.lower() or "intra-frase" in blob.lower() or "INTRA-SENTENCE" in blob
    assert "provocare" in blob
    assert "iniziare a" in blob
    assert "convention" in blob.lower() or "convenzione" in blob.lower()
    assert "quoted" in blob.lower() or "dialogo" in blob.lower()
    for value in ("fattuale", "ipotetico", "volitivo", "deontico"):
        assert value in blob
    for value in get_args(Modalita):
        assert value in blob


def test_prune_drops_adverb_and_quoted_and_aspectual_double():
    sheet = FraseFactsheet(
        eventi=[
            _grezzo(0, lemma="cominciare", e_testa=True, span="cominciò"),
            _grezzo(
                1,
                lemma="soffiare",
                e_testa=False,
                tempo="non_finito",
                completiva_di=0,
                span="soffiare",
            ),
            _grezzo(2, lemma="lentamente", e_testa=False, span="lentamente"),
            _grezzo(3, lemma="vedere", e_testa=False, span="Posso scuotere"),
        ]
    )
    pruned = _prune_spurious_events(
        sheet,
        'Il Vento cominciò a soffiare lentamente. Disse: «Posso scuotere».',
    )
    lemmas = {event.lemma for event in pruned.eventi}
    assert "lentamente" not in lemmas
    assert "cominciare" not in lemmas
    assert "soffiare" in lemmas
    assert "vedere" not in lemmas
    assert any(event.e_testa and event.lemma == "soffiare" for event in pruned.eventi)


def test_isolation_ast():
    violations: list[str] = []
    for path in (EXTRACTION_PATH, PROMPTS_PATH, FACTUALITY_PATH, MODELS_PATH):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for module in _import_modules(tree):
            if _is_forbidden_import(module):
                violations.append(f"{path}: {module}")
    assert violations == []
    extraction = EXTRACTION_PATH.read_text(encoding="utf-8")
    assert "app.core" not in extraction
    assert "sentence-transformers" not in extraction
    assert "estrai_frase" in extraction
    assert "estrai_unita_zona" in extraction
