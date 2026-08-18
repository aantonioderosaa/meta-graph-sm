"""Idempotent backfill of ``kernel_category`` on existing ``:Node`` (Fase 13.1).

Cold-runnable on a populated KB. Nodes that already have ``kernel_category``
are skipped; merged nodes (``merged_into IS NOT NULL``) are skipped. Nothing
is deleted. Reads Neo4j from ``Settings`` (same as the app).

Usage (from backend/):
  python scripts/backfill_kernel_category.py --dry-run
  python scripts/backfill_kernel_category.py --limit 100
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Allow `python scripts/backfill_kernel_category.py` from the backend/ directory.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from neo4j import AsyncGraphDatabase, AsyncSession  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.llm_client import call_structured  # noqa: E402
from app.models.kernel import EntityKernelType  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 50
JOB_ID = "backfill-kernel-category"

FETCH_UNCLASSIFIED_CYPHER = """
MATCH (n:Node)
WHERE n.kernel_category IS NULL AND n.merged_into IS NULL
RETURN n.id AS id, n.name AS name, n.summary AS summary, n.type AS type
ORDER BY n.id
LIMIT $limit
"""

SET_KERNEL_CATEGORY_CYPHER = """
MATCH (n:Node {id: $id})
WHERE n.kernel_category IS NULL AND n.merged_into IS NULL
SET n.kernel_category = $value
RETURN n.id AS id
"""

_ENTITY_KERNEL_LINES = "\n".join(f"- {member.value}" for member in EntityKernelType)

KERNEL_BACKFILL_SYSTEM_PROMPT = (
    "Sei un assistente che risponde sempre con un oggetto JSON valido, senza spiegazioni."
)

KERNEL_BACKFILL_USER_PROMPT_TEMPLATE = (
    "Classifica questa entità già ingerita in esattamente una categoria fondazionale "
    "E1–E8. Categorie ammesse:\n"
    "{entity_kernel_list}\n"
    "Non combinare categorie. Restituisci un oggetto JSON: "
    '{{"kernel_category": "..."}}.\n\n'
    "Nome: {name}\n"
    "Summary: {summary}\n"
    "Tipo (se presente): {node_type}\n"
)


class KernelCategoryAssignment(BaseModel):
    kernel_category: EntityKernelType


ClassifyFn = Callable[..., Awaitable[EntityKernelType]]


@dataclass
class BackfillStats:
    fetched: int = 0
    classified: int = 0
    written: int = 0


def build_backfill_prompt(name: str, summary: str, node_type: str | None) -> tuple[str, str]:
    user = KERNEL_BACKFILL_USER_PROMPT_TEMPLATE.format(
        entity_kernel_list=_ENTITY_KERNEL_LINES,
        name=name or "",
        summary=summary or "",
        node_type=node_type or "",
    )
    return KERNEL_BACKFILL_SYSTEM_PROMPT, user


async def classify_node_kernel_category(
    *,
    name: str,
    summary: str,
    node_type: str | None = None,
    job_id: str | None = JOB_ID,
) -> EntityKernelType:
    """LLM classify one node; returns the enum (``.value`` is e.g. ``Evento``, not ``E4``)."""
    system_prompt, user_prompt = build_backfill_prompt(name, summary, node_type)
    assignment = await call_structured(
        system_prompt,
        user_prompt,
        KernelCategoryAssignment,
        temperature=0,
        job_id=job_id,
    )
    return assignment.kernel_category


async def fetch_unclassified_batch(
    session: AsyncSession,
    batch_size: int,
) -> list[dict[str, Any]]:
    result = await session.run(FETCH_UNCLASSIFIED_CYPHER, limit=batch_size)
    rows: list[dict[str, Any]] = []
    async for record in result:
        rows.append(
            {
                "id": record["id"],
                "name": record.get("name") or "",
                "summary": record.get("summary") or "",
                "type": record.get("type"),
            }
        )
    return rows


async def set_kernel_category(session: AsyncSession, node_id: str, value: str) -> None:
    await session.run(SET_KERNEL_CATEGORY_CYPHER, id=node_id, value=value)


async def backfill_kernel_categories(
    session: AsyncSession,
    *,
    dry_run: bool = False,
    limit: int | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    classify: ClassifyFn | None = None,
) -> BackfillStats:
    """Batch-classify unclassified, unmerged ``:Node``. Idempotent; dry-run skips SET."""
    classify_fn = classify or classify_node_kernel_category
    stats = BackfillStats()
    remaining = limit
    while True:
        fetch_limit = batch_size if remaining is None else min(batch_size, remaining)
        if fetch_limit <= 0:
            break
        nodes = await fetch_unclassified_batch(session, fetch_limit)
        if not nodes:
            break
        stats.fetched += len(nodes)
        for node in nodes:
            category = await classify_fn(
                name=node["name"],
                summary=node["summary"],
                node_type=node.get("type"),
            )
            value = category.value if isinstance(category, EntityKernelType) else str(category)
            stats.classified += 1
            if dry_run:
                logger.info("[dry-run] %s -> %s", node["id"], value)
            else:
                await set_kernel_category(session, node["id"], value)
                stats.written += 1
        if remaining is not None:
            remaining -= len(nodes)
        if len(nodes) < fetch_limit:
            break
    return stats


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill kernel_category on :Node that lack it (idempotent)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Classify but do not SET kernel_category.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of unclassified nodes to process.",
    )
    return parser.parse_args(argv)


async def run_backfill(*, dry_run: bool, limit: int | None) -> BackfillStats:
    driver = AsyncGraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
    )
    try:
        async with driver.session() as session:
            return await backfill_kernel_categories(
                session, dry_run=dry_run, limit=limit
            )
    finally:
        await driver.close()


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    stats = asyncio.run(run_backfill(dry_run=args.dry_run, limit=args.limit))
    print(
        f"backfill kernel_category: fetched={stats.fetched} "
        f"classified={stats.classified} written={stats.written} "
        f"dry_run={args.dry_run}"
    )


if __name__ == "__main__":
    main()
