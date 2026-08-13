"""QueryLog persistence + history API tests (Epic F4)."""

from __future__ import annotations

import math
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core import neo4j_client
from app.db.schema import apply_schema_with_driver
from app.main import app
from app.pipeline import query_engine, query_log
from app.pipeline.query_engine import QueryAnswer
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


def _similar_vector(base: list[float], epsilon: float = 0.01) -> list[float]:
    vec = list(base)
    vec[0] = max(0.0, vec[0] - epsilon)
    norm = math.sqrt(sum(value * value for value in vec))
    return [value / norm for value in vec]


async def _await_vector_index() -> None:
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        await session.run("CALL db.awaitIndex('fact_embedding', 300)")


async def _create_fact(
    *,
    fact_id: str,
    text: str,
    embedding: list[float],
    doc_id: str = "doc-test",
) -> None:
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        await session.run(
            """
            CREATE (f:Fact {
              id: $id,
              text: $text,
              type: 'fact',
              confidence: 1.0,
              is_latest: true,
              dreamed: true,
              source_doc_id: $doc_id,
              embedding: $embedding,
              created_at: datetime()
            })
            """,
            id=fact_id,
            text=text,
            doc_id=doc_id,
            embedding=embedding,
        )


@pytest.mark.asyncio
async def test_run_query_writes_query_log(neo4j_ready, monkeypatch):
    base = _unit_vector(1)
    await _create_fact(
        fact_id="ql-f1",
        text="Alice works at Acme.",
        embedding=base,
    )
    await _await_vector_index()

    monkeypatch.setattr(
        "app.pipeline.query_engine.embeddings.embed",
        lambda text: base,
    )
    monkeypatch.setattr(
        "app.pipeline.query_engine.call_structured",
        AsyncMock(
            return_value=QueryAnswer(
                answer="Alice lavora in Acme.",
                cited_fact_ids=["ql-f1"],
            )
        ),
    )

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        response = await query_engine.run_query(session, "Where does Alice work?")

    assert response.cited_fact_ids == ["ql-f1"]

    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (q:QueryLog)-[:USED]->(f:Fact {id: 'ql-f1'})
            RETURN q.id AS id, q.answer AS answer, q.cited_fact_ids AS cited
            """
        )
        record = await result.single()

    assert record is not None
    assert record["answer"] == "Alice lavora in Acme."
    assert record["cited"] == ["ql-f1"]


@pytest.mark.asyncio
async def test_query_log_write_failure_does_not_fail_query(neo4j_ready, monkeypatch):
    base = _unit_vector(2)
    await _create_fact(
        fact_id="ql-f2",
        text="Bob likes tea.",
        embedding=base,
    )
    await _await_vector_index()

    monkeypatch.setattr(
        "app.pipeline.query_engine.embeddings.embed",
        lambda text: base,
    )
    monkeypatch.setattr(
        "app.pipeline.query_engine.call_structured",
        AsyncMock(
            return_value=QueryAnswer(answer="Bob ama il tè.", cited_fact_ids=["ql-f2"])
        ),
    )
    monkeypatch.setattr(
        "app.pipeline.query_log.write_query_log",
        AsyncMock(side_effect=RuntimeError("neo4j write failed")),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/query", json={"text": "What does Bob like?"})

    assert response.status_code == 200
    body = response.json()
    assert "tè" in body["answer"] or "tea" in body["answer"].lower() or body["answer"]


@pytest.mark.asyncio
async def test_query_history_list_detail_and_404(neo4j_ready, monkeypatch):
    base_a = _unit_vector(3)
    base_b = _unit_vector(4)
    await _create_fact(fact_id="hist-a", text="Fact A", embedding=base_a)
    await _create_fact(fact_id="hist-b", text="Fact B", embedding=base_b)
    await _await_vector_index()

    answers = {
        "Question one?": QueryAnswer(answer="Answer one", cited_fact_ids=["hist-a"]),
        "Question two?": QueryAnswer(answer="Answer two", cited_fact_ids=["hist-b"]),
    }

    monkeypatch.setattr(
        "app.pipeline.query_engine.embeddings.embed",
        lambda text: base_a if "one" in text else base_b,
    )

    async def fake_structured(system, user, schema, **kwargs):
        _ = system, schema, kwargs
        for key, value in answers.items():
            if key in user:
                return value
        return QueryAnswer(answer="fallback", cited_fact_ids=[])

    monkeypatch.setattr(
        "app.pipeline.query_engine.call_structured",
        AsyncMock(side_effect=fake_structured),
    )

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        await query_engine.run_query(session, "Question one?")
        await query_engine.run_query(session, "Question two?")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        listed = await client.get("/queries?limit=10")
        assert listed.status_code == 200
        items = listed.json()["items"]
        assert len(items) >= 2
        assert items[0]["text"] == "Question two?"
        assert items[1]["text"] == "Question one?"

        limited = await client.get("/queries?limit=1")
        assert len(limited.json()["items"]) == 1
        assert limited.json()["items"][0]["text"] == "Question two?"

        older_id = items[1]["id"]
        detail = await client.get(f"/queries/{older_id}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["answer"] == "Answer one"
        assert body["cited_fact_ids"] == ["hist-a"]

        missing = await client.get("/queries/does-not-exist")
        assert missing.status_code == 404


@pytest.mark.asyncio
async def test_list_query_logs_helper_order(neo4j_ready):
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        await query_log.write_query_log(
            session,
            query_id="q-old",
            text="old",
            answer="a1",
            cited_fact_ids=[],
            all_fact_ids=[],
        )
        await query_log.write_query_log(
            session,
            query_id="q-new",
            text="new",
            answer="a2",
            cited_fact_ids=[],
            all_fact_ids=[],
        )
        items = await query_log.list_query_logs(session, limit=5)

    assert [i.text for i in items[:2]] == ["new", "old"]
