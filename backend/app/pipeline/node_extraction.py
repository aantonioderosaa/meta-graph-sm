"""Entity/event/concept extraction LLM calls (Macrotask 2; Fase 3 two-pass)."""

from __future__ import annotations

from app.core.llm_client import call_structured
from app.models.node_extraction import (
    ConceptResult,
    EntityExtractionResult,
    EventEntityExtractionResult,
    EventRelationExtractionResult,
    PairIndexedDecision,
    PairRelationBatchResult,
    PairRelationDecision,
)
from app.pipeline.node_extraction_prompts import (
    build_entity_concept_prompt,
    build_entity_list_prompt,
    build_event_concept_prompt,
    build_event_entity_prompt,
    build_event_relation_prompt,
    build_pair_relation_batch_prompt,
    build_pair_relation_prompt,
)


async def extract_entities(
    chunk_text: str,
    job_id: str | None = None,
    corpus_summary: str = "",
) -> EntityExtractionResult:
    """Pass A: entities with summary, constrained to E1–E8."""
    system_prompt, user_prompt = build_entity_list_prompt(chunk_text, corpus_summary)
    return await call_structured(
        system_prompt,
        user_prompt,
        EntityExtractionResult,
        temperature=0,
        job_id=job_id,
    )


async def extract_pair_relation(
    chunk_text: str,
    name_a: str,
    summary_a: str,
    name_b: str,
    summary_b: str,
    job_id: str | None = None,
    corpus_summary: str = "",
) -> PairRelationDecision:
    """Pass B: one decision for an unordered pair (summary gate)."""
    system_prompt, user_prompt = build_pair_relation_prompt(
        chunk_text,
        name_a,
        summary_a,
        name_b,
        summary_b,
        corpus_summary=corpus_summary,
    )
    return await call_structured(
        system_prompt,
        user_prompt,
        PairRelationDecision,
        temperature=0,
        job_id=job_id,
    )


def align_pair_batch_decisions(
    n_pairs: int, batch: PairRelationBatchResult
) -> list[PairRelationDecision]:
    """Map batch decisions onto the requested pairs by ``pair_index``.

    Missing or out-of-range indices become ``related=false``. Duplicate
    indices keep the first occurrence. Matching is never by entity name.
    """
    by_index: dict[int, PairIndexedDecision] = {}
    for decision in batch.decisions:
        if 0 <= decision.pair_index < n_pairs and decision.pair_index not in by_index:
            by_index[decision.pair_index] = decision
    aligned: list[PairRelationDecision] = []
    for index in range(n_pairs):
        item = by_index.get(index)
        if item is None:
            aligned.append(PairRelationDecision(related=False))
            continue
        aligned.append(
            PairRelationDecision(
                related=item.related,
                relation=item.relation,
                kernel_parent=item.kernel_parent,
                witness_source=item.witness_source,
                witness_target=item.witness_target,
            )
        )
    return aligned


async def extract_pair_relations_batch(
    chunk_text: str,
    pairs: list[tuple[str, str, str, str]],
    job_id: str | None = None,
    corpus_summary: str = "",
) -> PairRelationBatchResult:
    """Pass B: one structured call for a batch of unordered pairs."""
    if not pairs:
        return PairRelationBatchResult(decisions=[])
    system_prompt, user_prompt = build_pair_relation_batch_prompt(
        chunk_text,
        pairs,
        corpus_summary=corpus_summary,
    )
    return await call_structured(
        system_prompt,
        user_prompt,
        PairRelationBatchResult,
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
