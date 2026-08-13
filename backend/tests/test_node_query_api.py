"""HTTP tests for POST /graph/query and GET /graph/queries (Macrotask 5)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.schemas import GraphNode, GraphRelationship, GraphResponse, QueryHistoryEntry
from app.core.neo4j_client import get_neo4j_session
from app.main import app
from app.models.query import NodeQueryResponse, NodeSubgraph, NodeUsed


@pytest.fixture
async def client():
    async def fake_session() -> AsyncIterator[AsyncMock]:
        yield AsyncMock()

    app.dependency_overrides[get_neo4j_session] = fake_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_neo4j_session, None)


def _empty_node_response() -> NodeQueryResponse:
    return NodeQueryResponse(
        answer="Nessuna informazione trovata nel grafo di entità, eventi e concetti.",
        nodes_used=[],
        concepts_used=[],
        cited_node_ids=[],
        subgraph=NodeSubgraph(nodes=[], relationships=[]),
    )


def _alice_response() -> NodeQueryResponse:
    return NodeQueryResponse(
        answer="Alice è un'entità.",
        nodes_used=[NodeUsed(id="alice", name="Alice", type="entity")],
        cited_node_ids=["alice"],
        subgraph=NodeSubgraph(nodes=[], relationships=[]),
    )


@pytest.mark.asyncio
async def test_post_graph_query_empty_graph_returns_200(
    client: AsyncClient, monkeypatch
):
    async def mock_run(session, text, *, job_id=None):
        _ = session, text, job_id
        return _empty_node_response()

    monkeypatch.setattr(
        "app.api.node_query.node_query_engine.run_node_query", mock_run
    )

    response = await client.post("/graph/query", json={"text": "chi è Alice?"})
    assert response.status_code == 200
    body = response.json()
    assert body["nodes_used"] == []
    assert "nessuna informazione trovata" in body["answer"].lower()
    assert "facts_used" not in body


@pytest.mark.asyncio
async def test_get_graph_entities_and_post_graph_query_do_not_collide(
    client: AsyncClient, monkeypatch
):
    async def mock_entity_graph(session, **kwargs):
        _ = session, kwargs
        return GraphResponse(
            nodes=[GraphNode(id="alice", caption="Alice", properties={"type": "entity"})],
            relationships=[
                GraphRelationship(
                    id="r1", **{"from": "alice", "to": "acme"}, type="works_at"
                )
            ],
        )

    async def mock_run(session, text, *, job_id=None):
        _ = session, text, job_id
        return _alice_response()

    monkeypatch.setattr(
        "app.api.node_graph.node_graph_engine.get_entity_graph", mock_entity_graph
    )
    monkeypatch.setattr(
        "app.api.node_query.node_query_engine.run_node_query", mock_run
    )

    listed = await client.get("/openapi.json")
    paths = listed.json()["paths"]
    assert "/graph/entities" in paths
    assert "get" in paths["/graph/entities"]
    assert "/graph/query" in paths
    assert "post" in paths["/graph/query"]
    assert "/query" not in paths

    graph = await client.get("/graph/entities")
    assert graph.status_code == 200
    assert graph.json()["nodes"][0]["id"] == "alice"

    queried = await client.post("/graph/query", json={"text": "chi è Alice?"})
    assert queried.status_code == 200
    assert queried.json()["nodes_used"][0]["id"] == "alice"


@pytest.mark.asyncio
async def test_get_graph_queries_fact_log_id_returns_404(
    client: AsyncClient, monkeypatch
):
    async def mock_detail(session, query_id):
        _ = session, query_id
        return None

    monkeypatch.setattr(
        "app.api.node_query.node_query_log.get_node_query_log_detail", mock_detail
    )

    response = await client.get("/graph/queries/fact-query-log-id")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_graph_queries_never_uses_fact_query_log(
    client: AsyncClient, monkeypatch
):
    async def mock_list(session, *, limit=20):
        _ = session, limit
        return [
            QueryHistoryEntry(
                id="nql-1", text="chi è Alice?", created_at="2026-01-01T00:00:00"
            )
        ]

    monkeypatch.setattr(
        "app.api.node_query.node_query_log.list_node_query_logs", mock_list
    )

    response = await client.get("/graph/queries")
    assert response.status_code == 200
    items = response.json()["items"]
    assert items[0]["id"] == "nql-1"
    assert items[0]["text"] == "chi è Alice?"


def test_node_query_request_has_no_type_filter():
    from app.api.schemas import NodeQueryRequest

    body = NodeQueryRequest(text="chi è Alice?")
    assert body.text == "chi è Alice?"
    assert "type_filter" not in NodeQueryRequest.model_fields
