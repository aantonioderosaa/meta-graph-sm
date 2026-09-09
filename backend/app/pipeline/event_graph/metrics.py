"""§16 — control metrics + :EventGraphRun (piano sez. 19)."""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone
from typing import Any

from app.models.event_graph import ArcoEvento, EventoRisolto, SottoGrafo
from app.pipeline.event_graph.chains import CHAIN_TIPI, biforcazioni
from app.pipeline.event_graph.wellformed import EVENT_EVENT_TIPI

_SOTTOCONTI = (
    "ciclo_cronologico",
    "ciclo_CAUSA",
    "estrazione_fallita",
    "malformazione",
)
_ESITI = ("Fusione", "Successione", "Catena")

_ELENCA_CYPHER = "MATCH (r:EventGraphRun) RETURN r ORDER BY r.timestamp DESC"

_REGISTRA_CYPHER = (
    "MERGE (r:EventGraphRun {id: $id}) "
    "SET r.documento=$doc, r.versione_regole=$ver, r.timestamp=$ts, "
    "r.tasso_quarantena=$tasso_quarantena, "
    "r.quarantena_sottoconti=$quarantena_sottoconti, "
    "r.quota_collegato=$quota_collegato, "
    "r.densita_contraddice=$densita_contraddice, "
    "r.frazione_chunk_tempo_base_ereditato=$frazione_chunk_tempo_base_ereditato, "
    "r.catene_biforcate=$catene_biforcate, "
    "r.distribuzione_esiti_coref=$distribuzione_esiti_coref, "
    "r.quota_precede_dato_esplicito=$quota_precede_dato_esplicito, "
    "r.archi_temporali_aggiunti=$archi_temporali_aggiunti, "
    "r.archi_temporali_superati=$archi_temporali_superati"
)


def calcola(sotto: SottoGrafo, *, temporal: Any = None, extra: Any = None) -> dict:
    """Compute §16 metrics from an in-memory subgraph. No Neo4j."""
    eventi = list(sotto.eventi) if sotto is not None else []
    quarantena = list(sotto.quarantena) if sotto is not None else []
    archi = list(sotto.archi) if sotto is not None else []

    n_eventi = len(eventi)
    n_quar = len(quarantena)
    tasso = n_quar / max(1, n_eventi + n_quar)

    sottoconti = {key: 0 for key in _SOTTOCONTI}
    for item in quarantena:
        bucket = _motivo_bucket(getattr(item, "motivo", "") or "")
        sottoconti[bucket] += 1

    ee_archi = [arco for arco in archi if str(arco.tipo) in EVENT_EVENT_TIPI]
    n_ee = len(ee_archi)
    n_collegato = sum(1 for arco in ee_archi if str(arco.tipo) == "COLLEGATO")
    n_contraddice = sum(
        1 for event in eventi if getattr(event, "catena_ruolo", None) == "CONTRADDICE"
    )
    quota_collegato = n_collegato / max(1, n_ee) if n_ee else 0.0
    densita_contraddice = n_contraddice / max(1, n_eventi) if n_eventi else 0.0

    frazione = 0.0
    if extra:
        inherited = extra.get("chunk_tempo_base_ereditato", 0)
        kept = extra.get("chunks_kept", 1)
        frazione = inherited / max(1, kept)

    n_biforcate = len(biforcazioni(sotto if sotto is not None else SottoGrafo()))
    esiti = _distribuzione_esiti(sotto if sotto is not None else SottoGrafo(), extra)
    quota_precede = _quota_precede_dato_esplicito(archi)
    aggiunti, superati = _temporal_counts(temporal)

    return {
        "tasso_quarantena": float(tasso),
        "quarantena_sottoconti": sottoconti,
        "quota_collegato": float(quota_collegato),
        "densita_contraddice": float(densita_contraddice),
        "frazione_chunk_tempo_base_ereditato": float(frazione),
        "catene_biforcate": int(n_biforcate),
        "distribuzione_esiti_coref": esiti,
        "quota_precede_dato_esplicito": float(quota_precede),
        "archi_temporali_aggiunti": int(aggiunti),
        "archi_temporali_superati": int(superati),
    }


async def registra_run(
    session: Any,
    job_id: str,
    doc_id: str,
    versione_regole: str,
    *,
    sotto: SottoGrafo | None = None,
    temporal: Any = None,
    extra: Any = None,
) -> dict:
    """MERGE :EventGraphRun for this job. Zeros when ``sotto`` is omitted."""
    graph = sotto if sotto is not None else SottoGrafo()
    metrics = calcola(graph, temporal=temporal, extra=extra)
    ts = datetime.now(timezone.utc).isoformat()
    params = {
        "id": job_id,
        "doc": doc_id,
        "ver": versione_regole,
        "ts": ts,
        "tasso_quarantena": metrics["tasso_quarantena"],
        "quarantena_sottoconti": json.dumps(
            metrics["quarantena_sottoconti"], ensure_ascii=False
        ),
        "quota_collegato": metrics["quota_collegato"],
        "densita_contraddice": metrics["densita_contraddice"],
        "frazione_chunk_tempo_base_ereditato": metrics[
            "frazione_chunk_tempo_base_ereditato"
        ],
        "catene_biforcate": metrics["catene_biforcate"],
        "distribuzione_esiti_coref": json.dumps(
            metrics["distribuzione_esiti_coref"], ensure_ascii=False
        ),
        "quota_precede_dato_esplicito": metrics["quota_precede_dato_esplicito"],
        "archi_temporali_aggiunti": metrics["archi_temporali_aggiunti"],
        "archi_temporali_superati": metrics["archi_temporali_superati"],
    }
    await _run(session, _REGISTRA_CYPHER, params)
    return {
        **metrics,
        "id": job_id,
        "documento": doc_id,
        "versione_regole": versione_regole,
        "timestamp": ts,
    }


async def elenca_run(session: Any) -> list[dict]:
    """Read-only list of :EventGraphRun, newest first."""
    rows = await _run(session, _ELENCA_CYPHER)
    if not rows:
        return []
    if not isinstance(rows, list):
        rows = [rows]
    return [_record_to_run(row) for row in rows]


def _motivo_bucket(motivo: str) -> str:
    text = motivo or ""
    folded = text.casefold()
    if "ciclo causa" in folded or text.startswith("ciclo_CAUSA"):
        return "ciclo_CAUSA"
    if folded.startswith("ciclo cronologico") or text.startswith("ciclo_cronologico"):
        return "ciclo_cronologico"
    if folded.startswith("incoerenza allen"):
        return "ciclo_cronologico"
    if folded.startswith("estrazione fallita") or text.startswith("estrazione_fallita"):
        return "estrazione_fallita"
    return "malformazione"


def _distribuzione_esiti(sotto: SottoGrafo, extra: Any) -> dict[str, int]:
    if extra and extra.get("esiti_coref") is not None:
        raw = extra["esiti_coref"]
        out = {key: 0 for key in _ESITI}
        if isinstance(raw, dict):
            for key in _ESITI:
                if key in raw:
                    out[key] = int(raw[key] or 0)
        return out
    return _infer_esiti_coref(sotto)


def _infer_esiti_coref(sotto: SottoGrafo) -> dict[str, int]:
    fusione = sum(1 for event in sotto.eventi if event.fuso_in)
    by_id = {event.id: event for event in sotto.eventi if event.id}
    successione = 0
    catena = sum(
        1
        for event in sotto.eventi
        if getattr(event, "catena_ruolo", None) in CHAIN_TIPI
    )
    for arco in sotto.archi:
        tipo = str(arco.tipo)
        if tipo != "SEQUENZA":
            continue
        da = by_id.get(arco.da_id)
        a = by_id.get(arco.a_id)
        if _same_lemma_primo_piano(da, a):
            successione += 1
    return {"Fusione": fusione, "Successione": successione, "Catena": catena}


def _same_lemma_primo_piano(
    da: EventoRisolto | None, a: EventoRisolto | None
) -> bool:
    if da is None or a is None:
        return False
    if da.piano != "PRIMO_PIANO" or a.piano != "PRIMO_PIANO":
        return False
    left = (da.lemma or "").strip().casefold()
    right = (a.lemma or "").strip().casefold()
    return bool(left) and left == right


def _quota_precede_dato_esplicito(archi: list[ArcoEvento]) -> float:
    n_precede = 0
    n_ordine = 0
    for arco in archi:
        tipo = str(arco.tipo)
        props = arco.props or {}
        if tipo == "PRECEDE" and props.get("base") == "dato_esplicito":
            n_precede += 1
        if tipo == "COLLEGATO":
            segnale = str(props.get("segnale") or "")
            if segnale.startswith("ordine_"):
                n_ordine += 1
    return n_precede / max(1, n_precede + n_ordine)


def _temporal_counts(temporal: Any) -> tuple[int, int]:
    if temporal is None:
        return 0, 0
    if isinstance(temporal, dict):
        added = temporal.get("archi_temporali_aggiunti", temporal.get("archi_aggiunti", 0))
        superseded = temporal.get(
            "archi_temporali_superati", temporal.get("archi_superati", 0)
        )
        return _as_count(added), _as_count(superseded)
    added = getattr(temporal, "archi_aggiunti", None)
    if added is None:
        added = getattr(temporal, "archi_temporali_aggiunti", 0)
    superseded = getattr(temporal, "archi_superati", None)
    if superseded is None:
        superseded = getattr(temporal, "archi_temporali_superati", 0)
    return _as_count(added), _as_count(superseded)


def _as_count(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    try:
        return len(value)
    except TypeError:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0


def _record_to_run(row: Any) -> dict:
    if row is None:
        return {}
    if isinstance(row, dict):
        if "r" in row:
            return _node_props(row["r"])
        return dict(row)
    getter = getattr(row, "get", None)
    if callable(getter):
        node = getter("r")
        if node is not None:
            return _node_props(node)
    data_fn = getattr(row, "data", None)
    if callable(data_fn):
        data = data_fn()
        if isinstance(data, dict):
            return _record_to_run(data)
    return _node_props(row)


def _node_props(value: Any) -> dict:
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


async def _run(
    session: Any,
    query: str,
    parameters: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Any:
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


__all__ = ["calcola", "elenca_run", "registra_run"]
