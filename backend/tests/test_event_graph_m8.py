"""M8 wellformed tests (no Docker, no live LLM)."""

from __future__ import annotations

import ast
from pathlib import Path

from app.models.event_graph import ArgomentoRisolto, ArcoEvento, EventoRisolto
from app.pipeline.event_graph import RULESET_VERSION
from app.pipeline.event_graph.ids import quarantena_id
from app.pipeline.event_graph.wellformed import valida

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "event_graph"
WELLFORMED_PATH = PACKAGE_DIR / "wellformed.py"


def _sogg(menzione_id: str) -> list[ArgomentoRisolto]:
    return [ArgomentoRisolto(ruolo="SOGG", menzione_id=menzione_id)]


def _evento(
    id: str,
    *,
    piano: str = "PRIMO_PIANO",
    ancora: str | None = "0:4",
    argomenti: list[ArgomentoRisolto] | None = None,
    sogg_speciale: str = "nessuno",
    menzione_id: str | None = None,
    posizione_chunk: int = 0,
    posizione_doc: int | None = None,
    chunk_id: str | None = "chunk-a",
    documento: str | None = "doc-m8",
    lemma: str | None = None,
) -> EventoRisolto:
    if argomenti is None:
        if menzione_id is not None:
            argomenti = _sogg(menzione_id)
        elif sogg_speciale == "nessuno":
            argomenti = _sogg(f"m-{id}")
        else:
            argomenti = []
    return EventoRisolto(
        id=id,
        lemma=lemma or id,
        piano=piano,  # type: ignore[arg-type]
        ancora=ancora,
        argomenti=argomenti,
        sogg_speciale=sogg_speciale,  # type: ignore[arg-type]
        posizione_chunk=posizione_chunk,
        posizione_doc=posizione_doc if posizione_doc is not None else posizione_chunk,
        chunk_id=chunk_id,
        documento=documento,
        span=lemma or id,
    )


def _pp(id: str, **kwargs) -> EventoRisolto:
    return _evento(id, piano="PRIMO_PIANO", **kwargs)


def _sf(id: str, **kwargs) -> EventoRisolto:
    return _evento(id, piano="SFONDO", **kwargs)


def _fuori(id: str, **kwargs) -> EventoRisolto:
    return _evento(id, piano="FUORI_LINEA", **kwargs)


def _sequenza(da_id: str, a_id: str) -> ArcoEvento:
    return ArcoEvento(tipo="SEQUENZA", da_id=da_id, a_id=a_id)


def _causa(da_id: str, a_id: str) -> ArcoEvento:
    return ArcoEvento(tipo="CAUSA", da_id=da_id, a_id=a_id)


def _ids(eventi: list[EventoRisolto]) -> list[str]:
    return [event.id for event in eventi]


def test_two_pp_sequenza_kept_no_quarantena():
    eventi = [
        _pp("pp-a", menzione_id="m-a", ancora="0:5", posizione_chunk=0),
        _pp("pp-b", menzione_id="m-b", ancora="6:11", posizione_chunk=1),
    ]
    archi = [_sequenza("pp-a", "pp-b")]
    q = valida(eventi, archi)

    assert q == []
    assert _ids(eventi) == ["pp-a", "pp-b"]
    assert [(arco.tipo, arco.da_id, arco.a_id) for arco in archi] == [
        ("SEQUENZA", "pp-a", "pp-b")
    ]


def test_missing_ancora_dropped():
    bare = _pp("pp-bare", menzione_id="m-bare", ancora="", posizione_chunk=0)
    none = _pp("pp-none", menzione_id="m-none", ancora=None, posizione_chunk=1)
    ok_a = _pp("pp-a", menzione_id="m-a", ancora="0:4", posizione_chunk=2)
    ok_b = _pp("pp-b", menzione_id="m-b", ancora="5:9", posizione_chunk=3)
    eventi = [bare, none, ok_a, ok_b]
    archi = [_sequenza("pp-a", "pp-b")]
    testo = "un evento senza ancora"
    q = valida(eventi, archi, testo_chunk=testo)

    assert _ids(eventi) == ["pp-a", "pp-b"]
    assert [(arco.tipo, arco.da_id, arco.a_id) for arco in archi] == [
        ("SEQUENZA", "pp-a", "pp-b")
    ]
    assert {item.motivo for item in q} == {"ancora assente"}
    assert all(item.versione_regole == RULESET_VERSION for item in q)
    dropped_bare = next(item for item in q if item.frammento == "pp-bare")
    assert dropped_bare.id == quarantena_id("doc-m8", testo, "pp-bare", "ancora assente")
    assert dropped_bare.ancora_doc == "doc-m8"
    assert dropped_bare.ancora_chunk == "chunk-a"
    assert dropped_bare.ancora_span == ""


def test_sogg_assente_vs_ignoto():
    missing = _pp(
        "no-sogg",
        ancora="0:4",
        argomenti=[],
        sogg_speciale="nessuno",
        posizione_chunk=0,
    )
    missing_list = [missing]
    q_missing = valida(missing_list, [])
    assert missing_list == []
    assert [item.motivo for item in q_missing] == ["sogg assente"]

    ignoto = _pp(
        "ignoto",
        ancora="0:4",
        argomenti=[],
        sogg_speciale="IGNOTO",
        posizione_chunk=0,
    )
    companion = _pp(
        "comp",
        menzione_id="m-comp",
        ancora="5:9",
        posizione_chunk=1,
    )
    eventi = [ignoto, companion]
    archi = [_sequenza("ignoto", "comp")]
    q_ok = valida(eventi, archi)
    assert q_ok == []
    assert _ids(eventi) == ["ignoto", "comp"]
    assert archi[0].tipo == "SEQUENZA"


def test_punto5_last_orphan_collegato_not_quarantena():
    previous = _pp("prev", menzione_id="m-prev", ancora="0:4", posizione_chunk=0)
    orphan = _pp("last", menzione_id="m-last", ancora="5:9", posizione_chunk=1)
    eventi = [previous, orphan]
    archi: list[ArcoEvento] = []
    q = valida(eventi, archi)

    assert q == []
    assert _ids(eventi) == ["prev", "last"]
    assert len(archi) == 1
    arco = archi[0]
    assert arco.tipo == "COLLEGATO"
    assert arco.da_id == "prev"
    assert arco.a_id == "last"
    assert arco.props["segnale"] == "ordine_menzione"
    assert arco.props["regola"] == "wellformed.punto5"


def test_isolated_non_last_dropped():
    isolated = _pp("iso", menzione_id="m-iso", ancora="0:3", posizione_chunk=0)
    a = _pp("pp-a", menzione_id="m-a", ancora="4:8", posizione_chunk=1)
    b = _pp("pp-b", menzione_id="m-b", ancora="9:13", posizione_chunk=2)
    eventi = [isolated, a, b]
    archi = [_sequenza("pp-a", "pp-b")]
    q = valida(eventi, archi)

    assert _ids(eventi) == ["pp-a", "pp-b"]
    assert [(arco.tipo, arco.da_id, arco.a_id) for arco in archi] == [
        ("SEQUENZA", "pp-a", "pp-b")
    ]
    assert any(item.motivo == "isolamento" for item in q)
    assert {item.frammento for item in q if item.motivo == "isolamento"} == {"iso"}


def test_sfondo_unattached_vs_sfondo_only_chunk():
    pp_a = _pp("pp-a", menzione_id="m-a", ancora="0:4", posizione_chunk=0)
    pp_b = _pp("pp-b", menzione_id="m-b", ancora="5:9", posizione_chunk=1)
    sf_loose = _sf("sf-loose", menzione_id="m-sf", ancora="10:14", posizione_chunk=2)
    mixed = [pp_a, pp_b, sf_loose]
    mixed_archi = [_sequenza("pp-a", "pp-b")]
    q_mixed = valida(mixed, mixed_archi)
    assert "sf-loose" not in _ids(mixed)
    assert _ids(mixed) == ["pp-a", "pp-b"]
    assert any(item.frammento == "sf-loose" for item in q_mixed)

    only_a = _sf(
        "sf-only-a",
        menzione_id="m-share",
        ancora="0:4",
        posizione_chunk=0,
        chunk_id="chunk-sf",
    )
    only_b = _sf(
        "sf-only-b",
        menzione_id="m-share",
        ancora="5:9",
        posizione_chunk=1,
        chunk_id="chunk-sf",
    )
    only = [only_a, only_b]
    only_archi: list[ArcoEvento] = []
    q_only = valida(only, only_archi)
    assert _ids(only) == ["sf-only-a", "sf-only-b"]
    assert q_only == []


def test_sequenza_fuori_linea_removed():
    pp_a = _pp("pp-a", menzione_id="m-share", ancora="0:4", posizione_chunk=0)
    fl = _fuori("fl-1", menzione_id="m-share", ancora="5:9", posizione_chunk=1)
    pp_b = _pp("pp-b", menzione_id="m-b", ancora="10:14", posizione_chunk=2)
    eventi = [pp_a, fl, pp_b]
    archi = [_sequenza("pp-a", "pp-b"), _sequenza("pp-a", "fl-1")]
    q = valida(eventi, archi)

    pairs = [(arco.tipo, arco.da_id, arco.a_id) for arco in archi]
    assert ("SEQUENZA", "pp-a", "fl-1") not in pairs
    assert ("SEQUENZA", "pp-a", "pp-b") in pairs
    assert all(
        not (arco.tipo == "SEQUENZA" and "fl-1" in (arco.da_id, arco.a_id))
        for arco in archi
    )
    assert any(item.motivo == "FUORI_LINEA in spina" for item in q)
    assert "fl-1" in _ids(eventi)
    assert "pp-a" in _ids(eventi)
    assert "pp-b" in _ids(eventi)


def test_causa_cycle_dropped():
    a = _pp("ev-a", menzione_id="m-a", ancora="0:4", posizione_chunk=0)
    b = _pp("ev-b", menzione_id="m-b", ancora="5:9", posizione_chunk=1)
    eventi = [a, b]
    archi = [_causa("ev-a", "ev-b"), _causa("ev-b", "ev-a")]
    q = valida(eventi, archi)

    causa = [arco for arco in archi if arco.tipo == "CAUSA"]
    pairs = {(arco.da_id, arco.a_id) for arco in causa}
    assert ("ev-a", "ev-b") not in pairs or ("ev-b", "ev-a") not in pairs
    assert not (
        ("ev-a", "ev-b") in pairs and ("ev-b", "ev-a") in pairs
    )
    assert any(item.motivo == "ciclo CAUSA" for item in q)
    assert _ids(eventi) == ["ev-a", "ev-b"]


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
    if "event_coref" in lowered:
        return True
    if "persistence" in lowered:
        return True
    return False


def test_wellformed_isolation_ast():
    source = WELLFORMED_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(WELLFORMED_PATH))
    violations = [
        f"{WELLFORMED_PATH}: {module}"
        for module in _import_modules(tree)
        if _is_forbidden_import(module)
    ]
    assert violations == []
    assert "event_coref" not in source
    assert "persistence" not in source
    assert "app.core" not in source
    assert "from app.pipeline.event_graph.ids import quarantena_id" in source
