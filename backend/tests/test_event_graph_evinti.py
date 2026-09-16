"""Inferred-event pass: stub only, designed later."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.models.event_graph import EventoRisolto, SottoGrafo
from app.pipeline.event_graph.evinti import REGOLA, evinci_eventi_documento
from app.pipeline.event_graph.zona_segmentation import Zona

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "event_graph"
EVINTI_PATH = PACKAGE_DIR / "evinti.py"
PIPELINE_PATH = PACKAGE_DIR / "pipeline.py"


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


def test_isolation_ast():
    source = EVINTI_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(EVINTI_PATH))
    modules = _import_modules(tree)
    assert not any(_is_forbidden_import(module) for module in modules)
    assert "call_structured" not in source


def test_pipeline_calls_evinti_after_sequenza():
    source = PIPELINE_PATH.read_text(encoding="utf-8")
    assert source.index("collega_dorsale_eventi(sotto, zone)") < source.index(
        "evinci_eventi_documento("
    )
    assert source.index("evinci_eventi_documento(") < source.index(
        "estrai_livello_relazioni("
    )


@pytest.mark.asyncio
async def test_evinci_is_noop():
    zona = Zona(
        id="z-0",
        documento="doc-ev",
        offset_inizio=0,
        offset_fine=20,
        ordinale=0,
        testo="Il Vento soffiò.",
        riassunto="Il Vento soffiò.",
        espansa=True,
    )
    sotto = SottoGrafo()
    sotto.aggiungi(
        eventi=[
            EventoRisolto(
                id="e-0",
                lemma="Il Vento soffiò.",
                ancora="Il Vento soffiò.",
                chunk_id="z-0",
            )
        ]
    )
    added = await evinci_eventi_documento(sotto, [zona], job_id="job-ev")
    assert added == []
    assert len(sotto.eventi) == 1
    assert REGOLA == "evinti.non_implementato"
