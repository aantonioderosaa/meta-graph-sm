"""Post-batch judge: epistemic hygiene after dreaming (Fase 10).

Runs once at the end of each dreaming batch (after ``reconcile``). Writes only
through existing primitives: INGEST-style ``:Relation`` witness splits (no new
S2 facts) and Famiglia B ``EQUIVALENT_TO``. No new kernel types.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from neo4j import AsyncSession

from app.core.config import settings
from app.models.kernel import MEMBER_OF

logger = logging.getLogger(__name__)

STAGE = "judge"
_MEMBER_OF_REL = MEMBER_OF.upper()

RequeuePair = Callable[[str, str], Awaitable[None]]


@dataclass
class JudgeStats:
    anti_blur: int = 0
    equivalent_to: int = 0


def cosine(a: list[float], b: list[float]) -> float:
    """Pure-Python cosine similarity. Empty / length-mismatch / zero-norm → 0.0."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b, strict=True):
        fx, fy = float(x), float(y)
        dot += fx * fy
        norm_a += fx * fx
        norm_b += fy * fy
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


FIND_BLURRED_RELATIONS_CYPHER = """
MATCH ()-[r:Relation]->()
WHERE size(coalesce(r.witnesses_a, [])) > 1
  AND size(coalesce(r.witnesses_b, [])) > 1
RETURN coalesce(r.id, elementId(r)) AS rel_id,
       r.witnesses_a AS witnesses_a,
       r.witnesses_b AS witnesses_b
"""

MARK_BLURRED_RELATION_CYPHER = """
MATCH ()-[r:Relation]->()
WHERE coalesce(r.id, elementId(r)) = $rel_id
SET r.needs_reverify = true,
    r.witnesses_a = [],
    r.witnesses_b = []
"""

FIND_EQUIVALENT_CONCEPT_PAIRS_CYPHER = """
MATCH (a:Concept {promoted: true}), (b:Concept {promoted: true})
WHERE a.id < b.id
  AND a.kernel_category = b.kernel_category
  AND a.parent_uri = b.parent_uri
  AND a.parent_uri IS NOT NULL
  AND NOT (a)-[:EQUIVALENT_TO]-(b)
RETURN a.id AS id_a, a.embedding AS embedding_a,
       b.id AS id_b, b.embedding AS embedding_b
"""

MERGE_EQUIVALENT_TO_CYPHER = """
MATCH (a:Concept {id: $src_id}), (b:Concept {id: $dst_id})
MERGE (a)-[:EQUIVALENT_TO]->(b)
"""

MOVE_ABSORBED_MEMBER_OF_CYPHER = f"""
MATCH (n:Node)-[old:{_MEMBER_OF_REL}]->(absorbed:Concept {{id: $absorbed_id}})
DELETE old
WITH n, absorbed
MATCH (survivor:Concept {{id: $survivor_id}})
CREATE (n)-[:{_MEMBER_OF_REL} {{absorbed_from: $absorbed_id}}]->(survivor)
SET absorbed.absorbed_from = $survivor_id
"""

MARK_ABSORBED_CONCEPT_CYPHER = """
MATCH (absorbed:Concept {id: $absorbed_id})
SET absorbed.absorbed_from = $survivor_id
"""

MERGE_JUDGE_RUN_CYPHER = """
MERGE (j:JudgeRun {id: $id})
SET j.batch_id = $batch_id,
    j.timestamp = datetime(),
    j.anti_blur = $anti_blur,
    j.equivalent_to = $equivalent_to
"""


async def requeue_pair(witness_a: str, witness_b: str) -> None:
    """Default no-op requeue. Tests replace this. Does not persist S2 facts."""
    _ = (witness_a, witness_b)
    return None


def split_blurred_relation(rel: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Cartesian pairs of multi-witness lists. Does not invent nodes."""
    witnesses_a = [str(w) for w in (rel.get("witnesses_a") or []) if str(w).strip()]
    witnesses_b = [str(w) for w in (rel.get("witnesses_b") or []) if str(w).strip()]
    if len(witnesses_a) <= 1 or len(witnesses_b) <= 1:
        return []
    return [(wa, wb) for wa in witnesses_a for wb in witnesses_b]


def _embedding_list(raw: object) -> list[float] | None:
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        values = [float(x) for x in raw]
        return values or None
    return None


async def _log_judge_run(session: AsyncSession, job_id: str, stats: JudgeStats) -> None:
    await session.run(
        MERGE_JUDGE_RUN_CYPHER,
        id=job_id,
        batch_id=job_id,
        anti_blur=stats.anti_blur,
        equivalent_to=stats.equivalent_to,
    )


async def _task_anti_blur(
    session: AsyncSession,
    *,
    requeue: RequeuePair,
) -> int:
    result = await session.run(FIND_BLURRED_RELATIONS_CYPHER)
    blurred: list[dict[str, Any]] = [dict(record) async for record in result]
    count = 0
    for rel in blurred:
        pairs = split_blurred_relation(rel)
        if not pairs:
            continue
        await session.run(MARK_BLURRED_RELATION_CYPHER, rel_id=rel["rel_id"])
        for witness_a, witness_b in pairs:
            await requeue(witness_a, witness_b)
        count += 1
    return count


async def _task_equivalent_to(session: AsyncSession, threshold: float) -> int:
    result = await session.run(FIND_EQUIVALENT_CONCEPT_PAIRS_CYPHER)
    pairs: list[dict[str, Any]] = [dict(record) async for record in result]
    count = 0
    for row in pairs:
        emb_a = _embedding_list(row.get("embedding_a"))
        emb_b = _embedding_list(row.get("embedding_b"))
        if emb_a is None or emb_b is None:
            continue
        if cosine(emb_a, emb_b) < threshold:
            continue
        src_id = str(row["id_a"])
        dst_id = str(row["id_b"])
        await session.run(MERGE_EQUIVALENT_TO_CYPHER, src_id=src_id, dst_id=dst_id)
        await session.run(
            MOVE_ABSORBED_MEMBER_OF_CYPHER,
            absorbed_id=dst_id,
            survivor_id=src_id,
        )
        await session.run(
            MARK_ABSORBED_CONCEPT_CYPHER,
            absorbed_id=dst_id,
            survivor_id=src_id,
        )
        count += 1
    return count


async def _task_event_triage(
    session: AsyncSession,
    run_id: str,
    *,
    touched_ids: Sequence[str] | None = None,
) -> int:
    """Last judge task when ``ENABLE_EVENT_TRIAGE``. Thin wrapper, no Cypher."""
    from app.pipeline.event_triage import run_event_triage

    try:
        return await run_event_triage(session, run_id, touched_ids=touched_ids)
    except Exception:
        logger.exception("event_triage_failed run_id=%s", run_id)
        return 0


async def run_judge(
    session: AsyncSession,
    job_id: str,
    *,
    on_requeue: RequeuePair | None = None,
    touched_ids: Sequence[str] | None = None,
) -> JudgeStats:
    """Anti-blur, equivalent_to, optional event triage, then ``:JudgeRun``.

    ``touched_ids`` is kept for the event-triage call below (scopes which
    events are eligible for this batch). Event triage runs only if
    ``ENABLE_EVENT_TRIAGE`` (default off).
    """
    stats = JudgeStats()
    if not settings.ENABLE_JUDGE:
        await _log_judge_run(session, job_id, stats)
        return stats

    requeue = on_requeue if on_requeue is not None else requeue_pair
    threshold = float(settings.BACKBONE_COLLAPSE_THRESHOLD)

    stats.anti_blur = await _task_anti_blur(session, requeue=requeue)
    stats.equivalent_to = await _task_equivalent_to(session, threshold)
    if settings.ENABLE_EVENT_TRIAGE:
        await _task_event_triage(session, job_id, touched_ids=touched_ids)
    await _log_judge_run(session, job_id, stats)
    logger.info("judge_complete job_id=%s stats=%s", job_id, asdict(stats))
    return stats
