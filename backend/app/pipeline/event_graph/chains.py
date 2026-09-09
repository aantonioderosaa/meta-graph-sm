"""§11 — chain properties on :Evento, heads, bifurcation.

STESSO_EVENTO / AGGIORNA / CONTRADDICE are node traits, not :Relation arcs.
"""

from __future__ import annotations

import inspect
import json
from collections import defaultdict
from typing import Any

from app.models.event_graph import ArcoEvento, EventoRisolto, SottoGrafo, _lemma_norm
from app.pipeline.event_graph.event_coref import (
    EsitoCoref,
    _fattualita_diverge,
    _polarita_diverge,
)
from app.pipeline.event_graph.ids import content_hash

CHAIN_TIPI = frozenset({"STESSO_EVENTO", "AGGIORNA", "CONTRADDICE"})
REGOLA = "chains.applica"

_FUSIONE_CYPHER = (
    "MATCH (a:Evento {id: $id_a}), (b:Evento {id: $id_b}) "
    "WITH a, b, "
    "CASE "
    "WHEN coalesce(a.posizione_doc, 0) < coalesce(b.posizione_doc, 0) THEN a "
    "WHEN coalesce(a.posizione_doc, 0) > coalesce(b.posizione_doc, 0) THEN b "
    "WHEN coalesce(a.posizione_chunk, 0) < coalesce(b.posizione_chunk, 0) THEN a "
    "WHEN coalesce(a.posizione_chunk, 0) > coalesce(b.posizione_chunk, 0) THEN b "
    "WHEN a.id < b.id THEN a ELSE b END AS canonical, "
    "CASE "
    "WHEN coalesce(a.posizione_doc, 0) < coalesce(b.posizione_doc, 0) THEN b "
    "WHEN coalesce(a.posizione_doc, 0) > coalesce(b.posizione_doc, 0) THEN a "
    "WHEN coalesce(a.posizione_chunk, 0) < coalesce(b.posizione_chunk, 0) THEN b "
    "WHEN coalesce(a.posizione_chunk, 0) > coalesce(b.posizione_chunk, 0) THEN a "
    "WHEN a.id < b.id THEN b ELSE a END AS loser "
    "SET loser.fuso_in = canonical.id"
)

_CATENA_SET_CYPHER = (
    "MATCH (old:Evento {id: $old_id}) "
    "MATCH (new:Evento {id: $new_id}) "
    "SET new.catena_id = coalesce(old.catena_id, $catena_id), "
    "new.catena_ruolo = $ruolo, "
    "new.catena_precedente_id = old.id, "
    "new.catena_divergenze = $divergenze, "
    "old.catena_id = coalesce(old.catena_id, $catena_id)"
)


def _sogg_id_canonico(evento: EventoRisolto) -> str | None:
    for arg in evento.argomenti:
        if arg.ruolo == "SOGG" and arg.menzione_id:
            return arg.menzione_id
    return None


def catena_id_per(evento: EventoRisolto) -> str | None:
    """Content-addressed chain key: lemma_norm|sogg_menzione_id."""
    lemma = _lemma_norm(evento.lemma)
    sogg = _sogg_id_canonico(evento)
    if not lemma or not sogg:
        return None
    return content_hash(f"{lemma}|{sogg}")


def divergenze_di(
    tipo: str | None,
    nuovo: EventoRisolto | None = None,
    candidato: EventoRisolto | None = None,
) -> list[str]:
    if tipo == "STESSO_EVENTO":
        return []
    if tipo == "AGGIORNA":
        return ["argomenti"]
    if tipo == "CONTRADDICE":
        out: list[str] = []
        if nuovo is not None and candidato is not None:
            if _polarita_diverge(nuovo, candidato):
                out.append("polarita")
            if _fattualita_diverge(nuovo, candidato):
                out.append("fattualita")
        if not out:
            out.append("polarita")
        return out
    return []


def assegna_catena(
    nuovo: EventoRisolto,
    candidato: EventoRisolto,
    catena_tipo: str,
) -> str | None:
    """Set catena_* on the newer node. Fill candidato.catena_id if missing."""
    cid = candidato.catena_id or nuovo.catena_id or catena_id_per(candidato) or catena_id_per(nuovo)
    if cid and not candidato.catena_id:
        candidato.catena_id = cid
    nuovo.catena_id = cid
    nuovo.catena_ruolo = catena_tipo  # type: ignore[assignment]
    nuovo.catena_precedente_id = candidato.id
    nuovo.catena_divergenze = divergenze_di(catena_tipo, nuovo, candidato)
    return cid


def applica(sotto: SottoGrafo, evento: EventoRisolto, esito: EsitoCoref | None) -> None:
    """Apply intra-document coref outcome. None is a no-op."""
    if esito is None:
        return
    if esito.kind == "Fusione":
        _applica_fusione(sotto, evento, esito)
        return
    if esito.kind == "Successione":
        return
    if esito.kind == "Catena" and esito.catena_tipo in CHAIN_TIPI:
        _applica_catena(sotto, evento, esito)


async def applica_persistente(
    session: Any,
    evento: EventoRisolto,
    esito: EsitoCoref | None,
) -> None:
    """Cross-document write of the same three outcomes. No node removal."""
    if esito is None:
        return
    if esito.kind == "Fusione":
        await _run(
            session,
            _FUSIONE_CYPHER,
            {"id_a": esito.nuovo_id, "id_b": esito.candidato_id},
        )
        return
    if esito.kind == "Successione":
        return
    if esito.kind != "Catena" or esito.catena_tipo not in CHAIN_TIPI:
        return
    old_id, new_id = esito.candidato_id, esito.nuovo_id
    cid = evento.catena_id or catena_id_per(evento)
    ruolo = esito.catena_tipo
    divergenze = divergenze_di(ruolo, evento, None)
    evento.catena_id = cid
    evento.catena_ruolo = ruolo
    evento.catena_precedente_id = old_id
    evento.catena_divergenze = divergenze
    await _run(
        session,
        _CATENA_SET_CYPHER,
        {
            "old_id": old_id,
            "new_id": new_id,
            "catena_id": cid,
            "ruolo": ruolo,
            "divergenze": json.dumps(divergenze, ensure_ascii=False),
        },
    )


def teste(sotto: SottoGrafo) -> list[EventoRisolto]:
    """Heads of each catena_id group: the min-position occurrence."""
    groups: dict[str, list[EventoRisolto]] = defaultdict(list)
    for event in sotto.eventi:
        if event.fuso_in or not event.catena_id:
            continue
        groups[event.catena_id].append(event)
    return [min(members, key=_pos_key) for members in groups.values()]


def biforcazioni(sotto: SottoGrafo) -> list[EventoRisolto]:
    """Origins of ≥2 diverging lines inside a catena_id group."""
    children: dict[str, list[EventoRisolto]] = defaultdict(list)
    for event in sotto.eventi:
        if event.fuso_in or not event.catena_precedente_id:
            continue
        children[event.catena_precedente_id].append(event)
    out: list[EventoRisolto] = []
    for event in sotto.eventi:
        if event.fuso_in:
            continue
        if len(children.get(event.id, ())) >= 2:
            out.append(event)
    return out


testa = teste
biforcazione = biforcazioni


def _applica_fusione(
    sotto: SottoGrafo,
    evento: EventoRisolto,
    esito: EsitoCoref,
) -> None:
    nuovo = _trova(sotto, esito.nuovo_id, evento)
    candidato = _trova(sotto, esito.candidato_id, None)
    if nuovo is None:
        nuovo = evento
    if candidato is None:
        canonical, loser = None, nuovo
        if nuovo.id == esito.candidato_id:
            loser.fuso_in = esito.nuovo_id
            canonical_id = esito.nuovo_id
        else:
            loser.fuso_in = esito.candidato_id
            canonical_id = esito.candidato_id
    else:
        canonical, loser = _canone(nuovo, candidato)
        loser.fuso_in = canonical.id
        canonical_id = canonical.id
        if evento.id == loser.id:
            evento.fuso_in = canonical_id
    _redirect_archi(sotto, loser.id, canonical_id)


def _applica_catena(
    sotto: SottoGrafo,
    evento: EventoRisolto,
    esito: EsitoCoref,
) -> None:
    tipo = esito.catena_tipo
    if tipo is None:
        return
    nuovo = _trova(sotto, esito.nuovo_id, evento)
    candidato = _trova(sotto, esito.candidato_id, None)
    if nuovo is None:
        nuovo = evento
    if candidato is None:
        stub = EventoRisolto(id=esito.candidato_id)
        old, new = stub, nuovo
    else:
        old, new = _ordine(candidato, nuovo)
    assegna_catena(new, old, tipo)
    if evento.id == new.id:
        evento.catena_id = new.catena_id
        evento.catena_ruolo = new.catena_ruolo
        evento.catena_precedente_id = new.catena_precedente_id
        evento.catena_divergenze = list(new.catena_divergenze)


def _pos_key(evento: EventoRisolto) -> tuple[int, int, str]:
    return (
        evento.posizione_doc if evento.posizione_doc is not None else 0,
        evento.posizione_chunk if evento.posizione_chunk is not None else 0,
        evento.id or "",
    )


def _ordine(left: EventoRisolto, right: EventoRisolto) -> tuple[EventoRisolto, EventoRisolto]:
    if _pos_key(left) <= _pos_key(right):
        return left, right
    return right, left


def _canone(left: EventoRisolto, right: EventoRisolto) -> tuple[EventoRisolto, EventoRisolto]:
    old, new = _ordine(left, right)
    return old, new


def _trova(
    sotto: SottoGrafo | None,
    event_id: str,
    fallback: EventoRisolto | None,
) -> EventoRisolto | None:
    if sotto is not None:
        for event in sotto.eventi:
            if event.id == event_id:
                return event
    if fallback is not None and fallback.id == event_id:
        return fallback
    return None


def _redirect_archi(sotto: SottoGrafo, loser_id: str, canonical_id: str) -> None:
    kept: list[ArcoEvento] = []
    for arco in sotto.archi:
        da_id = canonical_id if arco.da_id == loser_id else arco.da_id
        a_id = canonical_id if arco.a_id == loser_id else arco.a_id
        if da_id == a_id:
            continue
        arco.da_id = da_id
        arco.a_id = a_id
        kept.append(arco)
    sotto.archi[:] = kept


async def _run(session: Any, query: str, params: dict[str, Any]) -> Any:
    raw = session.run(query, params)
    if inspect.isawaitable(raw):
        raw = await raw
    if hasattr(raw, "data"):
        rows = raw.data()
        if inspect.isawaitable(rows):
            rows = await rows
        return rows
    return raw


__all__ = [
    "CHAIN_TIPI",
    "REGOLA",
    "applica",
    "applica_persistente",
    "assegna_catena",
    "biforcazione",
    "biforcazioni",
    "catena_id_per",
    "divergenze_di",
    "testa",
    "teste",
]
