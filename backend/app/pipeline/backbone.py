"""Dreaming stage: classify entities onto the Concept TBox (Fase 4).

Calls ``concepts.py`` for hash, two-level match, catch-all MERGE, ``MEMBER_OF``,
and ``IS_A``. Does not implement ``PROMOTE``.
"""

from __future__ import annotations

import logging

from neo4j import AsyncSession

from app.core import event_bus
from app.core.config import settings
from app.pipeline.concepts import (
    FIND_UNCLASSIFIED_NODES_CYPHER,
    assign_entity_home,
    ensure_kernel_catch_all,
    merge_member_of,
    parse_kernel_category,
    read_corpus_summary,
)

logger = logging.getLogger(__name__)

STAGE = "backbone_classification"


async def classify_and_grow_backbone(session: AsyncSession, job_id: str) -> int:
    """Assign exactly one ``MEMBER_OF`` per unclassified entity/event. Return writes.

    No-op when ``ENABLE_KERNEL_CLASSIFICATION`` is False. Nodes that already have
    ``MEMBER_OF`` are skipped (F4.7). Missing ``kernel_category`` is logged and
    skipped — catch-all assignment is impossible without a category.
    """
    if not settings.ENABLE_KERNEL_CLASSIFICATION:
        return 0

    corpus_summary = await read_corpus_summary(session)
    result = await session.run(FIND_UNCLASSIFIED_NODES_CYPHER)
    rows: list[dict] = []
    async for record in result:
        rows.append(
            {
                "id": record["id"],
                "name": record["name"] or "",
                "summary": record["summary"] or "",
                "kernel_category": record["kernel_category"],
                "type": record["type"],
            }
        )

    written = 0
    for row in rows:
        node_id = row["id"]
        try:
            concept_id = await assign_entity_home(
                session,
                node_id=node_id,
                name=row["name"],
                summary=row["summary"],
                kernel_category=row["kernel_category"],
                corpus_summary=corpus_summary,
            )
        except Exception as exc:
            logger.exception("backbone_classification_failed node_id=%s", node_id)
            await event_bus.publish(
                job_id,
                STAGE,
                "llm_call_failed",
                {"stage": STAGE, "item_id": node_id, "error": str(exc)},
            )
            concept_id = await _fallback_catchall(
                session, node_id, row["name"], row["kernel_category"]
            )

        if concept_id is None:
            continue
        written += 1
        await event_bus.publish(
            job_id,
            STAGE,
            "backbone_member_assigned",
            {
                "node_id": node_id,
                "concept_id": concept_id,
                "kernel_category": (
                    row["kernel_category"].value
                    if hasattr(row["kernel_category"], "value")
                    else row["kernel_category"]
                ),
            },
        )
    return written


async def _fallback_catchall(
    session: AsyncSession,
    node_id: str,
    name: str,
    kernel_category: object,
) -> str | None:
    """Keep the containment tree non-orphan when matching/embed fails."""
    category = parse_kernel_category(
        kernel_category.value if hasattr(kernel_category, "value") else kernel_category
    )
    if category is None:
        logger.warning(
            "UnanchoredCandidate skip after error: no kernel_category node_id=%s name=%s",
            node_id,
            name,
        )
        return None
    try:
        catch_all_id = await ensure_kernel_catch_all(session, category)
        await merge_member_of(session, node_id, catch_all_id)
        return catch_all_id
    except Exception:
        logger.exception("backbone_catchall_fallback_failed node_id=%s", node_id)
        return None
