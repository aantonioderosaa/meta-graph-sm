"""Bare entity heads: SOGG/OGG/TEMPO share one instance per grammatical name."""

from __future__ import annotations

import pytest

from app.models.event_graph import EventEntityExtractionResult, EventEntityParticipation
from app.pipeline.event_graph.dedup import espandi_zona_fino_dedup
from app.pipeline.event_graph.entita_forma import (
    contesto_entita,
    e_entita_ammessa,
    pulisci_forma,
)
from app.pipeline.event_graph.ids import content_hash, menzione_id
from app.pipeline.event_graph.text_norm import _normalize_referential
from app.pipeline.event_graph.zona_segmentation import Zona


def test_pulisci_forma_testa_nuda_senza_modificatori():
    assert pulisci_forma("lo stesso vecchio garage") == "garage"
    assert pulisci_forma("un giovane meccanico") == "meccanico"
    assert pulisci_forma("una vecchia automobile abbandonata") == "automobile"
    assert pulisci_forma("3 garage") == "garage"
    assert pulisci_forma("il Sole") == "Sole"
    assert pulisci_forma("Marco Rossi") == "Marco Rossi"
    assert pulisci_forma("l'uomo") == "uomo"


def test_aggettivi_numeri_avverbi_non_sono_entita():
    assert e_entita_ammessa("vecchio") is False
    assert e_entita_ammessa("3") is False
    assert e_entita_ammessa("lentamente") is False
    assert e_entita_ammessa("stesso") is False
    assert e_entita_ammessa("garage") is True
    assert e_entita_ammessa("meccanico") is True


def test_tempo_mantiene_numeri_della_data():
    assert pulisci_forma("il 12 marzo 1987", temporale=True) == "12 marzo 1987"
    assert pulisci_forma("ore 08:15", temporale=True) == "ore 08:15"
    assert pulisci_forma("il freddo 12 marzo 1987", temporale=True) == "12 marzo 1987"


def test_identita_grammaticale_ignora_aggettivi():
    assert _normalize_referential("lo stesso vecchio garage") == "garage"
    assert _normalize_referential("garage") == "garage"
    assert _normalize_referential("il vecchio Sole") == "sole"
    assert menzione_id("lo stesso vecchio garage", "sn_comune", "d", "c", 0).id == (
        content_hash("garage")
    )
    assert menzione_id("garage", "sn_comune", "d", "c", 1).id == content_hash("garage")


def test_contesto_e_il_sn_locale_non_l_evento():
    evento = (
        "Un giovane meccanico trova una vecchia automobile abbandonata in un capannone."
    )
    assert contesto_entita(evento, "un giovane meccanico", "meccanico") == (
        "Un giovane meccanico"
    )
    assert contesto_entita(evento, "una vecchia automobile abbandonata", "automobile") == (
        "una vecchia automobile abbandonata"
    )
    assert contesto_entita(evento, "capannone", "capannone") == "in un capannone"
    assert "trova" not in contesto_entita(evento, "meccanico", "meccanico")


def _zona(testo: str) -> Zona:
    return Zona(
        id="z-entita",
        documento="doc-entita",
        offset_inizio=0,
        offset_fine=len(testo),
        ordinale=0,
        testo=testo,
    )


@pytest.mark.asyncio
async def test_due_garage_diversi_stessa_istanza_con_summary_e_contatore():
    testo = (
        "Il meccanico apre lo stesso vecchio garage. "
        "Un altro entra nel garage nuovo."
    )

    async def fake_extract(chunk_text, job_id=None):
        del chunk_text, job_id
        return EventEntityExtractionResult(
            participations=[
                EventEntityParticipation(
                    event="Il meccanico apre lo stesso vecchio garage.",
                    entities=["meccanico", "lo stesso vecchio garage"],
                ),
                EventEntityParticipation(
                    event="Un altro entra nel garage nuovo.",
                    entities=["altro", "garage nuovo"],
                ),
            ]
        )

    result = await espandi_zona_fino_dedup(_zona(testo), estrai_frase=fake_extract)
    garage_id = content_hash("garage")
    assert garage_id in result.sotto.menzioni
    garage = result.sotto.menzioni[garage_id]
    assert garage.forma == "garage"
    assert garage.occorrenze == 2
    assert len(garage.eventi) == 2
    assert len(garage.riferimenti) == 2
    assert {item.evento_id for item in garage.riferimenti} == set(garage.eventi)
    assert any("vecchio" in testo_s for testo_s in garage.riassunti)
    assert any("nuovo" in testo_s or "garage" in testo_s for testo_s in garage.riassunti)
    summaries = [item.summary for item in garage.riferimenti]
    assert any("vecchio" in testo_s for testo_s in summaries)
    forme = {m.forma.casefold() for m in result.sotto.menzioni.values()}
    assert "vecchio" not in forme
    assert "nuovo" not in forme
    assert "stesso" not in forme


@pytest.mark.asyncio
async def test_tempo_stessa_forma_stessa_istanza():
    testo = (
        "1. 12 marzo 1987, ore 08:15 — Parte. "
        "2. 12 marzo 1987 — Arriva."
    )

    async def fake_extract(chunk_text, job_id=None):
        del chunk_text, job_id
        return EventEntityExtractionResult(
            participations=[
                EventEntityParticipation(
                    event="Parte.",
                    entities=["Marco"],
                    tempo=["12 marzo 1987", "ore 08:15"],
                ),
                EventEntityParticipation(
                    event="Arriva.",
                    entities=["Marco"],
                    tempo=["il 12 marzo 1987"],
                ),
            ]
        )

    result = await espandi_zona_fino_dedup(_zona(testo), estrai_frase=fake_extract)
    data_id = menzione_id("12 marzo 1987", "sn_comune", "doc-entita", "z-entita", 0).id
    data = result.sotto.menzioni[data_id]
    assert data.forma == "12 marzo 1987"
    assert data.occorrenze >= 2
    assert len(data.eventi) >= 2
