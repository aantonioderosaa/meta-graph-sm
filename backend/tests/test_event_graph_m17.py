"""M17: EventQuerySpec compiler + structured query HTTP (no LLM, no Docker)."""

from __future__ import annotations

import ast
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport

from app.models.event_graph import EventQuerySpec
from app.pipeline.event_graph.query_structured import (
    QueryResult,
    compile_cypher,
    elenca_query,
    esegui,
    get_query,
    registra_query,
    reset_query_history,
)

BACKEND = Path(__file__).resolve().parents[1]
QUERY_PATH = BACKEND / "app" / "pipeline" / "event_graph" / "query_structured.py"
API_PATH = BACKEND / "app" / "api" / "event_graph.py"


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

            def single(self_inner):
                return self.rows[0] if self.rows else None

        return R()


class _SessionCtx:
    def __init__(self, session: FakeSession):
        self.session = session

    async def __aenter__(self) -> FakeSession:
        return self.session

    async def __aexit__(self, *args) -> None:
        return None


class FakeDriver:
    def __init__(self, session: FakeSession):
        self._session = session

    def session(self) -> _SessionCtx:
        return _SessionCtx(self._session)


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


@pytest.fixture(autouse=True)
def _clean_history():
    reset_query_history()
    yield
    reset_query_history()


def test_compile_cypher_lemma_piano_uses_params_not_concatenated_strings():
    injected = "arrivare'; DROP TABLE eventi; --"
    spec = EventQuerySpec(lemma=injected, piano="PRIMO_PIANO")
    cypher, params = compile_cypher(spec)
    assert "$lemma" in cypher
    assert "$piano" in cypher
    assert "e.lemma = $lemma" in cypher
    assert "e.piano = $piano" in cypher
    assert injected not in cypher
    assert "DROP TABLE" not in cypher
    assert "PRIMO_PIANO" not in cypher
    assert params["lemma"] == injected
    assert params["piano"] == "PRIMO_PIANO"


def test_tipo_relazione_invalid_raises():
    spec = EventQuerySpec.model_construct(tipo_relazione="HACK")
    with pytest.raises(ValueError):
        compile_cypher(spec)


def test_tipo_relazione_causa_is_allowlisted_not_interpolated():
    spec = EventQuerySpec(tipo_relazione="CAUSA")
    cypher, params = compile_cypher(spec)
    assert ":CAUSA" in cypher
    assert "MATCH (e)-[r:CAUSA]->()" in cypher
    assert "CAUSA" not in params.values()
    assert spec.tipo_relazione not in list(params)


def test_traversal_catena_di_rel_types():
    spec = EventQuerySpec(traversal="catena_di", traversal_target="ev-target")
    cypher, params = compile_cypher(spec)
    assert "catena_id" in cypher
    assert "STESSO_EVENTO" not in cypher
    assert "AGGIORNA" not in cypher
    assert "CONTRADDICE" not in cypher
    assert "*1..8" not in cypher
    assert "$target" in cypher
    assert params["target"] == "ev-target"
    assert "ev-target" not in cypher
    assert "posizione_doc" in cypher


def test_traversal_spina_dorsale_di_sequenza_only():
    spec = EventQuerySpec(traversal="spina_dorsale_di", traversal_target="ev-spine")
    cypher, params = compile_cypher(spec)
    assert "SEQUENZA" in cypher
    assert "PRECEDE" in cypher
    assert "COLLEGATO" not in cypher
    assert "*0..12" in cypher
    assert "STESSO_EVENTO" not in cypher
    assert params["target"] == "ev-spine"
    assert "ev-spine" not in cypher


def test_traversal_prima_di_precede_not_superseded():
    spec = EventQuerySpec(traversal="prima_di", traversal_target="ev-t")
    cypher, params = compile_cypher(spec)
    assert "PRECEDE" in cypher
    assert "superato_da" in cypher
    assert "$target" in cypher
    assert params["target"] == "ev-t"
    assert "ev-t" not in cypher


@pytest.mark.asyncio
async def test_esegui_maps_fakesession_rows():
    session = FakeSession(
        rows=[
            {
                "id": "ev-1",
                "lemma": "arrivare",
                "piano": "PRIMO_PIANO",
                "fattualita": "FATTUALE",
                "tempo": "passato",
                "documento": "doc-1",
                "tempo_assoluto": "2020-01",
                "archi": [{"tipo": "CAUSA", "da_id": "ev-1", "a_id": "ev-2"}],
            }
        ]
    )
    spec = EventQuerySpec(lemma="arrivare")
    result = await esegui(session, spec)
    assert result.spec is spec
    assert result.eventi[0]["id"] == "ev-1"
    assert result.eventi[0]["lemma"] == "arrivare"
    assert result.archi[0]["tipo"] == "CAUSA"
    assert session.runs
    query, params = session.runs[0]
    assert "$lemma" in query
    assert params["lemma"] == "arrivare"
    assert "arrivare" not in query


def test_history_registra_elenca_get():
    spec = EventQuerySpec(lemma="restare")
    risultato = QueryResult(eventi=[{"id": "a"}, {"id": "b"}], archi=[], spec=spec)
    stored = registra_query(spec, risultato, modo="structured")
    assert stored.modo == "structured"
    assert stored.n_eventi == 2
    assert stored.spec is spec
    assert stored.id
    assert stored.ts
    listed = elenca_query()
    assert len(listed) == 1
    assert listed[0].id == stored.id
    assert get_query(stored.id) is stored
    assert get_query("missing") is None
    reset_query_history()
    assert elenca_query() == []
    assert get_query(stored.id) is None


@pytest.mark.asyncio
async def test_post_query_structured_200_with_stubbed_esegui(monkeypatch):
    async def stub_esegui(session, spec):
        return QueryResult(
            eventi=[{"id": "ev-stub", "lemma": spec.lemma}],
            archi=[],
            spec=spec,
        )

    monkeypatch.setattr("app.api.event_graph.get_driver", lambda: FakeDriver(FakeSession()))
    monkeypatch.setattr("app.api.event_graph.esegui", stub_esegui)
    from app.main import app

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/event-graph/query/structured",
            json={"lemma": "arrivare", "piano": "PRIMO_PIANO"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["modo"] == "structured"
    assert body["id"]
    assert body["spec"]["lemma"] == "arrivare"
    assert body["risultato"]["eventi"][0]["id"] == "ev-stub"
    assert "archi" in body["risultato"]


@pytest.mark.asyncio
async def test_get_queries_lists_stored_structured_query(monkeypatch):
    async def stub_esegui(session, spec):
        return QueryResult(eventi=[{"id": "ev-1"}], archi=[], spec=spec)

    monkeypatch.setattr("app.api.event_graph.get_driver", lambda: FakeDriver(FakeSession()))
    monkeypatch.setattr("app.api.event_graph.esegui", stub_esegui)
    from app.main import app

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        posted = await client.post(
            "/event-graph/query/structured",
            json={"lemma": "piovere"},
        )
        assert posted.status_code == 200
        qid = posted.json()["id"]
        listed = await client.get("/event-graph/queries")
        detail = await client.get(f"/event-graph/queries/{qid}")
        missing = await client.get("/event-graph/queries/does-not-exist")
    assert listed.status_code == 200
    queries = listed.json()["queries"]
    assert queries
    assert queries[0]["id"] == qid
    assert queries[0]["modo"] == "structured"
    assert queries[0]["n_eventi"] == 1
    assert "risultato" not in queries[0]
    assert detail.status_code == 200
    assert detail.json()["id"] == qid
    assert detail.json()["modo"] == "structured"
    assert detail.json()["risultato"]["eventi"][0]["id"] == "ev-1"
    assert missing.status_code == 404


def test_query_structured_isolation_ast():
    source = QUERY_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(QUERY_PATH))
    violations = [
        f"{QUERY_PATH}: {module}"
        for module in _import_modules(tree)
        if _is_forbidden_import(module)
    ]
    assert violations == []
    assert "app.core" not in source
    assert "sentence_transformers" not in source
    assert "graphdatascience" not in source.lower()
    api_source = API_PATH.read_text(encoding="utf-8")
    assert '@router.post("/query/structured")' in api_source
    assert '@router.get("/queries")' in api_source
    assert '@router.get("/queries/{query_id}")' in api_source


def test_query_structured_has_no_llm_or_openai():
    source = QUERY_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(QUERY_PATH))
    imported = _import_modules(tree)
    assert all("openai" not in module.lower() for module in imported)
    assert all("call_structured" not in module for module in imported)
    assert "call_structured" not in source
    assert "openai" not in source.lower()
