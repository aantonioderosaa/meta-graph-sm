"""Unit tests for touched_fact_ids tracking (D1.1)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.relations import RelationClassification, RelationLabel
from app.pipeline.dreaming import DreamingStats, NewFactForRelations, _process_relation_detection
from app.pipeline.relations import Candidate


@pytest.mark.asyncio
async def test_touched_fact_ids_tracks_new_fact_and_evaluated_candidates(monkeypatch):
    """After N candidates are evaluated, touched set = {new} ∪ {candidates} — not the whole KB."""
    new = NewFactForRelations(
        fact_id="new-1",
        text="New fact",
        embedding=[0.1] * 8,
        source_doc_id="doc-a",
    )
    candidates = [
        Candidate(id="c1", text="Cand 1", score=0.9, via="embedding"),
        Candidate(id="c2", text="Cand 2", score=0.8, via="embedding"),
        Candidate(id="c3", text="Cand 3", score=0.7, via="doc"),
    ]
    # Unrelated KB facts that must NOT appear in touched_fact_ids
    unrelated = {"old-a", "old-b", "old-c", "old-d"}

    monkeypatch.setattr(
        "app.pipeline.dreaming.relations.find_candidates",
        AsyncMock(return_value=candidates),
    )
    monkeypatch.setattr(
        "app.pipeline.dreaming.relations.classify_relation",
        AsyncMock(
            side_effect=[
                RelationClassification(relation=RelationLabel.replaces),
                RelationClassification(relation=RelationLabel.none),
                RelationClassification(relation=RelationLabel.extends),
            ]
        ),
    )
    apply_mock = AsyncMock(side_effect=[True, False, True])
    monkeypatch.setattr("app.pipeline.dreaming.relations.apply_relation", apply_mock)

    session = MagicMock()
    stats = DreamingStats()
    classified_pairs: set[frozenset[str]] = set()
    touched_fact_ids: set[str] = set()

    await _process_relation_detection(
        session,
        new_fact=new,
        job_id="job-d1.1",
        stats=stats,
        classified_pairs=classified_pairs,
        touched_fact_ids=touched_fact_ids,
    )

    assert touched_fact_ids == {"new-1", "c1", "c2", "c3"}
    assert touched_fact_ids.isdisjoint(unrelated)
    assert apply_mock.await_count == 3
    # replaces + extends wrote edges; none did not
    assert stats.edges_created == 2
