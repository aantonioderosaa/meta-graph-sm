"""MT2 — extract temporal anchors per zona (no Docker, no live LLM)."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from app.models.event_graph import (
    AncoraTemporaleProposta,
    ArgomentoRisolto,
    EventoRisolto,
    LivelloAncoreResult,
    MenzioneRisolta,
)
from app.pipeline.event_graph.ancore_estrazione import (
    EVENTO_DOCUMENTO,
    EVENTO_ZONA,
    MAX_CHAR_TESTO_ZONA,
    STAGE,
    AncoreZonaLlm,
    estrai_ancore,
    prepass_regex,
    proposte_da_menzioni_tempo,
    proposte_da_semi,
    user_ancore_zona,
)
from app.pipeline.event_graph.infra import bus as event_graph_bus
from app.pipeline.event_graph.zona_segmentation import Zona

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "event_graph"
MODULE_PATH = PACKAGE_DIR / "ancore_estrazione.py"


def _zona(
    testo: str,
    *,
    offset_inizio: int = 0,
    ancore_temporali: list[str] | None = None,
    zona_id: str = "z-1",
    documento: str = "doc-1",
    ordinale: int = 0,
) -> Zona:
    return Zona(
        id=zona_id,
        documento=documento,
        offset_inizio=offset_inizio,
        offset_fine=offset_inizio + len(testo),
        ordinale=ordinale,
        testo=testo,
        ancore_temporali=list(ancore_temporali or []),
    )


async def _vuoto_llm(*_args, **_kwargs) -> AncoreZonaLlm:
    return AncoreZonaLlm(ancore=[])


def _espressioni(ancore: list[AncoraTemporaleProposta]) -> set[str]:
    return {item.espressione or "" for item in ancore}


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


def test_prepass_regex_trova_anno_iso_orario_senza_llm():
    testo = "Nel 1843 uscì alle 18; il 1843-12-24 chiuse (18:30)."
    hits = prepass_regex(testo)
    espr = _espressioni(hits)
    assert "1843" in espr
    assert "1843-12-24" in espr
    assert "18:30" in espr
    assert any(
        item.espressione and item.espressione.lower().startswith("alle 18") for item in hits
    )
    anno = next(item for item in hits if item.espressione == "1843")
    assert anno.tipo == "data"
    assert anno.natura == "esplicita"
    assert anno.stimato is False
    assert anno.inizio == "1843"
    iso = next(item for item in hits if item.espressione == "1843-12-24")
    assert iso.inizio == "1843-12-24"
    ora = next(item for item in hits if item.espressione == "18:30")
    assert ora.tipo == "ora"
    assert ora.inizio is None


def test_seme_diventa_proposta_con_offset_se_nel_testo():
    testo = "Arrivò ieri sera."
    trovate = proposte_da_semi(testo, ["ieri"], offset_documento=100)
    assert len(trovate) == 1
    seme = trovate[0]
    assert seme.espressione == "ieri"
    assert seme.offset_inizio == 100 + testo.find("ieri")
    assert seme.offset_fine == seme.offset_inizio + len("ieri")
    assert seme.posizione_doc_min == seme.offset_inizio

    mancanti = proposte_da_semi(testo, ["l'estate scorsa"])
    assert len(mancanti) == 1
    assert mancanti[0].espressione == "l'estate scorsa"
    assert mancanti[0].offset_inizio is None
    assert mancanti[0].offset_fine is None


@pytest.mark.asyncio
async def test_colpo_regex_sopravvive_se_llm_vuoto():
    zona = _zona("Il libro è del 1843.", offset_inizio=0)

    async def boom_if_called(*_args, **_kwargs):
        return AncoreZonaLlm(ancore=[])

    result = await estrai_ancore([zona], call_structured=boom_if_called)
    assert result is not None
    assert any(item.espressione == "1843" for item in result.ancore)
    assert result.livello.segnali == []


@pytest.mark.asyncio
async def test_eccezione_llm_restituisce_regex_e_pubblica():
    zona = _zona("Nel 1843 Scrooge contò le monete.", offset_inizio=0)
    job_id = "job-ancore-fail"
    queue = await event_graph_bus.subscribe(job_id)

    async def boom(*_args, **_kwargs):
        raise RuntimeError("llm down")

    try:
        result = await estrai_ancore(
            [zona], job_id=job_id, call_structured=boom
        )
        assert result is not None
        assert any(item.espressione == "1843" for item in result.ancore)
        assert result.n_llm_failures == 1
        eventi = []
        while True:
            try:
                eventi.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        zona_events = [item for item in eventi if item["event"] == EVENTO_ZONA]
        assert zona_events
        payload = zona_events[0]["payload"]
        assert payload["failed"] is True
        assert payload["zona_id"] == zona.id
        assert payload["from_regex"] >= 1
        assert payload["extracted"] >= 1
        assert zona_events[0]["stage"] == STAGE
        riepiloghi = [item for item in eventi if item["event"] == EVENTO_DOCUMENTO]
        assert riepiloghi
        assert riepiloghi[0]["payload"]["n_llm_failures"] == 1
        assert riepiloghi[0]["payload"]["n_ancore"] >= 1
    finally:
        await event_graph_bus.unsubscribe(job_id, queue)
        event_graph_bus.reset_event_bus()


@pytest.mark.asyncio
async def test_user_prompt_solo_testo_della_zona():
    secret_other = "TESTO DELL ALTRA ZONA CHE NON DEVE COMPARIRE"
    coda_unica = "UNIQUE_TAIL_MARKER_SHOULD_NOT_APPEAR"
    blob = ("MARLEY was dead: to begin with. " * 4000) + coda_unica
    assert len(blob) > MAX_CHAR_TESTO_ZONA
    zona_a = _zona(
        "Nel 1843 Scrooge chiuse il negozio.",
        offset_inizio=0,
        zona_id="z-a",
        ordinale=0,
    )
    zona_b = _zona(
        blob,
        offset_inizio=10_000,
        zona_id="z-b",
        ordinale=1,
        ancore_temporali=["Natale"],
    )
    captured: list[str] = []

    async def spy(system_prompt, user_prompt, response_model, temperature=0, job_id=None):
        captured.append(user_prompt)
        assert temperature == 0
        assert response_model is AncoreZonaLlm
        assert secret_other not in user_prompt
        return AncoreZonaLlm(ancore=[])

    zona_b_alt = _zona(
        secret_other,
        offset_inizio=500,
        zona_id="z-other",
        ordinale=2,
    )
    await estrai_ancore(
        [zona_a, zona_b_alt],
        call_structured=spy,
    )
    assert len(captured) == 2
    assert zona_a.testo in captured[0]
    assert secret_other not in captured[0]
    assert zona_a.testo not in captured[1]
    assert secret_other in captured[1]

    captured.clear()
    await estrai_ancore([zona_b], call_structured=spy)
    assert captured
    prompt_huge = captured[0]
    assert coda_unica not in prompt_huge
    assert len(prompt_huge) < len(blob)
    assert "MARLEY was dead" in prompt_huge
    assert zona_a.testo not in prompt_huge


@pytest.mark.asyncio
async def test_offset_sono_assoluti_nel_documento():
    testo = "xxxxx1843"
    assert testo.find("1843") == 5
    zona = _zona(testo, offset_inizio=100)

    async def llm_span(system_prompt, user_prompt, response_model, temperature=0, job_id=None):
        return AncoreZonaLlm(
            ancore=[
                AncoraTemporaleProposta(
                    etichetta="1843",
                    tipo="data",
                    espressione="1843",
                    offset_inizio=5,
                    offset_fine=9,
                    inizio="1843",
                    natura="esplicita",
                    stimato=False,
                )
            ]
        )

    result = await estrai_ancore([zona], call_structured=llm_span)
    assert result.ancore
    for ancora in result.ancore:
        if ancora.espressione == "1843":
            assert ancora.offset_inizio == 105
            assert ancora.offset_fine == 109
            assert ancora.posizione_doc_min == 105


@pytest.mark.asyncio
async def test_inizio_iso_solo_se_scritto_natale_non_inventa_1843():
    zona = _zona("Era Natale.", offset_inizio=0)
    hits = prepass_regex(zona.testo)
    assert all("Natale" not in (item.espressione or "") for item in hits)
    assert all(item.inizio != "1843" for item in hits)

    async def inventa(system_prompt, user_prompt, response_model, temperature=0, job_id=None):
        return AncoreZonaLlm(
            ancore=[
                AncoraTemporaleProposta(
                    etichetta="Natale",
                    tipo="simbolica",
                    espressione="Natale",
                    offset_inizio=4,
                    offset_fine=10,
                    inizio="1843",
                    natura="esplicita",
                    stimato=False,
                )
            ]
        )

    result = await estrai_ancore([zona], call_structured=inventa)
    natale = next(item for item in result.ancore if item.espressione == "Natale")
    assert natale.inizio is None


@pytest.mark.asyncio
async def test_zona_vuota_salta_llm_ma_esegue_semi():
    chiamate = {"n": 0}

    async def spy(*_args, **_kwargs):
        chiamate["n"] += 1
        return AncoreZonaLlm(ancore=[])

    zona = _zona("   ", ancore_temporali=["ieri"])
    result = await estrai_ancore([zona], call_structured=spy)
    assert chiamate["n"] == 0
    assert any(item.espressione == "ieri" for item in result.ancore)
    assert result.ancore[0].offset_inizio is None


def test_user_ancore_zona_non_sostituisce_il_documento():
    zona = _zona("Solo questa zona 1843.")
    prompt = user_ancore_zona(zona, regex_hits=prepass_regex(zona.testo))
    assert "Solo questa zona 1843." in prompt
    assert "christmas" not in prompt.casefold()


def test_isolamento_d6():
    source = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MODULE_PATH))
    violations = [
        f"{MODULE_PATH}: {module}"
        for module in _import_modules(tree)
        if _is_forbidden_import(module)
    ]
    assert violations == []
    assert "app.core" not in source
    assert "except Exception: return None" not in source.replace("\n", " ")


def test_esito_non_e_none_ed_espone_livello():
    assert LivelloAncoreResult().ancore == []


@pytest.mark.asyncio
async def test_eventi_passati_usano_tempo_senza_llm():
    testo = "1. 12 marzo 1987, ore 08:15 — Un meccanico trova un'auto."
    zona = _zona(testo)
    menzione = MenzioneRisolta(id="m-data", forma="12 marzo 1987")
    evento = EventoRisolto(
        id="ev-1",
        lemma=testo,
        span=testo,
        documento="doc-1",
        chunk_id=zona.id,
        offset_inizio=0,
        offset_fine=len(testo),
        argomenti=[ArgomentoRisolto(ruolo="TEMPO", menzione_id="m-data")],
    )

    async def boom(*_args, **_kwargs):
        raise AssertionError("temporal phase must not re-extract")

    result = await estrai_ancore(
        [zona],
        call_structured=boom,
        eventi=[evento],
        menzioni=[menzione],
    )
    assert len(result.ancore) == 1
    ancora = result.ancore[0]
    assert ancora.espressione == "12 marzo 1987"
    assert ancora.inizio == "1987-03-12"
    assert ancora.eventi == ["ev-1"]
    assert ancora.tipo == "data"


def test_proposte_stessa_occorrenza_fondono_eventi():
    zona = _zona("Il 12 marzo 1987 aprì e chiuse.")
    menzione = MenzioneRisolta(id="m-1", forma="12 marzo 1987")
    eventi = [
        EventoRisolto(
            id="ev-a",
            lemma="aprì",
            chunk_id=zona.id,
            offset_inizio=0,
            argomenti=[ArgomentoRisolto(ruolo="TEMPO", menzione_id="m-1")],
        ),
        EventoRisolto(
            id="ev-b",
            lemma="chiuse",
            chunk_id=zona.id,
            offset_inizio=0,
            argomenti=[ArgomentoRisolto(ruolo="TEMPO", menzione_id="m-1")],
        ),
    ]
    ancore = proposte_da_menzioni_tempo(eventi, [menzione], [zona])
    assert len(ancore) == 1
    assert set(ancore[0].eventi) == {"ev-a", "ev-b"}
    assert ancore[0].inizio == "1987-03-12"


def test_proposte_stesso_orologio_occorrenze_diverse_restano_due():
    testo = (
        "Dopo circa un'ora, ore 16:30 — prova. "
        "Dopo 90 minuti, ore 16:30 — termina."
    )
    zona = _zona(testo)
    skate = MenzioneRisolta(id="m-a", forma="ore 16:30")
    calcio = MenzioneRisolta(id="m-b", forma="ore 16:30")
    off_a = testo.find("ore 16:30")
    off_b = testo.find("ore 16:30", off_a + 1)
    eventi = [
        EventoRisolto(
            id="ev-skate",
            lemma="provare",
            chunk_id=zona.id,
            offset_inizio=off_a,
            argomenti=[ArgomentoRisolto(ruolo="TEMPO", menzione_id="m-a")],
        ),
        EventoRisolto(
            id="ev-calcio",
            lemma="terminare",
            chunk_id=zona.id,
            offset_inizio=off_b,
            argomenti=[ArgomentoRisolto(ruolo="TEMPO", menzione_id="m-b")],
        ),
    ]
    ancore = proposte_da_menzioni_tempo(eventi, [skate, calcio], [zona])
    orari = [a for a in ancore if a.espressione == "ore 16:30"]
    assert len(orari) == 2
    assert {a.offset_inizio for a in orari} == {off_a, off_b}


def test_proposte_scartano_offset_frasale_e_tengono_vaga():
    zona = _zona("Dopo circa 3 ore ripartì. Anni 70, dopo un po' successe.")
    offset = MenzioneRisolta(id="m-off", forma="dopo circa 3 ore")
    vaga = MenzioneRisolta(id="m-vaga", forma="anni 70")
    poco = MenzioneRisolta(id="m-poco", forma="dopo un po'")
    eventi = [
        EventoRisolto(
            id="ev-off",
            lemma="ripartì",
            chunk_id=zona.id,
            argomenti=[ArgomentoRisolto(ruolo="TEMPO", menzione_id="m-off")],
        ),
        EventoRisolto(
            id="ev-vaga",
            lemma="successe",
            chunk_id=zona.id,
            argomenti=[
                ArgomentoRisolto(ruolo="TEMPO", menzione_id="m-vaga"),
                ArgomentoRisolto(ruolo="TEMPO", menzione_id="m-poco"),
            ],
        ),
    ]
    ancore = proposte_da_menzioni_tempo(eventi, [offset, vaga, poco], [zona])
    espr = {item.espressione for item in ancore}
    assert "dopo circa 3 ore" not in espr
    assert "anni 70" in espr
    assert "dopo un po'" in espr or "dopo un po" in espr
    assert all(item.tipo == "vaga" for item in ancore)
