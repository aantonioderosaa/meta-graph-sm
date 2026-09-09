"""§14 — idempotent MERGE persistence (piano sez. 12).

Writes the in-memory document subgraph. Append-only: MERGE + SET, no removals.
Re-ingest of the same ``SottoGrafo`` re-issues the same MERGE keys (no-op).
"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.models.event_graph import (
    ArcoEvento,
    EventoRisolto,
    MenzioneRisolta,
    QuarantenaItem,
    SottoGrafo,
)
from app.pipeline.event_graph import RULESET_VERSION
from app.pipeline.event_graph.ids import content_hash, quarantena_id
from app.pipeline.event_graph.zona_edges import ArcoZona
from app.pipeline.event_graph.zona_segmentation import Zona

REGOLA = "persistence.persisti"

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

    skipped = 0
    for arco in sotto.archi:
        tipo = str(arco.tipo)
        if tipo not in EVENT_EVENT_TIPI:
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
    "persisti_zona",
    "persisti_zone",
]
