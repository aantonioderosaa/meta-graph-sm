"""REST endpoint tests — stubs replaced by real handlers (E2.5 → E5)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.schemas import (
    ChunkProvenance,
    FactDetailResponse,
    FactHistoryEntry,
    FactHistoryResponse,
    GraphNode,
    GraphRelationship,
    GraphResponse,
)
from app.core.neo4j_client import get_neo4j_session
from app.main import app
from app.models.extraction import FactType
from app.models.query import FactUsed, QueryResponse, Subgraph, SubgraphNode


@pytest.fixture
async def client():
    async def fake_session() -> AsyncIterator[AsyncMock]:
        yield AsyncMock()

    app.dependency_overrides[get_neo4j_session] = fake_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_neo4j_session, None)


@pytest.mark.asyncio
async def test_openapi_docs_available(client: AsyncClient):
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    paths = schema["paths"]
    assert "/health" in paths
    assert "/documents" in paths
    assert "/dreaming/run" in paths
    assert "/graph" in paths
    assert "/facts/{fact_id}" in paths
    assert "/facts/{fact_id}/history" in paths
    assert "/query" in paths
    assert "/queries" in paths
    assert "/queries/{query_id}" in paths
    assert "/reconcile" in paths
    assert "/events/stream" in paths


@pytest.mark.asyncio
async def test_get_documents_returns_list(client: AsyncClient, monkeypatch):
    from app.api.schemas import DocumentListResponse, DocumentSummary

    async def mock_list(session):
        _ = session
        return [
            DocumentSummary(
                doc_id="doc-1",
                chunk_count=2,
                fact_count=3,
                first_ingested_at="2026-01-01T00:00:00Z",
                last_ingested_at="2026-01-02T00:00:00Z",
            )
        ]

    monkeypatch.setattr("app.api.documents.documents_engine.list_documents", mock_list)

    response = await client.get("/documents")
    assert response.status_code == 200
    body = response.json()
    assert body["documents"][0]["doc_id"] == "doc-1"
    assert body["documents"][0]["chunk_count"] == 2
    assert DocumentListResponse.model_validate(body)


@pytest.mark.asyncio
async def test_post_documents_returns_job_id(client: AsyncClient, monkeypatch):
    async def noop_pipeline(doc_id: str, text: str, job_id: str) -> None:
        _ = doc_id, text, job_id

    monkeypatch.setattr("app.api.documents.run_ingestion_pipeline", noop_pipeline)

    response = await client.post(
        "/documents",
        json={"doc_id": "doc-1", "text": "Sample document text."},
    )
    assert response.status_code == 202
    body = response.json()
    assert "job_id" in body
    assert body["job_id"]


@pytest.mark.asyncio
async def test_post_dreaming_run_returns_job_id(client: AsyncClient, monkeypatch):
    async def noop_pipeline(job_id: str, doc_id: str | None = None) -> None:
        _ = job_id, doc_id

    monkeypatch.setattr("app.api.dreaming.run_dreaming_pipeline", noop_pipeline)

    response = await client.post("/dreaming/run", json={})
    assert response.status_code == 202
    assert "job_id" in response.json()


@pytest.mark.asyncio
async def test_get_graph_returns_nvl_shape(client: AsyncClient, monkeypatch):
    async def mock_graph(session, **kwargs) -> GraphResponse:
        _ = session, kwargs
        return GraphResponse(
            nodes=[
                GraphNode(
                    id="fact-a",
                    caption="Alice works at Acme Corp.",
                    properties={"type": "fact", "is_latest": True},
                )
            ],
            relationships=[
                GraphRelationship(
                    id="rel-1",
                    **{"from": "fact-a", "to": "fact-b"},
                    type="EXTENDS",
                )
            ],
        )

    monkeypatch.setattr("app.api.graph.query_engine.get_graph", mock_graph)

    response = await client.get("/graph")
    assert response.status_code == 200
    body = response.json()
    assert "nodes" in body
    assert "relationships" in body
    assert len(body["nodes"]) >= 1
    rel = body["relationships"][0]
    assert "from" in rel
    assert "to" in rel
    assert "type" in rel


@pytest.mark.asyncio
async def test_get_fact_detail(client: AsyncClient, monkeypatch):
    async def mock_detail(session, fact_id: str) -> FactDetailResponse:
        _ = session
        return FactDetailResponse(
            id=fact_id,
            text="Alice works at Acme Corp.",
            type=FactType.fact,
            confidence=1.0,
            is_latest=True,
            created_at="2026-01-01T00:00:00Z",
            source_doc_id="doc-1",
            provenance=[
                ChunkProvenance(chunk_id="c1", snippet="Alice joined Acme.", doc_id="doc-1")
            ],
        )

    monkeypatch.setattr("app.api.facts.query_engine.get_fact_detail", mock_detail)

    response = await client.get("/facts/fact-stub-1")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "fact-stub-1"
    assert body["type"] in {"fact", "preference", "episode"}
    assert "provenance" in body


@pytest.mark.asyncio
async def test_get_fact_detail_404(client: AsyncClient, monkeypatch):
    async def mock_missing(session, fact_id: str) -> None:
        _ = session, fact_id
        return None

    monkeypatch.setattr("app.api.facts.query_engine.get_fact_detail", mock_missing)

    response = await client.get("/facts/missing-id")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_fact_history(client: AsyncClient, monkeypatch):
    async def mock_history(session, fact_id: str) -> FactHistoryResponse:
        _ = session
        return FactHistoryResponse(
            facts=[
                FactHistoryEntry(
                    id=fact_id,
                    text="Current",
                    type=FactType.fact,
                    is_latest=True,
                    path_length=0,
                )
            ]
        )

    monkeypatch.setattr("app.api.facts.query_engine.get_fact_history", mock_history)

    response = await client.get("/facts/fact-stub-1/history")
    assert response.status_code == 200
    body = response.json()
    assert "facts" in body
    assert len(body["facts"]) >= 1


@pytest.mark.asyncio
async def test_post_query_returns_query_response(client: AsyncClient, monkeypatch):
    async def mock_query(session, text: str, **kwargs) -> QueryResponse:
        _ = session, kwargs
        return QueryResponse(
            answer=f"Answer for: {text}",
            facts_used=[FactUsed(id="f1", text="Alice works at Acme.", source_doc_id="doc-1")],
            subgraph=Subgraph(
                nodes=[SubgraphNode(id="f1", label="Fact", properties={"text": "Alice"})],
                relationships=[],
            ),
        )

    monkeypatch.setattr("app.api.query.query_engine.run_query", mock_query)

    response = await client.post("/query", json={"text": "Where does Alice work?"})
    assert response.status_code == 200
    body = response.json()
    assert "answer" in body
    assert "facts_used" in body
    assert "cited_fact_ids" in body
    assert "subgraph" in body
    assert body["subgraph"]["nodes"][0]["label"] == "Fact"


@pytest.mark.asyncio
async def test_post_reconcile(client: AsyncClient, monkeypatch):
    async def mock_reconcile() -> int:
        return 0

    monkeypatch.setattr("app.api.reconcile.reconcile", mock_reconcile)

    response = await client.post("/reconcile")
    assert response.status_code == 200
    assert response.json()["drift_count"] == 0
