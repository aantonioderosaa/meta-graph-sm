"""§7 — tempo di base, piano narrativo, satelliti (piano sez. 9).

Does not write SEQUENZA (M7) or CONTRADDICE (M9).
"""

from __future__ import annotations

from collections import Counter, defaultdict

from app.models.event_graph import ArcoEvento, EventoRisolto, RunState, TempoVerbale

_TIE_BREAK: tuple[TempoVerbale, ...] = (
    "passato",
    "presente",
    "imperfetto",
    "futuro",
    "non_finito",
)
_REGOLA_SATELLITI = "narrative_plane.satelliti"


def _is_iterativo(event: EventoRisolto) -> bool:
    return bool(event.iterativo or event.iterativita)


def _narrator_factual(eventi: list[EventoRisolto]) -> list[EventoRisolto]:
    return [
        event
        for event in eventi
        if event.fattualita == "FATTUALE" and event.fonte == "NARRATORE"
    ]


def _moda(tempi: list[TempoVerbale]) -> TempoVerbale | None:
    if not tempi:
        return None
    counts = Counter(tempi)
    top = max(counts.values())
    tied = {tempo for tempo, count in counts.items() if count == top}
    for preferred in _TIE_BREAK:
        if preferred in tied:
            return preferred
    return next(iter(tied))


def tempo_base(
    eventi: list[EventoRisolto],
    state: RunState,
) -> TempoVerbale | None:
    """Moda of narrator-FATTUALE ``tempo``, or the previous base if fewer than 3."""
    candidates = _narrator_factual(eventi)
    tempi = [event.tempo for event in candidates if event.tempo is not None]
    if len(candidates) < 3:
        if state.tempo_base_precedente is not None:
            return state.tempo_base_precedente
        return _moda(tempi)
    return _moda(tempi)


def assegna_piano(
    eventi: list[EventoRisolto],
    base: TempoVerbale | None,
) -> list[EventoRisolto]:
    """Assign ``piano`` in place from fattualità / tempo / fonte / iteratività."""
    for event in eventi:
        if event.fattualita != "FATTUALE":
            event.piano = "FUORI_LINEA"
            continue
        tempo_eq_base = base is not None and event.tempo == base
        iterative = _is_iterativo(event)
        if (
            tempo_eq_base
            and not iterative
            and event.fonte == "NARRATORE"
        ):
            event.piano = "PRIMO_PIANO"
        else:
            event.piano = "SFONDO"
        if event.tempo == "imperfetto" and event.piano == "PRIMO_PIANO":
            event.piano = "SFONDO"
        if iterative and event.piano == "PRIMO_PIANO":
            event.piano = "SFONDO"
    return eventi


def _chunk_key(event: EventoRisolto) -> tuple[str, object]:
    if event.chunk_id:
        return ("chunk", event.chunk_id)
    if event.posizione_doc is not None:
        return ("docpos", event.posizione_doc)
    return ("default", None)


def _pos(event: EventoRisolto) -> int:
    return event.posizione_chunk if event.posizione_chunk is not None else 0


def satelliti(eventi: list[EventoRisolto]) -> list[ArcoEvento]:
    """Intra-chunk ``SATELLITE_DI`` from each SFONDO to its anchoring PRIMO_PIANO."""
    groups: dict[tuple[str, object], list[EventoRisolto]] = defaultdict(list)
    for event in eventi:
        groups[_chunk_key(event)].append(event)

    arcs: list[ArcoEvento] = []
    for group in groups.values():
        primi = [event for event in group if event.piano == "PRIMO_PIANO"]
        if not primi:
            continue
        for sfondo in group:
            if sfondo.piano != "SFONDO":
                continue
            here = _pos(sfondo)
            left = [pp for pp in primi if _pos(pp) < here]
            if left:
                target = max(left, key=_pos)
            else:
                right = [pp for pp in primi if _pos(pp) > here]
                if not right:
                    continue
                target = min(right, key=_pos)
            arcs.append(
                ArcoEvento(
                    tipo="SATELLITE_DI",
                    da_id=sfondo.id,
                    a_id=target.id,
                    props={"regola": _REGOLA_SATELLITI},
                )
            )
    return arcs


__all__ = ["tempo_base", "assegna_piano", "satelliti"]
