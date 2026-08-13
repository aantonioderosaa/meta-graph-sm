"""Node-extraction prompt builder tests (Macrotask 2)."""

from __future__ import annotations

import hashlib

from app.pipeline.concepts import compute_hash_id
from app.pipeline.node_extraction_prompts import (
    build_entity_concept_prompt,
    build_entity_relation_prompt,
    build_event_concept_prompt,
    build_event_entity_prompt,
    build_event_relation_prompt,
)

SAMPLE = "Alice works at Acme."


def test_entity_relation_prompt_substitutes_chunk_and_uses_snake_case_triples():
    _system, user = build_entity_relation_prompt(SAMPLE)
    assert SAMPLE in user
    assert "{chunk_text}" not in user
    assert "triples" in user
    assert '"head"' in user


def test_event_entity_prompt_substitutes_chunk_and_uses_participations():
    _system, user = build_event_entity_prompt(SAMPLE)
    assert SAMPLE in user
    assert "{chunk_text}" not in user
    assert "participations" in user
    assert "entities" in user


def test_event_relation_prompt_substitutes_chunk():
    _system, user = build_event_relation_prompt(SAMPLE)
    assert SAMPLE in user
    assert "{chunk_text}" not in user
    assert "triples" in user
    assert '"head"' in user


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
