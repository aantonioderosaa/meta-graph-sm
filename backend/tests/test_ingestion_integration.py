"""Ingestion pipeline integration tests (E3.3–E3.6) — Node-only."""

from __future__ import annotations

import asyncio
import hashlib

import httpx
import pytest
from httpx import ASGITransport

from app.core import event_bus, neo4j_client
from app.db.schema import apply_schema_with_driver
from app.main import app
from app.models.kernel import EntityKernelType, RelationKernelType
from app.models.node_extraction import (
    ConceptResult,
    EntityExtractionResult,
    EventEntityExtractionResult,
    EventRelationExtractionResult,
    ExtractedEntity,
    PairRelationDecision,
)
from app.pipeline.ingestion import run_ingestion_pipeline
from tests.neo4j_gds import neo4j_gds_container

EMBEDDING_DIM = 768


@pytest.fixture(scope="module")
def neo4j_container():
    container = neo4j_gds_container()
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


def _unit_vector(name: str) -> list[float]:
    vec = [0.0] * EMBEDDING_DIM
    idx = int(hashlib.md5(name.encode("utf-8")).hexdigest(), 16) % EMBEDDING_DIM
    vec[idx] = 1.0
    return vec


def _patch_embeddings(monkeypatch) -> None:
    monkeypatch.setattr("app.pipeline.ingestion.embeddings.embed", _unit_vector)
    monkeypatch.setattr(
        "app.pipeline.ingestion.embeddings.embed_batch",
        lambda texts: [_unit_vector(text) for text in texts],
    )


def _patch_extractors(monkeypatch) -> None:
    async def mock_entities(
        chunk_text: str, job_id: str | None = None, corpus_summary: str = ""
    ):
        _ = job_id, corpus_summary
        if "noise" in chunk_text.lower():
            return EntityExtractionResult(entities=[])
        words = chunk_text.split()
        head = words[0] if words else "Someone"
        tail = words[-1].rstrip(".") if len(words) > 1 else "Somewhere"
        names = [head]
        if tail.casefold() != head.casefold():
            names.append(tail)
        return EntityExtractionResult(
            entities=[
                ExtractedEntity(
                    name=name,
                    summary=f"Entity mentioned as {name}.",
                    kernel_category=(
                        EntityKernelType.Agente
                        if i == 0
                        else EntityKernelType.CostruttoSociale
                    ),
                )
                for i, name in enumerate(names)
            ]
        )

    async def mock_pair(*_args, **kwargs):
        _ = kwargs
        name_a = _args[1] if len(_args) > 1 else "Someone"
        name_b = _args[3] if len(_args) > 3 else "Somewhere"
        return PairRelationDecision(
            related=True,
            relation="related_to",
            kernel_parent=RelationKernelType.SocialeIntenzionale,
            witness_source=name_a,
            witness_target=name_b,
        )

    async def mock_event_entity(chunk_text: str, job_id: str | None = None):
        _ = chunk_text, job_id
        return EventEntityExtractionResult(participations=[])

    async def mock_event_rel(chunk_text: str, job_id: str | None = None):
        _ = chunk_text, job_id
        return EventRelationExtractionResult(triples=[])

    async def mock_concepts(*_args, **_kwargs):
        return ConceptResult(concepts=[])

    async def mock_corpus(session, document_text, job_id) -> str:
        _ = session, document_text, job_id
        return "Integration test corpus."

    monkeypatch.setattr("app.pipeline.node_extraction.extract_entities", mock_entities)
    monkeypatch.setattr("app.pipeline.node_extraction.extract_pair_relation", mock_pair)
    monkeypatch.setattr("app.pipeline.node_extraction.extract_event_entities", mock_event_entity)
    monkeypatch.setattr("app.pipeline.node_extraction.extract_event_relations", mock_event_rel)
    monkeypatch.setattr("app.pipeline.node_extraction.extract_entity_concepts", mock_concepts)
    monkeypatch.setattr("app.pipeline.node_extraction.extract_event_concepts", mock_concepts)
    monkeypatch.setattr("app.pipeline.ingestion.update_corpus_context", mock_corpus)


@pytest.mark.enable_node_extraction
@pytest.mark.asyncio
async def test_ingestion_writes_chunks_nodes_and_derived_from(neo4j_ready, monkeypatch):
    _patch_embeddings(monkeypatch)
    _patch_extractors(monkeypatch)

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
        assert chunk_record["n"] == 3

        node_count = await session.run("MATCH (n:Node) RETURN count(n) AS n")
        node_record = await node_count.single()
        assert node_record is not None
        assert node_record["n"] == 4

        fact_count = await session.run("MATCH (f:Fact) RETURN count(f) AS n")
        fact_record = await fact_count.single()
        assert fact_record is not None
        assert fact_record["n"] == 0

        derived = await session.run(
            """
            MATCH (n:Node)-[:DERIVED_FROM]->(c:Chunk)
            RETURN count(n) AS n
            """
        )
        derived_record = await derived.single()
        assert derived_record is not None
        assert derived_record["n"] == 4

        dreamed = await session.run(
            "MATCH (n:Node) WHERE n.dreamed = false RETURN count(n) AS n"
        )
        dreamed_record = await dreamed.single()
        assert dreamed_record is not None
        assert dreamed_record["n"] == 4


@pytest.mark.enable_node_extraction
@pytest.mark.asyncio
async def test_ingestion_sse_events_and_pipeline_complete(neo4j_ready, monkeypatch):
    _patch_embeddings(monkeypatch)
    _patch_extractors(monkeypatch)

    job_id = "job-sse-ingest"
    queue = await event_bus.subscribe(job_id)

    doc_text = "Line one about Paris.\nLine two about London.\nok capito noise filler."
    await run_ingestion_pipeline("doc-sse", doc_text, job_id)

    events = []
    while True:
        event = await asyncio.wait_for(queue.get(), timeout=5)
        events.append(event)
        if event.get("stage") == "done":
            break

    chunk_events = [event for event in events if event["event"] == "chunk_created"]
    assert len(chunk_events) == 3

    entity_events = [event for event in events if event["event"] == "entity_extracted"]
    assert len(entity_events) == 4

    assert not [event for event in events if event["event"] == "fact_extracted"]
    assert not [event for event in events if event["event"] == "chunk_discarded_noise"]

    complete = events[-1]
    assert complete["event"] == "pipeline_complete"
    assert complete["payload"]["stats"]["chunks"] == 3
    assert complete["payload"]["stats"]["nodes"] == 4
    assert "facts" not in complete["payload"]["stats"]


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
