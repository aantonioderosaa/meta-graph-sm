"""Document list endpoint integration tests (F3.1)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from testcontainers.community.neo4j import Neo4jContainer

from app.core import neo4j_client
from app.db.schema import apply_schema_with_driver
from app.main import app
from app.pipeline import documents_engine

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

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        await session.run("MATCH (n) DETACH DELETE n")

    yield neo4j_container

    async with driver.session() as session:
        await session.run("MATCH (n) DETACH DELETE n")
    await neo4j_client.close_neo4j_driver()


async def _seed_doc(
    *,
    doc_id: str,
    chunks: int,
    facts: int,
    last_offset_hours: int = 0,
) -> None:
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        for i in range(chunks):
            await session.run(
                """
                CREATE (c:Chunk {
                  id: $cid,
                  text: $text,
                  doc_id: $doc_id,
                  created_at: datetime() - duration({hours: $hours})
                })
                """,
                cid=f"{doc_id}-c{i}",
                text=f"chunk {i} of {doc_id}",
                doc_id=doc_id,
                hours=last_offset_hours + (chunks - 1 - i),
            )
        for i in range(facts):
            await session.run(
                """
                CREATE (f:Fact {
                  id: $fid,
                  text: $text,
                  type: 'fact',
                  confidence: 1.0,
                  is_latest: true,
                  dreamed: true,
                  source_doc_id: $doc_id,
                  created_at: datetime()
                })
                """,
                fid=f"{doc_id}-f{i}",
                text=f"fact {i} of {doc_id}",
                doc_id=doc_id,
            )


@pytest.mark.asyncio
async def test_list_documents_empty(neo4j_ready):
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        docs = await documents_engine.list_documents(session)
    assert docs == []

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/documents")
    assert response.status_code == 200
    assert response.json() == {"documents": []}


@pytest.mark.asyncio
async def test_list_documents_counts_and_order(neo4j_ready):
    # older last_at (more hours offset on newest chunk)
    await _seed_doc(doc_id="doc-old", chunks=2, facts=1, last_offset_hours=10)
    await _seed_doc(doc_id="doc-new", chunks=3, facts=2, last_offset_hours=0)

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        docs = await documents_engine.list_documents(session)

    assert [d.doc_id for d in docs] == ["doc-new", "doc-old"]
    by_id = {d.doc_id: d for d in docs}
    assert by_id["doc-new"].chunk_count == 3
    assert by_id["doc-new"].fact_count == 2
    assert by_id["doc-old"].chunk_count == 2
    assert by_id["doc-old"].fact_count == 1
    assert by_id["doc-new"].last_ingested_at
    assert by_id["doc-old"].first_ingested_at

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/documents")
    assert response.status_code == 200
    body = response.json()["documents"]
    assert [d["doc_id"] for d in body] == ["doc-new", "doc-old"]
    assert body[0]["chunk_count"] == 3
    assert body[0]["fact_count"] == 2
