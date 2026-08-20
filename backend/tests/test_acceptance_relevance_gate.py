"""Fase 20.1: T1/T2/T3 relevance gate. Pure lexical tests, no Neo4j."""

from __future__ import annotations

from app.models.kernel import EntityKernelType
from app.models.node_extraction import ExtractedEntity
from app.pipeline.entity_relation_resolution import ERROR_MARKERS, SUCCESSION_MARKERS
from app.pipeline.relevance_gate import (
    QUANTIFIER_MARKERS,
    RETRACTION_MARKERS,
    S0Outcome,
    classify_fragment_relevance,
    has_t2_marker,
    match_t2_marker,
)


def _entity(name: str, summary: str = "A described entity.") -> ExtractedEntity:
    return ExtractedEntity(
        name=name,
        summary=summary,
        kernel_category=EntityKernelType.Agente,
    )


def test_t1_candidate_predicate_without_pair():
    signal = classify_fragment_relevance("I cani sono usciti.", [], S0Outcome())
    assert signal is not None
    assert signal.kind == "t1"
    assert signal.marker_category is None
    assert "predicate" in signal.evidence_gap


def test_t1_candidate_empty_summaries_block_the_pair():
    pair = [
        ("id-a", _entity("Alice", "")),
        ("id-b", _entity("Acme", "   ")),
    ]
    signal = classify_fragment_relevance("Alice è impiegata ad Acme.", pair, S0Outcome())
    assert signal is not None
    assert signal.kind == "t1"
    assert signal.pair_entity_ids == ("id-a", "id-b")


def test_t1_non_candidate_no_predicate():
    assert classify_fragment_relevance("Nota a margine.", []) is None


def test_t1_non_candidate_usable_pair_without_t2():
    pair = [("id-a", _entity("Alice")), ("id-b", _entity("Acme"))]
    signal = classify_fragment_relevance(
        "Alice works at Acme.",
        pair,
        S0Outcome(relation_written=True, has_comparables=False),
    )
    assert signal is None


def test_t2_quantifier_candidate():
    pair = [("id-a", _entity("Cucina")), ("id-b", _entity("Giardino"))]
    signal = classify_fragment_relevance("Tutti i cani sono usciti.", pair)
    assert signal is not None
    assert signal.kind == "t2"
    assert signal.marker_category == "quantifier"
    assert "scope" in signal.evidence_gap
    assert any("tutti i " in marker for marker in QUANTIFIER_MARKERS)


def test_t2_retraction_candidate():
    signal = classify_fragment_relevance(
        "Tutto quello che ti ho detto finora è falso.",
        [],
    )
    assert signal is not None
    assert signal.kind == "t2"
    assert signal.marker_category == "retraction"
    assert match_t2_marker("everything I told you is false")[0] == "retraction"
    assert any("non è vero niente" in marker for marker in RETRACTION_MARKERS)


def test_t2_error_and_succession_reuse_shared_markers():
    err = classify_fragment_relevance("In realtà mi sono sbagliato sul datore.", [])
    assert err is not None
    assert err.kind == "t2"
    assert err.marker_category == "error"
    suc = classify_fragment_relevance("Da allora ora è presidente.", [])
    assert suc is not None
    assert suc.kind == "t2"
    assert suc.marker_category == "succession"
    assert ERROR_MARKERS
    assert SUCCESSION_MARKERS
    assert "mi sono sbagliato" in ERROR_MARKERS
    assert "ora è" in SUCCESSION_MARKERS


def test_t2_non_candidate_without_markers():
    assert has_t2_marker("Alice works at Acme.") is False
    assert classify_fragment_relevance(
        "Alice works at Acme.",
        [("a", _entity("Alice")), ("b", _entity("Acme"))],
    ) is None


def test_t3_candidate_s0_without_comparables_and_t2_marker():
    pair = [("id-a", _entity("Alice")), ("id-b", _entity("Acme"))]
    signal = classify_fragment_relevance(
        "Tutti i cani sono usciti e Alice lavora ad Acme.",
        pair,
        S0Outcome(relation_written=True, has_comparables=False),
    )
    assert signal is not None
    assert signal.kind == "t3"
    assert signal.marker_category == "quantifier"
    assert "recall gap" in signal.evidence_gap


def test_t3_non_candidate_when_comparables_exist():
    pair = [("id-a", _entity("Alice")), ("id-b", _entity("Acme"))]
    signal = classify_fragment_relevance(
        "Tutti i cani sono usciti.",
        pair,
        S0Outcome(relation_written=True, has_comparables=True),
    )
    assert signal is not None
    assert signal.kind == "t2"


def test_t3_non_candidate_s0_without_t2_marker():
    pair = [("id-a", _entity("Alice")), ("id-b", _entity("Acme"))]
    assert (
        classify_fragment_relevance(
            "Alice works at Acme.",
            pair,
            S0Outcome(relation_written=True, has_comparables=False),
        )
        is None
    )


def test_t2_outranks_t1_when_both_match():
    signal = classify_fragment_relevance("Tutti i cani sono usciti.", [])
    assert signal is not None
    assert signal.kind == "t2"
