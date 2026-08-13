"""NodeQueryLog persistence and history reconstruction (Macrotask 4)."""

from __future__ import annotations

from typing import Any

from neo4j import AsyncSession

from app.api.schemas import QueryHistoryEntry
from app.models.query import (
    ConceptUsed,
    NodeQueryResponse,
    NodeSubgraph,
    NodeSubgraphNode,
    NodeSubgraphRelationship,
    NodeUsed,
)
from app.pipeline.node_query_engine import SUBGRAPH_RELS_CYPHER

WRITE_LOG_CYPHER = """
CREATE (q:NodeQueryLog {
  id: $id,
  text: $text,
  answer: $answer,
  cited_node_ids: $cited,
  created_at: datetime()
})
"""

LINK_USED_NODES_CYPHER = """
MATCH (q:NodeQueryLog {id: $id})
UNWIND $node_ids AS nid
MATCH (n:Node {id: nid})
MERGE (q)-[:USED]->(n)
"""

LINK_USED_CONCEPTS_CYPHER = """
MATCH (q:NodeQueryLog {id: $id})
UNWIND $concept_ids AS cid
MATCH (c:Concept {id: cid})
MERGE (q)-[:USED]->(c)
"""

LIST_LOGS_CYPHER = """
MATCH (q:NodeQueryLog)
RETURN q.id AS id, q.text AS text, q.created_at AS created_at
ORDER BY q.created_at DESC
LIMIT $limit
"""

GET_LOG_CYPHER = """
MATCH (q:NodeQueryLog {id: $id})
OPTIONAL MATCH (q)-[:USED]->(n:Node)
WHERE n.merged_into IS NULL
WITH q, [x IN collect(DISTINCT n.id) WHERE x IS NOT NULL] AS node_ids
OPTIONAL MATCH (q)-[:USED]->(c:Concept)
RETURN q, node_ids, [x IN collect(DISTINCT c.id) WHERE x IS NOT NULL] AS concept_ids
"""

LOAD_USED_CYPHER = """
MATCH (n)
WHERE n.id IN $ids
  AND ((n:Node AND n.merged_into IS NULL) OR n:Concept)
RETURN n.id AS id,
       n.name AS name,
       n.type AS type,
       labels(n) AS labels
"""


def _datetime_to_str(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _is_concept(labels: list[str] | None) -> bool:
    return bool(labels) and "Concept" in labels


async def write_node_query_log(
    session: AsyncSession,
    *,
    query_id: str,
    text: str,
    answer: str,
    cited_node_ids: list[str],
    node_ids: list[str],
    concept_ids: list[str],
) -> None:
    """Persist an immutable NodeQueryLog snapshot linked via USED."""
    await session.run(
        WRITE_LOG_CYPHER,
        id=query_id,
        text=text,
        answer=answer,
        cited=cited_node_ids,
    )
    if node_ids:
        await session.run(
            LINK_USED_NODES_CYPHER,
            id=query_id,
            node_ids=node_ids,
        )
    if concept_ids:
        await session.run(
            LINK_USED_CONCEPTS_CYPHER,
            id=query_id,
            concept_ids=concept_ids,
        )


async def list_node_query_logs(
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


async def get_node_query_log_detail(
    session: AsyncSession, query_id: str
) -> NodeQueryResponse | None:
    """Rebuild a NodeQueryResponse-shaped snapshot from a NodeQueryLog node."""
    result = await session.run(GET_LOG_CYPHER, id=query_id)
    record = await result.single()
    if record is None:
        return None

    q = record["q"]
    node_ids = [nid for nid in (record["node_ids"] or []) if nid]
    concept_ids = [cid for cid in (record["concept_ids"] or []) if cid]
    item_ids = node_ids + concept_ids

    by_node: dict[str, dict[str, Any]] = {}
    by_concept: dict[str, dict[str, Any]] = {}
    if item_ids:
        loaded = await session.run(LOAD_USED_CYPHER, ids=item_ids)
        async for row in loaded:
            labels = list(row["labels"] or [])
            if _is_concept(labels):
                by_concept[row["id"]] = {"name": row["name"] or row["id"]}
            else:
                raw_type = row["type"]
                node_type = raw_type if raw_type in {"entity", "event"} else "entity"
                by_node[row["id"]] = {
                    "name": row["name"] or row["id"],
                    "type": node_type,
                }

    nodes_used: list[NodeUsed] = []
    for nid in node_ids:
        props = by_node.get(nid)
        if props is None:
            continue
        nodes_used.append(
            NodeUsed(
                id=nid,
                name=props["name"],
                type=props["type"],
            )
        )

    concepts_used: list[ConceptUsed] = []
    for cid in concept_ids:
        props = by_concept.get(cid)
        if props is None:
            continue
        concepts_used.append(ConceptUsed(id=cid, name=props["name"]))

    subgraph_nodes: list[NodeSubgraphNode] = []
    for nid in node_ids:
        props = by_node.get(nid)
        if props is None:
            continue
        subgraph_nodes.append(
            NodeSubgraphNode(
                id=nid,
                label="Node",
                properties={"name": props["name"], "type": props["type"]},
            )
        )
    for cid in concept_ids:
        props = by_concept.get(cid)
        if props is None:
            continue
        subgraph_nodes.append(
            NodeSubgraphNode(
                id=cid,
                label="Concept",
                properties={"name": props["name"]},
            )
        )

    rels: list[NodeSubgraphRelationship] = []
    loaded_ids = [n.id for n in subgraph_nodes]
    if loaded_ids:
        rel_result = await session.run(SUBGRAPH_RELS_CYPHER, ids=loaded_ids)
        seen: set[tuple[str, str, str]] = set()
        async for rel in rel_result:
            key = (rel["source"], rel["target"], rel["rel_type"])
            if key in seen:
                continue
            seen.add(key)
            rels.append(
                NodeSubgraphRelationship(
                    source=rel["source"],
                    target=rel["target"],
                    type=rel["rel_type"],
                )
            )

    cited = list(q.get("cited_node_ids") or [])
    return NodeQueryResponse(
        answer=q.get("answer") or "",
        nodes_used=nodes_used,
        concepts_used=concepts_used,
        cited_node_ids=cited,
        subgraph=NodeSubgraph(nodes=subgraph_nodes, relationships=rels),
    )
