"""§12 — buona formazione → :Quarantena (piano sez. 9, eccezione punto 5).

``valida`` filters ``eventi`` / ``archi`` in place and returns quarantena items.
Traits are never rewritten: only drop, or append one ``COLLEGATO`` (punto 5).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.models.event_graph import (
    ArcoEvento,
    EventoRisolto,
    QuarantenaItem,
)
from app.pipeline.event_graph import RULESET_VERSION
from app.pipeline.event_graph.ids import quarantena_id

EVENT_EVENT_TIPI = frozenset(
    {
        "CAUSA",
        "PRECEDE",
        "LIMITE",
        "CONDIZIONE",
        "SCOPO",
        "CONCESSIONE",
        "CONTRASTO",
        "SEQUENZA",
        "CONTENUTO",
        "COLLEGATO",
        "SATELLITE_DI",
    }
)

_MOTIVO_ANCORA = "ancora assente"
_MOTIVO_SOGG = "sogg assente"
_MOTIVO_FUORI = "FUORI_LINEA in spina"
_MOTIVO_CAUSA = "ciclo CAUSA"
_MOTIVO_ISOLAMENTO = "isolamento"


def valida(
    eventi: list[EventoRisolto],
    archi: list[ArcoEvento],
    factsheet: Any = None,
    testo_chunk: str = "",
) -> list[QuarantenaItem]:
    """Drop ill-formed events/arcs in place; return quarantena items.

    ``archi`` may be a bare list or an object with an ``.archi`` list
    (``EventEdgesResult``) so ``valida(ev, ee, fs)`` mutates the live arcs.
    """
    del factsheet
    working_archi = _as_archi_list(archi)
    quarantena: list[QuarantenaItem] = []
    originals = list(eventi)
    order = {event.id: index for index, event in enumerate(originals)}

    _drop_events(
        eventi,
        working_archi,
        quarantena,
        testo_chunk,
        [event for event in list(eventi) if not _has_ancora(event)],
        _MOTIVO_ANCORA,
    )
    _drop_events(
        eventi,
        working_archi,
        quarantena,
        testo_chunk,
        [event for event in list(eventi) if not _has_sogg(event)],
        _MOTIVO_SOGG,
    )
    _drop_events(
        eventi,
        working_archi,
        quarantena,
        testo_chunk,
        _sfondo_unattached(eventi, working_archi),
        "SFONDO non agganciato",
    )

    by_id = {event.id: event for event in eventi}
    keep_archi: list[ArcoEvento] = []
    for arco in working_archi:
        if arco.tipo == "SEQUENZA" and _sequenza_on_fuori_linea(arco, by_id):
            source = _fuori_endpoint(arco, by_id) or by_id.get(arco.da_id)
            if source is not None:
                quarantena.append(_item(source, _MOTIVO_FUORI, testo_chunk))
            else:
                quarantena.append(
                    _arc_item(arco, _MOTIVO_FUORI, testo_chunk, by_id)
                )
            continue
        keep_archi.append(arco)
    working_archi[:] = keep_archi

    dag_archi: list[ArcoEvento] = []
    causa_kept: list[ArcoEvento] = []
    for arco in working_archi:
        if arco.tipo != "CAUSA":
            dag_archi.append(arco)
            continue
        if _causa_reaches(causa_kept, arco.a_id, arco.da_id):
            source = by_id.get(arco.da_id) or by_id.get(arco.a_id)
            if source is not None:
                quarantena.append(_item(source, _MOTIVO_CAUSA, testo_chunk))
            else:
                quarantena.append(
                    _arc_item(arco, _MOTIVO_CAUSA, testo_chunk, by_id)
                )
            continue
        causa_kept.append(arco)
        dag_archi.append(arco)
    working_archi[:] = dag_archi

    isolated = [
        event
        for event in eventi
        if _is_isolated(event, eventi, working_archi)
    ]
    isolated_ids = {event.id for event in isolated}
    saved_ids: set[str] = set()
    punto5: list[ArcoEvento] = []
    for chunk_key, members in _group_by_chunk(originals).items():
        last = max(members, key=lambda event: _reading_key(event, order))
        last_now = next((event for event in eventi if event.id == last.id), None)
        if last_now is None or last_now.id not in isolated_ids:
            continue
        if not _well_formed(last_now):
            continue
        previous = [
            event
            for event in eventi
            if _chunk_key(event) == chunk_key
            and _reading_key(event, order) < _reading_key(last_now, order)
        ]
        if not previous:
            continue
        previous.sort(key=lambda event: _reading_key(event, order))
        prev = previous[-1]
        punto5.append(
            ArcoEvento(
                tipo="COLLEGATO",
                da_id=prev.id,
                a_id=last_now.id,
                props={
                    "segnale": "ordine_menzione",
                    "regola": "wellformed.punto5",
                },
            )
        )
        saved_ids.add(last_now.id)
        saved_ids.add(prev.id)

    _drop_events(
        eventi,
        working_archi,
        quarantena,
        testo_chunk,
        [
            event
            for event in isolated
            if event.id in {e.id for e in eventi}
            and event.id not in saved_ids
        ],
        _MOTIVO_ISOLAMENTO,
    )
    working_archi.extend(punto5)
    return quarantena


def _as_archi_list(archi: list[ArcoEvento] | Any) -> list[ArcoEvento]:
    if isinstance(archi, list):
        return archi
    inner = getattr(archi, "archi", None)
    if isinstance(inner, list):
        return inner
    raise TypeError("archi must be a list[ArcoEvento] or expose .archi")


def _has_ancora(event: EventoRisolto) -> bool:
    return bool(event.ancora and str(event.ancora).strip())


def _mention_ids(event: EventoRisolto) -> set[str]:
    found: set[str] = set()
    for arg in event.argomenti:
        mid = arg.menzione_id
        if mid is not None and str(mid).strip():
            found.add(str(mid).strip())
    return found


def _has_sogg(event: EventoRisolto) -> bool:
    if _mention_ids(event):
        return True
    return event.sogg_speciale != "nessuno"


def _well_formed(event: EventoRisolto) -> bool:
    return _has_ancora(event) and _has_sogg(event)


def _chunk_key(event: EventoRisolto) -> str:
    if event.chunk_id:
        return str(event.chunk_id)
    return f"doc:{event.documento or ''}|pos:{event.posizione_doc}"


def _reading_key(event: EventoRisolto, order: dict[str, int]) -> tuple[int, int]:
    pos = event.posizione_chunk
    if pos is None:
        pos = event.posizione_doc if event.posizione_doc is not None else order.get(
            event.id, 0
        )
    return (int(pos), order.get(event.id, 0))


def _group_by_chunk(
    events: Iterable[EventoRisolto],
) -> dict[str, list[EventoRisolto]]:
    groups: dict[str, list[EventoRisolto]] = {}
    for event in events:
        groups.setdefault(_chunk_key(event), []).append(event)
    return groups


def _sfondo_unattached(
    eventi: list[EventoRisolto],
    archi: list[ArcoEvento],
) -> list[EventoRisolto]:
    by_chunk = _group_by_chunk(eventi)
    pp_ids = {event.id for event in eventi if event.piano == "PRIMO_PIANO"}
    attached = {
        arco.da_id
        for arco in archi
        if arco.tipo == "SATELLITE_DI" and arco.a_id in pp_ids
    }
    drop: list[EventoRisolto] = []
    for members in by_chunk.values():
        if not any(event.piano == "PRIMO_PIANO" for event in members):
            continue
        for event in members:
            if event.piano != "SFONDO":
                continue
            if event.id not in attached:
                drop.append(event)
    return drop


def _sequenza_on_fuori_linea(
    arco: ArcoEvento,
    by_id: dict[str, EventoRisolto],
) -> bool:
    da = by_id.get(arco.da_id)
    a = by_id.get(arco.a_id)
    return (da is not None and da.piano == "FUORI_LINEA") or (
        a is not None and a.piano == "FUORI_LINEA"
    )


def _fuori_endpoint(
    arco: ArcoEvento,
    by_id: dict[str, EventoRisolto],
) -> EventoRisolto | None:
    da = by_id.get(arco.da_id)
    if da is not None and da.piano == "FUORI_LINEA":
        return da
    a = by_id.get(arco.a_id)
    if a is not None and a.piano == "FUORI_LINEA":
        return a
    return None


def _causa_reaches(archi: list[ArcoEvento], start_id: str, goal_id: str) -> bool:
    graph: dict[str, list[str]] = {}
    for arco in archi:
        if arco.tipo == "CAUSA":
            graph.setdefault(arco.da_id, []).append(arco.a_id)
    seen: set[str] = set()
    stack = [start_id]
    while stack:
        node = stack.pop()
        if node == goal_id:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(graph.get(node, []))
    return False


def _is_isolated(
    event: EventoRisolto,
    eventi: list[EventoRisolto],
    archi: list[ArcoEvento],
) -> bool:
    eid = event.id
    for arco in archi:
        if arco.tipo not in EVENT_EVENT_TIPI:
            continue
        if arco.da_id == eid or arco.a_id == eid:
            return False
    mine = _mention_ids(event)
    if mine:
        for other in eventi:
            if other.id == eid:
                continue
            if mine & _mention_ids(other):
                return False
    return True


def _drop_events(
    eventi: list[EventoRisolto],
    archi: list[ArcoEvento],
    quarantena: list[QuarantenaItem],
    testo_chunk: str,
    victims: list[EventoRisolto],
    motivo: str,
) -> None:
    if not victims:
        return
    drop_ids = {event.id for event in victims}
    for event in victims:
        if event.id not in {e.id for e in eventi}:
            continue
        quarantena.append(_item(event, motivo, testo_chunk))
    eventi[:] = [event for event in eventi if event.id not in drop_ids]
    archi[:] = [
        arco
        for arco in archi
        if arco.da_id not in drop_ids and arco.a_id not in drop_ids
    ]


def _item(event: EventoRisolto, motivo: str, testo_chunk: str) -> QuarantenaItem:
    documento = event.documento or ""
    ancora = event.ancora or ""
    lemma = event.lemma or ""
    text_for_id = testo_chunk or ancora or lemma
    span_for_id = ancora or event.id
    return QuarantenaItem(
        id=quarantena_id(documento, text_for_id, span_for_id, motivo),
        frammento=lemma or event.span or "",
        motivo=motivo,
        ancora_doc=event.documento,
        ancora_chunk=event.chunk_id,
        ancora_span=event.ancora,
        versione_regole=RULESET_VERSION,
    )


def _arc_item(
    arco: ArcoEvento,
    motivo: str,
    testo_chunk: str,
    by_id: dict[str, EventoRisolto],
) -> QuarantenaItem:
    source = by_id.get(arco.da_id) or by_id.get(arco.a_id)
    if source is not None:
        return _item(source, motivo, testo_chunk)
    text_for_id = testo_chunk or arco.da_id
    return QuarantenaItem(
        id=quarantena_id("", text_for_id, arco.da_id, motivo),
        frammento=f"{arco.da_id}->{arco.a_id}",
        motivo=motivo,
        versione_regole=RULESET_VERSION,
    )


__all__ = ["EVENT_EVENT_TIPI", "valida"]
