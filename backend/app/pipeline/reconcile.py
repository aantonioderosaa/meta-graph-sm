"""is_latest reconciliation (tech-spec §7, E4.6)."""

from __future__ import annotations

from app.core.neo4j_client import get_driver

RECONCILE_CYPHER = """
MATCH (f:Fact)
WITH f, NOT EXISTS { ()-[:UPDATES]->(f) } AS correct
WHERE f.is_latest <> correct
SET f.is_latest = correct
RETURN count(f) AS driftCount
"""

RECONCILE_SCOPED_CYPHER = """
MATCH (f:Fact) WHERE f.id IN $fact_ids
WITH f, NOT EXISTS { ()-[:UPDATES]->(f) } AS correct
WHERE f.is_latest <> correct
SET f.is_latest = correct
RETURN count(f) AS driftCount
"""


async def reconcile() -> int:
    """Reconcile is_latest flags across the full KB; return corrected fact count."""
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run(RECONCILE_CYPHER)
        record = await result.single()
        if record is None:
            return 0
        return int(record["driftCount"])


async def reconcile_scoped(fact_ids: list[str]) -> int:
    """Recompute is_latest only for the given facts (dreaming-cycle canary,
    bounded by this cycle's work). See reconcile() for the full-KB variant,
    used on-demand via POST /reconcile."""
    if not fact_ids:
        return 0
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run(RECONCILE_SCOPED_CYPHER, fact_ids=fact_ids)
        record = await result.single()
        if record is None:
            return 0
        return int(record["driftCount"])
