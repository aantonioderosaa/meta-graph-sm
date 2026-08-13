"""REST endpoint tests — stubs replaced by real handlers (E2.5 → E5)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.schemas import (
    GraphNode,
    GraphRelationship,
    GraphResetResponse,
    GraphResponse,
)
from app.core.neo4j_client import get_neo4j_session
from app.main import app


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
    assert "delete" in paths["/graph"]
    assert "/graph/entities" in paths
    assert "/graph/query" in paths
    assert "/graph/queries" in paths
    assert "/events/stream" in paths
    assert "/facts/{fact_id}" not in paths
    assert "/facts/{fact_id}/history" not in paths
    assert "/query" not in paths
    assert "/queries" not in paths
    assert "/queries/{query_id}" not in paths
    assert "/reconcile" not in paths


@pytest.mark.asyncio
async def test_get_documents_returns_list(client: AsyncClient, monkeypatch):
    from app.api.schemas import DocumentListResponse, DocumentSummary

    async def mock_list(session):
        _ = session
        return [
            DocumentSummary(
                doc_id="doc-1",
                chunk_count=2,
                node_count=3,
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
    assert body["documents"][0]["node_count"] == 3
    assert "fact_count" not in body["documents"][0]
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
async def test_get_entity_graph_returns_nvl_shape(client: AsyncClient, monkeypatch):
    async def mock_graph(session, **kwargs) -> GraphResponse:
        _ = session, kwargs
        return GraphResponse(
            nodes=[
                GraphNode(
                    id="alice",
                    caption="Alice",
                    properties={"type": "entity"},
                )
            ],
            relationships=[
                GraphRelationship(
                    id="rel-1",
                    **{"from": "alice", "to": "acme"},
                    type="works_at",
                )
            ],
        )

    monkeypatch.setattr("app.api.node_graph.node_graph_engine.get_entity_graph", mock_graph)

    response = await client.get("/graph/entities")
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
async def test_delete_graph_returns_deleted(client: AsyncClient):
    response = await client.delete("/graph")
    assert response.status_code == 200
    body = response.json()
    assert body == {"deleted": True}
    assert GraphResetResponse.model_validate(body)
