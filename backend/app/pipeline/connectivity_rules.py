"""Strato 1 — ConnectivityRule deposit (Fase 7).

The unique creation channel for ``:ConnectivityRule``. Called as a side effect of
asserted ``:Relation`` writes. Never seeded by an admin API.

Generalization walks ``IS_A`` a bounded number of hops and **stops before** the
kernel catch-all (``compute_hash_id("kernel:{Category}")``). Trivial
``kernel:Agente —rel→ kernel:Agente`` rules are never emitted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from neo4j import AsyncSession

from app.core.config import settings
from app.db.schema import BACKBONE_REL_TYPES, FAMIGLIA_B_REL_TYPES
from app.models.kernel import IS_A, MEMBER_OF, EntityKernelType
from app.pipeline.concepts import kernel_catch_all_concept_id

_ISA_REL = IS_A.upper()
_MEMBER_OF_REL = MEMBER_OF.upper()
_HAS_CONCEPT = "HAS_CONCEPT"

READ_NODE_TYPE_TOKEN_CYPHER = f"""
MATCH (n:Node {{id: $node_id}})
OPTIONAL MATCH (n)-[:{_MEMBER_OF_REL}]->(c:Concept)
RETURN n.kernel_category AS kernel_category,
       c.id AS concept_id,
       c.name AS concept_name
LIMIT 1
"""

READ_CONCEPT_ANCESTORS_CYPHER = f"""
MATCH (c:Concept {{id: $concept_id}})
OPTIONAL MATCH path = (c)-[:{_ISA_REL}*1..8]->(anc:Concept)
WHERE path IS NOT NULL
RETURN anc.id AS id, anc.name AS name, length(path) AS hops
"""

MERGE_CONNECTIVITY_RULE_CYPHER = """
MERGE (r:ConnectivityRule {
  source_category: $source_category,
  relation_type: $relation_type,
  target_category: $target_category
})
ON CREATE SET
  r.origin_fact_ids = [$origin_id],
  r.generalization_level = $generalization_level
ON MATCH SET
  r.origin_fact_ids = CASE
    WHEN r.origin_fact_ids IS NULL THEN [$origin_id]
    WHEN $origin_id IN r.origin_fact_ids THEN r.origin_fact_ids
    ELSE r.origin_fact_ids + [$origin_id]
  END
"""


def kernel_catch_all_ids() -> frozenset[str]:
    return frozenset(kernel_catch_all_concept_id(cat) for cat in EntityKernelType)


def is_structural_relation_type(relation_type: str | None) -> bool:
    """Famiglia B / backbone / HAS_CONCEPT — not an S1 affordance."""
    if not relation_type or not str(relation_type).strip():
        return True
    raw = str(relation_type).strip()
    upper, lower = raw.upper(), raw.lower()
    if upper in FAMIGLIA_B_REL_TYPES or lower in {m.lower() for m in FAMIGLIA_B_REL_TYPES}:
        return True
    if upper in BACKBONE_REL_TYPES or lower in {IS_A, MEMBER_OF}:
        return True
    return upper == _HAS_CONCEPT or lower == _HAS_CONCEPT.lower()


def type_token_from_row(row: dict[str, Any] | None) -> str | None:
    """Prefer MEMBER_OF Concept id (or name); else ``kernel_category``."""
    if not row:
        return None
    concept_id = row.get("concept_id")
    if concept_id:
        return str(concept_id)
    concept_name = row.get("concept_name")
    if concept_name:
        return str(concept_name)
    kernel_category = row.get("kernel_category")
    if kernel_category:
        return str(kernel_category)
    return None


@dataclass(frozen=True)
class _TypeInfo:
    token: str
    concept_id: str | None
    from_member_of: bool


@dataclass(frozen=True)
class _Ancestor:
    id: str
    name: str | None
    hops: int


def _row(record: Any) -> dict[str, Any]:
    if record is None:
        return {}
    if isinstance(record, dict):
        return record
    try:
        return dict(record)
    except Exception:
        return {}


async def _result_single(result: Any) -> dict[str, Any] | None:
    if result is None:
        return None
    single = getattr(result, "single", None)
    if callable(single):
        row = await single()
        return _row(row) if row is not None else None
    aiter = getattr(result, "__aiter__", None)
    if aiter is None:
        return None
    async for record in result:
        return _row(record)
    return None


async def _result_records(result: Any) -> list[dict[str, Any]]:
    if result is None:
        return []
    rows: list[dict[str, Any]] = []
    aiter = getattr(result, "__aiter__", None)
    if aiter is None:
        single = getattr(result, "single", None)
        if callable(single):
            row = await single()
            return [_row(row)] if row is not None else []
        return []
    async for record in result:
        rows.append(_row(record))
    return rows


def _both_kernel_catch_alls(source: str, target: str) -> bool:
    catch = kernel_catch_all_ids()
    return source in catch and target in catch


async def _read_type_info(session: AsyncSession, node_id: str) -> _TypeInfo | None:
    result = await session.run(READ_NODE_TYPE_TOKEN_CYPHER, node_id=node_id)
    row = await _result_single(result)
    token = type_token_from_row(row)
    if not token:
        return None
    concept_id = str(row["concept_id"]) if row and row.get("concept_id") else None
    return _TypeInfo(
        token=token,
        concept_id=concept_id,
        from_member_of=concept_id is not None,
    )


async def _ancestors(session: AsyncSession, concept_id: str) -> list[_Ancestor]:
    result = await session.run(READ_CONCEPT_ANCESTORS_CYPHER, concept_id=concept_id)
    rows = await _result_records(result)
    out: list[_Ancestor] = []
    for row in rows:
        anc_id = row.get("id")
        hops = row.get("hops")
        if not anc_id or hops is None:
            continue
        out.append(_Ancestor(id=str(anc_id), name=row.get("name"), hops=int(hops)))
    return out


async def _merge_rule(
    session: AsyncSession,
    *,
    source_category: str,
    relation_type: str,
    target_category: str,
    origin_id: str,
    generalization_level: int,
) -> None:
    if _both_kernel_catch_alls(source_category, target_category):
        return
    await session.run(
        MERGE_CONNECTIVITY_RULE_CYPHER,
        source_category=source_category,
        relation_type=relation_type,
        target_category=target_category,
        origin_id=origin_id,
        generalization_level=generalization_level,
    )


async def deposit_from_asserted_fact(
    session: AsyncSession,
    *,
    head_id: str,
    tail_id: str,
    relation_type: str,
    origin_id: str,
) -> None:
    """Deposit ``source —rel→ target`` (and bounded IS_A generalizations)."""
    rel = (relation_type or "").strip()
    if is_structural_relation_type(rel):
        return
    src = await _read_type_info(session, head_id)
    tgt = await _read_type_info(session, tail_id)
    if src is None or tgt is None:
        return

    await _merge_rule(
        session,
        source_category=src.token,
        relation_type=rel,
        target_category=tgt.token,
        origin_id=origin_id,
        generalization_level=0,
    )

    hops = int(settings.CONNECTIVITY_MAX_GENERALIZATION_HOPS)
    if hops < 1 or not src.from_member_of or not tgt.from_member_of:
        return
    if not src.concept_id or not tgt.concept_id:
        return

    catch = kernel_catch_all_ids()
    src_ancs = await _ancestors(session, src.concept_id)
    tgt_ancs = await _ancestors(session, tgt.concept_id)
    src_by_hop = {
        a.hops: a for a in src_ancs if a.hops <= hops and a.id not in catch
    }
    tgt_by_hop = {
        a.hops: a for a in tgt_ancs if a.hops <= hops and a.id not in catch
    }

    for hop in range(1, hops + 1):
        src_up = src_by_hop.get(hop)
        tgt_up = tgt_by_hop.get(hop)
        if src_up is not None and tgt_up is not None:
            await _merge_rule(
                session,
                source_category=src_up.id,
                relation_type=rel,
                target_category=tgt_up.id,
                origin_id=origin_id,
                generalization_level=hop,
            )
        elif src_up is not None:
            await _merge_rule(
                session,
                source_category=src_up.id,
                relation_type=rel,
                target_category=tgt.token,
                origin_id=origin_id,
                generalization_level=hop,
            )
        elif tgt_up is not None:
            await _merge_rule(
                session,
                source_category=src.token,
                relation_type=rel,
                target_category=tgt_up.id,
                origin_id=origin_id,
                generalization_level=hop,
            )
