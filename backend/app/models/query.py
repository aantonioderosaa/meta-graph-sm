"""Query response schemas (tech-spec §17.4)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


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
    subgraph: Subgraph
