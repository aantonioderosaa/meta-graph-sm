"""M-eval: gold corpus well-formedness + segmentation/attribute/relation metrics."""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

from app.pipeline.event_graph.eval_metrics import (
    REQUIRED_PHENOMENA,
    default_corpus_path,
    load_corpus,
    score_attributes,
    score_corpus,
    score_item,
    score_relations,
    score_segmentation,
    validate_corpus,
    validate_item,
)

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "event_graph"
EVAL_METRICS_PATH = PACKAGE_DIR / "eval_metrics.py"
CORPUS_PATH = Path(__file__).resolve().parent / "fixtures" / "event_graph_eval_corpus.json"


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


def test_corpus_size_and_languages():
    items = load_corpus(CORPUS_PATH)
    assert 95 <= len(items) <= 110
    langs = {item["lang"] for item in items}
    assert langs == {"it", "en"}
    n_it = sum(1 for item in items if item["lang"] == "it")
    n_en = sum(1 for item in items if item["lang"] == "en")
    assert n_it >= 40
    assert n_en >= 40
    assert default_corpus_path() == CORPUS_PATH


def test_all_required_phenomena_present():
    items = load_corpus(CORPUS_PATH)
    seen: set[str] = set()
    for item in items:
        seen.update(item["phenomena"])
    missing = REQUIRED_PHENOMENA - seen
    assert missing == set()
    for lang in ("it", "en"):
        lang_seen: set[str] = set()
        for item in items:
            if item["lang"] == lang:
                lang_seen.update(item["phenomena"])
        missing_lang = REQUIRED_PHENOMENA - lang_seen
        assert missing_lang == set(), f"{lang} missing {sorted(missing_lang)}"


def test_gold_offsets_and_indices_valid():
    items = load_corpus(CORPUS_PATH)
    errors = validate_corpus(items)
    assert errors == []
    for item in items:
        assert validate_item(item) == []
        gold = item["gold"]
        text = item["text"]
        for sent in gold["sentences"]:
            assert text[sent["offset_inizio"] : sent["offset_fine"]] == sent["testo"]
        indices = {ev["indice"] for ev in gold["eventi"]}
        for rel in gold["relazioni"]:
            assert rel["da_indice"] in indices
            assert rel["a_indice"] in indices


def test_metrics_perfect_copy_f1_one():
    items = load_corpus(CORPUS_PATH)
    for item in items:
        scored = score_item(item["gold"], item["gold"])
        assert scored["segmentation"]["f1"] == 1.0, item["id"]
        assert scored["attributes"]["f1"] == 1.0, item["id"]
        assert scored["relations"]["f1"] == 1.0, item["id"]
        for field, metrics in scored["attributes"]["per_field"].items():
            assert metrics["f1"] == 1.0, f"{item['id']} field {field}"

    predictions = {item["id"]: item["gold"] for item in items}
    aggregate = score_corpus(items, predictions)
    assert aggregate["segmentation"]["f1"] == 1.0
    assert aggregate["attributes"]["f1"] == 1.0
    assert aggregate["relations"]["f1"] == 1.0
    assert aggregate["n_items"] == len(items)


def test_metrics_empty_pred_f1_zero_no_crash():
    items = load_corpus(CORPUS_PATH)
    nonempty = next(
        item
        for item in items
        if item["gold"]["sentences"]
        and item["gold"]["eventi"]
        and item["gold"]["relazioni"]
    )
    empty = {"sentences": [], "eventi": [], "relazioni": []}
    scored = score_item(nonempty["gold"], empty)
    assert scored["segmentation"]["f1"] == 0.0
    assert scored["attributes"]["f1"] == 0.0
    assert scored["relations"]["f1"] == 0.0

    stub_none = score_item(nonempty["gold"], None)
    assert stub_none["segmentation"]["f1"] == 0.0
    assert stub_none["attributes"]["f1"] == 0.0
    assert stub_none["relations"]["f1"] == 0.0

    aggregate = score_corpus(items, {})
    assert aggregate["segmentation"]["f1"] == 0.0
    assert aggregate["attributes"]["f1"] == 0.0
    assert aggregate["relations"]["f1"] == 0.0
    assert aggregate["n_items"] == len(items)

    empty_lists = score_segmentation([], [])
    assert empty_lists["f1"] == 1.0
    assert score_attributes([], [])["f1"] == 1.0
    assert score_relations([], [])["f1"] == 1.0


def test_eval_metrics_isolation_ast():
    source = EVAL_METRICS_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(EVAL_METRICS_PATH))
    violations = [
        f"{EVAL_METRICS_PATH}: {module}"
        for module in _import_modules(tree)
        if _is_forbidden_import(module)
    ]
    assert violations == []
    assert "app.core" not in source
    assert "sklearn" not in source
    assert "sentence_transformers" not in source
    assert "app.models" not in source


def test_corpus_ids_unique_and_phenomena_counts():
    items = load_corpus(CORPUS_PATH)
    ids = [item["id"] for item in items]
    assert len(ids) == len(set(ids))
    counts = Counter(tag for item in items for tag in item["phenomena"])
    for tag in REQUIRED_PHENOMENA:
        assert counts[tag] >= 2, f"{tag} only {counts[tag]}"
