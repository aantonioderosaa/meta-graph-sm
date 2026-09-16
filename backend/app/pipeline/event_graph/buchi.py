"""Fill explicit holes: chunk sentences not yet covered by extracted events.

This pass does not invent. It does not ask a model to diff the document
against the event list. Finite sentences already in ``zona.testo`` and not
covered by an existing span become events (append-only).

Inferred events (facts evinced from extracted nodes but not written in the
chunk) belong to ``evinti`` — not implemented here.

Isolation D6: no ``app.core.*``, no LLM.
"""

from __future__ import annotations

from typing import Any

from app.models.event_graph import (
    EventEntityExtractionResult,
    EventEntityParticipation,
    EventoRisolto,
    SottoGrafo,
)
from app.pipeline.event_graph import RULESET_VERSION, chains, event_coref, factuality, mention_coref
from app.pipeline.event_graph.chunking_periods import (
    SentenceSpan,
    UnitaTesto,
    connettivo_confine,
    split_sentences_with_offsets,
)
from app.pipeline.event_graph.dedup import (
    _evento_da_partecipazione,
    _find_offset,
)
from app.pipeline.event_graph.segmentation import SegmentationResult
from app.pipeline.event_graph.zona_segmentation import Zona

REGOLA = "buchi.frase_non_coperta"
MIN_BUCO_CHARS = 12


def _norm_span(text: str | None) -> str:
    return " ".join((text or "").strip().casefold().split())


def _span_evento(evento: EventoRisolto) -> str:
    for raw in (evento.span, evento.lemma, evento.ancora):
        text = (raw or "").strip()
        if text:
            return text
    return ""


def _chiavi_esistenti(eventi: list[EventoRisolto] | None) -> set[str]:
    keys: set[str] = set()
    for evento in eventi or []:
        if evento.fuso_in:
            continue
        key = _norm_span(_span_evento(evento))
        if key:
            keys.add(key)
    return keys


def _gia_coperto(event_text: str, esistenti: set[str]) -> bool:
    needle = _norm_span(event_text)
    if not needle:
        return True
    if needle in esistenti:
        return True
    if len(needle) < MIN_BUCO_CHARS:
        return False
    for key in esistenti:
        if needle in key or key in needle:
            return True
    return False


def _zone_ordinate(zone: list[Zona] | None) -> list[Zona]:
    items = [zona for zona in list(zone or []) if isinstance(zona, Zona)]
    return sorted(
        items,
        key=lambda zona: (
            zona.ordinale if isinstance(zona.ordinale, int) else 0,
            zona.offset_inizio,
            zona.id,
        ),
    )


def frasi_non_coperte(
    zone: list[Zona] | None,
    eventi: list[EventoRisolto] | None,
) -> list[tuple[Zona, SentenceSpan]]:
    """Finite sentences in chunk text not covered by an extracted event span."""
    esistenti = _chiavi_esistenti(eventi)
    holes: list[tuple[Zona, SentenceSpan]] = []
    for zona in _zone_ordinate(zone):
        testo = zona.testo or ""
        if not testo.strip():
            continue
        for span in split_sentences_with_offsets(testo):
            raw = (span.testo or "").strip()
            if len(_norm_span(raw)) < MIN_BUCO_CHARS:
                continue
            # Do not reuse ha_verbo_finito: that gate keeps chunks when unsure,
            # so as a hole filter it would drop real leftover sentences
            # ("dovette arrendersi") as non-finite.
            if _gia_coperto(raw, esistenti):
                continue
            holes.append((zona, span))
    return holes


def _zona_per_testo(zone: list[Zona], event_text: str) -> Zona | None:
    needle = (event_text or "").strip()
    if not needle:
        return None
    for zona in _zone_ordinate(zone):
        haystack = zona.testo or ""
        if needle in haystack:
            return zona
    needle_norm = _norm_span(needle)
    for zona in _zone_ordinate(zone):
        if needle_norm and needle_norm in _norm_span(zona.testo):
            return zona
    return None


def _posizione_inserimento(sotto: SottoGrafo, zona_id: str, offset: int) -> int:
    return sum(
        1
        for evento in sotto.eventi
        if evento.chunk_id == zona_id
        and not evento.fuso_in
        and (evento.offset_inizio if evento.offset_inizio is not None else 0) < offset
    )


def applica_eventi_mancanti(
    sotto: SottoGrafo,
    zone: list[Zona],
    result: EventEntityExtractionResult | None,
) -> list[EventoRisolto]:
    """Mint events whose span is literal chunk text. Does not persist or raise."""
    added: list[EventoRisolto] = []
    if result is None:
        return added
    esistenti = _chiavi_esistenti(sotto.eventi)
    ordered_zone = _zone_ordinate(zone)
    serial = 0
    previous_testo: str | None = None

    for part in result.participations or []:
        if not isinstance(part, EventEntityParticipation):
            continue
        evento_testo = (part.event or "").strip()
        if not evento_testo or _gia_coperto(evento_testo, esistenti):
            continue
        zona = _zona_per_testo(ordered_zone, evento_testo)
        if zona is None:
            continue
        zona_testo = zona.testo or ""
        start, end = _find_offset(zona_testo, evento_testo, 0)
        base_offset = int(zona.offset_inizio or 0)
        global_start = base_offset + start
        global_end = base_offset + end
        indice_locale = _posizione_inserimento(sotto, zona.id, global_start)
        unita = UnitaTesto(
            testo=evento_testo,
            offset_inizio=global_start,
            offset_fine=global_end,
            tipo="narrativa",
            connettivo_confine=connettivo_confine(evento_testo, previous_testo),
            zona_id=zona.id,
            indice=indice_locale,
        )
        previous_testo = evento_testo
        evento, menzioni = _evento_da_partecipazione(
            part,
            unita,
            doc_id=zona.documento or "",
            zona_testo=zona_testo,
            ordinale=int(zona.ordinale or 0),
            indice=10_000 + serial,
        )
        serial += 1
        evento.regola = REGOLA
        evento.versione_regole = RULESET_VERSION
        evento.posizione_chunk = indice_locale
        evento.indice_chunk = indice_locale
        for menzione in menzioni:
            menzione.regola = REGOLA
            menzione.versione_regole = RULESET_VERSION
        try:
            factuality.applica([evento])
            seg = SegmentationResult(eventi=[evento], menzioni=menzioni, quarantena=[])
            mention_coref.risolvi_intra(seg, None, sotto)
            sotto.aggiungi(eventi=[evento], menzioni=menzioni)
            pool = [item for item in sotto.eventi if item.id != evento.id]
            found = event_coref.candidati(evento, pool)
            esito = event_coref.classifica(evento, found, sotto=sotto)
            chains.applica(sotto, evento, esito)
        except Exception:
            continue
        esistenti.add(_norm_span(evento_testo))
        added.append(evento)
        if not zona.espansa:
            zona.espansa = True
    return added


def _result_da_buchi(
    holes: list[tuple[Zona, SentenceSpan]],
) -> EventEntityExtractionResult:
    return EventEntityExtractionResult(
        participations=[
            EventEntityParticipation(event=(span.testo or "").strip(), entities=[])
            for _zona, span in holes
            if (span.testo or "").strip()
        ]
    )


async def riempi_buchi_documento(
    sotto: SottoGrafo,
    zone: list[Zona],
    *,
    job_id: str | None = None,
    session: Any = None,
) -> list[EventoRisolto]:
    """Add events for uncovered finite sentences. No LLM. No persistence.

    ``job_id`` / ``session`` are accepted for call-site compatibility.
    """
    del job_id, session
    try:
        holes = frasi_non_coperte(zone, list(sotto.eventi))
        return applica_eventi_mancanti(sotto, zone, _result_da_buchi(holes))
    except Exception:
        return []


__all__ = [
    "MIN_BUCO_CHARS",
    "REGOLA",
    "applica_eventi_mancanti",
    "frasi_non_coperte",
    "riempi_buchi_documento",
]
