"""Query engine integration tests (Epic 5)."""

from __future__ import annotations

import math
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from testcontainers.community.neo4j import Neo4jContainer

from app.core import neo4j_client
from app.db.schema import apply_schema_with_driver
from app.main import app
from app.pipeline import query_engine
from app.pipeline.query_engine import QueryAnswer
from app.pipeline.reconcile import reconcile

NEO4J_IMAGE = "neo4j:5.24-community"
EMBEDDING_DIM = 768


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
    is_latest: bool = True,
    dreamed: bool = True,
    fact_type: str = "fact",
) -> None:
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        await session.run(
            """
            CREATE (f:Fact {
              id: $id,
              text: $text,
              type: $type,
              is_latest: $is_latest,
              confidence: 1.0,
              dreamed: $dreamed,
              source_doc_id: $doc_id,
              embedding: $embedding,
              created_at: datetime()
            })
            """,
            id=fact_id,
            text=text,
            type=fact_type,
            is_latest=is_latest,
            dreamed=dreamed,
            doc_id=doc_id,
            embedding=embedding,
        )


async def _create_chunk_and_link(
    *,
    chunk_id: str,
    fact_id: str,
    doc_id: str,
    text: str,
) -> None:
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        await session.run(
            """
            CREATE (c:Chunk {
              id: $chunk_id,
              doc_id: $doc_id,
              text: $text,
              embedding: $embedding,
              created_at: datetime()
            })
            WITH c
            MATCH (f:Fact {id: $fact_id})
            CREATE (f)-[:DERIVED_FROM]->(c)
            """,
            chunk_id=chunk_id,
            doc_id=doc_id,
            text=text,
            embedding=_unit_vector(0),
            fact_id=fact_id,
        )


async def _link_updates(src: str, tgt: str) -> None:
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        await session.run(
            """
            MATCH (n:Fact {id: $src}), (v:Fact {id: $tgt})
            CREATE (n)-[:UPDATES {created_at: datetime()}]->(v)
            """,
            src=src,
            tgt=tgt,
        )


async def _link_extends(src: str, tgt: str) -> None:
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        await session.run(
            """
            MATCH (n:Fact {id: $src}), (v:Fact {id: $tgt})
            CREATE (n)-[:EXTENDS {created_at: datetime()}]->(v)
            """,
            src=src,
            tgt=tgt,
        )


@pytest.mark.asyncio
async def test_get_fact_detail_with_provenance(neo4j_ready):
    await _create_fact(fact_id="f1", text="Alice works at Acme.", embedding=_unit_vector(1))
    await _create_chunk_and_link(
        chunk_id="c1", fact_id="f1", doc_id="doc-test", text="Alice joined Acme Corp last year."
    )

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        detail = await query_engine.get_fact_detail(session, "f1")
        missing = await query_engine.get_fact_detail(session, "nope")

    assert detail is not None
    assert detail.id == "f1"
    assert detail.text == "Alice works at Acme."
    assert detail.source_doc_id == "doc-test"
    assert len(detail.provenance) == 1
    assert detail.provenance[0].chunk_id == "c1"
    assert missing is None


@pytest.mark.asyncio
async def test_get_fact_history_chain_and_isolated(neo4j_ready):
    """Criterio milestone1.md §8: query storica ricostruisce l'evoluzione via UPDATES."""
    base = _unit_vector(2)
    await _create_fact(
        fact_id="A", text="Alice at Beta", embedding=base, is_latest=False
    )
    await _create_fact(
        fact_id="B", text="Alice at Gamma", embedding=_similar_vector(base), is_latest=False
    )
    await _create_fact(
        fact_id="C", text="Alice at Acme", embedding=_similar_vector(base, 0.02), is_latest=True
    )
    await _link_updates("B", "A")
    await _link_updates("C", "B")
    await _create_fact(fact_id="solo", text="Isolated fact", embedding=_unit_vector(9))

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        history = await query_engine.get_fact_history(session, "C")
        isolated = await query_engine.get_fact_history(session, "solo")
        missing = await query_engine.get_fact_history(session, "missing")

    assert history is not None
    assert [e.id for e in history.facts] == ["C", "B", "A"]
    assert [e.path_length for e in history.facts] == [0, 1, 2]
    assert history.facts[0].is_latest is True
    assert history.facts[1].is_latest is False
    assert history.facts[2].is_latest is False

    assert isolated is not None
    assert len(isolated.facts) == 1
    assert isolated.facts[0].id == "solo"
    assert isolated.facts[0].path_length == 0
    assert missing is None


@pytest.mark.asyncio
async def test_query_current_excludes_historical_after_replaces(neo4j_ready, monkeypatch):
    """Criterio milestone1.md §8: la query corrente restituisce solo il nuovo."""
    base = _unit_vector(3)
    await _create_fact(
        fact_id="old",
        text="Alice works at Beta Inc.",
        embedding=base,
        is_latest=False,
    )
    await _create_fact(
        fact_id="new",
        text="Alice works at Acme Corp.",
        embedding=_similar_vector(base),
        is_latest=True,
    )
    await _link_updates("new", "old")
    await _await_vector_index()

    monkeypatch.setattr(
        "app.pipeline.query_engine.embeddings.embed",
        lambda text: _similar_vector(base, 0.005),
    )
    monkeypatch.setattr(
        "app.pipeline.query_engine.call_structured",
        AsyncMock(return_value=QueryAnswer(answer="Alice works at Acme Corp.")),
    )

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        response = await query_engine.run_query(session, "Where does Alice work?")

    used_ids = {f.id for f in response.facts_used}
    assert "new" in used_ids
    assert "old" not in used_ids
    assert response.answer


@pytest.mark.asyncio
async def test_query_expands_extends_neighbors(neo4j_ready, monkeypatch):
    base = _unit_vector(4)
    await _create_fact(
        fact_id="main",
        text="Alice works at Acme.",
        embedding=base,
        is_latest=True,
    )
    await _create_fact(
        fact_id="extra",
        text="Alice prefers remote work.",
        embedding=_unit_vector(5),
        is_latest=True,
        fact_type="preference",
    )
    await _link_extends("extra", "main")
    await _await_vector_index()

    monkeypatch.setattr(
        "app.pipeline.query_engine.embeddings.embed",
        lambda text: base,
    )
    monkeypatch.setattr(
        "app.pipeline.query_engine.call_structured",
        AsyncMock(return_value=QueryAnswer(answer="Alice works remotely at Acme.")),
    )

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        response = await query_engine.run_query(session, "Tell me about Alice")

    used_ids = {f.id for f in response.facts_used}
    assert "main" in used_ids
    assert "extra" in used_ids
    rel_types = {r.type for r in response.subgraph.relationships}
    assert "extends" in rel_types


@pytest.mark.asyncio
async def test_query_empty_facts_returns_explicit_answer(neo4j_ready, monkeypatch):
    monkeypatch.setattr(
        "app.pipeline.query_engine.embeddings.embed",
        lambda text: _unit_vector(50),
    )

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        response = await query_engine.run_query(session, "Unrelated question about zebras")

    assert response.facts_used == []
    assert response.cited_fact_ids == []
    assert "Nessun fatto rilevante" in response.answer
    assert response.subgraph.nodes == []


@pytest.mark.asyncio
async def test_cited_fact_ids_filters_invented_and_falls_back(neo4j_ready, monkeypatch):
    """F1.3: invented IDs dropped; empty cited_fact_ids falls back to facts_used."""
    base = _unit_vector(20)
    await _create_fact(
        fact_id="cite-a",
        text="Alice works at Acme.",
        embedding=base,
        is_latest=True,
    )
    await _create_fact(
        fact_id="cite-b",
        text="Alice prefers remote work.",
        embedding=_unit_vector(21),
        is_latest=True,
        fact_type="preference",
    )
    await _link_extends("cite-b", "cite-a")
    await _await_vector_index()

    monkeypatch.setattr(
        "app.pipeline.query_engine.embeddings.embed",
        lambda text: base,
    )

    # Invented ID must be filtered out; only real ids remain.
    monkeypatch.setattr(
        "app.pipeline.query_engine.call_structured",
        AsyncMock(
            return_value=QueryAnswer(
                answer="Alice lavora in Acme e preferisce il remoto.",
                cited_fact_ids=["cite-a", "invented-id", "cite-typo"],
            )
        ),
    )

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        filtered = await query_engine.run_query(session, "Tell me about Alice")

    assert filtered.cited_fact_ids == ["cite-a"]
    assert "invented-id" not in filtered.cited_fact_ids
    for fid in filtered.facts_used:
        assert fid.id not in filtered.answer  # AC: no IDs in answer text
    assert set(filtered.cited_fact_ids).issubset({f.id for f in filtered.facts_used})

    # Empty cited_fact_ids + non-empty facts_used → fallback to all facts_used.
    monkeypatch.setattr(
        "app.pipeline.query_engine.call_structured",
        AsyncMock(
            return_value=QueryAnswer(
                answer="Alice lavora in Acme e preferisce il remoto.",
                cited_fact_ids=[],
            )
        ),
    )
    async with driver.session() as session:
        fallback = await query_engine.run_query(session, "Tell me about Alice")

    assert set(fallback.cited_fact_ids) == {f.id for f in fallback.facts_used}
    assert fallback.cited_fact_ids  # non-empty when facts exist


@pytest.mark.asyncio
async def test_cited_fact_ids_empty_on_llm_failure(neo4j_ready, monkeypatch):
    """F1.3: LLM failure path returns cited_fact_ids=[] explicitly."""
    base = _unit_vector(22)
    await _create_fact(
        fact_id="fail-a",
        text="Bob likes tea.",
        embedding=base,
        is_latest=True,
    )
    await _await_vector_index()

    monkeypatch.setattr(
        "app.pipeline.query_engine.embeddings.embed",
        lambda text: base,
    )
    monkeypatch.setattr(
        "app.pipeline.query_engine.call_structured",
        AsyncMock(side_effect=RuntimeError("llm down")),
    )

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        response = await query_engine.run_query(session, "What does Bob like?")

    assert response.facts_used
    assert response.cited_fact_ids == []
    assert "generazione risposta non disponibile" in response.answer


def test_no_code_parses_ids_from_answer_text():
    """F1.6 canary: citations must not be extracted from free-text answer."""
    from pathlib import Path

    roots = [
        Path(__file__).resolve().parents[1] / "app",
        Path(__file__).resolve().parents[2] / "frontend",
    ]
    forbidden = (
        "extract_ids_from_answer",
        "parse_citation",
        "cited_from_answer",
        "answer.match(",
    )
    hits: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix not in {".py", ".ts", ".tsx"}:
                continue
            if "node_modules" in path.parts or ".next" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            for needle in forbidden:
                if needle in text:
                    hits.append(f"{path}:{needle}")
    assert hits == []


@pytest.mark.asyncio
async def test_get_graph_latest_filter_and_include_historical(neo4j_ready):
    await _create_fact(
        fact_id="cur", text="Current", embedding=_unit_vector(6), is_latest=True
    )
    await _create_fact(
        fact_id="hist", text="Historical", embedding=_unit_vector(7), is_latest=False
    )
    await _link_updates("cur", "hist")

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        only_latest = await query_engine.get_graph(session, is_latest=True)
        include_all = await query_engine.get_graph(session, is_latest=False)

    latest_ids = {n.id for n in only_latest.nodes}
    assert latest_ids == {"cur"}
    assert all(n.properties.get("is_latest") is True for n in only_latest.nodes)

    all_ids = {n.id for n in include_all.nodes}
    assert all_ids == {"cur", "hist"}
    assert any(r.type == "UPDATES" for r in include_all.relationships)
    assert not any(n.id.startswith("chunk") for n in include_all.nodes)


@pytest.mark.asyncio
async def test_reconcile_endpoint_detects_and_fixes_drift(neo4j_ready):
    await _create_fact(
        fact_id="head", text="Head", embedding=_unit_vector(8), is_latest=False
    )
    await _create_fact(
        fact_id="tail", text="Tail", embedding=_unit_vector(10), is_latest=True
    )
    await _link_updates("head", "tail")
    # Inconsistency: head (chain head) is_latest=false; tail (has UPDATES in) is_latest=true

    drift = await reconcile()
    assert drift > 0

    drift2 = await reconcile()
    assert drift2 == 0

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        result = await session.run(
            "MATCH (f:Fact) RETURN f.id AS id, f.is_latest AS latest ORDER BY id"
        )
        rows = {r["id"]: r["latest"] async for r in result}
    assert rows["head"] is True
    assert rows["tail"] is False


@pytest.mark.asyncio
async def test_http_endpoints_against_neo4j(neo4j_ready, monkeypatch):
    await _create_fact(
        fact_id="http-f1",
        text="Bob likes tea.",
        embedding=_unit_vector(11),
        is_latest=True,
    )
    await _create_chunk_and_link(
        chunk_id="http-c1",
        fact_id="http-f1",
        doc_id="doc-test",
        text="Bob prefers tea in the morning.",
    )
    await _await_vector_index()

    monkeypatch.setattr(
        "app.pipeline.query_engine.embeddings.embed",
        lambda text: _unit_vector(11),
    )
    monkeypatch.setattr(
        "app.pipeline.query_engine.call_structured",
        AsyncMock(return_value=QueryAnswer(answer="Bob likes tea.")),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        fact = await client.get("/facts/http-f1")
        assert fact.status_code == 200
        assert fact.json()["id"] == "http-f1"

        missing = await client.get("/facts/does-not-exist")
        assert missing.status_code == 404

        history = await client.get("/facts/http-f1/history")
        assert history.status_code == 200
        assert len(history.json()["facts"]) == 1

        graph = await client.get("/graph")
        assert graph.status_code == 200
        assert any(n["id"] == "http-f1" for n in graph.json()["nodes"])

        query = await client.post("/query", json={"text": "What does Bob like?"})
        assert query.status_code == 200
        body = query.json()
        assert body["facts_used"]
        assert "cited_fact_ids" in body
        assert set(body["cited_fact_ids"]).issubset(
            {f["id"] for f in body["facts_used"]}
        )
        assert all(f["id"] != "hist" for f in body["facts_used"])
        for fid in body["cited_fact_ids"]:
            assert fid not in body["answer"]

        recon = await client.post("/reconcile")
        assert recon.status_code == 200
        assert "drift_count" in recon.json()
