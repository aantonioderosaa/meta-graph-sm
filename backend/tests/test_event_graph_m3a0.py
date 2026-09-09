"""M3a-0 zone MICRO preprocessing: dialogue/narrative units (no Docker, no LLM)."""

from __future__ import annotations

import ast
from pathlib import Path

from app.pipeline.event_graph.chunking_periods import (
    UnitaTesto,
    preprocess_zona,
)
from app.pipeline.event_graph.zona_segmentation import Zona

CHUNKING_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "pipeline"
    / "event_graph"
    / "chunking_periods.py"
)

_HOMOGENEOUS = (
    "The old mill stood beside the quiet river. "
    "Water flowed under the wooden mill wheel. "
    "The miller watched the river from the mill door."
)


def _zona(testo: str, offset_inizio: int = 0, zona_id: str = "z-test") -> Zona:
    return Zona(
        id=zona_id,
        documento="doc-m3a0",
        offset_inizio=offset_inizio,
        offset_fine=offset_inizio + len(testo),
        ordinale=0,
        testo=testo,
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
    return False


def test_abbreviations_and_decimals_do_not_split():
    text = (
        "Dr. Rossi measured 3.14 units. "
        "Prof. Bianchi agreed with Dr. Rossi about the 3.14 reading."
    )
    units = preprocess_zona(_zona(text))
    assert len(units) == 2
    assert all(unit.tipo == "narrativa" for unit in units)
    assert "Dr. Rossi" in units[0].testo
    assert "3.14" in units[0].testo
    assert "Prof. Bianchi" in units[1].testo
    assert "3.14" in units[1].testo
    assert all(isinstance(unit, UnitaTesto) for unit in units)


def test_direct_speech_reporting_and_following_narrative():
    text = "Mario disse: «Vado via.» Poi partì."
    units = preprocess_zona(_zona(text))
    assert units
    quote = "Vado via"
    assert any(quote in unit.testo for unit in units)

    dialogo = [unit for unit in units if unit.tipo == "dialogo"]
    narrativa = [unit for unit in units if unit.tipo == "narrativa"]
    if dialogo:
        assert any("disse" in unit.testo for unit in narrativa)
        assert any(quote in unit.testo for unit in dialogo)
        for unit in narrativa:
            inside_quotes_only = (
                quote in unit.testo
                and "disse" not in unit.testo
                and "partì" not in unit.testo
                and "Poi" not in unit.testo
            )
            assert not inside_quotes_only
    else:
        reporting = [unit for unit in narrativa if "disse" in unit.testo]
        assert reporting
        assert any(quote in unit.testo for unit in reporting)

    assert any("partì" in unit.testo for unit in narrativa)
    assert any(unit.tipo == "narrativa" for unit in units)


def test_absolute_offsets_use_zona_offset_inizio():
    text = "Mario arrivò alle tre. Quindi partì."
    offset = 10
    zona = _zona(text, offset_inizio=offset)
    document = ("#" * offset) + text
    units = preprocess_zona(zona, document_text=document)
    assert units
    assert all(unit.offset_inizio >= offset for unit in units)
    assert all(unit.offset_fine > unit.offset_inizio for unit in units)
    for unit in units:
        excerpt = document[unit.offset_inizio : unit.offset_fine]
        assert excerpt == unit.testo or excerpt in unit.testo or unit.testo in excerpt
        local = unit.offset_inizio - offset
        assert zona.testo[local : local + len(unit.testo)] == unit.testo


def test_boundary_connective_on_following_sentence():
    text = "Mario arrivò alle tre. Quindi partì."
    units = preprocess_zona(_zona(text))
    assert len(units) >= 2
    second = units[1]
    assert second.connettivo_confine is not None
    assert second.connettivo_confine.lower() == "quindi"
    assert units[0].connettivo_confine is None


def test_homogeneous_narrative_is_all_narrativa():
    units = preprocess_zona(_zona(_HOMOGENEOUS))
    assert units
    assert all(unit.tipo == "narrativa" for unit in units)
    assert [unit.indice for unit in units] == list(range(len(units)))
    assert all(unit.zona_id == "z-test" for unit in units)


def test_unclosed_quote_is_not_split_into_dialogo():
    text = "Mario disse: «Vado via. Poi partì."
    units = preprocess_zona(_zona(text))
    assert units
    assert all(unit.tipo == "narrativa" for unit in units)
    assert any("Vado via" in unit.testo for unit in units)


def test_chunking_periods_isolation_ast():
    source = CHUNKING_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(CHUNKING_PATH))
    violations = [
        module for module in _import_modules(tree) if _is_forbidden_import(module)
    ]
    assert violations == []
    modules = _import_modules(tree)
    assert all(
        "sentence_transformers" not in module and "nltk" not in module
        for module in modules
    )
    assert "app.core" not in source
