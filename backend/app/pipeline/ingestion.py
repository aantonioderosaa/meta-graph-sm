"""Ingestion pipeline orchestration (tech-spec §5, E3.3–E3.6; Fase 3 anti-blur)."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from itertools import combinations

from neo4j import AsyncSession

from app.core import event_bus
from app.core.llm_client import LLMValidationError, call_structured, get_token_usage
from app.core.neo4j_client import get_driver
from app.models.kernel import EntityKernelType, RelationKernelType
from app.models.node_extraction import (
    EntityExtractionResult,
    EventEntityExtractionResult,
    EventRelationExtractionResult,
    ExtractedEntity,
    MacroDomainSummary,
    PairRelationDecision,
)
from app.pipeline import chunking, embeddings, node_extraction
from app.pipeline.chunking import Chunk
from app.pipeline.connectivity_rules import deposit_from_asserted_fact
from app.pipeline.node_extraction_prompts import build_corpus_summary_prompt

logger = logging.getLogger(__name__)

CORPUS_CONTEXT_ID = "default"

# Chunks processed concurrently in run_ingestion_pipeline. The real throttle on
# LLM traffic stays call_structured's LLM_MAX_CONCURRENCY semaphore in
# llm_client.py — this only bounds how many chunks (and their own Neo4j
# sessions) are in flight at once, so the next chunk's calls can queue up on
# that semaphore instead of the whole pipeline sitting idle while one chunk
# finishes its O(k^2) pairwise entity-relation classification before the next
# chunk's extraction even starts. Not a Settings flag, same spirit as
# EVENT_TRIAGE_MAX_SLOT_FANOUT — bounds cost, not something to retune per corpus.
CHUNK_CONCURRENCY = 8

MERGE_CHUNK_CYPHER = """
MERGE (c:Chunk {id: $id})
SET c.doc_id = $doc_id,
    c.text = $text,
    c.embedding = $emb,
    c.created_at = datetime()
"""

CREATE_NODE_CYPHER = """
CREATE (n:Node {
  id: $id,
  name: $name,
  type: $type,
  dreamed: false,
  merged_into: null,
  embedding: $emb,
  summary_embedding: $summary_emb,
  summary: $summary,
  kernel_category: $kernel_category,
  created_at: datetime()
})
WITH n
MATCH (c:Chunk {id: $chunk_id})
CREATE (n)-[:DERIVED_FROM]->(c)
"""

CREATE_NODE_RELATION_CYPHER = """
MATCH (h:Node {id: $head_id}), (t:Node {id: $tail_id})
CREATE (h)-[:Relation {
  relation: $relation,
  normalized_relation: $normalized_relation,
  embedding: $embedding,
  is_latest: true,
  kernel_parent: $kernel_parent,
  witnesses_a: $witnesses_a,
  witnesses_b: $witnesses_b,
  witness_text: $witness_text,
  valid_time: $valid_time,
  system_time: datetime(),
  provenance: $provenance,
  created_at: datetime()
}]->(t)
"""

READ_CORPUS_CONTEXT_CYPHER = """
MATCH (c:CorpusContext {id: $id})
RETURN c.summary_text AS summary_text, c.document_count AS document_count
"""

MERGE_CORPUS_CONTEXT_CYPHER = """
MERGE (c:CorpusContext {id: $id})
SET c.summary_text = $summary_text,
    c.updated_at = datetime(),
    c.document_count = coalesce(c.document_count, 0) + 1,
    c.embedding = $embedding
"""

CREATE_CONTRADICTS_CYPHER = """
MATCH (a:Node {id: $left_id}), (b:Node {id: $right_id})
CREATE (a)-[:CONTRADICTS {
  subject_id: $subject_id,
  relation: $relation,
  kernel_parent: $kernel_parent,
  created_at: datetime()
}]->(b)
"""


def has_required_witnesses(witness_source: str | None, witness_target: str | None) -> bool:
    """Pre-write filter: a fact without both non-empty witnesses is not written."""
    return bool((witness_source or "").strip() and (witness_target or "").strip())


def normalize_entity_name(name: str) -> str:
    return name.strip().casefold()


def dedupe_extracted_entities(entities: list[ExtractedEntity]) -> list[ExtractedEntity]:
    """Keep first occurrence of each case-insensitive stripped name."""
    seen: set[str] = set()
    unique: list[ExtractedEntity] = []
    for entity in entities:
        key = normalize_entity_name(entity.name)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(entity)
    return unique


def find_entity_by_loose_name(
    name: str, node_ids: dict[tuple[str, str], str]
) -> str | None:
    """Containment match against ``entity`` nodes already created for this chunk.

    The entity-list pass and the event-participation pass are two independent
    LLM calls reading the same chunk (Fase 3, run in parallel); they can name
    the same referent differently ("Rossi" vs "Mario Rossi"). Exact
    normalized-name matching (``_create_node``'s own dedup) misses that and
    used to create a second, bare ``:Node`` with no ``summary``/
    ``kernel_category`` — permanently unclassifiable by the backbone (Fase 4
    skips nodes without ``kernel_category``). This is a cheap, local
    heuristic: no new LLM/embedding call, and scoped to this chunk's own
    entities only — cross-chunk/cross-document duplicates stay the job of
    the embedding-based dedup in ``node_resolution.py`` during dreaming.
    Ties broken by longest known name (the more specific match).
    """
    target = normalize_entity_name(name)
    if not target:
        return None
    best_id: str | None = None
    best_len = -1
    for (node_type, known_name), node_id in node_ids.items():
        if node_type != "entity" or not known_name:
            continue
        if known_name == target:
            return node_id
        if (known_name in target or target in known_name) and len(known_name) > best_len:
            best_id = node_id
            best_len = len(known_name)
    return best_id


@dataclass(frozen=True)
class _WrittenEntityFact:
    head_id: str
    head_norm: str
    tail_id: str
    tail_norm: str
    relation: str
    kernel_parent: str


async def write_chunk(
    session: AsyncSession,
    chunk: Chunk,
    embedding: list[float],
    job_id: str,
) -> None:
    """Persist a chunk and emit chunk_created."""
    await session.run(
        MERGE_CHUNK_CYPHER,
        id=chunk.id,
        doc_id=chunk.doc_id,
        text=chunk.text,
        emb=embedding,
    )
    await event_bus.publish(
        job_id,
        "chunking",
        "chunk_created",
        {"chunk_id": chunk.id, "doc_id": chunk.doc_id},
    )


async def write_node(
    session: AsyncSession,
    *,
    node_id: str,
    name: str,
    node_type: str,
    chunk_id: str,
    embedding: list[float],
    job_id: str,
    summary: str = "",
    kernel_category: str | None = None,
) -> None:
    """Create a raw :Node linked to its source chunk."""
    summary_value = summary or ""
    await session.run(
        CREATE_NODE_CYPHER,
        id=node_id,
        name=name,
        type=node_type,
        emb=embedding,
        summary_emb=embeddings.embed(summary_value),
        chunk_id=chunk_id,
        summary=summary_value,
        kernel_category=kernel_category,
    )
    event = "entity_extracted" if node_type == "entity" else "event_extracted"
    await event_bus.publish(
        job_id,
        "node_extraction",
        event,
        {"node_id": node_id, "name": name, "chunk_id": chunk_id},
    )


def _provenance_value(provenance: dict | str | None) -> str | None:
    if provenance is None:
        return None
    if isinstance(provenance, str):
        return provenance
    return json.dumps(provenance, ensure_ascii=False, sort_keys=True)


async def write_node_relation(
    session: AsyncSession,
    *,
    head_id: str,
    tail_id: str,
    relation: str,
    normalized_relation: str | None,
    head_name: str = "",
    tail_name: str = "",
    kernel_parent: RelationKernelType | str | None = None,
    witness_source: str = "",
    witness_target: str = "",
    valid_time: str | None = None,
    provenance: dict | str | None = None,
) -> None:
    parent_value: str | None
    if isinstance(kernel_parent, RelationKernelType):
        parent_value = kernel_parent.value
    else:
        parent_value = kernel_parent
    if not (parent_value or "").strip():
        logger.warning(
            "skipping asserted relation without kernel_parent head_id=%s tail_id=%s",
            head_id,
            tail_id,
        )
        return
    embedding = None
    if normalized_relation != "participates":
        embedding = embeddings.embed(f"{head_name} {relation} {tail_name}")
    witnesses_a = [witness_source] if witness_source.strip() else []
    witnesses_b = [witness_target] if witness_target.strip() else []
    await session.run(
        CREATE_NODE_RELATION_CYPHER,
        head_id=head_id,
        tail_id=tail_id,
        relation=relation,
        normalized_relation=normalized_relation,
        embedding=embedding,
        kernel_parent=parent_value,
        witnesses_a=witnesses_a,
        witnesses_b=witnesses_b,
        witness_text=" ".join([*witnesses_a, *witnesses_b]),
        valid_time=valid_time,
        provenance=_provenance_value(provenance),
    )
    relation_type = (relation or "").strip() or parent_value
    origin_id = f"{head_id}|{relation_type}|{tail_id}"
    await deposit_from_asserted_fact(
        session,
        head_id=head_id,
        tail_id=tail_id,
        relation_type=relation_type,
        origin_id=origin_id,
    )


async def write_contradicts(
    session: AsyncSession,
    *,
    left_id: str,
    right_id: str,
    subject_id: str,
    relation: str,
    kernel_parent: str,
) -> None:
    """Famiglia B CONTRADICTS between two conflicting assertion endpoints (tails)."""
    await session.run(
        CREATE_CONTRADICTS_CYPHER,
        left_id=left_id,
        right_id=right_id,
        subject_id=subject_id,
        relation=relation,
        kernel_parent=kernel_parent,
    )


async def update_corpus_context(
    session: AsyncSession,
    document_text: str,
    job_id: str,
) -> str:
    """F3.0: O(1) running macro-summary; never re-reads the full corpus."""
    existing_summary = ""
    try:
        result = await session.run(READ_CORPUS_CONTEXT_CYPHER, id=CORPUS_CONTEXT_ID)
        record = await result.single()
        if record is not None:
            existing_summary = record["summary_text"] or ""
    except Exception:
        logger.exception("corpus_context_read_failed")
        existing_summary = ""

    summary_text = existing_summary
    try:
        system_prompt, user_prompt = build_corpus_summary_prompt(
            existing_summary, document_text
        )
        updated = await call_structured(
            system_prompt,
            user_prompt,
            MacroDomainSummary,
            temperature=0,
            job_id=job_id,
        )
        summary_text = updated.summary_text
    except Exception as exc:
        logger.error("corpus_context_llm_failed", exc_info=exc)
        error = "validation_error" if isinstance(exc, LLMValidationError) else str(exc)
        await event_bus.publish(
            job_id,
            "ingestion",
            "llm_call_failed",
            {"item_id": "corpus_context", "error": error},
        )

    embedding = embeddings.embed(summary_text) if summary_text.strip() else None
    await session.run(
        MERGE_CORPUS_CONTEXT_CYPHER,
        id=CORPUS_CONTEXT_ID,
        summary_text=summary_text,
        embedding=embedding,
    )
    return summary_text


async def _publish_node_llm_failure(job_id: str, chunk_id: str, exc: BaseException) -> None:
    logger.error("node_extraction_failed chunk_id=%s", chunk_id, exc_info=exc)
    error = "validation_error" if isinstance(exc, LLMValidationError) else str(exc)
    await event_bus.publish(
        job_id,
        "node_extraction",
        "llm_call_failed",
        {"item_id": chunk_id, "error": error},
    )


def _empty_entities() -> EntityExtractionResult:
    return EntityExtractionResult(entities=[])


def _empty_event_entities() -> EventEntityExtractionResult:
    return EventEntityExtractionResult(participations=[])


def _empty_event_relations() -> EventRelationExtractionResult:
    return EventRelationExtractionResult(triples=[])


def _unrelated_pair() -> PairRelationDecision:
    return PairRelationDecision(related=False)


async def _unwrap_node_extraction[T](
    result: T | BaseException, empty: T, job_id: str, chunk_id: str
) -> T:
    if isinstance(result, BaseException):
        await _publish_node_llm_failure(job_id, chunk_id, result)
        return empty
    return result


def _kernel_category_value(category: EntityKernelType | str | None) -> str | None:
    if category is None:
        return None
    if isinstance(category, EntityKernelType):
        return category.value
    return category


async def _write_same_chunk_contradicts(
    session: AsyncSession, facts: list[_WrittenEntityFact]
) -> None:
    """F3.5: same-head + same kernel_parent/relation + different tails → both + CONTRADICTS."""
    for left, right in combinations(facts, 2):
        if left.head_norm != right.head_norm:
            continue
        if left.tail_norm == right.tail_norm:
            continue
        same_attribute = (
            left.kernel_parent == right.kernel_parent
            or left.relation.strip().casefold() == right.relation.strip().casefold()
        )
        if not same_attribute:
            continue
        await write_contradicts(
            session,
            left_id=left.tail_id,
            right_id=right.tail_id,
            subject_id=left.head_id,
            relation=left.relation,
            kernel_parent=left.kernel_parent,
        )


async def process_chunk_node_extraction(
    session: AsyncSession,
    chunk: Chunk,
    doc_id: str,
    job_id: str,
    corpus_summary: str = "",
) -> int:
    """Two-pass entity extraction + events; write raw nodes; return node count."""
    raw_entities, raw_event_entity, raw_event_rel = await asyncio.gather(
        node_extraction.extract_entities(
            chunk.text, job_id=job_id, corpus_summary=corpus_summary
        ),
        node_extraction.extract_event_entities(chunk.text, job_id=job_id),
        node_extraction.extract_event_relations(chunk.text, job_id=job_id),
        return_exceptions=True,
    )
    entity_result = await _unwrap_node_extraction(
        raw_entities, _empty_entities(), job_id, chunk.id
    )
    event_entity = await _unwrap_node_extraction(
        raw_event_entity, _empty_event_entities(), job_id, chunk.id
    )
    event_rel = await _unwrap_node_extraction(
        raw_event_rel, _empty_event_relations(), job_id, chunk.id
    )

    nodes_written = 0
    node_ids: dict[tuple[str, str], str] = {}

    async def _create_node(
        name: str,
        node_type: str,
        *,
        summary: str = "",
        kernel_category: EntityKernelType | str | None = None,
    ) -> str:
        nonlocal nodes_written
        key = (node_type, normalize_entity_name(name))
        existing = node_ids.get(key)
        if existing is not None:
            return existing
        node_id = str(uuid.uuid4())
        await write_node(
            session,
            node_id=node_id,
            name=name,
            node_type=node_type,
            chunk_id=chunk.id,
            embedding=embeddings.embed(name),
            job_id=job_id,
            summary=summary,
            kernel_category=_kernel_category_value(kernel_category),
        )
        node_ids[key] = node_id
        nodes_written += 1
        return node_id

    unique_entities = dedupe_extracted_entities(entity_result.entities)
    pair_entities: list[tuple[str, ExtractedEntity]] = []
    for entity in unique_entities:
        name = entity.name.strip()
        node_id = await _create_node(
            name,
            "entity",
            summary=entity.summary,
            kernel_category=entity.kernel_category,
        )
        pair_entities.append((node_id, entity))

    pair_jobs: list[tuple[str, ExtractedEntity, str, ExtractedEntity]] = []
    for (id_a, ent_a), (id_b, ent_b) in combinations(pair_entities, 2):
        if normalize_entity_name(ent_a.name) == normalize_entity_name(ent_b.name):
            continue
        if not ent_a.summary.strip() or not ent_b.summary.strip():
            continue
        pair_jobs.append((id_a, ent_a, id_b, ent_b))

    raw_pairs: list[PairRelationDecision | BaseException] = []
    if pair_jobs:
        raw_pairs = await asyncio.gather(
            *[
                node_extraction.extract_pair_relation(
                    chunk.text,
                    ent_a.name,
                    ent_a.summary,
                    ent_b.name,
                    ent_b.summary,
                    job_id=job_id,
                    corpus_summary=corpus_summary,
                )
                for _id_a, ent_a, _id_b, ent_b in pair_jobs
            ],
            return_exceptions=True,
        )

    written_facts: list[_WrittenEntityFact] = []
    for (id_a, ent_a, id_b, ent_b), raw in zip(pair_jobs, raw_pairs, strict=True):
        decision = await _unwrap_node_extraction(
            raw, _unrelated_pair(), job_id, chunk.id
        )
        if not decision.related:
            continue
        if not has_required_witnesses(decision.witness_source, decision.witness_target):
            continue
        if decision.kernel_parent is None or not decision.relation.strip():
            continue
        await write_node_relation(
            session,
            head_id=id_a,
            tail_id=id_b,
            relation=decision.relation,
            normalized_relation=None,
            head_name=ent_a.name,
            tail_name=ent_b.name,
            kernel_parent=decision.kernel_parent,
            witness_source=decision.witness_source,
            witness_target=decision.witness_target,
        )
        written_facts.append(
            _WrittenEntityFact(
                head_id=id_a,
                head_norm=normalize_entity_name(ent_a.name),
                tail_id=id_b,
                tail_norm=normalize_entity_name(ent_b.name),
                relation=decision.relation,
                kernel_parent=decision.kernel_parent.value,
            )
        )

    await _write_same_chunk_contradicts(session, written_facts)

    for triple in event_rel.triples:
        head = triple.head.strip()
        tail = triple.tail.strip()
        if not head or not tail:
            continue
        if not has_required_witnesses(triple.witness_source, triple.witness_target):
            continue
        head_id = await _create_node(
            head, "event", summary=head, kernel_category=EntityKernelType.Evento
        )
        tail_id = await _create_node(
            tail, "event", summary=tail, kernel_category=EntityKernelType.Evento
        )
        await write_node_relation(
            session,
            head_id=head_id,
            tail_id=tail_id,
            relation=triple.relation,
            normalized_relation=None,
            head_name=head,
            tail_name=tail,
            kernel_parent=triple.kernel_parent,
            witness_source=triple.witness_source,
            witness_target=triple.witness_target,
        )

    for participation in event_entity.participations:
        event_name = participation.event.strip()
        if not event_name:
            continue
        event_id = await _create_node(
            event_name,
            "event",
            summary=event_name,
            kernel_category=EntityKernelType.Evento,
        )
        for entity_name in participation.entities:
            entity = entity_name.strip()
            if not entity:
                continue
            entity_id = find_entity_by_loose_name(
                entity, node_ids
            ) or await _create_node(entity, "entity")
            await write_node_relation(
                session,
                head_id=event_id,
                tail_id=entity_id,
                relation="is participated by",
                normalized_relation="participates",
                head_name=event_name,
                tail_name=entity,
                kernel_parent=RelationKernelType.Partecipativa,
                witness_source=event_name,
                witness_target=entity,
            )
            await event_bus.publish(
                job_id,
                "node_extraction",
                "participation_extracted",
                {
                    "event_id": event_id,
                    "entity_id": entity_id,
                    "chunk_id": chunk.id,
                },
            )

    return nodes_written


async def run_ingestion_pipeline(doc_id: str, text: str, job_id: str) -> None:
    """F3.0 corpus context → chunking → embed → write chunks → two-pass extract → complete."""
    driver = get_driver()
    chunks: list[Chunk] = []
    async with driver.session() as session:
        corpus_summary = await update_corpus_context(session, text, job_id)

        chunks = chunking.chunk_text(text, doc_id)

        if chunks:
            chunk_embeddings = embeddings.embed_batch([chunk.text for chunk in chunks])
            for chunk, embedding in zip(chunks, chunk_embeddings, strict=True):
                await write_chunk(session, chunk, embedding, job_id)

    total_nodes = 0
    if chunks:
        semaphore = asyncio.Semaphore(CHUNK_CONCURRENCY)

        async def _extract_one(chunk: Chunk) -> int:
            # Each concurrently-running chunk gets its own Neo4j session — an
            # AsyncSession is not safe to share across coroutines awaiting on
            # it at the same time.
            async with semaphore, driver.session() as chunk_session:
                return await process_chunk_node_extraction(
                    chunk_session, chunk, doc_id, job_id, corpus_summary=corpus_summary
                )

        results = await asyncio.gather(*(_extract_one(chunk) for chunk in chunks))
        total_nodes = sum(results)

    tokens = get_token_usage(job_id)
    stats: dict[str, int] = {"chunks": len(chunks), "nodes": total_nodes}
    if tokens:
        stats["tokens"] = tokens

    await event_bus.publish(
        job_id,
        "done",
        "pipeline_complete",
        {"stats": stats},
    )
