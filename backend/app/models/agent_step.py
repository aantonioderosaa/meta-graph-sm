"""Structured ReAct step for the agentic context layer (Fase 22).

One ``call_structured`` turn produces one of the six Fase 19 retrieval
actions or ``conclude``. The agent never emits Cypher.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AgentAction = Literal[
    "search_fulltext",
    "search_vector",
    "get_metadata",
    "get_relations",
    "get_domain_dictionary",
    "facts_from_source",
    "conclude",
]


class AgentStep(BaseModel):
    """One ReAct turn: a retrieval tool or a conclude proposal."""

    action: AgentAction = Field(
        description=(
            "search_fulltext, search_vector, get_metadata, get_relations, "
            "get_domain_dictionary, facts_from_source, or conclude"
        )
    )
    reasoning: str = Field(min_length=1, description="Short audit trail for this turn")
    query: str = Field(default="", description="Fulltext or vector query text")
    node_id: str = Field(default="", description="Node id for metadata/relations")
    concept_id: str = Field(default="", description="Concept id for domain dictionary")
    doc_id: str = Field(default="", description="Source document id for facts_from_source")
    candidate_ids: list[str] = Field(
        default_factory=list,
        description="Proposed leaf node ids (head/tail) when concluding",
    )
    evidence_span: str = Field(default="", description="Text span supporting the conclude")
    witness_source: str = Field(
        default="",
        description="Leaf witness for the source/head; required to write a Relation",
    )
    witness_target: str = Field(
        default="",
        description="Leaf witness for the target/tail; required to write a Relation",
    )
    relation: str = Field(default="", description="Relation refinement when concluding")
    kernel_parent: str = Field(default="", description="R1–R6 parent when concluding")
