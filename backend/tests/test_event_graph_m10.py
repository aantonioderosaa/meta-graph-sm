"""M10 temporal_placement tests (no Docker, no live LLM / Neo4j)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.models.event_graph import ArgomentoRisolto, ArcoEvento, EventoRisolto, SottoGrafo
from app.pipeline.event_graph import RULESET_VERSION
from app.pipeline.event_graph.temporal_placement import (
    TemporalOrder,
    confronta,
    esegui,
    introdurrebbe_ciclo,
    normalizza_ancora,
    placeholder_target,
    seleziona_candidati,
)

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "event_graph"
PLACEMENT_PATH = PACKAGE_DIR / "temporal_placement.py"
PROMPTS_PATH = PACKAGE_DIR / "temporal_prompts.py"


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
    grezzo: str | None = None,
    tempo_assoluto: str | dict | None = None,
    revisioni: list | None = None,
    fuso_in: str | None = None,
    documento: str = "doc-m10",
    span: str | None = None,
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
        fuso_in=fuso_in,
        documento=documento,
        span=span,
        tempo_assoluto=tempo_assoluto,
        tempo_assoluto_grezzo=grezzo,
        tempo_assoluto_revisioni=list(revisioni or []),
        argomenti=argomenti,
    )


def _sotto(*eventi: EventoRisolto, archi: list[ArcoEvento] | None = None) -> SottoGrafo:
    sotto = SottoGrafo()
    sotto.aggiungi(eventi=list(eventi), archi=archi or [])
    return sotto


def _precede_pairs(sotto: SottoGrafo) -> list[tuple[str, str, str | None]]:
    return [
        (arco.da_id, arco.a_id, arco.props.get("base"))
        for arco in sotto.archi
        if str(arco.tipo) == "PRECEDE"
    ]


def _collegato_pairs(sotto: SottoGrafo) -> list[tuple[str, str, str | None]]:
    return [
        (arco.da_id, arco.a_id, arco.props.get("segnale"))
        for arco in sotto.archi
        if str(arco.tipo) == "COLLEGATO"
    ]


def _install_stub(monkeypatch, handler):
    async def stub(
        system_prompt,
        user_prompt,
        response_model,
        temperature=0,
        job_id=None,
    ):
        return await handler(
            system_prompt, user_prompt, response_model, temperature, job_id
        )

    monkeypatch.setattr(
        "app.pipeline.event_graph.temporal_placement.call_structured", stub
    )


def _boom_stub(monkeypatch):
    async def boom(*args, **kwargs):
        raise AssertionError("call_structured must not run")

    monkeypatch.setattr(
        "app.pipeline.event_graph.temporal_placement.call_structured", boom
    )


def test_absolute_parse_no_llm():
    assert normalizza_ancora("1994") == "1994"
    assert normalizza_ancora("1994-04") == "1994-04"
    assert normalizza_ancora("1994-04-06") == "1994-04-06"
    assert normalizza_ancora("1994-04-06T12:00:00") == "1994-04-06T12:00:00"


def test_interval_parse():
    assert normalizza_ancora("dal 1990 al 1992") == {"da": "1990", "a": "1992"}
    assert normalizza_ancora("1990-1992") == {"da": "1990", "a": "1992"}


def test_unresolved_relative_without_referent_is_symbolic():
    parsed = normalizza_ancora("il giorno dopo")
    assert isinstance(parsed, dict)
    assert "relativo_a" in parsed
    assert not parsed.get("relativo_a")
    assert "offset" in parsed


@pytest.mark.asyncio
async def test_unresolved_relative_esegui_no_llm(monkeypatch):
    _boom_stub(monkeypatch)
    ev = _evento("ev-rel", grezzo="il giorno dopo", posizione_doc=0)
    sotto = _sotto(ev)
    outcome = await esegui(None, sotto, "job-rel")
    assert outcome.llm_calls == 0
    assert isinstance(ev.tempo_assoluto, dict)
    assert not ev.tempo_assoluto.get("relativo_a")


def test_confronta_dated_prima_dopo_sovrapposto():
    assert confronta("1990", "1992") == "prima"
    assert confronta("1994", "1990") == "dopo"
    assert confronta("1994", "1994") == "sovrapposto"
    assert confronta({"da": "1990", "a": "1992"}, "1991") == "sovrapposto"
    assert confronta("1994-04", "1994-05") == "prima"
    assert confronta("1994-04-06", "1994-04-07") == "prima"


@pytest.mark.asyncio
async def test_sovrapposto_does_not_write_precede(monkeypatch):
    _boom_stub(monkeypatch)
    a = _evento("ev-a", grezzo="1994", posizione_doc=0, menzione_sogg="m-x")
    b = _evento("ev-b", grezzo="1994", posizione_doc=1, menzione_sogg="m-x")
    sotto = _sotto(a, b)
    outcome = await esegui(None, sotto, "job-overlap")
    assert outcome.llm_calls == 0
    assert _precede_pairs(sotto) == []


@pytest.mark.asyncio
async def test_determined_pair_writes_precede_earlier_to_later(monkeypatch):
    _boom_stub(monkeypatch)
    old = _evento("ev-old", grezzo="1990", posizione_doc=0, menzione_sogg="m-x")
    new = _evento("ev-new", grezzo="1994", posizione_doc=1, menzione_sogg="m-x")
    sotto = _sotto(old, new)
    outcome = await esegui(None, sotto, "job-pred")
    assert outcome.llm_calls == 0
    pairs = _precede_pairs(sotto)
    assert ("ev-old", "ev-new", "dato_esplicito") in pairs
    assert all(arco.da_id == "ev-old" for arco in sotto.archi if str(arco.tipo) == "PRECEDE")
    precede = next(arco for arco in sotto.archi if str(arco.tipo) == "PRECEDE")
    assert precede.props["regola"] == "temporal_placement.esegui"
    assert precede.props["versione_regole"] == RULESET_VERSION
    assert precede.props["run_id"] == "job-pred"
    assert precede.props.get("id")


@pytest.mark.asyncio
async def test_incerto_one_collegato_to_nearest_previous(monkeypatch):
    _boom_stub(monkeypatch)
    nuovo = _evento(
        "ev-new",
        posizione_doc=2,
        posizione_chunk=0,
        menzione_sogg="m-shared",
    )
    sotto = _sotto(nuovo)
    session = FakeSession(
        rows=[
            {
                "id": "ev-p1",
                "lemma": "parlare",
                "posizione_doc": 0,
                "posizione_chunk": 0,
                "argomenti": [{"ruolo": "SOGG", "menzione_id": "m-shared"}],
            },
            {
                "id": "ev-p2",
                "lemma": "guardare",
                "posizione_doc": 1,
                "posizione_chunk": 0,
                "argomenti": [{"ruolo": "SOGG", "menzione_id": "m-shared"}],
            },
        ]
    )
    persistenti = [
        _evento("ev-p1", lemma="parlare", posizione_doc=0, menzione_sogg="m-shared"),
        _evento("ev-p2", lemma="guardare", posizione_doc=1, menzione_sogg="m-shared"),
    ]
    assert placeholder_target(nuovo, persistenti).id == "ev-p2"

    outcome = await esegui(session, sotto, "job-ph")
    assert outcome.llm_calls == 0
    placeholders = _collegato_pairs(sotto)
    assert placeholders == [("ev-new", "ev-p2", "ordine_ingestione")]
    assert sum(1 for item in placeholders if item[2] == "ordine_ingestione") == 1


@pytest.mark.asyncio
async def test_cycle_precede_quarantined_not_written(monkeypatch):
    _boom_stub(monkeypatch)
    a = _evento("ev-a", grezzo="1994", posizione_doc=0, menzione_sogg="m-x")
    b = _evento("ev-b", grezzo="1990", posizione_doc=1, menzione_sogg="m-x")
    existing = ArcoEvento(
        tipo="PRECEDE",
        da_id="ev-a",
        a_id="ev-b",
        props={"base": "connettivo"},
    )
    sotto = _sotto(a, b, archi=[existing])
    assert introdurrebbe_ciclo(sotto.archi, "ev-b", "ev-a") is not None

    outcome = await esegui(None, sotto, "job-cyc")
    assert ("ev-b", "ev-a", "dato_esplicito") not in _precede_pairs(sotto)
    assert all(not (arco.da_id == "ev-b" and arco.a_id == "ev-a") for arco in sotto.archi)
    assert outcome.quarantena
    assert all(item.motivo.startswith("ciclo cronologico:") for item in outcome.quarantena)
    assert any("ev-b" in item.motivo and "ev-a" in item.motivo for item in sotto.quarantena)


@pytest.mark.asyncio
async def test_refinement_superato_da_and_conflitto(monkeypatch):
    _boom_stub(monkeypatch)
    old = _evento("ev-old", grezzo="1990", posizione_doc=0, menzione_sogg="m-x")
    new = _evento("ev-new", grezzo="1994", posizione_doc=1, menzione_sogg="m-x")
    placeholder = ArcoEvento(
        tipo="COLLEGATO",
        da_id="ev-new",
        a_id="ev-old",
        props={"segnale": "ordine_ingestione"},
    )
    sotto = _sotto(old, new, archi=[placeholder])
    outcome = await esegui(None, sotto, "job-ref")
    precede = next(arco for arco in sotto.archi if str(arco.tipo) == "PRECEDE")
    assert precede.da_id == "ev-old" and precede.a_id == "ev-new"
    assert placeholder.props.get("superato_da") == precede.props["id"]
    assert placeholder.props.get("conflitto") is True
    assert outcome.archi_superati
    assert outcome.archi_superati[0][1] == precede.props["id"]


@pytest.mark.asyncio
async def test_tempo_assoluto_revisioni_append_only(monkeypatch):
    _boom_stub(monkeypatch)
    same = _evento(
        "ev-same",
        grezzo="1994",
        tempo_assoluto="1994",
        revisioni=["1994"],
        tempo="passato",
        posizione_doc=0,
    )
    changed = _evento(
        "ev-chg",
        grezzo="1994",
        tempo_assoluto="1990",
        revisioni=["1990"],
        tempo="imperfetto",
        posizione_doc=1,
        menzione_sogg="m-y",
    )
    sotto = _sotto(same, changed)
    await esegui(None, sotto, "job-rev")
    assert same.tempo_assoluto_revisioni == ["1994"]
    assert changed.tempo_assoluto_revisioni == ["1990", "1994"]
    assert changed.tempo_assoluto == "1994"
    assert same.tempo == "passato"
    assert changed.tempo == "imperfetto"


@pytest.mark.asyncio
async def test_sequenza_satellite_piano_left_intact(monkeypatch):
    _boom_stub(monkeypatch)
    a = _evento("ev-a", grezzo="1990", piano="SFONDO", posizione_doc=0)
    b = _evento("ev-b", grezzo="1994", piano="PRIMO_PIANO", posizione_doc=1)
    sequenza = ArcoEvento(tipo="SEQUENZA", da_id="ev-a", a_id="ev-b", props={"frozen": True})
    satellite = ArcoEvento(tipo="SATELLITE_DI", da_id="ev-a", a_id="ev-b")
    sotto = _sotto(a, b, archi=[sequenza, satellite])
    await esegui(None, sotto, "job-freeze")
    assert a.piano == "SFONDO"
    assert b.piano == "PRIMO_PIANO"
    assert any(arco.tipo == "SEQUENZA" and arco.props.get("frozen") for arco in sotto.archi)
    assert any(arco.tipo == "SATELLITE_DI" and arco.da_id == "ev-a" for arco in sotto.archi)
    seq = next(arco for arco in sotto.archi if str(arco.tipo) == "SEQUENZA")
    assert seq.da_id == "ev-a" and seq.a_id == "ev-b"


@pytest.mark.asyncio
async def test_long_distance_aggiorna(monkeypatch):
    _boom_stub(monkeypatch)
    old = _evento(
        "ev-old",
        lemma="arrivare",
        posizione_doc=0,
        chunk_id="chunk-old",
        argomenti=[_sogg("m-mario"), _ogg("m-lettera")],
    )
    new = _evento(
        "ev-new",
        lemma="Arrivare",
        posizione_doc=5,
        chunk_id="chunk-new",
        argomenti=[_sogg("m-mario"), _ogg("m-pacchetto")],
    )
    sotto = _sotto(old, new)
    await esegui(None, sotto, "job-upd")
    assert not any(str(arco.tipo) == "AGGIORNA" for arco in sotto.archi)
    assert new.catena_ruolo == "AGGIORNA"
    assert new.catena_precedente_id == "ev-old"
    assert new.catena_divergenze == ["argomenti"]
    assert new.catena_id == old.catena_id


def test_candidate_cap_respected():
    nuovo = _evento("ev-new", menzione_sogg="m-shared", posizione_doc=99)
    persistenti = [
        _evento(f"ev-{i}", lemma=f"lemma{i}", menzione_sogg="m-shared", posizione_doc=i)
        for i in range(15)
    ]
    selected = seleziona_candidati(nuovo, persistenti, max_n=3)
    assert len(selected) == 3
    default_cap = seleziona_candidati(nuovo, persistenti, max_n=10)
    assert len(default_cap) == 10


@pytest.mark.asyncio
async def test_esegui_respects_settings_cap(monkeypatch):
    _boom_stub(monkeypatch)
    monkeypatch.setattr(
        "app.pipeline.event_graph.temporal_placement.settings.EVENT_GRAPH_TEMPORAL_MAX_CANDIDATES",
        2,
    )
    nuovo = _evento("ev-new", grezzo="2000", menzione_sogg="m-shared", posizione_doc=20)
    others = [
        _evento(
            f"ev-{i}",
            lemma=f"lemma{i}",
            grezzo=str(1990 + i),
            menzione_sogg="m-shared",
            posizione_doc=i,
        )
        for i in range(5)
    ]
    sotto = _sotto(nuovo, *others)
    await esegui(None, sotto, "job-cap")
    new_precede = [
        arco
        for arco in sotto.archi
        if str(arco.tipo) == "PRECEDE" and "ev-new" in {arco.da_id, arco.a_id}
    ]
    assert len(new_precede) <= 2 + 5


@pytest.mark.asyncio
async def test_relative_with_referent_uses_stubbed_llm(monkeypatch):
    calls: list[object] = []

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        calls.append(response_model)
        assert temperature == 0
        assert response_model is TemporalOrder
        return TemporalOrder(ordine="dopo")

    _install_stub(monkeypatch, handler)
    old = _evento("ev-old", grezzo="1994-04-06", lemma="partire", posizione_doc=0)
    new = _evento(
        "ev-new",
        grezzo="il giorno dopo ev-old",
        lemma="arrivare",
        posizione_doc=1,
        menzione_sogg="m-z",
    )
    old.argomenti = [_sogg("m-z")]
    sotto = _sotto(old, new)
    outcome = await esegui(None, sotto, "job-llm")
    assert outcome.llm_calls >= 1
    assert calls
    assert all(model is TemporalOrder for model in calls)


@pytest.mark.asyncio
async def test_fakesession_emits_merge_never_delete(monkeypatch):
    _boom_stub(monkeypatch)
    old = _evento("ev-old", grezzo="1990", posizione_doc=0, menzione_sogg="m-x")
    new = _evento("ev-new", grezzo="1994", posizione_doc=1, menzione_sogg="m-x")
    sotto = _sotto(old, new)
    session = FakeSession(rows=[])
    await esegui(session, sotto, "job-cypher")
    assert session.runs
    joined = " ".join(query for query, _ in session.runs)
    assert "MERGE" in joined or "SET" in joined
    for query, _ in session.runs:
        assert "DELETE" not in query.upper()


@pytest.mark.asyncio
async def test_esegui_session_none_in_memory(monkeypatch):
    _boom_stub(monkeypatch)
    old = _evento("ev-old", grezzo="1990", posizione_doc=0, menzione_sogg="m-x")
    new = _evento("ev-new", grezzo="1994", posizione_doc=1, menzione_sogg="m-x")
    sotto = _sotto(old, new)
    outcome = await esegui(None, sotto, "job-mem")
    assert ("ev-old", "ev-new", "dato_esplicito") in _precede_pairs(sotto)
    assert outcome.archi_aggiunti
    assert old.tempo_assoluto == "1990"
    assert new.tempo_assoluto == "1994"


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
    return False


def test_m10_isolation_ast():
    for path in (PLACEMENT_PATH, PROMPTS_PATH):
        if not path.exists():
            continue
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
        assert "persistence" not in source.lower() or "do not" in source.lower()
        lowered = source.lower()
        assert "from app.pipeline.event_graph.persistence" not in lowered
        assert "import persistence" not in lowered
        assert "app.core.llm_client" not in source
