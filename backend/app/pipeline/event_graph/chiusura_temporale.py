"""MICRO stage 5 — temporal closure on an Allen constraint network.

Does not replace ``temporal_placement.esegui`` (binary PRECEDE path).
M-flash will wire this into ``espandi_zona``. Append-only: no DELETE, no
SEQUENZA rewrite. Cycle / empty constraint → Q-d (last added, path recorded).
"""

from __future__ import annotations

from collections.abc import Sequence

from app.models.event_graph import (
    ArcoEvento,
    EventoRisolto,
    QuarantenaItem,
    SottoGrafo,
)
from app.pipeline.event_graph import RULESET_VERSION
from app.pipeline.event_graph.allen import (
    ALLEN_13,
    AllenNetwork,
)
from app.pipeline.event_graph.candidati_entita import seleziona_per_entita
from app.pipeline.event_graph.ids import content_hash, quarantena_id
from app.pipeline.event_graph.sentence_pair_linking import testa_della_unita
from app.pipeline.event_graph.temporal_placement import _expand_bounds

REGOLA = "chiusura_temporale"
_ALLEN_FULL = frozenset(ALLEN_13)
_BEFORE_FAMILY = frozenset({"before", "meets"})
_VALID_BASE = frozenset(
    {"connettivo", "dato_esplicito", "riferimento_testuale", "trapassato"}
)


def chiusura_temporale(
    sotto: SottoGrafo,
    unita: list | None = None,
) -> SottoGrafo:
    """Seed Allen constraints, close by path-consistency, materialize singletons."""
    eventi = [event for event in sotto.eventi if event.id and not event.fuso_in]
    if len(eventi) < 2:
        return sotto

    heads = _heads_from_unita(sotto, unita) if unita else _heads_by_posizione(eventi)
    adjacent = {
        frozenset({left.id, right.id})
        for left, right in _consecutive_pairs(heads)
    }
    net = AllenNetwork()
    origin: dict[tuple[str, str], str] = {}

    for i, j, rels, base, kind in _seed_items(
        sotto, eventi, adjacent, heads
    ):
        _try_constraint(sotto, net, origin, i, j, rels, base, kind)

    _materialize(sotto, net, origin)
    _collegato_ordine_menzione(sotto, heads)
    return sotto


def _seed_items(
    sotto: SottoGrafo,
    eventi: list[EventoRisolto],
    adjacent: set[frozenset[str]],
    heads: list[EventoRisolto],
) -> list[tuple[str, str, set[str], str, str]]:
    items: list[tuple[str, str, set[str], str, str]] = []
    known = {event.id for event in eventi}

    for arco in sotto.archi:
        tipo = str(arco.tipo)
        if tipo not in {"PRECEDE", "LIMITE"}:
            continue
        if arco.props.get("superato_da"):
            continue
        if arco.da_id not in known or arco.a_id not in known:
            continue
        if tipo == "LIMITE":
            rels = {"meets"}
            base = "dato_esplicito"
        else:
            tagged = str(arco.props.get("relazione_allen") or "")
            rels = {tagged} if tagged in _ALLEN_FULL else {"before"}
            raw_base = str(arco.props.get("base") or "")
            base = raw_base if raw_base in _VALID_BASE else "connettivo"
        items.append((arco.da_id, arco.a_id, rels, base, "arco"))

    dated = [event for event in eventi if _expand_bounds(event.tempo_assoluto)]
    for idx, left in enumerate(dated):
        for right in dated[idx + 1 :]:
            if not _pair_allowed(left, right, adjacent):
                continue
            rels = _allen_from_assoluto(left, right)
            if rels is None:
                continue
            items.append((left.id, right.id, rels, "dato_esplicito", "assoluto"))

    for earlier, later in _consecutive_pairs(heads):
        if later.tempo == "trapassato" and earlier.tempo != "trapassato":
            items.append(
                (later.id, earlier.id, {"before"}, "trapassato", "trapassato")
            )

    return items


def _try_constraint(
    sotto: SottoGrafo,
    net: AllenNetwork,
    origin: dict[tuple[str, str], str],
    i: str,
    j: str,
    rels: set[str],
    base: str,
    kind: str,
) -> bool:
    prior = net.path_between(i, j)
    snap = net.snapshot()
    net.add_constraint(i, j, rels)
    if net.path_consistent():
        origin[(i, j)] = base
        return True
    cycle = list(prior or [i, j])
    if cycle[-1] != i:
        cycle = cycle + [i]
    net.restore(snap)
    _quarantena(sotto, cycle, i, j, kind, rels)
    return False


def _quarantena(
    sotto: SottoGrafo,
    cycle: list[str],
    i: str,
    j: str,
    kind: str,
    rels: set[str],
) -> None:
    path = " → ".join(cycle)
    before_like = rels <= _BEFORE_FAMILY or kind in {"mention", "trapassato", "arco"}
    prefix = "ciclo cronologico:" if before_like else "incoerenza allen:"
    motivo = f"{prefix} {path}"
    frammento = f"{i}->{j}"
    if any(
        item.frammento == frammento and item.motivo == motivo
        for item in sotto.quarantena
    ):
        return
    by_id = {event.id: event for event in sotto.eventi}
    left = by_id.get(i)
    right = by_id.get(j)
    doc = ""
    chunk_id = None
    if left is not None:
        doc = left.documento or ""
        chunk_id = left.chunk_id
    if right is not None:
        doc = doc or right.documento or ""
        chunk_id = chunk_id or right.chunk_id
    sotto.quarantena.append(
        QuarantenaItem(
            id=quarantena_id(doc, "chiusura_temporale", frammento, motivo),
            frammento=frammento,
            motivo=motivo,
            ancora_doc=doc or None,
            ancora_chunk=chunk_id,
            ancora_span=frammento,
            versione_regole=RULESET_VERSION,
        )
    )


def _materialize(
    sotto: SottoGrafo,
    net: AllenNetwork,
    origin: dict[tuple[str, str], str],
) -> None:
    """Write PRECEDE only for directly seeded pairs, not transitive closure."""
    for (i, j), base in list(origin.items()):
        rels = net.inferred(i, j)
        if len(rels) != 1:
            continue
        rel = next(iter(rels))
        if base not in _VALID_BASE:
            base = "connettivo"
        if rel == "before":
            _ensure_precede(sotto, i, j, "before", base)
        elif rel == "after":
            _ensure_precede(sotto, j, i, "before", base)
        elif rel == "meets":
            _ensure_precede(sotto, i, j, "meets", base)
        elif rel == "met_by":
            _ensure_precede(sotto, j, i, "meets", base)


def _has_event_event(
    sotto: SottoGrafo, left_id: str, right_id: str, *tipi: str
) -> bool:
    pair = {left_id, right_id}
    wanted = set(tipi)
    return any(
        str(arco.tipo) in wanted
        and {arco.da_id, arco.a_id} == pair
        and not arco.props.get("superato_da")
        for arco in sotto.archi
    )


def _collegato_ordine_menzione(
    sotto: SottoGrafo, heads: Sequence[EventoRisolto]
) -> None:
    """Mention order is a placeholder, never PRECEDE dato_esplicito."""
    for earlier, later in _consecutive_pairs(heads):
        if earlier.id == later.id:
            continue
        if _has_event_event(
            sotto, earlier.id, later.id, "PRECEDE", "COLLEGATO", "SEQUENZA"
        ):
            continue
        rel_id = content_hash(
            f"COLLEGATO|{earlier.id}|{later.id}|ordine_menzione"
        )
        sotto.archi.append(
            ArcoEvento(
                tipo="COLLEGATO",
                da_id=earlier.id,
                a_id=later.id,
                props={
                    "id": rel_id,
                    "segnale": "ordine_menzione",
                    "regola": REGOLA,
                    "versione_regole": RULESET_VERSION,
                },
            )
        )


def _ensure_precede(
    sotto: SottoGrafo,
    da_id: str,
    a_id: str,
    relazione: str,
    base: str,
) -> None:
    existing = [
        arco
        for arco in sotto.archi
        if str(arco.tipo) == "PRECEDE"
        and arco.da_id == da_id
        and arco.a_id == a_id
        and not arco.props.get("superato_da")
    ]
    if existing:
        for arco in existing:
            if not arco.props.get("relazione_allen"):
                arco.props["relazione_allen"] = relazione
        return
    if base not in _VALID_BASE:
        base = "connettivo"
    rel_id = content_hash(f"PRECEDE|{da_id}|{a_id}|{base}|{relazione}")
    sotto.archi.append(
        ArcoEvento(
            tipo="PRECEDE",
            da_id=da_id,
            a_id=a_id,
            props={
                "id": rel_id,
                "base": base,
                "relazione_allen": relazione,
                "regola": REGOLA,
                "versione_regole": RULESET_VERSION,
            },
        )
    )


def _allen_from_assoluto(left: EventoRisolto, right: EventoRisolto) -> set[str] | None:
    bounds_l = _expand_bounds(left.tempo_assoluto)
    bounds_r = _expand_bounds(right.tempo_assoluto)
    if bounds_l is None or bounds_r is None:
        return None
    a_start, a_end = bounds_l
    b_start, b_end = bounds_r
    if a_end < b_start:
        return {"before"}
    if a_start > b_end:
        return {"after"}
    if a_end == b_start:
        return {"meets"}
    if a_start == b_end:
        return {"met_by"}
    if a_start == b_start and a_end == b_end:
        return {"equals"}
    if a_start == b_start and a_end < b_end:
        return {"starts"}
    if a_start == b_start and a_end > b_end:
        return {"started_by"}
    if a_end == b_end and a_start > b_start:
        return {"finishes"}
    if a_end == b_end and a_start < b_start:
        return {"finished_by"}
    if b_start < a_start and a_end < b_end:
        return {"during"}
    if a_start < b_start and b_end < a_end:
        return {"contains"}
    if a_start < b_start < a_end < b_end:
        return {"overlaps"}
    if b_start < a_start < b_end < a_end:
        return {"overlapped_by"}
    return {
        "overlaps",
        "overlapped_by",
        "during",
        "contains",
        "starts",
        "started_by",
        "finishes",
        "finished_by",
        "equals",
    }


def _pair_allowed(
    left: EventoRisolto,
    right: EventoRisolto,
    adjacent: set[frozenset[str]],
) -> bool:
    if frozenset({left.id, right.id}) in adjacent:
        return True
    return bool(seleziona_per_entita(left, [right]))


def _heads_from_unita(sotto: SottoGrafo, unita: list) -> list[EventoRisolto]:
    heads: list[EventoRisolto] = []
    seen: set[str] = set()
    for unit in unita:
        head = testa_della_unita(unit, sotto)
        if head is None or not head.id or head.id in seen:
            continue
        seen.add(head.id)
        heads.append(head)
    return heads


def _heads_by_posizione(eventi: Sequence[EventoRisolto]) -> list[EventoRisolto]:
    teste = [event for event in eventi if event.e_testa and not event.fuso_in]
    pool = teste or [event for event in eventi if not event.fuso_in]
    return sorted(pool, key=_pos_key)


def _consecutive_pairs(
    heads: Sequence[EventoRisolto],
) -> list[tuple[EventoRisolto, EventoRisolto]]:
    out: list[tuple[EventoRisolto, EventoRisolto]] = []
    for idx in range(len(heads) - 1):
        left, right = heads[idx], heads[idx + 1]
        if _pos_key(left) <= _pos_key(right):
            out.append((left, right))
        else:
            out.append((right, left))
    return out


def _pos_key(evento: EventoRisolto) -> tuple[int, int, int, str]:
    return (
        evento.posizione_doc if evento.posizione_doc is not None else 0,
        evento.posizione_chunk if evento.posizione_chunk is not None else 0,
        evento.offset_inizio if evento.offset_inizio is not None else 0,
        evento.id or "",
    )


__all__ = ["REGOLA", "chiusura_temporale"]
