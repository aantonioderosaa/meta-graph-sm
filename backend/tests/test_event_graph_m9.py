"""M9 event_coref + chains tests (no Docker, no live LLM)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.models.event_graph import ArgomentoRisolto, ArcoEvento, EventoRisolto, SottoGrafo
from app.pipeline.event_graph import chains as eg_chains
from app.pipeline.event_graph.chains import CHAIN_TIPI, applica, applica_persistente
from app.pipeline.event_graph.event_coref import (
    EsitoCoref,
    candidati,
    classifica,
    persistente_candidati,
)

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "event_graph"
COREF_PATH = PACKAGE_DIR / "event_coref.py"
CHAINS_PATH = PACKAGE_DIR / "chains.py"
CHAIN_KINDS = frozenset({"Fusione", "Successione", "Catena"})


class FakeSession:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.runs = []

    async def run(self, query, parameters=None, **params):
        merged = dict(params)
        if parameters is not None and isinstance(parameters, dict):
            merged = {**parameters, **merged}
        self.runs.append((query, merged))

        class R:
            def data(self_inner):
                return self.rows

        return R()


def _sogg(menzione_id: str) -> ArgomentoRisolto:
    return ArgomentoRisolto(ruolo="SOGG", menzione_id=menzione_id)


def _ogg(menzione_id: str) -> ArgomentoRisolto:
    return ArgomentoRisolto(ruolo="OGG", menzione_id=menzione_id)


def _obl(menzione_id: str, preposizione: str | None = None) -> ArgomentoRisolto:
    return ArgomentoRisolto(ruolo="OBL", menzione_id=menzione_id, preposizione=preposizione)


def _evento(
    event_id: str,
    *,
    lemma: str = "arrivare",
    chunk_id: str = "chunk-a",
    posizione_doc: int = 0,
    posizione_chunk: int = 0,
    menzione_sogg: str = "m-mario",
    argomenti: list[ArgomentoRisolto] | None = None,
    piano: str = "PRIMO_PIANO",
    tempo: str | None = "passato",
    polarita: str | None = "affermata",
    polarita_negata: bool = False,
    fattualita: str | None = "FATTUALE",
    avverbio: bool = False,
    connettivo: bool = False,
    fuso_in: str | None = None,
    documento: str = "doc-m9",
) -> EventoRisolto:
    if argomenti is None:
        argomenti = [_sogg(menzione_sogg)]
    return EventoRisolto(
        id=event_id,
        lemma=lemma,
        chunk_id=chunk_id,
        posizione_doc=posizione_doc,
        posizione_chunk=posizione_chunk,
        piano=piano,  # type: ignore[arg-type]
        tempo=tempo,  # type: ignore[arg-type]
        polarita=polarita,
        polarita_negata=polarita_negata,
        fattualita=fattualita,  # type: ignore[arg-type]
        avverbio_temporale_esplicito=avverbio,
        connettivo_sequenziale_esplicito=connettivo,
        fuso_in=fuso_in,
        documento=documento,
        argomenti=argomenti,
    )


def _sotto(*eventi: EventoRisolto, archi: list[ArcoEvento] | None = None) -> SottoGrafo:
    sotto = SottoGrafo()
    sotto.aggiungi(eventi=list(eventi), archi=archi or [])
    return sotto


def _chain_pairs(sotto: SottoGrafo) -> list[tuple[str, str, str]]:
    return [
        (str(arco.tipo), arco.da_id, arco.a_id)
        for arco in sotto.archi
        if str(arco.tipo) in CHAIN_TIPI
    ]


def test_fusione_same_chunk_shared_sogg_no_separator():
    old = _evento("ev-old", posizione_chunk=0)
    new = _evento("ev-new", posizione_chunk=1)
    other = _evento("ev-other", lemma="parlare", menzione_sogg="m-luigi", posizione_chunk=2)
    causa = ArcoEvento(tipo="CAUSA", da_id="ev-new", a_id="ev-other")
    loop = ArcoEvento(tipo="CONTRASTO", da_id="ev-new", a_id="ev-old")
    inbound = ArcoEvento(tipo="COLLEGATO", da_id="ev-other", a_id="ev-new")
    sotto = _sotto(old, new, other, archi=[causa, loop, inbound])

    found = candidati(new, sotto.eventi)
    assert [event.id for event in found] == ["ev-old"]
    assert [event.id for event in sotto.candidati(new)] == ["ev-old"]

    esito = classifica(new, found, sotto=sotto)
    assert esito is not None
    assert esito.kind == "Fusione"
    assert esito.candidato_id == "ev-old"
    assert esito.catena_tipo is None

    applica(sotto, new, esito)

    assert new.fuso_in == "ev-old"
    assert old.fuso_in is None
    assert {event.id for event in sotto.eventi} == {"ev-old", "ev-new", "ev-other"}
    assert any(event.id == "ev-new" for event in sotto.eventi)
    pairs = {(arco.tipo, arco.da_id, arco.a_id) for arco in sotto.archi}
    assert ("CAUSA", "ev-old", "ev-other") in pairs
    assert ("COLLEGATO", "ev-other", "ev-old") in pairs
    assert ("CONTRASTO", "ev-new", "ev-old") not in pairs
    assert all(arco.da_id != arco.a_id for arco in sotto.archi)
    assert _chain_pairs(sotto) == []


def test_avverbio_temporale_not_fusione():
    old = _evento("ev-old", posizione_chunk=0)
    new = _evento("ev-new", posizione_chunk=1, avverbio=True)
    sotto = _sotto(old, new)
    esito = classifica(new, candidati(new, sotto.eventi), sotto=sotto)
    assert esito is not None
    assert esito.kind != "Fusione"
    applica(sotto, new, esito)
    assert new.fuso_in is None
    assert esito.kind == "Catena"


def test_tempo_change_not_fusione():
    old = _evento("ev-old", posizione_chunk=0, tempo="passato")
    new = _evento("ev-new", posizione_chunk=1, tempo="presente")
    sotto = _sotto(old, new)
    esito = classifica(new, candidati(new, sotto.eventi), sotto=sotto)
    assert esito is not None
    assert esito.kind != "Fusione"


def test_connettivo_sequenziale_not_fusione():
    old = _evento("ev-old", posizione_chunk=0, connettivo=True)
    new = _evento("ev-new", posizione_chunk=1)
    sotto = _sotto(old, new)
    esito = classifica(new, candidati(new, sotto.eventi), sotto=sotto)
    assert esito is not None
    assert esito.kind != "Fusione"


def test_different_chunk_not_fusione():
    old = _evento("ev-old", chunk_id="chunk-a", posizione_doc=0, posizione_chunk=0)
    new = _evento("ev-new", chunk_id="chunk-b", posizione_doc=1, posizione_chunk=0)
    sotto = _sotto(old, new)
    esito = classifica(new, candidati(new, sotto.eventi), sotto=sotto)
    assert esito is not None
    assert esito.kind != "Fusione"


def test_intermediate_separator_in_same_chunk_not_fusione():
    old = _evento("ev-old", posizione_chunk=0)
    mid = _evento(
        "ev-mid",
        lemma="guardare",
        menzione_sogg="m-altro",
        posizione_chunk=1,
        avverbio=True,
    )
    new = _evento("ev-new", posizione_chunk=2)
    sotto = _sotto(old, mid, new)
    esito = classifica(
        new,
        candidati(new, sotto.eventi),
        sotto=sotto,
        intermedi=[mid],
    )
    assert esito is not None
    assert esito.kind != "Fusione"
    assert esito.kind == "Catena"


def test_successione_primo_piano_sequenza_kept():
    old = _evento("ev-old", chunk_id="chunk-a", posizione_doc=0, posizione_chunk=0)
    new = _evento("ev-new", chunk_id="chunk-b", posizione_doc=1, posizione_chunk=0)
    sequenza = ArcoEvento(tipo="SEQUENZA", da_id="ev-old", a_id="ev-new")
    sotto = _sotto(old, new, archi=[sequenza])
    esito = classifica(new, candidati(new, sotto.eventi), sotto=sotto)
    assert esito is not None
    assert esito.kind == "Successione"
    applica(sotto, new, esito)
    assert new.fuso_in is None
    assert _chain_pairs(sotto) == []
    assert any(
        arco.tipo == "SEQUENZA" and arco.da_id == "ev-old" and arco.a_id == "ev-new"
        for arco in sotto.archi
    )


def test_catena_stesso_evento():
    old = _evento("ev-old", chunk_id="chunk-a", posizione_doc=0, posizione_chunk=0)
    new = _evento("ev-new", chunk_id="chunk-b", posizione_doc=1, posizione_chunk=0)
    sotto = _sotto(old, new)
    esito = classifica(new, candidati(new, sotto.eventi), sotto=sotto)
    assert esito is not None
    assert esito.kind == "Catena"
    assert esito.catena_tipo == "STESSO_EVENTO"
    applica(sotto, new, esito)
    applica(sotto, new, esito)
    assert _chain_pairs(sotto) == []
    assert new.catena_ruolo == "STESSO_EVENTO"
    assert new.catena_precedente_id == "ev-old"
    assert new.catena_divergenze == []
    assert new.catena_id == old.catena_id
    assert new.catena_id


def test_catena_contraddice_polarita_or_fattualita():
    old = _evento("ev-old", chunk_id="chunk-a", polarita="affermata")
    new = _evento(
        "ev-new",
        chunk_id="chunk-b",
        posizione_doc=1,
        polarita="negata",
        polarita_negata=True,
    )
    sotto = _sotto(old, new)
    esito = classifica(new, candidati(new, sotto.eventi), sotto=sotto)
    assert esito is not None
    assert esito.kind == "Catena"
    assert esito.catena_tipo == "CONTRADDICE"

    old_f = _evento("ev-f-old", fattualita="FATTUALE", chunk_id="chunk-a")
    new_f = _evento(
        "ev-f-new",
        fattualita="NON_FATTUALE",
        chunk_id="chunk-b",
        posizione_doc=1,
    )
    sotto_f = _sotto(old_f, new_f)
    esito_f = classifica(new_f, candidati(new_f, sotto_f.eventi), sotto=sotto_f)
    assert esito_f is not None
    assert esito_f.catena_tipo == "CONTRADDICE"


def test_catena_aggiorna_when_args_diverge():
    old = _evento(
        "ev-old",
        chunk_id="chunk-a",
        argomenti=[_sogg("m-mario"), _ogg("m-lettera")],
    )
    new = _evento(
        "ev-new",
        chunk_id="chunk-b",
        posizione_doc=1,
        argomenti=[_sogg("m-mario"), _ogg("m-pacco"), _obl("m-roma", "a")],
    )
    sotto = _sotto(old, new)
    esito = classifica(new, candidati(new, sotto.eventi), sotto=sotto)
    assert esito is not None
    assert esito.kind == "Catena"
    assert esito.catena_tipo == "AGGIORNA"


def test_no_candidates_classifica_none_applica_noop():
    lonely = _evento("ev-lonely", lemma="partire", menzione_sogg="m-solo")
    other = _evento("ev-other", lemma="arrivare", menzione_sogg="m-mario")
    sotto = _sotto(other, lonely)
    found = candidati(lonely, sotto.eventi)
    assert found == []
    esito = classifica(lonely, found, sotto=sotto)
    assert esito is None
    archi_before = list(sotto.archi)
    applica(sotto, lonely, esito)
    assert sotto.archi == archi_before
    assert lonely.fuso_in is None


def test_classifica_with_candidate_only_three_kinds():
    cases = [
        (
            _evento("a", posizione_chunk=0),
            _evento("b", posizione_chunk=1),
            [],
        ),
        (
            _evento("a", posizione_chunk=0, avverbio=True),
            _evento("b", posizione_chunk=1),
            [],
        ),
        (
            _evento("a", chunk_id="c1"),
            _evento("b", chunk_id="c2", posizione_doc=1),
            [ArcoEvento(tipo="SEQUENZA", da_id="a", a_id="b")],
        ),
        (
            _evento("a", chunk_id="c1", polarita="affermata"),
            _evento("b", chunk_id="c2", posizione_doc=1, polarita="negata"),
            [],
        ),
    ]
    for old, new, archi in cases:
        sotto = _sotto(old, new, archi=archi)
        found = candidati(new, sotto.eventi)
        assert found
        esito = classifica(new, found, sotto=sotto)
        assert esito is not None
        assert esito.kind in CHAIN_KINDS


def test_chain_direction_old_to_new():
    old = _evento("ev-old", chunk_id="chunk-a", posizione_doc=0, posizione_chunk=0)
    new = _evento("ev-new", chunk_id="chunk-b", posizione_doc=2, posizione_chunk=0)
    sotto = _sotto(old, new)
    esito = classifica(new, candidati(new, sotto.eventi), sotto=sotto)
    assert esito is not None
    applica(sotto, new, esito)
    assert _chain_pairs(sotto) == []
    assert new.catena_precedente_id == "ev-old"
    assert old.catena_precedente_id is None
    assert new.catena_ruolo == "STESSO_EVENTO"


def test_teste_and_biforcazioni():
    a = _evento("A", posizione_chunk=0)
    b = _evento("B", lemma="arrivare", menzione_sogg="m-mario", posizione_chunk=1)
    c = _evento("C", lemma="arrivare", menzione_sogg="m-mario", posizione_chunk=2)
    d = _evento("D", lemma="guardare", menzione_sogg="m-d", posizione_chunk=3)
    e = _evento("E", lemma="guardare", menzione_sogg="m-d", posizione_chunk=4)
    fused = _evento("F", lemma="fuso", menzione_sogg="m-f", posizione_chunk=5, fuso_in="A")
    a.catena_id = "cat-arrivo"
    b.catena_id = "cat-arrivo"
    b.catena_ruolo = "STESSO_EVENTO"
    b.catena_precedente_id = "A"
    c.catena_id = "cat-arrivo"
    c.catena_ruolo = "AGGIORNA"
    c.catena_precedente_id = "A"
    d.catena_id = "cat-guarda"
    e.catena_id = "cat-guarda"
    e.catena_ruolo = "CONTRADDICE"
    e.catena_precedente_id = "D"
    sotto = _sotto(a, b, c, d, e, fused)
    heads = {event.id for event in eg_chains.teste(sotto)}
    assert heads == {"A", "D"}
    assert "C" not in heads
    assert "F" not in heads
    forks = {event.id for event in eg_chains.biforcazioni(sotto)}
    assert forks == {"A"}

    linked_a = _evento("A", posizione_chunk=0)
    linked_b = _evento("B", posizione_chunk=1)
    linked_c = _evento("C", posizione_chunk=2)
    linked_a.catena_id = "cat-lin"
    linked_b.catena_id = "cat-lin"
    linked_b.catena_ruolo = "STESSO_EVENTO"
    linked_b.catena_precedente_id = "A"
    linked_c.catena_id = "cat-lin"
    linked_c.catena_ruolo = "AGGIORNA"
    linked_c.catena_precedente_id = "B"
    linked = _sotto(linked_a, linked_b, linked_c)
    assert eg_chains.biforcazioni(linked) == []


@pytest.mark.asyncio
async def test_persistente_candidati_and_applica_persistente():
    new = _evento("ev-new", posizione_doc=3, posizione_chunk=0)
    session = FakeSession(
        rows=[
            {
                "id": "ev-old",
                "lemma": "arrivare",
                "chunk_id": "chunk-persisted",
                "posizione_doc": 0,
                "posizione_chunk": 0,
                "polarita": "affermata",
                "fattualita": "FATTUALE",
                "piano": "PRIMO_PIANO",
                "sogg_ids": ["m-mario"],
            }
        ]
    )
    found = await persistente_candidati(session, new)
    assert [event.id for event in found] == ["ev-old"]
    assert session.runs
    query, params = session.runs[0]
    assert "Fatto" in query
    assert "SOGG" in query and "OGG" in query
    assert "DELETE" not in query.upper()
    assert params["lemma"] == "arrivare"
    assert "m-mario" in params["sogg_ogg_ids"]

    fusione = EsitoCoref(kind="Fusione", nuovo_id="ev-new", candidato_id="ev-old")
    await applica_persistente(session, new, fusione)
    fusion_query = session.runs[-1][0]
    assert "fuso_in" in fusion_query
    assert "DELETE" not in fusion_query.upper()

    catena = EsitoCoref(
        kind="Catena",
        nuovo_id="ev-new",
        candidato_id="ev-old",
        catena_tipo="STESSO_EVENTO",
    )
    await applica_persistente(session, new, catena)
    chain_query, chain_params = session.runs[-1]
    assert "new.catena_id" in chain_query
    assert "new.catena_ruolo" in chain_query
    assert "[:AGGIORNA]" not in chain_query
    assert "[:STESSO_EVENTO]" not in chain_query
    assert "MERGE (old)-[" not in chain_query
    assert "DELETE" not in chain_query.upper()
    assert chain_params["old_id"] == "ev-old"
    assert chain_params["new_id"] == "ev-new"
    assert new.catena_ruolo == "STESSO_EVENTO"
    assert new.catena_precedente_id == "ev-old"

    before = len(session.runs)
    await applica_persistente(
        session,
        new,
        EsitoCoref(kind="Successione", nuovo_id="ev-new", candidato_id="ev-old"),
    )
    assert len(session.runs) == before


def test_lemma_mismatch_or_no_sogg_ogg_overlap_not_candidate():
    base = _evento("ev-new", lemma="arrivare", menzione_sogg="m-mario")
    synonym = _evento("ev-syn", lemma="giungere", menzione_sogg="m-mario")
    only_obl = _evento(
        "ev-obl",
        lemma="arrivare",
        argomenti=[_obl("m-mario", "con")],
    )
    other_sogg = _evento("ev-other", lemma="arrivare", menzione_sogg="m-luigi")
    fused = _evento("ev-fused", lemma="arrivare", menzione_sogg="m-mario", fuso_in="x")
    casefold = _evento("ev-case", lemma="Arrivare", menzione_sogg="m-mario", posizione_chunk=0)
    newer = _evento("ev-new", lemma="arrivare", menzione_sogg="m-mario", posizione_chunk=1)

    assert candidati(base, [synonym, only_obl, other_sogg, fused]) == []
    found = candidati(newer, [casefold])
    assert [event.id for event in found] == ["ev-case"]
    assert candidati(base, [base]) == []


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
    if lowered.endswith(".persistence") or lowered.endswith(".temporal_placement"):
        return True
    if "persistence" in lowered or "temporal_placement" in lowered:
        return True
    return False


def test_m9_isolation_ast():
    for path in (COREF_PATH, CHAINS_PATH):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        violations = [
            f"{path}: {module}"
            for module in _import_modules(tree)
            if _is_forbidden_import(module)
        ]
        assert violations == []
        assert "app.core" not in source
        assert "sentence_transformers" not in source
        assert "sentence-transformers" not in source
        assert "temporal_placement" not in source
        lowered = source.lower()
        assert "from app.pipeline.event_graph.persistence" not in lowered
        assert "import persistence" not in lowered
