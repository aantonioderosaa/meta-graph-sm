"""Fase 17: domain dashboard endpoints. FakeSession, no Docker."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.schemas import (
    ConnectivityRuleListResponse,
    DomainDictionaryResponse,
    DomainListResponse,
    GraphNode,
    GraphRelationship,
    GraphResponse,
)
from app.core.neo4j_client import get_neo4j_session
from app.main import app
from app.pipeline.node_graph_engine import (
    CONCEPT_BY_ID_CYPHER,
    DOMAIN_DICTIONARY_RELS_CYPHER,
    DOMAIN_RULES_CYPHER,
    DOMAINS_GRAPH_CONCEPTS_CYPHER,
    DOMAINS_LIST_CYPHER,
    bundle_edge_id,
    get_domain_children_graph,
    get_domain_dictionary,
    get_domain_rules,
    get_domains,
    get_domains_graph,
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


KERNEL = FakeNode(id="kernel-agente", name="Agente", kernel_category="Agente")
SPORT = FakeNode(
    id="sport",
    name="Sport",
    kernel_category="Agente",
    promoted=True,
    definition="attività agonistica",
)


def test_domains_list_cypher_has_no_limit():
    assert "LIMIT" not in DOMAINS_LIST_CYPHER.upper()
    assert "LIMIT" not in DOMAINS_GRAPH_CONCEPTS_CYPHER.upper()


@pytest.mark.asyncio
async def test_domains_list_is_complete_promoted_and_catchall():
    session = FakeSession()
    session.enqueue(
        [
            {
                "id": "kernel-agente",
                "name": "Agente",
                "kernel_category": "Agente",
                "definition": "chi agisce",
                "promoted": False,
                "direct_member_count": 2,
            },
            {
                "id": "sport",
                "name": "Sport",
                "kernel_category": "Agente",
                "definition": "attività agonistica",
                "promoted": True,
                "direct_member_count": 4,
            },
            {
                "id": "kernel-evento",
                "name": "Evento",
                "kernel_category": "Evento",
                "definition": None,
                "promoted": False,
                "direct_member_count": 0,
            },
        ]
    )

    listing = await get_domains(session)

    assert session.calls[0][0] == DOMAINS_LIST_CYPHER
    assert [item.id for item in listing.items] == [
        "kernel-agente",
        "sport",
        "kernel-evento",
    ]
    assert listing.items[0].promoted is False
    assert listing.items[1].promoted is True
    assert listing.items[1].direct_member_count == 4
    assert listing.items[1].definition == "attività agonistica"


@pytest.mark.asyncio
async def test_dictionary_scoped_to_members():
    session = FakeSession()
    session.enqueue([{"c": SPORT}])
    session.enqueue(
        [
            {
                "name": "works_at",
                "kernel_parent": "SocialeIntenzionale",
                "count": 2,
            }
        ]
    )
    session.enqueue([{"name": "aliases", "count": 1}])

    data = await get_domain_dictionary(session, "sport")

    assert data is not None
    assert session.calls[0][0] == CONCEPT_BY_ID_CYPHER
    assert session.calls[1][0] == DOMAIN_DICTIONARY_RELS_CYPHER
    assert session.calls[1][1]["concept_id"] == "sport"
    kinds = {(item.kind, item.name, item.count) for item in data.items}
    assert ("relation", "works_at", 2) in kinds
    assert ("attribute", "aliases", 1) in kinds
    rel = next(item for item in data.items if item.kind == "relation")
    assert rel.kernel_parent == "SocialeIntenzionale"


@pytest.mark.asyncio
async def test_dictionary_empty_when_no_facts():
    session = FakeSession()
    session.enqueue([{"c": SPORT}])
    session.enqueue([])
    session.enqueue([])
    data = await get_domain_dictionary(session, "sport")
    assert data is not None
    assert data.items == []


@pytest.mark.asyncio
async def test_rules_exclude_unrelated_global_rows():
    session = FakeSession()
    session.enqueue([{"c": SPORT}])
    session.enqueue([{"id": "alice"}, {"id": "bob"}])
    session.enqueue(
        [
            {
                "source_category": "Agente",
                "relation_type": "works_at",
                "target_category": "CostruttoSociale",
                "generalization_level": 0,
                "origin_count": 1,
                "origin_fact_ids": ["alice|works_at|acme"],
            },
            {
                "source_category": "Luogo",
                "relation_type": "located_in",
                "target_category": "Luogo",
                "generalization_level": 1,
                "origin_count": 9,
                "origin_fact_ids": ["rome|located_in|italy"],
            },
            {
                "source_category": "OggettoFisico",
                "relation_type": "owns",
                "target_category": "Agente",
                "generalization_level": 0,
                "origin_count": 1,
                "origin_fact_ids": ["car|owns|alice"],
            },
        ]
    )

    data = await get_domain_rules(session, "sport")

    assert data is not None
    assert session.calls[2][0] == DOMAIN_RULES_CYPHER
    types = {
        (item.source_category, item.relation_type, item.target_category)
        for item in data.items
    }
    assert ("Agente", "works_at", "CostruttoSociale") in types
    assert ("OggettoFisico", "owns", "Agente") in types
    assert ("Luogo", "located_in", "Luogo") not in types


@pytest.mark.asyncio
async def test_children_graph_direct_children_only_with_bundles():
    session = FakeSession()
    session.enqueue([{"c": KERNEL}])
    session.enqueue([{"child": SPORT}])
    session.enqueue(
        [
            {
                "id": "alice",
                "name": "Alice",
                "type": "entity",
                "kernel_category": "Agente",
            }
        ]
    )
    session.enqueue(
        [
            {"node_id": "alice", "concept_id": "kernel-agente"},
            {"node_id": "p1", "concept_id": "sport"},
        ]
    )
    session.enqueue(
        [
            {"from_id": "alice", "to_id": "p1"},
            {"from_id": "outsider", "to_id": "alice"},
        ]
    )

    graph = await get_domain_children_graph(session, "kernel-agente")

    assert graph is not None
    by_id = {node.id: node for node in graph.nodes}
    assert set(by_id) == {"sport", "alice"}
    assert by_id["sport"].properties["type"] == "concept"
    assert by_id["alice"].properties["type"] == "entity"
    rels = {rel.id: rel.model_dump(by_alias=True) for rel in graph.relationships}
    assert set(rels) == {bundle_edge_id("alice", "sport")}
    assert rels[bundle_edge_id("alice", "sport")]["caption"] == "1"
    assert rels[bundle_edge_id("alice", "sport")]["type"] == "BUNDLE"


@pytest.mark.asyncio
async def test_domains_graph_omits_leftover_nodes():
    session = FakeSession()
    session.enqueue(
        [
            {"c": KERNEL},
            {"c": SPORT},
            {
                "c": FakeNode(
                    id="kernel-luogo",
                    name="Luogo",
                    kernel_category="Luogo",
                )
            },
        ]
    )
    session.enqueue(
        [
            {"node_id": "alice", "concept_id": "kernel-agente"},
            {"node_id": "p1", "concept_id": "sport"},
            {"node_id": "orphan", "concept_id": "kernel-agente"},
        ]
    )
    session.enqueue(
        [
            {"from_id": "alice", "to_id": "p1"},
            {"from_id": "alice", "to_id": "orphan"},
        ]
    )

    graph = await get_domains_graph(session)

    ids = {node.id for node in graph.nodes}
    assert ids == {"kernel-agente", "sport"}
    assert "alice" not in ids
    assert "orphan" not in ids
    assert all(node.properties.get("type") == "concept" for node in graph.nodes)
    rels = {rel.id for rel in graph.relationships}
    assert rels == {bundle_edge_id("kernel-agente", "sport")}


@pytest.mark.asyncio
async def test_missing_concept_returns_none():
    session = FakeSession()
    session.enqueue([])
    assert await get_domain_dictionary(session, "missing") is None

    session = FakeSession()
    session.enqueue([])
    assert await get_domain_rules(session, "missing") is None

    session = FakeSession()
    session.enqueue([])
    assert await get_domain_children_graph(session, "missing") is None


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
async def test_domain_http_shapes(client: AsyncClient, monkeypatch):
    async def mock_domains(session) -> DomainListResponse:
        _ = session
        return DomainListResponse(
            items=[
                {
                    "id": "sport",
                    "name": "Sport",
                    "kernel_category": "Agente",
                    "promoted": True,
                    "direct_member_count": 4,
                }
            ]
        )

    async def mock_graph(session) -> GraphResponse:
        _ = session
        return GraphResponse(
            nodes=[
                GraphNode(
                    id="sport",
                    caption="Sport",
                    properties={"type": "concept"},
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

    async def mock_dict(session, concept_id) -> DomainDictionaryResponse:
        _ = session
        return DomainDictionaryResponse(
            items=[{"kind": "relation", "name": "works_at", "count": 1}]
        )

    async def mock_rules(session, concept_id) -> ConnectivityRuleListResponse:
        _ = session, concept_id
        return ConnectivityRuleListResponse(items=[])

    async def mock_children(session, concept_id) -> GraphResponse:
        _ = session, concept_id
        return GraphResponse(nodes=[], relationships=[])

    monkeypatch.setattr("app.api.node_graph.node_graph_engine.get_domains", mock_domains)
    monkeypatch.setattr(
        "app.api.node_graph.node_graph_engine.get_domains_graph", mock_graph
    )
    monkeypatch.setattr(
        "app.api.node_graph.node_graph_engine.get_domain_dictionary", mock_dict
    )
    monkeypatch.setattr(
        "app.api.node_graph.node_graph_engine.get_domain_rules", mock_rules
    )
    monkeypatch.setattr(
        "app.api.node_graph.node_graph_engine.get_domain_children_graph",
        mock_children,
    )

    listing = await client.get("/graph/domains")
    assert listing.status_code == 200
    assert listing.json()["items"][0]["id"] == "sport"

    canvas = await client.get("/graph/domains-graph")
    assert canvas.status_code == 200
    assert canvas.json()["nodes"][0]["caption"] == "Sport"

    dictionary = await client.get("/graph/domains/sport/dictionary")
    assert dictionary.status_code == 200
    assert dictionary.json()["items"][0]["kind"] == "relation"

    rules = await client.get("/graph/domains/sport/rules")
    assert rules.status_code == 200
    assert rules.json()["items"] == []

    children = await client.get("/graph/domains/sport/children-graph")
    assert children.status_code == 200
    assert children.json() == {"nodes": [], "relationships": []}


@pytest.mark.asyncio
async def test_domain_http_404(client: AsyncClient, monkeypatch):
    async def missing(session, concept_id):
        _ = session, concept_id
        return None

    monkeypatch.setattr(
        "app.api.node_graph.node_graph_engine.get_domain_dictionary", missing
    )
    monkeypatch.setattr(
        "app.api.node_graph.node_graph_engine.get_domain_rules", missing
    )
    monkeypatch.setattr(
        "app.api.node_graph.node_graph_engine.get_domain_children_graph", missing
    )

    assert (await client.get("/graph/domains/nope/dictionary")).status_code == 404
    assert (await client.get("/graph/domains/nope/rules")).status_code == 404
    assert (await client.get("/graph/domains/nope/children-graph")).status_code == 404
