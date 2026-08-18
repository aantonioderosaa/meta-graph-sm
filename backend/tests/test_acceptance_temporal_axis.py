"""Fase 9 acceptance: temporal axis — versions, two times, three transitions."""

from __future__ import annotations

import json

import pytest

from app.core.config import Settings
from app.models.relations import RelationClassification, RelationLabel
from app.pipeline.entity_relation_resolution import (
    APPLY_SUPERSEDES_CYPHER,
    APPLY_UPDATED_BY_CYPHER,
    APPLY_UPDATES_CYPHER,
    MARK_CONTRADICTS_CYPHER,
    SYSTEM_PROMPT,
    classify_and_apply_entity_relation,
    map_temporal_transition,
    temporal_transitions_enabled,
)
from app.pipeline.identity_resolution import LINK_SAME_AS_CYPHER, index_entity_version
from app.pipeline.ingestion import (
    CREATE_CONTRADICTS_CYPHER,
    CREATE_NODE_RELATION_CYPHER,
    write_node_relation,
)

JOB_ID = "job-temporal-f9"
HEAD_ID = "weah-1"
TAIL_ID = "liberia-1"
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
    session.enqueue([])


async def _async_noop(*_args, **_kwargs):
    return None


def test_flag_defaults():
    assert Settings.model_fields["ENABLE_TEMPORAL_TRANSITIONS"].default is True
    assert Settings.model_fields["ENABLE_FACET_IDENTITY"].default is False


def test_temporal_flag_or_facet_identity(monkeypatch):
    monkeypatch.setattr(
        "app.pipeline.entity_relation_resolution.settings.ENABLE_TEMPORAL_TRANSITIONS",
        False,
    )
    monkeypatch.setattr(
        "app.pipeline.entity_relation_resolution.settings.ENABLE_FACET_IDENTITY",
        True,
    )
    assert temporal_transitions_enabled() is True
    monkeypatch.setattr(
        "app.pipeline.entity_relation_resolution.settings.ENABLE_FACET_IDENTITY",
        False,
    )
    assert temporal_transitions_enabled() is False


@pytest.mark.asyncio
async def test_index_entity_version_merges_same_as():
    session = FakeSession()
    await index_entity_version(session, "identity:weah:Agente", "weah-2018")
    assert len(session.calls) == 1
    cypher, kwargs = session.calls[0]
    assert cypher == LINK_SAME_AS_CYPHER
    assert kwargs["identity_id"] == "identity:weah:Agente"
    assert kwargs["facet_node_id"] == "weah-2018"
    compact = _compact(cypher)
    assert "[:SAME_AS]" in compact
    assert "DELETE" not in compact
    assert "merged_into" not in compact


def test_map_contradicts_authoritative_conflict_no_error_marker():
    """F9.5/F9.6: two sources, conflicting years, no correction → CONTRADICTS."""
    label = map_temporal_transition(
        "Fonte A: X ha vinto il torneo nel 2010.",
        "Fonte B: X ha vinto il torneo nel 2011.",
    )
    assert label == RelationLabel.contradicts
    assert label != RelationLabel.updated_by
    assert "CONTRADICTS" in SYSTEM_PROMPT or "`contradicts`" in SYSTEM_PROMPT
    assert "mai `updated_by`" in SYSTEM_PROMPT


def test_map_updated_by_explicit_correction():
    label = map_temporal_transition(
        "In realtà mi sono sbagliato, non nel 2010 ma nel 2011.",
        "X ha vinto il torneo nel 2010.",
    )
    assert label == RelationLabel.updated_by


def test_map_supersedes_legitimate_succession():
    label = map_temporal_transition(
        "Dal 2018 Weah è presidente.",
        "Weah era calciatore.",
    )
    assert label == RelationLabel.supersedes


def test_map_extends_complementary_not_a_transition():
    label = map_temporal_transition(
        "L'ufficio ha una palestra sul tetto.",
        "L'ufficio è a Milano.",
    )
    assert label == RelationLabel.extends


def test_map_updated_by_guardrail_no_error_wording():
    """F9.5: no explicit error wording → never UPDATED_BY."""
    label = map_temporal_transition(
        "Secondo la Gazzetta ha vinto nel 2010.",
        "Secondo la Repubblica ha vinto nel 2011.",
    )
    assert label == RelationLabel.contradicts
    assert label is not RelationLabel.updated_by


def test_create_node_relation_cypher_has_two_distinct_times():
    """F9.4: valid_time and system_time are separate properties."""
    compact = _compact(CREATE_NODE_RELATION_CYPHER)
    assert "valid_time: $valid_time" in compact
    assert "system_time: datetime()" in compact
    assert "valid_time: datetime()" not in compact
    assert "system_time: $valid_time" not in compact
    valid_idx = compact.index("valid_time: $valid_time")
    system_idx = compact.index("system_time: datetime()")
    assert valid_idx != system_idx


@pytest.mark.asyncio
async def test_write_node_relation_passes_valid_time_not_into_system_time(monkeypatch):
    monkeypatch.setattr("app.pipeline.ingestion.embeddings.embed", lambda _t: [0.1] * 8)
    monkeypatch.setattr(
        "app.pipeline.ingestion.deposit_from_asserted_fact",
        _async_noop,
    )
    session = FakeSession()
    await write_node_relation(
        session,
        head_id="h1",
        tail_id="t1",
        relation="ha vinto",
        normalized_relation=None,
        kernel_parent="SocialeIntenzionale",
        valid_time="2011",
        provenance={"doc_id": "doc-1", "run_id": JOB_ID},
    )
    cypher, kwargs = session.calls[0]
    assert cypher == CREATE_NODE_RELATION_CYPHER
    assert kwargs["valid_time"] == "2011"
    assert "system_time" not in kwargs
    assert kwargs["provenance"] == json.dumps(
        {"doc_id": "doc-1", "run_id": JOB_ID}, ensure_ascii=False, sort_keys=True
    )


@pytest.mark.asyncio
async def test_apply_contradicts_preserves_both_assertions(monkeypatch):
    session = FakeSession()
    _enqueue_same_endpoint_only(session, "vinto nel 2010")

    async def fake_classify(*_args, **_kwargs):
        return RelationClassification(relation=RelationLabel.contradicts)

    monkeypatch.setattr(
        "app.pipeline.entity_relation_resolution.classify_relation",
        fake_classify,
    )
    outcome = await classify_and_apply_entity_relation(
        session, HEAD_ID, TAIL_ID, NEW_REL_ID, "vinto nel 2011", JOB_ID
    )
    assert outcome == "contradicts"
    assert any(call[0] == CREATE_CONTRADICTS_CYPHER for call in session.calls)
    assert any(call[0] == MARK_CONTRADICTS_CYPHER for call in session.calls)
    assert "is_latest" not in _compact(MARK_CONTRADICTS_CYPHER)
    assert not any(call[0] == APPLY_UPDATED_BY_CYPHER for call in session.calls)
    assert not any(call[0] == APPLY_UPDATES_CYPHER for call in session.calls)


@pytest.mark.asyncio
async def test_apply_updated_by_from_correction_helper(monkeypatch):
    new_text = "In realtà mi sono sbagliato, non nel 2010 ma nel 2011."
    old_text = "X ha vinto il torneo nel 2010."
    assert map_temporal_transition(new_text, old_text) == RelationLabel.updated_by

    session = FakeSession()
    _enqueue_same_endpoint_only(session, old_text)

    async def fake_classify(*_args, **_kwargs):
        return RelationClassification(relation=map_temporal_transition(new_text, old_text))

    monkeypatch.setattr(
        "app.pipeline.entity_relation_resolution.classify_relation",
        fake_classify,
    )
    outcome = await classify_and_apply_entity_relation(
        session, HEAD_ID, TAIL_ID, NEW_REL_ID, new_text, JOB_ID
    )
    assert outcome == "updated_by"
    apply_calls = [call for call in session.calls if call[0] == APPLY_UPDATED_BY_CYPHER]
    assert len(apply_calls) == 1
    compact = _compact(APPLY_UPDATED_BY_CYPHER)
    assert "old.is_latest = false" in compact
    assert "DELETE" not in compact


@pytest.mark.asyncio
async def test_apply_supersedes_from_succession_helper(monkeypatch):
    new_text = "Dal 2018 Weah è presidente."
    old_text = "Weah era calciatore."
    assert map_temporal_transition(new_text, old_text) == RelationLabel.supersedes

    session = FakeSession()
    _enqueue_same_endpoint_only(session, old_text)

    async def fake_classify(*_args, **_kwargs):
        return RelationClassification(relation=map_temporal_transition(new_text, old_text))

    monkeypatch.setattr(
        "app.pipeline.entity_relation_resolution.classify_relation",
        fake_classify,
    )
    outcome = await classify_and_apply_entity_relation(
        session, HEAD_ID, TAIL_ID, NEW_REL_ID, new_text, JOB_ID
    )
    assert outcome == "supersedes"
    assert any(call[0] == APPLY_SUPERSEDES_CYPHER for call in session.calls)
    compact = _compact(APPLY_SUPERSEDES_CYPHER)
    assert "old.is_latest = false" in compact
    assert "[:SUPERSEDES" in compact
