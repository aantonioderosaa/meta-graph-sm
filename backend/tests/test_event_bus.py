"""Event bus and SSE tests (E2.3)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from httpx import ASGITransport

from app.core import event_bus
from app.main import app


@pytest.fixture(autouse=True)
def clean_event_bus():
    event_bus.reset_event_bus()
    yield
    event_bus.reset_event_bus()


@pytest.mark.asyncio
async def test_publish_delivers_events_in_order():
    job_id = "job-order-test"
    queue = await event_bus.subscribe(job_id)

    await event_bus.publish(job_id, "chunking", "chunk_created", {"chunk_id": "c1"})
    await event_bus.publish(job_id, "extraction", "fact_extracted", {"fact_id": "f1"})
    await event_bus.publish(job_id, "done", "pipeline_complete", {"stats": {"facts": 1}})

    events = [await queue.get(), await queue.get(), await queue.get()]
    assert [event["event"] for event in events] == [
        "chunk_created",
        "fact_extracted",
        "pipeline_complete",
    ]
    assert events[0]["job_id"] == job_id
    assert "ts" in events[0]


@pytest.mark.asyncio
async def test_unsubscribe_removes_queue_no_leak():
    job_id = "job-leak-test"
    queue = await event_bus.subscribe(job_id)
    assert event_bus.subscriber_count(job_id) == 1

    await event_bus.unsubscribe(job_id, queue)
    assert event_bus.subscriber_count(job_id) == 0
    assert event_bus.subscriber_count() == 0


@pytest.mark.asyncio
async def test_sse_stream_receives_ordered_events_and_closes_on_done():
    job_id = "job-sse-test"

    async def publisher():
        await asyncio.sleep(0.05)
        await event_bus.publish(job_id, "chunking", "chunk_created", {"chunk_id": "c1"})
        await event_bus.publish(job_id, "done", "pipeline_complete", {"stats": {}})

    publish_task = asyncio.create_task(publisher())

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("GET", f"/events/stream?job_id={job_id}") as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")

            lines: list[str] = []
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    lines.append(line[len("data: ") :])
                if len(lines) >= 2:
                    break

    await publish_task

    events = [json.loads(line) for line in lines]
    assert events[0]["event"] == "chunk_created"
    assert events[1]["stage"] == "done"

    await asyncio.sleep(0.05)
    assert event_bus.subscriber_count(job_id) == 0
