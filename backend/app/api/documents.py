"""Document ingestion endpoint (tech-spec §5, §9, E3.6)."""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, status

from app.api.schemas import DocumentRequest, JobResponse
from app.pipeline.ingestion import run_ingestion_pipeline

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def ingest_document(body: DocumentRequest) -> JobResponse:
    """Accept a document and run ingestion in the background."""
    job_id = str(uuid.uuid4())
    asyncio.create_task(run_ingestion_pipeline(body.doc_id, body.text, job_id))
    return JobResponse(job_id=job_id)
