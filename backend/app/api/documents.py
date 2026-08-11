"""Document ingestion stub (tech-spec §9, E2.5)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.schemas import DocumentRequest, JobResponse

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def ingest_document(body: DocumentRequest) -> JobResponse:
    """Stub: accept a document and return a background job id."""
    _ = body
    return JobResponse(job_id=str(uuid.uuid4()))
