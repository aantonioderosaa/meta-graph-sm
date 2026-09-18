"""Isolated event-graph HTTP surface — BASE + metrics + structured/NL query."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError

from app.models.event_graph import EventQuerySpec, SottoGrafo
from app.pipeline.event_graph.catalog import (
    catalogo,
    dettaglio_arco,
    dettaglio_nodo,
    grafo,
    grafo_livello1,
    grafo_livello2,
    grafo_livello3,
    stats,
)
from app.pipeline.event_graph.infra.bus import (
    elenca_job,
    merge_job_lists,
    register_job,
    reset_event_bus,
    run_tracked_job,
    subscribe,
    unsubscribe,
)
from app.pipeline.event_graph.infra.driver import get_driver
from app.pipeline.event_graph.infra.llm import LLMValidationError
from app.pipeline.event_graph.metrics import elenca_run
from app.pipeline.event_graph.persistence import (
    carica_archi_macro,
    carica_documento_testo,
    carica_zona,
    carica_zone,
    elenca_documenti,
    wipe_grafo,
)
from app.pipeline.event_graph.pipeline import espandi_zona, run_event_graph_ingestion
from app.pipeline.event_graph.query_nl import esegui_nl
from app.pipeline.event_graph.query_structured import (
    QueryResult,
    StoredQuery,
    elenca_query,
    esegui,
    get_query,
    registra_query,
    reset_query_history,
)
from app.pipeline.event_graph.zona_edges import ArcoZona
from app.pipeline.event_graph.zona_segmentation import Zona

router = APIRouter(prefix="/event-graph", tags=["event-graph"])


class EventGraphDocumentRequest(BaseModel):
    doc_id: str
    text: str


class EventGraphNlQueryRequest(BaseModel):
    testo: str


class EventGraphJobResponse(BaseModel):
    job_id: str


async def sse_event_generator(job_id: str) -> AsyncIterator[str]:
    """Yield bus messages as SSE lines; unsubscribe when the client drops."""
    queue = await subscribe(job_id)
    try:
        while True:
            event = await queue.get()
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event.get("stage") in {"done", "failed"}:
                break
    finally:
        await unsubscribe(job_id, queue)


@router.post("/documents", response_model=EventGraphJobResponse)
async def ingest_event_graph_document(
    body: EventGraphDocumentRequest,
) -> EventGraphJobResponse:
    job_id = str(uuid.uuid4())
    register_job(job_id)
    asyncio.create_task(
        run_tracked_job(
            job_id,
            run_event_graph_ingestion(body.doc_id, body.text, job_id),
        )
    )
    return EventGraphJobResponse(job_id=job_id)


@router.get("/documents")
async def list_event_graph_documents() -> dict:
    """Ingested documents: id, format, byte size, preview."""
    try:
        driver = get_driver()
        async with driver.session() as session:
            documents = await elenca_documenti(session)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"documents": documents}


@router.delete("/graph")
async def wipe_event_graph() -> dict:
    """Wipe the event-graph knowledge base (nodes, rels, in-memory query history)."""
    try:
        driver = get_driver()
        async with driver.session() as session:
            await wipe_grafo(session)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    reset_query_history()
    reset_event_bus()
    return {"deleted": True}


def _zona_payload(zona: Zona) -> dict:
    return {
        "id": zona.id,
        "documento": zona.documento,
        "ordinale": zona.ordinale,
        "espansa": bool(zona.espansa),
        "offset_inizio": zona.offset_inizio,
        "offset_fine": zona.offset_fine,
        "su_via_principale": list(zona.su_via_principale),
        "riassunto": zona.riassunto,
        "testo": zona.testo,
        "entita_principali": list(zona.entita_principali),
        "ancore_temporali": list(zona.ancore_temporali),
        "evento_centrale": zona.evento_centrale,
        "regola": zona.regola,
        "versione_regole": zona.versione_regole,
        "avviso": zona.avviso,
    }


def _arco_macro_payload(arco: ArcoZona) -> dict:
    return {
        "da_id": arco.da_id,
        "a_id": arco.a_id,
        "tipo": str(arco.tipo),
        "livello": arco.livello,
        "confidenza": arco.confidenza,
        "verificato": arco.verificato,
        "segnale": arco.segnale,
        "su_via_principale": list(arco.su_via_principale),
    }


def _empty_documento(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


@router.get("/zone")
async def event_graph_zone_list(
    documento: str | None = Query(default=None),
) -> dict:
    doc = _empty_documento(documento)
    try:
        driver = get_driver()
        async with driver.session() as session:
            zone = await carica_zone(session, documento=doc)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"zone": [_zona_payload(item) for item in zone]}


@router.post("/zone/{id}/espandi")
async def event_graph_zone_espandi(id: str) -> dict:
    try:
        driver = get_driver()
        async with driver.session() as session:
            zona = await carica_zona(session, id)
            if zona is None:
                raise HTTPException(status_code=404, detail="zona not found")
            siblings = await carica_zone(session, documento=zona.documento)
            if not any(item.id == zona.id for item in siblings):
                siblings.append(zona)
            else:
                siblings = [
                    zona if item.id == zona.id else item for item in siblings
                ]
            archi_macro = await carica_archi_macro(session, documento=zona.documento)
            document_text = zona.testo or await carica_documento_testo(
                session, zona.documento
            )
            sotto = SottoGrafo()
            result = await espandi_zona(
                zona,
                document_text=document_text or zona.testo,
                zone=siblings,
                archi_macro=archi_macro,
                sotto_doc=sotto,
                session=session,
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "id": result.zona.id,
        "espansa": True,
        "eventi": len(result.sotto.eventi),
    }


@router.get("/zone/{id}")
async def event_graph_zone_detail(id: str) -> dict:
    try:
        driver = get_driver()
        async with driver.session() as session:
            zona = await carica_zona(session, id)
            if zona is None:
                raise HTTPException(status_code=404, detail="zona not found")
            archi = await carica_archi_macro(session, documento=zona.documento)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    incident = [
        _arco_macro_payload(arco)
        for arco in archi
        if id in (arco.da_id, arco.a_id)
    ]
    return {**_zona_payload(zona), "archi": incident}


@router.get("/stream")
async def stream_event_graph_job(
    job_id: str = Query(..., description="Event-graph pipeline job identifier"),
) -> StreamingResponse:
    return StreamingResponse(
        sse_event_generator(job_id),
        media_type="text/event-stream",
    )


@router.get("/jobs")
async def list_event_graph_jobs() -> dict:
    """Live ingest jobs (in-memory SSE history) plus completed :EventGraphRun."""
    jobs = elenca_job()
    runs: list = []
    try:
        driver = get_driver()
        async with driver.session() as session:
            runs = await elenca_run(session)
    except HTTPException:
        raise
    except Exception:
        runs = []
    return {"jobs": merge_job_lists(jobs, runs)}


@router.get("/health")
async def event_graph_health() -> dict[str, str]:
    try:
        driver = get_driver()
        async with driver.session() as session:
            result = await session.run("RETURN 1")
            record = result.single()
            if asyncio.iscoroutine(record) or asyncio.isfuture(record):
                record = await record
            if record is None:
                raise RuntimeError("RETURN 1 produced no record")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "ok"}


@router.get("/metrics")
async def event_graph_metrics() -> dict:
    try:
        driver = get_driver()
        async with driver.session() as session:
            runs = await elenca_run(session)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"runs": runs}


@router.get("/catalog")
async def event_graph_catalog() -> dict:
    return catalogo()


@router.get("/stats")
async def event_graph_stats() -> dict:
    try:
        driver = get_driver()
        async with driver.session() as session:
            return await stats(session)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/graph")
async def event_graph_graph(
    documento: str | None = Query(default=None),
    piano: str | None = Query(default=None),
    lemma: str | None = Query(default=None),
    vista: Literal["tutto", "ordine", "temporale", "relazioni"] = Query(
        default="tutto"
    ),
) -> dict:
    try:
        driver = get_driver()
        async with driver.session() as session:
            if vista == "ordine":
                return await grafo_livello1(session, documento=documento)
            if vista == "temporale":
                return await grafo_livello2(session, documento=documento)
            if vista == "relazioni":
                return await grafo_livello3(session, documento=documento)
            return await grafo(
                session, documento=documento, piano=piano, lemma=lemma
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/nodo/{node_id}")
async def event_graph_nodo(node_id: str) -> dict:
    """Every property of one node (:Evento/:Menzione/:Quarantena/:Zona/...).

    Feeds the frontend selection dashboard.
    """
    try:
        driver = get_driver()
        async with driver.session() as session:
            dettaglio = await dettaglio_nodo(session, node_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if dettaglio is None:
        raise HTTPException(status_code=404, detail=f"nodo {node_id} non trovato")
    return dettaglio


@router.get("/arco/{arco_id}")
async def event_graph_arco(arco_id: str) -> dict:
    """Every property of one relationship, for the selection dashboard."""
    try:
        driver = get_driver()
        async with driver.session() as session:
            dettaglio = await dettaglio_arco(session, arco_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if dettaglio is None:
        raise HTTPException(status_code=404, detail=f"arco {arco_id} non trovato")
    return dettaglio


def _spec_dump(spec: EventQuerySpec) -> dict:
    dump = getattr(spec, "model_dump", None)
    if callable(dump):
        return dump()
    return dict(spec) if isinstance(spec, dict) else {}


def _risultato_dump(risultato: QueryResult | None) -> dict:
    if risultato is None:
        return {"eventi": [], "archi": []}
    return {
        "eventi": list(getattr(risultato, "eventi", None) or []),
        "archi": list(getattr(risultato, "archi", None) or []),
    }


def _stored_summary(record: StoredQuery) -> dict:
    return {
        "id": record.id,
        "modo": record.modo,
        "ts": record.ts,
        "n_eventi": record.n_eventi,
    }


def _stored_full(record: StoredQuery) -> dict:
    return {
        **_stored_summary(record),
        "spec": _spec_dump(record.spec),
        "risultato": _risultato_dump(record.risultato),
    }


@router.post("/query/structured")
async def event_graph_query_structured(body: EventQuerySpec) -> dict:
    try:
        driver = get_driver()
        async with driver.session() as session:
            risultato = await esegui(session, body)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    stored = registra_query(body, risultato, modo="structured")
    return {
        "id": stored.id,
        "modo": "structured",
        "spec": _spec_dump(body),
        "risultato": _risultato_dump(risultato),
    }


@router.post("/query/nl")
async def event_graph_query_nl(body: EventGraphNlQueryRequest) -> dict:
    try:
        driver = get_driver()
        async with driver.session() as session:
            outcome = await esegui_nl(session, body.testo)
    except HTTPException:
        raise
    except (ValueError, ValidationError, LLMValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "id": outcome.stored_id,
        "modo": "nl",
        "spec_generata": _spec_dump(outcome.spec),
        "risultato": _risultato_dump(outcome.risultato),
        "risposta": outcome.risposta,
        "eventi_citati": outcome.eventi_citati,
    }


@router.get("/queries")
async def event_graph_queries() -> dict:
    return {"queries": [_stored_summary(record) for record in elenca_query()]}


@router.get("/queries/{query_id}")
async def event_graph_query_detail(query_id: str) -> dict:
    stored = get_query(query_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="query not found")
    return _stored_full(stored)
