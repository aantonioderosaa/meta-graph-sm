"""Pydantic model contract tests (tech-spec §17, E2.2)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.query import (
    ConceptUsed,
    NodeQueryResponse,
    NodeSubgraph,
    NodeSubgraphNode,
    NodeSubgraphRelationship,
    NodeUsed,
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


def test_node_query_response_cited_node_ids_default_empty():
    response = NodeQueryResponse(
        answer="Nessuna informazione trovata.",
        nodes_used=[],
        subgraph=NodeSubgraph(nodes=[], relationships=[]),
    )
    assert response.cited_node_ids == []
    assert response.concepts_used == []


def test_import_app_models():
    import app.models as models

    assert models.RelationLabel is RelationLabel
    assert models.NodeQueryResponse is NodeQueryResponse
