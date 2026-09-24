"""§10 — event coreference: candidates, separator, Fusione | Successione | Catena."""

from __future__ import annotations

import inspect
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from app.models.event_graph import (
    ArgomentoRisolto,
    EventoRisolto,
    SottoGrafo,
    _filtra_candidati_evento,
    _lemma_norm,
    _sogg_ogg_ids,
)

EsitoKind = Literal["Fusione", "Successione", "Catena"]
CatenaTipo = Literal["STESSO_EVENTO", "AGGIORNA", "CONTRADDICE"]

_ARG_RUOLI = frozenset({"SOGG", "OGG", "OBL", "TEMPO", "LUOGO", "MODO"})

_CANDIDATI_QUERY = (
    "MATCH (e:Fatto) "
    "WHERE toLower(trim(coalesce(e.lemma, ''))) = $lemma "
    "AND e.id <> $id "
    "AND (e.fuso_in IS NULL OR e.fuso_in = '') "
    "OPTIONAL MATCH (e)-[rel:SOGG|OGG]->(m:Menzione) "
    "WITH e, collect(DISTINCT {ruolo: type(rel), menzione_id: m.id}) AS args "
    "WHERE any(item IN args WHERE item.menzione_id IN $sogg_ogg_ids) "
    "RETURN e.id AS id, e.lemma AS lemma, e.tempo AS tempo, "
    "e.polarita AS polarita, e.polarita_negata AS polarita_negata, "
    "e.fattualita AS fattualita, e.piano AS piano, "
    "e.chunk_id AS chunk_id, e.documento AS documento, "
    "e.posizione_doc AS posizione_doc, e.posizione_chunk AS posizione_chunk, "
    "e.fuso_in AS fuso_in, "
    "e.avverbio_temporale_esplicito AS avverbio_temporale_esplicito, "
    "e.connettivo_sequenziale_esplicito AS connettivo_sequenziale_esplicito, "
    "args AS argomenti"
)


@dataclass(frozen=True)
class EsitoCoref:
    kind: EsitoKind
    nuovo_id: str
    candidato_id: str
    catena_tipo: CatenaTipo | None = None


def candidati(
    evento: EventoRisolto,
    pool: Sequence[EventoRisolto],
    *args: Any,
    **kwargs: Any,
) -> list[EventoRisolto]:
    """Identical lemma (not synonym) and overlapping fused SOGG/OGG mention ids.

    Mention-id overlap is the shared-entity helper (candidati_entita);
    the lemma gate and Fusione/Successione/Catena table are unchanged.
    """
    del args, kwargs
    return _filtra_candidati_evento(evento, pool)


def classifica(
    evento: EventoRisolto,
    candidati_list: Sequence[EventoRisolto],
    *,
    sotto: SottoGrafo | None = None,
    intermedi: Sequence[EventoRisolto] | None = None,
) -> EsitoCoref | None:
    """Mandatory mutually exclusive outcome against the nearest previous candidate."""
    if not candidati_list:
        return None
    scelto = _scegli_candidato(evento, candidati_list)
    intermedi_pool = _pool_intermedi(evento, scelto, sotto, intermedi)
    if _stesso_chunk(evento, scelto) and not _ha_separatore(
        evento, scelto, intermedi_pool
    ):
        return EsitoCoref(
            kind="Fusione",
            nuovo_id=evento.id,
            candidato_id=scelto.id,
        )
    if (
        evento.piano == "PRIMO_PIANO"
        and scelto.piano == "PRIMO_PIANO"
        and sotto is not None
        and _sequenza_collegati(evento.id, scelto.id, sotto)
    ):
        return EsitoCoref(
            kind="Successione",
            nuovo_id=evento.id,
            candidato_id=scelto.id,
        )
    return EsitoCoref(
        kind="Catena",
        nuovo_id=evento.id,
        candidato_id=scelto.id,
        catena_tipo=_catena_tipo(evento, scelto),
    )


async def persistente_candidati(session: Any, evento: EventoRisolto) -> list[EventoRisolto]:
    """Same candidate rule against persisted :Fatto rows."""
    params = {
        "id": evento.id,
        "lemma": _lemma_norm(evento.lemma),
        "sogg_ogg_ids": list(_sogg_ogg_ids(evento)),
    }
    rows = await _session_rows(session, _CANDIDATI_QUERY, params)
    ricostruiti: list[EventoRisolto] = []
    for row in rows or []:
        parsed = _evento_from_row(row)
        if parsed is not None:
            ricostruiti.append(parsed)
    return candidati(evento, ricostruiti)


def _pos(evento: EventoRisolto) -> tuple[int, int]:
    return (
        evento.posizione_doc if evento.posizione_doc is not None else 0,
        evento.posizione_chunk if evento.posizione_chunk is not None else 0,
    )


def _scegli_candidato(
    evento: EventoRisolto,
    candidati_list: Sequence[EventoRisolto],
) -> EventoRisolto:
    ev_pos = _pos(evento)
    previous = [item for item in candidati_list if _pos(item) < ev_pos]
    if previous:
        return max(previous, key=lambda item: (_pos(item), item.id))
    return min(
        candidati_list,
        key=lambda item: (_pos_dist(_pos(item), ev_pos), _pos(item), item.id),
    )


def _pos_dist(left: tuple[int, int], right: tuple[int, int]) -> int:
    return abs(left[0] - right[0]) * 1_000_000 + abs(left[1] - right[1])


def _stesso_chunk(left: EventoRisolto, right: EventoRisolto) -> bool:
    return (left.chunk_id or "") == (right.chunk_id or "")


def _pool_intermedi(
    left: EventoRisolto,
    right: EventoRisolto,
    sotto: SottoGrafo | None,
    intermedi: Sequence[EventoRisolto] | None,
) -> list[EventoRisolto]:
    seen: set[str] = set()
    out: list[EventoRisolto] = []
    for source in (intermedi, sotto.eventi if sotto is not None else None):
        if not source:
            continue
        for item in source:
            key = item.id or str(id(item))
            if key in seen or item.id in {left.id, right.id}:
                continue
            seen.add(key)
            out.append(item)
    return out


def _ha_separatore(
    left: EventoRisolto,
    right: EventoRisolto,
    intermedi_pool: Sequence[EventoRisolto],
) -> bool:
    if not _stesso_chunk(left, right):
        return True
    if left.tempo and right.tempo and left.tempo != right.tempo:
        return True
    if left.avverbio_temporale_esplicito or right.avverbio_temporale_esplicito:
        return True
    if left.connettivo_sequenziale_esplicito or right.connettivo_sequenziale_esplicito:
        return True
    lo = min(
        left.posizione_chunk if left.posizione_chunk is not None else 0,
        right.posizione_chunk if right.posizione_chunk is not None else 0,
    )
    hi = max(
        left.posizione_chunk if left.posizione_chunk is not None else 0,
        right.posizione_chunk if right.posizione_chunk is not None else 0,
    )
    chunk = left.chunk_id or ""
    for item in intermedi_pool:
        if (item.chunk_id or "") != chunk:
            continue
        pos = item.posizione_chunk
        if pos is None or not (lo < pos < hi):
            continue
        if item.avverbio_temporale_esplicito or item.connettivo_sequenziale_esplicito:
            return True
    return False


def _sequenza_collegati(left_id: str, right_id: str, sotto: SottoGrafo) -> bool:
    if left_id == right_id:
        return True
    graph: dict[str, set[str]] = defaultdict(set)
    for arco in sotto.archi:
        if str(arco.tipo) != "SEQUENZA":
            continue
        graph[arco.da_id].add(arco.a_id)
        graph[arco.a_id].add(arco.da_id)
    seen = {left_id}
    stack = [left_id]
    while stack:
        cur = stack.pop()
        for nxt in graph.get(cur, ()):
            if nxt == right_id:
                return True
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return False


def _polarita_diverge(left: EventoRisolto, right: EventoRisolto) -> bool:
    if left.polarita and right.polarita:
        return left.polarita != right.polarita
    return bool(left.polarita_negata) != bool(right.polarita_negata)


def _fattualita_diverge(left: EventoRisolto, right: EventoRisolto) -> bool:
    if left.fattualita is not None and right.fattualita is not None:
        return left.fattualita != right.fattualita
    return False


def _arg_key(arg: ArgomentoRisolto) -> tuple[str, str | None] | tuple[str, str | None, str]:
    if arg.ruolo == "OBL" and arg.preposizione:
        return (arg.ruolo, arg.menzione_id, arg.preposizione)
    return (arg.ruolo, arg.menzione_id)


def _arg_set(evento: EventoRisolto) -> set[tuple]:
    return {_arg_key(arg) for arg in evento.argomenti if arg.ruolo in _ARG_RUOLI}


def _catena_tipo(left: EventoRisolto, right: EventoRisolto) -> CatenaTipo:
    if _polarita_diverge(left, right) or _fattualita_diverge(left, right):
        return "CONTRADDICE"
    if _arg_set(left) != _arg_set(right):
        return "AGGIORNA"
    return "STESSO_EVENTO"


def _evento_from_row(row: Any) -> EventoRisolto | None:
    if isinstance(row, EventoRisolto):
        return row
    if not isinstance(row, dict):
        return None
    if isinstance(row.get("evento"), EventoRisolto):
        return row["evento"]
    data = dict(row)
    nested = data.get("e")
    if isinstance(nested, EventoRisolto):
        return nested
    if isinstance(nested, dict):
        merged = dict(nested)
        for key, value in data.items():
            if key != "e" and key not in merged:
                merged[key] = value
        data = merged
    eid = data.get("id")
    if eid is None:
        eid = data.get("e.id")
    if eid is None:
        return None
    args: list[ArgomentoRisolto] = []
    raw_args = data.get("argomenti") or []
    if isinstance(raw_args, list):
        for item in raw_args:
            if isinstance(item, ArgomentoRisolto):
                if item.menzione_id:
                    args.append(item)
                continue
            if not isinstance(item, dict):
                continue
            ruolo = item.get("ruolo")
            mid = item.get("menzione_id")
            if not ruolo or not mid:
                continue
            args.append(
                ArgomentoRisolto(
                    ruolo=ruolo,
                    menzione_id=str(mid),
                    preposizione=item.get("preposizione"),
                )
            )
    for mid in data.get("sogg_ids") or []:
        args.append(ArgomentoRisolto(ruolo="SOGG", menzione_id=str(mid)))
    for mid in data.get("ogg_ids") or []:
        args.append(ArgomentoRisolto(ruolo="OGG", menzione_id=str(mid)))
    if not args:
        for mid in data.get("mention_ids") or data.get("sogg_ogg_ids") or []:
            if mid:
                args.append(ArgomentoRisolto(ruolo="SOGG", menzione_id=str(mid)))
    return EventoRisolto(
        id=str(eid),
        lemma=str(data.get("lemma") or ""),
        tempo=data.get("tempo"),
        polarita=data.get("polarita"),
        polarita_negata=bool(data.get("polarita_negata") or False),
        fattualita=data.get("fattualita"),
        piano=data.get("piano"),
        documento=data.get("documento"),
        chunk_id=data.get("chunk_id"),
        posizione_doc=data.get("posizione_doc"),
        posizione_chunk=data.get("posizione_chunk"),
        fuso_in=data.get("fuso_in") or None,
        argomenti=args,
        avverbio_temporale_esplicito=bool(
            data.get("avverbio_temporale_esplicito") or False
        ),
        connettivo_sequenziale_esplicito=bool(
            data.get("connettivo_sequenziale_esplicito") or False
        ),
    )


async def _session_rows(session: Any, query: str, params: dict[str, Any]) -> list[Any]:
    raw = session.run(query, params)
    if inspect.isawaitable(raw):
        raw = await raw
    rows = raw.data() if hasattr(raw, "data") else raw
    if inspect.isawaitable(rows):
        rows = await rows
    return list(rows or [])


__all__ = [
    "CatenaTipo",
    "EsitoCoref",
    "EsitoKind",
    "candidati",
    "classifica",
    "persistente_candidati",
]
