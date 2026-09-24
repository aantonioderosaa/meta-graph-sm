"""Static event-graph legend (piano 15.5) plus live stats/graph helpers.

``catalogo()`` is the single source of truth: closed vocabs from
``app.models.event_graph`` Literals, never from the database.
"""

from __future__ import annotations

import inspect
import json
from typing import Any, get_args

from app.models.event_graph import (
    EntitaKernelCategoria,
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
_TEMPORALE = ()
_PLACEHOLDER = ("COLLEGATO",)
_STRUTTURA = (
    "SATELLITE_DI",
    "APPARTIENE_A",
    "SUCCESSIONE_ANCORA",
    "SUCCESSIONE_ZONA",
    "CONTIENE",
)

_DIR_EVENTO_MENZIONE = "Fatto→Menzione"
_DIR_EVENTO_EVENTO = "Fatto→Fatto"
_DIR_EVENTO_ANCORA = "Fatto→AncoraTemporale"
_DIR_ANCORA_ANCORA = "AncoraTemporale→AncoraTemporale"
_DIR_ZONA_ZONA = "Zona→Zona"

# Unique types ``grafo()`` (vista=tutto) can return: Fatto/Menzione/Quarantena
# endpoints only, chain types excluded. APPARTIENE_A and SUCCESSIONE_ZONA
# target labels this view never queries.
_TUTTO_NODI = ("Fatto", "Menzione", "Quarantena")
_TUTTO_ARCHI = (
    *_ARGOMENTALI,
    *_DIZIONARIO,
    *_PLACEHOLDER,
    "SATELLITE_DI",
)
_ORDINE_NODI = ("Zona", "Fatto")
_ORDINE_ARCHI = ("SUCCESSIONE_ZONA", "SEQUENZA", "COLLEGATO")
_TEMPORALE_NODI = ("AncoraTemporale", "Fatto")
# APPARTIENE_A / CONTIENE assign data.parent; grafo_livello2 does not draw
# them. They still belong in the vista legend (same as the v2 note).
# SUCCESSIONE_ANCORA is drawn between sibling ancore.
_TEMPORALE_ARCHI = (
    "SUCCESSIONE_ANCORA",
    "APPARTIENE_A",
    "CONTIENE",
)
_RELAZIONI_NODI = ("Fatto",)
_RELAZIONI_ARCHI = tuple(get_args(TipoRelazioneLibera))

_SIGNIFICATO: dict[tuple[str, str], str] = {
    ("argomentali", "SOGG"): "soggetto dell'evento",
    ("argomentali", "OGG"): "oggetto dell'evento",
    ("argomentali", "OBL"): "argomento obliquo (preposizione)",
    ("argomentali", "TEMPO"): "circostanza temporale",
    ("argomentali", "LUOGO"): "circostanza spaziale",
    ("argomentali", "MODO"): "circostanza di modo",
    ("dizionario", "CAUSA"): "relazione causale fra eventi",
    ("dizionario", "LIMITE"): "limite temporale o condizionale",
    ("dizionario", "CONDIZIONE"): "condizione dell'evento dipendente",
    ("dizionario", "SCOPO"): "finalità dell'evento dipendente",
    ("dizionario", "CONCESSIONE"): "concessione rispetto all'evento principale",
    ("dizionario", "CONTRASTO"): "contrasto fra due eventi",
    ("dizionario", "SEQUENZA"): "passo della spina dorsale narrativa",
    ("dizionario", "CONTENUTO"): "contenuto di un evento di dire/pensare",
    ("placeholder", "COLLEGATO"): (
        "segnale d'ordine debole (ordine_menzione / ordine_ingestione)"
    ),
    ("struttura", "SATELLITE_DI"): "SFONDO agganciato al PRIMO_PIANO",
    ("struttura", "APPARTIENE_A"): "evento appartenente a un'ancora temporale",
    ("struttura", "SUCCESSIONE_ANCORA"): (
        "successione lineare fra ancore consecutive dello stesso livello"
    ),
    ("struttura", "SUCCESSIONE_ZONA"): "successione narrativa fra zone espanse",
    ("struttura", "CONTIENE"): (
        "ancora temporale che ne contiene un'altra (foresta)"
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
        return _DIR_EVENTO_ANCORA
    if tipo == "CONTIENE":
        return _DIR_ANCORA_ANCORA
    if tipo == "SUCCESSIONE_ANCORA":
        return _DIR_ANCORA_ANCORA
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
                "Vista completa: Fatto, Menzione e Quarantena con tutti gli "
                "archi micro (argomentali, dizionario, COLLEGATO, "
                "SATELLITE_DI). Nessun arco temporale fra eventi. "
                "Nessuna Zona né AncoraTemporale; "
                "SUCCESSIONE_ZONA e APPARTIENE_A restano fuori da questa proiezione."
            ),
        },
        "ordine": {
            "nodi": list(_ORDINE_NODI),
            "archi": list(_ORDINE_ARCHI),
            "significato": (
                "Livello 1: Zona + Fatto (compound, parent=zona). "
                "SUCCESSIONE_ZONA (successione narrativa fra zone espanse) "
                "e SEQUENZA/COLLEGATO intra-zona. Nessun CAUSA/CONTRASTO/…"
            ),
        },
        "temporale": {
            "nodi": list(_TEMPORALE_NODI),
            "archi": list(_TEMPORALE_ARCHI),
            "significato": (
                "Livello 2: AncoraTemporale + Fatto (compound, parent=ancora). "
                "Arco visibile SUCCESSIONE_ANCORA "
                "(AncoraTemporale→AncoraTemporale, fratelli consecutivi). "
                "APPARTIENE_A (evento appartenente a un'ancora temporale) e "
                "CONTIENE (AncoraTemporale→AncoraTemporale, foresta) "
                "assegnano il parent, non sono archi visibili. "
                "Gli eventi dentro un'ancora sono sfusi: nessun arco fra loro. "
                "granularita è la scala dell'ancora (secondo…secolo); "
                "stimato=true se inizio è inferito e non dichiarato dal testo."
            ),
        },
        "relazioni": {
            "nodi": list(_RELAZIONI_NODI),
            "archi": list(_RELAZIONI_ARCHI),
            "significato": (
                "Relazioni: ogni Fatto non fuso del documento e le "
                "TipoRelazioneLibera di significato (CAUSA, CONDIZIONE, "
                "SCOPO, CONCESSIONE, CONTRASTO, LIMITE, CONTENUTO) da grammatica "
                "e livello 3. Nessun arco SEQUENZA: il livello 3 legge il testo "
                "e la lista eventi, non l'ordine di esposizione. "
                "Vicinanza senza nulla in comune non basta; "
                "con qualcosa in comune non è una penalità."
            ),
        },
        "entita": {
            "nodi": ["KernelCategoria", "Menzione", "Fatto"],
            "archi": [],
            "significato": (
                "Vista entità: 10 nodi gruppo sintetici (uno per EntitaKernelCategoria) "
                "e i nodi Menzione/Fatto con kernel_category valorizzata come figli "
                "(SOGG/OGG classificati da LLM, TEMPO assegnato a 'Temporale' senza LLM, "
                "Fatto assegnato automaticamente a 'Fatti'). "
                "Nessun arco. I nodi gruppo sintetici hanno id stabile 'kernel:<categoria>' "
                "e sono presenti anche se vuoti."
            ),
        },
    }


def catalogo() -> dict:
    """nodes, traits, arches grouped for the legend."""
    return {
        "nodes": [
            {"id": "Fatto", "label": "Fatto", "shape": "pieno"},
            {"id": "Menzione", "label": "Menzione", "shape": "ovale"},
            {"id": "Quarantena", "label": "Quarantena", "shape": "tratteggiato"},
            {"id": "Zona", "label": "Zona", "shape": "round-rectangle"},
            {
                "id": "AncoraTemporale",
                "label": "AncoraTemporale",
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


def _attach_posizione(data: dict[str, Any], mapping: dict[str, Any]) -> None:
    """Forward exposition ordinals so Relazioni can place the zigzag ladder."""
    pos_doc = _as_int(mapping.get("posizione_doc"))
    if pos_doc is not None:
        data["posizione_doc"] = pos_doc
    pos_chunk = _as_int(mapping.get("posizione_chunk"))
    if pos_chunk is not None:
        data["posizione_chunk"] = pos_chunk
    offset = _as_int(mapping.get("offset_inizio"))
    if offset is not None:
        data["offset_inizio"] = offset


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
    n_evento = _first_int(await _single(session, "MATCH (e:Fatto) RETURN count(e)"))
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
        session, "MATCH (e:Fatto) RETURN e.piano AS p, count(*) AS n"
    ):
        mapping = _as_mapping(row)
        key = mapping.get("p") or mapping.get("e.piano")
        if not key:
            continue
        piano[str(key)] = _first_int(mapping)
    return {
        "nodi": {
            "Fatto": n_evento,
            "Menzione": n_menzione,
            "Quarantena": n_quarantena,
        },
        "archi": archi,
        "tratti": {"piano": piano},
    }


_NODES_CYPHER = (
    "MATCH (e:Fatto) "
    "WHERE (e.fuso_in IS NULL OR e.fuso_in = '') "
    "AND ($documento IS NULL OR e.documento = $documento) "
    "AND ($piano IS NULL OR e.piano = $piano) "
    "AND ($lemma IS NULL OR e.lemma = $lemma) "
    "RETURN e.id AS id, e.lemma AS label, 'Fatto' AS tipo, e.piano AS piano, "
    "e.fattualita AS fattualita, e.documento AS documento, e.fuso_in AS fuso_in, "
    "e.posizione_doc AS posizione_doc, e.posizione_chunk AS posizione_chunk, "
    "e.offset_inizio AS offset_inizio, "
    "null AS descrizione, null AS occorrenze, null AS riassunti "
    "UNION ALL "
    "MATCH (m:Menzione) "
    "WHERE ($documento IS NULL OR m.documento = $documento) "
    "RETURN m.id AS id, coalesce(m.forma, m.forma_canonica, m.id) AS label, "
    "'Menzione' AS tipo, null AS piano, null AS fattualita, "
    "m.documento AS documento, null AS fuso_in, "
    "null AS posizione_doc, null AS posizione_chunk, null AS offset_inizio, "
    "m.summary AS descrizione, m.occorrenze AS occorrenze, m.riassunti AS riassunti "
    "UNION ALL "
    "MATCH (q:Quarantena) "
    "WHERE ($documento IS NULL OR q.ancora_doc = $documento) "
    "RETURN q.id AS id, coalesce(q.frammento, q.id) AS label, "
    "'Quarantena' AS tipo, null AS piano, null AS fattualita, "
    "q.ancora_doc AS documento, null AS fuso_in, "
    "null AS posizione_doc, null AS posizione_chunk, null AS offset_inizio, "
    "null AS descrizione, null AS occorrenze, null AS riassunti"
)

_EDGES_CYPHER = (
    "MATCH (a)-[r]->(b) "
    # Both endpoints must be one of the labels _NODES_CYPHER returns
    # (Fatto/Menzione/Quarantena). Without this, any edge whose endpoints
    # are a label this view doesn't query for (e.g. :Zona<->:Zona macro arcs,
    # Addendum 2 M2) still comes back here with a source/target id that has
    # no matching node in the nodes list — Cytoscape then refuses to mount
    # ("nonexistant source"). This view is the MICRO event graph only; macro
    # zone arcs belong to GET /zone, not here.
    "WHERE (a:Fatto OR a:Menzione OR a:Quarantena) "
    "AND (b:Fatto OR b:Menzione OR b:Quarantena) "
    "AND (NOT a:Fatto OR a.fuso_in IS NULL OR a.fuso_in = '') "
    "AND (NOT b:Fatto OR b.fuso_in IS NULL OR b.fuso_in = '') "
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
    data: dict[str, Any] = {
        "id": str(node_id),
        "label": mapping.get("label") or str(node_id),
        "tipo": mapping.get("tipo") or "Fatto",
        "piano": mapping.get("piano"),
        "fattualita": mapping.get("fattualita"),
        "documento": mapping.get("documento"),
    }
    riassunti = _as_str_list(mapping.get("riassunti"))
    if riassunti:
        data["riassunti"] = riassunti
    if mapping.get("descrizione"):
        data["descrizione"] = mapping.get("descrizione")
    elif riassunti:
        data["descrizione"] = riassunti[-1]
    occorrenze = mapping.get("occorrenze")
    if occorrenze is not None and occorrenze != "":
        try:
            data["occorrenze"] = int(occorrenze)
        except (TypeError, ValueError):
            pass
    _attach_posizione(data, mapping)
    return {"data": data}


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
    
    # Aggiungi eventi_collegati per i nodi Menzione nella vista "entita"
    # (questo è solo per la vista entita, che si basa su questa funzione)
    if params.get("vista") == "entita":
        nodes = await _aggiungi_eventi_collegati(session, nodes, seen_nodes)
    
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
    "MATCH (e:Fatto) "
    "WHERE e.chunk_id = z.id "
    "AND (e.fuso_in IS NULL OR e.fuso_in = '') "
    "AND ($documento IS NULL OR z.documento = $documento) "
    "RETURN e.id AS id, e.lemma AS label, e.chunk_id AS parent, "
    "e.documento AS documento, 'Fatto' AS tipo, e.fuso_in AS fuso_in, "
    "e.posizione_doc AS posizione_doc, e.posizione_chunk AS posizione_chunk, "
    "e.offset_inizio AS offset_inizio "
    "ORDER BY z.ordinale, e.posizione_doc, e.posizione_chunk, e.offset_inizio"
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
    "MATCH (a:Fatto)-[r]->(b:Fatto) "
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
    if tipo and tipo != "Fatto":
        return None
    node_id = mapping.get("id")
    if not node_id:
        return None
    parent = mapping.get("parent") or mapping.get("chunk_id")
    if not parent:
        return None
    data: dict[str, Any] = {
        "id": str(node_id),
        "label": mapping.get("label") or mapping.get("lemma") or str(node_id),
        "tipo": "Fatto",
        "parent": str(parent),
        "documento": mapping.get("documento"),
    }
    _attach_posizione(data, mapping)
    return {"data": data}


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

    Compound Cytoscape nodes: Fatto ``data.parent`` = Zona.id. No Menzione,
    Quarantena, or AncoraTemporale. No CAUSA/CONTRASTO/… edges.
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


_L2_EDGE_TIPI = frozenset({"SUCCESSIONE_ANCORA"})

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
    "MATCH (a:AncoraTemporale) "
    "WHERE $documento IS NULL OR a.documento = $documento "
    "OPTIONAL MATCH (p:AncoraTemporale)-[rc:CONTIENE]->(a) "
    "WHERE coalesce(rc.attivo, true) "
    "RETURN a.id AS id, a.etichetta AS label, a.etichetta AS etichetta, "
    "a.tipo AS tipo_cluster, a.documento AS documento, "
    "a.descrizione AS descrizione, a.granularita AS granularita, "
    "a.inizio AS inizio, a.fine AS fine, "
    "a.chiave_ordine AS chiave_ordine, "
    "a.posizione_doc_min AS posizione_doc_min, "
    "a.stimato AS stimato, a.confidenza AS confidenza, "
    "a.natura AS natura, a.ordinale AS ordinale, "
    "a.occorrenze AS occorrenze, "
    "p.id AS parent, 'AncoraTemporale' AS tipo "
    # MT5 guarantees at most one active parent; the sort only makes the
    # first-wins dedupe below deterministic if that guarantee ever breaks.
    "ORDER BY id, parent"
)

_L2_EVENTS_CYPHER = (
    "/* grafo_livello2_eventi */ "
    "MATCH (e:Fatto) "
    "WHERE (e.fuso_in IS NULL OR e.fuso_in = '') "
    "AND ($documento IS NULL OR e.documento = $documento) "
    "OPTIONAL MATCH (e)-[ra:APPARTIENE_A]->(a:AncoraTemporale) "
    "WHERE coalesce(ra.attivo, true) "
    "RETURN e.id AS id, e.lemma AS label, a.id AS parent, "
    "e.documento AS documento, 'Fatto' AS tipo, e.fuso_in AS fuso_in, "
    "e.posizione_doc AS posizione_doc, "
    "e.posizione_chunk AS posizione_chunk, "
    "e.offset_inizio AS offset_inizio, "
    "ra.confidenza AS confidenza, ra.stimato AS stimato "
    "ORDER BY id, parent"
)

_L2_SUCCESSIONE_CYPHER = (
    "/* grafo_livello2_successione_ancora */ "
    "MATCH (aa:AncoraTemporale)-[r:SUCCESSIONE_ANCORA]->(ab:AncoraTemporale) "
    "WHERE coalesce(r.attivo, true) "
    "AND ($documento IS NULL OR aa.documento = $documento) "
    "RETURN coalesce(r.id, '') AS id, aa.id AS source, ab.id AS target, "
    "type(r) AS tipo, r.confidenza AS confidenza"
)


def _l2_cluster_element(mapping: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
    if _is_edge_row(mapping) or _is_fused(mapping):
        return None
    tipo = mapping.get("tipo")
    if tipo and tipo != "AncoraTemporale":
        return None
    node_id = mapping.get("id")
    if not node_id:
        return None
    if (
        tipo != "AncoraTemporale"
        and mapping.get("etichetta") is None
        and mapping.get("tipo_cluster") is None
        and mapping.get("natura") is None
    ):
        return None
    etichetta = mapping.get("etichetta") or mapping.get("label")
    data: dict[str, Any] = {
        "id": str(node_id),
        "label": etichetta or str(node_id),
        "tipo": "AncoraTemporale",
        "etichetta": etichetta,
        "tipo_cluster": mapping.get("tipo_cluster"),
        "descrizione": mapping.get("descrizione"),
        "granularita": mapping.get("granularita"),
        "inizio": mapping.get("inizio"),
        "fine": mapping.get("fine"),
        "natura": mapping.get("natura"),
        "ordinale": _as_int(mapping.get("ordinale")),
        "occorrenze": _as_int(mapping.get("occorrenze")),
        # Verbatim: an ancora written without a calendar signal has neither,
        # and the view never invents a time for it. Only ``ordine_vista``
        # is resolved.
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
    if tipo and tipo != "Fatto":
        return None
    node_id = mapping.get("id")
    if not node_id:
        return None
    data: dict[str, Any] = {
        "id": str(node_id),
        "label": mapping.get("label") or mapping.get("lemma") or str(node_id),
        "tipo": "Fatto",
        "documento": mapping.get("documento"),
        "posizione_doc": _as_int(mapping.get("posizione_doc")),
        "posizione_chunk": _as_int(mapping.get("posizione_chunk")),
        "offset_inizio": _as_int(mapping.get("offset_inizio")),
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


def _l2_campo_lettura(valore: Any) -> tuple[int, int]:
    """Known values first; missing sorts after every concrete number."""
    numero = _as_int(valore)
    if numero is None:
        return (1, 0)
    return (0, numero)


def _l2_chiave_evento(
    data: dict[str, Any],
    rango: dict[str, int],
    senza_cluster: int,
) -> tuple[int, int, int, int, int, int, int, str]:
    """Events follow their box, and inside the box they follow the text.

    A compound layout stacks a parent's children in array order, so the order
    of the rows is the only thing that makes the inside of a box readable.
    Primary key is ``offset_inizio`` (global char offset: in ``dedup.py`` it
    is ``base_offset + start``). Fallbacks: ``posizione_doc``,
    ``posizione_chunk``, then ``id``. ``posizione_doc`` in practice holds the
    zona ordinal — not a document-wide index; ``layout-ordine``, Neo4j
    ``eg_evento_posizione`` and several ``_pos_key`` still use the name.
    """
    parent = data.get("parent")
    off_manca, off_val = _l2_campo_lettura(data.get("offset_inizio"))
    pos_manca, pos_val = _l2_campo_lettura(data.get("posizione_doc"))
    chunk_manca, chunk_val = _l2_campo_lettura(data.get("posizione_chunk"))
    return (
        senza_cluster if parent is None else rango.get(parent, senza_cluster),
        off_manca,
        off_val,
        pos_manca,
        pos_val,
        chunk_manca,
        chunk_val,
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
    """Livello 2 (tempo): AncoraTemporale + Fatto + SUCCESSIONE_ANCORA.

    Compound Cytoscape nodes on two levels: an AncoraTemporale ``data.parent``
    is the active ``CONTIENE`` parent, a Fatto ``data.parent`` is its active
    ``APPARTIENE_A`` leaf ancora. Neither ``CONTIENE`` nor ``APPARTIENE_A`` is
    a drawn edge: they assign the parent. Drawn edges are ``SUCCESSIONE_ANCORA``
    only (sibling ancore). Events inside a box are unordered: no event-to-event
    temporal arcs. No Zona, Menzione, or Quarantena, no CAUSA/SEQUENZA/….

    Rows come out ordered on the time axis: ancore depth-first with roots and
    siblings by ``chiave_ordine``, events after their own box by
    ``offset_inizio`` (then ``posizione_doc``, ``posizione_chunk``, id).
    ``ordine_vista`` carries that resolved rank.

    Zero ancore: empty payload. Unplaced events are not drawn as a fake box.
    """
    params = {"documento": documento}
    cluster_elements: dict[str, dict[str, dict[str, Any]]] = {}
    for row in await _rows(session, _L2_CLUSTER_CYPHER, params):
        element = _l2_cluster_element(_as_mapping(row))
        if element is None:
            continue
        cluster_elements.setdefault(element["data"]["id"], element)
    if not cluster_elements:
        return {"elements": {"nodes": [], "edges": []}}
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
    # An ancora without posizione_doc_min: rather than parking it at the far
    # right, read the events the view already holds. On an ancora that did
    # persist the field this can only confirm the stored value, which is the
    # minimum over the whole subtree and so never larger.
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
    for row in await _rows(session, _L2_SUCCESSIONE_CYPHER, params):
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
        edges.append(element)
    return {"elements": {"nodes": nodes, "edges": edges}}


_L3_EDGE_TIPI = frozenset(get_args(TipoRelazioneLibera))
_L3_EDGE_TIPI_CYPHER = ",".join(f"'{tipo}'" for tipo in get_args(TipoRelazioneLibera))

# Distinctive comments so FakeSession mapping keys do not collide with
# grafo() / grafo_livello1 / grafo_livello2 Cypher.
_L3_EVENTS_CYPHER = (
    "/* grafo_livello3_eventi */ "
    "MATCH (e:Fatto) "
    "WHERE (e.fuso_in IS NULL OR e.fuso_in = '') "
    "AND ($documento IS NULL OR e.documento = $documento) "
    "RETURN e.id AS id, e.lemma AS label, e.documento AS documento, "
    "'Fatto' AS tipo, e.fuso_in AS fuso_in, "
    "e.posizione_doc AS posizione_doc, e.posizione_chunk AS posizione_chunk, "
    "e.offset_inizio AS offset_inizio"
)

_L3_EDGES_CYPHER = (
    "/* grafo_livello3_archi */ "
    "MATCH (a:Fatto)-[r]->(b:Fatto) "
    f"WHERE type(r) IN [{_L3_EDGE_TIPI_CYPHER}] "
    "AND (a.fuso_in IS NULL OR a.fuso_in = '') "
    "AND (b.fuso_in IS NULL OR b.fuso_in = '') "
    "AND ($documento IS NULL OR a.documento = $documento) "
    "RETURN coalesce(r.id, '') AS id, a.id AS source, b.id AS target, "
    "type(r) AS tipo, r.livello AS livello, r.spiegazione AS spiegazione, "
    "r.base AS base, r.regola AS regola, "
    "r.confidenza AS confidenza"
)


def _l3_evento_element(mapping: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
    if _is_edge_row(mapping) or _is_fused(mapping):
        return None
    tipo = mapping.get("tipo")
    if tipo and tipo != "Fatto":
        return None
    node_id = mapping.get("id")
    if not node_id:
        return None
    data: dict[str, Any] = {
        "id": str(node_id),
        "label": mapping.get("label") or mapping.get("lemma") or str(node_id),
        "tipo": "Fatto",
        "documento": mapping.get("documento"),
    }
    _attach_posizione(data, mapping)
    return {"data": data}


def _l3_edge_element(mapping: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
    if not _is_edge_row(mapping):
        return None
    source = mapping.get("source")
    target = mapping.get("target")
    tipo = mapping.get("tipo") or mapping.get("t") or mapping.get("type(r)") or ""
    if tipo not in _L3_EDGE_TIPI:
        return None
    edge_id = mapping.get("id") or f"{tipo}|{source}|{target}"
    data = {
        "id": str(edge_id),
        "source": str(source),
        "target": str(target),
        "tipo": tipo,
        "spiegazione": mapping.get("spiegazione"),
    }
    livello = mapping.get("livello")
    if livello is not None and livello != "":
        data["livello"] = str(livello)
    _attach_confidenza(data, mapping)
    return {"data": data}


async def grafo_livello3(session, documento: str | None = None) -> dict:
    """Vista relazioni: every non-fused ``:Fatto`` plus TipoRelazioneLibera
    (grammar and document-level L3). No SEQUENZA: L3 reads document text and
    the event list, not exposition order.

    No compound ``parent``, no Zona, AncoraTemporale, Menzione, or Quarantena.
    """
    params = {"documento": documento}
    events_by_id: dict[str, dict[str, dict[str, Any]]] = {}
    for row in await _rows(session, _L3_EVENTS_CYPHER, params):
        element = _l3_evento_element(_as_mapping(row))
        if element is None:
            continue
        node_id = element["data"]["id"]
        if node_id in events_by_id:
            continue
        events_by_id[node_id] = element
    edges: list[dict[str, dict[str, Any]]] = []
    seen_edges: set[str] = set()
    for row in await _rows(session, _L3_EDGES_CYPHER, params):
        element = _l3_edge_element(_as_mapping(row))
        if element is None:
            continue
        src, tgt = element["data"]["source"], element["data"]["target"]
        if src not in events_by_id or tgt not in events_by_id:
            continue
        edge_id = element["data"]["id"]
        if edge_id in seen_edges:
            continue
        seen_edges.add(edge_id)
        edges.append(element)
    nodes = list(events_by_id.values())
    return {"elements": {"nodes": nodes, "edges": edges}}


_NODO_CYPHER = "MATCH (n {id: $id}) RETURN properties(n) AS props, labels(n) AS labels"

_CATENA_OCCORRENZE_CYPHER = (
    "MATCH (e:Fatto {id: $id}) "
    "MATCH (o:Fatto {catena_id: e.catena_id}) "
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
# livello-3, …) appear in the graph payload as
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

    For ``:Fatto`` with ``catena_id``, also rebuilds the live ``catena``
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
    if "Fatto" in labels and proprieta.get("catena_id"):
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


async def grafo_entita(session, documento: str | None = None) -> dict:
    """Vista entità: 10 nodi gruppo sintetici (uno per EntitaKernelCategoria)
    e i nodi Menzione/Fatto con kernel_category valorizzata come figli.

    Nessun arco (relazioni fuori scope). I nodi gruppo sintetici hanno id
    stabile "kernel:<categoria>", tipo "KernelCategoria" (contratto atteso dal
    frontend: layout-entita.ts::isKernelCategoriaNode, encoding.ts) e sono
    presenti anche se vuoti.
    
    Aggiunge i :Fatto collegati alle Menzioni nella vista "entita".
    """
    classifiche_query = (
        "MATCH (m:Menzione) "
        "WHERE m.kernel_category IS NOT NULL "
        "AND ($documento IS NULL OR m.documento = $documento) "
        "RETURN m.id AS entita_id, coalesce(m.forma, m.forma_canonica, m.id) AS label, "
        "'Menzione' AS tipo, m.kernel_category AS categoria "
        "UNION ALL "
        "MATCH (e:Fatto) "
        "WHERE e.kernel_category IS NOT NULL "
        "AND (e.fuso_in IS NULL OR e.fuso_in = '') "
        "AND ($documento IS NULL OR e.documento = $documento) "
        "RETURN e.id AS entita_id, e.lemma AS label, "
        "'Fatto' AS tipo, e.kernel_category AS categoria"
    )

    params = {"documento": documento}
    classifiche_rows = await _rows(session, classifiche_query, params)

    conteggi: dict[str, int] = {}
    nodi_membri = []
    for row in classifiche_rows:
        mapping = _as_mapping(row)
        entita_id = mapping.get("entita_id")
        categoria = mapping.get("categoria")
        if not entita_id or not categoria:
            continue
        conteggi[categoria] = conteggi.get(categoria, 0) + 1
        nodi_membri.append(
            {
                "data": {
                    "id": f"entita:{entita_id}",
                    "label": mapping.get("label") or entita_id,
                    "tipo": mapping.get("tipo") or "Menzione",
                    "parent": f"kernel:{categoria}",
                    "categoria": categoria,
                }
            }
        )

    # Genera i 10 gruppi sintetici, anche quelli vuoti. `ordinale` fissa
    # l'ordine orizzontale letto da layout-entita.ts::positionsEntita.
    nodi_gruppo = [
        {
            "data": {
                "id": f"kernel:{categoria.value}",
                "label": categoria.value,
                "tipo": "KernelCategoria",
                "categoria": categoria.value,
                "ordinale": indice,
                "count": conteggi.get(categoria.value, 0),
            }
        }
        for indice, categoria in enumerate(EntitaKernelCategoria)
    ]

    # Aggiungi eventi collegati ai nodi Menzione
    # Questo passaggio è importante per il frontend che mostra i Fatti collegati a ogni Menzione
    menzioni_con_fatti = []
    
    # Prima estrai tutti gli ID delle Menzioni membro
    menzione_ids = [n["data"]["id"].replace("entita:", "") for n in nodi_membri if n["data"]["tipo"] == "Menzione"]
    
    if menzione_ids:
        # Query per trovare i Fatti collegati alle Menzioni
        fact_query = """
        MATCH (f:Fatto)-[r]->(m:Menzione)
        WHERE m.id IN $menzione_ids
        AND type(r) IN ['SOGG', 'OGG', 'OBL', 'TEMPO', 'LUOGO', 'MODO']
        RETURN m.id AS menzione_id, f.id AS fatto_id, f.lemma AS summary
        ORDER BY f.id
        """
        
        try:
            fact_rows = await _rows(session, fact_query, {"menzione_ids": menzione_ids})
            
            # Costruisci un dizionario per mappare i nodi Menzione ai loro Fatti collegati
            menzione_fatti_map = {}
            for row in fact_rows:
                mapping = _as_mapping(row)
                menzione_id = mapping.get("menzione_id")
                fatto_id = mapping.get("fatto_id")
                summary = mapping.get("summary", "")
                
                if menzione_id not in menzione_fatti_map:
                    menzione_fatti_map[menzione_id] = []
                    
                menzione_fatti_map[menzione_id].append({
                    "fatto_id": fatto_id,
                    "summary": summary
                })
            
            # Aggiungi i eventi_collegati ai nodi Menzione corrispondenti
            for node in nodi_membri:
                if node["data"]["tipo"] == "Menzione":
                    menzione_id = node["data"]["id"].replace("entita:", "")  # Rimuovi il prefisso "entita:"
                    if menzione_id in menzione_fatti_map:
                        node["data"]["eventi_collegati"] = menzione_fatti_map[menzione_id]
                    else:
                        node["data"]["eventi_collegati"] = []
            
        except Exception as e:
            # In caso di errore, continua senza eventi collegati
            print(f"Errore nella raccolta degli eventi collegati: {e}")
            for node in nodi_membri:
                if node["data"]["tipo"] == "Menzione":
                    node["data"]["eventi_collegati"] = []
    
    return {"elements": {"nodes": nodi_gruppo + nodi_membri, "edges": []}}


async def dettaglio_arco(session, arco_id: str) -> dict[str, Any] | None:
    """Every property of one relationship, for the selection dashboard.

    First match ``r.id``. If that misses (rels persisted without an id
    property — argomentali, SUCCESSIONE_ZONA, livello 3)
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
    "grafo_entita",
    "stats",
]
