"""M-ponte macro-vs-micro verification tests (no Docker, no LLM)."""

from __future__ import annotations

import ast
from pathlib import Path

from app.models.event_graph import ArcoEvento, EventoRisolto, SottoGrafo
from app.pipeline.event_graph.ponte_verifica import verifica_dopo_espansione
from app.pipeline.event_graph.zona_edges import ArcoZona
from app.pipeline.event_graph.zona_segmentation import Zona

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "event_graph"
PONTE_PATH = PACKAGE_DIR / "ponte_verifica.py"


def _zona(ordinale: int, *, espansa: bool = False, **overrides) -> Zona:
    payload = {
        "id": f"z-{ordinale}",
        "documento": "doc-ponte",
        "offset_inizio": ordinale * 100,
        "offset_fine": ordinale * 100 + 100,
        "ordinale": ordinale,
        "testo": f"zona {ordinale}",
        "espansa": espansa,
    }
    payload.update(overrides)
    return Zona(**payload)


def _arco_macro(
    tipo: str,
    da_id: str = "z-0",
    a_id: str = "z-1",
    *,
    verificato: bool | None = None,
) -> ArcoZona:
    return ArcoZona(
        da_id=da_id,
        a_id=a_id,
        tipo=tipo,
        livello="macro",
        verificato=verificato,
    )


def _evento(
    event_id: str,
    offset_inizio: int,
    offset_fine: int,
    *,
    e_testa: bool = True,
    posizione_doc: int | None = None,
) -> EventoRisolto:
    return EventoRisolto(
        id=event_id,
        lemma="arrivare",
        e_testa=e_testa,
        offset_inizio=offset_inizio,
        offset_fine=offset_fine,
        posizione_doc=posizione_doc,
    )


def _sotto_heads_causa() -> SottoGrafo:
    e0 = _evento("e0", 10, 20, posizione_doc=0)
    e1 = _evento("e1", 110, 120, posizione_doc=1)
    sotto = SottoGrafo()
    sotto.aggiungi(
        eventi=[e0, e1],
        archi=[ArcoEvento(tipo="CAUSA", da_id="e0", a_id="e1")],
    )
    return sotto


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
    if lowered.endswith(".persistence") or "persistence" in lowered:
        return True
    if module in {"openai", "neo4j", "httpx"}:
        return True
    return False


def test_neither_expanded_leaves_verificato_none():
    z0 = _zona(0, espansa=False)
    z1 = _zona(1, espansa=False)
    archi = [_arco_macro("CAUSA")]
    out = verifica_dopo_espansione("z-0", [z0, z1], archi, _sotto_heads_causa())
    assert out is archi
    assert len(out) == 1
    assert out[0].verificato is None
    assert z1.espansa is False


def test_only_one_expanded_leaves_verificato_none():
    z0 = _zona(0, espansa=True)
    z1 = _zona(1, espansa=False)
    archi = [_arco_macro("CAUSA")]
    out = verifica_dopo_espansione("z-0", [z0, z1], archi, _sotto_heads_causa())
    assert len(out) == 1
    assert out[0].verificato is None
    assert z1.espansa is False


def test_both_expanded_micro_causa_justifies_macro_causa():
    z0 = _zona(0, espansa=True)
    z1 = _zona(1, espansa=False)
    archi = [_arco_macro("CAUSA")]
    out = verifica_dopo_espansione("z-1", [z0, z1], archi, _sotto_heads_causa())
    assert z0.espansa is True
    assert z1.espansa is True
    assert len(out) == 1
    assert out[0].verificato is True
    assert out[0].tipo == "CAUSA"


def test_both_expanded_no_micro_marks_false_keeps_macro():
    z0 = _zona(0, espansa=True)
    z1 = _zona(1, espansa=True)
    archi = [_arco_macro("CAUSA")]
    sotto = SottoGrafo()
    sotto.aggiungi(
        eventi=[
            _evento("e0", 10, 20, posizione_doc=0),
            _evento("e1", 110, 120, posizione_doc=1),
        ]
    )
    out = verifica_dopo_espansione("z-1", [z0, z1], archi, sotto)
    assert len(out) == 1
    assert out[0] is archi[0]
    assert out[0].verificato is False
    assert out[0].tipo == "CAUSA"
    assert out[0].da_id == "z-0"
    assert out[0].a_id == "z-1"


def test_both_expanded_micro_collegato_does_not_justify_macro_causa():
    z0 = _zona(0, espansa=True)
    z1 = _zona(1, espansa=True)
    archi = [_arco_macro("CAUSA")]
    sotto = SottoGrafo()
    sotto.aggiungi(
        eventi=[
            _evento("e0", 10, 20, posizione_doc=0),
            _evento("e1", 110, 120, posizione_doc=1),
        ],
        archi=[ArcoEvento(tipo="COLLEGATO", da_id="e0", a_id="e1")],
    )
    out = verifica_dopo_espansione("z-1", [z0, z1], archi, sotto)
    assert len(out) == 1
    assert out[0].verificato is False


def test_both_expanded_micro_causa_justifies_macro_collegato():
    z0 = _zona(0, espansa=True)
    z1 = _zona(1, espansa=True)
    archi = [_arco_macro("COLLEGATO")]
    out = verifica_dopo_espansione("z-1", [z0, z1], archi, _sotto_heads_causa())
    assert len(out) == 1
    assert out[0].verificato is True
    assert out[0].tipo == "COLLEGATO"


def test_isolation_ast():
    source = PONTE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(PONTE_PATH))
    modules = _import_modules(tree)
    violations = [module for module in modules if _is_forbidden_import(module)]
    assert violations == []
    assert all("app.core" not in module for module in modules)
    assert "openai" not in modules
    assert "from app.pipeline.event_graph.persistence" not in source
    assert "FLASH_MODE" not in source
    assert "def verifica_dopo_espansione" in source
    assert "def giustifica" in source
    assert "verificato" in source
