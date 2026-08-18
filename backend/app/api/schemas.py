"""REST API request/response models for stub endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class JobResponse(BaseModel):
    job_id: str


class DocumentRequest(BaseModel):
    doc_id: str
    text: str


class DocumentSummary(BaseModel):
    doc_id: str
    chunk_count: int
    node_count: int
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


class IdentityFacet(BaseModel):
    id: str
    name: str
    kernel_category: str | None = None


class IdentityItem(BaseModel):
    uri: str
    facets: list[IdentityFacet] = Field(default_factory=list)


class IdentityListResponse(BaseModel):
    items: list[IdentityItem]


class UnlinkFacetRequest(BaseModel):
    facet_node_id: str


class UnlinkFacetResponse(BaseModel):
    unlinked: bool
    identity_uri: str
    facet_node_id: str


class ContradictionItem(BaseModel):
    id: str
    left_id: str
    left_name: str
    right_id: str
    right_name: str
    subject_id: str | None = None


class ContradictionListResponse(BaseModel):
    items: list[ContradictionItem]


class ConnectivityRuleItem(BaseModel):
    source_category: str
    relation_type: str
    target_category: str
    generalization_level: int = 0
    origin_count: int = 0


class ConnectivityRuleListResponse(BaseModel):
    items: list[ConnectivityRuleItem]


class JudgeRunItem(BaseModel):
    id: str
    batch_id: str | None = None
    timestamp: str | None = None
    anti_blur: int = 0
    equivalent_to: int = 0
    reraffine: int = 0
    identity: int = 0
    missed_contradictions: int = 0
    temporal: int = 0


class JudgeRunListResponse(BaseModel):
    items: list[JudgeRunItem]


class NodeQueryRequest(BaseModel):
    text: str


class QueryHistoryEntry(BaseModel):
    id: str
    text: str
    created_at: str


class QueryHistoryResponse(BaseModel):
    items: list[QueryHistoryEntry]


class HealthResponse(BaseModel):
    neo4j: str
    gds: str


__all__ = [
    "ConnectivityRuleItem",
    "ConnectivityRuleListResponse",
    "ContradictionItem",
    "ContradictionListResponse",
    "DocumentListResponse",
    "DocumentRequest",
    "DocumentSummary",
    "DreamingRunRequest",
    "GraphNode",
    "GraphRelationship",
    "GraphResponse",
    "GraphResetResponse",
    "HealthResponse",
    "IdentityFacet",
    "IdentityItem",
    "IdentityListResponse",
    "JobResponse",
    "JudgeRunItem",
    "JudgeRunListResponse",
    "NodeQueryRequest",
    "QueryHistoryEntry",
    "QueryHistoryResponse",
    "UnlinkFacetRequest",
    "UnlinkFacetResponse",
]
