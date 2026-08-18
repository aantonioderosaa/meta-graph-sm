"""Query response schemas (tech-spec §17.4)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


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


class DerivationStep(BaseModel):
    """One S0 fact or S1 rule in an S2 derivation chain (Fase 7 / 11)."""

    kind: Literal["s0", "s1"]
    detail: str


class QueryCitation(BaseModel):
    """Cited node or derived-link key with an explicit epistemic label."""

    id: str
    epistemic_status: Literal["asserted", "derived"] = "asserted"
    derivation_chain: list[DerivationStep] | None = None

    @model_validator(mode="after")
    def derived_requires_chain(self) -> QueryCitation:
        if self.epistemic_status == "derived" and not self.derivation_chain:
            raise ValueError(
                "derivation_chain is required when epistemic_status is 'derived'"
            )
        return self


class NodeQueryResponse(BaseModel):
    answer: str
    nodes_used: list[NodeUsed]
    concepts_used: list[ConceptUsed] = Field(default_factory=list)
    cited_node_ids: list[str] = Field(default_factory=list)
    subgraph: NodeSubgraph
    citations: list[QueryCitation] = Field(default_factory=list)
