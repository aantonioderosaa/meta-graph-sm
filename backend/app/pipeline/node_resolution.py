"""Incremental node dedup: exact-name → vector candidates → LLM (Macrotask 3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from neo4j import AsyncSession

from app.core.config import settings
from app.core.llm_client import call_structured
from app.models.node_extraction import NodeDedupResult
from app.pipeline.identity_resolution import link_possibly_same_as

HIGH_CONFIDENCE_SCORE = 0.90  # identity, not mere similarity

NodeCandidateVia = Literal["exact_name", "embedding", "high_confidence"]

FIND_NODE_CANDIDATES_CYPHER = """
CALL db.index.vector.queryNodes('node_embedding', $k, $embedding)
YIELD node AS candidate, score
WHERE candidate.type = $type
  AND candidate.merged_into IS NULL
  AND candidate.id <> $node_id
RETURN candidate.id AS id, candidate.name AS name, score
ORDER BY score DESC
"""

FIND_EXACT_NAME_CYPHER = """
MATCH (c:Node {type: $type, name: $name})
WHERE c.merged_into IS NULL AND c.id <> $node_id
RETURN c.id AS id, c.name AS name
"""

READ_OUTGOING_RELATIONS_CYPHER = """
MATCH (dup:Node {id: $dup_id})-[r:Relation]->(other:Node)
WHERE other.id <> $canon_id
RETURN r, other.id AS other_id
"""

READ_INCOMING_RELATIONS_CYPHER = """
MATCH (other:Node)-[r:Relation]->(dup:Node {id: $dup_id})
WHERE other.id <> $canon_id
RETURN r, other.id AS other_id
"""

CREATE_OUTGOING_ON_CANON_CYPHER = """
MATCH (canon:Node {id: $canon_id}), (other:Node {id: $other_id})
CREATE (canon)-[nr:Relation]->(other)
SET nr += $props
"""

CREATE_INCOMING_ON_CANON_CYPHER = """
MATCH (canon:Node {id: $canon_id}), (other:Node {id: $other_id})
CREATE (other)-[nr:Relation]->(canon)
SET nr += $props
"""

DELETE_DUP_RELATIONS_CYPHER = """
MATCH (dup:Node {id: $dup_id})-[r:Relation]-()
DELETE r
"""

COPY_HAS_CONCEPT_CYPHER = """
MATCH (dup:Node {id: $dup_id})-[hc:HAS_CONCEPT]->(c:Concept)
MATCH (canon:Node {id: $canon_id})
MERGE (canon)-[:HAS_CONCEPT]->(c)
DELETE hc
"""

COPY_DERIVED_FROM_CYPHER = """
MATCH (dup:Node {id: $dup_id})-[df:DERIVED_FROM]->(ch:Chunk)
MATCH (canon:Node {id: $canon_id})
MERGE (canon)-[:DERIVED_FROM]->(ch)
DELETE df
"""

SET_MERGED_INTO_CYPHER = """
MATCH (dup:Node {id: $dup_id})
SET dup.merged_into = $canon_id
"""

COLLAPSE_OUTGOING_RELATIONS_CYPHER = """
MATCH (canon:Node {id: $canon_id})-[r:Relation]->(other:Node)
WITH other, coalesce(r.normalized_relation, r.relation) AS key, r
ORDER BY r.created_at DESC
WITH other, key, collect(r) AS rels
WHERE size(rels) > 1
WITH rels[0] AS keep, rels[1..] AS extra
UNWIND extra AS r
DELETE r
"""

COLLAPSE_INCOMING_RELATIONS_CYPHER = """
MATCH (other:Node)-[r:Relation]->(canon:Node {id: $canon_id})
WITH other, coalesce(r.normalized_relation, r.relation) AS key, r
ORDER BY r.created_at DESC
WITH other, key, collect(r) AS rels
WHERE size(rels) > 1
WITH rels[0] AS keep, rels[1..] AS extra
UNWIND extra AS r
DELETE r
"""

DEDUP_SYSTEM_PROMPT = (
    "Confronta il NODO NUOVO con i CANDIDATI e decidi se si riferiscono alla stessa "
    "entità o allo stesso evento nel mondo reale.\n"
    "Se il nodo nuovo è un duplicato di uno dei candidati, imposta `duplicate_of` "
    "all'id di quel candidato. Se è un nodo nuovo, distinto da tutti i candidati, "
    "imposta `duplicate_of` a null.\n"
    "Usa solo id presenti nell'elenco dei candidati; non inventare id.\n"
    "Rispondi solo secondo lo schema fornito, senza aggiungere testo libero."
)


@dataclass(frozen=True)
class NodeCandidate:
    id: str
    name: str
    score: float | None = None
    via: NodeCandidateVia = "embedding"


def build_dedup_prompt(new_name: str, candidates: list[NodeCandidate]) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for node duplicate classification."""
    lines: list[str] = []
    for candidate in candidates:
        line = f'- id="{candidate.id}", nome="{candidate.name}"'
        if candidate.score is not None:
            line += f", score={candidate.score:.3f}"
        lines.append(line)
    listed = "\n".join(lines) if lines else "(nessun candidato)"
    user_prompt = (
        f'NODO NUOVO: "{new_name}"\n'
        f"CANDIDATI:\n{listed}\n\n"
        "Quale candidato (se esiste) è la stessa entità/lo stesso evento nel mondo reale?"
    )
    return DEDUP_SYSTEM_PROMPT, user_prompt


def _relationship_properties(rel: object) -> dict[str, Any]:
    if rel is None:
        return {}
    if isinstance(rel, dict):
        return dict(rel)
    items = getattr(rel, "items", None)
    if callable(items):
        return dict(items())
    keys = getattr(rel, "keys", None)
    if callable(keys):
        return {key: rel[key] for key in keys()}
    return {}


async def find_node_candidates(
    session: AsyncSession,
    node_id: str,
    node_type: str,
    embedding: list[float],
    name: str,
    k: int = 10,
) -> list[NodeCandidate]:
    """Find same-type candidates via exact name, else vector top-k. Never a full scan."""
    exact_result = await session.run(
        FIND_EXACT_NAME_CYPHER,
        type=node_type,
        name=name,
        node_id=node_id,
    )
    exact: list[NodeCandidate] = []
    async for record in exact_result:
        exact.append(
            NodeCandidate(
                id=record["id"],
                name=record["name"],
                score=None,
                via="exact_name",
            )
        )
    if exact:
        return exact

    vector_result = await session.run(
        FIND_NODE_CANDIDATES_CYPHER,
        k=k,
        embedding=embedding,
        type=node_type,
        node_id=node_id,
    )
    candidates: list[NodeCandidate] = []
    async for record in vector_result:
        candidates.append(
            NodeCandidate(
                id=record["id"],
                name=record["name"],
                score=float(record["score"]),
                via="embedding",
            )
        )
    return candidates


async def classify_node_duplicate(
    new_name: str,
    candidates: list[NodeCandidate],
    job_id: str | None = None,
) -> NodeDedupResult:
    """Ask the LLM which candidate (if any) is the same real-world entity/event."""
    if not candidates:
        return NodeDedupResult(duplicate_of=None)

    system_prompt, user_prompt = build_dedup_prompt(new_name, candidates)
    result = await call_structured(
        system_prompt,
        user_prompt,
        NodeDedupResult,
        temperature=0,
        job_id=job_id,
    )
    allowed = {candidate.id for candidate in candidates}
    if result.duplicate_of not in allowed:
        return NodeDedupResult(duplicate_of=None)
    return result


def _fast_path_canonical(candidates: list[NodeCandidate]) -> str | None:
    exact = [c for c in candidates if c.via == "exact_name"]
    if exact:
        return exact[0].id
    high = [
        c
        for c in candidates
        if c.score is not None and c.score >= HIGH_CONFIDENCE_SCORE
    ]
    if len(high) == 1:
        return high[0].id
    return None


async def merge_nodes(session: AsyncSession, dup_id: str, canon_id: str) -> None:
    """Redirect dup edges onto canon, collapse parallel Relations, keep dup for audit."""
    if dup_id == canon_id:
        return

    outgoing = await session.run(
        READ_OUTGOING_RELATIONS_CYPHER,
        dup_id=dup_id,
        canon_id=canon_id,
    )
    outgoing_rows: list[tuple[dict[str, Any], str]] = []
    async for record in outgoing:
        outgoing_rows.append((_relationship_properties(record["r"]), record["other_id"]))

    incoming = await session.run(
        READ_INCOMING_RELATIONS_CYPHER,
        dup_id=dup_id,
        canon_id=canon_id,
    )
    incoming_rows: list[tuple[dict[str, Any], str]] = []
    async for record in incoming:
        incoming_rows.append((_relationship_properties(record["r"]), record["other_id"]))

    for props, other_id in outgoing_rows:
        await session.run(
            CREATE_OUTGOING_ON_CANON_CYPHER,
            canon_id=canon_id,
            other_id=other_id,
            props=props,
        )
    for props, other_id in incoming_rows:
        await session.run(
            CREATE_INCOMING_ON_CANON_CYPHER,
            canon_id=canon_id,
            other_id=other_id,
            props=props,
        )

    await session.run(DELETE_DUP_RELATIONS_CYPHER, dup_id=dup_id)
    await session.run(COPY_HAS_CONCEPT_CYPHER, dup_id=dup_id, canon_id=canon_id)
    await session.run(COPY_DERIVED_FROM_CYPHER, dup_id=dup_id, canon_id=canon_id)
    await session.run(SET_MERGED_INTO_CYPHER, dup_id=dup_id, canon_id=canon_id)
    await session.run(COLLAPSE_OUTGOING_RELATIONS_CYPHER, canon_id=canon_id)
    await session.run(COLLAPSE_INCOMING_RELATIONS_CYPHER, canon_id=canon_id)


async def resolve_node(
    session: AsyncSession,
    node_id: str,
    node_type: str,
    name: str,
    embedding: list[float],
    job_id: str | None = None,
) -> str:
    """Return the canonical node id, merging into a duplicate when one is found.

    With ``ENABLE_FACET_IDENTITY``, a candidate becomes ``POSSIBLY_SAME_AS``
    (never ``SAME_AS``, never ``merge_nodes``) and this node keeps its own id.
    """
    candidates = await find_node_candidates(
        session,
        node_id,
        node_type,
        embedding,
        name,
    )
    if not candidates:
        return node_id

    canon_id = _fast_path_canonical(candidates)
    if canon_id is None:
        verdict = await classify_node_duplicate(name, candidates, job_id)
        canon_id = verdict.duplicate_of

    if canon_id is None or canon_id == node_id:
        return node_id

    if settings.ENABLE_FACET_IDENTITY:
        await link_possibly_same_as(session, node_id, canon_id)
        return node_id

    await merge_nodes(session, node_id, canon_id)
    return canon_id
