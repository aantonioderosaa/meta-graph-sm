"""HTTP tests for Node/Concept graph view endpoints (Macrotask 6)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.schemas import GraphNode, GraphRelationship, GraphResponse
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


def _sample_graph() -> GraphResponse:
    return GraphResponse(
        nodes=[
            GraphNode(
                id="n1",
                caption="Alice",
                properties={"type": "entity"},
            )
        ],
        relationships=[
            GraphRelationship(
                id="rel-1",
                **{"from": "n1", "to": "n2"},
                type="works_at",
                caption="works at",
            )
        ],
    )


@pytest.mark.asyncio
async def test_get_entity_graph_returns_nvl_shape(client: AsyncClient, monkeypatch):
    async def mock_graph(session, is_latest=True, limit=200) -> GraphResponse:
        _ = session, is_latest, limit
        return _sample_graph()

    monkeypatch.setattr("app.api.node_graph.node_graph_engine.get_entity_graph", mock_graph)

    response = await client.get("/graph/entities")
    assert response.status_code == 200
    body = response.json()
    assert "nodes" in body
    assert "relationships" in body
    rel = body["relationships"][0]
    assert "from" in rel
    assert "to" in rel
    assert "type" in rel


@pytest.mark.asyncio
async def test_get_event_graph_returns_nvl_shape(client: AsyncClient, monkeypatch):
    async def mock_graph(session, is_latest=True, limit=200) -> GraphResponse:
        _ = session, is_latest, limit
        return _sample_graph()

    monkeypatch.setattr("app.api.node_graph.node_graph_engine.get_event_graph", mock_graph)

    response = await client.get("/graph/events")
    assert response.status_code == 200
    body = response.json()
    assert "nodes" in body
    assert "relationships" in body


@pytest.mark.asyncio
async def test_get_participation_graph_returns_nvl_shape(client: AsyncClient, monkeypatch):
    async def mock_graph(session, limit=200) -> GraphResponse:
        _ = session, limit
        return _sample_graph()

    monkeypatch.setattr(
        "app.api.node_graph.node_graph_engine.get_participation_graph", mock_graph
    )

    response = await client.get("/graph/participation")
    assert response.status_code == 200
    body = response.json()
    assert "nodes" in body
    assert "relationships" in body


@pytest.mark.asyncio
async def test_get_concept_overview_returns_nvl_shape(client: AsyncClient, monkeypatch):
    async def mock_graph(session, limit=100) -> GraphResponse:
        _ = session, limit
        return GraphResponse(
            nodes=[
                GraphNode(
                    id="c1",
                    caption="technology",
                    properties={"degree": 3, "type": "concept"},
                )
            ],
            relationships=[],
        )

    monkeypatch.setattr(
        "app.api.node_graph.node_graph_engine.get_concept_overview", mock_graph
    )

    response = await client.get("/graph/concepts")
    assert response.status_code == 200
    body = response.json()
    assert "nodes" in body
    assert "relationships" in body
    assert body["relationships"] == []


@pytest.mark.asyncio
async def test_get_concept_neighbors_returns_nvl_shape(client: AsyncClient, monkeypatch):
    async def mock_graph(session, concept_id: str) -> GraphResponse:
        _ = session
        return GraphResponse(
            nodes=[
                GraphNode(id=concept_id, caption="technology", properties={"type": "concept"}),
                GraphNode(id="e1", caption="Alice", properties={"type": "entity"}),
            ],
            relationships=[
                GraphRelationship(
                    id="hc-1",
                    **{"from": "e1", "to": concept_id},
                    type="HAS_CONCEPT",
                    caption="HAS_CONCEPT",
                )
            ],
        )

    monkeypatch.setattr(
        "app.api.node_graph.node_graph_engine.get_concept_neighbors", mock_graph
    )

    response = await client.get("/graph/concepts/abc")
    assert response.status_code == 200
    body = response.json()
    assert "nodes" in body
    assert "relationships" in body
    rel = body["relationships"][0]
    assert "from" in rel
    assert "to" in rel
    assert "type" in rel
