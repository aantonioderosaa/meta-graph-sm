"""Static event-graph legend (piano 15.5) plus live stats/graph helpers.

``catalogo()`` is the single source of truth: closed vocabs from
``app.models.event_graph`` Literals, never from the database.
"""

from __future__ import annotations

import inspect
import json
from typing import Any, get_args

from app.models.event_graph import (
    Fattualita,
    Modalita,
    PianoNarrativo,
    TempoVerbale,
    TipoRelazione,
)

_ARGOMENTALI = ("SOGG", "OGG", "OBL", "TEMPO", "LUOGO", "MODO")
_DIZIONARIO = (
    "CAUSA",
    "PRECEDE",
    "LIMITE",
    "CONDIZIONE",
    "SCOPO",
    "CONCESSIONE",
    "CONTRASTO",
    "SEQUENZA",
    "CONTENUTO",
)
_CATENA = ("STESSO_EVENTO", "AGGIORNA", "CONTRADDICE")
_CHAIN_TIPI = frozenset(_CATENA)
_TEMPORALE = ("PRECEDE",)
_PLACEHOLDER = ("COLLEGATO",)
_STRUTTURA = ("SATELLITE_DI",)

_DIR_EVENTO_MENZIONE = "Evento→Menzione"
_DIR_EVENTO_EVENTO = "Evento→Evento"

_SIGNIFICATO: dict[tuple[str, str], str] = {
    ("argomentali", "SOGG"): "soggetto dell'evento",
    ("argomentali", "OGG"): "oggetto dell'evento",
    ("argomentali", "OBL"): "argomento obliquo (preposizione)",
    ("argomentali", "TEMPO"): "circostanza temporale",
    ("argomentali", "LUOGO"): "circostanza spaziale",
    ("argomentali", "MODO"): "circostanza di modo",
    ("dizionario", "CAUSA"): "relazione causale fra eventi",
    ("dizionario", "PRECEDE"): "ordine temporale da dizionario (connettivo)",
    ("dizionario", "LIMITE"): "limite temporale o condizionale",
    ("dizionario", "CONDIZIONE"): "condizione dell'evento dipendente",
    ("dizionario", "SCOPO"): "finalità dell'evento dipendente",
    ("dizionario", "CONCESSIONE"): "concessione rispetto all'evento principale",
    ("dizionario", "CONTRASTO"): "contrasto fra due eventi",
    ("dizionario", "SEQUENZA"): "passo della spina dorsale narrativa",
    ("dizionario", "CONTENUTO"): "contenuto di un evento di dire/pensare",
    ("temporale", "PRECEDE"): (
        "ordine cronologico (dato_esplicito / riferimento_testuale / "
        "connettivo / trapassato)"
    ),
    ("placeholder", "COLLEGATO"): (
        "segnale d'ordine debole (ordine_menzione / ordine_ingestione)"
    ),
    ("struttura", "SATELLITE_DI"): "SFONDO agganciato al PRIMO_PIANO",
}


def _arco(tipo: str, famiglia: str, direzione: str) -> dict[str, str]:
    return {
        "tipo": tipo,
        "famiglia": famiglia,
        "direzione": direzione,
        "significato": _SIGNIFICATO[(famiglia, tipo)],
    }


def catalogo() -> dict:
    """nodes, traits, arches grouped for the legend."""
    return {
        "nodes": [
            {"id": "Evento", "label": "Evento", "shape": "pieno"},
            {"id": "Menzione", "label": "Menzione", "shape": "ovale"},
            {"id": "Quarantena", "label": "Quarantena", "shape": "tratteggiato"},
        ],
        "traits": {
            "tempo": list(get_args(TempoVerbale)),
            "polarita": ["affermata", "negata"],
            "modalizzato": [False, True],
            "modalita": list(get_args(Modalita)),
            "iterativita": [False, True],
            "fattualita": list(get_args(Fattualita)),
            "piano": list(get_args(PianoNarrativo)),
            "fonte": ["NARRATORE | menzione"],
            "catena": {
                "ruoli": list(_CATENA),
                "significato": {
                    "STESSO_EVENTO": "stessa occorrenza (vecchio→nuovo)",
                    "AGGIORNA": "aggiornamento dello stesso evento",
                    "CONTRADDICE": "contraddizione di polarità o fattualità",
                },
            },
        },
        "arches": {
            "argomentali": [
                _arco(tipo, "argomentali", _DIR_EVENTO_MENZIONE)
                for tipo in _ARGOMENTALI
            ],
            "dizionario": [
                _arco(tipo, "dizionario", _DIR_EVENTO_EVENTO) for tipo in _DIZIONARIO
            ],
            "temporale": [
                _arco(tipo, "temporale", _DIR_EVENTO_EVENTO) for tipo in _TEMPORALE
            ],
            "placeholder": [
                _arco(tipo, "placeholder", _DIR_EVENTO_EVENTO) for tipo in _PLACEHOLDER
            ],
            "struttura": [
                _arco(tipo, "struttura", _DIR_EVENTO_EVENTO) for tipo in _STRUTTURA
            ],
        },
    }


def _as_mapping(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        data = dict(row)
    else:
        data_fn = getattr(row, "data", None)
        if callable(data_fn):
            try:
                dumped = data_fn()
            except TypeError:
                dumped = None
            if isinstance(dumped, dict):
                data = dict(dumped)
            else:
                data = {}
        else:
            data = {}
        if not data:
            keys = getattr(row, "keys", None)
            if callable(keys):
                data = {key: row[key] for key in keys()}
    nested = data.get("e") or data.get("n") or data.get("r")
    if isinstance(nested, dict):
        data = {**nested, **{k: v for k, v in data.items() if k not in {"e", "n", "r"}}}
    return data


def _first_int(record: Any) -> int:
    mapping = _as_mapping(record)
    for key in ("n", "count(e)", "count(m)", "count(q)", "count(*)"):
        if key in mapping and mapping[key] is not None:
            return int(mapping[key])
    for value in mapping.values():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return int(value)
    if record is not None and not mapping:
        try:
            return int(record[0])
        except (TypeError, IndexError, KeyError, ValueError):
            return 0
    return 0


def _is_fused(mapping: dict[str, Any]) -> bool:
    fuso = mapping.get("fuso_in")
    return bool(fuso)


def _is_edge_row(mapping: dict[str, Any]) -> bool:
    return mapping.get("source") is not None and mapping.get("target") is not None


async def _run(
    session: Any,
    query: str,
    parameters: dict[str, Any] | None = None,
) -> Any:
    params = dict(parameters or {})
    try:
        raw = session.run(query, **params) if params else session.run(query)
    except TypeError:
        try:
            raw = session.run(query, params)
        except TypeError:
            raw = session.run(query, parameters=params)
    if inspect.isawaitable(raw):
        raw = await raw
    return raw


async def _rows(session: Any, query: str, parameters: dict[str, Any] | None = None) -> list:
    raw = await _run(session, query, parameters)
    data_fn = getattr(raw, "data", None)
    if callable(data_fn):
        rows = data_fn()
        if inspect.isawaitable(rows):
            rows = await rows
        return list(rows or [])
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    return [raw]


async def _single(session: Any, query: str, parameters: dict[str, Any] | None = None) -> Any:
    raw = await _run(session, query, parameters)
    single_fn = getattr(raw, "single", None)
    if callable(single_fn):
        record = single_fn()
        if inspect.isawaitable(record):
            record = await record
        return record
    rows = await _rows(session, query, parameters)
    return rows[0] if rows else None


async def stats(session) -> dict:
    """Live counts: nodi / archi / tratti.piano. FakeSession-friendly."""
    n_evento = _first_int(await _single(session, "MATCH (e:Evento) RETURN count(e)"))
    n_menzione = _first_int(await _single(session, "MATCH (m:Menzione) RETURN count(m)"))
    n_quarantena = _first_int(
        await _single(session, "MATCH (q:Quarantena) RETURN count(q)")
    )
    archi = {tipo: 0 for tipo in get_args(TipoRelazione)}
    for row in await _rows(
        session, "MATCH ()-[r]->() RETURN type(r) AS t, count(r) AS n"
    ):
        mapping = _as_mapping(row)
        tipo = mapping.get("t") or mapping.get("type(r)")
        if not tipo:
            continue
        archi[str(tipo)] = _first_int(mapping)
    piano = {nome: 0 for nome in get_args(PianoNarrativo)}
    for row in await _rows(
        session, "MATCH (e:Evento) RETURN e.piano AS p, count(*) AS n"
    ):
        mapping = _as_mapping(row)
        key = mapping.get("p") or mapping.get("e.piano")
        if not key:
            continue
        piano[str(key)] = _first_int(mapping)
    return {
        "nodi": {
            "Evento": n_evento,
            "Menzione": n_menzione,
            "Quarantena": n_quarantena,
        },
        "archi": archi,
        "tratti": {"piano": piano},
    }


_NODES_CYPHER = (
    "MATCH (e:Evento) "
    "WHERE (e.fuso_in IS NULL OR e.fuso_in = '') "
    "AND ($documento IS NULL OR e.documento = $documento) "
    "AND ($piano IS NULL OR e.piano = $piano) "
    "AND ($lemma IS NULL OR e.lemma = $lemma) "
    "RETURN e.id AS id, e.lemma AS label, 'Evento' AS tipo, e.piano AS piano, "
    "e.fattualita AS fattualita, e.documento AS documento, e.fuso_in AS fuso_in "
    "UNION ALL "
    "MATCH (m:Menzione) "
    "WHERE ($documento IS NULL OR m.documento = $documento) "
    "RETURN m.id AS id, coalesce(m.forma, m.forma_canonica, m.id) AS label, "
    "'Menzione' AS tipo, null AS piano, null AS fattualita, "
    "m.documento AS documento, null AS fuso_in "
    "UNION ALL "
    "MATCH (q:Quarantena) "
    "WHERE ($documento IS NULL OR q.ancora_doc = $documento) "
    "RETURN q.id AS id, coalesce(q.frammento, q.id) AS label, "
    "'Quarantena' AS tipo, null AS piano, null AS fattualita, "
    "q.ancora_doc AS documento, null AS fuso_in"
)

_EDGES_CYPHER = (
    "MATCH (a)-[r]->(b) "
    # Both endpoints must be one of the labels _NODES_CYPHER returns
    # (Evento/Menzione/Quarantena). Without this, any edge whose endpoints
    # are a label this view doesn't query for (e.g. :Zona<->:Zona macro arcs,
    # Addendum 2 M2) still comes back here with a source/target id that has
    # no matching node in the nodes list — Cytoscape then refuses to mount
    # ("nonexistant source"). This view is the MICRO event graph only; macro
    # zone arcs belong to GET /zone, not here.
    "WHERE (a:Evento OR a:Menzione OR a:Quarantena) "
    "AND (b:Evento OR b:Menzione OR b:Quarantena) "
    "AND (NOT a:Evento OR a.fuso_in IS NULL OR a.fuso_in = '') "
    "AND (NOT b:Evento OR b.fuso_in IS NULL OR b.fuso_in = '') "
    "AND ($documento IS NULL OR a.documento = $documento "
    "OR b.documento = $documento) "
    "AND ($piano IS NULL OR a.piano = $piano OR b.piano = $piano) "
    "AND ($lemma IS NULL OR a.lemma = $lemma OR b.lemma = $lemma) "
    "AND NOT type(r) IN ['STESSO_EVENTO', 'AGGIORNA', 'CONTRADDICE'] "
    "RETURN coalesce(r.id, '') AS id, a.id AS source, b.id AS target, "
    "type(r) AS tipo, r.base AS base, r.segnale AS segnale, "
    "r.superato_da AS superato_da, r.conflitto AS conflitto"
)


def _node_element(mapping: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
    if _is_edge_row(mapping) or _is_fused(mapping):
        return None
    node_id = mapping.get("id")
    if not node_id:
        return None
    return {
        "data": {
            "id": str(node_id),
            "label": mapping.get("label") or str(node_id),
            "tipo": mapping.get("tipo") or "Evento",
            "piano": mapping.get("piano"),
            "fattualita": mapping.get("fattualita"),
            "documento": mapping.get("documento"),
        }
    }


def _edge_element(mapping: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
    if not _is_edge_row(mapping):
        return None
    source = mapping.get("source")
    target = mapping.get("target")
    tipo = mapping.get("tipo") or mapping.get("t") or ""
    if tipo in _CHAIN_TIPI:
        return None
    edge_id = mapping.get("id") or f"{tipo}|{source}|{target}"
    return {
        "data": {
            "id": str(edge_id),
            "source": str(source),
            "target": str(target),
            "tipo": tipo,
            "base": mapping.get("base"),
            "segnale": mapping.get("segnale"),
            "superato_da": mapping.get("superato_da"),
            "conflitto": mapping.get("conflitto"),
        }
    }


async def grafo(
    session,
    *,
    documento: str | None = None,
    piano: str | None = None,
    lemma: str | None = None,
) -> dict:
    """Cytoscape/NVL subgraph. Ignores fused events (``fuso_in`` set)."""
    params = {"documento": documento, "piano": piano, "lemma": lemma}
    nodes: list[dict[str, dict[str, Any]]] = []
    seen_nodes: set[str] = set()
    for row in await _rows(session, _NODES_CYPHER, params):
        element = _node_element(_as_mapping(row))
        if element is None:
            continue
        node_id = element["data"]["id"]
        if node_id in seen_nodes:
            continue
        seen_nodes.add(node_id)
        nodes.append(element)
    edges: list[dict[str, dict[str, Any]]] = []
    seen_edges: set[str] = set()
    for row in await _rows(session, _EDGES_CYPHER, params):
        element = _edge_element(_as_mapping(row))
        if element is None:
            continue
        # Belt-and-suspenders, independent of the Cypher WHERE above: never
        # hand Cytoscape an edge whose endpoint isn't in `nodes` — it throws
        # ("Can not create edge ... with nonexistant source") and the whole
        # panel fails to mount. Catches this regardless of *why* the two
        # queries diverged (wrong label, filter mismatch, future label added
        # to one query and not the other).
        src, tgt = element["data"]["source"], element["data"]["target"]
        if src not in seen_nodes or tgt not in seen_nodes:
            continue
        edge_id = element["data"]["id"]
        if edge_id in seen_edges:
            continue
        seen_edges.add(edge_id)
        edges.append(element)
    return {"elements": {"nodes": nodes, "edges": edges}}


_NODO_CYPHER = "MATCH (n {id: $id}) RETURN properties(n) AS props, labels(n) AS labels"

_CATENA_OCCORRENZE_CYPHER = (
    "MATCH (e:Evento {id: $id}) "
    "MATCH (o:Evento {catena_id: e.catena_id}) "
    "OPTIONAL MATCH (o)-[:SOGG]->(m:Menzione) "
    "RETURN o, m.forma AS sogg_forma "
    "ORDER BY o.posizione_doc, o.posizione_chunk"
)

_ARCO_CYPHER = (
    "MATCH (a)-[r {id: $id}]->(b) "
    "RETURN properties(r) AS props, type(r) AS tipo, "
    "a.id AS source_id, labels(a) AS source_labels, "
    "coalesce(a.lemma, a.forma, a.frammento, a.riassunto, a.id) AS source_label, "
    "b.id AS target_id, labels(b) AS target_labels, "
    "coalesce(b.lemma, b.forma, b.frammento, b.riassunto, b.id) AS target_label"
)


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return [value]
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
        return [value]
    return []


def _node_map(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    props = getattr(value, "_properties", None)
    if props is not None:
        return dict(props)
    items_fn = getattr(value, "items", None)
    if callable(items_fn):
        try:
            return dict(items_fn())
        except TypeError:
            pass
    return {}


async def dettaglio_nodo(session, node_id: str) -> dict[str, Any] | None:
    """Every property of the node with this id, whatever its label.

    ``properties(n)`` returns the full map as stored — new fields added
    anywhere in the pipeline (e_testa, modalita, tempo_assoluto_revisioni,
    offset_inizio, ...) show up here automatically, no projection to keep in
    sync. ``None`` if no node carries this id.

    For ``:Evento`` with ``catena_id``, also rebuilds the live ``catena``
    payload (occurrences old→new).
    """
    mapping = _as_mapping(await _single(session, _NODO_CYPHER, {"id": node_id}))
    labels = list(mapping.get("labels") or [])
    if not labels:
        return None
    proprieta = dict(mapping.get("props") or {})
    payload: dict[str, Any] = {
        "id": node_id,
        "labels": labels,
        "proprieta": proprieta,
    }
    if "Evento" in labels and proprieta.get("catena_id"):
        payload["catena"] = await _catena_payload(session, node_id, str(proprieta["catena_id"]))
    return payload


async def _catena_payload(session, node_id: str, catena_id: str) -> dict[str, Any]:
    occorrenze: list[dict[str, Any]] = []
    for row in await _rows(session, _CATENA_OCCORRENZE_CYPHER, {"id": node_id}):
        data = _as_mapping(row)
        props = _node_map(data.get("o"))
        if not props:
            props = {k: v for k, v in data.items() if k not in {"sogg_forma", "m.forma", "o"}}
        if not props.get("id"):
            continue
        occorrenze.append(
            {
                "id": props.get("id"),
                "ruolo": props.get("catena_ruolo"),
                "divergenze": _as_str_list(props.get("catena_divergenze")),
                "posizione_doc": props.get("posizione_doc"),
                "posizione_chunk": props.get("posizione_chunk"),
                "ancora": props.get("ancora"),
                "tempo": props.get("tempo"),
                "fattualita": props.get("fattualita"),
                "polarita": props.get("polarita"),
                "documento": props.get("documento"),
                "sogg_forma": data.get("sogg_forma") or data.get("m.forma"),
            }
        )
    return {"catena_id": catena_id, "occorrenze": occorrenze}


async def dettaglio_arco(session, arco_id: str) -> dict[str, Any] | None:
    """Every property of the relationship whose ``id`` property matches.

    Same dynamic-map approach as ``dettaglio_nodo``. ``None`` if no
    relationship carries this id.
    """
    mapping = _as_mapping(await _single(session, _ARCO_CYPHER, {"id": arco_id}))
    tipo = mapping.get("tipo")
    if not tipo:
        return None
    return {
        "id": arco_id,
        "tipo": tipo,
        "proprieta": dict(mapping.get("props") or {}),
        "source": {
            "id": mapping.get("source_id"),
            "labels": list(mapping.get("source_labels") or []),
            "label": mapping.get("source_label"),
        },
        "target": {
            "id": mapping.get("target_id"),
            "labels": list(mapping.get("target_labels") or []),
            "label": mapping.get("target_label"),
        },
    }


__all__ = ["catalogo", "dettaglio_arco", "dettaglio_nodo", "grafo", "stats"]
