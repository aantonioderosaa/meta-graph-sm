"""M4 factuality + narrative plane tests (no Docker, no live LLM)."""

from __future__ import annotations

import ast
from pathlib import Path

from app.models.event_graph import (
    ArgomentoGrezzo,
    ArgomentoRisolto,
    ChunkFactsheet,
    EventoGrezzo,
    EventoRisolto,
    RunState,
    TempoVerbale,
)
from app.pipeline.event_graph.chunking_periods import PeriodChunk
from app.pipeline.event_graph.factuality import applica
from app.pipeline.event_graph.ids import eg_chunk_id, menzione_id
from app.pipeline.event_graph.narrative_plane import (
    assegna_piano,
    satelliti,
    tempo_base,
)
from app.pipeline.event_graph.segmentation import risolvi

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "event_graph"
FACTUALITY_PATH = PACKAGE_DIR / "factuality.py"
NARRATIVE_PATH = PACKAGE_DIR / "narrative_plane.py"


def _evento(
    id: str,
    lemma: str = "arrivare",
    tempo: TempoVerbale | None = "passato",
    posizione_chunk: int = 0,
    *,
    polarita_negata: bool = False,
    modalizzato: bool = False,
    ruolo_se: str = "nessuno",
    completiva_di: int | None = None,
    classe_verbo_reggente: str = "nessuna",
    finale: bool = False,
    frase_tipo: str = "dichiarativa",
    iterativo: bool = False,
    iterativita: bool | None = None,
    fonte: str | None = None,
    fattualita=None,
    piano=None,
    chunk_id: str | None = "chunk-a",
    indice_grezzo: int | None = None,
    argomenti: list[ArgomentoRisolto] | None = None,
    posizione_doc: int | None = 0,
    span: str | None = None,
) -> EventoRisolto:
    return EventoRisolto(
        id=id,
        lemma=lemma,
        tempo=tempo,
        fattualita=fattualita,
        fonte=fonte,
        piano=piano,
        posizione_chunk=posizione_chunk,
        polarita_negata=polarita_negata,
        modalizzato=modalizzato,
        ruolo_se=ruolo_se,  # type: ignore[arg-type]
        completiva_di=completiva_di,
        classe_verbo_reggente=classe_verbo_reggente,  # type: ignore[arg-type]
        finale=finale,
        frase_tipo=frase_tipo,  # type: ignore[arg-type]
        iterativo=iterativo,
        iterativita=iterativo if iterativita is None else iterativita,
        chunk_id=chunk_id,
        indice_grezzo=indice_grezzo,
        argomenti=argomenti or [],
        posizione_doc=posizione_doc,
        span=span,
    )


def _sogg(forma: str = "Mario", tipo: str = "nome_proprio") -> ArgomentoGrezzo:
    return ArgomentoGrezzo(
        ruolo="SOGG",
        forma=forma,
        tipo_superficiale=tipo,  # type: ignore[arg-type]
        span=forma,
    )


def _grezzo(
    indice: int,
    lemma: str,
    span: str,
    argomenti: list[ArgomentoGrezzo] | None = None,
    **overrides,
) -> EventoGrezzo:
    payload = {
        "indice": indice,
        "lemma": lemma,
        "span": span,
        "tempo": "passato",
        "segmentazione": "principale_finita",
        "polarita_negata": False,
        "modalizzato": False,
        "iterativo": False,
        "ruolo_se": "nessuno",
        "completiva_di": None,
        "classe_verbo_reggente": "nessuna",
        "finale": False,
        "frase_tipo": "dichiarativa",
        "marca_dialogo": False,
        "frase_indice": 0,
        "avverbio_temporale_esplicito": False,
        "connettivo_sequenziale_esplicito": False,
        "argomenti": argomenti if argomenti is not None else [_sogg()],
        "sogg_speciale": "nessuno",
    }
    payload.update(overrides)
    return EventoGrezzo(**payload)


def _plane(eventi: list[EventoRisolto], base: TempoVerbale | None = "passato"):
    applica(eventi)
    return assegna_piano(eventi, base)


def test_default_past_narrator_is_fattuale_primo_piano():
    eventi = [_evento("e0")]
    same = applica(eventi)
    assert same is eventi
    assert eventi[0].fattualita == "FATTUALE"
    assert eventi[0].fonte == "NARRATORE"
    assegna_piano(eventi, "passato")
    assert eventi[0].piano == "PRIMO_PIANO"


def test_non_fattuale_signals_become_fuori_linea():
    cases = [
        _evento("neg", polarita_negata=True),
        _evento("mod", modalizzato=True),
        _evento("fut", tempo="futuro"),
        _evento("int", frase_tipo="interrogativa"),
        _evento("imp", frase_tipo="imperativa"),
        _evento("fin", finale=True),
    ]
    _plane(cases, "passato")
    for event in cases:
        assert event.fattualita == "NON_FATTUALE", event.id
        assert event.fonte == "NARRATORE"
        assert event.piano == "FUORI_LINEA", event.id


def test_ruolo_se_is_ipotetico_and_wins_over_non_fattuale():
    eventi = [
        _evento("se", ruolo_se="antecedente"),
        _evento("se-neg", ruolo_se="conseguente", polarita_negata=True),
    ]
    _plane(eventi, "passato")
    assert eventi[0].fattualita == "IPOTETICO"
    assert eventi[1].fattualita == "IPOTETICO"
    assert all(event.piano == "FUORI_LINEA" for event in eventi)


def test_completiva_non_fattivo_vs_fattivo_exception():
    non_fattivo = [
        _evento("regge", indice_grezzo=0),
        _evento(
            "emb-nf",
            lemma="partire",
            posizione_chunk=1,
            completiva_di=0,
            classe_verbo_reggente="non_fattivo",
            indice_grezzo=1,
        ),
    ]
    applica(non_fattivo)
    assert non_fattivo[0].fattualita == "FATTUALE"
    assert non_fattivo[1].fattualita == "NON_FATTUALE"

    fattivo = [
        _evento("regge-f", indice_grezzo=0),
        _evento(
            "emb-f",
            lemma="partire",
            posizione_chunk=1,
            completiva_di=0,
            classe_verbo_reggente="fattivo",
            indice_grezzo=1,
        ),
    ]
    applica(fattivo)
    assert fattivo[0].fattualita == "FATTUALE"
    assert fattivo[1].fattualita == "FATTUALE"

    fattivo_neg = [
        _evento("regge-n", indice_grezzo=0),
        _evento(
            "emb-fn",
            lemma="partire",
            posizione_chunk=1,
            completiva_di=0,
            classe_verbo_reggente="fattivo",
            polarita_negata=True,
            indice_grezzo=1,
        ),
    ]
    applica(fattivo_neg)
    assert fattivo_neg[1].fattualita == "NON_FATTUALE"


def test_inheritance_descends_and_never_climbs():
    parent = _evento("p", polarita_negata=True, indice_grezzo=0)
    child = _evento(
        "c",
        lemma="partire",
        posizione_chunk=1,
        completiva_di=0,
        indice_grezzo=1,
    )
    grandchild = _evento(
        "g",
        lemma="vedere",
        posizione_chunk=2,
        completiva_di=1,
        indice_grezzo=2,
    )
    eventi = [child, parent, grandchild]
    applica(eventi)
    assert parent.fattualita == "NON_FATTUALE"
    assert child.fattualita == "NON_FATTUALE"
    assert grandchild.fattualita == "NON_FATTUALE"
    assert parent.fonte == "NARRATORE"
    assert child.fonte == parent.id
    assert grandchild.fonte == child.id


def test_child_does_not_infect_parent():
    parent = _evento("p", indice_grezzo=0)
    child = _evento(
        "c",
        lemma="partire",
        posizione_chunk=1,
        completiva_di=0,
        polarita_negata=True,
        indice_grezzo=1,
    )
    applica([parent, child])
    assert parent.fattualita == "FATTUALE"
    assert child.fattualita == "NON_FATTUALE"


def test_dangling_completiva_is_skipped():
    child = _evento("c", completiva_di=99, indice_grezzo=1)
    applica([child])
    assert child.fattualita == "FATTUALE"
    assert child.fonte is None


def test_imperfetto_never_primo_piano_even_as_base():
    eventi = [_evento("impf", tempo="imperfetto")]
    _plane(eventi, "imperfetto")
    assert eventi[0].fattualita == "FATTUALE"
    assert eventi[0].piano == "SFONDO"


def test_iterativo_never_primo_piano():
    by_flag = [_evento("it", iterativo=True)]
    _plane(by_flag, "passato")
    assert by_flag[0].piano == "SFONDO"

    by_trait = [_evento("it2", iterativo=False, iterativita=True)]
    _plane(by_trait, "passato")
    assert by_trait[0].piano == "SFONDO"


def test_embedded_fattuale_same_tempo_is_sfondo():
    parent = _evento(
        "p",
        indice_grezzo=0,
        argomenti=[ArgomentoRisolto(ruolo="SOGG", menzione_id="menz-mario")],
    )
    child = _evento(
        "c",
        lemma="partire",
        posizione_chunk=1,
        completiva_di=0,
        classe_verbo_reggente="fattivo",
        indice_grezzo=1,
    )
    _plane([parent, child], "passato")
    assert child.fattualita == "FATTUALE"
    assert child.fonte == "menz-mario"
    assert parent.piano == "PRIMO_PIANO"
    assert child.piano == "SFONDO"


def test_tempo_base_inherits_previous_when_fewer_than_three():
    eventi = [
        _evento("a", tempo="presente", posizione_chunk=0),
        _evento("b", tempo="presente", posizione_chunk=1),
    ]
    applica(eventi)
    assert tempo_base(eventi, RunState(tempo_base_precedente="passato")) == "passato"
    assert tempo_base(eventi, RunState()) == "presente"
    assert tempo_base([], RunState()) is None
    assert tempo_base([], RunState(tempo_base_precedente="imperfetto")) == "imperfetto"


def test_tempo_base_moda_and_tie_break():
    mixed = [
        _evento("p1", tempo="passato", posizione_chunk=0),
        _evento("pr", tempo="presente", posizione_chunk=1),
        _evento("p2", tempo="passato", posizione_chunk=2),
        _evento("fu", tempo="futuro", polarita_negata=True, posizione_chunk=3),
    ]
    applica(mixed)
    assert tempo_base(mixed, RunState()) == "passato"

    tied = [
        _evento("a", tempo="presente", posizione_chunk=0),
        _evento("b", tempo="passato", posizione_chunk=1),
        _evento("c", tempo="imperfetto", posizione_chunk=2),
        _evento("d", tempo="futuro", posizione_chunk=3),
    ]
    # futuro is NON_FATTUALE locally, so force narrator FATTUALE for the tie
    tied[3].tempo = "futuro"
    for event in tied:
        event.fattualita = "FATTUALE"
        event.fonte = "NARRATORE"
    assert tempo_base(tied, RunState()) == "passato"

    four = [
        _evento("p1", tempo="passato", posizione_chunk=0),
        _evento("pr1", tempo="presente", posizione_chunk=1),
        _evento("p2", tempo="passato", posizione_chunk=2),
        _evento("pr2", tempo="presente", posizione_chunk=3),
    ]
    applica(four)
    assert tempo_base(four, RunState(tempo_base_precedente="imperfetto")) == "passato"


def test_base_none_fattuale_narrator_is_sfondo():
    eventi = [_evento("e0")]
    applica(eventi)
    assegna_piano(eventi, None)
    assert eventi[0].piano == "SFONDO"


def test_satelliti_left_right_and_free():
    left_anchor = [
        _evento("pp0", posizione_chunk=0),
        _evento("sf1", tempo="imperfetto", posizione_chunk=1),
        _evento("pp2", posizione_chunk=2),
        _evento("sf3", tempo="imperfetto", posizione_chunk=3),
    ]
    _plane(left_anchor, "passato")
    arcs = satelliti(left_anchor)
    assert {(arc.da_id, arc.a_id, arc.tipo) for arc in arcs} == {
        ("sf1", "pp0", "SATELLITE_DI"),
        ("sf3", "pp2", "SATELLITE_DI"),
    }
    assert all(arc.props.get("regola") == "narrative_plane.satelliti" for arc in arcs)
    assert all(arc.tipo != "SEQUENZA" for arc in arcs)

    only_right = [
        _evento("sf0", tempo="imperfetto", posizione_chunk=0),
        _evento("pp2", posizione_chunk=2),
    ]
    _plane(only_right, "passato")
    right_arcs = satelliti(only_right)
    assert [(arc.da_id, arc.a_id) for arc in right_arcs] == [("sf0", "pp2")]

    free = [
        _evento("sf0", tempo="imperfetto", posizione_chunk=0),
        _evento("sf1", tempo="imperfetto", posizione_chunk=1),
    ]
    _plane(free, "passato")
    assert satelliti(free) == []


def test_satelliti_stay_intra_chunk():
    eventi = [
        _evento("pp-a", chunk_id="A", posizione_chunk=0, posizione_doc=0),
        _evento(
            "sf-a",
            tempo="imperfetto",
            chunk_id="A",
            posizione_chunk=1,
            posizione_doc=0,
        ),
        _evento(
            "sf-b",
            tempo="imperfetto",
            chunk_id="B",
            posizione_chunk=0,
            posizione_doc=1,
        ),
    ]
    _plane(eventi, "passato")
    arcs = satelliti(eventi)
    assert [(arc.da_id, arc.a_id) for arc in arcs] == [("sf-a", "pp-a")]


def test_span_reorder_resolves_completiva_via_indice_grezzo():
    testo = "Mario credette che Anna partì."
    chunk = PeriodChunk(
        id=eg_chunk_id("doc-m4", 0, testo),
        doc_id="doc-m4",
        ordinale=0,
        testo=testo,
    )
    factsheet = ChunkFactsheet(
        eventi=[
            _grezzo(
                0,
                "partire",
                "Anna partì",
                [_sogg("Anna")],
                completiva_di=1,
                classe_verbo_reggente="non_fattivo",
            ),
            _grezzo(
                1,
                "credere",
                "Mario credette",
                [_sogg("Mario")],
                modalizzato=True,
            ),
        ],
        archi=[],
        quarantena=[],
    )
    result = risolvi(factsheet, chunk)
    assert [event.lemma for event in result.eventi] == ["credere", "partire"]
    assert result.eventi[0].indice_grezzo == 1
    assert result.eventi[1].indice_grezzo == 0
    assert result.eventi[1].completiva_di == 1
    applica(result.eventi, factsheet)
    parent, child = result.eventi
    assert parent.fattualita == "NON_FATTUALE"
    assert child.fattualita == "NON_FATTUALE"
    expected_sogg = menzione_id("Mario", "nome_proprio", chunk.doc_id, chunk.id, 0)
    assert parent.fonte == "NARRATORE"
    assert child.fonte == expected_sogg.id


def test_factsheet_span_fallback_without_indice_grezzo():
    parent = _evento("p", span="credette", polarita_negata=True)
    child = _evento(
        "c",
        lemma="partire",
        posizione_chunk=1,
        completiva_di=7,
        span="partì",
    )
    sheet = ChunkFactsheet(
        eventi=[
            _grezzo(7, "credere", "credette"),
            _grezzo(8, "partire", "partì", completiva_di=7),
        ],
        archi=[],
        quarantena=[],
    )
    applica([parent, child], sheet)
    assert child.fattualita == "NON_FATTUALE"
    assert parent.fattualita == "NON_FATTUALE"
    assert child.fonte == parent.id


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


def test_factuality_and_narrative_plane_isolation_ast():
    violations: list[str] = []
    for path in (FACTUALITY_PATH, NARRATIVE_PATH):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for module in _import_modules(tree):
            if _is_forbidden_import(module):
                violations.append(f"{path}: {module}")
    assert violations == []
    for path in (FACTUALITY_PATH, NARRATIVE_PATH):
        source = path.read_text(encoding="utf-8")
        assert "SEQUENZA" not in source or path == NARRATIVE_PATH
    narrative = NARRATIVE_PATH.read_text(encoding="utf-8")
    assert 'tipo="SEQUENZA"' not in narrative
    assert "event_edges" not in narrative
    assert "backbone" not in FACTUALITY_PATH.read_text(encoding="utf-8")
