"""R3.1 / R3.5 — DELETE /graph resets all data, preserves schema."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from testcontainers.community.neo4j import Neo4jContainer

from app.core import neo4j_client
from app.db.schema import apply_schema_with_driver
from app.main import app
from app.models.extraction import ExtractedFact, FactExtractionResult, FactType
from app.models.relations import RelationClassification, RelationLabel
from app.pipeline.dreaming import run_dreaming_pipeline
from app.pipeline.ingestion import run_ingestion_pipeline
from app.pipeline.query_engine import QueryAnswer

NEO4J_IMAGE = "neo4j:5.24-community"
EMBEDDING_DIM = 768


@pytest.fixture(scope="module")
def neo4j_container():
    container = Neo4jContainer(NEO4J_IMAGE)
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


def _unit_vector(index: int, dim: int = EMBEDDING_DIM) -> list[float]:
    vector = [0.0] * dim
    vector[index % dim] = 1.0
    return vector


async def _seed_populated_graph() -> None:
    driver = neo4j_client.get_driver()
    emb_a = _unit_vector(1)
    emb_b = _unit_vector(2)
    async with driver.session() as session:
        await session.run(
            """
            CREATE (c:Chunk {
              id: 'chunk-r31', doc_id: 'doc-r31', text: 'chunk',
              embedding: $emb, created_at: datetime()
            })
            CREATE (a:Fact {
              id: 'fact-a', text: 'Fact A', type: 'fact', is_latest: true,
              confidence: 1.0, dreamed: true, source_doc_id: 'doc-r31',
              embedding: $emb_a, created_at: datetime()
            })-[:DERIVED_FROM]->(c)
            CREATE (b:Fact {
              id: 'fact-b', text: 'Fact B', type: 'fact', is_latest: false,
              confidence: 1.0, dreamed: true, source_doc_id: 'doc-r31',
              embedding: $emb_b, created_at: datetime()
            })-[:DERIVED_FROM]->(c)
            CREATE (a)-[:UPDATES {created_at: datetime()}]->(b)
            CREATE (a)-[:EXTENDS {created_at: datetime()}]->(b)
            CREATE (q:QueryLog {
              id: 'ql-r31', question: 'q?', answer: 'a',
              cited_fact_ids: ['fact-a'], created_at: datetime()
            })
            """,
            emb=_unit_vector(0),
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
        OPTIONAL MATCH (f:Fact) WITH chunks, count(f) AS facts
        OPTIONAL MATCH (q:QueryLog) WITH chunks, facts, count(q) AS logs
        OPTIONAL MATCH ()-[r]->()
        RETURN chunks, facts, logs, count(r) AS rels
        """
    )
    rec = await result.single()
    assert rec is not None
    return {
        "chunks": rec["chunks"],
        "facts": rec["facts"],
        "logs": rec["logs"],
        "rels": rec["rels"],
    }


@pytest.mark.asyncio
async def test_delete_graph_clears_data_keeps_schema(neo4j_ready):
    await _seed_populated_graph()
    driver = neo4j_client.get_driver()

    async with driver.session() as session:
        before_constraints, before_indexes = await _schema_fingerprint(session)
        counts = await session.run(
            """
            OPTIONAL MATCH (c:Chunk) WITH count(c) AS chunks
            OPTIONAL MATCH (f:Fact) WITH chunks, count(f) AS facts
            OPTIONAL MATCH (q:QueryLog) WITH chunks, facts, count(q) AS logs
            OPTIONAL MATCH ()-[u:UPDATES]->() WITH chunks, facts, logs, count(u) AS updates
            OPTIONAL MATCH ()-[e:EXTENDS]->()
            RETURN chunks, facts, logs, updates, count(e) AS extends
            """
        )
        before = await counts.single()
        assert before is not None
        assert before["chunks"] >= 1
        assert before["facts"] >= 2
        assert before["logs"] >= 1
        assert before["updates"] >= 1
        assert before["extends"] >= 1

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete("/graph")

    assert response.status_code == 200
    assert response.json() == {"deleted": True}

    async with driver.session() as session:
        after = await _graph_counts(session)
        assert after == {"chunks": 0, "facts": 0, "logs": 0, "rels": 0}

        after_constraints, after_indexes = await _schema_fingerprint(session)
        assert after_constraints == before_constraints
        assert after_indexes == before_indexes


@pytest.mark.asyncio
async def test_e2e_ingest_dream_query_then_reset_is_empty(neo4j_ready, monkeypatch):
    """R3.5: ingest → dreaming → query (QueryLog) → DELETE /graph → all counts zero."""

    async def mock_extract(chunk_text: str, job_id: str | None = None) -> FactExtractionResult:
        _ = job_id
        return FactExtractionResult(
            facts=[
                ExtractedFact(text="The wind blew hard.", type=FactType.episode),
                ExtractedFact(text="The sun shone warmly.", type=FactType.episode),
            ]
        )

    emb_counter = {"n": 0}

    def mock_embed_batch(texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for _ in texts:
            out.append(_unit_vector(emb_counter["n"]))
            emb_counter["n"] += 1
        return out

    def mock_embed(text: str) -> list[float]:
        _ = text
        return _unit_vector(0)

    monkeypatch.setattr("app.pipeline.ingestion.extraction.extract_facts", mock_extract)
    monkeypatch.setattr("app.pipeline.ingestion.embeddings.embed_batch", mock_embed_batch)
    monkeypatch.setattr("app.pipeline.dreaming.embeddings.embed", mock_embed)

    await run_ingestion_pipeline(
        "doc-r35",
        "The wind and the sun argued about who was stronger.",
        "job-r35-ingest",
    )

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        fact_ids_result = await session.run(
            "MATCH (f:Fact {source_doc_id: 'doc-r35'}) RETURN f.id AS id ORDER BY f.id"
        )
        fact_ids = [rec["id"] async for rec in fact_ids_result]
    assert len(fact_ids) >= 2

    async def mock_groups(doc_id=None):
        _ = doc_id
        return [[fid] for fid in fact_ids]

    async def mock_classify(n_text, v_text, job_id=None, **kwargs):
        _ = n_text, v_text, job_id, kwargs
        return RelationClassification(relation=RelationLabel.extends)

    monkeypatch.setattr(
        "app.pipeline.dreaming.grouping.group_fresh_facts", mock_groups
    )
    monkeypatch.setattr(
        "app.pipeline.dreaming.relations.classify_relation", mock_classify
    )
    monkeypatch.setattr(
        "app.pipeline.dreaming.reconcile.reconcile_scoped",
        AsyncMock(return_value=0),
    )

    await run_dreaming_pipeline("job-r35-dream")

    monkeypatch.setattr(
        "app.pipeline.query_engine.embeddings.embed",
        lambda text: _unit_vector(0),
    )
    monkeypatch.setattr(
        "app.pipeline.query_engine.call_structured",
        AsyncMock(
            return_value=QueryAnswer(
                answer="The wind and the sun argued.",
                cited_fact_ids=fact_ids[:1],
            )
        ),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        query_resp = await client.post(
            "/query", json={"text": "What did the wind and sun do?"}
        )
        assert query_resp.status_code == 200

        async with driver.session() as session:
            before = await _graph_counts(session)
        assert before["chunks"] >= 1
        assert before["facts"] >= 2
        assert before["logs"] >= 1
        assert before["rels"] >= 1  # DERIVED_FROM + EXTENDS (+ USED)

        reset_resp = await client.delete("/graph")
        assert reset_resp.status_code == 200
        assert reset_resp.json() == {"deleted": True}

    async with driver.session() as session:
        after = await _graph_counts(session)
        assert after == {"chunks": 0, "facts": 0, "logs": 0, "rels": 0}
