"""M-macro1 zone summary tests (no Docker, no live LLM)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.models.event_graph import ZonaSummary
from app.pipeline.event_graph import RULESET_VERSION
from app.pipeline.event_graph.zona_segmentation import Zona
from app.pipeline.event_graph.zona_summary import (
    SYSTEM_ZONA_SUMMARY,
    applica_summary,
    riassumi_zona,
    riassumi_zone,
    user_zona_summary,
)

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "event_graph"
SUMMARY_PATH = PACKAGE_DIR / "zona_summary.py"
MODELS_PATH = Path(__file__).resolve().parents[1] / "app" / "models" / "event_graph.py"


class FakeSession:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.runs = []

    async def run(self, query, parameters=None, **params):
        if parameters is not None and isinstance(parameters, dict):
            merged = {**parameters, **params}
        else:
            merged = dict(params)
        self.runs.append((query, merged))

        class R:
            def single(self_inner):
                return self.rows[0] if self.rows else None

            def data(self_inner):
                return self.rows

        return R()


def _zona(
    testo: str = "Mario arrivò alle tre.",
    ordinale: int = 0,
    **overrides,
) -> Zona:
    payload = {
        "id": f"z-{ordinale}",
        "documento": "doc-macro1",
        "offset_inizio": 0,
        "offset_fine": len(testo),
        "ordinale": ordinale,
        "testo": testo,
    }
    payload.update(overrides)
    return Zona(**payload)


def _summary(**overrides) -> ZonaSummary:
    payload = {
        "riassunto": "Mario arrivò alle tre. Non successe altro nella zona.",
        "entita_principali": ["Mario"],
        "ancore_temporali": ["alle tre"],
        "evento_centrale": "arrivò",
    }
    payload.update(overrides)
    return ZonaSummary(**payload)


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
        "app.pipeline.event_graph.zona_summary.call_structured", stub
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
    return False


@pytest.mark.asyncio
async def test_one_llm_call_per_zona(monkeypatch):
    calls: list[object] = []

    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        calls.append(response_model)
        assert temperature == 0
        assert response_model is ZonaSummary
        assert "Mario arrivò" in user_prompt or "Anna partì" in user_prompt
        if "Anna" in user_prompt:
            return _summary(
                riassunto="Anna partì ieri.",
                entita_principali=["Anna"],
                ancore_temporali=["ieri"],
                evento_centrale="partì",
            )
        return _summary()

    _install_stub(monkeypatch, handler)
    zone = [
        _zona("Mario arrivò alle tre.", 0),
        _zona("Anna partì ieri.", 1),
    ]
    result = await riassumi_zone(zone)
    assert len(calls) == 2
    assert len(result) == 2
    assert result[0].riassunto
    assert result[1].riassunto


@pytest.mark.asyncio
async def test_fields_copied_onto_zona(monkeypatch):
    async def handler(system_prompt, user_prompt, response_model, temperature, job_id):
        return _summary()

    _install_stub(monkeypatch, handler)
    zona = _zona()
    updated = await riassumi_zona(zona)
    assert updated is not zona
    assert updated.riassunto == "Mario arrivò alle tre. Non successe altro nella zona."
    assert updated.entita_principali == ["Mario"]
    assert updated.ancore_temporali == ["alle tre"]
    assert updated.evento_centrale == "arrivò"
    assert updated.avviso is None
    assert zona.riassunto == ""
    assert zona.entita_principali == []


@pytest.mark.asyncio
async def test_cache_hit_skips_llm(monkeypatch):
    calls: list[object] = []

    async def handler(*args, **kwargs):
        calls.append(args)
        return _summary()

    _install_stub(monkeypatch, handler)
    session = FakeSession(
        rows=[
            {
                "z.riassunto": "Riassunto già in grafo.",
                "z.entita_principali": ["Mario"],
                "z.ancore_temporali": ["alle tre"],
                "z.evento_centrale": "arrivò",
                "z.versione_regole": RULESET_VERSION,
            }
        ]
    )
    updated = await riassumi_zona(_zona(), session=session)
    assert calls == []
    assert updated.riassunto == "Riassunto già in grafo."
    assert updated.entita_principali == ["Mario"]
    assert updated.ancore_temporali == ["alle tre"]
    assert updated.evento_centrale == "arrivò"
    assert not any("MERGE" in query for query, _ in session.runs)


@pytest.mark.asyncio
async def test_in_memory_cache_skips_llm_and_session(monkeypatch):
    calls: list[object] = []

    async def handler(*args, **kwargs):
        calls.append(args)
        return _summary()

    _install_stub(monkeypatch, handler)
    session = FakeSession()
    zona = _zona(
        riassunto="Già calcolato in memoria.",
        entita_principali=["Mario"],
        ancore_temporali=["alle tre"],
        evento_centrale="arrivò",
        versione_regole=RULESET_VERSION,
    )
    updated = await riassumi_zona(zona, session=session)
    assert calls == []
    assert updated is zona
    assert session.runs == []


@pytest.mark.asyncio
async def test_stale_cache_version_calls_llm(monkeypatch):
    calls: list[object] = []

    async def handler(*args, **kwargs):
        calls.append(args)
        return _summary()

    _install_stub(monkeypatch, handler)
    session = FakeSession(
        rows=[
            {
                "z.riassunto": "Vecchio riassunto.",
                "z.entita_principali": ["Mario"],
                "z.ancore_temporali": [],
                "z.evento_centrale": "arrivò",
                "z.versione_regole": "0.9.0",
            }
        ]
    )
    updated = await riassumi_zona(_zona(), session=session)
    assert calls
    assert updated.riassunto.startswith("Mario arrivò")
    merge_runs = [(query, params) for query, params in session.runs if "MERGE" in query]
    assert merge_runs
    _, params = merge_runs[0]
    assert params["id"] == "z-0"
    assert params["documento"] == "doc-macro1"
    assert params["versione_regole"] == RULESET_VERSION
    assert params["riassunto"] == updated.riassunto


@pytest.mark.asyncio
async def test_llm_exception_leaves_fields_empty(monkeypatch):
    async def handler(*args, **kwargs):
        raise RuntimeError("llm down")

    _install_stub(monkeypatch, handler)
    zona = _zona()
    session = FakeSession()
    updated = await riassumi_zona(zona, session=session)
    assert updated.riassunto == ""
    assert updated.entita_principali == []
    assert updated.ancore_temporali == []
    assert updated.evento_centrale is None
    assert updated.avviso == "estrazione fallita"
    assert not any("MERGE" in query for query, _ in session.runs)


@pytest.mark.asyncio
async def test_empty_zone_text_skips_llm(monkeypatch):
    calls: list[object] = []

    async def handler(*args, **kwargs):
        calls.append(args)
        return _summary()

    _install_stub(monkeypatch, handler)
    zona = _zona(testo="   \n")
    updated = await riassumi_zona(zona)
    assert calls == []
    assert updated is zona
    assert updated.riassunto == ""


def test_applica_summary_copies_fields():
    zona = _zona()
    summary = _summary(evento_centrale=None, ancore_temporali=[])
    updated = applica_summary(zona, summary)
    assert updated.riassunto == summary.riassunto
    assert updated.entita_principali == ["Mario"]
    assert updated.ancore_temporali == []
    assert updated.evento_centrale is None
    assert zona.riassunto == ""


def test_prompts_are_bilingual_structured_only():
    blob = SYSTEM_ZONA_SUMMARY + user_zona_summary(_zona())
    assert "riassunto" in blob
    assert "entita_principali" in blob
    assert "ancore_temporali" in blob
    assert "evento_centrale" in blob
    assert "invent" in blob.lower() or "inventare" in blob.lower()
    assert "non inventare" in blob.lower()
    assert "tools" in blob.lower() or "strumenti" in blob.lower()
    assert "Mario arrivò alle tre." in blob
    assert "zona" in blob.lower()
    lowered = blob.lower()
    assert "italian" in lowered or "italiano" in lowered or "frasi" in lowered
    assert "english" in lowered or "summarize" in lowered


def test_zona_summary_isolation_ast():
    source = SUMMARY_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(SUMMARY_PATH))
    modules = _import_modules(tree)
    violations = [module for module in modules if _is_forbidden_import(module)]
    assert violations == []
    assert any(
        module == "app.pipeline.event_graph.infra.llm"
        or module.startswith("app.pipeline.event_graph.infra.llm.")
        for module in modules
    )
    assert all("app.core" not in module for module in modules)
    assert "openai" not in modules

    models_src = MODELS_PATH.read_text(encoding="utf-8")
    models_tree = ast.parse(models_src, filename=str(MODELS_PATH))
    model_modules = _import_modules(models_tree)
    assert all(not _is_forbidden_import(module) for module in model_modules)
    assert "class ZonaSummary" in models_src
