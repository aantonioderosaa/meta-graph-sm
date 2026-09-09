"""M5 event_edges tests (no Docker, no live LLM)."""

from __future__ import annotations

import ast
from pathlib import Path

from app.models.event_graph import (
    ArcoEventoGrezzo,
    ChunkFactsheet,
    EventoRisolto,
    OrientamentoArco,
    RelazioneSegnale,
)
from app.pipeline.event_graph import RULESET_VERSION
from app.pipeline.event_graph.event_edges import REGOLA, categorizza
from app.pipeline.event_graph.ids import quarantena_id

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "event_graph"
EDGES_PATH = PACKAGE_DIR / "event_edges.py"


def _evento(
    id: str,
    indice_grezzo: int,
    *,
    piano: str | None = "PRIMO_PIANO",
    completiva_di: int | None = None,
    posizione_chunk: int | None = None,
    documento: str = "doc-m5",
    lemma: str | None = None,
    segmentazione: str | None = None,
    span: str | None = None,
    chunk_id: str | None = "chunk-m5",
) -> EventoRisolto:
    return EventoRisolto(
        id=id,
        lemma=lemma or id,
        indice_grezzo=indice_grezzo,
        piano=piano,  # type: ignore[arg-type]
        completiva_di=completiva_di,
        posizione_chunk=posizione_chunk if posizione_chunk is not None else indice_grezzo,
        documento=documento,
        segmentazione=segmentazione,  # type: ignore[arg-type]
        span=span or id,
        chunk_id=chunk_id,
    )


def _arco(
    da_indice: int,
    a_indice: int,
    segnale_testuale: str,
    relazione_segnale: RelazioneSegnale,
    orientamento: OrientamentoArco = "coordinata",
) -> ArcoEventoGrezzo:
    return ArcoEventoGrezzo(
        da_indice=da_indice,
        a_indice=a_indice,
        segnale_testuale=segnale_testuale,
        relazione_segnale=relazione_segnale,
        orientamento=orientamento,
    )


def _fs(*archi: ArcoEventoGrezzo) -> ChunkFactsheet:
    return ChunkFactsheet(eventi=[], archi=list(archi), quarantena=[])


def test_perche_causa_esplicita_sub_to_main():
    main = _evento("partire", 0, segmentazione="principale_finita")
    sub = _evento("piovere", 1, segmentazione="subordinata_finita")
    result = categorizza(
        _fs(_arco(1, 0, "perché", "causa_esplicita", "subordinata_principale")),
        [main, sub],
    )
    assert len(result.archi) == 1
    arco = result.archi[0]
    assert arco.tipo == "CAUSA"
    assert arco.da_id == "piovere"
    assert arco.a_id == "partire"
    assert arco.props["regola"] == REGOLA
    assert arco.props["versione_regole"] == RULESET_VERSION
    assert "base" not in arco.props
    assert result.quarantena == []


def test_quindi_consecuzione_previous_to_following():
    prev = _evento("cadere", 0)
    foll = _evento("alzare", 1)
    result = categorizza(
        _fs(_arco(0, 1, "quindi", "consecuzione", "coordinata")),
        [prev, foll],
    )
    assert [(arco.tipo, arco.da_id, arco.a_id) for arco in result.archi] == [
        ("CAUSA", "cadere", "alzare")
    ]


def test_mentre_temporale_ambiguo_collegato():
    left = _evento("cucinare", 0)
    right = _evento("parlare", 1)
    result = categorizza(
        _fs(_arco(0, 1, "mentre", "temporale_ambiguo")),
        [left, right],
    )
    assert len(result.archi) == 1
    arco = result.archi[0]
    assert arco.tipo == "COLLEGATO"
    assert arco.da_id == "cucinare"
    assert arco.a_id == "parlare"
    assert arco.props["segnale"] == "mentre"
    assert arco.props["regola"] == REGOLA


def test_empty_signal_gerundio_participio_nessuno_discarded():
    da = _evento("e0", 0)
    a = _evento("e1", 1)
    for relazione in ("gerundio", "participio_assoluto", "nessuno"):
        result = categorizza(
            _fs(_arco(0, 1, "", relazione)),  # type: ignore[arg-type]
            [da, a],
        )
        assert result.archi == [], relazione
        assert result.quarantena == [], relazione


def test_nessuno_with_signal_is_collegato():
    da = _evento("e0", 0)
    a = _evento("e1", 1)
    result = categorizza(_fs(_arco(0, 1, "e", "nessuno")), [da, a])
    assert len(result.archi) == 1
    assert result.archi[0].tipo == "COLLEGATO"
    assert result.archi[0].props["segnale"] == "e"


def test_asindeto_contiguous_primo_piano_keeps_sequenza():
    first = _evento("entrare", 0, piano="PRIMO_PIANO", posizione_chunk=0)
    second = _evento("sedersi", 1, piano="PRIMO_PIANO", posizione_chunk=1)
    result = categorizza(
        _fs(_arco(0, 1, "", "asindeto_sequenziale")),
        [first, second],
    )
    assert [(arco.tipo, arco.da_id, arco.a_id) for arco in result.archi] == [
        ("SEQUENZA", "entrare", "sedersi")
    ]


def test_asindeto_not_both_pp_or_not_contiguous_discarded():
    pp = _evento("e0", 0, piano="PRIMO_PIANO", posizione_chunk=0)
    sfondo = _evento("e1", 1, piano="SFONDO", posizione_chunk=1)
    skipped_plane = categorizza(
        _fs(_arco(0, 1, "", "asindeto_sequenziale")),
        [pp, sfondo],
    )
    assert skipped_plane.archi == []

    far = _evento("e2", 2, piano="PRIMO_PIANO", posizione_chunk=3)
    skipped_gap = categorizza(
        _fs(_arco(0, 2, "", "asindeto_sequenziale")),
        [pp, far],
    )
    assert skipped_gap.archi == []


def test_completiva_emits_contenuto_not_causa_or_sequenza():
    dire = _evento("dire", 0)
    partire = _evento("partire", 1, completiva_di=0)
    result = categorizza(_fs(), [dire, partire])
    assert [(arco.tipo, arco.da_id, arco.a_id) for arco in result.archi] == [
        ("CONTENUTO", "dire", "partire")
    ]
    assert partire.contenuto_di == "dire"
    assert all(arco.tipo not in {"CAUSA", "SEQUENZA"} for arco in result.archi)


def test_contenuto_dedup_and_props():
    dire = _evento("dire", 0)
    partire = _evento("partire", 1, completiva_di=0)
    result = categorizza(_fs(), [dire, partire, partire])
    contenuti = [arco for arco in result.archi if arco.tipo == "CONTENUTO"]
    assert len(contenuti) == 1
    assert contenuti[0].props["regola"] == REGOLA
    assert contenuti[0].props["versione_regole"] == RULESET_VERSION


def test_posteriorita_precede_connettivo_no_causa_implies_precede():
    earlier = _evento("arrivare", 0)
    later = _evento("partire", 1)
    result = categorizza(
        _fs(
            _arco(0, 1, "poi", "posteriorita"),
            _arco(0, 1, "perché", "causa_esplicita", "subordinata_principale"),
        ),
        [earlier, later],
    )
    tipi = [arco.tipo for arco in result.archi]
    assert tipi.count("PRECEDE") == 1
    assert tipi.count("CAUSA") == 1
    precede = next(arco for arco in result.archi if arco.tipo == "PRECEDE")
    assert precede.da_id == "arrivare"
    assert precede.a_id == "partire"
    assert precede.props["base"] == "connettivo"
    assert precede.props["regola"] == REGOLA


def test_anteriorita_reverses_so_earlier_precedes_later():
    later = _evento("partire", 0)
    earlier = _evento("arrivare", 1)
    result = categorizza(
        _fs(_arco(0, 1, "prima", "anteriorita")),
        [later, earlier],
    )
    assert [(arco.tipo, arco.da_id, arco.a_id) for arco in result.archi] == [
        ("PRECEDE", "arrivare", "partire")
    ]
    assert result.archi[0].props["base"] == "connettivo"


def test_causa_cycle_second_not_written():
    a = _evento("a", 0)
    b = _evento("b", 1)
    testo = "A perché B perché A"
    result = categorizza(
        _fs(
            _arco(0, 1, "perché", "causa_esplicita", "coordinata"),
            _arco(1, 0, "perché", "causa_esplicita", "coordinata"),
        ),
        [a, b],
        testo_chunk=testo,
    )
    assert [(arco.tipo, arco.da_id, arco.a_id) for arco in result.archi] == [
        ("CAUSA", "a", "b")
    ]
    assert len(result.quarantena) == 1
    item = result.quarantena[0]
    assert item.motivo == "ciclo CAUSA"
    assert item.versione_regole == RULESET_VERSION
    assert item.id == quarantena_id("doc-m5", testo, "perché", "ciclo CAUSA")


def test_nested_to_unrelated_toplevel_not_written():
    parent = _evento("dire", 0)
    nested = _evento("partire", 1, completiva_di=0)
    sibling = _evento("restare", 2)
    result = categorizza(
        _fs(_arco(1, 2, "perché", "causa_esplicita", "subordinata_principale")),
        [parent, nested, sibling],
    )
    assert not any(
        arco.tipo == "CAUSA" and {arco.da_id, arco.a_id} == {"partire", "restare"}
        for arco in result.archi
    )
    assert any(
        arco.tipo == "CONTENUTO" and arco.da_id == "dire" and arco.a_id == "partire"
        for arco in result.archi
    )


def test_parent_child_and_same_nest_siblings_allowed():
    parent = _evento("dire", 0)
    child_a = _evento("partire", 1, completiva_di=0)
    child_b = _evento("restare", 2, completiva_di=0)
    result = categorizza(
        _fs(
            _arco(0, 1, "che", "nessuno"),
            _arco(1, 2, "e", "nessuno"),
        ),
        [parent, child_a, child_b],
    )
    pairs = {(arco.tipo, arco.da_id, arco.a_id) for arco in result.archi}
    assert ("COLLEGATO", "dire", "partire") in pairs
    assert ("COLLEGATO", "partire", "restare") in pairs
    assert ("CONTENUTO", "dire", "partire") in pairs
    assert ("CONTENUTO", "dire", "restare") in pairs


def test_unresolved_grezzo_index_skipped():
    only = _evento("solo", 0)
    result = categorizza(
        _fs(_arco(0, 9, "perché", "causa_esplicita", "subordinata_principale")),
        [only],
    )
    assert result.archi == []
    assert result.quarantena == []


def test_self_loop_discarded():
    same = _evento("loop", 0)
    result = categorizza(
        _fs(_arco(0, 0, "perché", "causa_esplicita", "coordinata")),
        [same],
    )
    assert result.archi == []


def test_principale_subordinata_swaps_grezzo_da_a():
    main = _evento("partire", 0, segmentazione="principale_finita")
    sub = _evento("piovere", 1, segmentazione="subordinata_finita")
    result = categorizza(
        _fs(_arco(1, 0, "cosicché", "causa_esplicita", "principale_subordinata")),
        [main, sub],
    )
    assert [(arco.tipo, arco.da_id, arco.a_id) for arco in result.archi] == [
        ("CAUSA", "partire", "piovere")
    ]


def _import_modules(tree: ast.AST) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
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


def test_event_edges_isolation_ast():
    tree = ast.parse(EDGES_PATH.read_text(encoding="utf-8"), filename=str(EDGES_PATH))
    violations = [
        f"{EDGES_PATH}: {module}"
        for module in _import_modules(tree)
        if _is_forbidden_import(module)
    ]
    assert violations == []
    source = EDGES_PATH.read_text(encoding="utf-8")
    assert "mention_coref" not in source
    assert "wellformed" not in source
    assert "backbone" not in source
    assert "app.core" not in source
