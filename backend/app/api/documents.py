"""Document ingestion + listing endpoints (tech-spec §5, §9, E3.6, F3.1)."""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, status

from app.api.schemas import DocumentListResponse, DocumentRequest, JobResponse
from app.core.event_bus import run_tracked_job
from app.core.neo4j_client import Neo4jSessionDep
from app.pipeline import documents_engine
from app.pipeline.ingestion import run_ingestion_pipeline

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("", response_model=DocumentListResponse)
async def list_documents(session: Neo4jSessionDep) -> DocumentListResponse:
    """List ingested documents with chunk/fact counts."""
    documents = await documents_engine.list_documents(session)
    return DocumentListResponse(documents=documents)


@router.post("", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def ingest_document(body: DocumentRequest) -> JobResponse:
    """Accept a document and run ingestion in the background."""
    job_id = str(uuid.uuid4())
    asyncio.create_task(
        run_tracked_job(job_id, run_ingestion_pipeline(body.doc_id, body.text, job_id))
    )
    return JobResponse(job_id=job_id)
