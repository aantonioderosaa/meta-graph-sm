"""Pydantic model contract tests (tech-spec §17, E2.2)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.query import (
    ConceptUsed,
    DerivationStep,
    NodeQueryResponse,
    NodeSubgraph,
    NodeSubgraphNode,
    NodeSubgraphRelationship,
    NodeUsed,
    QueryCitation,
)
from app.models.relations import RelationClassification, RelationLabel


def test_relation_classification_valid():
    rc = RelationClassification(relation=RelationLabel.extends)
    assert rc.relation == RelationLabel.extends


def test_relation_label_temporal_members():
    assert RelationLabel.supersedes.value == "supersedes"
    assert RelationLabel.updated_by.value == "updated_by"
    assert RelationLabel.contradicts.value == "contradicts"
    assert RelationLabel.replaces.value == "replaces"
    assert RelationClassification(relation=RelationLabel.supersedes).relation == (
        RelationLabel.supersedes
    )


def test_relation_classification_invalid():
    with pytest.raises(ValidationError):
        RelationClassification(relation="conflicts")


def test_node_query_response_valid():
    response = NodeQueryResponse(
        answer="Alice è un'entità.",
        nodes_used=[NodeUsed(id="n1", name="Alice", type="entity", source_doc_ids=["doc-1"])],
        concepts_used=[ConceptUsed(id="c1", name="leadership")],
        cited_node_ids=["n1"],
        subgraph=NodeSubgraph(
            nodes=[
                NodeSubgraphNode(id="n1", label="Node", properties={"type": "entity"}),
            ],
            relationships=[
                NodeSubgraphRelationship(source="n1", target="n2", type="Relation"),
            ],
        ),
    )
    assert response.answer.startswith("Alice")
    assert response.cited_node_ids == ["n1"]
    assert response.nodes_used[0].type == "entity"
    assert response.citations == []


def test_node_query_response_cited_node_ids_default_empty():
    response = NodeQueryResponse(
        answer="Nessuna informazione trovata.",
        nodes_used=[],
        subgraph=NodeSubgraph(nodes=[], relationships=[]),
    )
    assert response.cited_node_ids == []
    assert response.concepts_used == []
    assert response.citations == []


def test_node_query_response_citations_default_empty():
    response = NodeQueryResponse(
        answer="Alice è un'entità.",
        nodes_used=[NodeUsed(id="n1", name="Alice", type="entity")],
        subgraph=NodeSubgraph(nodes=[], relationships=[]),
    )
    assert response.citations == []


def test_query_citation_derived_requires_chain():
    with pytest.raises(ValidationError):
        QueryCitation(id="a|rel|b", epistemic_status="derived")
    with pytest.raises(ValidationError):
        QueryCitation(id="a|rel|b", epistemic_status="derived", derivation_chain=[])


def test_query_citation_asserted_omits_chain():
    cited = QueryCitation(id="n1", epistemic_status="asserted")
    assert cited.derivation_chain is None
    derived = QueryCitation(
        id="a|knows|b",
        epistemic_status="derived",
        derivation_chain=[DerivationStep(kind="s0", detail="a-[knows]->b")],
    )
    assert derived.derivation_chain and derived.derivation_chain[0].kind == "s0"


def test_import_app_models():
    import app.models as models

    assert models.RelationLabel is RelationLabel
    assert models.NodeQueryResponse is NodeQueryResponse
    assert models.QueryCitation is QueryCitation
    assert models.DerivationStep is DerivationStep
