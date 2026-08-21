"""Node-extraction prompt builder tests (Macrotask 2; Fase 3 two-pass)."""

from __future__ import annotations

import hashlib

from app.models.kernel import EntityKernelType, RelationKernelType
from app.pipeline.concepts import compute_hash_id
from app.pipeline.domain_book import CATEGORY_CARDS, GENRE_NOT_TOPIC_PROMPT
from app.pipeline.node_extraction_prompts import (
    build_corpus_summary_prompt,
    build_entity_concept_prompt,
    build_entity_list_prompt,
    build_event_concept_prompt,
    build_event_entity_prompt,
    build_event_relation_prompt,
    build_pair_relation_prompt,
)

SAMPLE = "Alice works at Acme."
SUMMARY_A = "Alice is a person."
SUMMARY_B = "Acme is a company."
CORPUS = "A small corpus about employment."


def test_entity_list_prompt_extracts_background_fact_witnesses():
    """F23.6: background elements that witness a fact must be extracted too."""
    _system, user = build_entity_list_prompt(SAMPLE, CORPUS)
    assert "elementi di sfondo" in user
    assert "la strada" in user
    assert "estrai SOLO le entità importanti" not in user


def test_entity_list_prompt_substitutes_chunk_and_kernel_categories():
    _system, user = build_entity_list_prompt(SAMPLE, CORPUS)
    assert SAMPLE in user
    assert "{chunk_text}" not in user
    assert "entities" in user
    assert '"name"' in user
    assert "summary" in user
    assert CORPUS in user
    assert GENRE_NOT_TOPIC_PROMPT in user
    for category in EntityKernelType:
        assert category.value in user


def test_entity_list_prompt_includes_category_admission_criteria():
    """Bare E1-E8 labels alone are not enough to disambiguate edge cases (e.g. a
    traveller character being classified as OggettoFisico instead of Agente) —
    the same criterio_appartenenza already used by backbone classification must
    reach the extraction prompt too."""
    _system, user = build_entity_list_prompt(SAMPLE, CORPUS)
    for category in EntityKernelType:
        assert CATEGORY_CARDS[category].criterio_appartenenza in user


def test_pair_relation_prompt_includes_summaries_and_primitives():
    _system, user = build_pair_relation_prompt(
        SAMPLE, "Alice", SUMMARY_A, "Acme", SUMMARY_B, corpus_summary=CORPUS
    )
    assert SAMPLE in user
    assert SUMMARY_A in user
    assert SUMMARY_B in user
    assert "{chunk_text}" not in user
    assert "{summary_a}" not in user
    assert "related" in user
    assert "kernel_parent" in user
    assert "witness_source" in user
    assert GENRE_NOT_TOPIC_PROMPT in user
    for primitive in RelationKernelType:
        assert primitive.value in user


def test_event_entity_prompt_substitutes_chunk_and_uses_participations():
    _system, user = build_event_entity_prompt(SAMPLE)
    assert SAMPLE in user
    assert "{chunk_text}" not in user
    assert "participations" in user
    assert "entities" in user


def test_event_relation_prompt_substitutes_chunk_and_requires_witnesses():
    _system, user = build_event_relation_prompt(SAMPLE)
    assert SAMPLE in user
    assert "{chunk_text}" not in user
    assert "triples" in user
    assert '"head"' in user
    assert "witness_source" in user
    assert "kernel_parent" in user


def test_corpus_summary_prompt_is_running_text_not_subdomain_list():
    _system, user = build_corpus_summary_prompt("Existing overview.", SAMPLE)
    assert SAMPLE in user
    assert "Existing overview." in user
    assert "summary_text" in user
    assert "NON elencare i sottodomini" in user


def test_event_concept_prompt_keeps_few_shots_and_substitutes_event():
    _system, user = build_event_concept_prompt(SAMPLE)
    assert SAMPLE in user
    assert "{event_text}" not in user
    assert "A man retreats to mountains and forests" in user
    assert "hidding" in user
    assert "concepts" in user


def test_entity_concept_prompt_substitutes_name_and_context():
    _system, user = build_entity_concept_prompt(SAMPLE, SAMPLE)
    assert SAMPLE in user
    assert "{entity_name}" not in user
    assert "{context}" not in user
    assert "concepts" in user
    assert "Soul" in user
    assert "Thinkpad X60" in user
    assert "Harry Callahan" in user
    assert "Black Mountain College" in user
    assert "1st April" in user


def test_compute_hash_id_is_sha256_of_text_plus_concept_suffix():
    assert compute_hash_id("technology") == hashlib.sha256(b"technology_concept").hexdigest()
