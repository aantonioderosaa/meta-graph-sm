"""Read-only retrieval substrate with metadata (Fase 19).

Six thin wrappers for the future agentic context layer. Zero LLM calls.
This module never mutates the graph.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from neo4j import AsyncSession

from app.api.schemas import DomainDictionaryResponse, NodeMetadataResponse
from app.models.kernel import MEMBER_OF
from app.pipeline import embeddings
from app.pipeline.node_graph_engine import (
    get_domain_dictionary as _get_domain_dictionary,
)
from app.pipeline.node_graph_engine import (
    get_node_metadata as _get_node_metadata,
)

DEFAULT_RETRIEVAL_K = 10
_MEMBER_OF_REL = MEMBER_OF.upper()

NODE_SUMMARY_FULLTEXT_CYPHER = f"""
CALL db.index.fulltext.queryNodes('node_summary_fulltext', $text)
YIELD node, score
WHERE node:Node AND node.merged_into IS NULL
OPTIONAL MATCH (node)-[:{_MEMBER_OF_REL}]->(home:Concept)
OPTIONAL MATCH (node)-[:DERIVED_FROM]->(ch:Chunk)
WITH node, score, home,
     collect(DISTINCT ch.id) AS chunk_ids,
     collect(DISTINCT ch.doc_id) AS doc_ids
RETURN node.id AS id,
       node.name AS name,
       node.summary AS summary,
       node.type AS type,
       node.kernel_category AS kernel_category,
       node.created_at AS created_at,
       home.id AS member_of,
       home.name AS member_of_name,
       chunk_ids,
       doc_ids,
       score
ORDER BY score DESC
LIMIT $k
"""

NODE_CONCEPT_FULLTEXT_CYPHER = f"""
CALL db.index.fulltext.queryNodes('node_concept_fulltext', $text)
YIELD node, score
WHERE node:Node AND node.merged_into IS NULL
OPTIONAL MATCH (node)-[:{_MEMBER_OF_REL}]->(home:Concept)
OPTIONAL MATCH (node)-[:DERIVED_FROM]->(ch:Chunk)
WITH node, score, home,
     collect(DISTINCT ch.id) AS chunk_ids,
     collect(DISTINCT ch.doc_id) AS doc_ids
RETURN node.id AS id,
       node.name AS name,
       node.summary AS summary,
       node.type AS type,
       node.kernel_category AS kernel_category,
       node.created_at AS created_at,
       home.id AS member_of,
       home.name AS member_of_name,
       chunk_ids,
       doc_ids,
       score
ORDER BY score DESC
LIMIT $k
"""

RELATION_WITNESS_FULLTEXT_CYPHER = """
CALL db.index.fulltext.queryRelationships('relation_witness_fulltext', $text)
YIELD relationship, score
WITH relationship, score
MATCH (a)-[relationship]->(b)
WHERE a:Node AND b:Node
  AND a.merged_into IS NULL AND b.merged_into IS NULL
RETURN elementId(relationship) AS id,
       a.id AS from_id,
       a.name AS from_name,
       b.id AS to_id,
       b.name AS to_name,
       relationship.relation AS relation,
       relationship.normalized_relation AS normalized_relation,
       relationship.kernel_parent AS kernel_parent,
       relationship.is_latest AS is_latest,
       relationship.valid_time AS valid_time,
       relationship.system_time AS system_time,
       relationship.provenance AS provenance,
       relationship.witnesses_a AS witnesses_a,
       relationship.witnesses_b AS witnesses_b,
       relationship.witness_text AS witness_text,
       score
ORDER BY score DESC
LIMIT $k
"""

RELATION_FULLTEXT_CYPHER = """
CALL db.index.fulltext.queryRelationships('relation_fulltext', $text)
YIELD relationship, score
WITH relationship, score
MATCH (a)-[relationship]->(b)
WHERE a:Node AND b:Node
  AND a.merged_into IS NULL AND b.merged_into IS NULL
RETURN elementId(relationship) AS id,
       a.id AS from_id,
       a.name AS from_name,
       b.id AS to_id,
       b.name AS to_name,
       relationship.relation AS relation,
       relationship.normalized_relation AS normalized_relation,
       relationship.kernel_parent AS kernel_parent,
       relationship.is_latest AS is_latest,
       relationship.valid_time AS valid_time,
       relationship.system_time AS system_time,
       relationship.provenance AS provenance,
       relationship.witnesses_a AS witnesses_a,
       relationship.witnesses_b AS witnesses_b,
       relationship.witness_text AS witness_text,
       score
ORDER BY score DESC
LIMIT $k
"""

NODE_SUMMARY_VECTOR_CYPHER = f"""
CALL db.index.vector.queryNodes('node_summary_embedding', $k, $embedding)
YIELD node, score
WHERE node.merged_into IS NULL
OPTIONAL MATCH (node)-[:{_MEMBER_OF_REL}]->(home:Concept)
OPTIONAL MATCH (node)-[:DERIVED_FROM]->(ch:Chunk)
WITH node, score, home,
     collect(DISTINCT ch.id) AS chunk_ids,
     collect(DISTINCT ch.doc_id) AS doc_ids
RETURN node.id AS id,
       node.name AS name,
       node.summary AS summary,
       node.type AS type,
       node.kernel_category AS kernel_category,
       node.created_at AS created_at,
       home.id AS member_of,
       home.name AS member_of_name,
       chunk_ids,
       doc_ids,
       score
ORDER BY score DESC
"""

NODE_RELATIONS_CYPHER = """
MATCH (n:Node {id: $node_id})-[r:Relation]-(other:Node)
WHERE other.id <> $node_id
  AND n.merged_into IS NULL
  AND other.merged_into IS NULL
RETURN r.relation AS relation,
       r.normalized_relation AS normalized_relation,
       r.kernel_parent AS kernel_parent,
       r.is_latest AS is_latest,
       r.valid_time AS valid_time,
       r.system_time AS system_time,
       r.witnesses_a AS witnesses_a,
       r.witnesses_b AS witnesses_b,
       r.provenance AS provenance,
       r.witness_text AS witness_text,
       CASE WHEN startNode(r) = n THEN 'outgoing' ELSE 'incoming' END AS direction,
       other.id AS other_id,
       other.name AS other_name
"""

FACTS_FROM_SOURCE_NODES_CYPHER = f"""
MATCH (c:Chunk {{doc_id: $doc_id}})
MATCH (n:Node)-[:DERIVED_FROM]->(c)
WHERE n.merged_into IS NULL
OPTIONAL MATCH (n)-[:{_MEMBER_OF_REL}]->(home:Concept)
WITH n, home, collect(DISTINCT c.id) AS chunk_ids
RETURN n.id AS id,
       n.name AS name,
       n.summary AS summary,
       n.type AS type,
       n.kernel_category AS kernel_category,
       n.created_at AS created_at,
       home.id AS member_of,
       home.name AS member_of_name,
       chunk_ids
"""

FACTS_FROM_SOURCE_RELS_CYPHER = """
MATCH (c:Chunk {doc_id: $doc_id})
MATCH (n:Node)-[:DERIVED_FROM]->(c)
WHERE n.merged_into IS NULL
MATCH (n)-[r:Relation]-(other:Node)
WHERE other.merged_into IS NULL
WITH DISTINCT r, startNode(r) AS a, endNode(r) AS b
RETURN elementId(r) AS id,
       a.id AS from_id,
       a.name AS from_name,
       b.id AS to_id,
       b.name AS to_name,
       r.relation AS relation,
       r.normalized_relation AS normalized_relation,
       r.kernel_parent AS kernel_parent,
       r.is_latest AS is_latest,
       r.valid_time AS valid_time,
       r.system_time AS system_time,
       r.provenance AS provenance,
       r.witnesses_a AS witnesses_a,
       r.witnesses_b AS witnesses_b,
       r.witness_text AS witness_text
"""


@dataclass(frozen=True)
class RelationSnapshot:
    """Incident :Relation on a node, with metadata (F19.4)."""

    text: str
    relation: str | None
    normalized_relation: str | None
    kernel_parent: str | None
    is_latest: bool | None
    valid_time: Any
    system_time: Any
    witnesses_a: tuple[str, ...]
    witnesses_b: tuple[str, ...]
    provenance: Any
    direction: str
    other_id: str
    other_name: str | None
    witness_text: str | None = None


@dataclass(frozen=True)
class RetrievalHit:
    """Fulltext or vector hit with metadata, not only id/score."""

    kind: Literal["node", "relation"]
    score: float
    id: str
    index: str
    name: str | None = None
    summary: str | None = None
    type: str | None = None
    kernel_category: str | None = None
    member_of: str | None = None
    member_of_name: str | None = None
    created_at: Any = None
    chunk_ids: tuple[str, ...] = ()
    doc_ids: tuple[str, ...] = ()
    relation: str | None = None
    normalized_relation: str | None = None
    kernel_parent: str | None = None
    is_latest: bool | None = None
    valid_time: Any = None
    system_time: Any = None
    provenance: Any = None
    witnesses_a: tuple[str, ...] = ()
    witnesses_b: tuple[str, ...] = ()
    witness_text: str | None = None
    from_id: str | None = None
    from_name: str | None = None
    to_id: str | None = None
    to_name: str | None = None


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value
    iso_fn = getattr(value, "iso_format", None) or getattr(value, "isoformat", None)
    if callable(iso_fn):
        return str(iso_fn())
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return tuple(_plain(item) for item in value)
    try:
        return [_plain(item) for item in value]
    except TypeError:
        return str(value)


def _str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    try:
        return tuple(str(item) for item in value if str(item).strip())
    except TypeError:
        text = str(value).strip()
        return (text,) if text else ()


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _score(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


async def _rows(result: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    async for record in result:
        rows.append(dict(record))
    return rows


def _node_hit(row: dict[str, Any], *, index: str) -> RetrievalHit:
    return RetrievalHit(
        kind="node",
        score=_score(row.get("score")),
        id=str(row["id"]),
        index=index,
        name=_optional_str(row.get("name")),
        summary=_optional_str(row.get("summary")),
        type=_optional_str(row.get("type")),
        kernel_category=_optional_str(row.get("kernel_category")),
        member_of=_optional_str(row.get("member_of")),
        member_of_name=_optional_str(row.get("member_of_name")),
        created_at=_plain(row.get("created_at")),
        chunk_ids=_str_tuple(row.get("chunk_ids")),
        doc_ids=_str_tuple(row.get("doc_ids")),
    )


def _relation_hit(row: dict[str, Any], *, index: str) -> RetrievalHit:
    return RetrievalHit(
        kind="relation",
        score=_score(row.get("score")),
        id=str(row["id"]),
        index=index,
        relation=_optional_str(row.get("relation")),
        normalized_relation=_optional_str(row.get("normalized_relation")),
        kernel_parent=_optional_str(row.get("kernel_parent")),
        is_latest=_optional_bool(row.get("is_latest")),
        valid_time=_plain(row.get("valid_time")),
        system_time=_plain(row.get("system_time")),
        provenance=_plain(row.get("provenance")),
        witnesses_a=_str_tuple(row.get("witnesses_a")),
        witnesses_b=_str_tuple(row.get("witnesses_b")),
        witness_text=_optional_str(row.get("witness_text")),
        from_id=_optional_str(row.get("from_id")),
        from_name=_optional_str(row.get("from_name")),
        to_id=_optional_str(row.get("to_id")),
        to_name=_optional_str(row.get("to_name")),
    )


def _dedupe_hits(hits: list[RetrievalHit]) -> list[RetrievalHit]:
    best: dict[tuple[str, str], RetrievalHit] = {}
    for hit in hits:
        key = (hit.kind, hit.id)
        previous = best.get(key)
        if previous is None or hit.score > previous.score:
            best[key] = hit
    return sorted(best.values(), key=lambda item: item.score, reverse=True)


async def search_fulltext(
    session: AsyncSession,
    text: str,
    *,
    k: int = DEFAULT_RETRIEVAL_K,
) -> list[RetrievalHit]:
    """Query summary and witness fulltext indexes (plus existing name/relation)."""
    query = (text or "").strip()
    if not query or k <= 0:
        return []
    hits: list[RetrievalHit] = []
    node_queries = (
        (NODE_SUMMARY_FULLTEXT_CYPHER, "node_summary_fulltext"),
        (NODE_CONCEPT_FULLTEXT_CYPHER, "node_concept_fulltext"),
    )
    for cypher, index_name in node_queries:
        result = await session.run(cypher, text=query, k=k)
        for row in await _rows(result):
            hits.append(_node_hit(row, index=index_name))
    rel_queries = (
        (RELATION_WITNESS_FULLTEXT_CYPHER, "relation_witness_fulltext"),
        (RELATION_FULLTEXT_CYPHER, "relation_fulltext"),
    )
    for cypher, index_name in rel_queries:
        result = await session.run(cypher, text=query, k=k)
        for row in await _rows(result):
            hits.append(_relation_hit(row, index=index_name))
    return _dedupe_hits(hits)[:k]


async def search_vector(
    session: AsyncSession,
    text: str,
    *,
    k: int = DEFAULT_RETRIEVAL_K,
) -> list[RetrievalHit]:
    """Embed arbitrary query text and kNN on ``node_summary_embedding``."""
    query = (text or "").strip()
    if not query or k <= 0:
        return []
    vector = embeddings.embed(query)
    result = await session.run(NODE_SUMMARY_VECTOR_CYPHER, k=k, embedding=vector)
    return [_node_hit(row, index="node_summary_embedding") for row in await _rows(result)]


async def get_metadata(session: AsyncSession, node_id: str) -> NodeMetadataResponse | None:
    return await _get_node_metadata(session, node_id)


async def get_node_relations(session: AsyncSession, node_id: str) -> list[RelationSnapshot]:
    result = await session.run(NODE_RELATIONS_CYPHER, node_id=node_id)
    snapshots: list[RelationSnapshot] = []
    for row in await _rows(result):
        relation = _optional_str(row.get("relation"))
        normalized = _optional_str(row.get("normalized_relation"))
        snapshots.append(
            RelationSnapshot(
                text=(normalized or relation or "").strip(),
                relation=relation,
                normalized_relation=normalized,
                kernel_parent=_optional_str(row.get("kernel_parent")),
                is_latest=_optional_bool(row.get("is_latest")),
                valid_time=_plain(row.get("valid_time")),
                system_time=_plain(row.get("system_time")),
                witnesses_a=_str_tuple(row.get("witnesses_a")),
                witnesses_b=_str_tuple(row.get("witnesses_b")),
                provenance=_plain(row.get("provenance")),
                direction=str(row.get("direction") or ""),
                other_id=str(row["other_id"]),
                other_name=_optional_str(row.get("other_name")),
                witness_text=_optional_str(row.get("witness_text")),
            )
        )
    return snapshots


async def get_relations(session: AsyncSession, node_id: str) -> list[RelationSnapshot]:
    return await get_node_relations(session, node_id)


async def get_domain_dictionary(
    session: AsyncSession, concept_id: str
) -> DomainDictionaryResponse | None:
    return await _get_domain_dictionary(session, concept_id)


async def facts_from_source(session: AsyncSession, doc_id: str) -> list[dict[str, Any]]:
    """Nodes derived from ``doc_id`` chunks, plus :Relation incident to them."""
    facts: list[dict[str, Any]] = []
    node_result = await session.run(FACTS_FROM_SOURCE_NODES_CYPHER, doc_id=doc_id)
    for row in await _rows(node_result):
        facts.append(
            {
                "kind": "node",
                "id": str(row["id"]),
                "name": _optional_str(row.get("name")),
                "summary": _optional_str(row.get("summary")),
                "type": _optional_str(row.get("type")),
                "kernel_category": _optional_str(row.get("kernel_category")),
                "created_at": _plain(row.get("created_at")),
                "member_of": _optional_str(row.get("member_of")),
                "member_of_name": _optional_str(row.get("member_of_name")),
                "chunk_ids": list(_str_tuple(row.get("chunk_ids"))),
                "doc_id": doc_id,
            }
        )
    rel_result = await session.run(FACTS_FROM_SOURCE_RELS_CYPHER, doc_id=doc_id)
    for row in await _rows(rel_result):
        relation = _optional_str(row.get("relation"))
        normalized = _optional_str(row.get("normalized_relation"))
        facts.append(
            {
                "kind": "relation",
                "id": str(row["id"]),
                "text": (normalized or relation or "").strip(),
                "relation": relation,
                "normalized_relation": normalized,
                "kernel_parent": _optional_str(row.get("kernel_parent")),
                "is_latest": _optional_bool(row.get("is_latest")),
                "valid_time": _plain(row.get("valid_time")),
                "system_time": _plain(row.get("system_time")),
                "provenance": _plain(row.get("provenance")),
                "witnesses_a": list(_str_tuple(row.get("witnesses_a"))),
                "witnesses_b": list(_str_tuple(row.get("witnesses_b"))),
                "witness_text": _optional_str(row.get("witness_text")),
                "from_id": _optional_str(row.get("from_id")),
                "from_name": _optional_str(row.get("from_name")),
                "to_id": _optional_str(row.get("to_id")),
                "to_name": _optional_str(row.get("to_name")),
                "doc_id": doc_id,
            }
        )
    return facts
