"""Pydantic model contract tests (tech-spec §17, E2.2)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.consolidation import ConsolidationOutcome, ConsolidationResult
from app.models.extraction import ExtractedFact, FactExtractionResult, FactType
from app.models.query import FactUsed, QueryResponse, Subgraph, SubgraphNode, SubgraphRelationship
from app.models.relations import RelationClassification, RelationLabel


def test_fact_type_valid_values():
    assert FactType("fact") == FactType.fact
    assert FactType("preference") == FactType.preference
    assert FactType("episode") == FactType.episode


def test_fact_type_invalid_raises():
    with pytest.raises(ValueError):
        FactType("unknown")


def test_extracted_fact_valid():
    fact = ExtractedFact(text="Alice works at Acme.", type=FactType.fact)
    assert fact.text == "Alice works at Acme."
    assert fact.type == FactType.fact


def test_extracted_fact_missing_type_raises():
    with pytest.raises(ValidationError):
        ExtractedFact(text="missing type")


def test_fact_extraction_result_empty_facts_allowed():
    result = FactExtractionResult(facts=[])
    assert result.facts == []


def test_fact_extraction_result_with_facts():
    result = FactExtractionResult(
        facts=[ExtractedFact(text="Prefers tea.", type=FactType.preference)]
    )
    assert len(result.facts) == 1


def test_consolidation_cleaned_fact_without_sources():
    result = ConsolidationResult(
        outcome=ConsolidationOutcome.cleaned_fact,
        text="Clean fact.",
        type=FactType.fact,
    )
    assert result.source_fact_ids == []


def test_consolidation_abstraction_with_sources():
    result = ConsolidationResult(
        outcome=ConsolidationOutcome.abstraction,
        text="Abstracted fact.",
        type=FactType.fact,
        source_fact_ids=["f1", "f2"],
    )
    assert result.source_fact_ids == ["f1", "f2"]


def test_consolidation_abstraction_without_sources_raises():
    with pytest.raises(ValidationError, match="source_fact_id"):
        ConsolidationResult(
            outcome=ConsolidationOutcome.abstraction,
            text="Invalid abstraction.",
            type=FactType.fact,
            source_fact_ids=[],
        )


def test_relation_classification_valid():
    rc = RelationClassification(relation=RelationLabel.extends)
    assert rc.relation == RelationLabel.extends


def test_relation_classification_invalid():
    with pytest.raises(ValidationError):
        RelationClassification(relation="conflicts")


def test_query_response_valid():
    response = QueryResponse(
        answer="Alice works at Acme.",
        facts_used=[
            FactUsed(id="f1", text="Alice works at Acme.", source_doc_id="doc-1")
        ],
        subgraph=Subgraph(
            nodes=[
                SubgraphNode(id="f1", label="Fact", properties={"type": "fact"}),
            ],
            relationships=[
                SubgraphRelationship(source="f1", target="f2", type="extends"),
            ],
        ),
    )
    assert response.answer.startswith("Alice")


def test_subgraph_relationship_invalid_type():
    with pytest.raises(ValidationError):
        SubgraphRelationship(source="a", target="b", type="updates_invalid")


def test_import_app_models():
    import app.models as models

    assert models.FactType is FactType
    assert models.QueryResponse is QueryResponse
