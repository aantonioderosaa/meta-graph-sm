"""Ingestion pipeline orchestration (tech-spec §5, E3.3–E3.6)."""

from __future__ import annotations

import asyncio
import logging
import uuid

from neo4j import AsyncSession

from app.core import event_bus
from app.core.llm_client import LLMValidationError, get_token_usage
from app.core.neo4j_client import get_driver
from app.models.node_extraction import (
    EntityRelationExtractionResult,
    EventEntityExtractionResult,
    EventRelationExtractionResult,
)
from app.pipeline import chunking, embeddings, node_extraction
from app.pipeline.chunking import Chunk
from app.pipeline.concepts import merge_concept_and_link

logger = logging.getLogger(__name__)

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
  created_at: datetime()
}]->(t)
"""


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
) -> None:
    """Create a raw :Node linked to its source chunk."""
    await session.run(
        CREATE_NODE_CYPHER,
        id=node_id,
        name=name,
        type=node_type,
        emb=embedding,
        chunk_id=chunk_id,
    )
    event = "entity_extracted" if node_type == "entity" else "event_extracted"
    await event_bus.publish(
        job_id,
        "node_extraction",
        event,
        {"node_id": node_id, "name": name, "chunk_id": chunk_id},
    )


async def write_node_relation(
    session: AsyncSession,
    *,
    head_id: str,
    tail_id: str,
    relation: str,
    normalized_relation: str | None,
    head_name: str = "",
    tail_name: str = "",
) -> None:
    embedding = None
    if normalized_relation != "participates":
        embedding = embeddings.embed(f"{head_name} {relation} {tail_name}")
    await session.run(
        CREATE_NODE_RELATION_CYPHER,
        head_id=head_id,
        tail_id=tail_id,
        relation=relation,
        normalized_relation=normalized_relation,
        embedding=embedding,
    )


async def _publish_node_llm_failure(job_id: str, chunk_id: str, exc: BaseException) -> None:
    logger.error("node_extraction_failed chunk_id=%s", chunk_id, exc_info=exc)
    error = "validation_error" if isinstance(exc, LLMValidationError) else str(exc)
    await event_bus.publish(
        job_id,
        "node_extraction",
        "llm_call_failed",
        {"item_id": chunk_id, "error": error},
    )


def _empty_entity_relations() -> EntityRelationExtractionResult:
    return EntityRelationExtractionResult(triples=[])


def _empty_event_entities() -> EventEntityExtractionResult:
    return EventEntityExtractionResult(participations=[])


def _empty_event_relations() -> EventRelationExtractionResult:
    return EventRelationExtractionResult(triples=[])


async def _unwrap_node_extraction[T](
    result: T | BaseException, empty: T, job_id: str, chunk_id: str
) -> T:
    if isinstance(result, BaseException):
        await _publish_node_llm_failure(job_id, chunk_id, result)
        return empty
    return result


async def _concepts_for_node(
    node_type: str,
    name: str,
    chunk_text: str,
    job_id: str,
    chunk_id: str,
) -> list[str]:
    try:
        if node_type == "entity":
            result = await node_extraction.extract_entity_concepts(
                name, chunk_text, job_id=job_id
            )
        else:
            result = await node_extraction.extract_event_concepts(name, job_id=job_id)
    except LLMValidationError as exc:
        await _publish_node_llm_failure(job_id, chunk_id, exc)
        return []
    except Exception as exc:
        await _publish_node_llm_failure(job_id, chunk_id, exc)
        return []
    return [concept.strip() for concept in result.concepts if concept and concept.strip()]


async def process_chunk_node_extraction(
    session: AsyncSession,
    chunk: Chunk,
    doc_id: str,
    job_id: str,
) -> int:
    """Extract entity/event triples from one chunk and write raw nodes; return node count."""
    _ = doc_id
    raw_entity, raw_event_entity, raw_event_rel = await asyncio.gather(
        node_extraction.extract_entity_relations(chunk.text, job_id=job_id),
        node_extraction.extract_event_entities(chunk.text, job_id=job_id),
        node_extraction.extract_event_relations(chunk.text, job_id=job_id),
        return_exceptions=True,
    )
    entity_rel = await _unwrap_node_extraction(
        raw_entity, _empty_entity_relations(), job_id, chunk.id
    )
    event_entity = await _unwrap_node_extraction(
        raw_event_entity, _empty_event_entities(), job_id, chunk.id
    )
    event_rel = await _unwrap_node_extraction(
        raw_event_rel, _empty_event_relations(), job_id, chunk.id
    )

    created_nodes: list[tuple[str, str, str]] = []
    nodes_written = 0

    async def _create_node(name: str, node_type: str) -> str:
        nonlocal nodes_written
        node_id = str(uuid.uuid4())
        await write_node(
            session,
            node_id=node_id,
            name=name,
            node_type=node_type,
            chunk_id=chunk.id,
            embedding=embeddings.embed(name),
            job_id=job_id,
        )
        created_nodes.append((node_id, name, node_type))
        nodes_written += 1
        return node_id

    for triple in entity_rel.triples:
        head = triple.head.strip()
        tail = triple.tail.strip()
        if not head or not tail:
            continue
        head_id = await _create_node(head, "entity")
        tail_id = await _create_node(tail, "entity")
        await write_node_relation(
            session,
            head_id=head_id,
            tail_id=tail_id,
            relation=triple.relation,
            normalized_relation=None,
            head_name=head,
            tail_name=tail,
        )

    for triple in event_rel.triples:
        head = triple.head.strip()
        tail = triple.tail.strip()
        if not head or not tail:
            continue
        head_id = await _create_node(head, "event")
        tail_id = await _create_node(tail, "event")
        await write_node_relation(
            session,
            head_id=head_id,
            tail_id=tail_id,
            relation=triple.relation,
            normalized_relation=None,
            head_name=head,
            tail_name=tail,
        )

    for participation in event_entity.participations:
        event_name = participation.event.strip()
        if not event_name:
            continue
        event_id = await _create_node(event_name, "event")
        for entity_name in participation.entities:
            entity = entity_name.strip()
            if not entity:
                continue
            entity_id = await _create_node(entity, "entity")
            await write_node_relation(
                session,
                head_id=event_id,
                tail_id=entity_id,
                relation="is participated by",
                normalized_relation="participates",
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

    concept_cache: dict[tuple[str, str], list[str]] = {}
    for node_id, name, node_type in created_nodes:
        key = (node_type, name)
        if key not in concept_cache:
            concept_cache[key] = await _concepts_for_node(
                node_type, name, chunk.text, job_id, chunk.id
            )
        for concept_name in concept_cache[key]:
            await merge_concept_and_link(session, node_id, concept_name)

    return nodes_written


async def run_ingestion_pipeline(doc_id: str, text: str, job_id: str) -> None:
    """Run chunking → embed → write chunks → extract nodes → pipeline_complete."""
    chunks = chunking.chunk_text(text, doc_id)
    total_nodes = 0

    driver = get_driver()
    async with driver.session() as session:
        if chunks:
            chunk_embeddings = embeddings.embed_batch([chunk.text for chunk in chunks])
            for chunk, embedding in zip(chunks, chunk_embeddings, strict=True):
                await write_chunk(session, chunk, embedding, job_id)

        for chunk in chunks:
            total_nodes += await process_chunk_node_extraction(
                session, chunk, doc_id, job_id
            )

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
