"""Macrotask 8 named acceptance tests (scenarios 1–4, 6). No Docker.

Scenario 5 is the existing pytest/vitest suite: this file is additive and does not
change assertions in those tests.
"""

from __future__ import annotations

import pytest

from app.models.node_extraction import (
    ConceptResult,
    EntityExtractionResult,
    EventEntityExtractionResult,
    EventEntityParticipation,
    EventRelationClassification,
    EventRelationExtractionResult,
    EventRelationLabel,
    PairRelationDecision,
)
from app.pipeline.chunking import Chunk
from app.pipeline.dreaming import FIND_FRESH_ENTITIES_CYPHER, _resolve_fresh_entities
from app.pipeline.entity_relation_resolution import FIND_FRESH_ENTITY_RELS_TOUCHED_CYPHER
from app.pipeline.event_relation_resolution import FIND_FRESH_EVENTS_CYPHER, resolve_event
from app.pipeline.ingestion import process_chunk_node_extraction
from app.pipeline.node_graph_engine import get_concept_neighbors
from app.pipeline.node_resolution import FIND_NODE_CANDIDATES_CYPHER, resolve_node

JOB_ID = "job-m8-acceptance"
CHUNK = Chunk(id="chunk-m8", doc_id="doc-m8", text="Alice and Acme attended the product launch.")
EMBEDDING = [0.1, 0.2, 0.3]
BLANKET_NODE_SCAN = "MATCH (n:Node) RETURN n"
BLANKET_REL_SCAN = "MATCH ()-[r:Relation]->() RETURN r"


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


def _compact(cypher: str) -> str:
    return " ".join(cypher.split())


def _is_scoped(cypher: str) -> bool:
    compact = _compact(cypher)
    has_scope = (
        "dreamed:false" in compact
        or "dreamed: false" in compact
        or "normalized_relation IS NULL" in cypher
        or "touched_ids" in cypher
        or "db.index.vector.queryNodes" in cypher
    )
    return has_scope


def _assert_not_blanket(cypher: str) -> None:
    compact = _compact(cypher)
    assert BLANKET_NODE_SCAN not in compact
    assert BLANKET_REL_SCAN not in compact


async def _boom_llm(*_args, **_kwargs):
    raise AssertionError("LLM / call_structured must not be called on this fast-path")


def _patch_scenario1_extractors(monkeypatch) -> None:
    async def mock_entities(
        chunk_text: str, job_id: str | None = None, corpus_summary: str = ""
    ):
        _ = chunk_text, job_id, corpus_summary
        return EntityExtractionResult(entities=[])

    async def mock_pair(*_args, **_kwargs):
        return PairRelationDecision(related=False)

    async def mock_event_entity(chunk_text: str, job_id: str | None = None):
        _ = chunk_text, job_id
        return EventEntityExtractionResult(
            participations=[
                EventEntityParticipation(
                    event="product launch",
                    entities=["Alice", "Acme"],
                )
            ]
        )

    async def mock_event_rel(chunk_text: str, job_id: str | None = None):
        _ = chunk_text, job_id
        return EventRelationExtractionResult(triples=[])

    async def mock_concepts(*_args, **_kwargs):
        return ConceptResult(concepts=[])

    monkeypatch.setattr("app.pipeline.node_extraction.extract_entities", mock_entities)
    monkeypatch.setattr("app.pipeline.node_extraction.extract_pair_relation", mock_pair)
    monkeypatch.setattr("app.pipeline.node_extraction.extract_event_entities", mock_event_entity)
    monkeypatch.setattr("app.pipeline.node_extraction.extract_event_relations", mock_event_rel)
    monkeypatch.setattr("app.pipeline.node_extraction.extract_entity_concepts", mock_concepts)
    monkeypatch.setattr("app.pipeline.node_extraction.extract_event_concepts", mock_concepts)
    monkeypatch.setattr("app.pipeline.embeddings.embed", lambda name: [0.0] * 768)


@pytest.mark.enable_node_extraction
@pytest.mark.asyncio
async def test_scenario1_ingest_writes_two_entities_one_event_two_participates(monkeypatch):
    """Clean ingest: 2 entities, 1 event, 2 participates; no entity↔entity relation."""
    _patch_scenario1_extractors(monkeypatch)
    session = FakeSession()

    count = await process_chunk_node_extraction(session, CHUNK, "doc-m8", JOB_ID)

    assert count == 3
    kwargs_list = [kw for _, kw in session.calls]
    entity_writes = [kw for kw in kwargs_list if kw.get("type") == "entity"]
    event_writes = [kw for kw in kwargs_list if kw.get("type") == "event"]
    participates = [kw for kw in kwargs_list if kw.get("normalized_relation") == "participates"]
    entity_entity_rels = [
        kw
        for kw in kwargs_list
        if "head_id" in kw and kw.get("normalized_relation") is None
    ]

    assert len(entity_writes) == 2
    assert {kw["name"] for kw in entity_writes} == {"Alice", "Acme"}
    assert len(event_writes) == 1
    assert event_writes[0]["name"] == "product launch"
    assert len(participates) == 2
    assert entity_entity_rels == []


@pytest.mark.asyncio
async def test_scenario2_exact_name_entity_merge_skips_llm(monkeypatch):
    """Dedup: entity exact-name (M3) skips LLM; event merge only if shared>=1 (M4.2)."""
    entity_session = FakeSession()
    entity_session.enqueue([{"id": "alice-canon", "name": "Alice"}])
    monkeypatch.setattr("app.pipeline.node_resolution.call_structured", _boom_llm)
    entity_merges: list[tuple[str, str]] = []

    async def fake_entity_merge(_session, dup_id: str, canon_id: str) -> None:
        entity_merges.append((dup_id, canon_id))

    monkeypatch.setattr("app.pipeline.node_resolution.merge_nodes", fake_entity_merge)

    entity_canon = await resolve_node(
        entity_session,
        node_id="alice-new",
        node_type="entity",
        name="Alice",
        embedding=EMBEDDING,
        job_id=JOB_ID,
    )

    assert entity_canon == "alice-canon"
    assert entity_merges == [("alice-new", "alice-canon")]
    assert not any("vector.queryNodes" in call[0] for call in entity_session.calls)

    event_session = FakeSession()
    event_session.enqueue([{"id": "ev-canon", "name": "product launch"}])
    event_session.enqueue([{"shared": 1}])
    monkeypatch.setattr("app.pipeline.event_relation_resolution.call_structured", _boom_llm)
    event_merges: list[tuple[str, str]] = []

    async def fake_event_merge(_session, dup_id: str, canon_id: str) -> None:
        event_merges.append((dup_id, canon_id))

    monkeypatch.setattr("app.pipeline.event_relation_resolution.merge_nodes", fake_event_merge)

    event_canon = await resolve_event(
        event_session, "ev-new", "product launch", EMBEDDING, JOB_ID
    )

    assert event_canon == "ev-canon"
    assert event_merges == [("ev-new", "ev-canon")]

    no_share_session = FakeSession()
    no_share_session.enqueue([{"id": "ev-other", "name": "product launch"}])
    no_share_session.enqueue([{"shared": 0}])
    skipped_merges: list[tuple[str, str]] = []

    async def record_skipped(_session, dup_id: str, canon_id: str) -> None:
        skipped_merges.append((dup_id, canon_id))

    async def fake_event_llm(*_args, **_kwargs):
        return EventRelationClassification(label=EventRelationLabel.same_event)

    monkeypatch.setattr("app.pipeline.event_relation_resolution.merge_nodes", record_skipped)
    monkeypatch.setattr("app.pipeline.event_relation_resolution.call_structured", fake_event_llm)

    kept = await resolve_event(
        no_share_session, "ev-fresh", "product launch", EMBEDDING, JOB_ID
    )

    assert kept == "ev-fresh"
    assert skipped_merges == []


def test_scenario3_fresh_queries_are_scoped():
    """Incremental update: fresh queries are scoped; never a blanket Node/Relation scan."""
    for cypher in (
        FIND_FRESH_ENTITIES_CYPHER,
        FIND_FRESH_EVENTS_CYPHER,
        FIND_FRESH_ENTITY_RELS_TOUCHED_CYPHER,
        FIND_NODE_CANDIDATES_CYPHER,
    ):
        assert _is_scoped(cypher), cypher
        _assert_not_blanket(cypher)

    assert "dreamed:false" in _compact(FIND_FRESH_ENTITIES_CYPHER)
    assert "dreamed:false" in _compact(FIND_FRESH_EVENTS_CYPHER)
    assert "normalized_relation IS NULL" in FIND_FRESH_ENTITY_RELS_TOUCHED_CYPHER
    assert "touched_ids" in FIND_FRESH_ENTITY_RELS_TOUCHED_CYPHER
    assert "db.index.vector.queryNodes" in FIND_NODE_CANDIDATES_CYPHER


@pytest.mark.asyncio
async def test_scenario4_concept_neighbors_include_entity_and_event():
    """Concept bridge: get_concept_neighbors returns both an entity and an event."""
    session = FakeSession()
    session.enqueue([{"c": FakeNode(id="tech-id", name="technology")}])
    session.enqueue(
        [
            {"id": "alice", "name": "Alice", "type": "entity"},
            {"id": "launch", "name": "product launch", "type": "event"},
        ]
    )

    graph = await get_concept_neighbors(session, "tech-id")

    types = {node.properties.get("type") for node in graph.nodes}
    assert "entity" in types
    assert "event" in types
    by_id = {node.id: node for node in graph.nodes}
    assert by_id["alice"].properties["type"] == "entity"
    assert by_id["launch"].properties["type"] == "event"


@pytest.mark.asyncio
async def test_scenario6_dreaming_skips_already_dreamed_nodes(monkeypatch):
    """Cost: _resolve_fresh_entities only walks dreamed:false rows, not the whole graph."""
    session = FakeSession()
    session.enqueue([{"id": "fresh-alice", "name": "Alice", "embedding": EMBEDDING}])
    resolve_ids: list[str] = []

    async def spy_resolve(_session, node_id, node_type, name, embedding, job_id):
        _ = node_type, name, embedding, job_id
        resolve_ids.append(node_id)
        return node_id

    monkeypatch.setattr("app.pipeline.dreaming.node_resolution.resolve_node", spy_resolve)
    monkeypatch.setattr("app.pipeline.dreaming.event_bus.publish", _noop_publish)

    touched = await _resolve_fresh_entities(session, JOB_ID)

    assert session.calls[0][0] == FIND_FRESH_ENTITIES_CYPHER
    assert "dreamed:false" in _compact(FIND_FRESH_ENTITIES_CYPHER)
    _assert_not_blanket(session.calls[0][0])
    assert resolve_ids == ["fresh-alice"]
    assert len(resolve_ids) != 3
    assert touched == {"fresh-alice"}


async def _noop_publish(*_args, **_kwargs) -> None:
    return None
