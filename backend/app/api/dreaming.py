"""Dreaming pipeline stub (tech-spec §9, E2.5)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.schemas import DreamingRunRequest, JobResponse

router = APIRouter(prefix="/dreaming", tags=["dreaming"])


@router.post("/run", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def run_dreaming(body: DreamingRunRequest | None = None) -> JobResponse:
    """Stub: start a dreaming cycle and return a background job id."""
    _ = body
    return JobResponse(job_id=str(uuid.uuid4()))
