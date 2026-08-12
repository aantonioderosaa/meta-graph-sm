"""QueryLog persistence and history reconstruction (Epic F4)."""

from __future__ import annotations

from typing import Any

from neo4j import AsyncSession

from app.api.schemas import QueryHistoryEntry
from app.models.query import (
    FactUsed,
    QueryResponse,
    Subgraph,
    SubgraphNode,
    SubgraphRelationship,
)
from app.pipeline.query_engine import SUBGRAPH_RELS_CYPHER, _load_fact_bundle

WRITE_LOG_CYPHER = """
CREATE (q:QueryLog {
  id: $id,
  text: $text,
  answer: $answer,
  cited_fact_ids: $cited,
  created_at: datetime()
})
"""

LINK_USED_CYPHER = """
MATCH (q:QueryLog {id: $id})
UNWIND $fact_ids AS fid
MATCH (f:Fact {id: fid})
MERGE (q)-[:USED]->(f)
"""

LIST_LOGS_CYPHER = """
MATCH (q:QueryLog)
RETURN q.id AS id, q.text AS text, q.created_at AS created_at
ORDER BY q.created_at DESC
LIMIT $limit
"""

GET_LOG_CYPHER = """
MATCH (q:QueryLog {id: $id})
OPTIONAL MATCH (q)-[:USED]->(f:Fact)
RETURN q, collect(DISTINCT f.id) AS fact_ids
"""


def _datetime_to_str(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


async def write_query_log(
    session: AsyncSession,
    *,
    query_id: str,
    text: str,
    answer: str,
    cited_fact_ids: list[str],
    all_fact_ids: list[str],
) -> None:
    """Persist an immutable QueryLog snapshot linked to facts_used via USED."""
    await session.run(
        WRITE_LOG_CYPHER,
        id=query_id,
        text=text,
        answer=answer,
        cited=cited_fact_ids,
    )
    if all_fact_ids:
        await session.run(
            LINK_USED_CYPHER,
            id=query_id,
            fact_ids=all_fact_ids,
        )


async def list_query_logs(
    session: AsyncSession, *, limit: int = 20
) -> list[QueryHistoryEntry]:
    result = await session.run(LIST_LOGS_CYPHER, limit=limit)
    items: list[QueryHistoryEntry] = []
    async for record in result:
        items.append(
            QueryHistoryEntry(
                id=record["id"],
                text=record["text"] or "",
                created_at=_datetime_to_str(record["created_at"]),
            )
        )
    return items


async def get_query_log_detail(
    session: AsyncSession, query_id: str
) -> QueryResponse | None:
    """Rebuild a QueryResponse-shaped snapshot from a QueryLog node."""
    result = await session.run(GET_LOG_CYPHER, id=query_id)
    record = await result.single()
    if record is None:
        return None

    q = record["q"]
    fact_ids = [fid for fid in (record["fact_ids"] or []) if fid]
    facts_used_bundle, _nodes, by_id = await _load_fact_bundle(session, fact_ids)

    # Preserve USED-set order as returned; facts_used from bundle may reorder.
    by_loaded = {f.id: f for f in facts_used_bundle}
    facts_used: list[FactUsed] = []
    for fid in fact_ids:
        if fid in by_loaded:
            facts_used.append(by_loaded[fid])
        elif fid in by_id:
            facts_used.append(
                FactUsed(
                    id=fid,
                    text=by_id[fid]["text"],
                    source_doc_id=by_id[fid].get("source_doc_id") or "",
                )
            )

    subgraph_nodes = [
        SubgraphNode(id=fid, label="Fact", properties=by_id[fid])
        for fid in fact_ids
        if fid in by_id
    ]

    rels: list[SubgraphRelationship] = []
    if fact_ids:
        rel_result = await session.run(SUBGRAPH_RELS_CYPHER, ids=fact_ids)
        async for rel in rel_result:
            rel_type = rel["rel_type"].lower()
            if rel_type in {"updates", "extends", "derives"}:
                rels.append(
                    SubgraphRelationship(
                        source=rel["source"],
                        target=rel["target"],
                        type=rel_type,  # type: ignore[arg-type]
                    )
                )

    cited = list(q.get("cited_fact_ids") or [])
    return QueryResponse(
        answer=q.get("answer") or "",
        facts_used=facts_used,
        cited_fact_ids=cited,
        subgraph=Subgraph(nodes=subgraph_nodes, relationships=rels),
    )
