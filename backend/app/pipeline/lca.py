"""LCA of instance homes and read-only fact projections (Fase 6).

One physical leaf fact, many projections. Asserted facts are ``:Relation``
between ``:Node``, written once at ingest. Visibility from a subdomain is a
downward graph traversal (reverse ``IS_A``, then reverse ``MEMBER_OF``), never
a second Node–Node copy. Concept-endpoint bundle shadows from PROMOTE
(``lifted_from``) are not extra leaf facts.

Co-membership (sharing ``MEMBER_OF``) is not an edge.
"""

from __future__ import annotations

from typing import Any

from neo4j import AsyncSession

from app.models.kernel import IS_A, MEMBER_OF

_ISA_REL = IS_A.upper()
_MEMBER_OF_REL = MEMBER_OF.upper()

FIND_HOME_AND_ANCESTORS_CYPHER = f"""
MATCH (n:Node {{id: $node_id}})-[:{_MEMBER_OF_REL}]->(home:Concept)
OPTIONAL MATCH path = (home)-[:{_ISA_REL}*0..]->(anc:Concept)
RETURN home.id AS home_id, anc.id AS ancestor_id, length(path) AS dist
"""

FACTS_VISIBLE_IN_SUBDOMAIN_CYPHER = f"""
MATCH (root:Concept {{id: $concept_id}})
OPTIONAL MATCH (desc:Concept)-[:{_ISA_REL}*0..]->(root)
OPTIONAL MATCH (inst:Node)-[:{_MEMBER_OF_REL}]->(desc)
WHERE inst IS NULL OR inst.merged_into IS NULL
WITH [id IN collect(DISTINCT inst.id) WHERE id IS NOT NULL] AS instance_ids
MATCH (a:Node)-[r:Relation]->(b:Node)
WHERE a.id IN instance_ids AND b.id IN instance_ids
  AND r.lifted_from IS NULL
RETURN elementId(r) AS id,
       a.id AS from_id,
       b.id AS to_id,
       r.relation AS relation,
       coalesce(r.normalized_relation, r.relation) AS normalized_relation,
       r.kernel_parent AS kernel_parent
"""

WITNESSED_NEIGHBORS_CYPHER = """
MATCH (n:Node {id: $node_id})-[r:Relation]-(m:Node)
WHERE r.lifted_from IS NULL
  AND n.merged_into IS NULL
  AND m.merged_into IS NULL
  AND m.id <> n.id
RETURN DISTINCT m.id AS id
"""

COUNT_PHYSICAL_LEAF_FACTS_CYPHER = """
MATCH (a:Node)-[r:Relation]->(b:Node)
WHERE r.lifted_from IS NULL
RETURN count(r) AS n
"""


def lca_from_ancestor_lists(a_chain: list[str], b_chain: list[str]) -> str | None:
    """Lowest common ancestor: first id of ``a_chain`` (home-first) also in ``b_chain``.

    Chains run from the instance home toward kernel catch-alls. Same home is a
    valid LCA. Empty either side → None.
    """
    if not a_chain or not b_chain:
        return None
    b_set = set(b_chain)
    for ancestor in a_chain:
        if ancestor in b_set:
            return ancestor
    return None


def _chain_from_ancestor_rows(rows: list[dict[str, Any]]) -> list[str] | None:
    if not rows:
        return None
    ordered: list[tuple[int, str]] = []
    for row in rows:
        home_id = row.get("home_id")
        ancestor_id = row.get("ancestor_id")
        dist = row.get("dist")
        if home_id is None:
            continue
        node_id = ancestor_id if ancestor_id is not None else home_id
        hop = 0 if dist is None else int(dist)
        ordered.append((hop, str(node_id)))
    if not ordered:
        return None
    ordered.sort(key=lambda item: item[0])
    chain: list[str] = []
    seen: set[str] = set()
    for _hop, node_id in ordered:
        if node_id in seen:
            continue
        seen.add(node_id)
        chain.append(node_id)
    return chain or None


async def _ancestor_chain(session: AsyncSession, node_id: str) -> list[str] | None:
    result = await session.run(FIND_HOME_AND_ANCESTORS_CYPHER, node_id=node_id)
    rows: list[dict[str, Any]] = []
    async for record in result:
        rows.append(
            {
                "home_id": record["home_id"],
                "ancestor_id": record["ancestor_id"],
                "dist": record["dist"],
            }
        )
    return _chain_from_ancestor_rows(rows)


async def compute_lca(session: AsyncSession, node_a_id: str, node_b_id: str) -> str | None:
    """First common Concept on the MEMBER_OF / IS_A paths of two nodes.

    Each ``:Node`` has a unique home ``MEMBER_OF`` a ``:Concept``; Concepts
    climb ``IS_A`` toward kernel catch-alls. Missing ``MEMBER_OF`` → None.
    """
    chain_a = await _ancestor_chain(session, node_a_id)
    chain_b = await _ancestor_chain(session, node_b_id)
    if chain_a is None or chain_b is None:
        return None
    return lca_from_ancestor_lists(chain_a, chain_b)


async def facts_visible_in_subdomain(session: AsyncSession, concept_id: str) -> list[dict]:
    """Leaf ``:Relation`` facts whose both endpoints live under ``concept_id``.

    Traversal only — this function never CREATE/MERGE-s edges.
    """
    result = await session.run(FACTS_VISIBLE_IN_SUBDOMAIN_CYPHER, concept_id=concept_id)
    facts: list[dict] = []
    async for record in result:
        facts.append(
            {
                "id": str(record["id"]),
                "from_id": str(record["from_id"]),
                "to_id": str(record["to_id"]),
                "relation": record.get("relation"),
                "normalized_relation": record.get("normalized_relation"),
                "kernel_parent": record.get("kernel_parent"),
            }
        )
    return facts


async def witnessed_neighbors(session: AsyncSession, node_id: str) -> list[str]:
    """Node ids linked by a witnessed leaf ``:Relation``, not co-membership."""
    result = await session.run(WITNESSED_NEIGHBORS_CYPHER, node_id=node_id)
    ids: list[str] = []
    async for record in result:
        ids.append(str(record["id"]))
    return ids


async def count_physical_leaf_facts(session: AsyncSession) -> int:
    """Count Node–Node ``:Relation`` facts (excludes PROMOTE ``lifted_from`` shadows)."""
    result = await session.run(COUNT_PHYSICAL_LEAF_FACTS_CYPHER)
    async for record in result:
        return int(record["n"] or 0)
    return 0
