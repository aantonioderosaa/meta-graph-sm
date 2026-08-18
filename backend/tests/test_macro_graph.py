"""Fase 15: macro graph, bundle expansion, node metadata. FakeSession, no Docker."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.schemas import BundleResponse, GraphNode, GraphRelationship, GraphResponse
from app.core.neo4j_client import get_neo4j_session
from app.main import app
from app.models.kernel import EntityKernelType
from app.pipeline.concepts import kernel_catch_all_concept_id
from app.pipeline.node_graph_engine import (
    MACRO_BUNDLE_RELS_CYPHER,
    MACRO_LEAF_RELS_CYPHER,
    bundle_edge_id,
    collapse_pair_counts,
    get_graph_bundle,
    get_macro_graph,
    get_node_metadata,
)


class FakeNode:
    def __init__(self, **props):
        self._props = props

    def get(self, key, default=None):
        return self._props.get(key, default)

    def __getitem__(self, key):
        return self._props[key]


class FakeResult:
    def __init__(self, records: list[dict]):
        self._records = records

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for record in self._records:
            yield record

    async def single(self):
        return self._records[0] if self._records else None


class FakeSession:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.queue: list[list[dict]] = []

    def enqueue(self, records: list[dict]) -> None:
        self.queue.append(records)

    async def run(self, cypher, **kwargs):
        self.calls.append((cypher, kwargs))
        records = self.queue.pop(0) if self.queue else []
        return FakeResult(records)


KERNEL_AGENTE = kernel_catch_all_concept_id(EntityKernelType.Agente)


def test_bundle_edge_id_orders_undirected_pair():
    assert bundle_edge_id("b", "a") == "bundle:a:b"
    assert bundle_edge_id("a", "b") == "bundle:a:b"


def test_collapse_pair_counts_maps_members_onto_promoted_concepts():
    counts = collapse_pair_counts(
        [("p1", "c1"), ("p1", "c1"), ("c2", "p2"), ("p1", "p1")],
        {"sport", "club"},
        {"p1": "sport", "p2": "sport", "c1": "club", "c2": "club"},
    )
    assert counts == {("club", "sport"): 3}


@pytest.mark.asyncio
async def test_macro_graph_collapses_relation_count_caption_only():
    session = FakeSession()
    session.enqueue(
        [
            {
                "c": FakeNode(
                    id="sport",
                    name="Sport",
                    kernel_category="Agente",
                    promoted=True,
                    definition="non sul canvas",
                )
            }
        ]
    )
    session.enqueue(
        [
            {
                "n": FakeNode(
                    id="alice",
                    name="Alice",
                    type="entity",
                    kernel_category="Agente",
                )
            },
            {
                "n": FakeNode(
                    id="bob",
                    name="Bob",
                    type="entity",
                    kernel_category="Agente",
                )
            },
        ]
    )
    session.enqueue(
        [
            {"node_id": "alice", "concept_id": KERNEL_AGENTE},
            {"node_id": "bob", "concept_id": KERNEL_AGENTE},
            {"node_id": "p1", "concept_id": "sport"},
        ]
    )
    session.enqueue(
        [
            {"from_id": "alice", "to_id": "bob"},
            {"from_id": "alice", "to_id": "bob"},
            {"from_id": "bob", "to_id": "alice"},
            {"from_id": "p1", "to_id": "alice"},
        ]
    )

    graph = await get_macro_graph(session, limit=400)

    by_id = {node.id: node for node in graph.nodes}
    assert set(by_id) == {"sport", "alice", "bob"}
    assert by_id["sport"].caption == "Sport"
    assert by_id["alice"].caption == "Alice"
    assert "definition" not in by_id["sport"].caption
    assert by_id["sport"].properties["type"] == "concept"
    assert by_id["alice"].properties["type"] == "entity"
    assert by_id["alice"].properties["kernel_category"] == "Agente"

    rels = {rel.id: rel.model_dump(by_alias=True) for rel in graph.relationships}
    assert set(rels) == {
        bundle_edge_id("alice", "bob"),
        bundle_edge_id("sport", "alice"),
    }
    assert rels[bundle_edge_id("alice", "bob")]["caption"] == "3"
    assert rels[bundle_edge_id("alice", "bob")]["type"] == "BUNDLE"
    assert rels[bundle_edge_id("sport", "alice")]["caption"] == "1"
    assert session.calls[3][0] == MACRO_LEAF_RELS_CYPHER


@pytest.mark.asyncio
async def test_macro_graph_empty_skips_rel_queries():
    session = FakeSession()
    session.enqueue([])
    session.enqueue([])
    graph = await get_macro_graph(session)
    assert graph.nodes == []
    assert graph.relationships == []
    assert len(session.calls) == 2


@pytest.mark.asyncio
async def test_bundle_lists_individuals_both_directions():
    session = FakeSession()
    session.enqueue([{"ids": ["alice"]}])
    session.enqueue([{"ids": ["acme"]}])
    session.enqueue(
        [
            {
                "id": "rel-1",
                "from_id": "alice",
                "to_id": "acme",
                "relation": "works_at",
                "rel_type": "works_at",
                "kernel_parent": "SocialeIntenzionale",
                "witnesses_a": ["wa"],
                "witnesses_b": ["wb"],
                "provenance": '{"doc_id":"d1"}',
                "valid_time": "2010",
                "system_time": "2026-01-01T00:00:00",
            },
            {
                "id": "rel-2",
                "from_id": "acme",
                "to_id": "alice",
                "relation": "employs",
                "rel_type": "employs",
                "kernel_parent": "SocialeIntenzionale",
                "witnesses_a": ["wb"],
                "witnesses_b": ["wa"],
                "provenance": None,
                "valid_time": None,
                "system_time": None,
            },
        ]
    )

    bundle = await get_graph_bundle(session, "alice", "acme")

    assert len(bundle.items) == 2
    assert session.calls[2][0] == MACRO_BUNDLE_RELS_CYPHER
    first = bundle.items[0]
    assert first.type == "works_at"
    assert first.relation == "works_at"
    assert first.kernel_parent == "SocialeIntenzionale"
    assert first.witnesses_a == ["wa"]
    assert first.witnesses_b == ["wb"]
    assert first.epistemic_status == "asserted"
    assert first.valid_time == "2010"
    dumped = first.model_dump(by_alias=True)
    assert dumped["from"] == "alice"
    assert dumped["to"] == "acme"
    assert {item.id for item in bundle.items} == {"rel-1", "rel-2"}


@pytest.mark.asyncio
async def test_bundle_empty_when_no_endpoints():
    session = FakeSession()
    session.enqueue([{"ids": []}])
    session.enqueue([{"ids": ["acme"]}])
    bundle = await get_graph_bundle(session, "missing", "acme")
    assert bundle.items == []
    assert len(session.calls) == 2


@pytest.mark.asyncio
async def test_metadata_concept_breadcrumb_to_kernel():
    session = FakeSession()
    genre = FakeNode(
        id="sport",
        name="Sport",
        kernel_category="Agente",
        definition="attività agonistica",
        aliases=["sports"],
        promoted=True,
    )
    kernel = FakeNode(id=KERNEL_AGENTE, name="Agente", kernel_category="Agente")
    session.enqueue([{"c": genre, "n": None}])
    session.enqueue(
        [
            {"c": genre, "anc": genre, "dist": 0},
            {"c": genre, "anc": kernel, "dist": 1},
        ]
    )
    session.enqueue([{"n": 4}])

    meta = await get_node_metadata(session, "sport")

    assert meta is not None
    assert meta.kind == "concept"
    assert meta.name == "Sport"
    assert meta.definition == "attività agonistica"
    assert meta.aliases == ["sports"]
    assert meta.member_count == 4
    assert [item.id for item in meta.is_a_breadcrumb] == ["sport", KERNEL_AGENTE]
    assert meta.is_a_breadcrumb[-1].name == "Agente"


@pytest.mark.asyncio
async def test_metadata_node_summary_and_identities():
    session = FakeSession()
    node = FakeNode(
        id="alice",
        name="Alice",
        type="entity",
        kernel_category="Agente",
        summary="giocatrice",
        aliases=["A. Rossi"],
        embedding=[0.1],
    )
    session.enqueue([{"c": None, "n": node}])
    session.enqueue([{"uri": "identity:alice:Agente"}])

    meta = await get_node_metadata(session, "alice")

    assert meta is not None
    assert meta.kind == "node"
    assert meta.summary == "giocatrice"
    assert meta.kernel_category == "Agente"
    assert meta.identity_uris == ["identity:alice:Agente"]
    assert "embedding" not in meta.attributes
    assert meta.attributes.get("aliases") == ["A. Rossi"]


@pytest.mark.asyncio
async def test_metadata_missing_returns_none():
    session = FakeSession()
    session.enqueue([{"c": None, "n": None}])
    assert await get_node_metadata(session, "nope") is None


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
async def test_get_macro_http_shape(client: AsyncClient, monkeypatch):
    async def mock_graph(session, limit=400) -> GraphResponse:
        _ = session, limit
        return GraphResponse(
            nodes=[
                GraphNode(
                    id="sport",
                    caption="Sport",
                    properties={"type": "concept", "kernel_category": "Agente"},
                )
            ],
            relationships=[
                GraphRelationship(
                    id="bundle:a:b",
                    **{"from": "a", "to": "b"},
                    type="BUNDLE",
                    caption="2",
                )
            ],
        )

    monkeypatch.setattr("app.api.node_graph.node_graph_engine.get_macro_graph", mock_graph)
    response = await client.get("/graph/macro")
    assert response.status_code == 200
    body = response.json()
    assert body["nodes"][0]["caption"] == "Sport"
    assert body["relationships"][0]["caption"] == "2"
    assert body["relationships"][0]["type"] == "BUNDLE"


@pytest.mark.asyncio
async def test_get_bundle_and_metadata_http(client: AsyncClient, monkeypatch):
    async def mock_bundle(session, a, b) -> BundleResponse:
        _ = session
        return BundleResponse(
            items=[
                {
                    "id": "rel-1",
                    "from": a,
                    "to": b,
                    "type": "works_at",
                    "relation": "works_at",
                    "epistemic_status": "asserted",
                }
            ]
        )

    async def mock_meta(session, node_id):
        _ = session
        from app.api.schemas import NodeMetadataResponse

        return NodeMetadataResponse(id=node_id, kind="node", name="Alice")

    monkeypatch.setattr("app.api.node_graph.node_graph_engine.get_graph_bundle", mock_bundle)
    monkeypatch.setattr("app.api.node_graph.node_graph_engine.get_node_metadata", mock_meta)

    bundle = await client.get("/graph/bundle/alice/acme")
    assert bundle.status_code == 200
    assert bundle.json()["items"][0]["epistemic_status"] == "asserted"

    meta = await client.get("/graph/metadata/alice")
    assert meta.status_code == 200
    assert meta.json()["kind"] == "node"

    async def missing(session, node_id):
        _ = session, node_id
        return None

    monkeypatch.setattr("app.api.node_graph.node_graph_engine.get_node_metadata", missing)
    not_found = await client.get("/graph/metadata/missing")
    assert not_found.status_code == 404
