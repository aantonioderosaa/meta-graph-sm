"""Evaluate the agentic context-layer relevance gate (Fase 24).

Standalone, same pattern as ``backfill_kernel_category.py``. No Neo4j.
``--gate-only`` is fully deterministic. ``--with-model-fallback`` uses a cue
stub unless ``--live-llm`` is passed *and* ``OPENAI_API_KEY`` is set.
``--count-calls`` prints the F25.2 extra-LLM-call formula from the corpus mix
(no OpenAI).

Usage (from backend/):
  python scripts/eval_context_layer.py --gate-only
  python scripts/eval_context_layer.py --with-model-fallback
  python scripts/eval_context_layer.py --with-model-fallback --live-llm
  python scripts/eval_context_layer.py --count-calls
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.pipeline.context_layer_eval import (  # noqa: E402
    estimate_extra_llm_calls_from_corpus,
    evaluate_relevance_gate,
    evaluate_thresholds,
    gate_only_classify,
    make_fallback_classify,
    stub_model_fn,
)
from tests.fixtures.context_layer_eval_corpus import CONTEXT_LAYER_EVAL_CORPUS  # noqa: E402

logger = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure precision/recall of the context-layer relevance gate."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--gate-only",
        action="store_true",
        help="Deterministic T1/T2/T3 gate, zero LLM.",
    )
    group.add_argument(
        "--with-model-fallback",
        action="store_true",
        help="Gate plus model fallback (stub by default; --live-llm for OpenAI).",
    )
    group.add_argument(
        "--count-calls",
        action="store_true",
        help="Print extra LLM call estimate from the corpus mix (no OpenAI).",
    )
    parser.add_argument(
        "--live-llm",
        action="store_true",
        help="Call the configured OpenAI model (requires OPENAI_API_KEY).",
    )
    parser.add_argument(
        "--skip-thresholds",
        action="store_true",
        help="Do not print F24.5 listen-window / turn-cap calibration.",
    )
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> str:
    corpus = CONTEXT_LAYER_EVAL_CORPUS
    if args.count_calls:
        estimate = estimate_extra_llm_calls_from_corpus(corpus)
        return estimate.format_markdown()
    if args.gate_only:
        report = await evaluate_relevance_gate(corpus, gate_only_classify, mode="gate-only")
    else:
        model_fn = None
        mode = "with-model-fallback"
        if args.live_llm and (settings.OPENAI_API_KEY or "").strip():
            mode = "with-model-fallback-live"
        else:
            model_fn = stub_model_fn
            if args.live_llm:
                logger.warning("OPENAI_API_KEY unset — using cue stub, not a live model.")
        report = await evaluate_relevance_gate(
            corpus,
            make_fallback_classify(model_fn),
            mode=mode,
        )
    chunks = [report.format_markdown()]
    if not args.skip_thresholds:
        chunks.append(evaluate_thresholds(corpus).format_markdown())
    return "\n".join(chunks)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    text = asyncio.run(_run(args))
    print(text, end="")


if __name__ == "__main__":
    main()
