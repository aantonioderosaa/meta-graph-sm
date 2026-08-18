"""Entity/event/concept extraction schemas (adattato da autoschemakg
atlas_rag/llm_generator/prompt/triple_extraction_prompt.py — MIT).

Fase 3: anti-blur structural constraint (doc1 §2.4, doc4 §6) — ``head``/``tail``
are single strings (never lists); a fact without both witnesses is not
representable. ``relation`` stays a free refinement hanging under one of R1–R6
via ``kernel_parent``.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator

from app.models.kernel import EntityKernelType, RelationKernelType


class EntityRelationTriple(BaseModel):
    """One asserted entity–entity fact: single ids, kernel parent, both witnesses."""

    head: str = Field(min_length=1)
    tail: str = Field(min_length=1)
    relation: str
    kernel_parent: RelationKernelType
    witness_source: str = Field(min_length=1)
    witness_target: str = Field(min_length=1)


class EntityRelationExtractionResult(BaseModel):
    triples: list[EntityRelationTriple]


class ExtractedEntity(BaseModel):
    """Pass A: one entity with summary, constrained to E1–E8."""

    name: str = Field(min_length=1)
    summary: str = ""
    kernel_category: EntityKernelType


class EntityExtractionResult(BaseModel):
    entities: list[ExtractedEntity]


class PairRelationDecision(BaseModel):
    """Pass B: decide whether an unordered pair is related.

    When ``related`` is false, payload fields may be empty. When true, the
    refinement, kernel parent, and both witnesses are required.
    """

    related: bool
    relation: str = ""
    kernel_parent: RelationKernelType | None = None
    witness_source: str = ""
    witness_target: str = ""

    @model_validator(mode="after")
    def require_payload_when_related(self) -> PairRelationDecision:
        if not self.related:
            return self
        if not self.relation.strip():
            raise ValueError("relation is required when related is true")
        if self.kernel_parent is None:
            raise ValueError("kernel_parent is required when related is true")
        if not self.witness_source.strip() or not self.witness_target.strip():
            raise ValueError(
                "witness_source and witness_target are required when related is true"
            )
        return self


class MacroDomainSummary(BaseModel):
    """F3.0: running natural-language corpus summary (not a subdomain list)."""

    summary_text: str


class EventEntityParticipation(BaseModel):
    event: str
    entities: list[str]


class EventEntityExtractionResult(BaseModel):
    participations: list[EventEntityParticipation]


class EventRelationTriple(BaseModel):
    """One asserted event–event fact: single ids, kernel parent, both witnesses."""

    head: str = Field(min_length=1)
    tail: str = Field(min_length=1)
    relation: str
    kernel_parent: RelationKernelType
    witness_source: str = Field(min_length=1)
    witness_target: str = Field(min_length=1)


class EventRelationExtractionResult(BaseModel):
    triples: list[EventRelationTriple]


class ConceptResult(BaseModel):
    concepts: list[str]


class NodeDedupResult(BaseModel):
    duplicate_of: str | None  # candidate id, or null if this is a new node


class EventRelationLabel(str, Enum):
    same_event = "same_event"
    sequenced = "sequenced"
    none = "none"


class SequenceType(str, Enum):
    precedes = "precedes"
    causes = "causes"
    cooccurs = "cooccurs"


class EventRelationClassification(BaseModel):
    label: EventRelationLabel
    sequence_type: SequenceType | None = None  # required only if label=sequenced
