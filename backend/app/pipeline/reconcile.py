"""is_latest reconciliation for :Relation (Node layer)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from neo4j import AsyncSession

from app.core.neo4j_client import get_driver
from app.models.kernel import AttributeKernelType

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


# Attribute LWW (Macrotask 2): a new value is a new tail, so grouping by the
# directed pair (head, tail) never sees two successive values. Scope is
# (head_id, kernel_parent), equivalently slot_id (attributes ignore tail).
# The previous value stays on the graph: older edges flip is_latest=false and
# the newer edge gets an additive ``updates`` property pointing at the previous
# tail. ``normalized_relation`` / ``kernel_parent`` are not overwritten.
_ATTRIBUTE_KERNEL_VALUES = tuple(member.value for member in AttributeKernelType)

FIND_ATTRIBUTE_SLOT_EDGES_CYPHER = """
MATCH (h:Node)-[r:Relation]->(t:Node)
WHERE h.id IN $head_ids
  AND r.kernel_parent IN $attribute_kernels
  AND ($slot_id IS NULL OR r.slot_id = $slot_id)
RETURN h.id AS head_id,
       t.id AS tail_id,
       r.slot_id AS slot_id,
       r.kernel_parent AS kernel_parent,
       r.is_latest AS is_latest,
       r.created_at AS created_at,
       r.updates AS updates
"""

SET_ATTRIBUTE_SLOT_IS_LATEST_CYPHER = """
MATCH (h:Node {id: $head_id})-[r:Relation]->(t:Node {id: $tail_id})
WHERE r.kernel_parent = $kernel_parent
  AND ($slot_id IS NULL OR r.slot_id = $slot_id)
SET r.is_latest = $is_latest
"""

SET_ATTRIBUTE_SLOT_UPDATES_CYPHER = """
MATCH (h:Node {id: $head_id})-[r:Relation]->(t:Node {id: $tail_id})
WHERE r.kernel_parent = $kernel_parent
  AND ($slot_id IS NULL OR r.slot_id = $slot_id)
SET r.updates = $updates
"""


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, TypeError):
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return bool(value)


def _attribute_slot_group_key(row: Any) -> tuple[Any, ...]:
    sid = _row_get(row, "slot_id")
    if sid:
        return ("slot", str(sid))
    return (
        "hk",
        str(_row_get(row, "head_id") or ""),
        str(_row_get(row, "kernel_parent") or ""),
    )


def _newest_first(rows: list[Any]) -> list[Any]:
    indexed = list(enumerate(rows))

    def sort_key(item: tuple[int, Any]) -> tuple[Any, ...]:
        idx, row = item
        ts = _row_get(row, "created_at")
        return (ts is not None, ts, idx)

    indexed.sort(key=sort_key, reverse=True)
    return [row for _, row in indexed]


async def _fetch_all(result: Any) -> list[Any]:
    rows: list[Any] = []
    async for record in result:
        rows.append(record)
    return rows


async def reconcile_scoped_attribute_slots(
    session: AsyncSession,
    *,
    head_ids: list[str],
    slot_id: str | None = None,
) -> int:
    """Last-write-wins for AttributeKernelType slots on the given heads.

    Groups by ``slot_id`` when present, else ``(head_id, kernel_parent)``.
    No-op when ``head_ids`` is empty or a group has at most one ``is_latest``
    edge. Never removes edges; never writes ``normalized_relation='updates'``.
    """
    if not head_ids:
        return 0
    result = await session.run(
        FIND_ATTRIBUTE_SLOT_EDGES_CYPHER,
        head_ids=list(head_ids),
        attribute_kernels=list(_ATTRIBUTE_KERNEL_VALUES),
        slot_id=slot_id,
    )
    rows = await _fetch_all(result)
    groups: dict[tuple[Any, ...], list[Any]] = defaultdict(list)
    for row in rows:
        groups[_attribute_slot_group_key(row)].append(row)

    drift = 0
    for group_rows in groups.values():
        latest_count = sum(
            1 for row in group_rows if _as_bool(_row_get(row, "is_latest"), True)
        )
        if latest_count <= 1:
            continue
        ordered = _newest_first(group_rows)
        for idx, row in enumerate(ordered):
            should_be_latest = idx == 0
            prev_tail = (
                str(_row_get(ordered[idx + 1], "tail_id") or "")
                if idx + 1 < len(ordered)
                else ""
            )
            head_id = str(_row_get(row, "head_id") or "")
            tail_id = str(_row_get(row, "tail_id") or "")
            kernel_parent = str(_row_get(row, "kernel_parent") or "")
            row_slot = _row_get(row, "slot_id")
            match_slot = slot_id if slot_id is not None else row_slot
            if _as_bool(_row_get(row, "is_latest"), True) != should_be_latest:
                await session.run(
                    SET_ATTRIBUTE_SLOT_IS_LATEST_CYPHER,
                    head_id=head_id,
                    tail_id=tail_id,
                    kernel_parent=kernel_parent,
                    slot_id=match_slot,
                    is_latest=should_be_latest,
                )
                drift += 1
            if prev_tail and _row_get(row, "updates") != prev_tail:
                await session.run(
                    SET_ATTRIBUTE_SLOT_UPDATES_CYPHER,
                    head_id=head_id,
                    tail_id=tail_id,
                    kernel_parent=kernel_parent,
                    slot_id=match_slot,
                    updates=prev_tail,
                )
    return drift
