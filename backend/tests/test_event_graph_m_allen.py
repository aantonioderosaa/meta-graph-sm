"""M-allen: Allen constraint network + stage-5 chiusura temporale (no Docker, no LLM)."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import get_args

from app.models.event_graph import (
    ArcoEvento,
    ArgomentoRisolto,
    BasePrecede,
    EventoRisolto,
    SottoGrafo,
)
from app.pipeline.event_graph.allen import (
    ALLEN_13,
    INVERSE,
    AllenNetwork,
    compose,
    compose_sets,
)
from app.pipeline.event_graph.chiusura_temporale import chiusura_temporale

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "event_graph"
ALLEN_PATH = PACKAGE_DIR / "allen.py"
CHIUSURA_PATH = PACKAGE_DIR / "chiusura_temporale.py"
MODELS_PATH = Path(__file__).resolve().parents[1] / "app" / "models" / "event_graph.py"

_CLASSIC_CONTEMPORANEOUS = frozenset(
    {
        "equals",
        "overlaps",
        "overlapped_by",
        "during",
        "contains",
        "starts",
        "started_by",
        "finishes",
        "finished_by",
    }
)


def _evento(
    event_id: str,
    *,
    lemma: str = "arrivare",
    tempo: str = "passato",
    e_testa: bool = True,
    posizione_doc: int = 0,
    span: str | None = None,
    menzione: str = "m-marco",
) -> EventoRisolto:
    return EventoRisolto(
        id=event_id,
        lemma=lemma,
        tempo=tempo,  # type: ignore[arg-type]
        e_testa=e_testa,
        posizione_doc=posizione_doc,
        span=span,
        documento="doc-allen",
        argomenti=[ArgomentoRisolto(ruolo="SOGG", menzione_id=menzione)],
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
    if lowered.endswith(".persistence") or "persistence" in lowered:
        return True
    if module in {"openai", "neo4j", "httpx"}:
        return True
    return False


def test_compose_identities():
    assert compose("before", "before") == frozenset({"before"})
    assert compose("before", "meets") == frozenset({"before"})
    assert compose("meets", "meets") == frozenset({"before"})
    for rel in ALLEN_13:
        assert compose("equals", rel) == frozenset({rel})  # type: ignore[arg-type]
        assert compose(rel, "equals") == frozenset({rel})  # type: ignore[arg-type]
    before_after = compose("before", "after")
    assert before_after
    assert before_after == frozenset(ALLEN_13)
    meets_met_by = compose("meets", "met_by")
    assert {"equals", "finishes", "finished_by"} <= meets_met_by
    assert meets_met_by
    # Classic 9-set in Allen 1983 is overlaps ∘ overlapped_by (not meets ∘ met_by).
    assert _CLASSIC_CONTEMPORANEOUS <= compose("overlaps", "overlapped_by")
    assert _CLASSIC_CONTEMPORANEOUS <= compose_sets(
        frozenset({"overlaps"}), frozenset({"overlapped_by"})
    )


def test_compose_never_empty_for_base_pair():
    for left in ALLEN_13:
        for right in ALLEN_13:
            got = compose(left, right)  # type: ignore[arg-type]
            assert got, (left, right)


def test_compose_inverse_symmetry():
    for a in ALLEN_13:
        for b in ALLEN_13:
            left = frozenset(INVERSE[rel] for rel in compose(a, b))  # type: ignore[arg-type]
            right = compose(INVERSE[b], INVERSE[a])  # type: ignore[arg-type]
            assert left == right, (a, b, left, right)


def test_path_consistency_transitivity_singleton():
    net = AllenNetwork()
    net.add_constraint("A", "B", {"before"})
    net.add_constraint("B", "C", {"before"})
    assert net.path_consistent()
    assert net.inferred("A", "C") == frozenset({"before"})


def test_inconsistency_before_both_ways():
    net = AllenNetwork()
    net.add_constraint("A", "B", {"before"})
    net.add_constraint("B", "A", {"before"})
    assert net.path_consistent() is False

    a = _evento("ev-a", lemma="arrivare", posizione_doc=0)
    b = _evento("ev-b", lemma="partire", posizione_doc=1)
    sotto = SottoGrafo()
    sotto.aggiungi(
        eventi=[a, b],
        archi=[
            ArcoEvento(
                tipo="PRECEDE",
                da_id="ev-a",
                a_id="ev-b",
                props={"base": "connettivo"},
            ),
            ArcoEvento(
                tipo="PRECEDE",
                da_id="ev-b",
                a_id="ev-a",
                props={"base": "connettivo"},
            ),
        ],
    )
    chiusura_temporale(sotto)
    assert sotto.quarantena
    assert any(
        item.motivo.startswith("ciclo cronologico:")
        or item.motivo.startswith("incoerenza allen:")
        for item in sotto.quarantena
    )
    assert any("ev-a" in item.motivo and "ev-b" in item.motivo for item in sotto.quarantena)
    assert all(str(arco.tipo) != "SEQUENZA" for arco in sotto.archi)
    assert len([arco for arco in sotto.archi if str(arco.tipo) == "PRECEDE"]) == 2


def test_pluperfect_inverts_mention_order():
    arrivò = _evento(
        "ev-a",
        lemma="arrivare",
        tempo="passato",
        e_testa=True,
        posizione_doc=0,
        span="arrivò",
    )
    perdere = _evento(
        "ev-b",
        lemma="perdere",
        tempo="trapassato",
        e_testa=True,
        posizione_doc=1,
        span="perdere",
    )
    sotto = SottoGrafo()
    sotto.aggiungi(eventi=[arrivò, perdere])
    chiusura_temporale(sotto)
    precede = [arco for arco in sotto.archi if str(arco.tipo) == "PRECEDE"]
    assert precede
    inverted = [
        arco
        for arco in precede
        if arco.da_id == "ev-b" and arco.a_id == "ev-a"
    ]
    assert inverted
    assert inverted[0].props.get("relazione_allen") == "before"
    assert inverted[0].props.get("base") == "trapassato"
    surviving_mention = [
        arco
        for arco in precede
        if arco.da_id == "ev-a"
        and arco.a_id == "ev-b"
        and not arco.props.get("superato_da")
    ]
    assert surviving_mention == []
    assert "trapassato" in get_args(BasePrecede)


def test_mention_order_is_collegato_not_precede():
    a = _evento("ev-a", lemma="discutere", posizione_doc=0)
    b = _evento("ev-b", lemma="proporre", posizione_doc=1)
    c = _evento("ev-c", lemma="soffiare", posizione_doc=2)
    sotto = SottoGrafo()
    sotto.aggiungi(eventi=[a, b, c])
    chiusura_temporale(sotto)
    precede = [arco for arco in sotto.archi if str(arco.tipo) == "PRECEDE"]
    collegato = [arco for arco in sotto.archi if str(arco.tipo) == "COLLEGATO"]
    assert precede == []
    assert {(arco.da_id, arco.a_id, arco.props.get("segnale")) for arco in collegato} == {
        ("ev-a", "ev-b", "ordine_menzione"),
        ("ev-b", "ev-c", "ordine_menzione"),
    }
    assert all(arco.props.get("base") != "dato_esplicito" for arco in sotto.archi)


def test_isolation_ast():
    for path in (ALLEN_PATH, CHIUSURA_PATH, MODELS_PATH):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        modules = _import_modules(tree)
        violations = [module for module in modules if _is_forbidden_import(module)]
        assert violations == [], violations
        assert all("app.core" not in module for module in modules)
        assert "openai" not in modules
        assert "sentence_transformers" not in source
        assert "from app.pipeline.event_graph.persistence" not in source.lower()

    allen_src = ALLEN_PATH.read_text(encoding="utf-8")
    allen_tree = ast.parse(allen_src, filename=str(ALLEN_PATH))
    allen_modules = _import_modules(allen_tree)
    assert all(
        not module.startswith("app.") for module in allen_modules
    ), allen_modules
    assert "def compose" in allen_src
    assert "class AllenNetwork" in allen_src
    assert "def path_consistent" in allen_src

    chiusura_src = CHIUSURA_PATH.read_text(encoding="utf-8")
    assert "def chiusura_temporale" in chiusura_src
    assert "seleziona_per_entita" in chiusura_src
    assert "trapassato" in chiusura_src
    assert "relazione_allen" in chiusura_src

    models_src = MODELS_PATH.read_text(encoding="utf-8")
    assert "trapassato" in models_src
    assert 'BasePrecede' in models_src
