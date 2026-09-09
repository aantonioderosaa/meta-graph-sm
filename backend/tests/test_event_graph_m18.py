"""M18: NL → EventQuerySpec compiler + POST /query/nl (no live LLM, no Docker)."""

from __future__ import annotations

import ast
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport
from pydantic import ValidationError

from app.models.event_graph import EventQuerySpec
from app.pipeline.event_graph.infra.llm import LLMValidationError
from app.pipeline.event_graph.query_nl import compila, esegui_nl
from app.pipeline.event_graph.query_structured import (
    QueryResult,
    get_query,
    reset_query_history,
)

BACKEND = Path(__file__).resolve().parents[1]
PACKAGE = BACKEND / "app" / "pipeline" / "event_graph"
QUERY_NL_PATH = PACKAGE / "query_nl.py"
PROMPTS_PATH = PACKAGE / "query_nl_prompts.py"
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


@pytest.mark.asyncio
async def test_compila_stubs_llm_and_does_not_call_esegui(monkeypatch):
    async def stub_llm(system, user, response_model, temperature=0, job_id=None):
        assert response_model is EventQuerySpec
        assert temperature == 0
        assert "chi è arrivato" in user
        return EventQuerySpec(lemma="arrivare", piano="PRIMO_PIANO")

    async def boom_esegui(*args, **kwargs):
        raise AssertionError("query_structured.esegui must not run during compila")

    monkeypatch.setattr(
        "app.pipeline.event_graph.query_nl.esegui", boom_esegui
    )
    spec = await compila("chi è arrivato?", call_structured=stub_llm)
    assert isinstance(spec, EventQuerySpec)
    assert spec.lemma == "arrivare"
    assert spec.piano == "PRIMO_PIANO"


@pytest.mark.asyncio
async def test_esegui_nl_compila_then_structured_esegui(monkeypatch):
    order: list[str] = []

    async def spy_compila(testo, *, call_structured=None, job_id=None):
        order.append("compila")
        assert testo == "eventi di causa"
        return EventQuerySpec(tipo_relazione="CAUSA")

    async def spy_esegui(session, spec):
        order.append("esegui")
        assert spec.tipo_relazione == "CAUSA"
        return QueryResult(
            eventi=[{"id": "ev-1", "lemma": "piovere"}],
            archi=[{"tipo": "CAUSA"}],
            spec=spec,
        )

    monkeypatch.setattr("app.pipeline.event_graph.query_nl.compila", spy_compila)
    monkeypatch.setattr("app.pipeline.event_graph.query_nl.esegui", spy_esegui)
    outcome = await esegui_nl(FakeSession(), "eventi di causa")
    assert order == ["compila", "esegui"]
    assert outcome.spec.tipo_relazione == "CAUSA"
    assert outcome.risultato.eventi[0]["id"] == "ev-1"
    assert outcome.stored_id


@pytest.mark.asyncio
async def test_post_query_nl_returns_spec_generata_and_risultato(monkeypatch):
    spec = EventQuerySpec(lemma="arrivare", piano="PRIMO_PIANO")

    async def stub_compila(testo, *, call_structured=None, job_id=None):
        assert testo == "quando è arrivato Mario?"
        return spec

    async def stub_esegui(session, compiled):
        return QueryResult(
            eventi=[{"id": "ev-nl", "lemma": compiled.lemma}],
            archi=[],
            spec=compiled,
        )

    monkeypatch.setattr("app.pipeline.event_graph.query_nl.compila", stub_compila)
    monkeypatch.setattr("app.pipeline.event_graph.query_nl.esegui", stub_esegui)
    monkeypatch.setattr(
        "app.api.event_graph.get_driver", lambda: FakeDriver(FakeSession())
    )
    from app.main import app

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/event-graph/query/nl",
            json={"testo": "quando è arrivato Mario?"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["modo"] == "nl"
    assert body["id"]
    assert body["spec_generata"]["lemma"] == "arrivare"
    assert body["spec_generata"]["piano"] == "PRIMO_PIANO"
    assert body["risultato"]["eventi"][0]["id"] == "ev-nl"
    assert "archi" in body["risultato"]


@pytest.mark.asyncio
async def test_history_get_query_has_modo_nl(monkeypatch):
    async def stub_compila(testo, *, call_structured=None, job_id=None):
        return EventQuerySpec(lemma="restare")

    async def stub_esegui(session, spec):
        return QueryResult(eventi=[{"id": "a"}], archi=[], spec=spec)

    monkeypatch.setattr("app.pipeline.event_graph.query_nl.compila", stub_compila)
    monkeypatch.setattr("app.pipeline.event_graph.query_nl.esegui", stub_esegui)
    outcome = await esegui_nl(FakeSession(), "chi è rimasto?")
    stored = get_query(outcome.stored_id)
    assert stored is not None
    assert stored.modo == "nl"
    assert stored.spec.lemma == "restare"
    assert stored.n_eventi == 1


def test_query_nl_isolation_ast():
    source = QUERY_NL_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(QUERY_NL_PATH))
    imported = _import_modules(tree)
    violations = [
        f"{QUERY_NL_PATH}: {module}"
        for module in imported
        if _is_forbidden_import(module)
    ]
    assert violations == []
    assert "app.core" not in source
    assert any(
        module == "app.pipeline.event_graph.infra.llm"
        or module.startswith("app.pipeline.event_graph.infra.llm.")
        for module in imported
    )
    assert "call_structured" in imported or any(
        module.endswith("call_structured") for module in imported
    )
    assert "sentence_transformers" not in source
    assert "graphdatascience" not in source.lower()
    assert not any("gds" in module.lower() for module in imported)
    api_source = API_PATH.read_text(encoding="utf-8")
    assert '@router.post("/query/nl")' in api_source
    if PROMPTS_PATH.exists():
        prompts = PROMPTS_PATH.read_text(encoding="utf-8")
        prompts_tree = ast.parse(prompts, filename=str(PROMPTS_PATH))
        prompt_imports = _import_modules(prompts_tree)
        assert not any(_is_forbidden_import(m) for m in prompt_imports)
        assert "sentence_transformers" not in prompts
        assert not any("gds" in module.lower() for module in prompt_imports)


def test_query_nl_has_no_sentence_transformers_or_gds():
    source = QUERY_NL_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(QUERY_NL_PATH))
    imported = _import_modules(tree)
    assert "sentence_transformers" not in source
    assert "SentenceTransformer" not in source
    assert "graphdatascience" not in source.lower()
    assert not any("gds" in module.lower() for module in imported)


@pytest.mark.asyncio
async def test_invalid_llm_payload_surfaces():
    async def stub_bad(system, user, response_model, temperature=0, job_id=None):
        return {"piano": "NOT_A_PIANO", "tipo_relazione": "HACK"}

    with pytest.raises((ValidationError, LLMValidationError, ValueError)):
        await compila("filtro invalido", call_structured=stub_bad)
