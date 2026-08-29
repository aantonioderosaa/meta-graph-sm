"""Unit tests for entity↔entity Relation resolution (Macrotask 4.1). No Docker."""

from __future__ import annotations

import pytest

from app.models.relations import RelationClassification, RelationLabel
from app.pipeline.entity_relation_resolution import (
    APPLY_EXTENDS_CYPHER,
    APPLY_SUPERSEDES_CYPHER,
    APPLY_UPDATED_BY_CYPHER,
    APPLY_UPDATES_CYPHER,
    FIND_FRESH_ENTITY_RELS_CYPHER,
    FIND_FRESH_ENTITY_RELS_TOUCHED_CYPHER,
    FIND_SAME_ENDPOINT_RELS_CYPHER,
    MARK_CONTRADICTS_CYPHER,
    MARK_PROCESSED_NONE_CYPHER,
    VECTOR_ASSISTED_SAME_ENDPOINT_RELS_CYPHER,
    classify_and_apply_entity_relation,
    find_entity_relation_candidates,
    resolve_fresh_entity_relations,
)
from app.pipeline.ingestion import CREATE_CONTRADICTS_CYPHER

JOB_ID = "job-entity-rel-1"
HEAD_ID = "alice-1"
TAIL_ID = "acme-1"
NEW_REL_ID = "rel-new"
OLD_REL_ID = "rel-old"


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


def _all_cypher() -> list[str]:
    return [
        FIND_FRESH_ENTITY_RELS_CYPHER,
        FIND_FRESH_ENTITY_RELS_TOUCHED_CYPHER,
        FIND_SAME_ENDPOINT_RELS_CYPHER,
        VECTOR_ASSISTED_SAME_ENDPOINT_RELS_CYPHER,
        APPLY_UPDATES_CYPHER,
        APPLY_EXTENDS_CYPHER,
        MARK_PROCESSED_NONE_CYPHER,
        MARK_CONTRADICTS_CYPHER,
        APPLY_SUPERSEDES_CYPHER,
        APPLY_UPDATED_BY_CYPHER,
    ]


def _disable_temporal(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.pipeline.entity_relation_resolution.settings.ENABLE_TEMPORAL_TRANSITIONS",
        False,
    )


async def _boom_classify(*_args, **_kwargs):
    raise AssertionError("classify_relation must not be called")


def _enqueue_same_endpoint_only(session: FakeSession, old_relation: str) -> None:
    session.enqueue(
        [
            {
                "rel_id": OLD_REL_ID,
                "relation": old_relation,
                "normalized_relation": old_relation,
            }
        ]
    )
    session.enqueue([])  # no head embedding → skip vector assist


@pytest.mark.asyncio
async def test_flag_off_replaces_still_applies_updates(monkeypatch):
    _disable_temporal(monkeypatch)
    session = FakeSession()
    _enqueue_same_endpoint_only(session, "works at Acme")

    async def fake_classify(new_rel_text, old_rel_text, job_id=None, **_kwargs):
        assert new_rel_text == "works at Beta"
        assert old_rel_text == "works at Acme"
        assert job_id == JOB_ID
        return RelationClassification(relation=RelationLabel.replaces)

    monkeypatch.setattr(
        "app.pipeline.entity_relation_resolution.classify_relation",
        fake_classify,
    )

    outcome = await classify_and_apply_entity_relation(
        session,
        HEAD_ID,
        TAIL_ID,
        NEW_REL_ID,
        "works at Beta",
        JOB_ID,
    )

    assert outcome == "updates"
    apply_calls = [call for call in session.calls if call[0] == APPLY_UPDATES_CYPHER]
    assert len(apply_calls) == 1
    _, kwargs = apply_calls[0]
    assert kwargs["head_id"] == HEAD_ID
    assert kwargs["tail_id"] == TAIL_ID
    assert kwargs["new_rel_id"] == NEW_REL_ID
    assert kwargs["old_rel_id"] == OLD_REL_ID
    compact = _compact(APPLY_UPDATES_CYPHER)
    assert "neu.normalized_relation = 'updates'" in compact
    assert "old.is_latest = false" in compact
    assert not any(call[0] == APPLY_SUPERSEDES_CYPHER for call in session.calls)


@pytest.mark.asyncio
async def test_supersedes_flips_old_and_creates_famiglia_b(monkeypatch):
    session = FakeSession()
    _enqueue_same_endpoint_only(session, "calciatore")

    async def fake_classify(*_args, **_kwargs):
        return RelationClassification(relation=RelationLabel.supersedes)

    monkeypatch.setattr(
        "app.pipeline.entity_relation_resolution.classify_relation",
        fake_classify,
    )

    outcome = await classify_and_apply_entity_relation(
        session,
        HEAD_ID,
        TAIL_ID,
        NEW_REL_ID,
        "dal 2018 presidente",
        JOB_ID,
    )

    assert outcome == "supersedes"
    apply_calls = [call for call in session.calls if call[0] == APPLY_SUPERSEDES_CYPHER]
    assert len(apply_calls) == 1
    _, kwargs = apply_calls[0]
    assert kwargs["new_rel_id"] == NEW_REL_ID
    assert kwargs["old_rel_id"] == OLD_REL_ID
    compact = _compact(APPLY_SUPERSEDES_CYPHER)
    assert "old.is_latest = false" in compact
    assert "[:SUPERSEDES" in compact
    assert "new_rel_id: $new_rel_id" in compact
    assert "old_rel_id: $old_rel_id" in compact
    assert not any(call[0] == APPLY_UPDATES_CYPHER for call in session.calls)
    assert not any(call[0] == APPLY_UPDATED_BY_CYPHER for call in session.calls)


@pytest.mark.asyncio
async def test_updated_by_keeps_old_relation_but_not_latest(monkeypatch):
    session = FakeSession()
    _enqueue_same_endpoint_only(session, "vinto nel 2010")

    async def fake_classify(*_args, **_kwargs):
        return RelationClassification(relation=RelationLabel.updated_by)

    monkeypatch.setattr(
        "app.pipeline.entity_relation_resolution.classify_relation",
        fake_classify,
    )

    outcome = await classify_and_apply_entity_relation(
        session,
        HEAD_ID,
        TAIL_ID,
        NEW_REL_ID,
        "in realtà mi sono sbagliato, non nel 2010 ma nel 2011",
        JOB_ID,
    )

    assert outcome == "updated_by"
    apply_calls = [call for call in session.calls if call[0] == APPLY_UPDATED_BY_CYPHER]
    assert len(apply_calls) == 1
    compact = _compact(APPLY_UPDATED_BY_CYPHER)
    assert "old.is_latest = false" in compact
    assert "[:UPDATED_BY" in compact
    assert "DELETE" not in compact
    assert not any(call[0] == APPLY_SUPERSEDES_CYPHER for call in session.calls)


@pytest.mark.asyncio
async def test_contradicts_keeps_both_latest_never_updated_by(monkeypatch):
    session = FakeSession()
    _enqueue_same_endpoint_only(session, "vinto nel 2010")

    async def fake_classify(*_args, **_kwargs):
        return RelationClassification(relation=RelationLabel.contradicts)

    monkeypatch.setattr(
        "app.pipeline.entity_relation_resolution.classify_relation",
        fake_classify,
    )

    outcome = await classify_and_apply_entity_relation(
        session,
        HEAD_ID,
        TAIL_ID,
        NEW_REL_ID,
        "vinto nel 2011",
        JOB_ID,
    )

    assert outcome == "contradicts"
    mark_calls = [call for call in session.calls if call[0] == MARK_CONTRADICTS_CYPHER]
    assert len(mark_calls) == 1
    assert "is_latest" not in _compact(MARK_CONTRADICTS_CYPHER)
    contra = [call for call in session.calls if call[0] == CREATE_CONTRADICTS_CYPHER]
    assert len(contra) == 1
    _, kwargs = contra[0]
    assert kwargs["left_id"] == TAIL_ID
    assert kwargs["right_id"] == TAIL_ID
    assert kwargs["subject_id"] == HEAD_ID
    assert not any(call[0] == APPLY_UPDATED_BY_CYPHER for call in session.calls)
    assert not any(call[0] == APPLY_SUPERSEDES_CYPHER for call in session.calls)
    assert not any(call[0] == APPLY_UPDATES_CYPHER for call in session.calls)


@pytest.mark.asyncio
async def test_extends_sets_normalized_without_flipping_old(monkeypatch):
    session = FakeSession()
    _enqueue_same_endpoint_only(session, "works at Acme")

    async def fake_classify(*_args, **_kwargs):
        return RelationClassification(relation=RelationLabel.extends)

    monkeypatch.setattr(
        "app.pipeline.entity_relation_resolution.classify_relation",
        fake_classify,
    )

    outcome = await classify_and_apply_entity_relation(
        session,
        HEAD_ID,
        TAIL_ID,
        NEW_REL_ID,
        "has a gym on the roof",
        JOB_ID,
    )

    assert outcome == "extends"
    apply_calls = [call for call in session.calls if call[0] == APPLY_EXTENDS_CYPHER]
    assert len(apply_calls) == 1
    _, kwargs = apply_calls[0]
    assert kwargs["new_rel_id"] == NEW_REL_ID
    compact = _compact(APPLY_EXTENDS_CYPHER)
    assert "neu.normalized_relation = 'extends'" in compact
    assert "is_latest" not in compact
    assert not any(call[0] == APPLY_UPDATES_CYPHER for call in session.calls)


@pytest.mark.asyncio
async def test_none_marks_fresh_rel_processed_old_unchanged(monkeypatch):
    session = FakeSession()
    _enqueue_same_endpoint_only(session, "works at Acme")

    async def fake_classify(*_args, **_kwargs):
        return RelationClassification(relation=RelationLabel.none)

    monkeypatch.setattr(
        "app.pipeline.entity_relation_resolution.classify_relation",
        fake_classify,
    )

    outcome = await classify_and_apply_entity_relation(
        session,
        HEAD_ID,
        TAIL_ID,
        NEW_REL_ID,
        "lives in Rome",
        JOB_ID,
    )

    assert outcome == "none"
    mark_calls = [call for call in session.calls if call[0] == MARK_PROCESSED_NONE_CYPHER]
    assert len(mark_calls) == 1
    _, kwargs = mark_calls[0]
    assert kwargs["new_rel_id"] == NEW_REL_ID
    compact = _compact(MARK_PROCESSED_NONE_CYPHER)
    assert "neu.normalized_relation = neu.relation" in compact
    assert "is_latest" not in compact
    assert not any(call[0] == APPLY_UPDATES_CYPHER for call in session.calls)
    assert not any(call[0] == APPLY_EXTENDS_CYPHER for call in session.calls)


@pytest.mark.asyncio
async def test_candidate_queries_are_scoped(monkeypatch):
    session = FakeSession()
    session.enqueue([])
    session.enqueue([{"embedding": [0.1, 0.2]}])
    session.enqueue([])
    monkeypatch.setattr(
        "app.pipeline.entity_relation_resolution.classify_relation",
        _boom_classify,
    )

    await find_entity_relation_candidates(session, HEAD_ID, TAIL_ID, NEW_REL_ID)

    same_ep, same_kwargs = session.calls[0]
    assert same_ep == FIND_SAME_ENDPOINT_RELS_CYPHER
    assert same_kwargs["head_id"] == HEAD_ID
    assert same_kwargs["tail_id"] == TAIL_ID
    assert same_kwargs["rel_id"] == NEW_REL_ID
    assert "elementId(r)" in same_ep
    assert "$head_id" in same_ep
    assert "$tail_id" in same_ep

    vector_calls = [call for call in session.calls if "vector.queryNodes" in call[0]]
    assert vector_calls
    vector_cypher, vector_kwargs = vector_calls[0]
    assert vector_cypher == VECTOR_ASSISTED_SAME_ENDPOINT_RELS_CYPHER
    assert vector_kwargs["head_id"] == HEAD_ID
    assert vector_kwargs["tail_id"] == TAIL_ID
    assert "MATCH (a:Node {id: $head_id})-[r:Relation]->(b:Node {id: $tail_id})" in _compact(
        vector_cypher
    )

    forbidden = "MATCH ()-[r:Relation]->()"
    for cypher, _kwargs in session.calls:
        assert forbidden not in _compact(cypher)


def test_module_cypher_has_no_unfiltered_relation_scan():
    forbidden = "MATCH ()-[r:Relation]->()"
    for cypher in _all_cypher():
        assert forbidden not in _compact(cypher)
    assert "normalized_relation IS NULL" in FIND_FRESH_ENTITY_RELS_CYPHER
    assert "type:'entity'" in FIND_FRESH_ENTITY_RELS_CYPHER
    assert "$touched_ids" in FIND_FRESH_ENTITY_RELS_TOUCHED_CYPHER
    assert "a.id IN $touched_ids OR b.id IN $touched_ids" in _compact(
        FIND_FRESH_ENTITY_RELS_TOUCHED_CYPHER
    )


@pytest.mark.asyncio
async def test_resolve_fresh_scoped_to_touched_ids():
    session = FakeSession()
    session.enqueue([])

    count = await resolve_fresh_entity_relations(
        session, JOB_ID, touched_entity_ids={"alice-1"}
    )

    assert count == 0
    cypher, kwargs = session.calls[0]
    assert cypher == FIND_FRESH_ENTITY_RELS_TOUCHED_CYPHER
    assert kwargs["touched_ids"] == ["alice-1"]
    assert "MATCH ()-[r:Relation]->()" not in _compact(cypher)


@pytest.mark.asyncio
async def test_resolve_fresh_without_touched_still_filters_null_normalized():
    session = FakeSession()
    session.enqueue([])

    count = await resolve_fresh_entity_relations(session, JOB_ID)

    assert count == 0
    cypher, _kwargs = session.calls[0]
    assert cypher == FIND_FRESH_ENTITY_RELS_CYPHER
    assert "normalized_relation IS NULL" in cypher
    assert "merged_into IS NULL" in cypher
    assert "MATCH ()-[r:Relation]->()" not in _compact(cypher)
