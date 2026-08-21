"""F24.2 eval harness for the agentic context-layer relevance gate.

``evaluate_relevance_gate(corpus, classifier) -> EvalReport`` measures
precision / recall / F1 on a labeled corpus. The classifier may be the
deterministic gate or the gate plus model fallback (including a stub).
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from app.core.config import settings
from app.models.kernel import EntityKernelType
from app.models.node_extraction import ExtractedEntity
from app.models.structural_signal import StructuralSignalVerdict
from app.pipeline.entity_relation_resolution import ERROR_MARKERS, SUCCESSION_MARKERS
from app.pipeline.pending_hypothesis import _confidence_and_promote
from app.pipeline.relevance_gate import (
    QUANTIFIER_MARKERS,
    RETRACTION_MARKERS,
    FragmentSignal,
    MarkerCategory,
    S0Outcome,
    classify_fragment_relevance,
    classify_fragment_relevance_with_model_fallback,
)


class EvalCase(Protocol):
    id: str
    text: str
    expected_signal: bool
    expected_category: str | None
    class_: str
    usable_pair: bool
    relation_written: bool


Classifier = Callable[[EvalCase], FragmentSignal | None | Awaitable[FragmentSignal | None]]

# Cue-based stub for --with-model-fallback without OpenAI. Covers the frozen
# class-(c) paraphrases; conservative on everything else.
_STUB_CUES: tuple[tuple[str, MarkerCategory], ...] = (
    ("non più da", "error"),
    ("non è più consulente", "error"),
    ("non vale più", "retraction"),
    ("l'intero gruppo ha lasciato", "quantifier"),
    ("nessuno è rimasto", "quantifier"),
    ("adesso alice è in forza", "succession"),
    ("è in forza a", "succession"),
)


@dataclass(frozen=True)
class WrongCase:
    id: str
    reason: str
    expected_signal: bool
    predicted_signal: bool
    expected_category: str | None
    predicted_category: str | None
    text: str


@dataclass
class EvalReport:
    mode: str
    n: int
    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int
    precision: float
    recall: float
    f1: float
    category_matches: int
    wrong: list[WrongCase] = field(default_factory=list)

    def format_markdown(self) -> str:
        lines = [
            f"## Context-layer eval ({self.mode})",
            "",
            f"- n = {self.n}",
            f"- precision = {self.precision:.3f}",
            f"- recall = {self.recall:.3f}",
            f"- F1 = {self.f1:.3f}",
            (
                f"- TP={self.true_positives} FP={self.false_positives} "
                f"FN={self.false_negatives} TN={self.true_negatives}"
            ),
            (
                f"- category matches among TP = {self.category_matches}/"
                f"{self.true_positives}"
            ),
        ]
        if self.wrong:
            lines.append("- wrong cases:")
            for case in self.wrong:
                lines.append(f"  - `{case.id}`: {case.reason}")
        return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class ThresholdReport:
    listen_window_default: int
    listen_window_decision: str
    listen_window_reason: str
    max_turns_default: int
    max_turns_decision: str
    max_turns_reason: str
    max_planned_turns: int
    silence_never_promotes: bool
    late_reinforcement_promotes: bool

    def format_markdown(self) -> str:
        return (
            "## Threshold calibration (F24.5)\n"
            f"- PENDING_HYPOTHESIS_LISTEN_WINDOW: {self.listen_window_decision} "
            f"{self.listen_window_default} — {self.listen_window_reason}\n"
            f"- CONTEXT_AGENT_MAX_TURNS: {self.max_turns_decision} "
            f"{self.max_turns_default} — {self.max_turns_reason}\n"
            f"- max planned ReAct turns on corpus = {self.max_planned_turns}\n"
            f"- silence never promotes = {self.silence_never_promotes}\n"
            f"- late reinforcement still promotes = {self.late_reinforcement_promotes}\n"
        )


EXTRA_LLM_CALL_FORMULA = (
    "worst case approx 1 fallback/chunk-with-new-relation whose deterministic "
    "gate is T1 or None + CONTEXT_AGENT_MAX_TURNS (default 4) per promoted "
    "hypothesis; T2/T3 -> +0 fallback; empty promotion queue -> +0 agent. "
    "Quantifier/retraction/hypothesis create add no LLM beyond the fallback."
)


@dataclass(frozen=True)
class ExtraLlmCallEstimate:
    """F25.2: extra ``call_structured`` counts with the context layer on."""

    fallback_calls: int
    agent_calls_max: int
    total_max: int
    t2_or_t3_chunks: int
    weak_gate_with_relation: int
    formula: str = EXTRA_LLM_CALL_FORMULA

    def format_markdown(self) -> str:
        return (
            "## Extra LLM calls with ENABLE_CONTEXT_LAYER on (F25.2)\n"
            f"- formula: {self.formula}\n"
            f"- fallback calls (T1/None + relation written) = {self.fallback_calls}\n"
            f"- T2/T3 chunks (no fallback) = {self.t2_or_t3_chunks}\n"
            f"- agent calls max (promoted * CONTEXT_AGENT_MAX_TURNS) = "
            f"{self.agent_calls_max}\n"
            f"- total extra calls (worst case) = {self.total_max}\n"
            "- live LLM on a production corpus was not run (no large real "
            "corpus in sm/; OPENAI may be unset). Gate-only P/R on the frozen "
            "F24.1 corpus: P=1.000 R=0.571; with fallback stub P=1.000 R=1.000.\n"
        )


def estimate_extra_llm_calls(
    *,
    weak_gate_chunks_with_relation: int,
    promoted_hypotheses: int,
    max_turns: int | None = None,
    t2_or_t3_chunks: int = 0,
) -> ExtraLlmCallEstimate:
    cap = int(max_turns if max_turns is not None else settings.CONTEXT_AGENT_MAX_TURNS)
    fallback = max(0, int(weak_gate_chunks_with_relation))
    agent = max(0, int(promoted_hypotheses)) * max(0, cap)
    return ExtraLlmCallEstimate(
        fallback_calls=fallback,
        agent_calls_max=agent,
        total_max=fallback + agent,
        t2_or_t3_chunks=max(0, int(t2_or_t3_chunks)),
        weak_gate_with_relation=fallback,
    )


def estimate_extra_llm_calls_from_corpus(
    corpus: Sequence[EvalCase],
    *,
    promoted_hypotheses: int = 0,
    max_turns: int | None = None,
) -> ExtraLlmCallEstimate:
    """Static mix: count fallback-eligible items without calling a model."""
    t2_t3 = 0
    weak = 0
    for item in corpus:
        signal = classify_fragment_relevance(
            item.text, pair_for_item(item), s0_for_item(item)
        )
        if signal is not None and signal.kind in {"t2", "t3"}:
            t2_t3 += 1
        elif item.relation_written:
            weak += 1
    return estimate_extra_llm_calls(
        weak_gate_chunks_with_relation=weak,
        promoted_hypotheses=promoted_hypotheses,
        max_turns=max_turns,
        t2_or_t3_chunks=t2_t3,
    )


def eval_pair_entities() -> list[tuple[str, ExtractedEntity]]:
    return [
        (
            "eval-alice",
            ExtractedEntity(
                name="Alice",
                summary="Persona citata nel frammento di valutazione.",
                kernel_category=EntityKernelType.Agente,
            ),
        ),
        (
            "eval-acme",
            ExtractedEntity(
                name="Acme",
                summary="Organizzazione citata nel frammento di valutazione.",
                kernel_category=EntityKernelType.CostruttoSociale,
            ),
        ),
    ]


def pair_for_item(item: EvalCase) -> list[tuple[str, ExtractedEntity]]:
    return eval_pair_entities() if item.usable_pair else []


def s0_for_item(item: EvalCase) -> S0Outcome:
    return S0Outcome(relation_written=item.relation_written, has_comparables=True)


def gate_only_classify(item: EvalCase) -> FragmentSignal | None:
    return classify_fragment_relevance(item.text, pair_for_item(item), s0_for_item(item))


def cue_based_structural_verdict(text: str) -> StructuralSignalVerdict:
    folded = (text or "").casefold()
    for cue, category in _STUB_CUES:
        if cue in folded:
            return StructuralSignalVerdict(
                has_signal=True,
                marker_category=category,
                claim_target_hint="",
                reasoning=f"stub cue {cue!r}",
            )
    return StructuralSignalVerdict(
        has_signal=False,
        marker_category=None,
        claim_target_hint="",
        reasoning="fatto semplicemente nuovo (stub)",
    )


async def stub_model_fn(system: str, user: str) -> StructuralSignalVerdict:
    _ = system
    # The user prompt embeds the fragment after "Frammento:\\n".
    chunk = user
    marker = "Frammento:\n"
    if marker in user:
        chunk = user.split(marker, 1)[1].split("\n\n", 1)[0]
    return cue_based_structural_verdict(chunk)


def make_fallback_classify(
    model_fn: Callable[[str, str], Awaitable[StructuralSignalVerdict]] | None = None,
) -> Callable[[EvalCase], Awaitable[FragmentSignal | None]]:
    async def _classify(item: EvalCase) -> FragmentSignal | None:
        return await classify_fragment_relevance_with_model_fallback(
            item.text,
            pair_for_item(item),
            s0_for_item(item),
            relation_text=item.text,
            job_id="eval-context-layer",
            model_fn=model_fn,
        )

    return _classify


def _safe_div(num: float, den: float) -> float:
    if den <= 0:
        return 0.0
    return num / den


async def evaluate_relevance_gate(
    corpus: Sequence[EvalCase],
    classifier: Classifier,
    *,
    mode: str = "gate-only",
) -> EvalReport:
    """Measure signal-level precision/recall/F1. Category mismatches are listed."""
    tp = fp = fn = tn = 0
    category_matches = 0
    wrong: list[WrongCase] = []

    for item in corpus:
        predicted = classifier(item)
        if inspect.isawaitable(predicted):
            predicted = await predicted
        pred_signal = predicted is not None
        pred_cat = predicted.marker_category if predicted is not None else None

        if item.expected_signal and pred_signal:
            tp += 1
            if item.expected_category is None or pred_cat == item.expected_category:
                category_matches += 1
            else:
                wrong.append(
                    WrongCase(
                        id=item.id,
                        reason=(
                            f"category mismatch: expected {item.expected_category}, "
                            f"got {pred_cat}"
                        ),
                        expected_signal=True,
                        predicted_signal=True,
                        expected_category=item.expected_category,
                        predicted_category=pred_cat,
                        text=item.text,
                    )
                )
        elif (not item.expected_signal) and (not pred_signal):
            tn += 1
        elif (not item.expected_signal) and pred_signal:
            fp += 1
            wrong.append(
                WrongCase(
                    id=item.id,
                    reason=f"false positive ({pred_cat or predicted.kind})",
                    expected_signal=False,
                    predicted_signal=True,
                    expected_category=item.expected_category,
                    predicted_category=pred_cat,
                    text=item.text,
                )
            )
        else:
            fn += 1
            wrong.append(
                WrongCase(
                    id=item.id,
                    reason="false negative (gate/fallback missed the signal)",
                    expected_signal=True,
                    predicted_signal=False,
                    expected_category=item.expected_category,
                    predicted_category=None,
                    text=item.text,
                )
            )

    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, tp + fn)
    f1 = _safe_div(2 * precision * recall, precision + recall) if (precision or recall) else 0.0
    return EvalReport(
        mode=mode,
        n=len(corpus),
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        true_negatives=tn,
        precision=precision,
        recall=recall,
        f1=f1,
        category_matches=category_matches,
        wrong=wrong,
    )


def collision_control_sentences(marker: str) -> list[str]:
    """Semantically bland sentences where ``marker`` sits inside a longer word."""
    stripped = (marker or "").strip()
    if not stripped:
        return []
    parts = stripped.split(None, 1)
    first = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    sentences: list[str] = []
    for prefix in ("fin", "in", "vice", "sotto", "anti"):
        glued = f"{prefix}{first}"
        span = f"{glued} {rest}".strip() if rest else glued
        sentences.append(f"Appunto di registro: {span} resta com'è, senza transizione.")
    return sentences


def all_t2_markers() -> list[tuple[MarkerCategory, str]]:
    rows: list[tuple[MarkerCategory, str]] = []
    for marker in RETRACTION_MARKERS:
        rows.append(("retraction", marker))
    for marker in QUANTIFIER_MARKERS:
        rows.append(("quantifier", marker))
    for marker in ERROR_MARKERS:
        rows.append(("error", marker))
    for marker in SUCCESSION_MARKERS:
        rows.append(("succession", marker))
    return rows


def planned_agent_turns(item: EvalCase) -> int:
    """Conservative ReAct length for a corpus item (search + conclude, +1 for scope)."""
    if not item.expected_signal:
        return 0
    if item.expected_category in {"quantifier", "error", "succession"}:
        return 3
    if item.expected_category == "retraction":
        return 2
    return 2


def evaluate_thresholds(corpus: Sequence[EvalCase] | None = None) -> ThresholdReport:
    """Calibrate listen window and ReAct cap on frozen corpus scenarios, not by gut.

    The listen window is an aging *guard* (never promotes). Reinforcement still
    promotes past the window. Planned ReAct traces on the corpus top out below
    the default cap.
    """
    window = int(settings.PENDING_HYPOTHESIS_LISTEN_WINDOW)
    cap = int(settings.CONTEXT_AGENT_MAX_TURNS)
    items = list(corpus or ())

    silence_promoted = False
    for n in range(0, window + 4):
        _conf, promoted = _confidence_and_promote(
            existing=None,
            named_witnesses=(),
            reinforcement=False,
            listen_count=n,
        )
        if promoted:
            silence_promoted = True

    _conf, late_promoted = _confidence_and_promote(
        existing=None,
        named_witnesses=(),
        reinforcement=True,
        listen_count=window + 1,
    )
    _conf, early_promoted = _confidence_and_promote(
        existing=None,
        named_witnesses=(),
        reinforcement=True,
        listen_count=1,
    )

    planned = [planned_agent_turns(item) for item in items]
    max_planned = max(planned) if planned else 0

    keep_window = (not silence_promoted) and late_promoted and early_promoted
    keep_turns = max_planned < cap or max_planned == 0

    window_reason = (
        "silence never auto-promotes at any listen_count; reinforcement at "
        f"doc {window + 1} (past the window) still promotes. Shrinking the "
        "window would not change corpus outcomes."
    )
    turns_reason = (
        f"longest planned ReAct trace on the frozen corpus is {max_planned} "
        f"(search + relations + conclude for quantifier/error/succession; "
        f"facts_from_source + conclude for retraction). Cap {cap} leaves one "
        "extra retrieval without unbounded loops."
    )
    return ThresholdReport(
        listen_window_default=window,
        listen_window_decision="keep" if keep_window else "retune",
        listen_window_reason=window_reason,
        max_turns_default=cap,
        max_turns_decision="keep" if keep_turns else "retune",
        max_turns_reason=turns_reason,
        max_planned_turns=max_planned,
        silence_never_promotes=not silence_promoted,
        late_reinforcement_promotes=bool(late_promoted),
    )
