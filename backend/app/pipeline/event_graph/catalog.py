"""Static event-graph legend (piano 15.5) plus live stats/graph helpers.

``catalogo()`` is the single source of truth: closed vocabs from
``app.models.event_graph`` Literals, never from the database.
"""

from __future__ import annotations

import inspect
import json
from typing import Any, get_args

from app.models.event_graph import (
    GRANULARITA_TEMPORALI,
    Fattualita,
    Modalita,
    PianoNarrativo,
    TempoVerbale,
    TipoRelazione,
    TipoRelazioneLibera,
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
_TEMPORALE = ("PRECEDE", "CONTEMPORANEO")
_PLACEHOLDER = ("COLLEGATO",)
_STRUTTURA = ("SATELLITE_DI", "APPARTIENE_A", "SUCCESSIONE_ZONA", "CONTIENE")

_DIR_EVENTO_MENZIONE = "Evento→Menzione"
_DIR_EVENTO_EVENTO = "Evento→Evento"
_DIR_EVENTO_CLUSTER = "Evento→ClusterTemporale"
_DIR_CLUSTER_CLUSTER = "ClusterTemporale→ClusterTemporale"
_DIR_ZONA_ZONA = "Zona→Zona"

# Unique types ``grafo()`` (vista=tutto) can return: Evento/Menzione/Quarantena
# endpoints only, chain types excluded. APPARTIENE_A and SUCCESSIONE_ZONA
# target labels this view never queries.
_TUTTO_NODI = ("Evento", "Menzione", "Quarantena")
_TUTTO_ARCHI = (
    *_ARGOMENTALI,
    *_DIZIONARIO,
    "CONTEMPORANEO",
    *_PLACEHOLDER,
    "SATELLITE_DI",
)
_ORDINE_NODI = ("Zona", "Evento")
_ORDINE_ARCHI = ("SUCCESSIONE_ZONA", "SEQUENZA", "COLLEGATO")
_TEMPORALE_NODI = ("ClusterTemporale", "Evento")
# APPARTIENE_A / CONTIENE assign data.parent; grafo_livello2 does not draw
# them. They still belong in the vista legend (PIANO-LIVELLO-TEMPORALE-V2
# MT7, lines 121–125).
_TEMPORALE_ARCHI = ("PRECEDE", "CONTEMPORANEO", "APPARTIENE_A", "CONTIENE")
_RELAZIONI_NODI = ("Evento",)

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
    ("temporale", "CONTEMPORANEO"): "stesso momento, non direzionale",
    ("placeholder", "COLLEGATO"): (
        "segnale d'ordine debole (ordine_menzione / ordine_ingestione)"
    ),
    ("struttura", "SATELLITE_DI"): "SFONDO agganciato al PRIMO_PIANO",
    ("struttura", "APPARTIENE_A"): "evento appartenente a un cluster temporale",
    ("struttura", "SUCCESSIONE_ZONA"): "successione narrativa fra zone espanse",
    ("struttura", "CONTIENE"): (
        "cluster temporale che ne contiene un altro (foresta)"
    ),
}


def _arco(tipo: str, famiglia: str, direzione: str) -> dict[str, str]:
    return {
        "tipo": tipo,
        "famiglia": famiglia,
        "direzione": direzione,
        "significato": _SIGNIFICATO[(famiglia, tipo)],
    }


def _struttura_direzione(tipo: str) -> str:
    if tipo == "APPARTIENE_A":
        return _DIR_EVENTO_CLUSTER
    if tipo == "CONTIENE":
        return _DIR_CLUSTER_CLUSTER
    if tipo == "SUCCESSIONE_ZONA":
        return _DIR_ZONA_ZONA
    return _DIR_EVENTO_EVENTO


def _viste() -> dict[str, dict[str, Any]]:
    """Per-vista node/edge types matching grafo / grafo_livello1/2/3."""
    return {
        "tutto": {
            "nodi": list(_TUTTO_NODI),
            "archi": list(_TUTTO_ARCHI),
            "significato": (
                "Vista completa: Evento, Menzione e Quarantena con tutti gli "
                "archi micro (argomentali, dizionario, PRECEDE/CONTEMPORANEO, "
                "COLLEGATO, SATELLITE_DI). Nessuna Zona né ClusterTemporale; "
                "SUCCESSIONE_ZONA e APPARTIENE_A restano fuori da questa proiezione."
            ),
        },
        "ordine": {
            "nodi": list(_ORDINE_NODI),
            "archi": list(_ORDINE_ARCHI),
            "significato": (
                "Livello 1: Zona + Evento (compound, parent=zona). "
                "SUCCESSIONE_ZONA (successione narrativa fra zone espanse) "
                "e SEQUENZA/COLLEGATO intra-zona. Nessun CAUSA/CONTRASTO/…"
            ),
        },
        "temporale": {
            "nodi": list(_TEMPORALE_NODI),
            "archi": list(_TEMPORALE_ARCHI),
            "significato": (
                "Livello 2: ClusterTemporale + Evento (compound, parent=cluster). "
                "Archi live PRECEDE (superato_da vuoto) e CONTEMPORANEO "
                "(stesso momento, non direzionale). APPARTIENE_A "
                "(evento appartenente a un cluster temporale) e CONTIENE "
                "(ClusterTemporale→ClusterTemporale, foresta) assegnano il "
                "parent, non sono archi visibili. "
                "granularita è la scala del cluster (secondo…secolo); "
                "stimato=true se inizio è inferito e non dichiarato dal testo."
            ),
        },
        "relazioni": {
            "nodi": list(_RELAZIONI_NODI),
            "archi": list(get_args(TipoRelazioneLibera)),
            "significato": (
                "Livello 3: solo Evento, senza raggruppamento. Archi "
                "TipoRelazioneLibera (CAUSA, CONDIZIONE, SCOPO, CONCESSIONE, "
                "CONTRASTO, LIMITE, CONTENUTO) con livello='3'."
            ),
        },
    }


def catalogo() -> dict:
    """nodes, traits, arches grouped for the legend."""
    return {
        "nodes": [
            {"id": "Evento", "label": "Evento", "shape": "pieno"},
            {"id": "Menzione", "label": "Menzione", "shape": "ovale"},
            {"id": "Quarantena", "label": "Quarantena", "shape": "tratteggiato"},
            {"id": "Zona", "label": "Zona", "shape": "round-rectangle"},
            {
                "id": "ClusterTemporale",
                "label": "ClusterTemporale",
                "shape": "round-rectangle",
            },
        ],
        "traits": {
            "tempo": list(get_args(TempoVerbale)),
            "polarita": ["affermata", "negata"],
            "modalizzato": [False, True],
            "modalita": list(get_args(Modalita)),
            "iterativita": [False, True],
            "fattualita": list(get_args(Fattualita)),
            "piano": list(get_args(PianoNarrativo)),
            "granularita": list(GRANULARITA_TEMPORALI),
            "stimato": [False, True],
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
                _arco(tipo, "struttura", _struttura_direzione(tipo))
                for tipo in _STRUTTURA
            ],
        },
        "viste": _viste(),
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


def _as_confidenza(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _as_int(value: Any) -> int | None:
    """Best-effort int: a malformed property must not break a whole view."""
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool | None:
    """``None`` stays ``None``: "not estimated" and "unknown" differ."""
    if value is None:
        return None
    return bool(value)


def _attach_confidenza(data: dict[str, Any], mapping: dict[str, Any]) -> None:
    confidenza = _as_confidenza(mapping.get("confidenza"))
    if confidenza is not None:
        data["confidenza"] = confidenza


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
    "r.superato_da AS superato_da, r.conflitto AS conflitto, "
    "r.confidenza AS confidenza"
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
    data: dict[str, Any] = {
        "id": str(edge_id),
        "source": str(source),
        "target": str(target),
        "tipo": tipo,
        "base": mapping.get("base"),
        "segnale": mapping.get("segnale"),
        "superato_da": mapping.get("superato_da"),
        "conflitto": mapping.get("conflitto"),
    }
    _attach_confidenza(data, mapping)
    return {"data": data}


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


_L1_EDGE_TIPI = frozenset({"SUCCESSIONE_ZONA", "SEQUENZA", "COLLEGATO"})

# Distinctive comments so FakeSession mapping keys do not collide with
# grafo() Cypher (or with each other: both zone and event queries MATCH Zona).
_L1_ZONES_CYPHER = (
    "/* grafo_livello1_zone */ "
    "MATCH (z:Zona) "
    "WHERE $documento IS NULL OR z.documento = $documento "
    "RETURN z.id AS id, z.ordinale AS ordinale, z.riassunto AS riassunto, "
    "z.evento_centrale AS evento_centrale, z.documento AS documento, "
    "'Zona' AS tipo"
)

_L1_EVENTS_CYPHER = (
    "/* grafo_livello1_eventi */ "
    "MATCH (z:Zona) "
    "MATCH (e:Evento) "
    "WHERE e.chunk_id = z.id "
    "AND (e.fuso_in IS NULL OR e.fuso_in = '') "
    "AND ($documento IS NULL OR z.documento = $documento) "
    "RETURN e.id AS id, e.lemma AS label, e.chunk_id AS parent, "
    "e.documento AS documento, 'Evento' AS tipo, e.fuso_in AS fuso_in, "
    "e.posizione_chunk AS posizione_chunk "
    "ORDER BY z.ordinale, e.posizione_chunk"
)

_L1_SUCCESSIONE_CYPHER = (
    "/* grafo_livello1_successione_zona */ "
    "MATCH (za:Zona)-[r:SUCCESSIONE_ZONA]->(zb:Zona) "
    "WHERE $documento IS NULL OR za.documento = $documento "
    "RETURN coalesce(r.id, '') AS id, za.id AS source, zb.id AS target, "
    "r.riassunto_transizione AS riassunto_transizione, type(r) AS tipo, "
    "r.confidenza AS confidenza"
)

_L1_ORDER_EDGES_CYPHER = (
    "/* grafo_livello1_ordine_eventi */ "
    "MATCH (a:Evento)-[r]->(b:Evento) "
    "WHERE type(r) IN ['SEQUENZA','COLLEGATO'] "
    "AND a.chunk_id = b.chunk_id "
    "AND (a.fuso_in IS NULL OR a.fuso_in = '') "
    "AND (b.fuso_in IS NULL OR b.fuso_in = '') "
    # Livello 1 è la ferrovia pulita: un COLLEGATO che una SEQUENZA ha reso
    # ridondante (persistence.sopprimi_collegato_ridondanti, superato_da
    # marcato) non deve comparire qui — a differenza della vista "tutto",
    # dove resta visibile e attenuata come ogni altro arco superato.
    "AND (type(r) <> 'COLLEGATO' OR r.superato_da IS NULL) "
    "AND ($documento IS NULL OR a.documento = $documento) "
    "RETURN coalesce(r.id, '') AS id, a.id AS source, b.id AS target, "
    "type(r) AS tipo, r.confidenza AS confidenza"
)


def _l1_zona_element(mapping: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
    if _is_edge_row(mapping) or _is_fused(mapping):
        return None
    tipo = mapping.get("tipo")
    if tipo and tipo != "Zona":
        return None
    node_id = mapping.get("id")
    if not node_id:
        return None
    if tipo != "Zona" and mapping.get("ordinale") is None:
        return None
    riassunto = mapping.get("riassunto")
    evento_centrale = mapping.get("evento_centrale")
    return {
        "data": {
            "id": str(node_id),
            "label": riassunto or evento_centrale or str(node_id),
            "tipo": "Zona",
            "ordinale": mapping.get("ordinale"),
            "riassunto": riassunto,
            "evento_centrale": evento_centrale,
        }
    }


def _l1_evento_element(mapping: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
    if _is_edge_row(mapping) or _is_fused(mapping):
        return None
    tipo = mapping.get("tipo")
    if tipo and tipo != "Evento":
        return None
    node_id = mapping.get("id")
    if not node_id:
        return None
    parent = mapping.get("parent") or mapping.get("chunk_id")
    if not parent:
        return None
    return {
        "data": {
            "id": str(node_id),
            "label": mapping.get("label") or mapping.get("lemma") or str(node_id),
            "tipo": "Evento",
            "parent": str(parent),
            "documento": mapping.get("documento"),
            # The preset layout stacks a zone's children in array order, so the
            # vertical order is only meaningful if the query ordered them.
            "posizione_chunk": mapping.get("posizione_chunk"),
        }
    }


def _l1_edge_element(mapping: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
    if not _is_edge_row(mapping):
        return None
    source = mapping.get("source")
    target = mapping.get("target")
    tipo = mapping.get("tipo") or mapping.get("t") or mapping.get("type(r)") or ""
    if tipo not in _L1_EDGE_TIPI:
        return None
    edge_id = mapping.get("id") or f"{tipo}|{source}|{target}"
    data: dict[str, Any] = {
        "id": str(edge_id),
        "source": str(source),
        "target": str(target),
        "tipo": tipo,
    }
    if tipo == "SUCCESSIONE_ZONA":
        data["riassunto_transizione"] = mapping.get("riassunto_transizione")
    _attach_confidenza(data, mapping)
    return {"data": data}


async def grafo_livello1(session, documento: str | None = None) -> dict:
    """Livello 1 (ordine): Zona hubs + intra-zone SEQUENZA/COLLEGATO.

    Compound Cytoscape nodes: Evento ``data.parent`` = Zona.id. No Menzione,
    Quarantena, or ClusterTemporale. No CAUSA/CONTRASTO/… edges.
    """
    params = {"documento": documento}
    nodes: list[dict[str, dict[str, Any]]] = []
    seen_nodes: set[str] = set()
    for row in await _rows(session, _L1_ZONES_CYPHER, params):
        element = _l1_zona_element(_as_mapping(row))
        if element is None:
            continue
        node_id = element["data"]["id"]
        if node_id in seen_nodes:
            continue
        seen_nodes.add(node_id)
        nodes.append(element)
    zona_ids = set(seen_nodes)
    for row in await _rows(session, _L1_EVENTS_CYPHER, params):
        element = _l1_evento_element(_as_mapping(row))
        if element is None:
            continue
        if element["data"].get("parent") not in zona_ids:
            continue
        node_id = element["data"]["id"]
        if node_id in seen_nodes:
            continue
        seen_nodes.add(node_id)
        nodes.append(element)
    edges: list[dict[str, dict[str, Any]]] = []
    seen_edges: set[str] = set()
    for query in (_L1_SUCCESSIONE_CYPHER, _L1_ORDER_EDGES_CYPHER):
        for row in await _rows(session, query, params):
            element = _l1_edge_element(_as_mapping(row))
            if element is None:
                continue
            src, tgt = element["data"]["source"], element["data"]["target"]
            if src not in seen_nodes or tgt not in seen_nodes:
                continue
            edge_id = element["data"]["id"]
            if edge_id in seen_edges:
                continue
            seen_edges.add(edge_id)
            edges.append(element)
    return {"elements": {"nodes": nodes, "edges": edges}}


_L2_EDGE_TIPI = frozenset({"PRECEDE", "CONTEMPORANEO"})

# Ordering bands for the horizontal axis. ``chiave_ordine`` (sixteenths of a
# second since year 1) and ``posizione_doc_min`` (an exposition ordinal) are
# both monotone but they measure different things, so they are never summed or
# compared with each other: a cluster that claims a time sorts before every
# cluster that only claims a narrative position, which in turn sorts before the
# clusters that claim nothing.
_L2_BANDA_DATATO = 0
_L2_BANDA_NARRATIVO = 1
_L2_BANDA_IGNOTO = 2

# Distinctive comments so FakeSession mapping keys do not collide with
# grafo() / grafo_livello1 Cypher (or with each other).
#
# ``coalesce(r.attivo, true)`` on CONTIENE / APPARTIENE_A: MT5 never deletes an
# edge a re-ingestion superseded, it tombstones it with ``attivo = false`` plus
# ``sostituito_da``. Without the filter a re-ingested document would show the
# old and the new nesting at once and ``data.parent`` would stop being a
# function. ``coalesce`` because edges written before MT5 carry no ``attivo``.
_L2_CLUSTER_CYPHER = (
    "/* grafo_livello2_cluster */ "
    "MATCH (c:ClusterTemporale) "
    "WHERE $documento IS NULL OR c.documento = $documento "
    "OPTIONAL MATCH (p:ClusterTemporale)-[rc:CONTIENE]->(c) "
    "WHERE coalesce(rc.attivo, true) "
    "RETURN c.id AS id, c.etichetta AS label, c.etichetta AS etichetta, "
    "c.tipo AS tipo_cluster, c.documento AS documento, "
    "c.descrizione AS descrizione, c.granularita AS granularita, "
    "c.inizio AS inizio, c.fine AS fine, "
    "c.chiave_ordine AS chiave_ordine, "
    "c.posizione_doc_min AS posizione_doc_min, "
    "c.stimato AS stimato, c.confidenza AS confidenza, "
    "p.id AS parent, 'ClusterTemporale' AS tipo "
    # MT5 guarantees at most one active parent; the sort only makes the
    # first-wins dedupe below deterministic if that guarantee ever breaks.
    "ORDER BY id, parent"
)

_L2_EVENTS_CYPHER = (
    "/* grafo_livello2_eventi */ "
    "MATCH (e:Evento) "
    "WHERE (e.fuso_in IS NULL OR e.fuso_in = '') "
    "AND ($documento IS NULL OR e.documento = $documento) "
    "OPTIONAL MATCH (e)-[ra:APPARTIENE_A]->(c:ClusterTemporale) "
    "WHERE coalesce(ra.attivo, true) "
    "RETURN e.id AS id, e.lemma AS label, c.id AS parent, "
    "e.documento AS documento, 'Evento' AS tipo, e.fuso_in AS fuso_in, "
    "e.posizione_doc AS posizione_doc, "
    "ra.confidenza AS confidenza, ra.stimato AS stimato "
    "ORDER BY id, parent"
)

_L2_PRECEDE_CYPHER = (
    "/* grafo_livello2_precede */ "
    "MATCH (a:Evento)-[r:PRECEDE]->(b:Evento) "
    "WHERE (r.superato_da IS NULL OR r.superato_da = '') "
    "AND (a.fuso_in IS NULL OR a.fuso_in = '') "
    "AND (b.fuso_in IS NULL OR b.fuso_in = '') "
    "AND ($documento IS NULL OR a.documento = $documento) "
    "RETURN coalesce(r.id, '') AS id, a.id AS source, b.id AS target, "
    "type(r) AS tipo, r.superato_da AS superato_da, r.confidenza AS confidenza"
)

_L2_CONTEMPORANEO_CYPHER = (
    "/* grafo_livello2_contemporaneo */ "
    "MATCH (a:Evento)-[r:CONTEMPORANEO]->(b:Evento) "
    "WHERE (a.fuso_in IS NULL OR a.fuso_in = '') "
    "AND (b.fuso_in IS NULL OR b.fuso_in = '') "
    "AND ($documento IS NULL OR a.documento = $documento) "
    "RETURN coalesce(r.id, '') AS id, a.id AS source, b.id AS target, "
    "type(r) AS tipo, r.confidenza AS confidenza"
)


def _l2_cluster_element(mapping: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
    if _is_edge_row(mapping) or _is_fused(mapping):
        return None
    tipo = mapping.get("tipo")
    if tipo and tipo != "ClusterTemporale":
        return None
    node_id = mapping.get("id")
    if not node_id:
        return None
    if tipo != "ClusterTemporale" and mapping.get("etichetta") is None and mapping.get(
        "tipo_cluster"
    ) is None:
        return None
    etichetta = mapping.get("etichetta") or mapping.get("label")
    data: dict[str, Any] = {
        "id": str(node_id),
        "label": etichetta or str(node_id),
        "tipo": "ClusterTemporale",
        "etichetta": etichetta,
        "tipo_cluster": mapping.get("tipo_cluster"),
        "descrizione": mapping.get("descrizione"),
        "granularita": mapping.get("granularita"),
        "inizio": mapping.get("inizio"),
        "fine": mapping.get("fine"),
        # Verbatim: a cluster written before MT5 has neither, and the view
        # never invents a time for it. Only ``ordine_vista`` is resolved.
        "chiave_ordine": _as_int(mapping.get("chiave_ordine")),
        "posizione_doc_min": _as_int(mapping.get("posizione_doc_min")),
        "stimato": _as_bool(mapping.get("stimato")),
        "confidenza": _as_confidenza(mapping.get("confidenza")),
    }
    parent = mapping.get("parent")
    if parent:
        data["parent"] = str(parent)
    return {"data": data}


def _l2_evento_element(mapping: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
    if _is_edge_row(mapping) or _is_fused(mapping):
        return None
    tipo = mapping.get("tipo")
    if tipo and tipo != "Evento":
        return None
    node_id = mapping.get("id")
    if not node_id:
        return None
    data: dict[str, Any] = {
        "id": str(node_id),
        "label": mapping.get("label") or mapping.get("lemma") or str(node_id),
        "tipo": "Evento",
        "documento": mapping.get("documento"),
        "posizione_doc": _as_int(mapping.get("posizione_doc")),
    }
    parent = mapping.get("parent")
    if parent:
        # ``stimato``/``confidenza`` come from APPARTIENE_A and describe the
        # membership, not the event: without a cluster they mean nothing, and
        # APPARTIENE_A is not a drawn edge, so this is their only way out.
        data["parent"] = str(parent)
        stimato = _as_bool(mapping.get("stimato"))
        if stimato is not None:
            data["stimato"] = stimato
        _attach_confidenza(data, mapping)
    return {"data": data}


def _l2_scarta_appartenenza(data: dict[str, Any]) -> None:
    """Drop a membership whose cluster is not among the returned nodes."""
    data.pop("parent", None)
    data.pop("stimato", None)
    data.pop("confidenza", None)


def _l2_ciclo(padre_di: dict[str, str]) -> set[str] | None:
    """The node set of one cycle in a child→parent map, ``None`` if acyclic."""
    for start in sorted(padre_di):
        percorso: list[str] = []
        corrente: str | None = start
        while corrente is not None:
            if corrente in percorso:
                return set(percorso[percorso.index(corrente) :])
            percorso.append(corrente)
            corrente = padre_di.get(corrente)
    return None


def _l2_padri_validi(padre_di: dict[str, str], ids: set[str]) -> dict[str, str]:
    """Parent map a Cytoscape compound can accept: present, non-self, acyclic.

    MT4 already refuses cycles and second parents when it builds the forest,
    but the view reads what is in the database, which also holds whatever
    older rulesets wrote. A ``parent`` pointing outside the returned nodes
    makes Cytoscape drop the child, and a cycle makes it throw, so both are
    checked here and not only in Cypher. A cycle is broken at its smallest id
    so that the same database always yields the same picture.
    """
    validi = {
        figlio: padre
        for figlio, padre in padre_di.items()
        if padre in ids and padre != figlio
    }
    while (ciclo := _l2_ciclo(validi)) is not None:
        validi.pop(min(ciclo))
    return validi


def _l2_minimi_sottoalbero(
    propri: dict[str, int | None],
    padre_di: dict[str, str],
) -> dict[str, int | None]:
    """Each cluster's own value, lowered by the minimum of its subtree.

    A pure container declares no time and holds no event of its own, so on its
    own properties it would land in the "claims nothing" band even when all of
    its children are dated. Same reasoning MT5 used to persist
    ``posizione_doc_min`` over the subtree instead of over the direct members.
    """
    fuori = dict(propri)
    for cid in sorted(propri):
        valore = propri.get(cid)
        if valore is None:
            continue
        visti = {cid}
        corrente = padre_di.get(cid)
        while corrente is not None and corrente not in visti:
            visti.add(corrente)
            attuale = fuori.get(corrente)
            if attuale is None or valore < attuale:
                fuori[corrente] = valore
            corrente = padre_di.get(corrente)
    return fuori


def _l2_chiave_vista(
    cid: str,
    chiave_ordine: int | None,
    posizione_doc_min: int | None,
) -> tuple[int, int, str]:
    """Total, deterministic sort key over the two non-mixable scales."""
    if chiave_ordine is not None:
        return (_L2_BANDA_DATATO, chiave_ordine, cid)
    if posizione_doc_min is not None:
        return (_L2_BANDA_NARRATIVO, posizione_doc_min, cid)
    # Last-resort tie-break, not a position: nothing else is left, and the id
    # only decides between clusters that are already all at the far right.
    return (_L2_BANDA_IGNOTO, 0, cid)


def _l2_ordine_cluster(
    ids: set[str],
    padre_di: dict[str, str],
    chiavi: dict[str, tuple[int, int, str]],
) -> list[str]:
    """Cluster ids in depth-first preorder, roots and siblings sorted by key.

    Preorder keeps every parent before its own children, which is what a
    compound layout wants, and preserves the left-to-right order of any set of
    siblings, which is what MT8 has to reproduce.
    """
    figli: dict[str, list[str]] = {}
    radici: list[str] = []
    for cid in ids:
        padre = padre_di.get(cid)
        if padre is None:
            radici.append(cid)
        else:
            figli.setdefault(padre, []).append(cid)
    ordine: list[str] = []
    pila = sorted(radici, key=chiavi.__getitem__, reverse=True)
    while pila:
        cid = pila.pop()
        ordine.append(cid)
        pila.extend(sorted(figli.get(cid, []), key=chiavi.__getitem__, reverse=True))
    return ordine


def _l2_chiave_evento(
    data: dict[str, Any],
    rango: dict[str, int],
    senza_cluster: int,
) -> tuple[int, int, int, str]:
    """Events follow their box, and inside the box they follow the text.

    A compound layout stacks a parent's children in array order, exactly as
    ``grafo_livello1`` does with ``posizione_chunk``, so the order of the rows
    is the only thing that makes the inside of a box readable.
    """
    parent = data.get("parent")
    posizione = data.get("posizione_doc")
    return (
        senza_cluster if parent is None else rango.get(parent, senza_cluster),
        1 if posizione is None else 0,
        0 if posizione is None else posizione,
        data["id"],
    )


def _l2_edge_element(mapping: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
    if not _is_edge_row(mapping):
        return None
    source = mapping.get("source")
    target = mapping.get("target")
    tipo = mapping.get("tipo") or mapping.get("t") or mapping.get("type(r)") or ""
    if tipo not in _L2_EDGE_TIPI:
        return None
    if tipo == "PRECEDE" and mapping.get("superato_da"):
        return None
    edge_id = mapping.get("id") or f"{tipo}|{source}|{target}"
    data: dict[str, Any] = {
        "id": str(edge_id),
        "source": str(source),
        "target": str(target),
        "tipo": tipo,
    }
    _attach_confidenza(data, mapping)
    return {"data": data}


async def grafo_livello2(session, documento: str | None = None) -> dict:
    """Livello 2 (tempo): ClusterTemporale + Evento + PRECEDE/CONTEMPORANEO.

    Compound Cytoscape nodes on two levels: a ClusterTemporale ``data.parent``
    is the active ``CONTIENE`` parent, an Evento ``data.parent`` is its active
    ``APPARTIENE_A`` leaf cluster. Neither ``CONTIENE`` nor ``APPARTIENE_A`` is
    a drawn edge: they assign the parent. No Zona, Menzione, or Quarantena, no
    CAUSA/SEQUENZA/… edges, no superseded PRECEDE (``superato_da`` set).

    Rows come out ordered on the time axis: clusters depth-first with roots and
    siblings by ``chiave_ordine``, events after their own box in
    ``posizione_doc`` order. ``ordine_vista`` carries that resolved rank.

    A CONTEMPORANEO is dropped when both its events sit in the same box, which
    already says so, and when the same unordered pair also carries a PRECEDE,
    which contradicts it.
    """
    params = {"documento": documento}
    cluster_elements: dict[str, dict[str, dict[str, Any]]] = {}
    for row in await _rows(session, _L2_CLUSTER_CYPHER, params):
        element = _l2_cluster_element(_as_mapping(row))
        if element is None:
            continue
        cluster_elements.setdefault(element["data"]["id"], element)
    cluster_ids = set(cluster_elements)

    evento_elements: dict[str, dict[str, dict[str, Any]]] = {}
    for row in await _rows(session, _L2_EVENTS_CYPHER, params):
        element = _l2_evento_element(_as_mapping(row))
        if element is None:
            continue
        if element["data"].get("parent") not in cluster_ids:
            _l2_scarta_appartenenza(element["data"])
        evento_elements.setdefault(element["data"]["id"], element)

    padre_di = _l2_padri_validi(
        {
            cid: element["data"]["parent"]
            for cid, element in cluster_elements.items()
            if element["data"].get("parent")
        },
        cluster_ids,
    )
    for cid, element in cluster_elements.items():
        if cid in padre_di:
            element["data"]["parent"] = padre_di[cid]
        else:
            element["data"].pop("parent", None)

    chiavi_proprie = {
        cid: element["data"]["chiave_ordine"]
        for cid, element in cluster_elements.items()
    }
    posizioni_proprie = {
        cid: element["data"]["posizione_doc_min"]
        for cid, element in cluster_elements.items()
    }
    # A cluster written before MT5 has no posizione_doc_min: rather than
    # parking it at the far right, read the events the view already holds.
    # On a cluster MT5 did write this can only confirm the stored value,
    # which is the minimum over the whole subtree and so never larger.
    for element in evento_elements.values():
        parent = element["data"].get("parent")
        posizione = element["data"].get("posizione_doc")
        if parent is None or posizione is None:
            continue
        attuale = posizioni_proprie.get(parent)
        if attuale is None or posizione < attuale:
            posizioni_proprie[parent] = posizione
    chiavi = _l2_minimi_sottoalbero(chiavi_proprie, padre_di)
    posizioni = _l2_minimi_sottoalbero(posizioni_proprie, padre_di)
    chiavi_vista = {
        cid: _l2_chiave_vista(cid, chiavi.get(cid), posizioni.get(cid))
        for cid in cluster_ids
    }

    nodes: list[dict[str, dict[str, Any]]] = []
    rango: dict[str, int] = {}
    for indice, cid in enumerate(_l2_ordine_cluster(cluster_ids, padre_di, chiavi_vista)):
        rango[cid] = indice
        cluster_elements[cid]["data"]["ordine_vista"] = indice
        nodes.append(cluster_elements[cid])
    senza_cluster = len(rango)
    nodes.extend(
        sorted(
            evento_elements.values(),
            key=lambda element: _l2_chiave_evento(
                element["data"], rango, senza_cluster
            ),
        )
    )
    seen_nodes = cluster_ids | set(evento_elements)

    edges: list[dict[str, dict[str, Any]]] = []
    seen_edges: set[str] = set()
    coppie_precede: set[frozenset[str]] = set()
    for row in await _rows(session, _L2_PRECEDE_CYPHER, params):
        element = _l2_edge_element(_as_mapping(row))
        if element is None:
            continue
        src, tgt = element["data"]["source"], element["data"]["target"]
        if src not in seen_nodes or tgt not in seen_nodes:
            continue
        edge_id = element["data"]["id"]
        if edge_id in seen_edges:
            continue
        seen_edges.add(edge_id)
        coppie_precede.add(frozenset((src, tgt)))
        edges.append(element)

    candidati: list[dict[str, dict[str, Any]]] = []
    for row in await _rows(session, _L2_CONTEMPORANEO_CYPHER, params):
        element = _l2_edge_element(_as_mapping(row))
        if element is None:
            continue
        src, tgt = element["data"]["source"], element["data"]["target"]
        if src not in seen_nodes or tgt not in seen_nodes:
            continue
        candidati.append(element)
    cluster_di = {
        eid: element["data"].get("parent")
        for eid, element in evento_elements.items()
    }
    coppie_viste: set[frozenset[str]] = set()
    # CONTEMPORANEO is symmetric by design (D2), so every comparison here is on
    # the unordered pair; sorting by id first makes the survivor of a mirrored
    # pair independent of the order the database hands the rows over.
    for element in sorted(candidati, key=lambda item: item["data"]["id"]):
        src, tgt = element["data"]["source"], element["data"]["target"]
        if src == tgt:
            continue
        coppia = frozenset((src, tgt))
        if coppia in coppie_precede or coppia in coppie_viste:
            continue
        box = cluster_di.get(src)
        if box is not None and box == cluster_di.get(tgt):
            continue
        edge_id = element["data"]["id"]
        if edge_id in seen_edges:
            continue
        seen_edges.add(edge_id)
        coppie_viste.add(coppia)
        edges.append(element)
    return {"elements": {"nodes": nodes, "edges": edges}}


_L3_EDGE_TIPI = frozenset(get_args(TipoRelazioneLibera))
_L3_EDGE_TIPI_CYPHER = ",".join(f"'{tipo}'" for tipo in get_args(TipoRelazioneLibera))

# Distinctive comments so FakeSession mapping keys do not collide with
# grafo() / grafo_livello1 / grafo_livello2 Cypher.
_L3_EVENTS_CYPHER = (
    "/* grafo_livello3_eventi */ "
    "MATCH (e:Evento) "
    "WHERE (e.fuso_in IS NULL OR e.fuso_in = '') "
    "AND ($documento IS NULL OR e.documento = $documento) "
    "RETURN e.id AS id, e.lemma AS label, e.documento AS documento, "
    "'Evento' AS tipo, e.fuso_in AS fuso_in"
)

_L3_EDGES_CYPHER = (
    "/* grafo_livello3_archi */ "
    "MATCH (a:Evento)-[r]->(b:Evento) "
    "WHERE (r.livello = '3' OR r.livello = \"3\") "
    f"AND type(r) IN [{_L3_EDGE_TIPI_CYPHER}] "
    "AND (a.fuso_in IS NULL OR a.fuso_in = '') "
    "AND (b.fuso_in IS NULL OR b.fuso_in = '') "
    "AND ($documento IS NULL OR a.documento = $documento) "
    "RETURN coalesce(r.id, '') AS id, a.id AS source, b.id AS target, "
    "type(r) AS tipo, r.livello AS livello, r.spiegazione AS spiegazione, "
    "r.confidenza AS confidenza"
)


def _is_livello3(mapping: dict[str, Any]) -> bool:
    livello = mapping.get("livello")
    if livello is None:
        return False
    return str(livello) == "3"


def _l3_evento_element(mapping: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
    if _is_edge_row(mapping) or _is_fused(mapping):
        return None
    tipo = mapping.get("tipo")
    if tipo and tipo != "Evento":
        return None
    node_id = mapping.get("id")
    if not node_id:
        return None
    return {
        "data": {
            "id": str(node_id),
            "label": mapping.get("label") or mapping.get("lemma") or str(node_id),
            "tipo": "Evento",
            "documento": mapping.get("documento"),
        }
    }


def _l3_edge_element(mapping: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
    if not _is_edge_row(mapping):
        return None
    if not _is_livello3(mapping):
        return None
    source = mapping.get("source")
    target = mapping.get("target")
    tipo = mapping.get("tipo") or mapping.get("t") or mapping.get("type(r)") or ""
    if tipo not in _L3_EDGE_TIPI:
        return None
    edge_id = mapping.get("id") or f"{tipo}|{source}|{target}"
    data: dict[str, Any] = {
        "id": str(edge_id),
        "source": str(source),
        "target": str(target),
        "tipo": tipo,
        "livello": "3",
        "spiegazione": mapping.get("spiegazione"),
    }
    _attach_confidenza(data, mapping)
    return {"data": data}


async def grafo_livello3(session, documento: str | None = None) -> dict:
    """Livello 3 (relazioni): all Evento + only ``livello='3'`` free arcs.

    No compound ``parent``, no Zona, ClusterTemporale, Menzione, or
    Quarantena. Only TipoRelazioneLibera edges (CAUSA, CONDIZIONE, …)
    with ``r.livello = '3'``. ``spiegazione`` is copied onto ``data``.
    Fused events (``fuso_in`` set) and dangling endpoints are dropped.
    """
    params = {"documento": documento}
    nodes: list[dict[str, dict[str, Any]]] = []
    seen_nodes: set[str] = set()
    for row in await _rows(session, _L3_EVENTS_CYPHER, params):
        element = _l3_evento_element(_as_mapping(row))
        if element is None:
            continue
        node_id = element["data"]["id"]
        if node_id in seen_nodes:
            continue
        seen_nodes.add(node_id)
        nodes.append(element)
    edges: list[dict[str, dict[str, Any]]] = []
    seen_edges: set[str] = set()
    for row in await _rows(session, _L3_EDGES_CYPHER, params):
        element = _l3_edge_element(_as_mapping(row))
        if element is None:
            continue
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

# Rel types persisted without ``r.id`` (SOGG/OGG, SUCCESSIONE_ZONA,
# CONTEMPORANEO, livello-3, …) appear in the graph payload as
# ``{tipo}|{source}|{target}``. Look them up by endpoints.
_ARCO_ENDPOINTS_CYPHER = (
    "/* dettaglio_arco_endpoints */ "
    "MATCH (a {id: $source})-[r]->(b {id: $target}) "
    "WHERE type(r) = $tipo "
    "AND (coalesce(r.id, '') = '' OR r.id = $id) "
    "RETURN properties(r) AS props, type(r) AS tipo, "
    "a.id AS source_id, labels(a) AS source_labels, "
    "coalesce(a.lemma, a.forma, a.frammento, a.riassunto, a.id) AS source_label, "
    "b.id AS target_id, labels(b) AS target_labels, "
    "coalesce(b.lemma, b.forma, b.frammento, b.riassunto, b.id) AS target_label "
    "ORDER BY CASE WHEN coalesce(r.id, '') = '' THEN 0 ELSE 1 END "
    "LIMIT 1"
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


def _synthetic_arco_parts(arco_id: str) -> tuple[str, str, str] | None:
    """Parse graph payload ids of the form ``TIPO|source|target`` (extra
    segments after target are ignored). ``None`` if the string is not
    that shape.
    """
    parts = str(arco_id or "").split("|")
    if len(parts) < 3:
        return None
    tipo, source, target = parts[0], parts[1], parts[2]
    if not tipo or not source or not target:
        return None
    return tipo, source, target


def _arco_payload(mapping: dict[str, Any], arco_id: str) -> dict[str, Any] | None:
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


async def dettaglio_arco(session, arco_id: str) -> dict[str, Any] | None:
    """Every property of one relationship, for the selection dashboard.

    First match ``r.id``. If that misses (rels persisted without an id
    property — argomentali, SUCCESSIONE_ZONA, CONTEMPORANEO, livello 3)
    and ``arco_id`` is the synthetic ``tipo|source|target`` used by
    ``grafo`` / ``grafo_livello*``, look up by endpoints. Prefer the
    relationship that has no ``r.id`` so livello-3 CAUSA is not
    confused with a micro CAUSA that carries a hashed id.
    """
    mapping = _as_mapping(await _single(session, _ARCO_CYPHER, {"id": arco_id}))
    payload = _arco_payload(mapping, arco_id)
    if payload is not None:
        return payload
    parts = _synthetic_arco_parts(arco_id)
    if parts is None:
        return None
    tipo, source, target = parts
    mapping = _as_mapping(
        await _single(
            session,
            _ARCO_ENDPOINTS_CYPHER,
            {"id": arco_id, "tipo": tipo, "source": source, "target": target},
        )
    )
    return _arco_payload(mapping, arco_id)


__all__ = [
    "catalogo",
    "dettaglio_arco",
    "dettaglio_nodo",
    "grafo",
    "grafo_livello1",
    "grafo_livello2",
    "grafo_livello3",
    "stats",
]
