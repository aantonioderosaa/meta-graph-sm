"""§7 — incremental per-document SEQUENZA spine + SATELLITE_DI (piano sez. 9).

Does not add events to ``SottoGrafo`` (the caller does that after). Reuses
``narrative_plane.satelliti`` for pairing.
"""

from __future__ import annotations

from app.models.event_graph import ArcoEvento, EventoRisolto, RunState, SottoGrafo, TempoVerbale
from app.pipeline.event_graph import RULESET_VERSION
from app.pipeline.event_graph.narrative_plane import satelliti

_REGOLA = "backbone.estendi"


def _pos_key(event: EventoRisolto) -> tuple[int, int]:
    doc = event.posizione_doc if event.posizione_doc is not None else 0
    chunk = event.posizione_chunk if event.posizione_chunk is not None else 0
    return (doc, chunk)


def _arc_key(arco: ArcoEvento) -> tuple[str, str, str]:
    return (str(arco.tipo), arco.da_id, arco.a_id)


def _primi_piano(eventi: list[EventoRisolto]) -> list[EventoRisolto]:
    pps = [event for event in eventi if event.piano == "PRIMO_PIANO"]
    pps.sort(key=_pos_key)
    return pps


def _lookup(sotto: SottoGrafo, event_id: str) -> EventoRisolto | None:
    for event in sotto.eventi:
        if event.id == event_id:
            return event
    return None


def _chunk_documento(eventi: list[EventoRisolto]) -> str | None:
    for event in eventi:
        if event.documento:
            return event.documento
    return None


def _same_documento(
    tail: EventoRisolto | None,
    first: EventoRisolto,
    eventi: list[EventoRisolto],
) -> bool:
    """True unless both sides have a documento and they differ (parallel spines)."""
    if tail is None:
        return True
    tail_doc = tail.documento
    first_doc = first.documento or _chunk_documento(eventi)
    if tail_doc is None:
        # Stored none: single-doc run, or cannot prove a mismatch.
        return True
    if first_doc is None:
        return True
    return tail_doc == first_doc


def _sequenza(da_id: str, a_id: str) -> ArcoEvento:
    return ArcoEvento(
        tipo="SEQUENZA",
        da_id=da_id,
        a_id=a_id,
        props={"regola": _REGOLA, "versione_regole": RULESET_VERSION},
    )


def estendi(
    eventi: list[EventoRisolto],
    sotto: SottoGrafo,
    state: RunState,
) -> list[ArcoEvento]:
    """Chain this chunk's PRIMO_PIANO onto the per-document spine; attach satellites."""
    seen = {_arc_key(arco) for arco in sotto.archi}
    nuovi: list[ArcoEvento] = []

    def _consider(arco: ArcoEvento) -> None:
        if arco.da_id == arco.a_id:
            return
        key = _arc_key(arco)
        if key in seen:
            return
        seen.add(key)
        nuovi.append(arco)

    pps = _primi_piano(eventi)
    if pps:
        first = pps[0]
        if state.backbone_tail:
            tail_event = _lookup(sotto, state.backbone_tail)
            if _same_documento(tail_event, first, eventi):
                _consider(_sequenza(state.backbone_tail, first.id))
        for prev, nxt in zip(pps, pps[1:]):
            _consider(_sequenza(prev.id, nxt.id))
        state.backbone_tail = pps[-1].id

    for satellite in satelliti(eventi):
        _consider(satellite)

    sotto.archi.extend(nuovi)
    return nuovi


def avanza(
    state: RunState,
    base: TempoVerbale | None,
    backbone_tail: str | None = None,
) -> RunState:
    """Orchestrator hook: record the chunk tempo base and optional spine tail."""
    state.tempo_base_precedente = base
    if backbone_tail is not None:
        state.backbone_tail = backbone_tail
    return state


__all__ = ["estendi", "avanza"]
