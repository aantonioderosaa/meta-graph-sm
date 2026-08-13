"""Entity/event/concept extraction LLM calls (Macrotask 2)."""

from __future__ import annotations

from app.core.llm_client import call_structured
from app.models.node_extraction import (
    ConceptResult,
    EntityRelationExtractionResult,
    EventEntityExtractionResult,
    EventRelationExtractionResult,
)
from app.pipeline.node_extraction_prompts import (
    build_entity_concept_prompt,
    build_entity_relation_prompt,
    build_event_concept_prompt,
    build_event_entity_prompt,
    build_event_relation_prompt,
)


async def extract_entity_relations(
    chunk_text: str, job_id: str | None = None
) -> EntityRelationExtractionResult:
    system_prompt, user_prompt = build_entity_relation_prompt(chunk_text)
    return await call_structured(
        system_prompt,
        user_prompt,
        EntityRelationExtractionResult,
        temperature=0,
        job_id=job_id,
    )


async def extract_event_entities(
    chunk_text: str, job_id: str | None = None
) -> EventEntityExtractionResult:
    system_prompt, user_prompt = build_event_entity_prompt(chunk_text)
    return await call_structured(
        system_prompt,
        user_prompt,
        EventEntityExtractionResult,
        temperature=0,
        job_id=job_id,
    )


async def extract_event_relations(
    chunk_text: str, job_id: str | None = None
) -> EventRelationExtractionResult:
    system_prompt, user_prompt = build_event_relation_prompt(chunk_text)
    return await call_structured(
        system_prompt,
        user_prompt,
        EventRelationExtractionResult,
        temperature=0,
        job_id=job_id,
    )


async def extract_event_concepts(event_text: str, job_id: str | None = None) -> ConceptResult:
    system_prompt, user_prompt = build_event_concept_prompt(event_text)
    return await call_structured(
        system_prompt,
        user_prompt,
        ConceptResult,
        temperature=0,
        job_id=job_id,
    )


async def extract_entity_concepts(
    entity_name: str, context: str, job_id: str | None = None
) -> ConceptResult:
    system_prompt, user_prompt = build_entity_concept_prompt(entity_name, context)
    return await call_structured(
        system_prompt,
        user_prompt,
        ConceptResult,
        temperature=0,
        job_id=job_id,
    )
