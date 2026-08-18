"""PROMOTE: lift a cluster of instances into a subdomain Concept (Fase 5).

One recursion level only (F5.8): clusters of ``:Node`` under a kernel catch-all
or a first-level catch-all (``IS_A`` a kernel vertex). Clusters of ``:Concept``
are out of scope. Famiglia B and backbone relations are never lifted or retyped.

Atomicity is a single Neo4j write transaction (``execute_write``). Gates run
before any write. Idempotence is a deterministic Concept id; a second call is a
no-op. External edges are CREATE-copied onto S (no MERGE / no fusion); leaf
facts stay in place.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from neo4j import AsyncSession
from pydantic import BaseModel, Field

from app.core import event_bus
from app.core.config import settings
from app.core.llm_client import call_structured
from app.db.schema import BACKBONE_REL_TYPES, FAMIGLIA_B_REL_TYPES
from app.models.kernel import IS_A, KERNEL_VERSION, MEMBER_OF, EntityKernelType
from app.pipeline import embeddings
from app.pipeline.concepts import (
    compute_hash_id,
    kernel_catch_all_concept_id,
    parse_kernel_category,
)
from app.pipeline.domain_book import (
    CATEGORY_CARDS,
    Cluster,
    ClusterCandidate,
    passes_genre_vs_filter_gate,
    passes_mdl_gate,
)

logger = logging.getLogger(__name__)

STAGE = "promote_clusters"
_ISA_REL = IS_A.upper()
_MEMBER_OF_REL = MEMBER_OF.upper()
HAS_CONCEPT_REL = "HAS_CONCEPT"

DefinitionKind = Literal["primitive_concept", "value_filter"]

# Domain-bundle skip list: Famiglia B (doc1 §11.2) + backbone + free HAS_CONCEPT.
_SKIP_REL_TYPES: frozenset[str] = frozenset(
    {t.upper() for t in FAMIGLIA_B_REL_TYPES}
    | {t.lower() for t in FAMIGLIA_B_REL_TYPES}
    | {t.upper() for t in BACKBONE_REL_TYPES}
    | {t.lower() for t in BACKBONE_REL_TYPES}
    | {HAS_CONCEPT_REL, HAS_CONCEPT_REL.lower(), IS_A, MEMBER_OF}
)

FIND_PARENT_CYPHER = f"""
MATCH (p:Concept {{id: $parent_id}})
OPTIONAL MATCH (p)-[:{_ISA_REL}]->(up:Concept)
RETURN p.id AS id, p.name AS name, p.kernel_category AS kernel_category,
       up.id AS isa_parent_id
"""

FIND_CONCEPTS_IN_CLUSTER_CYPHER = """
MATCH (c:Concept)
WHERE c.id IN $cluster_ids
RETURN c.id AS id
"""

FIND_CLUSTER_MEMBERS_CYPHER = f"""
MATCH (n:Node)-[:{_MEMBER_OF_REL}]->(p:Concept {{id: $parent_id}})
WHERE n.id IN $cluster_ids
RETURN n.id AS id, n.name AS name, n.summary AS summary,
       n.kernel_category AS kernel_category, labels(n) AS labels
"""

FIND_EXISTING_PROMOTED_CYPHER = """
MATCH (c:Concept {id: $concept_id})
RETURN c.id AS id
"""

FIND_CLUSTER_RELATIONS_CYPHER = """
MATCH (a)-[r:Relation]->(b)
WHERE a.id IN $cluster_ids OR b.id IN $cluster_ids
RETURN a.id AS src_id, b.id AS tgt_id,
       r.relation AS relation,
       r.kernel_parent AS kernel_parent,
       r.normalized_relation AS normalized_relation,
       r.witnesses_a AS witnesses_a,
       r.witnesses_b AS witnesses_b
"""

FIND_KERNEL_CATCH_ALL_CYPHER = """
MATCH (c:Concept)
WHERE c.id IN $kernel_ids
RETURN c.id AS id
"""

FIND_FIRST_LEVEL_CATCH_ALL_CYPHER = f"""
MATCH (c:Concept)-[:{_ISA_REL}]->(k:Concept)
WHERE k.id IN $kernel_ids
RETURN c.id AS id
"""

FIND_DIRECT_NODE_MEMBERS_CYPHER = f"""
MATCH (n:Node)-[:{_MEMBER_OF_REL}]->(c:Concept {{id: $parent_id}})
WHERE NOT n:Concept
RETURN n.id AS id, n.name AS name, n.summary AS summary,
       n.kernel_category AS kernel_category
"""

CREATE_PROMOTED_CONCEPT_CYPHER = """
CREATE (s:Concept {
  id: $concept_id,
  name: $name,
  kernel_category: $kernel_category,
  parent_uri: $parent_uri,
  promoted: true,
  kernel_version: $kernel_version,
  definition: $definition,
  embedding: $embedding
})
RETURN s.id AS id
"""

LINK_PROMOTED_ISA_CYPHER = f"""
MATCH (s:Concept {{id: $concept_id}}), (parent:Concept {{id: $parent_id}})
CREATE (s)-[:{_ISA_REL}]->(parent)
"""

MOVE_MEMBER_OF_CYPHER = f"""
UNWIND $node_ids AS nid
MATCH (n:Node {{id: nid}})-[old:{_MEMBER_OF_REL}]->(parent:Concept {{id: $parent_id}})
DELETE old
WITH n
MATCH (s:Concept {{id: $concept_id}})
CREATE (n)-[:{_MEMBER_OF_REL}]->(s)
"""

LIFT_EXTERNAL_RELATION_CYPHER = """
UNWIND $edges AS edge
MATCH (src {id: edge.src_id}), (tgt {id: edge.tgt_id})
CREATE (src)-[:Relation {
  relation: edge.relation,
  kernel_parent: edge.kernel_parent,
  normalized_relation: edge.normalized_relation,
  witnesses_a: edge.witnesses_a,
  witnesses_b: edge.witnesses_b,
  lifted_from: edge.lifted_from,
  is_latest: true,
  created_at: datetime()
}]->(tgt)
"""

MERGE_TYPE_MIGRATION_ALIAS_CYPHER = """
UNWIND $types AS old_type
MERGE (a:TypeMigrationAlias {
  old_type: old_type,
  new_type: old_type,
  concept_id: $concept_id
})
ON CREATE SET a.frozen_at = datetime()
"""

LOOKUP_TYPE_MIGRATION_ALIAS_CYPHER = """
MATCH (a:TypeMigrationAlias {old_type: $old_type, concept_id: $concept_id})
RETURN a.old_type AS old_type, a.new_type AS new_type,
       a.concept_id AS concept_id, a.frozen_at AS frozen_at
"""

WriteFn = Callable[[Any], Awaitable[Any]]


class PromotedDefinition(BaseModel):
    """Optional intensional definition of a promoted subdomain (F5.6)."""

    definition: str = Field(default="", description="Short genre definition; empty is ok")


@dataclass(frozen=True)
class ClusterRelation:
    src_id: str
    tgt_id: str
    relation: str | None
    kernel_parent: str | None
    normalized_relation: str | None
    witnesses_a: list[str] = field(default_factory=list)
    witnesses_b: list[str] = field(default_factory=list)


def kernel_catch_all_ids() -> frozenset[str]:
    return frozenset(kernel_catch_all_concept_id(cat) for cat in EntityKernelType)


def promoted_concept_id(parent_concept_id: str, cluster_node_ids: list[str]) -> str:
    """Deterministic S id (F5.5). Same cluster + parent → same Concept."""
    members = ",".join(sorted(cluster_node_ids))
    return compute_hash_id(f"promote:{parent_concept_id}:{members}")


def is_promotable_parent(parent_id: str, isa_parent_id: str | None) -> bool:
    """F5.8: kernel catch-all, or first-level (IS_A a kernel catch-all)."""
    kernel_ids = kernel_catch_all_ids()
    if parent_id in kernel_ids:
        return True
    return isa_parent_id in kernel_ids


def is_skipped_relation(relation: str | None, kernel_parent: str | None = None) -> bool:
    for raw in (relation, kernel_parent):
        if raw and str(raw) in _SKIP_REL_TYPES:
            return True
    return False


def _as_str_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    return [str(item) for item in value if item is not None]


def _own_type(rel: ClusterRelation) -> str | None:
    if rel.relation:
        return str(rel.relation)
    if rel.kernel_parent:
        return str(rel.kernel_parent)
    return None


def _append_witness(existing: list[str], node_id: str) -> list[str]:
    witnesses = list(existing)
    witnesses.append(node_id)
    return witnesses


def _cluster_name(member_names: list[str], parent_name: str) -> str:
    names = sorted(n for n in member_names if n)
    if not names:
        return f"promoted:{parent_name or 'cluster'}"
    head = ", ".join(names[:3])
    if len(names) > 3:
        head += ", …"
    return head


async def _execute_write(session: Any, fn: WriteFn) -> Any:
    """Use Neo4j ``execute_write`` when present; else call ``fn(session)`` (tests)."""
    execute_write = getattr(session, "execute_write", None)
    if execute_write is not None:
        return await execute_write(fn)
    return await fn(session)


async def update_bundle(
    tx: Any,
    *,
    promoted_concept_id: str,
    lift_edges: list[dict[str, Any]],
) -> None:
    """CREATE-lift distinct external bundle elements onto S. No MERGE, no fusion.

    Called from ``work()`` inside the same ``execute_write`` as the rest of
    ``promote()``. ``lifted_from`` is already set on each edge.
    """
    _ = promoted_concept_id
    if not lift_edges:
        return
    await tx.run(LIFT_EXTERNAL_RELATION_CYPHER, edges=lift_edges)


async def _maybe_definition(member_rows: list[dict[str, Any]], name: str) -> str:
    """LLM definition is optional; empty string is acceptable this phase (F5.6)."""
    if not settings.OPENAI_API_KEY:
        return ""
    lines = []
    for row in member_rows:
        lines.append(f"- {row.get('name') or row['id']}: {row.get('summary') or ''}")
    user = (
        f"Nome candidato del genere: {name}\n"
        "Membri:\n" + "\n".join(lines) + "\n"
        "Scrivi una definition breve del genere (non un filtro sui valori)."
    )
    try:
        parsed = await call_structured(
            "Definisci un genere omogeneo di entità, non un argomento.",
            user,
            PromotedDefinition,
        )
    except Exception:
        logger.exception("promote_definition_llm_failed")
        return ""
    return (parsed.definition or "").strip()


def _passes_pre_write_gates(
    *,
    cluster_ids: list[str],
    parent: dict[str, Any] | None,
    concept_ids_in_cluster: set[str],
    member_rows: list[dict[str, Any]],
    internal_types: frozenset[str],
    definition_kind: DefinitionKind,
) -> bool:
    if not cluster_ids:
        return False
    if parent is None:
        return False
    if concept_ids_in_cluster:
        return False
    if not is_promotable_parent(parent["id"], parent.get("isa_parent_id")):
        return False
    found = {row["id"] for row in member_rows}
    if found != set(cluster_ids):
        return False
    categories: list[EntityKernelType] = []
    for row in member_rows:
        cat = parse_kernel_category(row.get("kernel_category"))
        if cat is None:
            return False
        categories.append(cat)
    if len(set(categories)) != 1:
        return False
    kernel_category = categories[0]
    candidate = ClusterCandidate(
        definition_kind=definition_kind,
        kernel_category=kernel_category,
        member_categories=tuple(categories),
    )
    if not passes_genre_vs_filter_gate(candidate):
        return False
    cluster = Cluster(members=tuple(cluster_ids), distinct_own_types=internal_types)
    return passes_mdl_gate(
        cluster,
        k=settings.BACKBONE_MDL_MIN_COVERAGE,
        m=settings.BACKBONE_MDL_MIN_PAYLOAD,
    )


async def promote(
    session: AsyncSession,
    parent_concept_id: str,
    cluster_node_ids: list[str],
    *,
    definition_kind: DefinitionKind = "primitive_concept",
) -> str:
    """Promote cluster C under parent into Concept S. Empty string = skipped, no write."""
    cluster_ids = sorted(set(cluster_node_ids))
    if not cluster_ids:
        return ""

    concept_id = promoted_concept_id(parent_concept_id, cluster_ids)
    existing = await session.run(FIND_EXISTING_PROMOTED_CYPHER, concept_id=concept_id)
    existing_row = await existing.single()
    if existing_row is not None:
        return existing_row["id"]

    parent_result = await session.run(FIND_PARENT_CYPHER, parent_id=parent_concept_id)
    parent_row = await parent_result.single()
    parent = dict(parent_row) if parent_row is not None else None

    concept_result = await session.run(
        FIND_CONCEPTS_IN_CLUSTER_CYPHER, cluster_ids=cluster_ids
    )
    concept_ids_in_cluster = {record["id"] async for record in concept_result}

    member_result = await session.run(
        FIND_CLUSTER_MEMBERS_CYPHER,
        parent_id=parent_concept_id,
        cluster_ids=cluster_ids,
    )
    member_rows = [dict(record) async for record in member_result]

    rel_result = await session.run(FIND_CLUSTER_RELATIONS_CYPHER, cluster_ids=cluster_ids)
    relations: list[ClusterRelation] = []
    async for record in rel_result:
        rel = ClusterRelation(
            src_id=record["src_id"],
            tgt_id=record["tgt_id"],
            relation=record["relation"],
            kernel_parent=record["kernel_parent"],
            normalized_relation=record["normalized_relation"],
            witnesses_a=_as_str_list(record["witnesses_a"]),
            witnesses_b=_as_str_list(record["witnesses_b"]),
        )
        if is_skipped_relation(rel.relation, rel.kernel_parent):
            continue
        relations.append(rel)

    cluster_set = set(cluster_ids)
    internal = [
        rel
        for rel in relations
        if rel.src_id in cluster_set and rel.tgt_id in cluster_set
    ]
    external = [
        rel
        for rel in relations
        if (rel.src_id in cluster_set) ^ (rel.tgt_id in cluster_set)
    ]
    internal_types = frozenset(t for rel in internal if (t := _own_type(rel)))

    if not _passes_pre_write_gates(
        cluster_ids=cluster_ids,
        parent=parent,
        concept_ids_in_cluster=concept_ids_in_cluster,
        member_rows=member_rows,
        internal_types=internal_types,
        definition_kind=definition_kind,
    ):
        return ""

    assert parent is not None
    kernel_category = parse_kernel_category(member_rows[0]["kernel_category"])
    assert kernel_category is not None
    parent_name = parent.get("name") or CATEGORY_CARDS[kernel_category].catch_all
    name = _cluster_name([str(row.get("name") or "") for row in member_rows], parent_name)
    definition = await _maybe_definition(member_rows, name)
    embedding = embeddings.embed(definition or name)

    lift_edges: list[dict[str, Any]] = []
    for rel in external:
        if rel.src_id in cluster_set:
            leaf = rel.src_id
            lift_edges.append(
                {
                    "src_id": concept_id,
                    "tgt_id": rel.tgt_id,
                    "relation": rel.relation,
                    "kernel_parent": rel.kernel_parent,
                    "normalized_relation": rel.normalized_relation,
                    "witnesses_a": _append_witness(rel.witnesses_a, leaf),
                    "witnesses_b": list(rel.witnesses_b),
                    "lifted_from": leaf,
                }
            )
        else:
            leaf = rel.tgt_id
            lift_edges.append(
                {
                    "src_id": rel.src_id,
                    "tgt_id": concept_id,
                    "relation": rel.relation,
                    "kernel_parent": rel.kernel_parent,
                    "normalized_relation": rel.normalized_relation,
                    "witnesses_a": list(rel.witnesses_a),
                    "witnesses_b": _append_witness(rel.witnesses_b, leaf),
                    "lifted_from": leaf,
                }
            )

    alias_types = sorted(internal_types)

    async def work(tx: Any) -> str:
        await tx.run(
            CREATE_PROMOTED_CONCEPT_CYPHER,
            concept_id=concept_id,
            name=name,
            kernel_category=kernel_category.value,
            parent_uri=parent_concept_id,
            kernel_version=KERNEL_VERSION,
            definition=definition,
            embedding=embedding,
        )
        await tx.run(
            LINK_PROMOTED_ISA_CYPHER,
            concept_id=concept_id,
            parent_id=parent_concept_id,
        )
        await tx.run(
            MOVE_MEMBER_OF_CYPHER,
            node_ids=cluster_ids,
            parent_id=parent_concept_id,
            concept_id=concept_id,
        )
        await update_bundle(
            tx, promoted_concept_id=concept_id, lift_edges=lift_edges
        )
        await tx.run(
            MERGE_TYPE_MIGRATION_ALIAS_CYPHER,
            types=alias_types,
            concept_id=concept_id,
        )
        return concept_id

    return await _execute_write(session, work)


async def promote_clusters(
    session: AsyncSession,
    job_id: str,
    *,
    parent_ids_out: list[str] | None = None,
) -> int:
    """Dreaming stage: promote Node clusters under kernel / first-level catch-alls.

    When ``parent_ids_out`` is provided, append each parent that produced an S
    (for the judge's historical re-refine pass, F10.4).
    """
    if not settings.ENABLE_PROMOTE:
        return 0

    kernel_ids = list(kernel_catch_all_ids())
    kernel_result = await session.run(FIND_KERNEL_CATCH_ALL_CYPHER, kernel_ids=kernel_ids)
    catch_all_ids = [record["id"] async for record in kernel_result]

    first_result = await session.run(
        FIND_FIRST_LEVEL_CATCH_ALL_CYPHER, kernel_ids=kernel_ids
    )
    catch_all_ids.extend([record["id"] async for record in first_result])
    # Snapshot before any write so a newly created S is not re-promoted this run.
    seen: set[str] = set()
    unique_parents: list[str] = []
    for cid in catch_all_ids:
        if cid not in seen:
            seen.add(cid)
            unique_parents.append(cid)

    written = 0
    for parent_id in unique_parents:
        members_result = await session.run(
            FIND_DIRECT_NODE_MEMBERS_CYPHER, parent_id=parent_id
        )
        members = [dict(record) async for record in members_result]
        if not members:
            continue
        cluster_ids = [row["id"] for row in members]
        try:
            concept_id = await promote(session, parent_id, cluster_ids)
        except Exception as exc:
            logger.exception("promote_clusters_failed parent_id=%s", parent_id)
            await event_bus.publish(
                job_id,
                STAGE,
                "llm_call_failed",
                {"stage": STAGE, "item_id": parent_id, "error": str(exc)},
            )
            continue
        if not concept_id:
            continue
        written += 1
        if parent_ids_out is not None:
            parent_ids_out.append(parent_id)
        await event_bus.publish(
            job_id,
            STAGE,
            "cluster_promoted",
            {
                "concept_id": concept_id,
                "parent_id": parent_id,
                "member_count": len(cluster_ids),
            },
        )
    return written
