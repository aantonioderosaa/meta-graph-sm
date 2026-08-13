"""is_latest reconciliation for :Relation (Node layer)."""

from __future__ import annotations

from app.core.neo4j_client import get_driver

# `updates` is a property on :Relation, not a separate UPDATES edge. For each
# directed pair that has at least one updates rel, the newest created_at is
# is_latest=true and older rels on that pair are is_latest=false.
RECONCILE_SCOPED_RELATIONS_CYPHER = """
MATCH (a:Node)-[r:Relation]->(b:Node)
WHERE a.id IN $node_ids OR b.id IN $node_ids
WITH a, b, collect(r) AS rels
WHERE any(rel IN rels WHERE rel.normalized_relation = 'updates')
UNWIND rels AS r
WITH a, b, r
ORDER BY r.created_at DESC
WITH a, b, collect(r) AS ordered
UNWIND range(0, size(ordered) - 1) AS idx
WITH ordered[idx] AS r, (idx = 0) AS should_be_latest
WHERE r.is_latest <> should_be_latest
SET r.is_latest = should_be_latest
RETURN count(r) AS driftCount
"""


async def reconcile_scoped_relations(node_ids: list[str]) -> int:
    """Recompute is_latest on :Relation pairs that include an `updates` rel.

    Scoped to directed pairs where either endpoint is in ``node_ids``. Empty
    ``node_ids`` is a no-op (no session).
    """
    if not node_ids:
        return 0
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run(RECONCILE_SCOPED_RELATIONS_CYPHER, node_ids=node_ids)
        record = await result.single()
        if record is None:
            return 0
        return int(record["driftCount"])
