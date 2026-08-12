"""R1.1 — NewFactForRelations propagates source_doc_id on all construction paths."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.models.consolidation import ConsolidationOutcome, ConsolidationResult
from app.models.extraction import FactType
from app.pipeline.dreaming import (
    NewFactForRelations,
    _add_facts_as_individual_candidates,
    _write_abstraction,
    _write_cleaned_fact,
)


@pytest.mark.asyncio
async def test_add_facts_as_individual_candidates_sets_source_doc_id(monkeypatch):
    session = AsyncMock()
    loaded = {
        "id": "f1",
        "text": "Alice works at Acme.",
        "type": "claim",
        "source_doc_id": "doc-a",
        "embedding": [0.1, 0.2],
    }

    async def fake_load(_session, fact_id: str):
        assert fact_id == "f1"
        return loaded

    monkeypatch.setattr("app.pipeline.dreaming._load_fact", fake_load)

    new_facts: list[NewFactForRelations] = []
    processed: set[str] = set()
    await _add_facts_as_individual_candidates(session, ["f1"], new_facts, processed)

    assert len(new_facts) == 1
    assert new_facts[0].source_doc_id == "doc-a"
    assert new_facts[0].fact_id == "f1"
    assert processed == {"f1"}


@pytest.mark.asyncio
async def test_write_abstraction_sets_source_doc_id():
    session = AsyncMock()
    result = ConsolidationResult(
        outcome=ConsolidationOutcome.abstraction,
        text="Alice is employed.",
        type=FactType.fact,
        source_fact_ids=["s1", "s2"],
    )

    with patch("app.pipeline.dreaming.embeddings.embed", return_value=[0.3, 0.4]):
        with patch("app.pipeline.dreaming.event_bus.publish", new_callable=AsyncMock):
            new_fact = await _write_abstraction(session, result, "doc-abs", "job-1")

    assert new_fact.source_doc_id == "doc-abs"
    assert new_fact.source_fact_ids == ["s1", "s2"]
    assert new_fact.text == "Alice is employed."
    session.run.assert_awaited()


@pytest.mark.asyncio
async def test_write_cleaned_fact_sets_source_doc_id():
    session = AsyncMock()
    result = ConsolidationResult(
        outcome=ConsolidationOutcome.cleaned_fact,
        text="Alice works at Acme Corp.",
        type=FactType.fact,
    )

    with patch("app.pipeline.dreaming.embeddings.embed", return_value=[0.5, 0.6]):
        new_fact = await _write_cleaned_fact(session, result, "doc-clean")

    assert new_fact.source_doc_id == "doc-clean"
    assert new_fact.source_fact_ids == []
    assert new_fact.text == "Alice works at Acme Corp."
    session.run.assert_awaited()
