"""F24.2 / F24.5 / F24.6: eval harness, thresholds, model fallback. No OpenAI."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.structural_signal import StructuralSignalVerdict
from app.pipeline.context_layer_eval import (
    estimate_extra_llm_calls,
    estimate_extra_llm_calls_from_corpus,
    evaluate_relevance_gate,
    evaluate_thresholds,
    gate_only_classify,
    make_fallback_classify,
    stub_model_fn,
)
from app.pipeline.relevance_gate import (
    STRUCTURAL_SIGNAL_SYSTEM_PROMPT,
    S0Outcome,
    classify_fragment_relevance,
    classify_fragment_relevance_with_model_fallback,
)
from tests.fixtures.context_layer_eval_corpus import CONTEXT_LAYER_EVAL_CORPUS


def _paraphrase_item():
    return next(item for item in CONTEXT_LAYER_EVAL_CORPUS if item.id == "c-err-non-piu-da")


@pytest.mark.asyncio
async def test_harness_gate_only_reports_measured_precision_recall():
    report = await evaluate_relevance_gate(
        CONTEXT_LAYER_EVAL_CORPUS,
        gate_only_classify,
        mode="gate-only",
    )
    assert report.n == len(CONTEXT_LAYER_EVAL_CORPUS)
    assert 0.0 <= report.precision <= 1.0
    assert 0.0 <= report.recall <= 1.0
    assert report.false_positives == 0
    class_c = [item for item in CONTEXT_LAYER_EVAL_CORPUS if item.class_ == "c"]
    assert report.false_negatives >= len(class_c)
    assert "precision" in report.format_markdown()


@pytest.mark.asyncio
async def test_harness_with_stub_fallback_recovers_paraphrases():
    report = await evaluate_relevance_gate(
        CONTEXT_LAYER_EVAL_CORPUS,
        make_fallback_classify(stub_model_fn),
        mode="with-model-fallback",
    )
    assert report.false_positives == 0
    assert report.false_negatives == 0
    assert report.precision == 1.0
    assert report.recall == 1.0
    paraphrase_ids = {item.id for item in CONTEXT_LAYER_EVAL_CORPUS if item.class_ == "c"}
    wrong_ids = {case.id for case in report.wrong}
    assert paraphrase_ids.isdisjoint(wrong_ids)


def test_estimate_extra_llm_calls_formula_and_corpus_mix():
    typical = estimate_extra_llm_calls(
        weak_gate_chunks_with_relation=1,
        promoted_hypotheses=0,
    )
    assert typical.fallback_calls == 1
    assert typical.agent_calls_max == 0
    assert typical.total_max == 1
    worst = estimate_extra_llm_calls(
        weak_gate_chunks_with_relation=1,
        promoted_hypotheses=1,
        max_turns=4,
    )
    assert worst.total_max == 5
    zero = estimate_extra_llm_calls(
        weak_gate_chunks_with_relation=0,
        promoted_hypotheses=0,
        t2_or_t3_chunks=1,
    )
    assert zero.total_max == 0
    from_corpus = estimate_extra_llm_calls_from_corpus(CONTEXT_LAYER_EVAL_CORPUS)
    assert from_corpus.fallback_calls + from_corpus.t2_or_t3_chunks == sum(
        1 for item in CONTEXT_LAYER_EVAL_CORPUS if item.relation_written
    )
    assert "fallback" in from_corpus.formula
    assert "CONTEXT_AGENT_MAX_TURNS" in from_corpus.format_markdown()


def test_thresholds_confirmed_on_frozen_corpus():
    report = evaluate_thresholds(CONTEXT_LAYER_EVAL_CORPUS)
    assert report.silence_never_promotes is True
    assert report.late_reinforcement_promotes is True
    assert report.listen_window_decision == "keep"
    assert report.listen_window_default == 5
    assert report.max_turns_decision == "keep"
    assert report.max_turns_default == 4
    assert report.max_planned_turns <= 4
    assert "keep" in report.format_markdown()


@pytest.mark.asyncio
async def test_model_fallback_catches_paraphrased_correction_t2_misses():
    item = _paraphrase_item()
    pair = [
        ("a", type("E", (), {"name": "Alice", "summary": "persona"})()),
        ("b", type("E", (), {"name": "Acme", "summary": "azienda"})()),
    ]
    s0 = S0Outcome(relation_written=True, has_comparables=True)
    assert classify_fragment_relevance(item.text, pair, s0) is None

    async def stub(_system: str, _user: str) -> StructuralSignalVerdict:
        return StructuralSignalVerdict(
            has_signal=True,
            marker_category="error",
            claim_target_hint="Alice",
            reasoning="paraphrased correction",
        )

    signal = await classify_fragment_relevance_with_model_fallback(
        item.text,
        pair,
        s0,
        relation_text=item.text,
        job_id="f24-fallback",
        model_fn=stub,
    )
    assert signal is not None
    assert signal.kind == "t2"
    assert signal.marker_category == "error"


@pytest.mark.asyncio
async def test_model_fallback_skips_llm_on_t2():
    called = {"n": 0}

    async def boom(_system: str, _user: str) -> StructuralSignalVerdict:
        called["n"] += 1
        raise AssertionError("model must not run when T2 already matched")

    signal = await classify_fragment_relevance_with_model_fallback(
        "Tutti i cani sono usciti.",
        [],
        S0Outcome(relation_written=True, has_comparables=True),
        relation_text="Tutti i cani sono usciti.",
        job_id="f24-t2",
        model_fn=boom,
    )
    assert signal is not None
    assert signal.kind == "t2"
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_model_fallback_conservative_on_ordinary_fact():
    text = "Alice lavora ad Acme come analista."
    pair = [
        ("a", type("E", (), {"name": "Alice", "summary": "persona"})()),
        ("b", type("E", (), {"name": "Acme", "summary": "azienda"})()),
    ]
    s0 = S0Outcome(relation_written=True, has_comparables=True)

    async def stub(_system: str, _user: str) -> StructuralSignalVerdict:
        return StructuralSignalVerdict(
            has_signal=False,
            reasoning="fatto semplicemente nuovo",
        )

    assert classify_fragment_relevance(text, pair, s0) is None
    signal = await classify_fragment_relevance_with_model_fallback(
        text, pair, s0, relation_text=text, job_id="f24-tn", model_fn=stub
    )
    assert signal is None


def test_prudence_prompt_does_not_force_new_facts():
    assert "non forzare un segnale se il fatto è semplicemente nuovo" in (
        STRUCTURAL_SIGNAL_SYSTEM_PROMPT.casefold()
        .replace("è", "e")
        .replace("é", "e")
        or STRUCTURAL_SIGNAL_SYSTEM_PROMPT
    ) or "semplicemente nuovo" in STRUCTURAL_SIGNAL_SYSTEM_PROMPT
    source = Path(classify_fragment_relevance_with_model_fallback.__code__.co_filename)
    text = source.read_text(encoding="utf-8")
    assert "Non inventare testimoni" in text
    assert "semplicemente nuovo" in text
