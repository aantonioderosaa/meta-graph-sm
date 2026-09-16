"""§14 — idempotent MERGE persistence (piano sez. 12).

Writes the in-memory document subgraph. Append-only: MERGE + SET, no removals.
Re-ingest of the same ``SottoGrafo`` re-issues the same MERGE keys (no-op).
"""

from __future__ import annotations

import inspect
import json
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, get_args

from app.models.event_graph import (
    GRANULARITA_TEMPORALI,
    AncoraTemporaleProposta,
    ArcoEvento,
    ClusterTemporaleProposto,
    EventoRisolto,
    LivelloRelazioniResult,
    LivelloTemporaleResult,
    MenzioneRisolta,
    QuarantenaItem,
    SegnaleTemporaleEvento,
    SottoGrafo,
    TipoRelazioneLibera,
)
from app.pipeline.event_graph import RULESET_VERSION, foresta_temporale, tempo_iso
from app.pipeline.event_graph.ancore_identita import identita_ancora
from app.pipeline.event_graph.ancore_linea import etichetta_normalizzata
from app.pipeline.event_graph.ancore_smistamento import (
    AppartenenzaAncora,
    SmistamentoAncore,
)
from app.pipeline.event_graph.ids import cluster_temporale_id, content_hash, quarantena_id
from app.pipeline.event_graph.infra.bus import publish
from app.pipeline.event_graph.livello_relazioni import relazioni_causa_ciclo
from app.pipeline.event_graph.zona_edges import SUCCESSIONE_ZONA, ArcoZona
from app.pipeline.event_graph.zona_segmentation import Zona
from app.pipeline.event_graph.zona_transizioni import REGOLA as REGOLA_SUCCESSIONE_ZONA

REGOLA = "persistence.persisti"
REGOLA_LIVELLO_TEMPORALE = "livello_temporale.estrai"
REGOLA_LIVELLO_RELAZIONI = "livello_relazioni.estrai"
REGOLA_LIVELLO_ANCORE = "persistence.persisti_livello_ancore"
_STAGE_ANCORE = "collocazione_temporale"
_EVENTO_PERSIST_ANCORE = "ancore_persistenza"
_TIPI_RELAZIONE_LIBERA = frozenset(get_args(TipoRelazioneLibera))
MOTIVO_CICLO_CAUSA_LIVELLO3 = "ciclo CAUSA livello 3"

ARG_RUOLI = frozenset({"SOGG", "OGG", "OBL", "TEMPO", "LUOGO", "MODO"})

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

CROSS_DOC_ALLOWED = frozenset({"PRECEDE", "COLLEGATO"})


@dataclass
class PersistOutcome:
    nodi: int = 0
    archi: int = 0
    skipped_cross_doc: int = 0
    queries: list[str] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_prop(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _evento_by_id(sotto: SottoGrafo) -> dict[str, EventoRisolto]:
    return {event.id: event for event in sotto.eventi if event.id}


def _documento_di(sotto: SottoGrafo, event_id: str) -> str | None:
    event = _evento_by_id(sotto).get(event_id)
    if event is None:
        return None
    return event.documento or None


def _cross_doc_vietato(arco: ArcoEvento, sotto: SottoGrafo) -> bool:
    tipo = str(arco.tipo)
    if tipo in CROSS_DOC_ALLOWED:
        return False
    if tipo not in EVENT_EVENT_TIPI:
        return False
    da_doc = _documento_di(sotto, arco.da_id)
    a_doc = _documento_di(sotto, arco.a_id)
    return bool(da_doc and a_doc and da_doc != a_doc)


def archi_ammissibili(sotto: SottoGrafo) -> list[ArcoEvento]:
    """Event→event arcs that pass the parallel-spine / per-doc gate."""
    return [
        arco
        for arco in sotto.archi
        if str(arco.tipo) in EVENT_EVENT_TIPI and not _cross_doc_vietato(arco, sotto)
    ]


def _rel_id(arco: ArcoEvento) -> str:
    existing = arco.props.get("id")
    if existing:
        return str(existing)
    base = arco.props.get("base") if arco.props.get("base") is not None else ""
    segnale = arco.props.get("segnale") if arco.props.get("segnale") is not None else ""
    return content_hash(f"{arco.tipo}|{arco.da_id}|{arco.a_id}|{base}|{segnale}")


def _regola_di(value: str | None) -> str:
    return value or REGOLA


async def _run(
    session: Any,
    query: str,
    parameters: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Any:
    """Support ``run(query, **params)`` and ``run(query, parameters=dict)``."""
    params = {**(parameters or {}), **kwargs}
    try:
        raw = session.run(query, **params)
    except TypeError:
        try:
            raw = session.run(query, params)
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


def _documenti(sotto: SottoGrafo) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for event in sotto.eventi:
        doc = event.documento
        if doc and doc not in seen:
            seen.add(doc)
            ids.append(doc)
    for mention in sotto.menzioni.values():
        doc = mention.documento
        if doc and doc not in seen:
            seen.add(doc)
            ids.append(doc)
    for item in sotto.quarantena:
        doc = item.ancora_doc
        if doc and doc not in seen:
            seen.add(doc)
            ids.append(doc)
    return ids


def _backbone_tail_id(sotto: SottoGrafo, documento: str) -> str | None:
    pps = [
        event
        for event in sotto.eventi
        if event.id
        and event.piano == "PRIMO_PIANO"
        and not event.fuso_in
        and (event.documento == documento or event.documento is None)
    ]
    pps.sort(
        key=lambda event: (
            event.posizione_doc if event.posizione_doc is not None else 0,
            event.posizione_chunk if event.posizione_chunk is not None else 0,
            event.id,
        )
    )
    if pps:
        return pps[-1].id
    return None


def _chunk_count(sotto: SottoGrafo, documento: str) -> int:
    chunks: set[str] = set()
    for event in sotto.eventi:
        if event.documento in (documento, None) and event.chunk_id:
            chunks.add(event.chunk_id)
    for mention in sotto.menzioni.values():
        if mention.documento in (documento, None) and mention.chunk_id:
            chunks.add(mention.chunk_id)
    return len(chunks)


def _quarantena_id(item: QuarantenaItem) -> str:
    if item.id:
        return item.id
    return quarantena_id(
        item.ancora_doc or "",
        "",
        item.ancora_span or "",
        item.motivo or "",
    )


def _iterativita(event: EventoRisolto) -> bool:
    return bool(event.iterativita or event.iterativo)


async def _merge_documento(
    session: Any,
    sotto: SottoGrafo,
    documento: str,
    now: str,
) -> str:
    query = (
        "MERGE (d:Documento {id: $id}) "
        "SET d.versione_regole = $versione_regole, "
        "d.backbone_tail_id = $backbone_tail_id, "
        "d.chunk_count = $chunk_count, "
        "d.updated_at = $updated_at, "
        "d.regola = $regola"
    )
    await _run(
        session,
        query,
        {
            "id": documento,
            "versione_regole": RULESET_VERSION,
            "backbone_tail_id": _backbone_tail_id(sotto, documento),
            "chunk_count": _chunk_count(sotto, documento),
            "updated_at": now,
            "regola": REGOLA,
        },
    )
    return query


async def _merge_evento(session: Any, event: EventoRisolto, now: str) -> str:
    query = (
        "MERGE (e:Evento {id: $id}) "
        "ON CREATE SET e.created_at = $created_at "
        "SET e.lemma = $lemma, "
        "e.tempo = $tempo, "
        "e.polarita = $polarita, "
        "e.modalizzato = $modalizzato, "
        "e.modalizzato_forma = $modalizzato_forma, "
        "e.iterativita = $iterativita, "
        "e.fattualita = $fattualita, "
        "e.piano = $piano, "
        "e.fonte = $fonte, "
        "e.documento = $documento, "
        "e.chunk_id = $chunk_id, "
        "e.indice_chunk = $indice_chunk, "
        "e.posizione_doc = $posizione_doc, "
        "e.posizione_chunk = $posizione_chunk, "
        "e.segmentazione = $segmentazione, "
        "e.contenuto_di = $contenuto_di, "
        "e.sogg_speciale = $sogg_speciale, "
        "e.fuso_in = $fuso_in, "
        "e.tempo_assoluto = $tempo_assoluto, "
        "e.tempo_assoluto_revisioni = $tempo_assoluto_revisioni, "
        "e.regola = $regola, "
        "e.versione_regole = $versione_regole, "
        "e.e_testa = $e_testa, "
        "e.offset_inizio = $offset_inizio, "
        "e.offset_fine = $offset_fine, "
        "e.modalita = $modalita, "
        "e.polarita_negata = $polarita_negata, "
        "e.marca_dialogo = $marca_dialogo, "
        "e.ancora = $ancora, "
        "e.span = $span, "
        "e.tempo_assoluto_grezzo = $tempo_assoluto_grezzo, "
        "e.avverbio_temporale_esplicito = $avverbio_temporale_esplicito, "
        "e.connettivo_sequenziale_esplicito = $connettivo_sequenziale_esplicito, "
        "e.catena_id = coalesce($catena_id, e.catena_id), "
        "e.catena_ruolo = coalesce($catena_ruolo, e.catena_ruolo), "
        "e.catena_precedente_id = coalesce($catena_precedente_id, e.catena_precedente_id), "
        "e.catena_divergenze = coalesce($catena_divergenze, e.catena_divergenze)"
    )
    await _run(
        session,
        query,
        {
            "id": event.id,
            "created_at": event.created_at or now,
            "lemma": event.lemma,
            "tempo": event.tempo,
            "polarita": event.polarita,
            "modalizzato": event.modalizzato,
            "modalizzato_forma": event.modalizzato_forma,
            "iterativita": _iterativita(event),
            "fattualita": event.fattualita,
            "piano": event.piano,
            "fonte": event.fonte,
            "documento": event.documento,
            "chunk_id": event.chunk_id,
            "indice_chunk": event.indice_chunk,
            "posizione_doc": event.posizione_doc,
            "posizione_chunk": event.posizione_chunk,
            "segmentazione": event.segmentazione,
            "contenuto_di": event.contenuto_di,
            "sogg_speciale": event.sogg_speciale,
            "fuso_in": event.fuso_in,
            "tempo_assoluto": _json_prop(event.tempo_assoluto),
            "tempo_assoluto_revisioni": _json_prop(event.tempo_assoluto_revisioni),
            "regola": _regola_di(event.regola),
            "versione_regole": RULESET_VERSION,
            "e_testa": bool(event.e_testa),
            "offset_inizio": event.offset_inizio,
            "offset_fine": event.offset_fine,
            "modalita": event.modalita,
            "polarita_negata": bool(event.polarita_negata),
            "marca_dialogo": bool(event.marca_dialogo),
            "ancora": event.ancora,
            "span": event.span,
            "tempo_assoluto_grezzo": event.tempo_assoluto_grezzo,
            "avverbio_temporale_esplicito": bool(event.avverbio_temporale_esplicito),
            "connettivo_sequenziale_esplicito": bool(
                event.connettivo_sequenziale_esplicito
            ),
            "catena_id": event.catena_id,
            "catena_ruolo": event.catena_ruolo,
            "catena_precedente_id": event.catena_precedente_id,
            "catena_divergenze": _json_prop(list(event.catena_divergenze or [])),
        },
    )
    return query


async def _merge_menzione(session: Any, mention: MenzioneRisolta) -> str:
    query = (
        "MERGE (m:Menzione {id: $id}) "
        "SET m.forma = $forma, "
        "m.forma_canonica = $forma_canonica, "
        "m.numero = $numero, "
        "m.genere = $genere, "
        "m.tipo_superficiale = $tipo_superficiale, "
        "m.non_risolto = $non_risolto, "
        "m.documento = $documento, "
        "m.chunk_id = $chunk_id, "
        "m.regola = $regola, "
        "m.versione_regole = $versione_regole"
    )
    await _run(
        session,
        query,
        {
            "id": mention.id,
            "forma": mention.forma,
            "forma_canonica": mention.forma_canonica,
            "numero": mention.numero,
            "genere": mention.genere,
            "tipo_superficiale": mention.tipo_superficiale,
            "non_risolto": mention.non_risolto,
            "documento": mention.documento,
            "chunk_id": mention.chunk_id,
            "regola": _regola_di(mention.regola),
            "versione_regole": RULESET_VERSION,
        },
    )
    return query


async def _merge_argomento(
    session: Any,
    event: EventoRisolto,
    ruolo: str,
    menzione_id: str,
    preposizione: str | None,
    regola: str,
) -> str:
    extra = ", r.preposizione = $preposizione" if ruolo == "OBL" else ""
    query = (
        f"MATCH (e:Evento {{id: $e_id}}) "
        f"MATCH (m:Menzione {{id: $m_id}}) "
        f"MERGE (e)-[r:{ruolo}]->(m) "
        f"SET r.regola = $regola, r.versione_regole = $versione_regole{extra}"
    )
    params: dict[str, Any] = {
        "e_id": event.id,
        "m_id": menzione_id,
        "regola": regola,
        "versione_regole": RULESET_VERSION,
    }
    if ruolo == "OBL":
        params["preposizione"] = preposizione
    await _run(session, query, params)
    return query


async def _merge_arco(
    session: Any,
    arco: ArcoEvento,
    *,
    job_id: str | None,
) -> str:
    tipo = str(arco.tipo)
    query = (
        f"MATCH (da:Evento {{id: $da_id}}) "
        f"MATCH (a:Evento {{id: $a_id}}) "
        f"MERGE (da)-[r:{tipo} {{id: $rel_id}}]->(a) "
        f"SET r.base = $base, r.segnale = $segnale, r.run_id = $run_id, "
        f"r.regola = $regola, r.versione_regole = $versione_regole, "
        f"r.superato_da = $superato_da, r.conflitto = $conflitto, r.ancora = $ancora, "
        f"r.relazione_allen = $relazione_allen, r.livello = $livello, "
        f"r.confidenza = $confidenza"
    )
    await _run(
        session,
        query,
        {
            "da_id": arco.da_id,
            "a_id": arco.a_id,
            "rel_id": _rel_id(arco),
            "base": arco.props.get("base"),
            "segnale": arco.props.get("segnale"),
            "run_id": arco.props.get("run_id", job_id),
            "regola": _regola_di(arco.props.get("regola")),
            "versione_regole": RULESET_VERSION,
            "superato_da": arco.props.get("superato_da"),
            "conflitto": arco.props.get("conflitto"),
            "ancora": arco.props.get("ancora"),
            "relazione_allen": arco.props.get("relazione_allen"),
            "livello": arco.props.get("livello"),
            "confidenza": arco.props.get("confidenza"),
        },
    )
    return query


async def _merge_quarantena(session: Any, item: QuarantenaItem) -> str:
    query = (
        "MERGE (q:Quarantena {id: $id}) "
        "SET q.frammento = $frammento, "
        "q.motivo = $motivo, "
        "q.ancora_doc = $ancora_doc, "
        "q.ancora_chunk = $ancora_chunk, "
        "q.ancora_span = $ancora_span, "
        "q.regola = $regola, "
        "q.versione_regole = $versione_regole"
    )
    await _run(
        session,
        query,
        {
            "id": _quarantena_id(item),
            "frammento": item.frammento,
            "motivo": item.motivo,
            "ancora_doc": item.ancora_doc,
            "ancora_chunk": item.ancora_chunk,
            "ancora_span": item.ancora_span,
            "regola": REGOLA,
            "versione_regole": item.versione_regole or RULESET_VERSION,
        },
    )
    return query


async def persisti(
    session: Any,
    sotto: SottoGrafo,
    *,
    job_id: str | None = None,
) -> PersistOutcome:
    """MERGE the document subgraph. Idempotent; append-only."""
    now = _now_iso()
    queries: list[str] = []
    nodi = 0
    archi = 0

    for documento in _documenti(sotto):
        queries.append(await _merge_documento(session, sotto, documento, now))
        nodi += 1

    for event in sotto.eventi:
        if not event.id:
            continue
        queries.append(await _merge_evento(session, event, now))
        nodi += 1

    for mention in sotto.menzioni.values():
        if not mention.id:
            continue
        queries.append(await _merge_menzione(session, mention))
        nodi += 1

    for event in sotto.eventi:
        if not event.id:
            continue
        regola = _regola_di(event.regola)
        for arg in event.argomenti:
            ruolo = str(arg.ruolo)
            if ruolo not in ARG_RUOLI:
                continue
            if not arg.menzione_id:
                continue
            queries.append(
                await _merge_argomento(
                    session, event, ruolo, arg.menzione_id, arg.preposizione, regola
                )
            )
            archi += 1

    # SEQUENZA (ordine narrativo asserito) copre già la coppia: un COLLEGATO
    # (placeholder d'ordine debole) fra gli stessi due eventi è ridondante e
    # non si scrive. Micro-fix: nessun arco cancellato, solo non persistito.
    _coppie_sequenza = {
        frozenset((arco.da_id, arco.a_id))
        for arco in sotto.archi
        if str(arco.tipo) == "SEQUENZA"
        and arco.da_id
        and arco.a_id
        and not _cross_doc_vietato(arco, sotto)  # una SEQUENZA non scritta non copre nulla
    }

    skipped = 0
    for arco in sotto.archi:
        tipo = str(arco.tipo)
        if tipo not in EVENT_EVENT_TIPI:
            continue
        if tipo == "COLLEGATO" and frozenset((arco.da_id, arco.a_id)) in _coppie_sequenza:
            skipped += 1
            continue
        if _cross_doc_vietato(arco, sotto):
            skipped += 1
            continue
        queries.append(await _merge_arco(session, arco, job_id=job_id))
        archi += 1

    for item in sotto.quarantena:
        queries.append(await _merge_quarantena(session, item))
        nodi += 1

    return PersistOutcome(
        nodi=nodi,
        archi=archi,
        skipped_cross_doc=skipped,
        queries=queries,
    )


async def sopprimi_collegato_ridondanti(session: Any, doc_id: str) -> int:
    """Same append-only rule as everywhere else: SET, never DELETE.

    A COLLEGATO (weak, non-committal order placeholder) between two events
    that also have a SEQUENZA (asserted narrative order) says strictly less
    than the SEQUENZA and adds no information — unlike PRECEDE/CAUSA, which
    are independent semantic claims kept *alongside* a SEQUENZA on purpose
    (see ``pipeline.collega_dorsale_eventi``). The redundant COLLEGATO can
    only exist because the intra-zone COLLEGATO (``chiusura_temporale``,
    ``sentence_pair_linking``) is persisted before the document-wide
    exposition SEQUENZA is computed, so the in-memory dedup in ``persisti()``
    above never sees it in time. Call once per ingestion, after the dorsale
    arcs are persisted.

    Marks ``superato_da`` with the covering SEQUENZA's id — the same
    convention ``query_structured``'s traversals already read on COLLEGATO
    (``superato_da IS NOT NULL`` → excluded). The graph-view queries
    (``catalog._EDGES_CYPHER`` / ``_L1_ORDER_EDGES_CYPHER``) apply the same
    filter, so a suppressed COLLEGATO never renders. Nothing is removed.
    """
    rows = await _run(
        session,
        "MATCH (a:Evento {documento: $doc_id})-[c:COLLEGATO]-(b:Evento) "
        "WHERE c.superato_da IS NULL AND EXISTS { MATCH (a)-[:SEQUENZA]-(b) } "
        "WITH DISTINCT c "
        "SET c.superato_da = 'sequenza' "
        "RETURN count(c) AS marcati",
        doc_id=doc_id,
    )
    if rows:
        first = rows[0]
        value = first.get("marcati") if isinstance(first, dict) else None
        if isinstance(value, int):
            return value
    return 0


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        if not value.strip():
            return []
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return [value]
        if isinstance(parsed, list):
            return [str(item) for item in parsed if item is not None]
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if item is not None]
    return []


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no", ""}:
            return False
    return bool(value)


def _row_mapping(row: Any) -> dict[str, Any]:
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
                try:
                    data = {key: row[key] for key in keys()}
                except Exception:
                    data = {}
    nested = data.get("z") or data.get("d")
    if nested is not None and not isinstance(nested, (str, int, float, bool, list)):
        try:
            nested_map = dict(nested)
        except Exception:
            nested_map = None
        if nested_map:
            data = {**nested_map, **{k: v for k, v in data.items() if k not in {"z", "d"}}}
    return data


def _zona_from_row(row: Any) -> Zona | None:
    data = _row_mapping(row)
    zona_id = data.get("id") or data.get("z.id")
    if not zona_id:
        return None
    return Zona(
        id=str(zona_id),
        documento=str(data.get("documento") or data.get("z.documento") or ""),
        offset_inizio=_as_int(data.get("offset_inizio", data.get("z.offset_inizio"))),
        offset_fine=_as_int(data.get("offset_fine", data.get("z.offset_fine"))),
        ordinale=_as_int(data.get("ordinale", data.get("z.ordinale"))),
        testo=str(data.get("testo") or data.get("z.testo") or ""),
        riassunto=str(data.get("riassunto") or data.get("z.riassunto") or ""),
        entita_principali=_as_str_list(
            data.get("entita_principali", data.get("z.entita_principali"))
        ),
        ancore_temporali=_as_str_list(
            data.get("ancore_temporali", data.get("z.ancore_temporali"))
        ),
        evento_centrale=_optional_str(
            data.get("evento_centrale", data.get("z.evento_centrale"))
        ),
        espansa=_as_bool(data.get("espansa", data.get("z.espansa"))),
        regola=str(data.get("regola") or data.get("z.regola") or "M0_texttiling"),
        versione_regole=str(
            data.get("versione_regole") or data.get("z.versione_regole") or RULESET_VERSION
        ),
        avviso=_optional_str(data.get("avviso", data.get("z.avviso"))),
        su_via_principale=_as_str_list(
            data.get("su_via_principale", data.get("z.su_via_principale"))
        ),
    )


def _arco_macro_from_row(row: Any) -> ArcoZona | None:
    data = _row_mapping(row)
    da_id = data.get("da_id") or data.get("da.id")
    a_id = data.get("a_id") or data.get("a.id")
    tipo = data.get("tipo") or data.get("type(r)")
    if not da_id or not a_id or not tipo:
        return None
    verificato = data.get("verificato")
    if verificato is not None and not isinstance(verificato, bool):
        verificato = _as_bool(verificato)
    confidenza = data.get("confidenza")
    try:
        confidenza_f = float(confidenza) if confidenza is not None else 0.0
    except (TypeError, ValueError):
        confidenza_f = 0.0
    return ArcoZona(
        da_id=str(da_id),
        a_id=str(a_id),
        tipo=str(tipo),
        livello="macro",
        confidenza=confidenza_f,
        verificato=verificato if isinstance(verificato, bool) or verificato is None else None,
        segnale=_optional_str(data.get("segnale")),
        regola=str(data.get("regola") or REGOLA),
        versione_regole=str(data.get("versione_regole") or RULESET_VERSION),
        su_via_principale=_as_str_list(data.get("su_via_principale")),
    )


async def _query_rows(
    session: Any, query: str, parameters: dict[str, Any] | None = None
) -> list[Any]:
    raw = await _run(session, query, parameters)
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    data_fn = getattr(raw, "data", None)
    if callable(data_fn):
        rows = data_fn()
        if inspect.isawaitable(rows):
            rows = await rows
        return list(rows or [])
    return [raw]


_ZONA_RETURN = (
    "z.id AS id, z.documento AS documento, z.ordinale AS ordinale, "
    "z.espansa AS espansa, z.offset_inizio AS offset_inizio, "
    "z.offset_fine AS offset_fine, z.su_via_principale AS su_via_principale, "
    "z.riassunto AS riassunto, z.testo AS testo, "
    "z.entita_principali AS entita_principali, "
    "z.ancore_temporali AS ancore_temporali, "
    "z.evento_centrale AS evento_centrale, z.regola AS regola, "
    "z.versione_regole AS versione_regole, z.avviso AS avviso"
)


async def persisti_documento(
    session: Any, doc_id: str, *, testo: str | None = None
) -> str:
    """MERGE ``:Documento`` so re-expand can recover source text."""
    query = (
        "MERGE (d:Documento {id: $id}) "
        "SET d.versione_regole = $versione_regole, "
        "d.updated_at = $updated_at, "
        "d.regola = $regola, "
        "d.testo = $testo"
    )
    await _run(
        session,
        query,
        {
            "id": doc_id,
            "versione_regole": RULESET_VERSION,
            "updated_at": _now_iso(),
            "regola": REGOLA,
            "testo": testo,
        },
    )
    return query


async def persisti_zona(session: Any, zona: Zona) -> str:
    """Idempotent MERGE on ``:Zona.id`` including ``testo`` (needed to re-expand)."""
    query = (
        "MERGE (z:Zona {id: $id}) "
        "SET z.documento = $documento, "
        "z.offset_inizio = $offset_inizio, "
        "z.offset_fine = $offset_fine, "
        "z.ordinale = $ordinale, "
        "z.testo = $testo, "
        "z.riassunto = $riassunto, "
        "z.entita_principali = $entita_principali, "
        "z.ancore_temporali = $ancore_temporali, "
        "z.evento_centrale = $evento_centrale, "
        "z.espansa = $espansa, "
        "z.regola = $regola, "
        "z.versione_regole = $versione_regole, "
        "z.su_via_principale = $su_via_principale, "
        "z.avviso = $avviso"
    )
    await _run(
        session,
        query,
        {
            "id": zona.id,
            "documento": zona.documento,
            "offset_inizio": zona.offset_inizio,
            "offset_fine": zona.offset_fine,
            "ordinale": zona.ordinale,
            "testo": zona.testo,
            "riassunto": zona.riassunto,
            "entita_principali": list(zona.entita_principali),
            "ancore_temporali": list(zona.ancore_temporali),
            "evento_centrale": zona.evento_centrale,
            "espansa": zona.espansa,
            "regola": zona.regola,
            "versione_regole": zona.versione_regole or RULESET_VERSION,
            "su_via_principale": list(zona.su_via_principale),
            "avviso": zona.avviso,
        },
    )
    return query


async def persisti_zone(session: Any, zone: list[Zona]) -> None:
    for zona in zone:
        await persisti_zona(session, zona)


async def persisti_arco_macro(session: Any, arco: ArcoZona) -> str | None:
    tipo = str(arco.tipo)
    if tipo not in EVENT_EVENT_TIPI:
        return None
    query = (
        f"MATCH (da:Zona {{id: $da_id}}) "
        f"MATCH (a:Zona {{id: $a_id}}) "
        f"MERGE (da)-[r:{tipo} {{livello: $livello}}]->(a) "
        f"SET r.confidenza = $confidenza, "
        f"r.verificato = $verificato, "
        f"r.segnale = $segnale, "
        f"r.regola = $regola, "
        f"r.versione_regole = $versione_regole, "
        f"r.su_via_principale = $su_via_principale, "
        f"r.tipo = $tipo, "
        f"r.livello = $livello"
    )
    await _run(
        session,
        query,
        {
            "da_id": arco.da_id,
            "a_id": arco.a_id,
            "livello": arco.livello or "macro",
            "confidenza": float(arco.confidenza),
            "verificato": arco.verificato,
            "segnale": arco.segnale,
            "regola": arco.regola,
            "versione_regole": arco.versione_regole or RULESET_VERSION,
            "su_via_principale": list(arco.su_via_principale),
            "tipo": tipo,
        },
    )
    return query


async def persisti_archi_macro(session: Any, archi: list[ArcoZona]) -> None:
    for arco in archi:
        await persisti_arco_macro(session, arco)


async def _merge_successione_zona(
    session: Any,
    id_a: str,
    id_b: str,
    riassunto: str,
    job_id: str | None,
) -> str:
    """Idempotent Zona→Zona SUCCESSIONE_ZONA. MERGE on type, not a unique rel id."""
    del job_id  # accepted for caller symmetry; not stored on the arc
    query = (
        f"MATCH (za:Zona {{id: $id_a}}) "
        f"MATCH (zb:Zona {{id: $id_b}}) "
        f"MERGE (za)-[r:{SUCCESSIONE_ZONA}]->(zb) "
        f"SET r.riassunto_transizione = $riassunto, "
        f"r.regola = $regola, "
        f"r.versione_regole = $versione_regole"
    )
    await _run(
        session,
        query,
        {
            "id_a": id_a,
            "id_b": id_b,
            "riassunto": riassunto,
            "regola": REGOLA_SUCCESSIONE_ZONA,
            "versione_regole": RULESET_VERSION,
        },
    )
    return query


async def persisti_transizioni_zona(
    session: Any,
    transizioni: dict,
    job_id: str | None,
) -> None:
    for key, riassunto in (transizioni or {}).items():
        if not isinstance(key, (tuple, list)) or len(key) != 2:
            continue
        id_a, id_b = key
        text = riassunto if isinstance(riassunto, str) else str(riassunto or "")
        if not text.strip():
            continue
        await _merge_successione_zona(session, str(id_a), str(id_b), text, job_id)


def _evento_overlay(
    eventi: list[EventoRisolto] | None,
) -> dict[str, EventoRisolto]:
    if not eventi:
        return {}
    return {event.id: event for event in eventi if event.id}


def _known_evento_ids(overlay: dict[str, EventoRisolto]) -> set[str] | None:
    if not overlay:
        return None
    return set(overlay)


def _evento_id_known(evento_id: str, known: set[str] | None) -> bool:
    if not evento_id:
        return False
    if known is None:
        return True
    return evento_id in known


@dataclass(frozen=True)
class _PropsCluster:
    """Le proprietà derivate di un ``ClusterTemporale``, già pronte per Cypher.

    ``chiave_ordine`` e ``posizione_doc_min`` sono le uniche calcolate qui e non
    dichiarate dal modello, e vivono su **due scale diverse** che non vanno
    mescolate: la prima è in sedicesimi di secondo dall'anno 1 (ordine 9e11), la
    seconda è l'ordinale di esposizione di un evento nel documento (ordine 10).
    Un cluster senza collocazione ha ``chiave_ordine`` a ``null``, mai un
    surrogato preso dall'altra scala: sommare le due metterebbe ogni cluster non
    datato all'estrema sinistra dell'asse.
    """

    descrizione: str | None
    granularita: str | None
    inizio: str | None
    fine: str | None
    chiave_ordine: int | None
    posizione_doc_min: int | None
    stimato: bool
    confidenza: float


def _prop_testo(valore: Any) -> str | None:
    if not isinstance(valore, str):
        return None
    return valore.strip() or None


def _prop_confidenza(valore: Any) -> float:
    """Confidenza in [0, 1]; un valore illeggibile vale 1.0, mai un errore."""
    try:
        numero = float(valore)
    except (TypeError, ValueError):
        return 1.0
    if numero != numero:
        return 1.0
    return min(1.0, max(0.0, numero))


def _etichetta_cluster(cluster: ClusterTemporaleProposto) -> str:
    return (cluster.etichetta or "").strip()


def _cluster_per_etichetta(
    clusters: list[ClusterTemporaleProposto],
) -> dict[str, ClusterTemporaleProposto]:
    """Cluster per etichetta, la stessa chiave che entra in ``cluster_temporale_id``.

    Il primo vince: MT4 garantisce etichette distinte dentro il documento, e se
    la garanzia saltasse due cluster omonimi finirebbero comunque in un nodo
    solo, quindi tenerne uno è più onesto che sovrascriverne le proprietà.
    """
    fuori: dict[str, ClusterTemporaleProposto] = {}
    for cluster in clusters:
        etichetta = _etichetta_cluster(cluster)
        if etichetta and etichetta not in fuori:
            fuori[etichetta] = cluster
    return fuori


def _padri_per_etichetta(
    clusters: list[ClusterTemporaleProposto],
) -> dict[str, str]:
    """Figlio → padre, letto dagli archi che MT4 ha già validato."""
    return {
        figlio: padre
        for padre, figlio in foresta_temporale.archi_contiene(clusters)
    }


def _antenati(etichetta: str, padre_di: dict[str, str]) -> set[str]:
    fuori: set[str] = set()
    corrente = padre_di.get(etichetta)
    while corrente is not None and corrente not in fuori:
        fuori.add(corrente)
        corrente = padre_di.get(corrente)
    return fuori


def _rango_specificita(cluster: ClusterTemporaleProposto) -> int:
    """Quanto è stretto il cluster: 0 = secondi, il massimo = granularità ignota.

    Un cluster ``relativo``/``simbolico`` non ha una granularità leggibile e
    conta come il più largo di tutti, così fra due cluster non imparentati che
    reclamano lo stesso evento vince quello datato.
    """
    rango = tempo_iso.rango_granularita(
        foresta_temporale.granularita_effettiva(cluster)
    )
    return len(GRANULARITA_TEMPORALI) if rango is None else rango


def _foglia_per_evento(
    per_etichetta: dict[str, ClusterTemporaleProposto],
    padre_di: dict[str, str],
    known: set[str] | None,
) -> dict[str, str]:
    """Evento → l'unico cluster a cui appende, il più specifico che lo dichiara.

    Il piano vuole ``APPARTIENE_A`` **solo sulla foglia**: i cluster più grossi
    si ricavano risalendo ``CONTIENE``, quindi un arco verso un antenato non
    aggiunge informazione e raddoppia il box in cui l'evento comparirebbe.
    Niente garantisce che il modello non abbia messo lo stesso evento anche in
    un contenitore, perciò un candidato che è antenato di un altro candidato
    viene scartato. Restano i candidati non imparentati, dove "più specifico"
    non è definito dalla gerarchia: lì decide la granularità più fine, e a pari
    granularità l'etichetta, perché la scelta dev'essere la stessa a ogni run.
    """
    candidati: dict[str, list[str]] = {}
    for etichetta, cluster in per_etichetta.items():
        for raw in cluster.eventi or []:
            evento_id = str(raw or "").strip()
            if not _evento_id_known(evento_id, known):
                continue
            lista = candidati.setdefault(evento_id, [])
            if etichetta not in lista:
                lista.append(etichetta)

    fuori: dict[str, str] = {}
    for evento_id, etichette in candidati.items():
        antenati = {a for e in etichette for a in _antenati(e, padre_di)}
        discendenti = [e for e in etichette if e not in antenati] or etichette
        fuori[evento_id] = min(
            discendenti,
            key=lambda e: (_rango_specificita(per_etichetta[e]), e),
        )
    return fuori


def _posizione_doc_minima(
    per_etichetta: dict[str, ClusterTemporaleProposto],
    padre_di: dict[str, str],
    overlay: dict[str, EventoRisolto],
) -> dict[str, int | None]:
    """Minima ``posizione_doc`` del **sottoalbero** di ogni cluster.

    È il dato con cui MT8 ordina i cluster che ``chiave_ordine`` lascia a
    ``null`` ("mai l'id"), e va calcolato sul sottoalbero e non sui soli membri
    diretti: un contenitore puro non ha eventi propri, quindi sui membri diretti
    resterebbe senza nessuna delle due chiavi.
    """
    fuori: dict[str, int | None] = {}
    for etichetta, cluster in per_etichetta.items():
        fuori.setdefault(etichetta, None)
        posizioni = [
            evento.posizione_doc
            for raw in (cluster.eventi or [])
            if (evento := overlay.get(str(raw or "").strip())) is not None
            and isinstance(evento.posizione_doc, int)
        ]
        if not posizioni:
            continue
        minima = min(posizioni)
        corrente: str | None = etichetta
        visti: set[str] = set()
        while corrente is not None and corrente not in visti:
            visti.add(corrente)
            attuale = fuori.get(corrente)
            if attuale is None or minima < attuale:
                fuori[corrente] = minima
            corrente = padre_di.get(corrente)
    return fuori


def _props_cluster(
    cluster: ClusterTemporaleProposto,
    posizione_doc_min: int | None,
) -> _PropsCluster:
    return _PropsCluster(
        descrizione=_prop_testo(cluster.descrizione),
        granularita=_prop_testo(cluster.granularita),
        inizio=_prop_testo(cluster.inizio),
        fine=_prop_testo(cluster.fine),
        chiave_ordine=foresta_temporale.chiave_ordine_cluster(cluster),
        posizione_doc_min=posizione_doc_min,
        stimato=bool(cluster.stimato),
        confidenza=_prop_confidenza(cluster.confidenza),
    )


def _segnale_per_evento(
    livello_tempo: LivelloTemporaleResult,
) -> dict[str, SegnaleTemporaleEvento]:
    fuori: dict[str, SegnaleTemporaleEvento] = {}
    for segnale in livello_tempo.segnali or []:
        evento_id = str(segnale.evento_id or "").strip()
        if evento_id and evento_id not in fuori:
            fuori[evento_id] = segnale
    return fuori


def _peso_appartenenza(
    cluster: ClusterTemporaleProposto,
    segnale: SegnaleTemporaleEvento | None,
) -> tuple[float, bool]:
    """``confidenza``/``stimato`` dell'arco: raggruppamento **e** collocazione.

    Le due sorgenti dicono cose diverse e nessuna delle due da sola descrive
    l'arco. La confidenza del ``ClusterTemporaleProposto`` è quella del
    *raggruppamento* ed è già sul nodo, dove il gate di MT3 l'ha filtrata; il
    ``SegnaleTemporaleEvento`` porta quella della *collocazione* del singolo
    evento, che è l'unica cosa che distingue un membro dall'altro dentro lo
    stesso cluster. L'arco afferma la congiunzione delle due ("questo evento sta
    davvero qui"), quindi non è più sicuro della meno sicura: ``confidenza`` è
    il minimo e ``stimato`` è l'or logico. Senza segnale i default del modello
    (1.0 / False) fanno cadere l'arco esattamente sui valori del cluster.
    """
    confidenza = _prop_confidenza(cluster.confidenza)
    stimato = bool(cluster.stimato)
    if segnale is not None:
        confidenza = min(confidenza, _prop_confidenza(segnale.confidenza))
        stimato = stimato or bool(segnale.stimato)
    return confidenza, stimato


async def _merge_cluster_temporale(
    session: Any,
    cid: str,
    doc_id: str,
    etichetta: str,
    tipo: str,
    props: _PropsCluster,
) -> str:
    query = (
        "MERGE (c:ClusterTemporale {id: $id}) "
        "SET c.documento = $documento, "
        "c.etichetta = $etichetta, "
        "c.tipo = $tipo, "
        "c.descrizione = $descrizione, "
        "c.granularita = $granularita, "
        "c.inizio = $inizio, "
        "c.fine = $fine, "
        "c.chiave_ordine = $chiave_ordine, "
        "c.posizione_doc_min = $posizione_doc_min, "
        "c.stimato = $stimato, "
        "c.confidenza = $confidenza, "
        "c.regola = $regola, "
        "c.versione_regole = $versione_regole"
    )
    await _run(
        session,
        query,
        {
            "id": cid,
            "documento": doc_id,
            "etichetta": etichetta,
            "tipo": tipo,
            "descrizione": props.descrizione,
            "granularita": props.granularita,
            "inizio": props.inizio,
            "fine": props.fine,
            "chiave_ordine": props.chiave_ordine,
            "posizione_doc_min": props.posizione_doc_min,
            "stimato": props.stimato,
            "confidenza": props.confidenza,
            "regola": REGOLA_LIVELLO_TEMPORALE,
            "versione_regole": RULESET_VERSION,
        },
    )
    return query


async def _merge_contiene(session: Any, padre_id: str, figlio_id: str) -> str:
    query = (
        "MATCH (p:ClusterTemporale {id: $p_id}) "
        "MATCH (f:ClusterTemporale {id: $f_id}) "
        "MERGE (p)-[r:CONTIENE]->(f) "
        "SET r.attivo = true, "
        "r.regola = $regola, "
        "r.versione_regole = $versione_regole"
    )
    await _run(
        session,
        query,
        {
            "p_id": padre_id,
            "f_id": figlio_id,
            "regola": REGOLA_LIVELLO_TEMPORALE,
            "versione_regole": RULESET_VERSION,
        },
    )
    return query


async def _disattiva_contiene_obsoleti(
    session: Any,
    figlio_id: str,
    padre_id: str | None,
) -> str:
    """Marca ``attivo = false`` gli altri ``CONTIENE`` entranti nel figlio.

    Il piano chiede una **foresta** (≤ 1 padre) e insieme "nessuna
    cancellazione". Una re-ingestione dello stesso documento rigenera lo stesso
    id per un cluster la cui etichetta non è cambiata, ma può annidarlo sotto un
    padre diverso — o non annidarlo più: il solo ``MERGE`` lascerebbe due archi
    entranti, e la vista compound di MT6, che da ``CONTIENE`` ricava un
    ``data.parent`` singolo, sceglierebbe a caso.

    La soluzione non ha bisogno di un'eccezione al "nessuna cancellazione":
    l'arco superato resta nel grafo con ``attivo = false`` e
    ``sostituito_da`` che dice dove è finito il figlio (``null`` se è tornato
    radice). Chi legge la gerarchia filtra ``attivo``; chi legge la storia delle
    ingestioni la trova ancora tutta. ``padre_id`` a ``null`` disattiva ogni
    padre, ed è il caso del cluster che ha perso l'annidamento.
    """
    query = (
        "MATCH (p:ClusterTemporale)-[r:CONTIENE]->(f:ClusterTemporale {id: $f_id}) "
        "WHERE $p_id IS NULL OR p.id <> $p_id "
        "SET r.attivo = false, "
        "r.sostituito_da = $p_id, "
        "r.versione_regole = $versione_regole"
    )
    await _run(
        session,
        query,
        {
            "f_id": figlio_id,
            "p_id": padre_id,
            "versione_regole": RULESET_VERSION,
        },
    )
    return query


async def _merge_appartiene_a(
    session: Any,
    evento_id: str,
    cluster_id: str,
    confidenza: float,
    stimato: bool,
) -> str:
    query = (
        "MATCH (e:Evento {id: $e_id}) "
        "MATCH (c:ClusterTemporale {id: $c_id}) "
        "MERGE (e)-[r:APPARTIENE_A]->(c) "
        "SET r.attivo = true, "
        "r.confidenza = $confidenza, "
        "r.stimato = $stimato, "
        "r.regola = $regola, "
        "r.versione_regole = $versione_regole"
    )
    await _run(
        session,
        query,
        {
            "e_id": evento_id,
            "c_id": cluster_id,
            "confidenza": confidenza,
            "stimato": stimato,
            "regola": REGOLA_LIVELLO_TEMPORALE,
            "versione_regole": RULESET_VERSION,
        },
    )
    return query


async def _disattiva_appartiene_a_obsoleti(
    session: Any,
    evento_id: str,
    cluster_id: str,
) -> str:
    """Stessa tombstone di ``_disattiva_contiene_obsoleti``, per la foglia.

    "Solo al cluster foglia" è un invariante per evento, non per run: una
    re-ingestione che sposta l'evento in un cluster con un'etichetta diversa
    genera un id diverso e quindi un secondo arco, e ``Evento.data.parent`` in
    MT6 tornerebbe ambiguo. L'arco vecchio resta, disattivato.
    """
    query = (
        "MATCH (e:Evento {id: $e_id})-[r:APPARTIENE_A]->(c:ClusterTemporale) "
        "WHERE c.id <> $c_id "
        "SET r.attivo = false, "
        "r.sostituito_da = $c_id, "
        "r.versione_regole = $versione_regole"
    )
    await _run(
        session,
        query,
        {
            "e_id": evento_id,
            "c_id": cluster_id,
            "versione_regole": RULESET_VERSION,
        },
    )
    return query


async def _merge_precede_livello(session: Any, da_id: str, a_id: str) -> str:
    query = (
        "MATCH (da:Evento {id: $da}) "
        "MATCH (a:Evento {id: $a}) "
        "MERGE (da)-[r:PRECEDE]->(a) "
        "SET r.regola = coalesce(r.regola, $regola), "
        "r.versione_regole = $versione_regole, "
        "r.base = coalesce(r.base, $base)"
    )
    await _run(
        session,
        query,
        {
            "da": da_id,
            "a": a_id,
            "regola": REGOLA_LIVELLO_TEMPORALE,
            "versione_regole": RULESET_VERSION,
            "base": "livello_temporale",
        },
    )
    return query


async def _merge_contemporaneo(session: Any, da_id: str, a_id: str) -> str:
    query = (
        "MATCH (da:Evento {id: $da}) "
        "MATCH (a:Evento {id: $a}) "
        "MERGE (da)-[r:CONTEMPORANEO]->(a) "
        "SET r.regola = $regola, r.versione_regole = $versione_regole"
    )
    await _run(
        session,
        query,
        {
            "da": da_id,
            "a": a_id,
            "regola": REGOLA_LIVELLO_TEMPORALE,
            "versione_regole": RULESET_VERSION,
        },
    )
    return query


async def _set_tempo_assoluto(
    session: Any,
    evento_id: str,
    tempo_assoluto: Any,
    revisioni: Any,
) -> str:
    query = (
        "MATCH (e:Evento {id: $id}) "
        "SET e.tempo_assoluto = $tempo_assoluto, "
        "e.tempo_assoluto_revisioni = $revisioni"
    )
    await _run(
        session,
        query,
        {
            "id": evento_id,
            "tempo_assoluto": _json_prop(tempo_assoluto),
            "revisioni": _json_prop(revisioni),
        },
    )
    return query


def _tempo_from_overlay(event: EventoRisolto) -> tuple[Any, Any] | None:
    tempo = event.tempo_assoluto
    revisioni = event.tempo_assoluto_revisioni
    if tempo is None and not revisioni:
        return None
    return tempo, list(revisioni or [])


# Unused by the live pipeline (MT10). ClusterTemporale writer kept so isolation
# tests of this function still run. Flag off does not re-enable this path.
async def persisti_livello_temporale(
    session: Any,
    livello_tempo: LivelloTemporaleResult | None,
    doc_id: str,
    job_id: str | None,
    eventi: list[EventoRisolto] | None = None,
    coppie_precede: set[frozenset[str]] | None = None,
) -> None:
    """MERGE ClusterTemporale / CONTIENE / APPARTIENE_A / CONTEMPORANEO.

    Append-only e best-effort: nulla viene cancellato (gli archi superati da una
    re-ingestione restano con ``attivo = false``) e nessuna eccezione risale al
    chiamante. La scrittura è divisa in tre passate — prima tutti i nodi, poi
    tutti i ``CONTIENE``, poi tutti gli ``APPARTIENE_A`` — perché gli archi sono
    ``MATCH``-based e un nodo mancante li farebbe sparire in silenzio. MT4
    consegna i cluster in ordine canonico (padri prima dei figli), ma
    appoggiarsi a quell'ordine legherebbe la persistenza a un invariante deciso
    due moduli più a monte: un chiamante che passasse una lista non ordinata
    perderebbe archi senza un errore. Tre passate non costano nulla e non
    dipendono dall'ordine d'ingresso.
    """
    del job_id
    if livello_tempo is None:
        return

    overlay = _evento_overlay(eventi)
    known = _known_evento_ids(overlay)

    clusters = [
        cluster
        for cluster in (livello_tempo.cluster or [])
        if _etichetta_cluster(cluster)
    ]
    per_etichetta = _cluster_per_etichetta(clusters)
    padre_di = _padri_per_etichetta(clusters)
    posizioni = _posizione_doc_minima(per_etichetta, padre_di, overlay)
    foglia_di = _foglia_per_evento(per_etichetta, padre_di, known)
    segnali = _segnale_per_evento(livello_tempo)

    # Un cluster senza eventi propri non è saltato: dopo MT4 i contenitori puri
    # sono esattamente i nodi che reggono la gerarchia, e saltarli azzererebbe
    # i CONTIENE.
    cid_di: dict[str, str] = {}
    for etichetta, cluster in per_etichetta.items():
        tipo = str(cluster.tipo)
        cid = cluster_temporale_id(doc_id, etichetta, tipo)
        cid_di[etichetta] = cid
        with suppress(Exception):
            await _merge_cluster_temporale(
                session,
                cid,
                doc_id,
                etichetta,
                tipo,
                _props_cluster(cluster, posizioni.get(etichetta)),
            )

    for etichetta, cid in cid_di.items():
        padre_cid = cid_di.get(padre_di.get(etichetta, ""))
        # Due blocchi e non uno: la tombstone è una pulizia, e se fallisce
        # l'arco vero va scritto lo stesso.
        with suppress(Exception):
            await _disattiva_contiene_obsoleti(session, cid, padre_cid)
        if padre_cid is None:
            continue
        with suppress(Exception):
            await _merge_contiene(session, padre_cid, cid)

    for etichetta, cluster in per_etichetta.items():
        cid = cid_di[etichetta]
        emessi: set[str] = set()
        for raw_id in cluster.eventi or []:
            evento_id = str(raw_id or "").strip()
            if evento_id in emessi or foglia_di.get(evento_id) != etichetta:
                continue
            emessi.add(evento_id)
            confidenza, stimato = _peso_appartenenza(
                cluster, segnali.get(evento_id)
            )
            with suppress(Exception):
                await _disattiva_appartiene_a_obsoleti(session, evento_id, cid)
            with suppress(Exception):
                await _merge_appartiene_a(
                    session, evento_id, cid, confidenza, stimato
                )

    vietate = set(coppie_precede or ())
    seen_precede: set[tuple[str, str]] = set()
    for segnale in livello_tempo.segnali or []:
        evento_id = str(segnale.evento_id or "").strip()
        for other in segnale.precede or []:
            other_id = str(other or "").strip()
            if not evento_id or not other_id or evento_id == other_id:
                continue
            if not _evento_id_known(evento_id, known) or not _evento_id_known(
                other_id, known
            ):
                continue
            pair = (evento_id, other_id)
            if pair in seen_precede:
                continue
            seen_precede.add(pair)
            vietate.add(frozenset((evento_id, other_id)))
            with suppress(Exception):
                await _merge_precede_livello(session, evento_id, other_id)

    seen_pairs: set[tuple[str, str]] = set()
    for segnale in livello_tempo.segnali or []:
        evento_id = str(segnale.evento_id or "").strip()
        for other in segnale.contemporaneo_a or []:
            other_id = str(other or "").strip()
            if not evento_id or not other_id or evento_id == other_id:
                continue
            if not _evento_id_known(evento_id, known) or not _evento_id_known(
                other_id, known
            ):
                continue
            da_id, a_id = sorted((evento_id, other_id))
            pair = (da_id, a_id)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            if frozenset((da_id, a_id)) in vietate:
                continue
            await _merge_contemporaneo(session, da_id, a_id)

    written_tempo: set[str] = set()
    for event_id, event in overlay.items():
        payload = _tempo_from_overlay(event)
        if payload is None:
            continue
        tempo, revisioni = payload
        await _set_tempo_assoluto(session, event_id, tempo, revisioni)
        written_tempo.add(event_id)

    for segnale in livello_tempo.segnali or []:
        evento_id = str(segnale.evento_id or "").strip()
        if not evento_id or evento_id in written_tempo:
            continue
        if not _evento_id_known(evento_id, known):
            continue
        raw_tempo = segnale.tempo_assoluto
        if raw_tempo is None:
            continue
        if isinstance(raw_tempo, str) and not raw_tempo.strip():
            continue
        overlay_event = overlay.get(evento_id)
        if overlay_event is not None:
            tempo = (
                overlay_event.tempo_assoluto
                if overlay_event.tempo_assoluto is not None
                else raw_tempo
            )
            revisioni = list(overlay_event.tempo_assoluto_revisioni or [])
            if not revisioni:
                revisioni = [raw_tempo]
        else:
            tempo = raw_tempo
            revisioni = [raw_tempo]
        await _set_tempo_assoluto(session, evento_id, tempo, revisioni)


def _etichetta_ancora(ancora: AncoraTemporaleProposta) -> str:
    return (ancora.etichetta or "").strip()


def _id_arco_ancore(tipo: str, da_id: str, a_id: str, base: str) -> str:
    """Stable relationship id: ``tipo|da|a|base``, document-local via ``base``."""
    return content_hash(f"{tipo}|{da_id}|{a_id}|{base}")


def _valore_linea(mappa: dict[str, Any], etichetta: str) -> Any:
    if etichetta in mappa:
        return mappa[etichetta]
    chiave = etichetta_normalizzata(etichetta)
    if not chiave:
        return None
    for nome, valore in mappa.items():
        if etichetta_normalizzata(nome) == chiave:
            return valore
    return None


def _indice_ancore_persist(
    ancore: list[AncoraTemporaleProposta],
    doc_id: str,
) -> tuple[
    dict[str, AncoraTemporaleProposta],
    dict[str, str],
    dict[str, str],
]:
    """First etichetta wins; ids are ``identita_ancora`` / ``ancora_temporale_id``."""
    per_etichetta: dict[str, AncoraTemporaleProposta] = {}
    id_di: dict[str, str] = {}
    id_chiave: dict[str, str] = {}
    for ancora in ancore:
        etichetta = _etichetta_ancora(ancora)
        if not etichetta or etichetta in per_etichetta:
            continue
        nid = identita_ancora(ancora, doc_id)
        per_etichetta[etichetta] = ancora
        id_di[etichetta] = nid
        chiave = etichetta_normalizzata(etichetta)
        if chiave and chiave not in id_chiave:
            id_chiave[chiave] = nid
    return per_etichetta, id_di, id_chiave


def _id_per_etichetta(
    etichetta: str,
    id_di: dict[str, str],
    id_chiave: dict[str, str],
) -> str | None:
    nome = (etichetta or "").strip()
    if not nome:
        return None
    trovato = id_di.get(nome)
    if trovato:
        return trovato
    return id_chiave.get(etichetta_normalizzata(nome))


def _foglie_etichette(
    per_etichetta: dict[str, AncoraTemporaleProposta],
) -> set[str]:
    genitori = {
        etichetta_normalizzata(ancora.padre)
        for ancora in per_etichetta.values()
        if (ancora.padre or "").strip()
    }
    return {
        etichetta
        for etichetta in per_etichetta
        if etichetta_normalizzata(etichetta) not in genitori
    }


async def _merge_ancora_temporale(
    session: Any,
    nid: str,
    doc_id: str,
    ancora: AncoraTemporaleProposta,
    *,
    chiave_ordine: int | None,
    ordinale: int | None,
) -> str:
    query = (
        "MERGE (a:AncoraTemporale {id: $id}) "
        "SET a.documento = $documento, "
        "a.etichetta = $etichetta, "
        "a.descrizione = $descrizione, "
        "a.natura = $natura, "
        "a.tipo = $tipo, "
        "a.granularita = $granularita, "
        "a.inizio = $inizio, "
        "a.fine = $fine, "
        "a.chiave_ordine = $chiave_ordine, "
        "a.ordinale = $ordinale, "
        "a.espressione = $espressione, "
        "a.offset_inizio = $offset_inizio, "
        "a.offset_fine = $offset_fine, "
        "a.stimato = $stimato, "
        "a.confidenza = $confidenza, "
        "a.posizione_doc_min = $posizione_doc_min, "
        "a.regola = $regola, "
        "a.versione_regole = $versione_regole"
    )
    await _run(
        session,
        query,
        {
            "id": nid,
            "documento": doc_id,
            "etichetta": _etichetta_ancora(ancora),
            "descrizione": _prop_testo(ancora.descrizione),
            "natura": str(ancora.natura),
            "tipo": str(ancora.tipo),
            "granularita": _prop_testo(ancora.granularita),
            "inizio": _prop_testo(ancora.inizio),
            "fine": _prop_testo(ancora.fine),
            "chiave_ordine": chiave_ordine,
            "ordinale": ordinale,
            "espressione": _prop_testo(ancora.espressione),
            "offset_inizio": ancora.offset_inizio,
            "offset_fine": ancora.offset_fine,
            "stimato": bool(ancora.stimato),
            "confidenza": _prop_confidenza(ancora.confidenza),
            "posizione_doc_min": ancora.posizione_doc_min,
            "regola": REGOLA_LIVELLO_ANCORE,
            "versione_regole": RULESET_VERSION,
        },
    )
    return query


async def _merge_contiene_ancora(
    session: Any,
    padre_id: str,
    figlio_id: str,
    *,
    rel_id: str,
) -> str:
    query = (
        "MATCH (p:AncoraTemporale {id: $p_id}) "
        "MATCH (f:AncoraTemporale {id: $f_id}) "
        "MERGE (p)-[r:CONTIENE {id: $rel_id}]->(f) "
        "SET r.attivo = true, "
        "r.regola = $regola, "
        "r.versione_regole = $versione_regole"
    )
    await _run(
        session,
        query,
        {
            "p_id": padre_id,
            "f_id": figlio_id,
            "rel_id": rel_id,
            "regola": REGOLA_LIVELLO_ANCORE,
            "versione_regole": RULESET_VERSION,
        },
    )
    return query


async def _disattiva_contiene_ancora_obsoleti(
    session: Any,
    figlio_id: str,
    padre_id: str | None,
) -> str:
    query = (
        "MATCH (p:AncoraTemporale)-[r:CONTIENE]->(f:AncoraTemporale {id: $f_id}) "
        "WHERE $p_id IS NULL OR p.id <> $p_id "
        "SET r.attivo = false, "
        "r.sostituito_da = $p_id, "
        "r.versione_regole = $versione_regole"
    )
    await _run(
        session,
        query,
        {
            "f_id": figlio_id,
            "p_id": padre_id,
            "versione_regole": RULESET_VERSION,
        },
    )
    return query


async def _merge_appartiene_a_ancora(
    session: Any,
    evento_id: str,
    ancora_id: str,
    *,
    rel_id: str,
    confidenza: float,
    stimato: bool,
    base: str | None,
) -> str:
    query = (
        "MATCH (e:Evento {id: $e_id}) "
        "MATCH (a:AncoraTemporale {id: $a_id}) "
        "MERGE (e)-[r:APPARTIENE_A {id: $rel_id}]->(a) "
        "SET r.attivo = true, "
        "r.confidenza = $confidenza, "
        "r.stimato = $stimato, "
        "r.base = $base, "
        "r.regola = $regola, "
        "r.versione_regole = $versione_regole"
    )
    await _run(
        session,
        query,
        {
            "e_id": evento_id,
            "a_id": ancora_id,
            "rel_id": rel_id,
            "confidenza": confidenza,
            "stimato": stimato,
            "base": base,
            "regola": REGOLA_LIVELLO_ANCORE,
            "versione_regole": RULESET_VERSION,
        },
    )
    return query


async def _disattiva_appartiene_a_ancora_obsoleti(
    session: Any,
    evento_id: str,
    ancora_id: str,
) -> str:
    """Tombstone APPARTIENE_A toward another AncoraTemporale; never DELETE."""
    query = (
        "MATCH (e:Evento {id: $e_id})-[r:APPARTIENE_A]->(a:AncoraTemporale) "
        "WHERE a.id <> $a_id "
        "SET r.attivo = false, "
        "r.sostituito_da = $a_id, "
        "r.versione_regole = $versione_regole"
    )
    await _run(
        session,
        query,
        {
            "e_id": evento_id,
            "a_id": ancora_id,
            "versione_regole": RULESET_VERSION,
        },
    )
    return query


async def _merge_successione_ancora(
    session: Any,
    da_id: str,
    a_id: str,
    *,
    rel_id: str,
) -> str:
    query = (
        "MATCH (da:AncoraTemporale {id: $da_id}) "
        "MATCH (a:AncoraTemporale {id: $a_id}) "
        "MERGE (da)-[r:SUCCESSIONE_ANCORA {id: $rel_id}]->(a) "
        "SET r.attivo = true, "
        "r.regola = $regola, "
        "r.versione_regole = $versione_regole"
    )
    await _run(
        session,
        query,
        {
            "da_id": da_id,
            "a_id": a_id,
            "rel_id": rel_id,
            "regola": REGOLA_LIVELLO_ANCORE,
            "versione_regole": RULESET_VERSION,
        },
    )
    return query


async def _disattiva_successione_ancora_obsoleti(
    session: Any,
    da_id: str,
    a_id: str,
) -> str:
    query = (
        "MATCH (da:AncoraTemporale {id: $da_id})-[r:SUCCESSIONE_ANCORA]->"
        "(a:AncoraTemporale) "
        "WHERE a.id <> $a_id "
        "SET r.attivo = false, "
        "r.sostituito_da = $a_id, "
        "r.versione_regole = $versione_regole"
    )
    await _run(
        session,
        query,
        {
            "da_id": da_id,
            "a_id": a_id,
            "versione_regole": RULESET_VERSION,
        },
    )
    return query


def _coppie_successione_etichette(linea: Any) -> list[tuple[str, str]]:
    fuori: list[tuple[str, str]] = []
    visti: set[tuple[str, str]] = set()
    for coppia in getattr(linea, "successione", None) or []:
        if not isinstance(coppia, (list, tuple)) or len(coppia) < 2:
            continue
        sinistra = str(coppia[0] or "").strip()
        destra = str(coppia[1] or "").strip()
        if not sinistra or not destra or sinistra == destra:
            continue
        chiave = (sinistra, destra)
        if chiave in visti:
            continue
        visti.add(chiave)
        fuori.append(chiave)
    return fuori


async def persisti_livello_ancore(
    session: Any,
    smistamento: SmistamentoAncore | None,
    doc_id: str,
    job_id: str | None,
    eventi: list[EventoRisolto] | None = None,
) -> None:
    """MERGE AncoraTemporale and structural edges **with id in the pattern**.

    Three passes — nodes, then CONTIENE, then APPARTIENE_A / SUCCESSIONE_ANCORA
    — because MATCH-based rels vanish if the node is missing. Does not write
    PRECEDE / CONTEMPORANEO. Append-only: obsolete APPARTIENE_A / CONTIENE are
    deactivated (``attivo=false``, ``sostituito_da``), never DELETE'd.
    Empty/None is a no-op.
    """
    if smistamento is None:
        return

    linea = smistamento.linea
    ancore = [
        ancora
        for ancora in (linea.ancore or [])
        if isinstance(ancora, AncoraTemporaleProposta) and _etichetta_ancora(ancora)
    ]
    appartenenze = [
        item
        for item in (smistamento.appartenenze or [])
        if isinstance(item, AppartenenzaAncora)
    ]
    if not ancore and not appartenenze and not (linea.successione or []):
        return

    overlay = _evento_overlay(eventi)
    known = _known_evento_ids(overlay)
    per_etichetta, id_di, id_chiave = _indice_ancore_persist(ancore, doc_id)
    foglie = _foglie_etichette(per_etichetta)
    chiave_ordine_mappa = getattr(linea, "chiave_ordine", None) or {}
    ordinale_mappa = getattr(linea, "ordinale", None) or {}

    n_ancore = 0
    n_contiene = 0
    n_appartenenza = 0
    n_successione = 0

    scritti: dict[str, str] = {}
    for etichetta, ancora in per_etichetta.items():
        nid = id_di[etichetta]
        if nid in scritti:
            continue
        scritti[nid] = etichetta
        await _merge_ancora_temporale(
            session,
            nid,
            doc_id,
            ancora,
            chiave_ordine=_valore_linea(chiave_ordine_mappa, etichetta),
            ordinale=_valore_linea(ordinale_mappa, etichetta),
        )
        n_ancore += 1

    padre_id_di: dict[str, str | None] = {}
    for etichetta, ancora in per_etichetta.items():
        figlio_id = id_di[etichetta]
        padre_raw = (ancora.padre or "").strip()
        padre_id = (
            _id_per_etichetta(padre_raw, id_di, id_chiave) if padre_raw else None
        )
        if padre_id == figlio_id:
            padre_id = None
        padre_id_di[figlio_id] = padre_id

    for figlio_id, padre_id in padre_id_di.items():
        await _disattiva_contiene_ancora_obsoleti(session, figlio_id, padre_id)
        if padre_id is None:
            continue
        await _merge_contiene_ancora(
            session,
            padre_id,
            figlio_id,
            rel_id=_id_arco_ancore("CONTIENE", padre_id, figlio_id, doc_id),
        )
        n_contiene += 1

    emessi: set[str] = set()
    for item in appartenenze:
        evento_id = (item.evento_id or "").strip()
        foglia = (item.etichetta_foglia or "").strip()
        if not evento_id or evento_id in emessi:
            continue
        if not _evento_id_known(evento_id, known):
            continue
        if foglia not in foglie and etichetta_normalizzata(foglia) not in {
            etichetta_normalizzata(nome) for nome in foglie
        }:
            continue
        ancora_id = _id_per_etichetta(foglia, id_di, id_chiave)
        if not ancora_id:
            continue
        emessi.add(evento_id)
        await _disattiva_appartiene_a_ancora_obsoleti(session, evento_id, ancora_id)
        await _merge_appartiene_a_ancora(
            session,
            evento_id,
            ancora_id,
            rel_id=_id_arco_ancore("APPARTIENE_A", evento_id, ancora_id, doc_id),
            confidenza=_prop_confidenza(item.confidenza),
            stimato=bool(item.stimato),
            base=item.base,
        )
        n_appartenenza += 1

    visti_succ: set[tuple[str, str]] = set()
    for sinistra, destra in _coppie_successione_etichette(linea):
        da_id = _id_per_etichetta(sinistra, id_di, id_chiave)
        a_id = _id_per_etichetta(destra, id_di, id_chiave)
        if not da_id or not a_id or da_id == a_id:
            continue
        coppia = (da_id, a_id)
        if coppia in visti_succ:
            continue
        visti_succ.add(coppia)
        await _disattiva_successione_ancora_obsoleti(session, da_id, a_id)
        await _merge_successione_ancora(
            session,
            da_id,
            a_id,
            rel_id=_id_arco_ancore("SUCCESSIONE_ANCORA", da_id, a_id, doc_id),
        )
        n_successione += 1

    if job_id:
        await publish(
            job_id,
            _STAGE_ANCORE,
            _EVENTO_PERSIST_ANCORE,
            {
                "n_ancore": n_ancore,
                "n_contiene": n_contiene,
                "n_appartenenza": n_appartenenza,
                "n_successione": n_successione,
            },
        )


async def _merge_relazione_libera(
    session: Any,
    da_id: str,
    a_id: str,
    tipo: str,
    spiegazione: str,
) -> str:
    query = (
        f"MATCH (da:Evento {{id: $da_id}}) "
        f"MATCH (a:Evento {{id: $a_id}}) "
        f"MERGE (da)-[r:{tipo} {{livello: '3'}}]->(a) "
        f"SET r.livello = '3', "
        f"r.regola = $regola, "
        f"r.versione_regole = $versione_regole, "
        f"r.spiegazione = $spiegazione"
    )
    await _run(
        session,
        query,
        {
            "da_id": da_id,
            "a_id": a_id,
            "regola": REGOLA_LIVELLO_RELAZIONI,
            "versione_regole": RULESET_VERSION,
            "spiegazione": spiegazione,
        },
    )
    return query


def _quarantena_ciclo_livello3(
    da_id: str,
    a_id: str,
    job_id: str | None,
    overlay: dict[str, EventoRisolto],
) -> QuarantenaItem:
    motivo = MOTIVO_CICLO_CAUSA_LIVELLO3
    event = overlay.get(da_id) or overlay.get(a_id)
    doc_id = (event.documento if event is not None else None) or ""
    chunk_id = event.chunk_id if event is not None else None
    span = None
    if event is not None:
        span = event.span or event.ancora
    da_event = overlay.get(da_id)
    a_event = overlay.get(a_id)
    lemma_da = (da_event.lemma if da_event is not None else None) or da_id
    lemma_a = (a_event.lemma if a_event is not None else None) or a_id
    span_for_id = span or f"{da_id}->{a_id}"
    return QuarantenaItem(
        id=quarantena_id(doc_id, job_id or "", span_for_id, motivo),
        frammento=f"{lemma_da}->{lemma_a}",
        motivo=motivo,
        ancora_doc=doc_id or None,
        ancora_chunk=chunk_id,
        ancora_span=span or None,
        versione_regole=RULESET_VERSION,
    )


async def persisti_livello_relazioni(
    session: Any,
    livello_rel: LivelloRelazioniResult | None,
    job_id: str | None,
    eventi: list[EventoRisolto] | None = None,
) -> None:
    """MERGE Evento-Evento arcs with ``livello='3'``. Append-only; no Evento MERGE.

    CAUSA triples dropped for acyclicity (``relazioni_causa_ciclo``) become
    ``:Quarantena`` and are never written as relationships.
    """
    if livello_rel is None:
        return

    overlay = _evento_overlay(eventi)
    known = _known_evento_ids(overlay)
    dropped = {(str(da), str(a), str(tipo)) for da, a, tipo in relazioni_causa_ciclo()}

    for rel in livello_rel.relazioni or []:
        da_id = str(getattr(rel, "da_id", "") or "").strip()
        a_id = str(getattr(rel, "a_id", "") or "").strip()
        tipo = getattr(rel, "tipo", None)
        if tipo not in _TIPI_RELAZIONE_LIBERA:
            continue
        tipo_s = str(tipo)
        if not da_id or not a_id:
            continue
        if not _evento_id_known(da_id, known) or not _evento_id_known(a_id, known):
            continue
        if (da_id, a_id, tipo_s) in dropped:
            continue
        spiegazione = getattr(rel, "spiegazione", None)
        if spiegazione is None:
            spiegazione = ""
        await _merge_relazione_libera(session, da_id, a_id, tipo_s, str(spiegazione))

    for da_id, a_id, _tipo in relazioni_causa_ciclo():
        item = _quarantena_ciclo_livello3(str(da_id), str(a_id), job_id, overlay)
        await _merge_quarantena(session, item)


async def carica_zona(session: Any, zona_id: str) -> Zona | None:
    rows = await _query_rows(
        session,
        f"MATCH (z:Zona {{id: $id}}) RETURN {_ZONA_RETURN}",
        {"id": zona_id},
    )
    for row in rows:
        zona = _zona_from_row(row)
        if zona is not None:
            return zona
    return None


async def carica_zone(
    session: Any, documento: str | None = None
) -> list[Zona]:
    rows = await _query_rows(
        session,
        "MATCH (z:Zona) "
        "WHERE $documento IS NULL OR z.documento = $documento "
        f"RETURN {_ZONA_RETURN} "
        "ORDER BY z.ordinale",
        {"documento": documento},
    )
    zone: list[Zona] = []
    seen: set[str] = set()
    for row in rows:
        zona = _zona_from_row(row)
        if zona is None or zona.id in seen:
            continue
        seen.add(zona.id)
        zone.append(zona)
    return zone


async def carica_archi_macro(
    session: Any, documento: str | None = None
) -> list[ArcoZona]:
    rows = await _query_rows(
        session,
        "MATCH (da:Zona)-[r]->(a:Zona) "
        "WHERE r.livello = 'macro' "
        "AND ($documento IS NULL OR da.documento = $documento "
        "OR a.documento = $documento) "
        "RETURN da.id AS da_id, a.id AS a_id, type(r) AS tipo, "
        "r.confidenza AS confidenza, r.verificato AS verificato, "
        "r.segnale AS segnale, r.regola AS regola, "
        "r.versione_regole AS versione_regole, "
        "r.su_via_principale AS su_via_principale, r.livello AS livello",
        {"documento": documento},
    )
    archi: list[ArcoZona] = []
    for row in rows:
        arco = _arco_macro_from_row(row)
        if arco is not None:
            archi.append(arco)
    return archi


async def carica_documento_testo(session: Any, doc_id: str) -> str | None:
    rows = await _query_rows(
        session,
        "MATCH (d:Documento {id: $id}) RETURN d.testo AS testo",
        {"id": doc_id},
    )
    if not rows:
        return None
    data = _row_mapping(rows[0])
    return _optional_str(data.get("testo") or data.get("d.testo"))


__all__ = [
    "ARG_RUOLI",
    "CROSS_DOC_ALLOWED",
    "EVENT_EVENT_TIPI",
    "PersistOutcome",
    "REGOLA",
    "archi_ammissibili",
    "carica_archi_macro",
    "carica_documento_testo",
    "carica_zona",
    "carica_zone",
    "persisti",
    "persisti_archi_macro",
    "persisti_arco_macro",
    "persisti_documento",
    "persisti_livello_ancore",
    "persisti_livello_relazioni",
    "persisti_livello_temporale",
    "persisti_transizioni_zona",
    "persisti_zona",
    "persisti_zone",
    "sopprimi_collegato_ridondanti",
    "_merge_successione_zona",
]
