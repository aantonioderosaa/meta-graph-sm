"""Document listing aggregation (Epic F3.1)."""

from __future__ import annotations

from typing import Any

from neo4j import AsyncSession

from app.api.schemas import DocumentSummary

LIST_DOCUMENTS_CYPHER = """
MATCH (c:Chunk)
WITH c.doc_id AS doc_id, count(c) AS chunk_count,
     min(c.created_at) AS first_at, max(c.created_at) AS last_at
CALL {
  WITH doc_id
  MATCH (f:Fact {source_doc_id: doc_id})
  RETURN count(f) AS fact_count
}
RETURN doc_id, chunk_count, fact_count, first_at, last_at
ORDER BY last_at DESC
"""


def _datetime_to_str(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


async def list_documents(session: AsyncSession) -> list[DocumentSummary]:
    """Aggregate ingested documents by doc_id with chunk/fact counts."""
    result = await session.run(LIST_DOCUMENTS_CYPHER)
    documents: list[DocumentSummary] = []
    async for record in result:
        doc_id = record["doc_id"]
        if not doc_id:
            continue
        documents.append(
            DocumentSummary(
                doc_id=doc_id,
                chunk_count=int(record["chunk_count"] or 0),
                fact_count=int(record["fact_count"] or 0),
                first_ingested_at=_datetime_to_str(record["first_at"]),
                last_ingested_at=_datetime_to_str(record["last_at"]),
            )
        )
    return documents
