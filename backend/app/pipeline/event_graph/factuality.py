"""§6 — local fattualità, descending inheritance, fonte (piano sez. 9).

``completiva_di`` is the LLM indice. Parents are resolved via
``indice_grezzo`` or the factsheet span. CONTRADDICE (fonte relativity) is M9.
"""

from __future__ import annotations

from collections import defaultdict, deque

from app.models.event_graph import ChunkFactsheet, EventoRisolto, Fattualita

_NON_FATTUALE_FRASI = frozenset({"interrogativa", "imperativa"})
_INHERITED = frozenset({"NON_FATTUALE", "IPOTETICO"})
_MAIN_FINITE_SEGS = frozenset({"principale_finita", "coordinata_finita"})
_REALIZED_TEMPI = frozenset({"passato", "imperfetto", "trapassato", "presente"})


def _key(event: EventoRisolto) -> object:
    return event.id if event.id else id(event)


def _sogg_menzione_id(event: EventoRisolto) -> str | None:
    for arg in event.argomenti:
        if arg.ruolo == "SOGG" and arg.menzione_id:
            return arg.menzione_id
    return None


def _modalita(event: EventoRisolto) -> str:
    return getattr(event, "modalita", None) or "fattuale"


def _is_realized_assertion(event: EventoRisolto) -> bool:
    """Main/coord finite past-present + modalita fattuale is a realized event.

    LLM leftover ``finale`` / ``completiva_di`` on "si tolse" must not demote
    the climax to NON_FATTUALE.
    """
    seg = getattr(event, "segmentazione", None)
    return (
        _modalita(event) == "fattuale"
        and not event.polarita_negata
        and event.ruolo_se == "nessuno"
        and event.tempo in _REALIZED_TEMPI
        and (seg is None or seg in _MAIN_FINITE_SEGS)
    )


def _local_fattualita(event: EventoRisolto) -> Fattualita:
    if event.ruolo_se != "nessuno":
        return "IPOTETICO"
    if _modalita(event) == "ipotetico":
        return "IPOTETICO"
    if event.polarita_negata:
        return "NON_FATTUALE"
    if _modalita(event) in {"volitivo", "deontico"}:
        return "NON_FATTUALE"
    if event.modalizzato and _modalita(event) != "fattuale":
        return "NON_FATTUALE"
    if _is_realized_assertion(event):
        return "FATTUALE"
    if event.modalizzato:
        return "NON_FATTUALE"
    if (
        event.completiva_di is not None
        and event.classe_verbo_reggente == "non_fattivo"
    ):
        return "NON_FATTUALE"
    if event.finale:
        return "NON_FATTUALE"
    if event.frase_tipo in _NON_FATTUALE_FRASI:
        return "NON_FATTUALE"
    if event.tempo == "futuro":
        return "NON_FATTUALE"
    return "FATTUALE"


def _index_parents(
    eventi: list[EventoRisolto],
    factsheet: ChunkFactsheet | None,
) -> dict[int, EventoRisolto]:
    by_indice: dict[int, EventoRisolto] = {}
    for event in eventi:
        if event.indice_grezzo is not None:
            by_indice[event.indice_grezzo] = event
    if factsheet is None:
        return by_indice
    by_span: dict[str, EventoRisolto] = {}
    for event in eventi:
        if event.span:
            by_span[event.span] = event
    for grezzo in factsheet.eventi:
        if grezzo.indice in by_indice:
            continue
        parent = by_span.get(grezzo.span)
        if parent is not None:
            by_indice[grezzo.indice] = parent
    return by_indice


def _resolve_parent(
    event: EventoRisolto,
    by_indice: dict[int, EventoRisolto],
) -> EventoRisolto | None:
    pointer = event.completiva_di
    if pointer is None:
        return None
    parent = by_indice.get(pointer)
    if parent is None or parent is event:
        return None
    return parent


def _inherit_down(
    eventi: list[EventoRisolto],
    parent_of: dict[object, EventoRisolto],
    children: dict[object, list[EventoRisolto]],
) -> None:
    in_degree = { _key(event): 0 for event in eventi }
    for event in eventi:
        if _key(event) in parent_of:
            in_degree[_key(event)] += 1
    queue = deque(event for event in eventi if in_degree[_key(event)] == 0)
    seen = 0
    while queue:
        current = queue.popleft()
        seen += 1
        parent = parent_of.get(_key(current))
        if (
            parent is not None
            and parent.fattualita in _INHERITED
            and not _is_realized_assertion(current)
        ):
            current.fattualita = parent.fattualita
        for child in children[_key(current)]:
            child_key = _key(child)
            in_degree[child_key] -= 1
            if in_degree[child_key] == 0:
                queue.append(child)
    if seen < len(eventi):
        return


def _assign_fonte(
    eventi: list[EventoRisolto],
    parent_of: dict[object, EventoRisolto],
) -> None:
    for event in eventi:
        if event.completiva_di is None:
            event.fonte = "NARRATORE"
            continue
        parent = parent_of.get(_key(event))
        if parent is None:
            continue
        event.fonte = _sogg_menzione_id(parent) or parent.id


def applica(
    eventi: list[EventoRisolto],
    factsheet: ChunkFactsheet | None = None,
) -> list[EventoRisolto]:
    """Assign ``fattualita`` and ``fonte`` in place; return the same list."""
    for event in eventi:
        event.fattualita = _local_fattualita(event)

    by_indice = _index_parents(eventi, factsheet)
    parent_of: dict[object, EventoRisolto] = {}
    children: dict[object, list[EventoRisolto]] = defaultdict(list)
    for event in eventi:
        parent = _resolve_parent(event, by_indice)
        if parent is None:
            continue
        parent_of[_key(event)] = parent
        children[_key(parent)].append(event)

    _inherit_down(eventi, parent_of, children)
    _assign_fonte(eventi, parent_of)
    return eventi


__all__ = ["applica"]
