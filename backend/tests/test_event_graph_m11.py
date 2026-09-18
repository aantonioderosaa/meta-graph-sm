"""M11 persistence tests (no Docker, no live Neo4j)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.models.event_graph import (
    ArgomentoRisolto,
    ArcoEvento,
    EventoRisolto,
    MenzioneRisolta,
    QuarantenaItem,
    SottoGrafo,
)
from app.pipeline.event_graph import RULESET_VERSION
from app.pipeline.event_graph.persistence import (
    REGOLA,
    archi_ammissibili,
    persisti,
)

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "event_graph"
PERSIST_PATH = PACKAGE_DIR / "persistence.py"


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


def _sogg(menzione_id: str | None) -> ArgomentoRisolto:
    return ArgomentoRisolto(ruolo="SOGG", menzione_id=menzione_id)


def _menzione(
    mid: str,
    *,
    forma: str = "Mario",
    documento: str = "doc-a",
    chunk_id: str = "chunk-a",
) -> MenzioneRisolta:
    return MenzioneRisolta(
        id=mid,
        forma=forma,
        forma_canonica=forma,
        numero="sing",
        genere="masc",
        tipo_superficiale="nome_proprio",
        non_risolto=False,
        documento=documento,
        chunk_id=chunk_id,
    )


def _evento(
    event_id: str,
    *,
    lemma: str = "arrivare",
    documento: str = "doc-a",
    chunk_id: str = "chunk-a",
    piano: str = "PRIMO_PIANO",
    tempo: str | None = "passato",
    polarita: str | None = "affermata",
    sogg_speciale: str = "nessuno",
    menzione_id: str | None = "m-mario",
    argomenti: list[ArgomentoRisolto] | None = None,
    fuso_in: str | None = None,
    posizione_doc: int = 0,
    posizione_chunk: int = 0,
    regola: str | None = None,
) -> EventoRisolto:
    if argomenti is None:
        if sogg_speciale != "nessuno":
            argomenti = [_sogg(None)]
        elif menzione_id is not None:
            argomenti = [_sogg(menzione_id)]
        else:
            argomenti = []
    return EventoRisolto(
        id=event_id,
        lemma=lemma,
        tempo=tempo,  # type: ignore[arg-type]
        polarita=polarita,
        piano=piano,  # type: ignore[arg-type]
        documento=documento,
        chunk_id=chunk_id,
        indice_chunk=posizione_chunk,
        posizione_doc=posizione_doc,
        posizione_chunk=posizione_chunk,
        sogg_speciale=sogg_speciale,  # type: ignore[arg-type]
        fuso_in=fuso_in,
        argomenti=argomenti,
        regola=regola,
        fattualita="FATTUALE",
        segmentazione="principale_finita",
    )


def _sotto(
    *eventi: EventoRisolto,
    menzioni: list[MenzioneRisolta] | None = None,
    archi: list[ArcoEvento] | None = None,
    quarantena: list[QuarantenaItem] | None = None,
) -> SottoGrafo:
    graph = SottoGrafo()
    graph.aggiungi(
        eventi=list(eventi),
        menzioni=menzioni or [],
        archi=archi or [],
        quarantena=quarantena or [],
    )
    return graph


def _queries(session: FakeSession) -> list[str]:
    return [query for query, _ in session.runs]


def _blob(session: FakeSession) -> str:
    return "\n".join(_queries(session))


def _assert_no_delete(session: FakeSession) -> None:
    for query, _ in session.runs:
        upper = query.upper()
        assert "DELETE" not in upper
        assert "DETACH" not in upper
        assert "REMOVE " not in upper


def _merge_shapes(session: FakeSession) -> list[tuple[str, dict[str, object]]]:
    keys = ("id", "rel_id", "da_id", "a_id", "e_id", "m_id")
    shapes: list[tuple[str, dict[str, object]]] = []
    for query, params in session.runs:
        identity = {key: params[key] for key in keys if key in params}
        shapes.append((query, identity))
    return shapes


def _base_graph() -> SottoGrafo:
    mention = _menzione("m-mario")
    event = _evento("ev-1", menzione_id="m-mario")
    quarantena = QuarantenaItem(
        id="q-1",
        frammento="…",
        motivo="ancora assente",
        ancora_doc="doc-a",
        ancora_chunk="chunk-a",
        ancora_span="0:4",
    )
    return _sotto(
        event,
        menzioni=[mention],
        archi=[ArcoEvento(tipo="SEQUENZA", da_id="ev-1", a_id="ev-1")],
        quarantena=[quarantena],
    )


@pytest.mark.asyncio
async def test_merge_evento_menzione_documento_quarantena_no_delete():
    session = FakeSession()
    outcome = await persisti(session, _base_graph(), job_id="job-1")
    blob = _blob(session)
    assert "MERGE (e:Evento" in blob
    assert "MERGE (m:Menzione" in blob
    assert "m.riferimenti = $riferimenti" in blob
    assert "m.occorrenze = $occorrenze" in blob
    assert "MERGE (d:Documento" in blob
    assert "MERGE (q:Quarantena" in blob
    assert "MERGE" in blob
    _assert_no_delete(session)
    assert outcome.nodi >= 4
    assert all("MERGE" in query for query in _queries(session))


@pytest.mark.asyncio
async def test_argomental_sogg_written_sogg_speciale_skips_rel():
    mention = _menzione("m-mario")
    with_sogg = _evento("ev-sogg", menzione_id="m-mario")
    speciale = _evento(
        "ev-speciale",
        sogg_speciale="IGNOTO",
        menzione_id=None,
        posizione_doc=1,
    )
    session = FakeSession()
    await persisti(session, _sotto(with_sogg, speciale, menzioni=[mention]))

    sogg_runs = [(q, p) for q, p in session.runs if "[r:SOGG]" in q or "[r:SOGG" in q]
    assert sogg_runs
    assert any(p.get("e_id") == "ev-sogg" and p.get("m_id") == "m-mario" for _, p in sogg_runs)
    assert all(p.get("e_id") != "ev-speciale" for _, p in sogg_runs)
    assert all(p.get("m_id") != "ev-speciale" for _, p in sogg_runs)
    mention_merges = [
        p
        for q, p in session.runs
        if "MERGE (m:Menzione" in q
    ]
    assert [p.get("id") for p in mention_merges] == ["m-mario"]


@pytest.mark.asyncio
async def test_persisti_twice_same_merge_shapes():
    sotto = _base_graph()
    first = FakeSession()
    second = FakeSession()
    out1 = await persisti(first, sotto, job_id="job-1")
    out2 = await persisti(second, sotto, job_id="job-1")
    assert _merge_shapes(first) == _merge_shapes(second)
    assert [q for q, _ in first.runs] == [q for q, _ in second.runs]
    assert out1.nodi == out2.nodi
    assert out1.archi == out2.archi
    assert out1.skipped_cross_doc == out2.skipped_cross_doc
    _assert_no_delete(first)
    _assert_no_delete(second)


@pytest.mark.asyncio
async def test_cross_doc_sequenza_skipped_precede_collegato_written():
    ev_a = _evento("ev-a", documento="doc-a", menzione_id="m-a")
    ev_b = _evento(
        "ev-b",
        documento="doc-b",
        chunk_id="chunk-b",
        menzione_id="m-b",
        posizione_doc=1,
    )
    archi = [
        ArcoEvento(tipo="SEQUENZA", da_id="ev-a", a_id="ev-b"),
        ArcoEvento(tipo="SATELLITE_DI", da_id="ev-b", a_id="ev-a"),
        ArcoEvento(tipo="STESSO_EVENTO", da_id="ev-a", a_id="ev-b"),
        ArcoEvento(tipo="AGGIORNA", da_id="ev-a", a_id="ev-b"),
        ArcoEvento(tipo="CONTRADDICE", da_id="ev-a", a_id="ev-b"),
        ArcoEvento(tipo="CAUSA", da_id="ev-a", a_id="ev-b"),
        ArcoEvento(
            tipo="PRECEDE",
            da_id="ev-a",
            a_id="ev-b",
            props={"base": "dato_esplicito"},
        ),
        ArcoEvento(
            tipo="COLLEGATO",
            da_id="ev-a",
            a_id="ev-b",
            props={"segnale": "ordine_ingestione"},
        ),
    ]
    sotto = _sotto(
        ev_a,
        ev_b,
        menzioni=[_menzione("m-a"), _menzione("m-b", documento="doc-b", chunk_id="chunk-b")],
        archi=archi,
    )
    session = FakeSession()
    outcome = await persisti(session, sotto, job_id="job-x")
    blob = _blob(session)
    assert "SEQUENZA" not in blob
    assert "SATELLITE_DI" not in blob
    assert "STESSO_EVENTO" not in blob
    assert "AGGIORNA" not in blob
    assert "CONTRADDICE" not in blob
    assert "[r:CAUSA" not in blob
    assert "[r:PRECEDE" in blob
    assert "[r:COLLEGATO" in blob
    assert outcome.skipped_cross_doc == 3


@pytest.mark.asyncio
async def test_fuso_in_set_on_loser_still_merged():
    winner = _evento("ev-win", menzione_id="m-mario")
    loser = _evento(
        "ev-lose",
        menzione_id="m-mario",
        fuso_in="ev-win",
        posizione_doc=1,
    )
    session = FakeSession()
    await persisti(session, _sotto(winner, loser, menzioni=[_menzione("m-mario")]))
    evento_runs = [(q, p) for q, p in session.runs if "MERGE (e:Evento" in q]
    ids = {p["id"] for _, p in evento_runs}
    assert ids == {"ev-win", "ev-lose"}
    loser_params = next(p for _, p in evento_runs if p["id"] == "ev-lose")
    assert loser_params["fuso_in"] == "ev-win"
    assert any("e.fuso_in = $fuso_in" in q for q, _ in evento_runs)
    _assert_no_delete(session)


@pytest.mark.asyncio
async def test_superato_da_set_on_collegato():
    ev_a = _evento("ev-a")
    ev_b = _evento("ev-b", posizione_doc=1)
    arco = ArcoEvento(
        tipo="COLLEGATO",
        da_id="ev-a",
        a_id="ev-b",
        props={"segnale": "ordine_ingestione", "superato_da": "rel-precede-1"},
    )
    session = FakeSession()
    await persisti(session, _sotto(ev_a, ev_b, menzioni=[_menzione("m-mario")], archi=[arco]))
    collegato = [(q, p) for q, p in session.runs if "COLLEGATO" in q]
    assert collegato
    query, params = collegato[0]
    assert "SET" in query
    assert "r.superato_da = $superato_da" in query
    assert params["superato_da"] == "rel-precede-1"
    _assert_no_delete(session)


@pytest.mark.asyncio
async def test_evento_provenance_regola_versione():
    session = FakeSession()
    await persisti(session, _base_graph())
    evento_runs = [(q, p) for q, p in session.runs if "MERGE (e:Evento" in q]
    assert evento_runs
    query, params = evento_runs[0]
    assert "e.versione_regole = $versione_regole" in query
    assert "e.regola = $regola" in query
    assert params["versione_regole"] == RULESET_VERSION
    assert params["regola"] == REGOLA


@pytest.mark.asyncio
async def test_morphological_tempo_written_not_removed():
    session = FakeSession()
    await persisti(session, _base_graph())
    evento_runs = [(q, p) for q, p in session.runs if "MERGE (e:Evento" in q]
    assert evento_runs
    query, params = evento_runs[0]
    assert "e.tempo = $tempo" in query
    assert params["tempo"] == "passato"
    for q, _ in session.runs:
        upper = q.upper()
        assert "REMOVE" not in upper
        assert "DELETE" not in upper
        assert "e.tempo = null" not in q.lower()


def test_archi_ammissibili_without_session():
    ev_a = _evento("ev-a", documento="doc-a")
    ev_b = _evento("ev-b", documento="doc-b", posizione_doc=1)
    ev_c = _evento("ev-c", documento="doc-a", posizione_doc=2)
    ev_none = _evento("ev-none", documento=None, posizione_doc=3)
    sotto = _sotto(
        ev_a,
        ev_b,
        ev_c,
        ev_none,
        archi=[
            ArcoEvento(tipo="SEQUENZA", da_id="ev-a", a_id="ev-b"),
            ArcoEvento(tipo="SEQUENZA", da_id="ev-a", a_id="ev-c"),
            ArcoEvento(tipo="CAUSA", da_id="ev-a", a_id="ev-b"),
            ArcoEvento(tipo="PRECEDE", da_id="ev-a", a_id="ev-b"),
            ArcoEvento(tipo="COLLEGATO", da_id="ev-a", a_id="ev-b"),
            ArcoEvento(tipo="STESSO_EVENTO", da_id="ev-a", a_id="ev-b"),
            ArcoEvento(tipo="SATELLITE_DI", da_id="ev-b", a_id="ev-a"),
            ArcoEvento(tipo="CONTENUTO", da_id="ev-a", a_id="ev-none"),
        ],
    )
    allowed = {(arco.tipo, arco.da_id, arco.a_id) for arco in archi_ammissibili(sotto)}
    assert ("SEQUENZA", "ev-a", "ev-c") in allowed
    assert ("PRECEDE", "ev-a", "ev-b") in allowed
    assert ("COLLEGATO", "ev-a", "ev-b") in allowed
    assert ("CONTENUTO", "ev-a", "ev-none") in allowed
    assert ("SEQUENZA", "ev-a", "ev-b") not in allowed
    assert ("CAUSA", "ev-a", "ev-b") not in allowed
    assert ("STESSO_EVENTO", "ev-a", "ev-b") not in allowed
    assert ("SATELLITE_DI", "ev-b", "ev-a") not in allowed


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
    if module == "app.api" or module.startswith("app.api."):
        return True
    if module == "app.pipeline.event_graph.pipeline" or module.startswith(
        "app.pipeline.event_graph.pipeline."
    ):
        return True
    lowered = module.lower()
    if "sentence_transformers" in lowered or "sentence-transformers" in lowered:
        return True
    return False


def test_m11_isolation_ast():
    source = PERSIST_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(PERSIST_PATH))
    violations = [
        f"{PERSIST_PATH}: {module}"
        for module in _import_modules(tree)
        if _is_forbidden_import(module)
    ]
    assert violations == []
    assert "app.core" not in source
    assert "sentence_transformers" not in source
    assert "sentence-transformers" not in source
    assert "from app.api" not in source
    assert "import api" not in source
    assert "from app.pipeline.event_graph.pipeline" not in source
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "MERGE" not in node.value.upper():
                continue
            upper = node.value.upper()
            assert "DELETE" not in upper
            assert "DETACH" not in upper
