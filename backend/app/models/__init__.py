"""Pydantic data contracts (tech-spec §17)."""

from app.models.consolidation import ConsolidationOutcome, ConsolidationResult
from app.models.extraction import ExtractedFact, FactExtractionResult, FactType
from app.models.query import (
    ConceptUsed,
    FactUsed,
    NodeQueryResponse,
    NodeSubgraph,
    NodeSubgraphNode,
    NodeSubgraphRelationship,
    NodeUsed,
    QueryResponse,
    Subgraph,
    SubgraphNode,
    SubgraphRelationship,
)
from app.models.relations import RelationClassification, RelationLabel

__all__ = [
    "ConsolidationOutcome",
    "ConsolidationResult",
    "ExtractedFact",
    "FactExtractionResult",
    "FactType",
    "ConceptUsed",
    "FactUsed",
    "NodeQueryResponse",
    "NodeSubgraph",
    "NodeSubgraphNode",
    "NodeSubgraphRelationship",
    "NodeUsed",
    "QueryResponse",
    "RelationClassification",
    "RelationLabel",
    "Subgraph",
    "SubgraphNode",
    "SubgraphRelationship",
]
