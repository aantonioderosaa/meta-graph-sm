"""Live job list + SSE replay for the event-graph pipeline dashboard."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from httpx import ASGITransport

from app.pipeline.event_graph.infra import bus as event_graph_bus
from app.pipeline.event_graph.infra.bus import (
    PIPELINE_STAGES,
    elenca_job,
    merge_job_lists,
    register_job,
)


@pytest.fixture(autouse=True)
def _reset_bus():
    event_graph_bus.reset_event_bus()
    yield
    event_graph_bus.reset_event_bus()


def test_pipeline_stages_match_current_ingestion():
    assert PIPELINE_STAGES == (
        "macro",
        "espansione",
        "collocazione_temporale",
        "relazioni",
        "riconciliazione",
        "done",
        "failed",
    )
    assert "regole_chunk" not in PIPELINE_STAGES
    assert "estrazione" not in PIPELINE_STAGES


@pytest.mark.asyncio
async def test_subscribe_replays_history_for_late_client():
    job_id = "job-replay"
    await event_graph_bus.publish(job_id, "macro", "macro_done", {"n": 1})
    await event_graph_bus.publish(job_id, "done", "pipeline_complete", {"n": 2})

    queue = await event_graph_bus.subscribe(job_id)
    first = queue.get_nowait()
    second = queue.get_nowait()
    assert first["stage"] == "macro"
    assert second["stage"] == "done"
    assert first["job_id"] == job_id
    listed = elenca_job()
    assert listed[0]["job_id"] == job_id
    assert listed[0]["status"] == "done"
    assert len(listed[0]["events"]) == 2


def test_register_job_appears_before_first_event():
    register_job("job-queued")
    jobs = elenca_job()
    assert jobs[0]["job_id"] == "job-queued"
    assert jobs[0]["status"] == "running"
    assert jobs[0]["events"] == []


def test_merge_job_lists_keeps_live_and_appends_runs():
    live = [
        {
            "job_id": "live-1",
            "status": "running",
            "events": [],
            "documento": "doc-live",
        }
    ]
    runs = [
        {"id": "live-1", "documento": "doc-live", "timestamp": "t0"},
        {"id": "done-2", "documento": "doc-old", "timestamp": "t1"},
    ]
    merged = merge_job_lists(live, runs)
    ids = [item["job_id"] for item in merged]
    assert ids == ["live-1", "done-2"]
    assert merged[0]["status"] == "running"
    assert merged[1]["status"] == "done"
    assert merged[1]["documento"] == "doc-old"


@pytest.mark.asyncio
async def test_reset_clears_job_history():
    await event_graph_bus.publish("j", "macro", "macro_start", {"doc_id": "d"})
    assert elenca_job()
    event_graph_bus.reset_event_bus()
    assert elenca_job() == []


@pytest.mark.asyncio
async def test_get_jobs_and_stream_replay(monkeypatch):
    monkeypatch.setattr(
        "app.api.event_graph.get_driver",
        lambda: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    job_id = "job-http-replay"
    await event_graph_bus.publish(
        job_id, "macro", "macro_done", {"doc_id": "sole"}
    )
    await event_graph_bus.publish(
        job_id, "espansione", "zona_extracted", {"zona_id": "z0"}
    )

    from app.main import app

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        listed = await client.get("/event-graph/jobs")
        assert listed.status_code == 200
        body = listed.json()
        assert body["jobs"][0]["job_id"] == job_id
        assert body["jobs"][0]["status"] == "running"
        assert body["jobs"][0]["documento"] == "sole"
        assert len(body["jobs"][0]["events"]) == 2

        await event_graph_bus.publish(
            job_id, "done", "pipeline_complete", {"doc_id": "sole"}
        )
        async with client.stream(
            "GET",
            f"/event-graph/stream?job_id={job_id}",
        ) as response:
            assert response.status_code == 200
            lines: list[str] = []
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    lines.append(line[len("data: ") :])
                if len(lines) >= 3:
                    break

    stages = [json.loads(line)["stage"] for line in lines]
    assert stages == ["macro", "espansione", "done"]


@pytest.mark.asyncio
async def test_post_documents_registers_job(monkeypatch):
    started = asyncio.Event()

    async def fake_run(doc_id, text, job_id, *, session=None, driver=None):
        started.set()
        return None

    monkeypatch.setattr(
        "app.api.event_graph.run_event_graph_ingestion",
        fake_run,
    )
    monkeypatch.setattr(
        "app.api.event_graph.get_driver",
        lambda: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    from app.main import app

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/event-graph/documents",
            json={"doc_id": "doc-api", "text": "Mario arrivò ieri."},
        )
        assert response.status_code == 200
        job_id = response.json()["job_id"]
        listed = await client.get("/event-graph/jobs")
    assert listed.status_code == 200
    ids = [item["job_id"] for item in listed.json()["jobs"]]
    assert job_id in ids
    await asyncio.wait_for(started.wait(), timeout=1)
