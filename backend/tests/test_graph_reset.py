"""R3.1 / R3.5 — DELETE /graph resets all data, preserves schema."""

from __future__ import annotations

import asyncio
import time

import pytest
from httpx import ASGITransport, AsyncClient

from app.core import event_bus, neo4j_client
from app.db.schema import apply_schema_with_driver
from app.main import app
from app.models.node_extraction import (
    ConceptResult,
    EntityRelationExtractionResult,
    EntityRelationTriple,
    EventEntityExtractionResult,
    EventRelationClassification,
    EventRelationExtractionResult,
    EventRelationLabel,
    NodeDedupResult,
)
from app.models.relations import RelationClassification, RelationLabel
from app.pipeline.node_query_engine import NodeQueryAnswer
from tests.neo4j_gds import neo4j_gds_container

EMBEDDING_DIM = 768
DREAM_JOB = "job-r35-dream"


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

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        await session.run("MATCH (n) DETACH DELETE n")

    yield neo4j_container

    async with driver.session() as session:
        await session.run("MATCH (n) DETACH DELETE n")
    await neo4j_client.close_neo4j_driver()


@pytest.fixture(autouse=True)
def clean_event_bus():
    event_bus.reset_event_bus()
    yield
    event_bus.reset_event_bus()


def _unit_vector(index: int, dim: int = EMBEDDING_DIM) -> list[float]:
    vector = [0.0] * dim
    vector[index % dim] = 1.0
    return vector


async def _seed_populated_graph() -> None:
    driver = neo4j_client.get_driver()
    emb = _unit_vector(0)
    emb_a = _unit_vector(1)
    emb_b = _unit_vector(2)
    async with driver.session() as session:
        await session.run(
            """
            CREATE (c:Chunk {
              id: 'chunk-r31', doc_id: 'doc-r31', text: 'chunk',
              embedding: $emb, created_at: datetime()
            })
            CREATE (a:Node {
              id: 'node-a', name: 'Alice', type: 'entity', dreamed: true,
              merged_into: null, embedding: $emb_a, created_at: datetime()
            })-[:DERIVED_FROM]->(c)
            CREATE (b:Node {
              id: 'node-b', name: 'product launch', type: 'event', dreamed: true,
              merged_into: null, embedding: $emb_b, created_at: datetime()
            })-[:DERIVED_FROM]->(c)
            CREATE (k:Concept {id: 'concept-tech', name: 'technology', embedding: $emb})
            CREATE (a)-[:Relation {
              relation: 'participates in', normalized_relation: 'participates',
              embedding: $emb, is_latest: true, created_at: datetime()
            }]->(b)
            CREATE (a)-[:HAS_CONCEPT]->(k)
            CREATE (q:NodeQueryLog {
              id: 'nql-r31', text: 'chi è Alice?', answer: 'Alice.',
              cited_node_ids: ['node-a'], created_at: datetime()
            })
            """,
            emb=emb,
            emb_a=emb_a,
            emb_b=emb_b,
        )


async def _schema_fingerprint(session) -> tuple[list[str], list[str]]:
    constraints = await session.run("SHOW CONSTRAINTS YIELD name RETURN name ORDER BY name")
    indexes = await session.run("SHOW INDEXES YIELD name RETURN name ORDER BY name")
    c_names = [record["name"] async for record in constraints]
    i_names = [record["name"] async for record in indexes]
    return c_names, i_names


async def _graph_counts(session) -> dict[str, int]:
    result = await session.run(
        """
        OPTIONAL MATCH (c:Chunk) WITH count(c) AS chunks
        OPTIONAL MATCH (n:Node) WITH chunks, count(n) AS nodes
        OPTIONAL MATCH (k:Concept) WITH chunks, nodes, count(k) AS concepts
        OPTIONAL MATCH (q:NodeQueryLog) WITH chunks, nodes, concepts, count(q) AS logs
        OPTIONAL MATCH ()-[r]->()
        RETURN chunks, nodes, concepts, logs, count(r) AS rels
        """
    )
    rec = await result.single()
    assert rec is not None
    return {
        "chunks": rec["chunks"],
        "nodes": rec["nodes"],
        "concepts": rec["concepts"],
        "logs": rec["logs"],
        "rels": rec["rels"],
    }


async def _wait_until(cypher: str, minimum: int, timeout: float = 60) -> int:
    driver = neo4j_client.get_driver()
    deadline = time.monotonic() + timeout
    last = 0
    while time.monotonic() < deadline:
        async with driver.session() as session:
            rec = await (await session.run(cypher)).single()
            last = int(rec["n"]) if rec else 0
            if last >= minimum:
                return last
        await asyncio.sleep(0.1)
    raise AssertionError(f"timed out waiting for {cypher!r} >= {minimum}, last={last}")


def _patch_node_llm(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.pipeline.ingestion.embeddings.embed",
        lambda text: _unit_vector(0),
    )
    monkeypatch.setattr(
        "app.pipeline.ingestion.embeddings.embed_batch",
        lambda texts: [_unit_vector(0) for _ in texts],
    )
    monkeypatch.setattr(
        "app.pipeline.embeddings.embed",
        lambda text: _unit_vector(0),
    )
    monkeypatch.setattr(
        "app.pipeline.embeddings.embed_batch",
        lambda texts: [_unit_vector(0) for _ in texts],
    )

    async def mock_entity(chunk_text: str, job_id: str | None = None):
        _ = chunk_text, job_id
        return EntityRelationExtractionResult(
            triples=[
                EntityRelationTriple(head="Wind", relation="argues_with", tail="Sun"),
            ]
        )

    async def empty_event_entity(chunk_text: str, job_id: str | None = None):
        _ = chunk_text, job_id
        return EventEntityExtractionResult(participations=[])

    async def empty_event_rel(chunk_text: str, job_id: str | None = None):
        _ = chunk_text, job_id
        return EventRelationExtractionResult(triples=[])

    async def empty_concepts(*_args, **_kwargs):
        return ConceptResult(concepts=[])

    async def fake_call_structured(system, user, model, temperature=0, job_id=None):
        _ = system, user, temperature, job_id
        if model is NodeDedupResult:
            return NodeDedupResult(duplicate_of=None)
        if model is EventRelationClassification:
            return EventRelationClassification(label=EventRelationLabel.none)
        if model is NodeQueryAnswer:
            return NodeQueryAnswer(answer="The wind and the sun argued.", cited_node_ids=[])
        raise AssertionError(f"unexpected structured model {model}")

    async def fake_classify_relation(*_args, **_kwargs):
        return RelationClassification(relation=RelationLabel.none)

    monkeypatch.setattr("app.pipeline.node_extraction.extract_entity_relations", mock_entity)
    monkeypatch.setattr(
        "app.pipeline.node_extraction.extract_event_entities", empty_event_entity
    )
    monkeypatch.setattr("app.pipeline.node_extraction.extract_event_relations", empty_event_rel)
    monkeypatch.setattr("app.pipeline.node_extraction.extract_entity_concepts", empty_concepts)
    monkeypatch.setattr("app.pipeline.node_extraction.extract_event_concepts", empty_concepts)
    monkeypatch.setattr("app.pipeline.node_resolution.call_structured", fake_call_structured)
    monkeypatch.setattr(
        "app.pipeline.event_relation_resolution.call_structured", fake_call_structured
    )
    monkeypatch.setattr(
        "app.pipeline.entity_relation_resolution.classify_relation", fake_classify_relation
    )
    monkeypatch.setattr("app.pipeline.node_query_engine.call_structured", fake_call_structured)
    monkeypatch.setattr(
        "app.pipeline.node_query_engine.embeddings.embed",
        lambda text: _unit_vector(0),
    )


@pytest.mark.asyncio
async def test_delete_graph_clears_data_keeps_schema(neo4j_ready):
    await _seed_populated_graph()
    driver = neo4j_client.get_driver()

    async with driver.session() as session:
        before_constraints, before_indexes = await _schema_fingerprint(session)
        counts = await _graph_counts(session)
        assert counts["chunks"] >= 1
        assert counts["nodes"] >= 2
        assert counts["concepts"] >= 1
        assert counts["logs"] >= 1
        assert counts["rels"] >= 1

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete("/graph")

    assert response.status_code == 200
    assert response.json() == {"deleted": True}

    async with driver.session() as session:
        after = await _graph_counts(session)
        assert after == {"chunks": 0, "nodes": 0, "concepts": 0, "logs": 0, "rels": 0}

        after_constraints, after_indexes = await _schema_fingerprint(session)
        assert after_constraints == before_constraints
        assert after_indexes == before_indexes


@pytest.mark.enable_node_extraction
@pytest.mark.asyncio
async def test_e2e_ingest_dream_query_then_reset_is_empty(neo4j_ready, monkeypatch):
    """R3.5: ingest → dreaming → query (NodeQueryLog) → DELETE /graph → all counts zero."""
    _patch_node_llm(monkeypatch)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        ingest_resp = await client.post(
            "/documents",
            json={
                "doc_id": "doc-r35",
                "text": "The wind and the sun argued about who was stronger.",
            },
        )
        assert ingest_resp.status_code == 202
        assert ingest_resp.json()["job_id"]
        await _wait_until(
            "MATCH (n:Node)-[:DERIVED_FROM]->(:Chunk {doc_id: 'doc-r35'}) "
            "RETURN count(n) AS n",
            2,
        )
        await asyncio.sleep(0.3)

        driver = neo4j_client.get_driver()
        async with driver.session() as session:
            node_ids_result = await session.run(
                "MATCH (n:Node)-[:DERIVED_FROM]->(:Chunk {doc_id: 'doc-r35'}) "
                "RETURN n.id AS id ORDER BY n.id"
            )
            node_ids = [rec["id"] async for rec in node_ids_result]
        assert len(node_ids) >= 2

        dream_queue = await event_bus.subscribe(DREAM_JOB)
        dream_resp = await client.post("/dreaming/run", json={"job_id": DREAM_JOB})
        assert dream_resp.status_code == 202
        while True:
            event = await asyncio.wait_for(dream_queue.get(), timeout=60)
            if event.get("stage") == "done":
                break
            if event.get("stage") == "failed":
                raise AssertionError(event)

        query_resp = await client.post(
            "/graph/query", json={"text": "What did the wind and sun do?"}
        )
        assert query_resp.status_code == 200
        assert "nodes_used" in query_resp.json()
        assert "facts_used" not in query_resp.json()

        async with driver.session() as session:
            before = await _graph_counts(session)
        assert before["chunks"] >= 1
        assert before["nodes"] >= 2
        assert before["logs"] >= 1
        assert before["rels"] >= 1

        reset_resp = await client.delete("/graph")
        assert reset_resp.status_code == 200
        assert reset_resp.json() == {"deleted": True}

    async with driver.session() as session:
        after = await _graph_counts(session)
        assert after == {"chunks": 0, "nodes": 0, "concepts": 0, "logs": 0, "rels": 0}
