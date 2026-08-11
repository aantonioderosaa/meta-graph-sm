"""Ingestion pipeline orchestration (tech-spec §5, E3.3–E3.6)."""

from __future__ import annotations

import logging
import uuid

from neo4j import AsyncSession

from app.core import event_bus
from app.core.llm_client import LLMValidationError, get_token_usage
from app.core.neo4j_client import get_driver
from app.pipeline import chunking, embeddings, extraction
from app.pipeline.chunking import Chunk

logger = logging.getLogger(__name__)

MERGE_CHUNK_CYPHER = """
MERGE (c:Chunk {id: $id})
SET c.doc_id = $doc_id,
    c.text = $text,
    c.embedding = $emb,
    c.created_at = datetime()
"""

CREATE_FACT_CYPHER = """
CREATE (f:Fact {
  id: $fid,
  text: $text,
  type: $type,
  is_latest: true,
  confidence: 1.0,
  dreamed: false,
  source_doc_id: $doc_id,
  embedding: $emb,
  created_at: datetime()
})
WITH f
MATCH (c:Chunk {id: $chunk_id})
CREATE (f)-[:DERIVED_FROM]->(c)
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


async def write_fact(
    session: AsyncSession,
    *,
    fact_id: str,
    text: str,
    fact_type: str,
    doc_id: str,
    chunk_id: str,
    embedding: list[float],
    job_id: str,
) -> None:
    """Create a Fact node linked to its source chunk."""
    await session.run(
        CREATE_FACT_CYPHER,
        fid=fact_id,
        text=text,
        type=fact_type,
        doc_id=doc_id,
        chunk_id=chunk_id,
        emb=embedding,
    )
    await event_bus.publish(
        job_id,
        "extraction",
        "fact_extracted",
        {"fact_id": fact_id, "chunk_id": chunk_id, "type": fact_type},
    )


async def process_chunk_extraction(
    session: AsyncSession,
    chunk: Chunk,
    doc_id: str,
    job_id: str,
) -> int:
    """Extract facts from one chunk and write them; return fact count written."""
    try:
        result = await extraction.extract_facts(chunk.text, job_id=job_id)
    except LLMValidationError:
        logger.exception("extraction_failed chunk_id=%s validation_error", chunk.id)
        await event_bus.publish(
            job_id,
            "extraction",
            "llm_call_failed",
            {"item_id": chunk.id, "error": "validation_error"},
        )
        return 0
    except Exception as exc:
        logger.exception("extraction_failed chunk_id=%s", chunk.id)
        await event_bus.publish(
            job_id,
            "extraction",
            "llm_call_failed",
            {"item_id": chunk.id, "error": str(exc)},
        )
        return 0

    if not result.facts:
        await event_bus.publish(
            job_id,
            "extraction",
            "chunk_discarded_noise",
            {"chunk_id": chunk.id},
        )
        return 0

    facts_written = 0
    for fact in result.facts:
        fact_id = str(uuid.uuid4())
        fact_embedding = embeddings.embed(fact.text)
        await write_fact(
            session,
            fact_id=fact_id,
            text=fact.text,
            fact_type=fact.type.value,
            doc_id=doc_id,
            chunk_id=chunk.id,
            embedding=fact_embedding,
            job_id=job_id,
        )
        facts_written += 1
    return facts_written


async def run_ingestion_pipeline(doc_id: str, text: str, job_id: str) -> None:
    """Run chunking → embed → write chunks → extract → write facts → pipeline_complete."""
    chunks = chunking.chunk_text(text, doc_id)
    total_facts = 0

    driver = get_driver()
    async with driver.session() as session:
        if chunks:
            chunk_embeddings = embeddings.embed_batch([chunk.text for chunk in chunks])
            for chunk, embedding in zip(chunks, chunk_embeddings, strict=True):
                await write_chunk(session, chunk, embedding, job_id)

        for chunk in chunks:
            total_facts += await process_chunk_extraction(session, chunk, doc_id, job_id)

    tokens = get_token_usage(job_id)
    stats: dict[str, int] = {"chunks": len(chunks), "facts": total_facts}
    if tokens:
        stats["tokens"] = tokens

    await event_bus.publish(
        job_id,
        "done",
        "pipeline_complete",
        {"stats": stats},
    )
