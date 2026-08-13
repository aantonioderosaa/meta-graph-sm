"""Entity/event/concept extraction schemas (adattato da autoschemakg
atlas_rag/llm_generator/prompt/triple_extraction_prompt.py — MIT)."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class EntityRelationTriple(BaseModel):
    head: str
    relation: str
    tail: str


class EntityRelationExtractionResult(BaseModel):
    triples: list[EntityRelationTriple]


class EventEntityParticipation(BaseModel):
    event: str
    entities: list[str]


class EventEntityExtractionResult(BaseModel):
    participations: list[EventEntityParticipation]


class EventRelationTriple(BaseModel):
    head: str
    relation: str
    tail: str


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
