"""Stub REST endpoint tests (E2.5)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


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
    assert "/reconcile" in paths
    assert "/events/stream" in paths


@pytest.mark.asyncio
async def test_post_documents_returns_job_id(client: AsyncClient):
    response = await client.post(
        "/documents",
        json={"doc_id": "doc-1", "text": "Sample document text."},
    )
    assert response.status_code == 202
    body = response.json()
    assert "job_id" in body
    assert body["job_id"]


@pytest.mark.asyncio
async def test_post_dreaming_run_returns_job_id(client: AsyncClient):
    response = await client.post("/dreaming/run", json={})
    assert response.status_code == 202
    assert "job_id" in response.json()


@pytest.mark.asyncio
async def test_get_graph_returns_nvl_shape(client: AsyncClient):
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
async def test_get_fact_detail(client: AsyncClient):
    response = await client.get("/facts/fact-stub-1")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "fact-stub-1"
    assert body["type"] in {"fact", "preference", "episode"}
    assert "provenance" in body


@pytest.mark.asyncio
async def test_get_fact_history(client: AsyncClient):
    response = await client.get("/facts/fact-stub-1/history")
    assert response.status_code == 200
    body = response.json()
    assert "facts" in body
    assert len(body["facts"]) >= 1


@pytest.mark.asyncio
async def test_post_query_returns_query_response(client: AsyncClient):
    response = await client.post("/query", json={"text": "Where does Alice work?"})
    assert response.status_code == 200
    body = response.json()
    assert "answer" in body
    assert "facts_used" in body
    assert "subgraph" in body
    assert body["subgraph"]["nodes"][0]["label"] == "Fact"


@pytest.mark.asyncio
async def test_post_reconcile(client: AsyncClient):
    response = await client.post("/reconcile")
    assert response.status_code == 200
    assert response.json()["drift_count"] == 0
