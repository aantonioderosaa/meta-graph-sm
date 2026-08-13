"""Dreaming pipeline orchestration (tech-spec §6, §7, §10, E4.9)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from neo4j import AsyncSession

from app.core import event_bus
from app.core.llm_client import LLMValidationError, get_token_usage
from app.core.neo4j_client import get_driver
from app.pipeline import (
    entity_relation_resolution,
    event_relation_resolution,
    node_ppr_projection,
    node_resolution,
    reconcile,
)

logger = logging.getLogger(__name__)

FIND_FRESH_ENTITIES_CYPHER = """
MATCH (n:Node {type:'entity', dreamed:false})
WHERE n.merged_into IS NULL
RETURN n.id AS id, n.name AS name, n.embedding AS embedding
"""

MARK_NODE_DREAMED_CYPHER = """
UNWIND $node_ids AS nid
MATCH (n:Node {id: nid})
SET n.dreamed = true
"""


@dataclass
class DreamingStats:
    node_drift_count: int = 0


async def _mark_nodes_dreamed(session: AsyncSession, node_ids: list[str]) -> None:
    unique = list(dict.fromkeys(node_ids))
    if not unique:
        return
    await session.run(MARK_NODE_DREAMED_CYPHER, node_ids=unique)


async def _resolve_fresh_entities(session: AsyncSession, job_id: str) -> set[str]:
    """Phase 1: dedup fresh entity nodes. Empty MATCH is a no-op."""
    result = await session.run(FIND_FRESH_ENTITIES_CYPHER)
    rows: list[tuple[str, str, list[float]]] = []
    async for record in result:
        embedding = record["embedding"]
        rows.append(
            (
                record["id"],
                record["name"],
                list(embedding) if embedding is not None else [],
            )
        )

    touched: set[str] = set()
    for node_id, name, embedding in rows:
        try:
            canon_id = await node_resolution.resolve_node(
                session, node_id, "entity", name, embedding, job_id
            )
        except LLMValidationError as exc:
            logger.exception("entity_resolution_failed node_id=%s validation_error", node_id)
            await event_bus.publish(
                job_id,
                "entity_resolution",
                "llm_call_failed",
                {"stage": "entity_resolution", "item_id": node_id, "error": str(exc)},
            )
            continue
        except Exception as exc:
            logger.exception("entity_resolution_failed node_id=%s", node_id)
            await event_bus.publish(
                job_id,
                "entity_resolution",
                "llm_call_failed",
                {"stage": "entity_resolution", "item_id": node_id, "error": str(exc)},
            )
            continue

        mark_ids = [node_id] if canon_id == node_id else [node_id, canon_id]
        await _mark_nodes_dreamed(session, mark_ids)
        if canon_id != node_id:
            await event_bus.publish(
                job_id,
                "entity_resolution",
                "node_merged",
                {"dup_id": node_id, "canon_id": canon_id},
            )
        touched.add(node_id)
        touched.add(canon_id)
    return touched


async def _classify_entity_relations(
    session: AsyncSession,
    job_id: str,
    touched_entity_ids: set[str],
) -> int:
    """Phase 2 branch: classify fresh entity↔entity Relations."""

    async def on_classified(label: str, src: str, tgt: str) -> None:
        await event_bus.publish(
            job_id,
            "entity_relation_classification",
            "node_relation_classified",
            {"type": label, "src": src, "tgt": tgt},
        )

    async def on_error(src: str, tgt: str, error: str) -> None:
        await event_bus.publish(
            job_id,
            "entity_relation_classification",
            "llm_call_failed",
            {
                "stage": "entity_relation_classification",
                "item_id": f"{src}:{tgt}",
                "error": error,
            },
        )

    try:
        return await entity_relation_resolution.resolve_fresh_entity_relations(
            session,
            job_id,
            touched_entity_ids,
            on_classified=on_classified,
            on_error=on_error,
        )
    except Exception as exc:
        logger.exception("entity_relation_classification_failed")
        await event_bus.publish(
            job_id,
            "entity_relation_classification",
            "llm_call_failed",
            {
                "stage": "entity_relation_classification",
                "item_id": job_id,
                "error": str(exc),
            },
        )
        return 0


async def _resolve_and_classify_events(session: AsyncSession, job_id: str) -> set[str]:
    """Phase 2 branch: event dedup + classification (Macrotask 4.2), then mark dreamed."""
    result = await session.run(event_relation_resolution.FIND_FRESH_EVENTS_CYPHER)
    rows: list[tuple[str, str, list[float]]] = []
    async for record in result:
        embedding = record["embedding"]
        rows.append(
            (
                record["id"],
                record["name"],
                list(embedding) if embedding is not None else [],
            )
        )

    touched: set[str] = set()
    for event_id, name, embedding in rows:
        try:
            canon_id = await event_relation_resolution.resolve_event(
                session, event_id, name, embedding, job_id
            )
        except LLMValidationError as exc:
            logger.exception("event_resolution_failed event_id=%s validation_error", event_id)
            await event_bus.publish(
                job_id,
                "event_resolution_and_classification",
                "llm_call_failed",
                {
                    "stage": "event_resolution_and_classification",
                    "item_id": event_id,
                    "error": str(exc),
                },
            )
            continue
        except Exception as exc:
            logger.exception("event_resolution_failed event_id=%s", event_id)
            await event_bus.publish(
                job_id,
                "event_resolution_and_classification",
                "llm_call_failed",
                {
                    "stage": "event_resolution_and_classification",
                    "item_id": event_id,
                    "error": str(exc),
                },
            )
            continue

        mark_ids = [event_id] if canon_id == event_id else [event_id, canon_id]
        await _mark_nodes_dreamed(session, mark_ids)
        if canon_id != event_id:
            await event_bus.publish(
                job_id,
                "event_resolution_and_classification",
                "node_merged",
                {"dup_id": event_id, "canon_id": canon_id},
            )
        touched.add(event_id)
        touched.add(canon_id)
    return touched


async def _run_node_phases(driver, job_id: str) -> set[str]:
    """Entity resolution, then relation + event branches in parallel (two sessions)."""
    async with driver.session() as entity_session:
        node_touched = await _resolve_fresh_entities(entity_session, job_id)

    async with driver.session() as rel_session, driver.session() as event_session:
        _, event_touched = await asyncio.gather(
            _classify_entity_relations(rel_session, job_id, node_touched),
            _resolve_and_classify_events(event_session, job_id),
        )
        node_touched |= event_touched
    return node_touched


async def run_dreaming_pipeline(job_id: str, doc_id: str | None = None) -> DreamingStats:
    """Run entity resolution → entity-relation/event resolution → GDS refresh."""
    _ = doc_id  # no longer used for Fact scoping
    stats = DreamingStats()
    driver = get_driver()

    node_touched = await _run_node_phases(driver, job_id)
    node_drift = await reconcile.reconcile_scoped_relations(list(node_touched))
    stats.node_drift_count = node_drift

    async with driver.session() as session:
        await node_ppr_projection.refresh_ppr_projection(session)

    await event_bus.publish(
        job_id, "reconciliation", "drift_check", {"drift_count": node_drift},
    )
    tokens = get_token_usage(job_id)
    payload_stats = {"node_drift_count": node_drift}
    if tokens:
        payload_stats["tokens"] = tokens
    await event_bus.publish(job_id, "done", "pipeline_complete", {"stats": payload_stats})
    return stats
