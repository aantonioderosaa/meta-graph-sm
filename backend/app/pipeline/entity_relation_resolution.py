"""Incremental entity↔entity :Relation resolution (Macrotask 4.1).

Reuses relations.classify_relation / build_relation_prompt; candidates are
same-endpoint edges identified by elementId(r). Never a full-graph Relation scan.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from neo4j import AsyncSession

from app.models.relations import RelationLabel
from app.pipeline.relations import classify_relation

logger = logging.getLogger(__name__)

OnClassified = Callable[[str, str, str], Awaitable[None]]
OnClassifyError = Callable[[str, str, str], Awaitable[None]]

VECTOR_RELATION_CANDIDATE_K = 10

FIND_FRESH_ENTITY_RELS_CYPHER = """
MATCH (a:Node {type:'entity'})-[r:Relation]->(b:Node {type:'entity'})
WHERE r.normalized_relation IS NULL
  AND a.merged_into IS NULL AND b.merged_into IS NULL
RETURN a.id AS head_id, b.id AS tail_id, r.relation AS relation, elementId(r) AS rel_id
"""

FIND_FRESH_ENTITY_RELS_TOUCHED_CYPHER = """
MATCH (a:Node {type:'entity'})-[r:Relation]->(b:Node {type:'entity'})
WHERE r.normalized_relation IS NULL
  AND a.merged_into IS NULL AND b.merged_into IS NULL
  AND (a.id IN $touched_ids OR b.id IN $touched_ids)
RETURN a.id AS head_id, b.id AS tail_id, r.relation AS relation, elementId(r) AS rel_id
"""

FIND_SAME_ENDPOINT_RELS_CYPHER = """
MATCH (a:Node {id:$head_id})-[r:Relation]->(b:Node {id:$tail_id})
WHERE elementId(r) <> $rel_id AND r.is_latest = true
RETURN elementId(r) AS rel_id, r.relation AS relation,
       r.normalized_relation AS normalized_relation
"""

FETCH_HEAD_EMBEDDING_CYPHER = """
MATCH (a:Node {id: $head_id})
RETURN a.embedding AS embedding
"""

VECTOR_ASSISTED_SAME_ENDPOINT_RELS_CYPHER = """
CALL db.index.vector.queryNodes('node_embedding', $k, $embedding)
YIELD node AS neighbor, score
WHERE neighbor.type = 'entity'
  AND neighbor.merged_into IS NULL
WITH collect(neighbor.id) AS neighbor_ids
MATCH (a:Node {id: $head_id})-[r:Relation]->(b:Node {id: $tail_id})
WHERE elementId(r) <> $rel_id
  AND r.is_latest = true
  AND ($head_id IN neighbor_ids OR $tail_id IN neighbor_ids)
RETURN elementId(r) AS rel_id, r.relation AS relation,
       r.normalized_relation AS normalized_relation
"""

APPLY_UPDATES_CYPHER = """
MATCH (a:Node {id:$head_id})-[neu:Relation]->(b:Node {id:$tail_id})
WHERE elementId(neu) = $new_rel_id
MATCH (a)-[old:Relation]->(b)
WHERE elementId(old) = $old_rel_id
SET neu.normalized_relation = 'updates',
    old.is_latest = false
"""

APPLY_EXTENDS_CYPHER = """
MATCH (a:Node {id:$head_id})-[neu:Relation]->(b:Node {id:$tail_id})
WHERE elementId(neu) = $new_rel_id
SET neu.normalized_relation = 'extends'
"""

MARK_PROCESSED_NONE_CYPHER = """
MATCH (a:Node {id:$head_id})-[neu:Relation]->(b:Node {id:$tail_id})
WHERE elementId(neu) = $new_rel_id
SET neu.normalized_relation = neu.relation
"""


@dataclass(frozen=True)
class EntityRelationCandidate:
    rel_id: str
    relation: str
    normalized_relation: str | None = None


def _candidate_from_record(record: dict) -> EntityRelationCandidate:
    return EntityRelationCandidate(
        rel_id=str(record["rel_id"]),
        relation=record["relation"],
        normalized_relation=record.get("normalized_relation"),
    )


async def _collect_candidates(result) -> list[EntityRelationCandidate]:
    candidates: list[EntityRelationCandidate] = []
    async for record in result:
        candidates.append(_candidate_from_record(record))
    return candidates


async def find_entity_relation_candidates(
    session: AsyncSession,
    head_id: str,
    tail_id: str,
    rel_id: str,
) -> list[EntityRelationCandidate]:
    """Same-endpoint is_latest Relations, plus a scoped vector assist on the endpoints."""
    result = await session.run(
        FIND_SAME_ENDPOINT_RELS_CYPHER,
        head_id=head_id,
        tail_id=tail_id,
        rel_id=rel_id,
    )
    by_id: dict[str, EntityRelationCandidate] = {}
    for candidate in await _collect_candidates(result):
        by_id[candidate.rel_id] = candidate

    embedding_result = await session.run(FETCH_HEAD_EMBEDDING_CYPHER, head_id=head_id)
    embedding = None
    async for record in embedding_result:
        embedding = record.get("embedding")
        break
    if embedding:
        vector_result = await session.run(
            VECTOR_ASSISTED_SAME_ENDPOINT_RELS_CYPHER,
            k=VECTOR_RELATION_CANDIDATE_K,
            embedding=list(embedding),
            head_id=head_id,
            tail_id=tail_id,
            rel_id=rel_id,
        )
        for candidate in await _collect_candidates(vector_result):
            by_id.setdefault(candidate.rel_id, candidate)

    return list(by_id.values())


async def classify_and_apply_entity_relation(
    session: AsyncSession,
    head_id: str,
    tail_id: str,
    rel_id: str,
    new_relation_text: str,
    job_id: str,
) -> str | None:
    """Classify a fresh entity-entity Relation against same-endpoint candidates.

    Returns ``updates``, ``extends``, or ``none``.
    """
    candidates = await find_entity_relation_candidates(session, head_id, tail_id, rel_id)
    for candidate in candidates:
        verdict = await classify_relation(
            new_relation_text,
            candidate.relation,
            job_id=job_id,
        )
        if verdict.relation == RelationLabel.replaces:
            await session.run(
                APPLY_UPDATES_CYPHER,
                head_id=head_id,
                tail_id=tail_id,
                new_rel_id=rel_id,
                old_rel_id=candidate.rel_id,
            )
            return "updates"
        if verdict.relation == RelationLabel.extends:
            await session.run(
                APPLY_EXTENDS_CYPHER,
                head_id=head_id,
                tail_id=tail_id,
                new_rel_id=rel_id,
            )
            return "extends"

    await session.run(
        MARK_PROCESSED_NONE_CYPHER,
        head_id=head_id,
        tail_id=tail_id,
        new_rel_id=rel_id,
    )
    return "none"


async def resolve_fresh_entity_relations(
    session: AsyncSession,
    job_id: str,
    touched_entity_ids: set[str] | None = None,
    *,
    on_classified: OnClassified | None = None,
    on_error: OnClassifyError | None = None,
) -> int:
    """Classify fresh entity-entity Relations. Scoped to touched ids when provided."""
    if touched_entity_ids is None:
        result = await session.run(FIND_FRESH_ENTITY_RELS_CYPHER)
    else:
        result = await session.run(
            FIND_FRESH_ENTITY_RELS_TOUCHED_CYPHER,
            touched_ids=list(touched_entity_ids),
        )

    fresh: list[tuple[str, str, str, str]] = []
    async for record in result:
        fresh.append(
            (
                record["head_id"],
                record["tail_id"],
                str(record["rel_id"]),
                record["relation"],
            )
        )

    processed = 0
    for head_id, tail_id, rel_id, relation in fresh:
        try:
            outcome = await classify_and_apply_entity_relation(
                session,
                head_id,
                tail_id,
                rel_id,
                relation,
                job_id,
            )
        except Exception as exc:
            logger.exception(
                "entity_relation_classification_failed head=%s tail=%s",
                head_id,
                tail_id,
            )
            if on_error is not None:
                await on_error(head_id, tail_id, str(exc))
            continue
        if outcome is not None:
            processed += 1
            if on_classified is not None:
                await on_classified(outcome, head_id, tail_id)
    return processed
