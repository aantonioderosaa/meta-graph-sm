"""M-dedup: sequential per-sentence dedup before inter-sentence linking."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.models.event_graph import EventEntityExtractionResult, EventEntityParticipation
from app.pipeline.event_graph.dedup import (
    STAGE_ORDER,
    STAGES_FINO_DEDUP,
    DedupResult,
    espandi_zona_fino_dedup,
)
from app.pipeline.event_graph.zona_segmentation import Zona

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "event_graph"
DEDUP_PATH = PACKAGE_DIR / "dedup.py"
SEGMENTATION_PATH = PACKAGE_DIR / "segmentation.py"
MODELS_PATH = Path(__file__).resolve().parents[1] / "app" / "models" / "event_graph.py"

_FORBIDDEN_PAIR = ("sentence_pair_linking", "zona_edges")


def _zona(
    testo: str = "Marco arrivò. Marco arrivò.",
    zona_id: str = "z-dedup",
    documento: str = "doc-dedup",
    offset_inizio: int = 0,
) -> Zona:
    return Zona(
        id=zona_id,
        documento=documento,
        offset_inizio=offset_inizio,
        offset_fine=offset_inizio + len(testo),
        ordinale=0,
        testo=testo,
    )


async def _due_marco_arrivo(_chunk_text, job_id=None):
    del _chunk_text, job_id
    return EventEntityExtractionResult(
        participations=[
            EventEntityParticipation(event="Marco arrivò.", entities=["Marco"]),
            EventEntityParticipation(event="Marco arrivò.", entities=["Marco"]),
        ]
    )


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
    lowered = module.lower()
    if "sentence_transformers" in lowered or "sentence-transformers" in lowered:
        return True
    if "sentence_pair_linking" in lowered:
        return True
    if "zona_edges" in lowered:
        return True
    return False


@pytest.mark.asyncio
async def test_same_lemma_same_sogg_fuses_or_chains():
    result = await espandi_zona_fino_dedup(_zona(), estrai_frase=_due_marco_arrivo)
    assert isinstance(result, DedupResult)
    narrativa = [unit for unit in result.unita if unit.tipo == "narrativa"]
    assert len(narrativa) == 2
    assert len(result.sotto.eventi) == 2
    assert {event.lemma for event in result.sotto.eventi} == {"Marco arrivò."}
    assert result.esiti
    kinds = {esito.kind for esito in result.esiti}
    assert kinds <= {"Fusione", "Successione", "Catena"}
    assert "Fusione" in kinds or "Catena" in kinds
    if any(esito.kind == "Fusione" for esito in result.esiti):
        assert any(event.fuso_in for event in result.sotto.eventi)


@pytest.mark.asyncio
async def test_sentence_two_classifies_against_nonempty_pool(monkeypatch):
    recorded: list[dict[str, object]] = []
    original = __import__(
        "app.pipeline.event_graph.event_coref", fromlist=["classifica"]
    ).classifica

    def wrapped(evento, candidati_list, **kwargs):
        recorded.append(
            {
                "nuovo": evento.id,
                "pool": [item.id for item in candidati_list],
                "pool_size": len(list(candidati_list)),
            }
        )
        return original(evento, candidati_list, **kwargs)

    monkeypatch.setattr(
        "app.pipeline.event_graph.event_coref.classifica", wrapped
    )

    result = await espandi_zona_fino_dedup(_zona(), estrai_frase=_due_marco_arrivo)
    assert len(result.sotto.eventi) == 2
    nonempty = [row for row in recorded if row["pool"]]
    assert nonempty, "sentence 2 must be classified against a non-empty pool"
    first_id = result.sotto.eventi[0].id
    second_id = result.sotto.eventi[1].id
    later = next(row for row in recorded if row["nuovo"] == second_id)
    assert first_id in later["pool"]


def test_stage_order_dedup_before_pair():
    assert STAGE_ORDER == ("preprocess", "extract", "dedup", "pair", "nonadj", "allen")
    assert STAGE_ORDER.index("dedup") < STAGE_ORDER.index("pair")
    assert STAGES_FINO_DEDUP == ("preprocess", "extract", "dedup")
    assert "pair" not in STAGES_FINO_DEDUP


def test_dedup_does_not_import_pair_linking():
    source = DEDUP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(DEDUP_PATH))
    modules = _import_modules(tree)
    for forbidden in _FORBIDDEN_PAIR:
        assert all(forbidden not in module for module in modules)
    assert not any(
        node.module and "sentence_pair_linking" in node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )
    assert not any(
        alias.name == "sentence_pair_linking" or "zona_edges" in alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )


def test_isolation_ast():
    violations: list[str] = []
    for path in (DEDUP_PATH, SEGMENTATION_PATH):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for module in _import_modules(tree):
            if _is_forbidden_import(module):
                violations.append(f"{path}: {module}")
        assert "sentence-transformers" not in source
        assert "sentence_transformers" not in source
    models_tree = ast.parse(
        MODELS_PATH.read_text(encoding="utf-8"), filename=str(MODELS_PATH)
    )
    for module in _import_modules(models_tree):
        if _is_forbidden_import(module):
            violations.append(f"{MODELS_PATH}: {module}")
    dedup_src = DEDUP_PATH.read_text(encoding="utf-8")
    assert "app.core" not in dedup_src
    assert violations == []
    assert "espandi_zona_fino_dedup" in dedup_src
    assert "STAGE_ORDER" in dedup_src
    assert "risolvi_intra" in dedup_src
    assert "classifica" in dedup_src
    assert "chains" in dedup_src
