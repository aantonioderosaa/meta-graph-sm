"""Entity/event/concept extraction schemas (adattato da autoschemakg
atlas_rag/llm_generator/prompt/triple_extraction_prompt.py — MIT)."""

from __future__ import annotations

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
