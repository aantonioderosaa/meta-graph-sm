"""Query response schemas (tech-spec §17.4)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class FactUsed(BaseModel):
    id: str
    text: str
    source_doc_id: str


class SubgraphNode(BaseModel):
    id: str
    label: Literal["Fact"]
    properties: dict[str, Any]


class SubgraphRelationship(BaseModel):
    source: str
    target: str
    type: Literal["updates", "extends", "derives"]


class Subgraph(BaseModel):
    nodes: list[SubgraphNode]
    relationships: list[SubgraphRelationship]


class QueryResponse(BaseModel):
    answer: str
    facts_used: list[FactUsed]
    cited_fact_ids: list[str] = Field(default_factory=list)
    subgraph: Subgraph


class NodeUsed(BaseModel):
    id: str
    name: str
    type: Literal["entity", "event"]
    source_doc_ids: list[str] = Field(default_factory=list)


class ConceptUsed(BaseModel):
    id: str
    name: str


class NodeSubgraphNode(BaseModel):
    id: str
    label: Literal["Node", "Concept"]
    properties: dict[str, Any]


class NodeSubgraphRelationship(BaseModel):
    source: str
    target: str
    type: str


class NodeSubgraph(BaseModel):
    nodes: list[NodeSubgraphNode]
    relationships: list[NodeSubgraphRelationship]


class NodeQueryResponse(BaseModel):
    answer: str
    nodes_used: list[NodeUsed]
    concepts_used: list[ConceptUsed] = Field(default_factory=list)
    cited_node_ids: list[str] = Field(default_factory=list)
    subgraph: NodeSubgraph
