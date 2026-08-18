"""Identity + facets: non-destructive SAME_AS / POSSIBLY_SAME_AS (Fase 8).

Identity URI scheme
-------------------
Primary: ``identity:{normalized_name}:{kernel_category}``
    Stable lexical key for a named facet of a known ``EntityKernelType``.
Fallback: ``identity:{id1,id2,...}`` with facet ids sorted lexicographically
    Used when name/category are unavailable.

Creating an ``:IdentityNode`` never deletes, moves, or copies facet
``:Relation`` / ``HAS_CONCEPT`` / ``DERIVED_FROM``, and never sets ``merged_into``.

Canonical directions (fixed for this module):
- ``(:Node)-[:SAME_AS]->(:IdentityNode)``
- ``(:Node)-[:POSSIBLY_SAME_AS]->(:Node)`` with ``src_id < dst_id``
- ``(:Node)-[:NOT_SAME_AS]->(:Node)`` with ``src_id < dst_id``

Ingestion/dreaming with ``ENABLE_FACET_IDENTITY`` writes only POSSIBLY_SAME_AS.
``link_as_facet`` (SAME_AS) is implemented for the judge (Fase 10) and tests;
dreaming must not call it in this phase.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from neo4j import AsyncSession

from app.core.config import settings

SAME_AS = "SAME_AS"
POSSIBLY_SAME_AS = "POSSIBLY_SAME_AS"
NOT_SAME_AS = "NOT_SAME_AS"

MERGE_IDENTITY_NODE_CYPHER = """
MERGE (i:IdentityNode {uri: $uri})
ON CREATE SET i.canonical_summary = $canonical_summary
ON MATCH SET i.canonical_summary = CASE
  WHEN i.canonical_summary IS NULL OR i.canonical_summary = ''
  THEN $canonical_summary
  ELSE i.canonical_summary
END
RETURN i.uri AS uri
"""

LINK_SAME_AS_CYPHER = """
MATCH (facet:Node {id: $facet_node_id})
MATCH (identity:IdentityNode {uri: $identity_id})
MERGE (facet)-[:SAME_AS]->(identity)
"""

UNLINK_FACET_CYPHER = """
MATCH (facet:Node {id: $facet_node_id})-[r:SAME_AS|POSSIBLY_SAME_AS]-
      (identity:IdentityNode {uri: $identity_id})
DELETE r
"""

LINK_POSSIBLY_SAME_AS_CYPHER = """
MATCH (a:Node {id: $src_id}), (b:Node {id: $dst_id})
MERGE (a)-[:POSSIBLY_SAME_AS]->(b)
"""

MARK_NOT_SAME_AS_CYPHER = """
MATCH (a:Node {id: $src_id}), (b:Node {id: $dst_id})
MERGE (a)-[:NOT_SAME_AS]->(b)
"""

LOAD_NODE_IDENTITY_FIELDS_CYPHER = """
MATCH (n:Node {id: $node_id})
WHERE n.merged_into IS NULL
RETURN n.id AS id, n.name AS name, n.kernel_category AS kernel_category,
       coalesce(n.summary_embedding, n.embedding) AS summary_embedding,
       n.aliases AS aliases
"""

LOAD_OTHER_NODES_IDENTITY_FIELDS_CYPHER = """
MATCH (n:Node)
WHERE n.merged_into IS NULL AND n.id <> $node_id
RETURN n.id AS id, n.name AS name, n.kernel_category AS kernel_category,
       coalesce(n.summary_embedding, n.embedding) AS summary_embedding,
       n.aliases AS aliases
"""

LOAD_NOT_SAME_AS_NEIGHBORS_CYPHER = """
MATCH (n:Node {id: $node_id})-[:NOT_SAME_AS]-(other:Node)
RETURN other.id AS id
"""


def identity_uri_from_name(name: str, kernel_category: str) -> str:
    """Primary URI: ``identity:{normalized_name}:{kernel_category}``."""
    return f"identity:{normalize_identity_name(name)}:{kernel_category}"


def identity_uri_from_facet_ids(facet_ids: Sequence[str]) -> str:
    """Fallback URI: ``identity:{sorted_facet_ids}`` joined by comma."""
    return "identity:" + ",".join(sorted(facet_ids))


def normalize_identity_name(name: str) -> str:
    """Lowercase, strip accents and punctuation (doc4 §2 lexical key)."""
    nfkd = unicodedata.normalize("NFKD", name or "")
    no_accents = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    lowered = no_accents.casefold()
    cleaned = re.sub(r"[^\w\s]", "", lowered, flags=re.UNICODE)
    cleaned = cleaned.replace("_", " ")
    return " ".join(cleaned.split())


def cosine(a: list[float], b: list[float]) -> float:
    """Pure-Python cosine similarity. Empty / length-mismatch / zero-norm → 0.0."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b, strict=True):
        fx, fy = float(x), float(y)
        dot += fx * fy
        norm_a += fx * fx
        norm_b += fy * fy
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def _ordered_pair(node_a_id: str, node_b_id: str) -> tuple[str, str]:
    if node_a_id <= node_b_id:
        return node_a_id, node_b_id
    return node_b_id, node_a_id


def _alias_values(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        return (raw,) if raw.strip() else ()
    if isinstance(raw, Sequence) and not isinstance(raw, (bytes, bytearray)):
        return tuple(str(item) for item in raw if str(item).strip())
    return ()


def _normalized_aliases(raw: object) -> set[str]:
    return {normalize_identity_name(alias) for alias in _alias_values(raw)} - {""}


def _embedding_list(raw: object) -> list[float] | None:
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        values = [float(x) for x in raw]
        return values or None
    return None


def _alias_hit(node: Mapping[str, Any], other: Mapping[str, Any]) -> bool:
    node_name = normalize_identity_name(str(node.get("name") or ""))
    other_name = normalize_identity_name(str(other.get("name") or ""))
    node_aliases = _normalized_aliases(node.get("aliases"))
    other_aliases = _normalized_aliases(other.get("aliases"))
    if node_name and node_name in other_aliases:
        return True
    if other_name and other_name in node_aliases:
        return True
    return bool(node_aliases and other_aliases and node_aliases & other_aliases)


def _pair_excluded(
    node_id: str,
    other_id: str,
    not_same_as: set[frozenset[str]] | None,
) -> bool:
    if not not_same_as:
        return False
    return frozenset({node_id, other_id}) in not_same_as


def generate_identity_candidates(
    node: Mapping[str, Any],
    others: Sequence[Mapping[str, Any]],
    *,
    tau: float | None = None,
    not_same_as: set[frozenset[str]] | None = None,
) -> list[str]:
    """Blocking (doc4 §2). Same ``kernel_category`` AND (name / alias / cosine ≥ τ).

    Different ``kernel_category`` is never a candidate. Pairs in ``not_same_as``
    (``NOT_SAME_AS``) are skipped. Returns candidate ids in ``others`` order.
    """
    threshold = settings.IDENTITY_BLOCK_THRESHOLD if tau is None else tau
    node_id = str(node.get("id") or "")
    node_cat = str(node.get("kernel_category") or "")
    node_name = normalize_identity_name(str(node.get("name") or ""))
    node_emb = _embedding_list(node.get("summary_embedding"))
    hits: list[str] = []
    for other in others:
        other_id = str(other.get("id") or "")
        if not other_id or other_id == node_id:
            continue
        if _pair_excluded(node_id, other_id, not_same_as):
            continue
        other_cat = str(other.get("kernel_category") or "")
        if not node_cat or node_cat != other_cat:
            continue
        other_name = normalize_identity_name(str(other.get("name") or ""))
        names_equal = bool(node_name) and node_name == other_name
        alias_ok = _alias_hit(node, other)
        cosine_ok = False
        other_emb = _embedding_list(other.get("summary_embedding"))
        if node_emb is not None and other_emb is not None:
            cosine_ok = cosine(node_emb, other_emb) >= threshold
        if names_equal or alias_ok or cosine_ok:
            hits.append(other_id)
    return hits


async def ensure_identity_node(
    session: AsyncSession, *, uri: str, canonical_summary: str
) -> str:
    """MERGE ``:IdentityNode {uri}``. Set summary only when empty. Facets untouched."""
    result = await session.run(
        MERGE_IDENTITY_NODE_CYPHER,
        uri=uri,
        canonical_summary=canonical_summary,
    )
    record = await result.single()
    if record is None:
        return uri
    return str(record["uri"])


async def link_as_facet(session: AsyncSession, identity_id: str, facet_node_id: str) -> None:
    """MERGE ``(facet)-[:SAME_AS]->(identity)``. No edge move/delete on the facet."""
    await session.run(
        LINK_SAME_AS_CYPHER,
        identity_id=identity_id,
        facet_node_id=facet_node_id,
    )


async def index_entity_version(
    session: AsyncSession, identity_id: str, node_id: str
) -> None:
    """Index a temporal version ``:Node`` under ``:IdentityNode`` via SAME_AS.

    Versions stay autonomous (own facts). SAME_AS here is the identity index,
    not a destructive merge — no edges are moved or deleted.
    """
    await link_as_facet(session, identity_id, node_id)


async def unlink_facet(session: AsyncSession, identity_id: str, facet_node_id: str) -> None:
    """DELETE SAME_AS and POSSIBLY_SAME_AS between that IdentityNode/Node pair only."""
    await session.run(
        UNLINK_FACET_CYPHER,
        identity_id=identity_id,
        facet_node_id=facet_node_id,
    )


async def link_possibly_same_as(session: AsyncSession, node_id: str, candidate_id: str) -> None:
    """MERGE POSSIBLY_SAME_AS between two ``:Node``s. Never writes SAME_AS."""
    if node_id == candidate_id:
        return
    src_id, dst_id = _ordered_pair(node_id, candidate_id)
    await session.run(LINK_POSSIBLY_SAME_AS_CYPHER, src_id=src_id, dst_id=dst_id)


async def mark_not_same_as(session: AsyncSession, node_a_id: str, node_b_id: str) -> None:
    """MERGE ``:NOT_SAME_AS`` between two ``:Node``s (canonical src < dst)."""
    if node_a_id == node_b_id:
        return
    src_id, dst_id = _ordered_pair(node_a_id, node_b_id)
    await session.run(MARK_NOT_SAME_AS_CYPHER, src_id=src_id, dst_id=dst_id)


def _row_to_identity_record(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row.get("name") or "",
        "kernel_category": row.get("kernel_category") or "",
        "summary_embedding": _embedding_list(row.get("summary_embedding")),
        "aliases": _alias_values(row.get("aliases")),
    }


async def generate_identity_candidates_from_session(
    session: AsyncSession,
    node_id: str,
    *,
    tau: float | None = None,
) -> list[tuple[str, str]]:
    """Load graph neighbors, skip ``NOT_SAME_AS``, apply blocking. Pairs ``(node, cand)``."""
    node_result = await session.run(LOAD_NODE_IDENTITY_FIELDS_CYPHER, node_id=node_id)
    node_record = await node_result.single()
    if node_record is None:
        return []
    node = _row_to_identity_record(node_record)

    others_result = await session.run(LOAD_OTHER_NODES_IDENTITY_FIELDS_CYPHER, node_id=node_id)
    others: list[dict[str, Any]] = []
    async for row in others_result:
        others.append(_row_to_identity_record(row))

    excluded_result = await session.run(LOAD_NOT_SAME_AS_NEIGHBORS_CYPHER, node_id=node_id)
    not_same_as: set[frozenset[str]] = set()
    async for row in excluded_result:
        other_id = row["id"]
        if other_id:
            not_same_as.add(frozenset({node_id, str(other_id)}))

    candidate_ids = generate_identity_candidates(node, others, tau=tau, not_same_as=not_same_as)
    return [(node_id, candidate_id) for candidate_id in candidate_ids]
