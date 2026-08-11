"""Dreaming pipeline orchestration (tech-spec §6, §7, §10, E4.9)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from neo4j import AsyncSession

from app.core import event_bus
from app.core.config import settings
from app.core.llm_client import LLMValidationError, get_token_usage
from app.core.neo4j_client import get_driver
from app.models.consolidation import ConsolidationOutcome, ConsolidationResult
from app.pipeline import consolidation, embeddings, grouping, reconcile, relations

logger = logging.getLogger(__name__)

CREATE_ABSTRACTION_CYPHER = """
CREATE (d:Fact {
  id: $did,
  text: $text,
  type: $type,
  is_latest: true,
  confidence: 1.0,
  source_doc_id: $doc_id,
  embedding: $emb,
  created_at: datetime(),
  dreamed: true
})
WITH d
UNWIND $source_ids AS sid
MATCH (s:Fact {id: sid})
CREATE (d)-[:DERIVES]->(s)
WITH d, s
MATCH (s)-[:DERIVED_FROM]->(ch:Chunk)
MERGE (d)-[:DERIVED_FROM]->(ch)
"""

CREATE_CLEANED_FACT_CYPHER = """
CREATE (f:Fact {
  id: $fid,
  text: $text,
  type: $type,
  is_latest: true,
  confidence: 1.0,
  dreamed: false,
  source_doc_id: $doc_id,
  embedding: $emb,
  created_at: datetime()
})
"""

LOAD_FACTS_CYPHER = """
MATCH (f:Fact {id: $fact_id})
RETURN f.id AS id, f.text AS text, f.type AS type,
       f.source_doc_id AS source_doc_id, f.embedding AS embedding
"""

MARK_DREAMED_CYPHER = """
UNWIND $fact_ids AS fid
MATCH (f:Fact {id: fid})
SET f.dreamed = true
"""


@dataclass
class NewFactForRelations:
    fact_id: str
    text: str
    embedding: list[float]
    source_fact_ids: list[str] = field(default_factory=list)


@dataclass
class DreamingStats:
    groups: int = 0
    edges_created: int = 0
    drift_count: int = 0
    facts_processed: int = 0


async def _load_fact(session: AsyncSession, fact_id: str) -> dict | None:
    result = await session.run(LOAD_FACTS_CYPHER, fact_id=fact_id)
    record = await result.single()
    if record is None:
        return None
    return {
        "id": record["id"],
        "text": record["text"],
        "type": record["type"],
        "source_doc_id": record["source_doc_id"],
        "embedding": list(record["embedding"]),
    }


async def _load_group_facts(
    session: AsyncSession,
    fact_ids: list[str],
) -> list[tuple[str, str]]:
    loaded: list[tuple[str, str]] = []
    for fact_id in fact_ids:
        fact = await _load_fact(session, fact_id)
        if fact is not None:
            loaded.append((fact["id"], fact["text"]))
    return loaded


async def _write_abstraction(
    session: AsyncSession,
    result: ConsolidationResult,
    source_doc_id: str,
    job_id: str,
) -> NewFactForRelations:
    fact_id = str(uuid.uuid4())
    embedding = embeddings.embed(result.text)
    await session.run(
        CREATE_ABSTRACTION_CYPHER,
        did=fact_id,
        text=result.text,
        type=result.type.value,
        doc_id=source_doc_id,
        emb=embedding,
        source_ids=result.source_fact_ids,
    )
    await event_bus.publish(
        job_id,
        "consolidation",
        "fact_derived",
        {"fact_id": fact_id, "source_fact_ids": result.source_fact_ids},
    )
    return NewFactForRelations(
        fact_id=fact_id,
        text=result.text,
        embedding=embedding,
        source_fact_ids=list(result.source_fact_ids),
    )


async def _write_cleaned_fact(
    session: AsyncSession,
    result: ConsolidationResult,
    source_doc_id: str,
) -> NewFactForRelations:
    fact_id = str(uuid.uuid4())
    embedding = embeddings.embed(result.text)
    await session.run(
        CREATE_CLEANED_FACT_CYPHER,
        fid=fact_id,
        text=result.text,
        type=result.type.value,
        doc_id=source_doc_id,
        emb=embedding,
    )
    return NewFactForRelations(
        fact_id=fact_id,
        text=result.text,
        embedding=embedding,
    )


async def _add_facts_as_individual_candidates(
    session: AsyncSession,
    fact_ids: list[str],
    new_facts: list[NewFactForRelations],
    successfully_processed_fact_ids: set[str],
) -> None:
    """Load each fact and queue it for relation detection on its own — used both
    for singleton groups and, while ENABLE_DERIVES=False, for grouped facts that
    would otherwise have been collapsed into an abstraction."""
    for fact_id in fact_ids:
        fact = await _load_fact(session, fact_id)
        if fact is None:
            continue
        new_facts.append(
            NewFactForRelations(
                fact_id=fact["id"],
                text=fact["text"],
                embedding=fact["embedding"],
            )
        )
        successfully_processed_fact_ids.add(fact_id)


async def _process_consolidation_group(
    session: AsyncSession,
    *,
    component_id: int,
    fact_ids: list[str],
    job_id: str,
    stats: DreamingStats,
    new_facts: list[NewFactForRelations],
    failed_fact_ids: set[str],
    successfully_processed_fact_ids: set[str],
) -> None:
    await event_bus.publish(
        job_id,
        "grouping",
        "group_formed",
        {"component_id": component_id, "fact_ids": fact_ids},
    )
    stats.groups += 1

    group_facts = await _load_group_facts(session, fact_ids)
    if not group_facts:
        return

    try:
        result = await consolidation.consolidate_group(group_facts, job_id=job_id)
    except LLMValidationError as exc:
        logger.exception("consolidation_failed component_id=%s validation_error", component_id)
        await event_bus.publish(
            job_id,
            "consolidation",
            "llm_call_failed",
            {"stage": "consolidation", "item_id": str(component_id), "error": str(exc)},
        )
        failed_fact_ids.update(fact_ids)
        return
    except Exception as exc:
        logger.exception("consolidation_failed component_id=%s", component_id)
        await event_bus.publish(
            job_id,
            "consolidation",
            "llm_call_failed",
            {"stage": "consolidation", "item_id": str(component_id), "error": str(exc)},
        )
        failed_fact_ids.update(fact_ids)
        return

    source_doc_id = await _resolve_source_doc_id(session, fact_ids)
    if result.outcome == ConsolidationOutcome.abstraction:
        if not settings.ENABLE_DERIVES:
            # derives disabled (default): don't collapse the group into an
            # abstraction — evaluate each original fact individually for
            # updates/extends instead. Re-enable via settings/.env (see
            # milestone1-tech-spec discussion) once derives semantics are reworked.
            await _add_facts_as_individual_candidates(
                session, fact_ids, new_facts, successfully_processed_fact_ids
            )
            return
        new_fact = await _write_abstraction(session, result, source_doc_id, job_id)
        new_facts.append(new_fact)
        successfully_processed_fact_ids.update(fact_ids)
        return

    new_fact = await _write_cleaned_fact(session, result, source_doc_id)
    new_facts.append(new_fact)
    successfully_processed_fact_ids.update(fact_ids)


async def _resolve_source_doc_id(session: AsyncSession, fact_ids: list[str]) -> str:
    for fact_id in fact_ids:
        fact = await _load_fact(session, fact_id)
        if fact is not None and fact["source_doc_id"]:
            return fact["source_doc_id"]
    return ""


async def _process_relation_detection(
    session: AsyncSession,
    *,
    new_fact: NewFactForRelations,
    job_id: str,
    stats: DreamingStats,
) -> None:
    candidates = await relations.find_candidates(
        session,
        new_fact.fact_id,
        new_fact.embedding,
    )
    for candidate in candidates:
        try:
            classification = await relations.classify_relation(
                new_fact.text,
                candidate.text,
                job_id=job_id,
            )
        except LLMValidationError as exc:
            logger.exception(
                "relation_classification_failed n=%s v=%s validation_error",
                new_fact.fact_id,
                candidate.id,
            )
            await event_bus.publish(
                job_id,
                "relation_detection",
                "llm_call_failed",
                {
                    "stage": "relation_detection",
                    "item_id": f"{new_fact.fact_id}:{candidate.id}",
                    "error": str(exc),
                },
            )
            continue
        except Exception as exc:
            logger.exception(
                "relation_classification_failed n=%s v=%s",
                new_fact.fact_id,
                candidate.id,
            )
            await event_bus.publish(
                job_id,
                "relation_detection",
                "llm_call_failed",
                {
                    "stage": "relation_detection",
                    "item_id": f"{new_fact.fact_id}:{candidate.id}",
                    "error": str(exc),
                },
            )
            continue

        wrote = await relations.apply_relation(
            session,
            n_id=new_fact.fact_id,
            v_id=candidate.id,
            relation=classification.relation,
            job_id=job_id,
        )
        if wrote:
            stats.edges_created += 1


async def run_dreaming_pipeline(job_id: str, doc_id: str | None = None) -> DreamingStats:
    """Run grouping → consolidation → relation detection → reconciliation."""
    stats = DreamingStats()
    new_facts: list[NewFactForRelations] = []
    failed_fact_ids: set[str] = set()
    successfully_processed_fact_ids: set[str] = set()

    groups = await grouping.group_fresh_facts(doc_id)
    driver = get_driver()

    async with driver.session() as session:
        component_id = 0
        for group in groups:
            # Consolidation always runs for multi-member groups (grouping + cleaned_fact
            # dedup are independent of ENABLE_DERIVES); the flag only gates whether an
            # "abstraction" outcome is allowed to collapse the group into a DERIVES fact
            # (see _process_consolidation_group).
            if len(group) >= 2:
                await _process_consolidation_group(
                    session,
                    component_id=component_id,
                    fact_ids=group,
                    job_id=job_id,
                    stats=stats,
                    new_facts=new_facts,
                    failed_fact_ids=failed_fact_ids,
                    successfully_processed_fact_ids=successfully_processed_fact_ids,
                )
                component_id += 1
            else:
                await _add_facts_as_individual_candidates(
                    session, group, new_facts, successfully_processed_fact_ids
                )

        for new_fact in new_facts:
            await _process_relation_detection(
                session,
                new_fact=new_fact,
                job_id=job_id,
                stats=stats,
            )
            successfully_processed_fact_ids.add(new_fact.fact_id)
            stats.facts_processed += 1

        mark_ids = [
            fid
            for fid in successfully_processed_fact_ids
            if fid not in failed_fact_ids
        ]
        if mark_ids:
            await session.run(MARK_DREAMED_CYPHER, fact_ids=mark_ids)

    drift_count = await reconcile.reconcile()
    stats.drift_count = drift_count
    await event_bus.publish(
        job_id,
        "reconciliation",
        "drift_check",
        {"drift_count": drift_count},
    )

    tokens = get_token_usage(job_id)
    payload_stats: dict[str, int] = {
        "groups": stats.groups,
        "edges_created": stats.edges_created,
        "drift_count": drift_count,
        "facts_processed": stats.facts_processed,
    }
    if tokens:
        payload_stats["tokens"] = tokens

    await event_bus.publish(
        job_id,
        "done",
        "pipeline_complete",
        {"stats": payload_stats},
    )
    return stats
