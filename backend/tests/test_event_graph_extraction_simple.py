"""Event span keeps list numbers and dates; TEMPO is a dictionary argument."""

from __future__ import annotations

import pytest

from app.models.event_graph import EventEntityExtractionResult, EventEntityParticipation
from app.pipeline.event_graph.dedup import espandi_zona_fino_dedup
from app.pipeline.event_graph.extraction_simple import (
    EventEntityExtractionLLM,
    USER_EVENT_ENTITIES_TEMPLATE,
)
from app.pipeline.event_graph.zona_segmentation import Zona

_LINE = (
    "1. 12 marzo 1987, ore 08:15 — Un giovane meccanico trova una vecchia "
    "automobile abbandonata in un capannone."
)
_CLAUSE = (
    "Un giovane meccanico trova una vecchia automobile abbandonata in un capannone."
)


def _zona(testo: str = _LINE) -> Zona:
    return Zona(
        id="z-macchine",
        documento="doc-1",
        offset_inizio=0,
        offset_fine=len(testo),
        ordinale=0,
        testo=testo,
    )


@pytest.mark.asyncio
async def test_list_number_and_date_stay_inside_event_span():
    async def fake_extract(chunk_text, job_id=None):
        del chunk_text, job_id
        return EventEntityExtractionResult(
            participations=[
                EventEntityParticipation(
                    event=_CLAUSE,
                    entities=["giovane meccanico", "vecchia automobile", "capannone"],
                )
            ]
        )

    result = await espandi_zona_fino_dedup(_zona(), estrai_frase=fake_extract)
    assert len(result.sotto.eventi) == 1
    evento = result.sotto.eventi[0]
    assert evento.span is not None
    assert evento.span.startswith("1. 12 marzo 1987, ore 08:15")
    assert _CLAUSE in evento.span
    assert evento.lemma == evento.span
    assert evento.offset_inizio == 0
    ruoli = [arg.ruolo for arg in evento.argomenti]
    assert ruoli.count("SOGG") == 1
    assert "TEMPO" in ruoli
    forme = {
        menzione.forma
        for menzione in result.sotto.menzioni.values()
    }
    assert "12 marzo 1987" in forme
    assert "ore 08:15" in forme
    assert evento.tempo_assoluto_grezzo in {"12 marzo 1987", "ore 08:15"}
    assert evento.tempo_assoluto == "1987-03-12"


@pytest.mark.asyncio
async def test_llm_tempo_slot_becomes_tempo_argument_not_sogg():
    async def fake_extract(chunk_text, job_id=None):
        del chunk_text, job_id
        return EventEntityExtractionResult(
            participations=[
                EventEntityParticipation(
                    event=_LINE,
                    entities=["giovane meccanico", "12 marzo 1987", "automobile"],
                    tempo=["12 marzo 1987", "ore 08:15"],
                )
            ]
        )

    result = await espandi_zona_fino_dedup(_zona(), estrai_frase=fake_extract)
    evento = result.sotto.eventi[0]
    by_id = {menzione.id: menzione for menzione in result.sotto.menzioni.values()}
    tempo_forme = [
        by_id[arg.menzione_id].forma
        for arg in evento.argomenti
        if arg.ruolo == "TEMPO" and arg.menzione_id
    ]
    sogg_forme = [
        by_id[arg.menzione_id].forma
        for arg in evento.argomenti
        if arg.ruolo == "SOGG" and arg.menzione_id
    ]
    assert "12 marzo 1987" in tempo_forme
    assert "ore 08:15" in tempo_forme
    assert sogg_forme == ["meccanico"]
    assert all(arg.ruolo != "OGG" or by_id[arg.menzione_id].forma != "12 marzo 1987"
               for arg in evento.argomenti if arg.menzione_id)


def test_prompt_asks_for_tempo_and_keeps_list_prefix():
    assert "TEMPO" in USER_EVENT_ENTITIES_TEMPLATE
    assert "1." in USER_EVENT_ENTITIES_TEMPLATE
    assert '"tempo"' in USER_EVENT_ENTITIES_TEMPLATE
    assert "Non tagliare quel prefisso" in USER_EVENT_ENTITIES_TEMPLATE
    assert "standalone" in USER_EVENT_ENTITIES_TEMPLATE
    assert "testa grammaticale" in USER_EVENT_ENTITIES_TEMPLATE
    assert '"entities": ["..."' in USER_EVENT_ENTITIES_TEMPLATE
    assert "aggettivi" in USER_EVENT_ENTITIES_TEMPLATE
    assert "dopo circa 3 ore" in USER_EVENT_ENTITIES_TEMPLATE
    assert "alle otto" in USER_EVENT_ENTITIES_TEMPLATE


def test_llm_schema_resta_liste_di_stringhe():
    schema = EventEntityExtractionLLM.model_json_schema()
    defs = schema.get("$defs") or {}
    assert "EntitaEstratta" not in defs
    part = defs.get("EventEntityParticipationLLM") or schema
    entities = (part.get("properties") or {}).get("entities") or {}
    items = entities.get("items") or {}
    assert items.get("type") == "string"
    parsed = EventEntityExtractionLLM.model_validate(
        {
            "participations": [
                {
                    "event": "Apre il garage.",
                    "entities": [
                        {"name": "garage", "summary": "lo stesso vecchio garage"},
                        "meccanico",
                    ],
                    "tempo": [],
                }
            ]
        }
    )
    assert parsed.participations[0].entities == ["garage", "meccanico"]


@pytest.mark.asyncio
async def test_interval_becomes_single_tempo_entity():
    testo = (
        "2. Dal 12 marzo 1987 al 15 marzo 1987 — Resta chiuso il capannone."
    )

    async def fake_extract(chunk_text, job_id=None):
        del chunk_text, job_id
        return EventEntityExtractionResult(
            participations=[
                EventEntityParticipation(
                    event="Resta chiuso il capannone.",
                    entities=["capannone"],
                    tempo=["Dal 12 marzo 1987 al 15 marzo 1987"],
                )
            ]
        )

    result = await espandi_zona_fino_dedup(_zona(testo), estrai_frase=fake_extract)
    evento = result.sotto.eventi[0]
    assert evento.span is not None
    assert evento.span.startswith("2. Dal 12 marzo 1987")
    forme = {menzione.forma for menzione in result.sotto.menzioni.values()}
    assert "Dal 12 marzo 1987 al 15 marzo 1987" in forme or (
        "12 marzo 1987 al 15 marzo 1987" in forme
    )
    assert {arg.ruolo for arg in evento.argomenti} >= {"SOGG", "TEMPO"}


@pytest.mark.asyncio
async def test_dopo_circa_tre_ore_non_diventa_entita_tempo():
    testo = (
        "3. Dopo circa 3 ore, ore 11:20 — Il meccanico riaccende il motore."
    )

    async def fake_extract(chunk_text, job_id=None):
        del chunk_text, job_id
        return EventEntityExtractionResult(
            participations=[
                EventEntityParticipation(
                    event="Il meccanico riaccende il motore.",
                    entities=["meccanico", "motore"],
                    tempo=["Dopo circa 3 ore", "ore 11:20"],
                )
            ]
        )

    result = await espandi_zona_fino_dedup(_zona(testo), estrai_frase=fake_extract)
    evento = result.sotto.eventi[0]
    assert evento.span is not None
    assert evento.span.startswith("3. Dopo circa 3 ore, ore 11:20")
    forme = {menzione.forma for menzione in result.sotto.menzioni.values()}
    assert "ore 11:20" in forme
    assert not any("dopo circa" in forma.casefold() for forma in forme)
    tempo_forme = [
        result.sotto.menzioni[arg.menzione_id].forma
        for arg in evento.argomenti
        if arg.ruolo == "TEMPO" and arg.menzione_id
    ]
    assert tempo_forme == ["ore 11:20"]
