"""REST API request/response models for stub endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.models.extraction import FactType
from app.models.query import QueryResponse


class JobResponse(BaseModel):
    job_id: str


class DocumentRequest(BaseModel):
    doc_id: str
    text: str


class DocumentSummary(BaseModel):
    doc_id: str
    chunk_count: int
    fact_count: int
    first_ingested_at: str
    last_ingested_at: str


class DocumentListResponse(BaseModel):
    documents: list[DocumentSummary]


class DreamingRunRequest(BaseModel):
    job_id: str | None = None
    doc_id: str | None = None


class GraphNode(BaseModel):
    id: str
    caption: str
    size: float = 1.0
    color: str = "#4C8BF5"
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphRelationship(BaseModel):
    id: str
    from_id: str = Field(alias="from")
    to: str
    type: str
    caption: str | None = None

    model_config = {"populate_by_name": True}


class GraphResponse(BaseModel):
    nodes: list[GraphNode]
    relationships: list[GraphRelationship]


class GraphResetResponse(BaseModel):
    deleted: bool


class ChunkProvenance(BaseModel):
    chunk_id: str
    snippet: str
    doc_id: str


class FactDetailResponse(BaseModel):
    id: str
    text: str
    type: FactType
    confidence: float
    is_latest: bool
    created_at: str
    source_doc_id: str
    provenance: list[ChunkProvenance]


class FactHistoryEntry(BaseModel):
    id: str
    text: str
    type: FactType
    is_latest: bool
    path_length: int


class FactHistoryResponse(BaseModel):
    facts: list[FactHistoryEntry]


class QueryRequest(BaseModel):
    text: str
    type_filter: FactType | None = None


class NodeQueryRequest(BaseModel):
    text: str


class QueryHistoryEntry(BaseModel):
    id: str
    text: str
    created_at: str


class QueryHistoryResponse(BaseModel):
    items: list[QueryHistoryEntry]


class ReconcileResponse(BaseModel):
    drift_count: int


class HealthResponse(BaseModel):
    neo4j: str
    gds: str


# Re-export QueryResponse for OpenAPI consistency
__all__ = [
    "ChunkProvenance",
    "DocumentListResponse",
    "DocumentRequest",
    "DocumentSummary",
    "DreamingRunRequest",
    "FactDetailResponse",
    "FactHistoryEntry",
    "FactHistoryResponse",
    "GraphNode",
    "GraphRelationship",
    "GraphResponse",
    "GraphResetResponse",
    "HealthResponse",
    "JobResponse",
    "NodeQueryRequest",
    "QueryHistoryEntry",
    "QueryHistoryResponse",
    "QueryRequest",
    "QueryResponse",
    "ReconcileResponse",
]
