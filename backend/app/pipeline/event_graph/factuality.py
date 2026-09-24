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


def _is_realized_assertion(event: EventoRisolto, has_real_parent: bool = False) -> bool:
    """Main/coord finite past-present + modalita fattuale is a realized event.

    LLM leftover ``finale`` / ``completiva_di`` on "si tolse" must not demote
    the climax to NON_FATTUALE.
    
    But if an event has finale=True, it should never be considered realized in this sense.
    
    When has_real_parent is True, events with modalizzato or non-fattualita indicators are 
    not considered realized assertions.
    """
    # If this is a final event, don't consider it as realized assertion regardless of other factors
    if getattr(event, "finale", False):
        return False
        
    # When has_real_parent=True, events with modalizzato or non-fattualita indicators are 
    # not considered realized assertions
    if has_real_parent:
        if event.modalizzato:
            return False
        if event.polarita_negata:
            return False
        if event.frase_tipo in _NON_FATTUALE_FRASI:
            return False
        if event.tempo == "futuro":
            return False
    
    seg = getattr(event, "segmentazione", None)
    return (
        _modalita(event) == "fattuale"
        and not event.polarita_negata
        and event.ruolo_se == "nessuno"
        and event.tempo in _REALIZED_TEMPI
        and (seg is None or seg in _MAIN_FINITE_SEGS)
    )


def _local_fattualita(event: EventoRisolto, parent_of: dict[object, EventoRisolto] | None = None) -> Fattualita:
    # Check for IPOTETICO first - ruolo_se != "nessuno" 
    if event.ruolo_se != "nessuno":
        return "IPOTETICO"
    
    # Then check modalita == "ipotetico"
    if _modalita(event) == "ipotetico":
        return "IPOTETICO"
        
    # Handle modalizzato as a condition independent of modalita
    if event.modalizzato:
        return "NON_FATTUALE"
    
    if event.polarita_negata:
        return "NON_FATTUALE"
    
    # Check for volitivo and deontico modalita - these should be NON_FATTUALE
    if _modalita(event) in ("volitivo", "deontico"):
        return "NON_FATTUALE"
        
    if event.frase_tipo in _NON_FATTUALE_FRASI:
        return "NON_FATTUALE"
    
    if event.tempo == "futuro":
        return "NON_FATTUALE"
        
    # Handle the special case for non_fattivo verbs with completiva_di  
    # If an event has a real parent and it's syntactically embedded, check conditions
    if (
        event.completiva_di is not None
        and event.classe_verbo_reggente == "non_fattivo"
    ):
        return "NON_FATTUALE"
    
    # Handle the case for fattivo verbs with completiva_di 
    # If an embedded event has classe_verbo_reggente="fattivo", it should be FATTUALE
    if (
        event.completiva_di is not None
        and event.classe_verbo_reggente == "fattivo"
    ):
        return "FATTUALE"
    
    # Now check realized assertions  
    if _is_realized_assertion(event):
        # If we have parent information, this might be syntactically embedded
        if parent_of is not None:
            key = _key(event)
            parent = parent_of.get(key)
            if parent is not None and getattr(parent, 'classe_verbo_reggente', None) == "non_fattivo":
                # If parent has classe_verbo_reggente="non_fattivo", embedded events should be NON_FATTUALE
                return "NON_FATTUALE"
        return "FATTUALE"
    
    if event.finale:
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
        # Apply inheritance only when:
        # 1. Parent exists and is inherited (NON_FATTUALE or IPOTETICO) 
        # AND
        # 2. Current event is NOT realized by _is_realized_assertion() OR the current event has special non-fattualità attributes  
        if (
            parent is not None
            and parent.fattualita in _INHERITED
            and not _is_realized_assertion(current, has_real_parent=True)
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
    
    # Compute by_indice and parent_of before any fattualita calculation
    by_indice = _index_parents(eventi, factsheet)
    parent_of: dict[object, EventoRisolto] = {}
    children: dict[object, list[EventoRisolto]] = defaultdict(list)
    
    for event in eventi:
        parent = _resolve_parent(event, by_indice)
        if parent is None:
            continue
        parent_of[_key(event)] = parent
        children[_key(parent)].append(event)

    # First pass: calculate local fattualita without inheritance
    for event in eventi:
        event.fattualita = _local_fattualita(event, parent_of)

    # Apply inheritance logic with the information about real parents
    _inherit_down(eventi, parent_of, children)
    _assign_fonte(eventi, parent_of)
    return eventi


__all__ = ["applica"]