"""Generic subdomain instances (Fase 23).

A catch-all member with no plausible specific genre is redirected onto a
stable generic ``:Node`` of that subdomain. Edge move copies the
READ/CREATE/DELETE pattern from node resolution, but the generic summary
never converges. The original node is kept for audit (``merged_into``).
Observation confidence is a dedicated ``generic_observation_count`` on
``:Node``, not the context-layer hypothesis mechanism of Fasi 20-22.
"""

from __future__ import annotations

from typing import Any

from neo4j import AsyncSession

from app.models.kernel import MEMBER_OF, EntityKernelType
from app.pipeline.concepts import MERGE_MEMBER_OF_CYPHER, compute_hash_id
from app.pipeline.node_resolution import (
    COLLAPSE_INCOMING_RELATIONS_CYPHER,
    COLLAPSE_OUTGOING_RELATIONS_CYPHER,
    COPY_DERIVED_FROM_CYPHER,
    COPY_HAS_CONCEPT_CYPHER,
    CREATE_INCOMING_ON_CANON_CYPHER,
    CREATE_OUTGOING_ON_CANON_CYPHER,
    DELETE_DUP_RELATIONS_CYPHER,
    READ_INCOMING_RELATIONS_CYPHER,
    READ_OUTGOING_RELATIONS_CYPHER,
    SET_MERGED_INTO_CYPHER,
    _relationship_properties,
)

_MEMBER_OF_REL = MEMBER_OF.upper()

GENERIC_INSTANCE_SUMMARY = (
    "Istanza generica del sottodominio. I fatti specifici restano sulle Relation."
)

ENSURE_GENERIC_INSTANCE_CYPHER = """
MERGE (n:Node {id: $node_id})
ON CREATE SET
  n.name = $name,
  n.type = $type,
  n.is_generic = true,
  n.kernel_category = $kernel_category,
  n.summary = $summary
ON MATCH SET
  n.is_generic = true,
  n.type = $type,
  n.kernel_category = $kernel_category
RETURN n.id AS id
"""

SET_GENERIC_OBSERVATION_COUNT_CYPHER = """
MATCH (n:Node {id: $node_id})
SET n.generic_observation_count = $count
"""

DELETE_NODE_MEMBER_OF_CYPHER = f"""
MATCH (n:Node {{id: $node_id}})-[old:{_MEMBER_OF_REL}]->()
DELETE old
"""


def generic_instance_id(subdomain_concept_id: str, kernel_category: str) -> str:
    return compute_hash_id(f"generic:{subdomain_concept_id}:{kernel_category}")


def _category_value(kernel_category: EntityKernelType | str) -> str:
    if isinstance(kernel_category, EntityKernelType):
        return kernel_category.value
    return str(kernel_category)


async def ensure_generic_instance(
    session: AsyncSession,
    subdomain_concept_id: str,
    kernel_category: EntityKernelType | str,
) -> str:
    """Idempotent MERGE of the generic instance for a subdomain + kernel category.

    ``ON MATCH`` never overwrites ``summary`` — the generic summary does not
    converge toward witnessed fact text.
    """
    category = _category_value(kernel_category)
    node_id = generic_instance_id(subdomain_concept_id, category)
    name = f"istanza generica {category}"
    result = await session.run(
        ENSURE_GENERIC_INSTANCE_CYPHER,
        node_id=node_id,
        name=name,
        type="entity",
        kernel_category=category,
        summary=GENERIC_INSTANCE_SUMMARY,
    )
    row = await result.single()
    await session.run(
        MERGE_MEMBER_OF_CYPHER,
        node_id=node_id,
        concept_id=subdomain_concept_id,
    )
    return row["id"] if row is not None else node_id


async def redirect_to_generic(
    session: AsyncSession, node_id: str, generic_id: str
) -> None:
    """Move edges onto the generic node; keep the original for audit.

    Same READ/CREATE/DELETE edge-move as node resolution, without promoting
    ``summary`` onto the generic. Specific fact text stays on copied
    ``:Relation`` properties. Original ``MEMBER_OF`` is dropped so the
    singleton is no longer a catch-all member.
    """
    if node_id == generic_id:
        return

    outgoing = await session.run(
        READ_OUTGOING_RELATIONS_CYPHER,
        dup_id=node_id,
        canon_id=generic_id,
    )
    outgoing_rows: list[tuple[dict[str, Any], str]] = []
    async for record in outgoing:
        outgoing_rows.append((_relationship_properties(record["r"]), record["other_id"]))

    incoming = await session.run(
        READ_INCOMING_RELATIONS_CYPHER,
        dup_id=node_id,
        canon_id=generic_id,
    )
    incoming_rows: list[tuple[dict[str, Any], str]] = []
    async for record in incoming:
        incoming_rows.append((_relationship_properties(record["r"]), record["other_id"]))

    for props, other_id in outgoing_rows:
        await session.run(
            CREATE_OUTGOING_ON_CANON_CYPHER,
            canon_id=generic_id,
            other_id=other_id,
            props=props,
        )
    for props, other_id in incoming_rows:
        await session.run(
            CREATE_INCOMING_ON_CANON_CYPHER,
            canon_id=generic_id,
            other_id=other_id,
            props=props,
        )

    await session.run(DELETE_DUP_RELATIONS_CYPHER, dup_id=node_id)
    await session.run(
        COPY_HAS_CONCEPT_CYPHER, dup_id=node_id, canon_id=generic_id
    )
    await session.run(
        COPY_DERIVED_FROM_CYPHER, dup_id=node_id, canon_id=generic_id
    )
    await session.run(DELETE_NODE_MEMBER_OF_CYPHER, node_id=node_id)
    await session.run(SET_MERGED_INTO_CYPHER, dup_id=node_id, canon_id=generic_id)
    await session.run(COLLAPSE_OUTGOING_RELATIONS_CYPHER, canon_id=generic_id)
    await session.run(COLLAPSE_INCOMING_RELATIONS_CYPHER, canon_id=generic_id)
