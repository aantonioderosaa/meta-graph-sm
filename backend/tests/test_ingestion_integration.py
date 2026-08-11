"""Ingestion pipeline integration tests (E3.3–E3.6)."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from httpx import ASGITransport
from testcontainers.community.neo4j import Neo4jContainer

from app.core import event_bus, neo4j_client
from app.db.schema import apply_schema_with_driver
from app.main import app
from app.models.extraction import ExtractedFact, FactExtractionResult, FactType
from app.pipeline.ingestion import run_ingestion_pipeline

NEO4J_IMAGE = "neo4j:5.24-community"


@pytest.fixture(scope="module")
def neo4j_container():
    container = (
        Neo4jContainer(NEO4J_IMAGE)
        .with_env("NEO4J_PLUGINS", '["graph-data-science"]')
        .with_env("NEO4J_dbms_security_procedures_unrestricted", "gds.*")
    )
    container.start()
    try:
        yield container
    finally:
        container.stop()


@pytest.fixture
async def neo4j_ready(neo4j_container, monkeypatch):
    uri = neo4j_container.get_connection_url()
    user = neo4j_container.username
    password = neo4j_container.password

    monkeypatch.setattr("app.core.config.settings.NEO4J_URI", uri)
    monkeypatch.setattr("app.core.config.settings.NEO4J_USER", user)
    monkeypatch.setattr("app.core.config.settings.NEO4J_PASSWORD", password)
    monkeypatch.setattr("app.core.config.settings.AUTO_MIGRATE", False)

    apply_schema_with_driver(neo4j_container.get_driver())

    await neo4j_client.close_neo4j_driver()
    await neo4j_client.init_neo4j_driver()

    yield neo4j_container

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        await session.run("MATCH (n) DETACH DELETE n")
    await neo4j_client.close_neo4j_driver()


@pytest.fixture(autouse=True)
def clean_event_bus():
    event_bus.reset_event_bus()
    yield
    event_bus.reset_event_bus()


def _fact_result(text: str, fact_type: FactType = FactType.fact) -> FactExtractionResult:
    return FactExtractionResult(facts=[ExtractedFact(text=text, type=fact_type)])


def _noise_result() -> FactExtractionResult:
    return FactExtractionResult(facts=[])


@pytest.mark.asyncio
async def test_ingestion_writes_chunks_facts_and_derived_from(neo4j_ready, monkeypatch):
    calls: list[str] = []

    async def mock_extract(chunk_text: str, job_id: str | None = None) -> FactExtractionResult:
        calls.append(chunk_text)
        if "noise" in chunk_text.lower():
            return _noise_result()
        return _fact_result(f"Fact from: {chunk_text[:40]}")

    monkeypatch.setattr("app.pipeline.ingestion.extraction.extract_facts", mock_extract)

    doc_text = (
        "Alice works at Acme Corp.\n"
        "Bob prefers dark chocolate.\n"
        "ok capito noise filler only."
    )
    job_id = "job-integration-1"
    await run_ingestion_pipeline("doc-int-1", doc_text, job_id)

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        chunk_count = await session.run("MATCH (c:Chunk) RETURN count(c) AS n")
        chunk_record = await chunk_count.single()
        assert chunk_record is not None
        assert chunk_record["n"] == len(calls)

        fact_count = await session.run("MATCH (f:Fact) RETURN count(f) AS n")
        fact_record = await fact_count.single()
        assert fact_record is not None
        assert fact_record["n"] == 2

        derived = await session.run(
            """
            MATCH (f:Fact)-[:DERIVED_FROM]->(c:Chunk)
            RETURN count(f) AS n
            """
        )
        derived_record = await derived.single()
        assert derived_record is not None
        assert derived_record["n"] == 2

        dreamed = await session.run(
            "MATCH (f:Fact) WHERE f.dreamed = false RETURN count(f) AS n"
        )
        dreamed_record = await dreamed.single()
        assert dreamed_record is not None
        assert dreamed_record["n"] == 2


@pytest.mark.asyncio
async def test_ingestion_sse_events_and_pipeline_complete(neo4j_ready, monkeypatch):
    async def mock_extract(chunk_text: str, job_id: str | None = None) -> FactExtractionResult:
        if "noise" in chunk_text.lower():
            return _noise_result()
        return _fact_result("Structured fact.")

    monkeypatch.setattr("app.pipeline.ingestion.extraction.extract_facts", mock_extract)

    job_id = "job-sse-ingest"
    queue = await event_bus.subscribe(job_id)

    doc_text = "Line one about Paris.\nLine two about London.\nok capito noise filler."
    pipeline_task = asyncio.create_task(run_ingestion_pipeline("doc-sse", doc_text, job_id))
    await pipeline_task

    events = []
    while True:
        event = await asyncio.wait_for(queue.get(), timeout=5)
        events.append(event)
        if event.get("stage") == "done":
            break

    chunk_events = [event for event in events if event["event"] == "chunk_created"]
    assert len(chunk_events) == 3

    noise_events = [event for event in events if event["event"] == "chunk_discarded_noise"]
    assert len(noise_events) == 1

    fact_events = [event for event in events if event["event"] == "fact_extracted"]
    assert len(fact_events) == 2

    complete = events[-1]
    assert complete["event"] == "pipeline_complete"
    assert complete["payload"]["stats"]["chunks"] == 3
    assert complete["payload"]["stats"]["facts"] == 2


@pytest.mark.asyncio
async def test_post_documents_endpoint_with_mocked_pipeline(monkeypatch):
    ran = asyncio.Event()

    async def mock_pipeline(doc_id: str, text: str, job_id: str) -> None:
        _ = doc_id, text, job_id
        ran.set()

    monkeypatch.setattr("app.api.documents.run_ingestion_pipeline", mock_pipeline)

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/documents",
            json={"doc_id": "doc-api", "text": "Sample text."},
        )
        assert response.status_code == 202
        assert response.json()["job_id"]

        await asyncio.wait_for(ran.wait(), timeout=2)
