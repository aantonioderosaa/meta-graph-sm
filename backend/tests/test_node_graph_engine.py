"""Unit tests for Node/Concept graph views (Macrotask 6). No Docker."""

from __future__ import annotations

import inspect

import pytest

from app.pipeline import node_graph_engine
from app.pipeline.node_graph_engine import (
    ENTITY_GRAPH_RELS_CYPHER,
    EVENT_GRAPH_NODES_CYPHER,
    PARTICIPATION_GRAPH_CYPHER,
    get_concept_neighbors,
    get_entity_graph,
    get_event_graph,
    get_participation_graph,
)


class FakeNode:
    def __init__(self, **props):
        self._props = props

    def get(self, key, default=None):
        return self._props.get(key, default)

    def __getitem__(self, key):
        return self._props[key]


class FakeRel:
    def __init__(self, element_id="rel-1", **props):
        self.element_id = element_id
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


def _cyphers() -> list[str]:
    return [
        value
        for name, value in inspect.getmembers(node_graph_engine)
        if name.endswith("_CYPHER") and isinstance(value, str)
    ]


@pytest.mark.asyncio
async def test_entity_graph_maps_entity_entity_rel_not_participates():
    session = FakeSession()
    session.enqueue([{"n": FakeNode(id="alice", name="Alice", type="entity")}])
    session.enqueue(
        [
            {
                "id": "rel-works-at",
                "from_id": "alice",
                "to_id": "alice",
                "caption": "works_at",
                "rel_type": "works_at",
            }
        ]
    )

    graph = await get_entity_graph(session, is_latest=True, limit=200)

    assert all(node.properties.get("type") != "event" for node in graph.nodes)
    assert all(node.properties.get("type") == "entity" for node in graph.nodes)
    assert graph.nodes[0].id == "alice"
    assert graph.nodes[0].caption == "Alice"
    assert len(graph.relationships) == 1
    rel = graph.relationships[0]
    assert rel.type != "participates"
    assert rel.caption != "participates"
    assert "participates" not in (rel.type or "")
    assert "participates" not in (rel.caption or "")
    assert session.calls[0][0] == node_graph_engine.ENTITY_GRAPH_NODES_CYPHER
    assert session.calls[1][0] == ENTITY_GRAPH_RELS_CYPHER
    assert session.calls[1][1]["is_latest"] is True


def test_entity_graph_rels_cypher_filters_entity_entity_not_participates():
    assert "a.type = 'entity' AND b.type = 'entity'" in ENTITY_GRAPH_RELS_CYPHER
    assert "participates" not in ENTITY_GRAPH_RELS_CYPHER


@pytest.mark.asyncio
async def test_event_graph_cypher_and_mapping_exclude_entities():
    assert "type:'event'" in EVENT_GRAPH_NODES_CYPHER.replace(" ", "")

    session = FakeSession()
    session.enqueue([{"n": FakeNode(id="summit", name="Summit", type="event")}])
    session.enqueue([])

    graph = await get_event_graph(session, is_latest=True, limit=200)

    assert all(node.properties.get("type") != "entity" for node in graph.nodes)
    assert all(node.properties.get("type") == "event" for node in graph.nodes)
    assert graph.nodes[0].id == "summit"
    assert session.calls[0][0] == node_graph_engine.EVENT_GRAPH_NODES_CYPHER


@pytest.mark.asyncio
async def test_participation_graph_maps_event_entity_only():
    assert "normalized_relation:'participates'" in PARTICIPATION_GRAPH_CYPHER.replace(" ", "")
    assert "HAS_CONCEPT" not in PARTICIPATION_GRAPH_CYPHER
    assert "OPTIONAL MATCH" not in PARTICIPATION_GRAPH_CYPHER

    session = FakeSession()
    session.enqueue(
        [
            {
                "ev": FakeNode(id="summit", name="Summit", type="event"),
                "r": FakeRel(
                    element_id="part-1",
                    relation="participates",
                    normalized_relation="participates",
                ),
                "e": FakeNode(id="alice", name="Alice", type="entity"),
            }
        ]
    )

    graph = await get_participation_graph(session, limit=200)

    node_ids = {node.id for node in graph.nodes}
    types = {node.properties.get("type") for node in graph.nodes}
    assert node_ids == {"summit", "alice"}
    assert types == {"event", "entity"}
    assert len(graph.nodes) == 2
    assert len(graph.relationships) == 1
    rel = graph.relationships[0]
    assert rel.type == "participates"
    assert rel.caption == "participates"
    dumped = rel.model_dump(by_alias=True)
    assert dumped["from"] == "summit"
    assert dumped["to"] == "alice"


@pytest.mark.asyncio
async def test_concept_neighbors_includes_entity_and_event_bridge():
    session = FakeSession()
    session.enqueue([{"c": FakeNode(id="abc", name="technology")}])
    session.enqueue(
        [
            {"id": "alice", "name": "Alice", "type": "entity"},
            {"id": "summit", "name": "Summit", "type": "event"},
        ]
    )

    graph = await get_concept_neighbors(session, "abc")

    types = {node.properties.get("type") for node in graph.nodes}
    assert "entity" in types
    assert "event" in types
    assert "concept" in types
    by_id = {node.id: node for node in graph.nodes}
    assert by_id["abc"].caption == "technology"
    assert by_id["alice"].properties["type"] == "entity"
    assert by_id["summit"].properties["type"] == "event"
    assert len(graph.relationships) == 2
    assert all(rel.type == "HAS_CONCEPT" for rel in graph.relationships)
    dumped = [rel.model_dump(by_alias=True) for rel in graph.relationships]
    assert {row["from"] for row in dumped} == {"alice", "summit"}
    assert {row["to"] for row in dumped} == {"abc"}


@pytest.mark.asyncio
async def test_concept_neighbors_missing_returns_empty():
    session = FakeSession()
    session.enqueue([])

    graph = await get_concept_neighbors(session, "missing")

    assert graph.nodes == []
    assert graph.relationships == []
    assert len(session.calls) == 1


def test_no_cypher_matches_fact_or_chunk_labels():
    for cypher in _cyphers():
        assert ":Fact" not in cypher
        assert ":Chunk" not in cypher
