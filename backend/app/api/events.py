"""SSE event stream endpoint (tech-spec §9, E2.3)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.core import event_bus

router = APIRouter(tags=["events"])


async def _event_generator(job_id: str) -> AsyncIterator[str]:
    queue = await event_bus.subscribe(job_id)
    try:
        while True:
            event = await queue.get()
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("stage") == "done":
                break
    finally:
        await event_bus.unsubscribe(job_id, queue)


@router.get("/events/stream")
async def stream_events(
    job_id: str = Query(..., description="Pipeline job identifier"),
) -> StreamingResponse:
    """Stream pipeline events for the given job_id."""
    return StreamingResponse(_event_generator(job_id), media_type="text/event-stream")
