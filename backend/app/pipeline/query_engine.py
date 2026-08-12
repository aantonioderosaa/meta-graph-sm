"""Query engine: fact detail, history, NL query, graph export (tech-spec §8, E5)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from neo4j import AsyncSession
from pydantic import BaseModel, Field

from app.api.schemas import (
    ChunkProvenance,
    FactDetailResponse,
    FactHistoryEntry,
    FactHistoryResponse,
    GraphNode,
    GraphRelationship,
    GraphResponse,
)
from app.core.llm_client import call_structured
from app.models.extraction import FactType
from app.models.query import FactUsed, QueryResponse, Subgraph, SubgraphNode, SubgraphRelationship
from app.pipeline import embeddings

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 5

FACT_DETAIL_CYPHER = """
MATCH (f:Fact {id: $id})
OPTIONAL MATCH (f)-[:DERIVED_FROM]->(c:Chunk)
RETURN f,
       collect(c) AS chunks
"""

FACT_HISTORY_CYPHER = """
MATCH path = (current:Fact {id: $id})-[:UPDATES*0..]->(historical:Fact)
RETURN path
ORDER BY length(path)
"""

VECTOR_SEARCH_CYPHER = """
CALL db.index.vector.queryNodes('fact_embedding', $k, $embedding)
YIELD node, score
WHERE node.is_latest = true
  AND ($type_filter IS NULL OR node.type = $type_filter)
RETURN node.id AS id, score
ORDER BY score DESC
"""

EXPAND_CYPHER = """
MATCH (f:Fact) WHERE f.id IN $topKIds
OPTIONAL MATCH (f)-[:EXTENDS]-(e:Fact {is_latest: true})
OPTIONAL MATCH (f)-[:DERIVES]-(d:Fact)
RETURN f,
       collect(DISTINCT e) AS extended,
       collect(DISTINCT d) AS derived
"""

PROVENANCE_CYPHER = """
MATCH (f:Fact) WHERE f.id IN $ids
OPTIONAL MATCH (f)-[:DERIVED_FROM]->(c:Chunk)
RETURN f.id AS id,
       f.text AS text,
       f.type AS type,
       f.confidence AS confidence,
       f.is_latest AS is_latest,
       f.source_doc_id AS source_doc_id,
       collect(DISTINCT {
         chunk_id: c.id,
         snippet: c.text,
         doc_id: c.doc_id
       }) AS chunks
"""

SUBGRAPH_RELS_CYPHER = """
MATCH (a:Fact)-[r]->(b:Fact)
WHERE a.id IN $ids AND b.id IN $ids
  AND type(r) IN ['UPDATES', 'EXTENDS', 'DERIVES']
RETURN a.id AS source, b.id AS target, type(r) AS rel_type
"""

GRAPH_NODES_CYPHER = """
MATCH (f:Fact)
WHERE ($is_latest IS NULL OR f.is_latest = $is_latest)
  AND ($type_filter IS NULL OR f.type = $type_filter)
  AND ($doc_id IS NULL OR f.source_doc_id = $doc_id)
RETURN f
LIMIT $limit
"""

GRAPH_RELS_CYPHER = """
MATCH (a:Fact)-[r]->(b:Fact)
WHERE a.id IN $ids AND b.id IN $ids
  AND type(r) IN ['UPDATES', 'EXTENDS', 'DERIVES']
RETURN elementId(r) AS id, a.id AS from_id, b.id AS to_id, type(r) AS rel_type
"""

GRAPH_HAS_HISTORY_CYPHER = """
MATCH (f:Fact)-[:UPDATES]->()
WHERE f.id IN $ids
RETURN DISTINCT f.id AS id
"""

ANSWER_SYSTEM_PROMPT = (
    "Sei un assistente che risponde a domande basandoti SOLO sui fatti forniti. "
    "Non scrivere mai ID o UUID dentro il testo di `answer` — scrivi prosa naturale "
    "e leggibile, come se parlassi a una persona. Elenca invece in `cited_fact_ids` "
    "gli ID (tra quelli forniti sotto) dei fatti che hai usato per costruire la risposta. "
    "Se i fatti non bastano a rispondere, dillo esplicitamente senza inventare informazioni."
)


class QueryAnswer(BaseModel):
    """Structured LLM answer for POST /query (natural-language portion only)."""

    answer: str = Field(
        description="Risposta in linguaggio naturale, senza ID o UUID nel testo."
    )
    cited_fact_ids: list[str] = Field(
        default_factory=list,
        description="ID (tra quelli forniti nel prompt) dei fatti usati per costruire answer.",
    )


def _datetime_to_str(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


async def get_fact_detail(session: AsyncSession, fact_id: str) -> FactDetailResponse | None:
    """Return fact detail with chunk provenance, or None if missing."""
    result = await session.run(FACT_DETAIL_CYPHER, id=fact_id)
    record = await result.single()
    if record is None:
        return None

    fact = record["f"]
    chunks = [c for c in record["chunks"] if c is not None]
    provenance: list[ChunkProvenance] = []
    for chunk in chunks:
        snippet = chunk.get("text") or ""
        if len(snippet) > 200:
            snippet = snippet[:197] + "..."
        provenance.append(
            ChunkProvenance(
                chunk_id=chunk["id"],
                snippet=snippet,
                doc_id=chunk.get("doc_id") or fact.get("source_doc_id") or "",
            )
        )

    return FactDetailResponse(
        id=fact["id"],
        text=fact["text"],
        type=FactType(fact["type"]),
        confidence=float(fact.get("confidence") if fact.get("confidence") is not None else 1.0),
        is_latest=bool(fact["is_latest"]),
        created_at=_datetime_to_str(fact.get("created_at")),
        source_doc_id=fact.get("source_doc_id") or "",
        provenance=provenance,
    )


async def get_fact_history(session: AsyncSession, fact_id: str) -> FactHistoryResponse | None:
    """Return UPDATES chain from newest (path_length 0) to oldest."""
    exists = await session.run("MATCH (f:Fact {id: $id}) RETURN f.id AS id", id=fact_id)
    if await exists.single() is None:
        return None

    result = await session.run(FACT_HISTORY_CYPHER, id=fact_id)
    entries: list[FactHistoryEntry] = []
    seen: set[str] = set()
    async for record in result:
        path = record["path"]
        nodes = list(path.nodes)
        # Path is current -[:UPDATES*]-> historical; last node is the historical end.
        historical = nodes[-1]
        hid = historical["id"]
        if hid in seen:
            continue
        seen.add(hid)
        entries.append(
            FactHistoryEntry(
                id=hid,
                text=historical["text"],
                type=FactType(historical["type"]),
                is_latest=bool(historical["is_latest"]),
                path_length=len(nodes) - 1,
            )
        )

    entries.sort(key=lambda e: e.path_length)
    return FactHistoryResponse(facts=entries)


async def get_graph(
    session: AsyncSession,
    *,
    is_latest: bool | None = True,
    fact_type: FactType | None = None,
    doc_id: str | None = None,
    limit: int = 200,
) -> GraphResponse:
    """Return NVL-compatible Fact graph (no Chunk nodes).

    ``is_latest=True`` (default): only current facts.
    ``is_latest=False`` or ``None``: filter removed — include historical facts too.
    """
    filter_latest = True if is_latest is True else None
    type_filter = fact_type.value if fact_type is not None else None

    result = await session.run(
        GRAPH_NODES_CYPHER,
        is_latest=filter_latest,
        type_filter=type_filter,
        doc_id=doc_id,
        limit=limit,
    )
    nodes: list[GraphNode] = []
    ids: list[str] = []
    async for record in result:
        fact = record["f"]
        fid = fact["id"]
        ids.append(fid)
        props = {
            "type": fact.get("type"),
            "is_latest": bool(fact.get("is_latest")),
            "confidence": float(
                fact.get("confidence") if fact.get("confidence") is not None else 1.0
            ),
            "text": fact.get("text"),
            "source_doc_id": fact.get("source_doc_id"),
            "has_history": False,
        }
        nodes.append(
            GraphNode(
                id=fid,
                caption=fact.get("text") or fid,
                size=1.0,
                properties=props,
            )
        )

    relationships: list[GraphRelationship] = []
    if ids:
        history_ids: set[str] = set()
        hist_result = await session.run(GRAPH_HAS_HISTORY_CYPHER, ids=ids)
        async for record in hist_result:
            history_ids.add(record["id"])
        for node in nodes:
            if node.id in history_ids:
                node.properties["has_history"] = True

        rel_result = await session.run(GRAPH_RELS_CYPHER, ids=ids)
        async for record in rel_result:
            rel_type = record["rel_type"]
            relationships.append(
                GraphRelationship(
                    id=str(record["id"]),
                    **{"from": record["from_id"], "to": record["to_id"]},
                    type=rel_type,
                    caption=rel_type.lower(),
                )
            )

    return GraphResponse(nodes=nodes, relationships=relationships)


def build_query_answer_prompt(question: str, facts: list[FactUsed]) -> tuple[str, str]:
    """Build (system, user) prompts for the NL answer LLM call."""
    if not facts:
        lines = "(nessun fatto rilevante trovato)"
    else:
        lines = "\n".join(
            f"- [{f.id}] (doc={f.source_doc_id}) {f.text}" for f in facts
        )
    user = (
        f"DOMANDA: {question}\n\n"
        f"FATTI:\n{lines}\n\n"
        "Rispondi basandoti solo sui fatti sopra."
    )
    return ANSWER_SYSTEM_PROMPT, user


async def _load_fact_bundle(
    session: AsyncSession, fact_ids: list[str]
) -> tuple[list[FactUsed], list[SubgraphNode], dict[str, dict[str, Any]]]:
    if not fact_ids:
        return [], [], {}

    result = await session.run(PROVENANCE_CYPHER, ids=fact_ids)
    facts_used: list[FactUsed] = []
    nodes: list[SubgraphNode] = []
    by_id: dict[str, dict[str, Any]] = {}
    async for record in result:
        fid = record["id"]
        source_doc_id = record["source_doc_id"] or ""
        if not source_doc_id:
            for chunk in record["chunks"] or []:
                if chunk and chunk.get("doc_id"):
                    source_doc_id = chunk["doc_id"]
                    break
        props = {
            "text": record["text"],
            "type": record["type"],
            "confidence": record["confidence"],
            "is_latest": record["is_latest"],
            "source_doc_id": source_doc_id,
        }
        by_id[fid] = props
        facts_used.append(
            FactUsed(id=fid, text=record["text"], source_doc_id=source_doc_id)
        )
        nodes.append(SubgraphNode(id=fid, label="Fact", properties=props))
    return facts_used, nodes, by_id


async def run_query(
    session: AsyncSession,
    text: str,
    *,
    type_filter: FactType | None = None,
    k: int = DEFAULT_TOP_K,
    job_id: str | None = None,
) -> QueryResponse:
    """Execute current-state NL query (tech-spec §8.1).

    Direct vector hits are always ``is_latest=true``. Expanded EXTENDS neighbors are
    also current-only. DERIVES neighbors may be included in the subgraph for context
    but ``facts_used`` never contains ``is_latest=false`` facts.
    """
    embedding = await asyncio.to_thread(embeddings.embed, text)
    type_value = type_filter.value if type_filter is not None else None

    search = await session.run(
        VECTOR_SEARCH_CYPHER,
        k=k,
        embedding=embedding,
        type_filter=type_value,
    )
    top_ids: list[str] = []
    async for record in search:
        top_ids.append(record["id"])

    if not top_ids:
        response = QueryResponse(
            answer="Nessun fatto rilevante trovato per questa domanda.",
            facts_used=[],
            cited_fact_ids=[],
            subgraph=Subgraph(nodes=[], relationships=[]),
        )
        await _persist_query_log(session, text=text, response=response)
        return response

    expand = await session.run(EXPAND_CYPHER, topKIds=top_ids)
    all_ids: set[str] = set(top_ids)
    async for record in expand:
        fact = record["f"]
        all_ids.add(fact["id"])
        for node in record["extended"] or []:
            if node is not None:
                all_ids.add(node["id"])
        for node in record["derived"] or []:
            if node is not None:
                all_ids.add(node["id"])

    _all_facts, _all_nodes, by_id = await _load_fact_bundle(session, sorted(all_ids))

    # facts_used: only current facts (never historical)
    facts_used = [
        FactUsed(
            id=fid,
            text=by_id[fid]["text"],
            source_doc_id=by_id[fid]["source_doc_id"] or "",
        )
        for fid in sorted(all_ids)
        if fid in by_id and by_id[fid].get("is_latest") is True
    ]

    highlight_ids = {f.id for f in facts_used}
    # Keep DERIVES counterparts in subgraph even if not latest (context), but not in facts_used
    for fid in all_ids:
        if fid in by_id:
            highlight_ids.add(fid)

    subgraph_nodes = [
        SubgraphNode(id=fid, label="Fact", properties=by_id[fid])
        for fid in sorted(highlight_ids)
        if fid in by_id
    ]

    rels: list[SubgraphRelationship] = []
    if highlight_ids:
        rel_result = await session.run(SUBGRAPH_RELS_CYPHER, ids=list(highlight_ids))
        async for record in rel_result:
            rel_type = record["rel_type"].lower()
            if rel_type in {"updates", "extends", "derives"}:
                rels.append(
                    SubgraphRelationship(
                        source=record["source"],
                        target=record["target"],
                        type=rel_type,  # type: ignore[arg-type]
                    )
                )

    system, user = build_query_answer_prompt(text, facts_used)
    cited: list[str] = []
    try:
        answer_model = await call_structured(
            system, user, QueryAnswer, temperature=0, job_id=job_id
        )
        answer = answer_model.answer
        valid_ids = {f.id for f in facts_used}
        cited = [fid for fid in answer_model.cited_fact_ids if fid in valid_ids]
        if not cited and facts_used:
            cited = [f.id for f in facts_used]
    except Exception:
        cited = []
        if facts_used:
            answer = (
                "Ecco i fatti rilevanti trovati (generazione risposta non disponibile): "
                + "; ".join(f.text for f in facts_used)
            )
        else:
            answer = "Nessun fatto rilevante trovato per questa domanda."

    response = QueryResponse(
        answer=answer,
        facts_used=facts_used,
        cited_fact_ids=cited,
        subgraph=Subgraph(nodes=subgraph_nodes, relationships=rels),
    )
    await _persist_query_log(session, text=text, response=response)
    return response


async def _persist_query_log(
    session: AsyncSession, *, text: str, response: QueryResponse
) -> None:
    """Best-effort QueryLog write — never fails the user-facing query (F4.2)."""
    from app.pipeline import query_log

    try:
        await query_log.write_query_log(
            session,
            query_id=str(uuid.uuid4()),
            text=text,
            answer=response.answer,
            cited_fact_ids=response.cited_fact_ids,
            all_fact_ids=[f.id for f in response.facts_used],
        )
    except Exception:
        logger.warning("Failed to persist QueryLog for text=%r", text[:80], exc_info=True)
