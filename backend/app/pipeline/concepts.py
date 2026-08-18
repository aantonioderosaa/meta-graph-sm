"""Concept hashing, HAS_CONCEPT merge, and TBox MEMBER_OF matching (Fase 4).

``merge_concept_and_link`` remains the free thematic bridge (``HAS_CONCEPT``).
Unique home of an instance is ``MEMBER_OF`` — exactly one, never a substitute
for ``HAS_CONCEPT``.

Match is two-level (same pattern as ``node_resolution``): exact Concept id/name,
then ``concept_embedding`` cosine restricted to the same ``kernel_category``.

Decision rule (doc4 §3):
- ``score >= θ_reuse`` (``BACKBONE_REUSE_THRESHOLD``, default 0.80) → reuse that
  Concept; do not create a genre.
- ``θ_near <= score < θ_reuse`` (``BACKBONE_NEAR_THRESHOLD``, default 0.50) →
  ``MEMBER_OF`` the kernel catch-all.
- no rows / ``score < θ_near`` → propose a named genre. Create it only when
  ``passes_genre_vs_filter_gate`` is True. **MDL is not required here** (Fase 5
  ``PROMOTE``); a singleton named type may hang under the kernel catch-all.
  Gate fail → ``MEMBER_OF`` catch-all + ``:UnanchoredCandidate``.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Literal

from neo4j import AsyncSession

from app.core.config import settings
from app.models.kernel import IS_A, KERNEL_VERSION, MEMBER_OF, EntityKernelType
from app.pipeline import embeddings
from app.pipeline.domain_book import (
    CATEGORY_CARDS,
    ClusterCandidate,
    passes_genre_vs_filter_gate,
)

logger = logging.getLogger(__name__)

CONCEPT_VECTOR_INDEX = "concept_embedding"
CONCEPT_VECTOR_K = 8
CORPUS_CONTEXT_ID = "default"
_ISA_REL = IS_A.upper()
_MEMBER_OF_REL = MEMBER_OF.upper()

MERGE_CONCEPT_LINK_CYPHER = """
MATCH (n:Node {id: $node_id})
MERGE (c:Concept {id: $concept_id})
ON CREATE SET c.name = $name, c.embedding = $embedding
MERGE (n)-[:HAS_CONCEPT]->(c)
"""

FIND_CONCEPT_BY_ID_CYPHER = """
MATCH (c:Concept {id: $concept_id})
RETURN c.id AS id, c.name AS name
"""

FIND_CONCEPT_EXACT_NAME_CYPHER = """
MATCH (c:Concept {name: $name})
WHERE c.kernel_category = $kernel_category
RETURN c.id AS id, c.name AS name
"""

FIND_CONCEPT_VECTOR_CYPHER = """
CALL db.index.vector.queryNodes('concept_embedding', $k, $embedding)
YIELD node, score
WHERE node.kernel_category = $kernel_category
RETURN node.id AS id, node.name AS name, score
ORDER BY score DESC
"""

FIND_EXISTING_MEMBER_OF_CYPHER = f"""
MATCH (n:Node {{id: $node_id}})-[:{_MEMBER_OF_REL}]->(c:Concept)
RETURN c.id AS concept_id
LIMIT 1
"""

MERGE_MEMBER_OF_CYPHER = f"""
MATCH (n:Node {{id: $node_id}}), (c:Concept {{id: $concept_id}})
MERGE (n)-[:{_MEMBER_OF_REL}]->(c)
"""

ENSURE_KERNEL_CATCH_ALL_CYPHER = """
MERGE (c:Concept {id: $concept_id})
ON CREATE SET
  c.name = $name,
  c.kernel_category = $kernel_category,
  c.promoted = true,
  c.kernel_version = $kernel_version,
  c.definition = $definition,
  c.embedding = $embedding
ON MATCH SET
  c.kernel_category = $kernel_category,
  c.promoted = true,
  c.kernel_version = $kernel_version
RETURN c.id AS id
"""

CREATE_ANCHORED_GENRE_CYPHER = f"""
MATCH (parent:Concept {{id: $parent_id}})
MERGE (c:Concept {{id: $concept_id}})
ON CREATE SET
  c.name = $name,
  c.kernel_category = $kernel_category,
  c.promoted = true,
  c.kernel_version = $kernel_version,
  c.definition = $definition,
  c.embedding = $embedding,
  c.parent_uri = $parent_id
MERGE (c)-[:{_ISA_REL}]->(parent)
RETURN c.id AS id
"""

WRITE_UNANCHORED_CANDIDATE_CYPHER = """
MERGE (u:UnanchoredCandidate {node_id: $node_id})
ON CREATE SET
  u.name = $name,
  u.kernel_category = $kernel_category,
  u.reason = $reason,
  u.priority = $priority,
  u.created_at = datetime()
ON MATCH SET
  u.name = $name,
  u.kernel_category = $kernel_category,
  u.reason = $reason,
  u.priority = $priority
"""

READ_CORPUS_CONTEXT_SUMMARY_CYPHER = """
MATCH (c:CorpusContext {id: $id})
RETURN c.summary_text AS summary_text
"""

FIND_UNCLASSIFIED_NODES_CYPHER = f"""
MATCH (n:Node)
WHERE n.merged_into IS NULL
  AND (n.type = 'entity' OR n.type = 'event')
  AND NOT (n)-[:{_MEMBER_OF_REL}]->(:Concept)
RETURN n.id AS id, n.name AS name, n.summary AS summary,
       n.kernel_category AS kernel_category, n.type AS type
"""

ConceptMatchVia = Literal["exact_id", "exact_name", "embedding"]
DefinitionKind = Literal["primitive_concept", "value_filter"]


@dataclass(frozen=True)
class ConceptMatch:
    concept_id: str
    name: str
    score: float
    via: ConceptMatchVia


def compute_hash_id(text: str) -> str:
    text = text + "_concept"
    hash_object = hashlib.sha256(text.encode("utf-8"))
    return hash_object.hexdigest()


def kernel_catch_all_concept_id(category: EntityKernelType) -> str:
    """Stable id for the kernel vertex; prefix avoids colliding with free tags."""
    return compute_hash_id(f"kernel:{category.value}")


def genre_concept_id(category: EntityKernelType, genre_name: str) -> str:
    """Stable id for a named genre under a kernel category (not a free HAS_CONCEPT tag)."""
    return compute_hash_id(f"genre:{category.value}:{genre_name}")


async def merge_concept_and_link(session: AsyncSession, node_id: str, concept_name: str) -> None:
    concept_id = compute_hash_id(concept_name)
    embedding = embeddings.embed(concept_name)
    await session.run(
        MERGE_CONCEPT_LINK_CYPHER,
        node_id=node_id,
        concept_id=concept_id,
        name=concept_name,
        embedding=embedding,
    )


def infer_definition_kind(name: str, summary: str = "") -> DefinitionKind:
    """Heuristic for F4.4: value filters vs primitive types. No extra LLM."""
    blob = f"{name} {summary}".casefold()
    markers = ("σ_", "sigma_", "età>", "età <", "age>", "age <", "value_filter")
    if any(marker in blob for marker in markers):
        return "value_filter"
    return "primitive_concept"


def unanchored_priority(name: str, summary: str, corpus_text: str) -> int:
    """Higher (2) when name/summary is not reflected in ``:CorpusContext`` summary."""
    hay = (corpus_text or "").casefold()
    if not hay:
        return 2
    if name and name.casefold() in hay:
        return 1
    tokens = [
        tok
        for tok in f"{name} {summary}".casefold().replace(",", " ").split()
        if len(tok) > 3
    ]
    if tokens and any(tok in hay for tok in tokens):
        return 1
    return 2


def parse_kernel_category(value: object) -> EntityKernelType | None:
    if value is None:
        return None
    try:
        return EntityKernelType(str(value))
    except ValueError:
        return None


def _embed_text_for_node(name: str, summary: str | None) -> str:
    text = (summary or "").strip()
    return text if text else name


async def find_concept_match(
    session: AsyncSession,
    *,
    name: str,
    kernel_category: EntityKernelType,
    embedding: list[float],
) -> ConceptMatch | None:
    """Exact id → exact name (same kernel_category) → vector (same kernel_category)."""
    genre_id = genre_concept_id(kernel_category, name)
    exact_id = await session.run(FIND_CONCEPT_BY_ID_CYPHER, concept_id=genre_id)
    row = await exact_id.single()
    if row is not None:
        return ConceptMatch(
            concept_id=row["id"],
            name=row["name"] or name,
            score=1.0,
            via="exact_id",
        )

    exact_name = await session.run(
        FIND_CONCEPT_EXACT_NAME_CYPHER,
        name=name,
        kernel_category=kernel_category.value,
    )
    row = await exact_name.single()
    if row is not None:
        return ConceptMatch(
            concept_id=row["id"],
            name=row["name"] or name,
            score=1.0,
            via="exact_name",
        )

    vector = await session.run(
        FIND_CONCEPT_VECTOR_CYPHER,
        k=CONCEPT_VECTOR_K,
        embedding=embedding,
        kernel_category=kernel_category.value,
    )
    best: ConceptMatch | None = None
    async for record in vector:
        score = float(record["score"])
        if best is None or score > best.score:
            best = ConceptMatch(
                concept_id=record["id"],
                name=record["name"] or "",
                score=score,
                via="embedding",
            )
    return best


async def get_existing_member_of(session: AsyncSession, node_id: str) -> str | None:
    result = await session.run(FIND_EXISTING_MEMBER_OF_CYPHER, node_id=node_id)
    row = await result.single()
    if row is None:
        return None
    return row["concept_id"]


async def merge_member_of(session: AsyncSession, node_id: str, concept_id: str) -> None:
    await session.run(MERGE_MEMBER_OF_CYPHER, node_id=node_id, concept_id=concept_id)


async def ensure_kernel_catch_all(
    session: AsyncSession, category: EntityKernelType
) -> str:
    """Idempotent MERGE of the kernel vertex. Catch-alls have no ``IS_A`` parent."""
    card = CATEGORY_CARDS[category]
    concept_id = kernel_catch_all_concept_id(category)
    definition = card.criterio_appartenenza
    embedding = embeddings.embed(definition)
    result = await session.run(
        ENSURE_KERNEL_CATCH_ALL_CYPHER,
        concept_id=concept_id,
        name=card.catch_all,
        kernel_category=category.value,
        kernel_version=KERNEL_VERSION,
        definition=definition,
        embedding=embedding,
    )
    row = await result.single()
    return row["id"] if row is not None else concept_id


async def create_anchored_genre(
    session: AsyncSession,
    *,
    name: str,
    definition: str,
    category: EntityKernelType,
    parent_id: str,
    embedding: list[float],
) -> str:
    """New promoted genre ``IS_A`` an existing Concept (catch-all or closer parent)."""
    concept_id = genre_concept_id(category, name)
    result = await session.run(
        CREATE_ANCHORED_GENRE_CYPHER,
        parent_id=parent_id,
        concept_id=concept_id,
        name=name,
        kernel_category=category.value,
        kernel_version=KERNEL_VERSION,
        definition=definition,
        embedding=embedding,
    )
    row = await result.single()
    return row["id"] if row is not None else concept_id


async def write_unanchored_candidate(
    session: AsyncSession,
    *,
    node_id: str,
    name: str,
    kernel_category: str | None,
    reason: str,
    priority: int,
) -> None:
    await session.run(
        WRITE_UNANCHORED_CANDIDATE_CYPHER,
        node_id=node_id,
        name=name,
        kernel_category=kernel_category,
        reason=reason,
        priority=priority,
    )


async def read_corpus_summary(session: AsyncSession) -> str:
    result = await session.run(READ_CORPUS_CONTEXT_SUMMARY_CYPHER, id=CORPUS_CONTEXT_ID)
    row = await result.single()
    if row is None:
        return ""
    return row["summary_text"] or ""


async def assign_entity_home(
    session: AsyncSession,
    *,
    node_id: str,
    name: str,
    summary: str | None,
    kernel_category: EntityKernelType | str | None,
    definition_kind: DefinitionKind | None = None,
    corpus_summary: str = "",
) -> str | None:
    """Assign exactly one ``MEMBER_OF``. Skip if already homed. Return concept id.

    Nodes with a null/invalid ``kernel_category`` cannot be catch-all-assigned:
    log ``:UnanchoredCandidate`` and skip (no crash).
    """
    existing = await get_existing_member_of(session, node_id)
    if existing is not None:
        return existing

    category = (
        kernel_category
        if isinstance(kernel_category, EntityKernelType)
        else parse_kernel_category(kernel_category)
    )
    if category is None:
        logger.warning(
            "UnanchoredCandidate skip: kernel_category is null node_id=%s name=%s",
            node_id,
            name,
        )
        await write_unanchored_candidate(
            session,
            node_id=node_id,
            name=name,
            kernel_category=str(kernel_category) if kernel_category is not None else None,
            reason="missing_kernel_category",
            priority=unanchored_priority(name, summary or "", corpus_summary),
        )
        return None

    catch_all_id = await ensure_kernel_catch_all(session, category)
    embed_source = _embed_text_for_node(name, summary)
    embedding = embeddings.embed(embed_source)
    match = await find_concept_match(
        session, name=name, kernel_category=category, embedding=embedding
    )

    reuse_th = settings.BACKBONE_REUSE_THRESHOLD
    near_th = settings.BACKBONE_NEAR_THRESHOLD

    if match is not None and match.score >= reuse_th:
        await merge_member_of(session, node_id, match.concept_id)
        return match.concept_id

    if match is not None and match.score >= near_th:
        await merge_member_of(session, node_id, catch_all_id)
        return catch_all_id

    kind = definition_kind or infer_definition_kind(name, summary or "")
    candidate = ClusterCandidate(
        definition_kind=kind,
        kernel_category=category,
        member_categories=(category,),
    )
    # MDL is Fase 5 PROMOTE. F4.4 àncora-o-fallisci is genre-vs-filter only:
    # otherwise a singleton named type could never hang under the catch-all (k=5).
    if not passes_genre_vs_filter_gate(candidate):
        await merge_member_of(session, node_id, catch_all_id)
        await write_unanchored_candidate(
            session,
            node_id=node_id,
            name=name,
            kernel_category=category.value,
            reason="genre_vs_filter_gate",
            priority=unanchored_priority(name, summary or "", corpus_summary),
        )
        return catch_all_id

    definition = (summary or "").strip() or name
    new_id = await create_anchored_genre(
        session,
        name=name,
        definition=definition,
        category=category,
        parent_id=catch_all_id,
        embedding=embeddings.embed(definition),
    )
    await merge_member_of(session, node_id, new_id)
    return new_id
