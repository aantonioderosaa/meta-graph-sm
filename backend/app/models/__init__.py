"""Pydantic data contracts (tech-spec §17)."""

from app.models.consolidation import ConsolidationOutcome, ConsolidationResult
from app.models.extraction import ExtractedFact, FactExtractionResult, FactType
from app.models.query import (
    FactUsed,
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
    "FactUsed",
    "QueryResponse",
    "RelationClassification",
    "RelationLabel",
    "Subgraph",
    "SubgraphNode",
    "SubgraphRelationship",
]
