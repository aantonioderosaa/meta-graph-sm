"""M7 backbone tests (no Docker, no live LLM)."""

from __future__ import annotations

import ast
from pathlib import Path

from app.models.event_graph import ArcoEvento, EventoRisolto, RunState, SottoGrafo
from app.pipeline.event_graph.backbone import avanza, estendi
from app.pipeline.event_graph.narrative_plane import satelliti

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "event_graph"
BACKBONE_PATH = PACKAGE_DIR / "backbone.py"


def _evento(
    id: str,
    *,
    piano: str,
    posizione_doc: int = 0,
    posizione_chunk: int = 0,
    documento: str | None = "doc-a",
    chunk_id: str | None = "chunk-a",
) -> EventoRisolto:
    return EventoRisolto(
        id=id,
        lemma=id,
        piano=piano,  # type: ignore[arg-type]
        posizione_doc=posizione_doc,
        posizione_chunk=posizione_chunk,
        documento=documento,
        chunk_id=chunk_id,
    )


def _pp(id: str, **kwargs) -> EventoRisolto:
    return _evento(id, piano="PRIMO_PIANO", **kwargs)


def _sf(id: str, **kwargs) -> EventoRisolto:
    return _evento(id, piano="SFONDO", **kwargs)


def _fuori(id: str, **kwargs) -> EventoRisolto:
    return _evento(id, piano="FUORI_LINEA", **kwargs)


def _sequenza_pairs(archi: list[ArcoEvento]) -> list[tuple[str, str]]:
    return [(arco.da_id, arco.a_id) for arco in archi if arco.tipo == "SEQUENZA"]


def test_two_pp_one_chunk_sequenza_and_tail():
    sotto = SottoGrafo()
    state = RunState()
    eventi = [
        _pp("pp-b", posizione_chunk=2),
        _pp("pp-a", posizione_chunk=1),
    ]
    before_eventi = list(sotto.eventi)
    arcs = estendi(eventi, sotto, state)

    assert _sequenza_pairs(arcs) == [("pp-a", "pp-b")]
    assert all(arco.props.get("regola") == "backbone.estendi" for arco in arcs)
    assert state.backbone_tail == "pp-b"
    assert sotto.eventi == before_eventi
    assert _sequenza_pairs(sotto.archi) == [("pp-a", "pp-b")]


def test_next_chunk_links_previous_tail():
    sotto = SottoGrafo()
    state = RunState()
    chunk1 = [
        _pp("pp-a", posizione_doc=0, posizione_chunk=0),
        _pp("pp-b", posizione_doc=0, posizione_chunk=1),
    ]
    estendi(chunk1, sotto, state)
    sotto.aggiungi(eventi=chunk1)
    assert state.backbone_tail == "pp-b"

    chunk2 = [
        _pp("pp-c", posizione_doc=1, posizione_chunk=0, chunk_id="chunk-b"),
        _pp("pp-d", posizione_doc=1, posizione_chunk=1, chunk_id="chunk-b"),
    ]
    arcs = estendi(chunk2, sotto, state)
    assert _sequenza_pairs(arcs) == [("pp-b", "pp-c"), ("pp-c", "pp-d")]
    assert state.backbone_tail == "pp-d"


def test_cross_doc_no_sequenza_new_spine_still_chains():
    tail = _pp("pp-old", documento="doc-a", posizione_doc=0, posizione_chunk=0)
    sotto = SottoGrafo()
    sotto.aggiungi(eventi=[tail])
    state = RunState(backbone_tail=tail.id)
    nuovi = [
        _pp("pp-x", documento="doc-b", posizione_doc=0, posizione_chunk=0, chunk_id="chunk-b"),
        _pp("pp-y", documento="doc-b", posizione_doc=0, posizione_chunk=1, chunk_id="chunk-b"),
    ]
    arcs = estendi(nuovi, sotto, state)
    pairs = _sequenza_pairs(arcs)
    assert ("pp-old", "pp-x") not in pairs
    assert pairs == [("pp-x", "pp-y")]
    assert state.backbone_tail == "pp-y"


def test_sfondo_only_no_sequenza_satellites_when_pp_present():
    tail = _pp("pp-prev", posizione_doc=0, posizione_chunk=0)
    sotto = SottoGrafo()
    sotto.aggiungi(eventi=[tail])
    state = RunState(backbone_tail=tail.id)
    only_sfondo = [
        _sf("sf-1", posizione_doc=1, posizione_chunk=0, chunk_id="chunk-b"),
        _sf("sf-2", posizione_doc=1, posizione_chunk=1, chunk_id="chunk-b"),
    ]
    arcs = estendi(only_sfondo, sotto, state)
    assert _sequenza_pairs(arcs) == []
    assert all(arco.tipo != "SEQUENZA" for arco in sotto.archi)
    assert state.backbone_tail == tail.id
    assert satelliti(only_sfondo) == []

    sotto2 = SottoGrafo()
    state2 = RunState()
    mixed = [
        _pp("pp-here", posizione_chunk=0),
        _sf("sf-here", posizione_chunk=1),
    ]
    arcs2 = estendi(mixed, sotto2, state2)
    satellites = [arco for arco in arcs2 if arco.tipo == "SATELLITE_DI"]
    expected = satelliti(mixed)
    assert [(arco.da_id, arco.a_id) for arco in satellites] == [
        (arco.da_id, arco.a_id) for arco in expected
    ]
    assert satellites
    assert all(
        arco.props.get("regola") == "narrative_plane.satelliti" for arco in satellites
    )
    assert _sequenza_pairs(arcs2) == []


def test_fuori_linea_not_chained():
    sotto = SottoGrafo()
    state = RunState()
    eventi = [
        _pp("pp-1", posizione_chunk=0),
        _fuori("fl-1", posizione_chunk=1),
        _pp("pp-2", posizione_chunk=2),
    ]
    arcs = estendi(eventi, sotto, state)
    pairs = _sequenza_pairs(arcs)
    assert pairs == [("pp-1", "pp-2")]
    assert all("fl-1" not in (arco.da_id, arco.a_id) for arco in arcs)
    assert state.backbone_tail == "pp-2"


def test_dedup_existing_sequenza():
    sotto = SottoGrafo()
    existing = ArcoEvento(
        tipo="SEQUENZA",
        da_id="pp-a",
        a_id="pp-b",
        props={"regola": "event_edges.categorizza"},
    )
    sotto.archi.append(existing)
    state = RunState()
    eventi = [
        _pp("pp-a", posizione_chunk=0),
        _pp("pp-b", posizione_chunk=1),
    ]
    arcs = estendi(eventi, sotto, state)
    assert _sequenza_pairs(arcs) == []
    sequenze = [arco for arco in sotto.archi if arco.tipo == "SEQUENZA"]
    assert len(sequenze) == 1
    assert sequenze[0] is existing
    assert state.backbone_tail == "pp-b"


def test_avanza_updates_base_and_optional_tail():
    state = RunState()
    returned = avanza(state, "passato")
    assert returned is state
    assert state.tempo_base_precedente == "passato"
    assert state.backbone_tail is None

    avanza(state, "presente", backbone_tail="ev-1")
    assert state.tempo_base_precedente == "presente"
    assert state.backbone_tail == "ev-1"

    avanza(state, "imperfetto")
    assert state.tempo_base_precedente == "imperfetto"
    assert state.backbone_tail == "ev-1"


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
    if module == "app.pipeline.backbone" or module.startswith("app.pipeline.backbone."):
        return True
    if "wellformed" in module:
        return True
    return False


def test_backbone_isolation_ast():
    source = BACKBONE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(BACKBONE_PATH))
    violations = [
        f"{BACKBONE_PATH}: {module}"
        for module in _import_modules(tree)
        if _is_forbidden_import(module)
    ]
    assert violations == []
    assert "app.pipeline.backbone" not in source
    assert "app.core" not in source
    assert "wellformed" not in source
    assert "from app.pipeline.event_graph.narrative_plane import satelliti" in source
