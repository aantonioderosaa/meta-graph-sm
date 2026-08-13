"""Dreaming pipeline endpoint (tech-spec §6, §9, E4.9)."""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, status

from app.api.schemas import DreamingRunRequest, JobResponse
from app.core.event_bus import run_tracked_job
from app.pipeline.dreaming import run_dreaming_pipeline

router = APIRouter(prefix="/dreaming", tags=["dreaming"])


@router.post("/run", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def run_dreaming(body: DreamingRunRequest | None = None) -> JobResponse:
    """Start a dreaming cycle on fresh (undreamed) facts."""
    request = body or DreamingRunRequest()
    job_id = request.job_id or str(uuid.uuid4())
    asyncio.create_task(
        run_tracked_job(job_id, run_dreaming_pipeline(job_id, doc_id=request.doc_id))
    )
    return JobResponse(job_id=job_id)
