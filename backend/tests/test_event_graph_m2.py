"""M2 period-safe chunker tests (no Docker, no LLM)."""

from __future__ import annotations

import ast
from pathlib import Path

from app.pipeline.event_graph.chunking_periods import (
    PeriodChunk,
    ha_verbo_finito,
    split,
)
from app.pipeline.event_graph.config import settings
from app.pipeline.event_graph.ids import eg_chunk_id

CHUNKING_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "pipeline"
    / "event_graph"
    / "chunking_periods.py"
)

TARGET = settings.EVENT_GRAPH_TARGET_CHUNK_WORDS
MAX_WORDS = settings.EVENT_GRAPH_MAX_CHUNK_WORDS


def _heading_filler(n_sentences: int) -> str:
    return " ".join("Mario Rossi." for _ in range(n_sentences))


def _verb_sentences(n: int) -> list[str]:
    return [f"The committee approved the bill number {i}." for i in range(n)]


def test_short_finite_verb_is_one_chunk_with_stable_id():
    text = "Mario arrivò alle tre."
    chunks = split(text, "doc-short")
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.doc_id == "doc-short"
    assert chunk.ordinale == 0
    assert chunk.testo == text
    assert chunk.id == eg_chunk_id("doc-short", 0, chunk.testo)
    assert ha_verbo_finito(chunk) is True
    assert ha_verbo_finito(text) is True


def test_long_text_packs_without_overlap():
    sentences = _verb_sentences(120)
    text = " ".join(sentences)
    chunks = split(text, "doc-pack")
    assert len(chunks) >= 2
    assert [chunk.ordinale for chunk in chunks] == list(range(len(chunks)))

    slack = max(len(sent.split()) for sent in sentences)
    for index, chunk in enumerate(chunks):
        words = len(chunk.testo.split())
        if index == len(chunks) - 1:
            continue
        if chunk.testo.count(".") <= 1 and words > MAX_WORDS:
            continue
        assert words <= TARGET + slack

    for sentence in sentences:
        owners = [chunk.ordinale for chunk in chunks if sentence in chunk.testo]
        assert owners, f"missing sentence: {sentence}"
        assert len(owners) == 1

    for left, right in zip(chunks, chunks[1:]):
        left_sents = [part.strip() for part in left.testo.split(".") if part.strip()]
        right_sents = [part.strip() for part in right.testo.split(".") if part.strip()]
        assert set(left_sents).isdisjoint(right_sents)
        assert left.testo.split()[-3:] != right.testo.split()[:3]


def test_oversized_period_is_not_split():
    period = "The committee approved " + ("additional " * 520) + "reforms in congress."
    assert len(period.split()) > MAX_WORDS
    chunks = split(period, "doc-max")
    assert len(chunks) == 1
    assert chunks[0].testo == period
    assert "The committee approved" in chunks[0].testo
    assert "reforms in congress." in chunks[0].testo
    assert len(chunks[0].testo.split()) == len(period.split())


def test_verb_less_headings_are_discarded():
    text = "Chapter 3. Mario Rossi. Anna Bianchi."
    assert ha_verbo_finito(text) is False
    assert split(text, "doc-headings") == []
    assert split("   \n\n  ", "doc-empty") == []
    assert split("", "doc-empty") == []


def test_mixed_headings_then_finite_verb_keeps_packed_ordinale():
    filler = _heading_filler(180)
    verb = "Mario arrivò alle tre."
    chunks = split(f"{filler} {verb}", "doc-mixed")
    assert chunks
    assert all(ha_verbo_finito(chunk) for chunk in chunks)
    assert all(chunk.ordinale > 0 for chunk in chunks)
    assert verb in chunks[-1].testo
    assert "arrivò" in chunks[-1].testo
    assert chunks[-1].id == eg_chunk_id(
        "doc-mixed", chunks[-1].ordinale, chunks[-1].testo
    )


def test_split_is_deterministic():
    text = " ".join(_verb_sentences(80))
    first = split(text, "doc-det")
    second = split(text, "doc-det")
    assert [chunk.id for chunk in first] == [chunk.id for chunk in second]
    assert [chunk.ordinale for chunk in first] == [chunk.ordinale for chunk in second]
    assert [chunk.testo for chunk in first] == [chunk.testo for chunk in second]


def _import_modules(tree: ast.AST) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            base = node.module or ""
            modules.append(base)
            for alias in node.names:
                if alias.name == "*":
                    continue
                modules.append(f"{base}.{alias.name}" if base else alias.name)
    return modules


def _is_forbidden_import(module: str) -> bool:
    if module == "app.core" or module.startswith("app.core."):
        return True
    if module == "app.pipeline":
        return True
    if module.startswith("app.pipeline.") and not module.startswith(
        "app.pipeline.event_graph"
    ):
        return True
    return False


def test_chunking_periods_isolation_ast():
    source = CHUNKING_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(CHUNKING_PATH))
    violations = [
        module for module in _import_modules(tree) if _is_forbidden_import(module)
    ]
    assert violations == []


def test_does_not_import_legacy_chunking():
    source = CHUNKING_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(CHUNKING_PATH))
    modules = _import_modules(tree)
    assert "app.pipeline.chunking" not in source
    assert all(
        module != "app.pipeline.chunking"
        and not module.startswith("app.pipeline.chunking.")
        for module in modules
    )


def test_finite_verb_examples():
    assert ha_verbo_finito("The cat.") is False
    assert ha_verbo_finito("Mario Rossi.") is False
    assert ha_verbo_finito("Chapter 3") is False
    assert ha_verbo_finito("Mario arrivò alle tre. Poi partì.") is True
    assert ha_verbo_finito("The committee approved the bill.") is True
    assert ha_verbo_finito("Il comitato approvò la legge.") is True
    chunk = PeriodChunk(id="x", doc_id="d", ordinale=2, testo="Mario arrivò alle tre.")
    assert ha_verbo_finito(chunk) is True
