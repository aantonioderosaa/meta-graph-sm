"""Deterministic EventQuerySpec → Cypher executor (piano sez. 15.4 / M17).

No LLM, no GDS, no embeddings. Relationship types come from a TipoRelazione
allowlist; user strings are never interpolated as labels.
"""

from __future__ import annotations

import inspect
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, get_args

from app.models.event_graph import EventQuerySpec, TipoRelazione, TraversalKind

ALLOWED_REL_TYPES: frozenset[str] = frozenset(get_args(TipoRelazione))
TRAVERSAL_KINDS: frozenset[str] = frozenset(get_args(TraversalKind))
_CHAIN_TIPI = frozenset({"STESSO_EVENTO", "AGGIORNA", "CONTRADDICE"})

_EVENTO_KEYS = (
    "id",
    "lemma",
    "piano",
    "fattualita",
    "tempo",
    "documento",
    "tempo_assoluto",
)

# Hardcoded traversal fragments — keys are TraversalKind, never user text.
_TRAVERSAL_CLAUSES: dict[str, str] = {
    "catena_di": (
        "MATCH (s:Evento {id: $target}) "
        "WHERE s.catena_id IS NOT NULL AND e.catena_id = s.catena_id"
    ),
    "spina_dorsale_di": (
        "MATCH path = (start:Evento)-[:SEQUENZA|PRECEDE*0..12]-(e) "
        "WHERE start.id = $target AND ALL(r IN relationships(path) WHERE "
        "r.superato_da IS NULL)"
    ),
    "prima_di": (
        "MATCH (start:Evento) WHERE start.id = $target "
        "AND ("
        "e.tempo_assoluto < start.tempo_assoluto "
        "OR EXISTS { MATCH (e)-[r:PRECEDE]->(start) WHERE r.superato_da IS NULL }"
        ") "
        "AND NOT EXISTS { MATCH (e)-[c:COLLEGATO]-(start) WHERE c.superato_da IS NOT NULL }"
    ),
    "dopo_di": (
        "MATCH (start:Evento) WHERE start.id = $target "
        "AND ("
        "e.tempo_assoluto > start.tempo_assoluto "
        "OR EXISTS { MATCH (start)-[r:PRECEDE]->(e) WHERE r.superato_da IS NULL }"
        ") "
        "AND NOT EXISTS { MATCH (e)-[c:COLLEGATO]-(start) WHERE c.superato_da IS NOT NULL }"
    ),
    "vicinato_temporale": (
        "MATCH (start:Evento)-[r:PRECEDE]-(e) "
        "WHERE start.id = $target AND r.superato_da IS NULL"
    ),
}


@dataclass
class QueryResult:
    eventi: list[dict] = field(default_factory=list)
    archi: list[dict] = field(default_factory=list)
    spec: EventQuerySpec | None = None


@dataclass
class StoredQuery:
    id: str
    modo: str
    spec: EventQuerySpec
    n_eventi: int
    ts: str
    risultato: QueryResult | None = None


_HISTORY: list[StoredQuery] = []


def _safe_rel_type(tipo: str) -> str:
    if tipo in _CHAIN_TIPI:
        raise ValueError(
            f"tipo_relazione non ammesso (catena: usa traversal=catena_di): {tipo}"
        )
    if tipo not in ALLOWED_REL_TYPES:
        raise ValueError(f"tipo_relazione non ammesso: {tipo}")
    for allowed in ALLOWED_REL_TYPES:
        if allowed == tipo:
            return allowed
    raise ValueError(f"tipo_relazione non ammesso: {tipo}")


def _finestra_bounds(finestra: Any) -> tuple[str | None, str | None]:
    if finestra is None:
        return None, None
    if isinstance(finestra, dict):
        da = finestra.get("da")
        a = finestra.get("a")
    else:
        da = getattr(finestra, "da", None)
        a = getattr(finestra, "a", None)
    return (str(da) if da else None), (str(a) if a else None)


def compile_cypher(spec: EventQuerySpec) -> tuple[str, dict]:
    """Pure: return (cypher, params). No session."""
    params: dict[str, Any] = {}
    where: list[str] = ["(e.fuso_in IS NULL OR e.fuso_in = '')"]
    extra: list[str] = []

    lemma = getattr(spec, "lemma", None)
    if lemma is not None:
        where.append("e.lemma = $lemma")
        params["lemma"] = lemma

    piano = getattr(spec, "piano", None)
    if piano is not None:
        where.append("e.piano = $piano")
        params["piano"] = piano

    fattualita = getattr(spec, "fattualita", None)
    if fattualita is not None:
        where.append("e.fattualita = $fattualita")
        params["fattualita"] = fattualita

    tempo = getattr(spec, "tempo", None)
    if tempo is not None:
        where.append("e.tempo = $tempo")
        params["tempo"] = tempo

    fonte = getattr(spec, "fonte", None)
    if fonte is not None:
        where.append("e.fonte = $fonte")
        params["fonte"] = fonte

    documento = getattr(spec, "documento", None)
    if documento is not None:
        where.append("e.documento = $documento")
        params["documento"] = documento

    da, a = _finestra_bounds(getattr(spec, "finestra_tempo_assoluto", None))
    if da is not None:
        where.append("e.tempo_assoluto >= $da")
        params["da"] = da
    if a is not None:
        where.append("e.tempo_assoluto <= $a")
        params["a"] = a

    tipo = getattr(spec, "tipo_relazione", None)
    if tipo is not None:
        safe = _safe_rel_type(str(tipo))
        extra.append(f"MATCH (e)-[r:{safe}]->()")

    traversal = getattr(spec, "traversal", None)
    if traversal is not None:
        kind = str(traversal)
        if kind not in TRAVERSAL_KINDS:
            raise ValueError(f"traversal non ammesso: {kind}")
        target = getattr(spec, "traversal_target", None)
        if not target:
            raise ValueError("traversal_target is required when traversal is set")
        params["target"] = target
        extra.append(_TRAVERSAL_CLAUSES[kind])

    parts = [
        "MATCH (e:Evento)",
        "WHERE " + " AND ".join(where),
        *extra,
        (
            "RETURN DISTINCT e.id AS id, e.lemma AS lemma, e.piano AS piano, "
            "e.fattualita AS fattualita, e.tempo AS tempo, e.documento AS documento, "
            "e.tempo_assoluto AS tempo_assoluto"
        ),
    ]
    cypher = " ".join(parts)
    if str(getattr(spec, "traversal", None) or "") == "catena_di":
        cypher += " ORDER BY e.posizione_doc, e.posizione_chunk"
    return cypher, params


def _as_mapping(row: Any) -> dict:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    data_fn = getattr(row, "data", None)
    if callable(data_fn):
        data = data_fn()
        if inspect.isawaitable(data):
            return {}
        if isinstance(data, dict):
            return dict(data)
    items_fn = getattr(row, "items", None)
    if callable(items_fn):
        try:
            return dict(items_fn())
        except TypeError:
            pass
    return {}


def _row_to_evento(row: Any) -> dict:
    data = _as_mapping(row)
    nested = data.get("e")
    if isinstance(nested, dict):
        data = {**nested, **{k: v for k, v in data.items() if k != "e"}}
    out: dict[str, Any] = {}
    for key in _EVENTO_KEYS:
        if key in data:
            out[key] = data[key]
        elif f"e.{key}" in data:
            out[key] = data[f"e.{key}"]
    return out


def _rel_to_dict(item: Any) -> dict:
    if isinstance(item, dict):
        return dict(item)
    props = getattr(item, "_properties", None)
    tipo = getattr(item, "type", None) or getattr(item, "tipo", None)
    if props is not None:
        payload = dict(props)
        if tipo is not None:
            payload.setdefault("tipo", tipo)
        return payload
    if tipo is not None:
        return {"tipo": tipo}
    return {}


def _row_to_archi(row: Any) -> list[dict]:
    data = _as_mapping(row)
    raw = data.get("archi")
    if raw is None:
        raw = data.get("rels")
    if raw is None:
        rel = data.get("r")
        if rel is None:
            rel = data.get("rel")
        raw = [rel] if rel is not None else []
    if not isinstance(raw, list):
        raw = [raw]
    return [_rel_to_dict(item) for item in raw if item is not None]


async def _run(session: Any, query: str, params: dict[str, Any]) -> Any:
    try:
        raw = session.run(query, params)
    except TypeError:
        try:
            raw = session.run(query, **params)
        except TypeError:
            raw = session.run(query, parameters=params)
    if inspect.isawaitable(raw):
        raw = await raw
    data_fn = getattr(raw, "data", None)
    if callable(data_fn):
        rows = data_fn()
        if inspect.isawaitable(rows):
            rows = await rows
        return rows
    return raw


async def esegui(session: Any, spec: EventQuerySpec) -> QueryResult:
    """Run compile_cypher, map rows to {eventi, archi}."""
    cypher, params = compile_cypher(spec)
    rows = await _run(session, cypher, params)
    eventi: list[dict] = []
    archi: list[dict] = []
    seen: set[str] = set()
    for row in rows or []:
        evento = _row_to_evento(row)
        event_id = evento.get("id")
        if evento and (event_id is None or event_id not in seen):
            if event_id is not None:
                seen.add(str(event_id))
            eventi.append(evento)
        archi.extend(_row_to_archi(row))
    return QueryResult(eventi=eventi, archi=archi, spec=spec)


def registra_query(spec: EventQuerySpec, risultato: QueryResult, *, modo: str = "structured") -> StoredQuery:
    """Append to in-process history; return record with id, modo, spec, n_eventi, ts."""
    n_eventi = len(getattr(risultato, "eventi", None) or [])
    record = StoredQuery(
        id=str(uuid.uuid4()),
        modo=modo,
        spec=spec,
        n_eventi=n_eventi,
        ts=datetime.now(timezone.utc).isoformat(),
        risultato=risultato,
    )
    _HISTORY.append(record)
    return record


def elenca_query() -> list[StoredQuery]:
    return list(_HISTORY)


def get_query(qid: str) -> StoredQuery | None:
    for record in _HISTORY:
        if record.id == qid:
            return record
    return None


def reset_query_history() -> None:
    _HISTORY.clear()


__all__ = [
    "ALLOWED_REL_TYPES",
    "QueryResult",
    "StoredQuery",
    "compile_cypher",
    "elenca_query",
    "esegui",
    "get_query",
    "registra_query",
    "reset_query_history",
]
