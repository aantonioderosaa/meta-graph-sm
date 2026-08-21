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
    generic_instances: int = 0


class JudgeRunListResponse(BaseModel):
    items: list[JudgeRunItem]


class AgentSearchRunItem(BaseModel):
    id: str
    hypothesis_id: str = ""
    verdict: str | None = None
    turns_used: int = 0
    timestamp: str | None = None
    steps: str | None = None


class PendingHypothesisItem(BaseModel):
    id: str
    claim_target: str = ""
    confidence: str = "low"
    status: str = "open"
    marker_category: str | None = None
    kind: str | None = None
    origin_doc_id: str = ""
    listen_count: int = 0
    promoted: bool = False
    evidence_gap: str = ""


class ContextLayerRunItem(BaseModel):
    """Per-job gate/promotion/agent counters on ``:ContextLayerRun`` (F25.1)."""

    id: str
    job_id: str | None = None
    timestamp: str | None = None
    t1: int = 0
    t2: int = 0
    t3: int = 0
    model_fallback: int = 0
    promotions: int = 0
    agent_runs: int = 0
    agent_turns_used: int = 0


class ContextLayerRunsResponse(BaseModel):
    """Read-only inspection surface for the agentic context layer.

    Gate counters live on ``gate_runs`` (``:ContextLayerRun`` log nodes,
    same idea as ``:JudgeRun``), not a separate ``/stats`` endpoint.
    """

    agent_runs: list[AgentSearchRunItem]
    open_hypotheses: list[PendingHypothesisItem]
    gate_runs: list[ContextLayerRunItem]


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


class BundleRelation(BaseModel):
    """One stored ``:Relation`` between two macro endpoints (Fase 15)."""

    id: str
    from_id: str = Field(alias="from")
    to: str
    type: str
    relation: str | None = None
    kernel_parent: str | None = None
    witnesses_a: list[str] = Field(default_factory=list)
    witnesses_b: list[str] = Field(default_factory=list)
    provenance: Any = None
    valid_time: str | None = None
    system_time: str | None = None
    epistemic_status: str = "asserted"

    model_config = {"populate_by_name": True}


class BundleResponse(BaseModel):
    items: list[BundleRelation] = Field(default_factory=list)


class MetadataBreadcrumbItem(BaseModel):
    id: str
    name: str
    kernel_category: str | None = None


class NodeMetadataResponse(BaseModel):
    id: str
    kind: str
    name: str
    kernel_category: str | None = None
    definition: str | None = None
    aliases: list[str] = Field(default_factory=list)
    is_a_breadcrumb: list[MetadataBreadcrumbItem] = Field(default_factory=list)
    member_count: int | None = None
    summary: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    identity_uris: list[str] = Field(default_factory=list)
    node_type: str | None = None


class DomainListItem(BaseModel):
    """One :Concept for the Fase 17 scrolling dashboard (not NVL)."""

    id: str
    name: str
    kernel_category: str | None = None
    definition: str | None = None
    promoted: bool = False
    direct_member_count: int = 0


class DomainListResponse(BaseModel):
    items: list[DomainListItem] = Field(default_factory=list)


class DomainDictionaryItem(BaseModel):
    """One Σ_D entry: a relation or attribute type in use among direct members."""

    kind: str
    name: str
    kernel_parent: str | None = None
    count: int = 0


class DomainDictionaryResponse(BaseModel):
    items: list[DomainDictionaryItem] = Field(default_factory=list)


__all__ = [
    "ConnectivityRuleItem",
    "ConnectivityRuleListResponse",
    "ContradictionItem",
    "ContradictionListResponse",
    "DomainDictionaryItem",
    "DomainDictionaryResponse",
    "DomainListItem",
    "DomainListResponse",
    "DocumentListResponse",
    "DocumentRequest",
    "DocumentSummary",
    "DreamingRunRequest",
    "GraphNode",
    "GraphRelationship",
    "GraphResponse",
    "BundleRelation",
    "BundleResponse",
    "GraphResetResponse",
    "HealthResponse",
    "IdentityFacet",
    "MetadataBreadcrumbItem",
    "NodeMetadataResponse",
    "IdentityItem",
    "IdentityListResponse",
    "JobResponse",
    "AgentSearchRunItem",
    "ContextLayerRunItem",
    "ContextLayerRunsResponse",
    "JudgeRunItem",
    "JudgeRunListResponse",
    "PendingHypothesisItem",
    "NodeQueryRequest",
    "QueryHistoryEntry",
    "QueryHistoryResponse",
    "UnlinkFacetRequest",
    "UnlinkFacetResponse",
]
