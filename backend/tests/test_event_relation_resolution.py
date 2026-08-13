"""Unit tests for event dedup + event↔event classification (Macrotask 4.2). No Docker."""

from __future__ import annotations

import pytest

from app.models.node_extraction import EventRelationClassification, EventRelationLabel, SequenceType
from app.pipeline.event_relation_resolution import (
    CREATE_EVENT_SEQUENCE_CYPHER,
    FIND_EVENT_CANDIDATES_CYPHER,
    FIND_FRESH_EVENTS_CYPHER,
    SET_EVENT_SEQUENCE_CYPHER,
    SHARED_ENTITIES_CYPHER,
    classify_event_relation,
    resolve_event,
    resolve_fresh_events,
)
from app.pipeline.node_resolution import FIND_EXACT_NAME_CYPHER, FIND_NODE_CANDIDATES_CYPHER

JOB_ID = "job-event-rel-1"
EMBEDDING = [0.1, 0.2, 0.3]
FRESH_ID = "ev-new"
CANDIDATE_ID = "ev-old"


class FakeResult:
    def __init__(self, records: list[dict]):
        self._records = records

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for record in self._records:
            yield record


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


async def _boom_llm(*_args, **_kwargs):
    raise AssertionError("LLM / call_structured must not be called on this fast-path")


def _spy_merge(monkeypatch) -> list[tuple[str, str]]:
    merges: list[tuple[str, str]] = []

    async def fake_merge(_session, dup_id: str, canon_id: str) -> None:
        merges.append((dup_id, canon_id))

    monkeypatch.setattr("app.pipeline.event_relation_resolution.merge_nodes", fake_merge)
    return merges


@pytest.mark.asyncio
async def test_shared_zero_does_not_merge_even_at_high_score(monkeypatch):
    session = FakeSession()
    session.enqueue([])  # exact name
    session.enqueue(
        [{"id": CANDIDATE_ID, "name": "Similar meeting", "score": 0.95}]
    )
    session.enqueue([{"shared": 0}])

    async def fake_llm(*_args, **_kwargs):
        return EventRelationClassification(
            label=EventRelationLabel.same_event, sequence_type=None
        )

    monkeypatch.setattr("app.pipeline.event_relation_resolution.call_structured", fake_llm)
    merges = _spy_merge(monkeypatch)

    result = await resolve_event(
        session, FRESH_ID, "Weekly meeting", EMBEDDING, JOB_ID
    )

    assert result == FRESH_ID
    assert merges == []
    shared_calls = [call for call in session.calls if call[0] == SHARED_ENTITIES_CYPHER]
    assert shared_calls
    assert not any("CREATE" in call[0] and ":Relation" in call[0] for call in session.calls)


@pytest.mark.asyncio
async def test_shared_two_sequenced_precedes_writes_relation_no_merge(monkeypatch):
    session = FakeSession()
    session.enqueue([])  # exact name
    session.enqueue([{"id": CANDIDATE_ID, "name": "Kickoff", "score": 0.85}])
    session.enqueue([{"shared": 2}])
    session.enqueue([])  # no existing raw rel intended direction
    session.enqueue([])  # no reverse raw rel

    async def fake_llm(*_args, **_kwargs):
        return EventRelationClassification(
            label=EventRelationLabel.sequenced,
            sequence_type=SequenceType.precedes,
        )

    monkeypatch.setattr("app.pipeline.event_relation_resolution.call_structured", fake_llm)
    merges = _spy_merge(monkeypatch)

    result = await resolve_event(session, FRESH_ID, "Follow-up", EMBEDDING, JOB_ID)

    assert result == FRESH_ID
    assert merges == []
    write_calls = [call for call in session.calls if call[0] == CREATE_EVENT_SEQUENCE_CYPHER]
    assert len(write_calls) == 1
    _, kwargs = write_calls[0]
    assert kwargs["normalized_relation"] == "precedes"
    assert kwargs["head_id"] == FRESH_ID
    assert kwargs["tail_id"] == CANDIDATE_ID
    assert kwargs["relation"] == "precedes"
    assert not any(call[0] == SET_EVENT_SEQUENCE_CYPHER for call in session.calls)


@pytest.mark.asyncio
async def test_exact_name_and_shared_merges_without_llm(monkeypatch):
    session = FakeSession()
    session.enqueue([{"id": CANDIDATE_ID, "name": "Standup"}])
    session.enqueue([{"shared": 1}])
    monkeypatch.setattr("app.pipeline.event_relation_resolution.call_structured", _boom_llm)
    merges = _spy_merge(monkeypatch)

    result = await resolve_event(session, FRESH_ID, "Standup", EMBEDDING, JOB_ID)

    assert result == CANDIDATE_ID
    assert merges == [(FRESH_ID, CANDIDATE_ID)]
    assert session.calls[0][0] == FIND_EXACT_NAME_CYPHER
    assert session.calls[0][1]["type"] == "event"
    assert not any("vector.queryNodes" in call[0] for call in session.calls)


def test_fresh_event_query_filters_undreamed_events():
    compact = _compact(FIND_FRESH_EVENTS_CYPHER)
    assert "type:'event'" in compact or "type: 'event'" in compact
    assert "dreamed:false" in compact or "dreamed: false" in compact
    assert "merged_into IS NULL" in FIND_FRESH_EVENTS_CYPHER
    assert "MATCH (n:Node) RETURN n" not in compact
    assert "n.id" in FIND_FRESH_EVENTS_CYPHER
    assert "n.name" in FIND_FRESH_EVENTS_CYPHER
    assert "n.embedding" in FIND_FRESH_EVENTS_CYPHER


def test_no_unfiltered_node_scan():
    for cypher in (
        FIND_FRESH_EVENTS_CYPHER,
        FIND_EVENT_CANDIDATES_CYPHER,
        SHARED_ENTITIES_CYPHER,
        CREATE_EVENT_SEQUENCE_CYPHER,
        SET_EVENT_SEQUENCE_CYPHER,
        FIND_NODE_CANDIDATES_CYPHER,
    ):
        assert "MATCH (n:Node) RETURN n" not in _compact(cypher)


@pytest.mark.asyncio
async def test_resolve_fresh_events_uses_undreamed_filter(monkeypatch):
    session = FakeSession()
    session.enqueue([])
    monkeypatch.setattr("app.pipeline.event_relation_resolution.call_structured", _boom_llm)
    merges = _spy_merge(monkeypatch)

    touched = await resolve_fresh_events(session, JOB_ID)

    assert touched == set()
    assert merges == []
    cypher, _kwargs = session.calls[0]
    assert cypher == FIND_FRESH_EVENTS_CYPHER
    assert "dreamed:false" in _compact(cypher)
    assert "MATCH (n:Node) RETURN n" not in _compact(cypher)


@pytest.mark.asyncio
async def test_classify_same_event_with_zero_shared_coerced_to_none(monkeypatch):
    async def fake_llm(*_args, **_kwargs):
        return EventRelationClassification(label=EventRelationLabel.same_event)

    monkeypatch.setattr("app.pipeline.event_relation_resolution.call_structured", fake_llm)

    verdict = await classify_event_relation("A", "B", shared_count=0, job_id=JOB_ID)

    assert verdict.label == EventRelationLabel.none
    assert verdict.sequence_type is None


@pytest.mark.asyncio
async def test_classify_sequenced_without_sequence_type_coerced_to_none(monkeypatch):
    async def fake_llm(*_args, **_kwargs):
        return EventRelationClassification(
            label=EventRelationLabel.sequenced, sequence_type=None
        )

    monkeypatch.setattr("app.pipeline.event_relation_resolution.call_structured", fake_llm)

    verdict = await classify_event_relation("A", "B", shared_count=2, job_id=JOB_ID)

    assert verdict.label == EventRelationLabel.none
