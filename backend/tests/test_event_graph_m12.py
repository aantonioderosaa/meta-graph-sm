"""M12 orchestrator + BASE /event-graph router tests (no Docker, no live LLM)."""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport

from app.models.event_graph import EventoRisolto, SottoGrafo
from app.pipeline.event_graph.infra import bus as event_graph_bus
from app.pipeline.event_graph.pipeline import EspansioneZona, run_event_graph_ingestion
from app.pipeline.event_graph.zona_segmentation import Zona

BACKEND = Path(__file__).resolve().parents[1]
PIPELINE_PATH = BACKEND / "app" / "pipeline" / "event_graph" / "pipeline.py"
API_PATH = BACKEND / "app" / "api" / "event_graph.py"
MAIN_PATH = BACKEND / "app" / "main.py"


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


def _zona(doc_id: str = "doc-m12", ordinale: int = 0, **overrides) -> Zona:
    testo = "Mario arrivò ieri. Mario arrivò oggi."
    payload = {
        "id": f"z-{ordinale}",
        "documento": doc_id,
        "offset_inizio": 0,
        "offset_fine": len(testo),
        "ordinale": ordinale,
        "testo": testo,
        "riassunto": "Mario arrivò.",
        "espansa": False,
    }
    payload.update(overrides)
    return Zona(**payload)


def _install_macro(monkeypatch, zone: list[Zona], order: list[str] | None = None):
    def segmenta(text, doc_id):
        if order is not None:
            order.append("m0")
        return list(zone)

    async def riassumi(items, **kwargs):
        if order is not None:
            order.append("m1")
        return list(items)

    async def collega(items, **kwargs):
        if order is not None:
            order.append("m2")
        return []

    monkeypatch.setattr(
        "app.pipeline.event_graph.pipeline.segmenta_zone",
        segmenta,
    )
    monkeypatch.setattr(
        "app.pipeline.event_graph.pipeline.riassumi_zone",
        riassumi,
    )
    monkeypatch.setattr(
        "app.pipeline.event_graph.pipeline.collega_zone",
        collega,
    )


def _install_espandi(monkeypatch, order: list[str] | None = None, fuse: bool = False):
    called: list[str] = []

    async def stub(zona, **kwargs):
        if order is not None:
            order.append("espandi")
        called.append(zona.id)
        zona.espansa = True
        sotto_doc = kwargs.get("sotto_doc")
        local = SottoGrafo()
        first = EventoRisolto(
            id=f"e-{zona.id}-0",
            lemma="arrivare",
            documento=zona.documento,
            e_testa=True,
        )
        second = EventoRisolto(
            id=f"e-{zona.id}-1",
            lemma="arrivare",
            documento=zona.documento,
            e_testa=False,
            fuso_in=first.id if fuse else None,
        )
        local.aggiungi(eventi=[first, second])
        if fuse:
            second.catena_id = first.catena_id or first.id
            first.catena_id = second.catena_id
            second.catena_ruolo = "STESSO_EVENTO"
            second.catena_precedente_id = first.id
        if sotto_doc is not None:
            sotto_doc.aggiungi(
                eventi=list(local.eventi),
                archi=list(local.archi),
            )
        return EspansioneZona(zona=zona, sotto=local, unita=[])

    monkeypatch.setattr(
        "app.pipeline.event_graph.pipeline.espandi_zona",
        stub,
    )
    return called


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
def _reset_eg_bus():
    event_graph_bus.reset_event_bus()
    yield
    event_graph_bus.reset_event_bus()


@pytest.mark.asyncio
async def test_macro_then_espandi_zona_order(monkeypatch):
    zona = _zona()
    order: list[str] = []
    _install_macro(monkeypatch, [zona], order=order)
    called = _install_espandi(monkeypatch, order=order)
    session = FakeSession()

    outcome = await run_event_graph_ingestion(
        "doc-m12",
        "ignored",
        "job-fase-a",
        session=None,
    )

    assert order == ["m0", "m1", "m2", "espandi"]
    assert called == [zona.id]
    assert outcome.zones_expanded == 1
    assert outcome.chunks_kept == 1
    assert len(outcome.sotto.eventi) >= 1
    assert session.runs == []
    assert not any("DELETE" in query.upper() for query, _ in session.runs)


@pytest.mark.asyncio
async def test_intra_coref_fusion_or_catena_same_lemma_shared_sogg(monkeypatch):
    zona = _zona()
    _install_macro(monkeypatch, [zona])
    _install_espandi(monkeypatch, fuse=True)

    outcome = await run_event_graph_ingestion(
        "doc-m12",
        "ignored",
        "job-coref",
        session=None,
    )

    fused = [event for event in outcome.sotto.eventi if event.fuso_in]
    chained = [event for event in outcome.sotto.eventi if event.catena_ruolo]
    chain_arcs = [
        arco
        for arco in outcome.sotto.archi
        if str(arco.tipo) in {"STESSO_EVENTO", "AGGIORNA", "CONTRADDICE"}
    ]
    assert fused or chained
    assert chain_arcs == []
    if fused:
        assert fused[0].fuso_in
    lemmas = {event.lemma for event in outcome.sotto.eventi}
    assert "arrivare" in lemmas


@pytest.mark.asyncio
async def test_fase_b_persisti_then_temporal_esegui(monkeypatch):
    zona = _zona()
    _install_macro(monkeypatch, [zona])
    _install_espandi(monkeypatch)
    order: list[str] = []

    async def fake_persisti(session, sotto, *, job_id=None):
        order.append("persisti")
        return None

    async def fake_esegui(session, sotto, job_id, **kwargs):
        order.append("esegui")
        return None

    monkeypatch.setattr(
        "app.pipeline.event_graph.persistence.persisti",
        fake_persisti,
    )
    monkeypatch.setattr(
        "app.pipeline.event_graph.temporal_placement.esegui",
        fake_esegui,
    )

    session = FakeSession()
    await run_event_graph_ingestion(
        "doc-m12",
        "ignored",
        "job-fase-b",
        session=session,
    )
    assert order == ["persisti", "esegui"]


@pytest.mark.asyncio
async def test_metrics_missing_does_not_crash(monkeypatch):
    zona = _zona()
    _install_macro(monkeypatch, [zona])
    _install_espandi(monkeypatch)

    async def fake_persisti(session, sotto, *, job_id=None):
        return None

    async def fake_esegui(session, sotto, job_id, **kwargs):
        return None

    monkeypatch.setattr(
        "app.pipeline.event_graph.persistence.persisti",
        fake_persisti,
    )
    monkeypatch.setattr(
        "app.pipeline.event_graph.temporal_placement.esegui",
        fake_esegui,
    )

    calls: list[tuple] = []

    async def spy_registra_run(session, job_id, doc_id, versione_regole, **kwargs):
        calls.append((job_id, doc_id, versione_regole, kwargs))
        return {}

    monkeypatch.setattr(
        "app.pipeline.event_graph.metrics.registra_run",
        spy_registra_run,
    )

    outcome = await run_event_graph_ingestion(
        "doc-m12",
        "ignored",
        "job-metrics",
        session=FakeSession(),
    )
    assert outcome.job_id == "job-metrics"
    assert outcome.zones_expanded == 1
    assert outcome.chunks_kept == 1
    assert calls
    job_id, doc_id, versione_regole, kwargs = calls[0]
    assert job_id == "job-metrics"
    assert doc_id == "doc-m12"
    assert versione_regole
    assert kwargs.get("sotto") is outcome.sotto


@pytest.mark.asyncio
async def test_post_documents_returns_job_id_without_legacy_bus(monkeypatch):
    async def fake_run(doc_id, text, job_id, *, session=None, driver=None):
        return None

    monkeypatch.setattr(
        "app.api.event_graph.run_event_graph_ingestion",
        fake_run,
    )
    from app.main import app

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/event-graph/documents",
            json={"doc_id": "doc-api", "text": "Mario arrivò ieri."},
        )
    assert response.status_code == 200
    body = response.json()
    assert "job_id" in body
    assert body["job_id"]

    api_source = API_PATH.read_text(encoding="utf-8")
    tree = ast.parse(api_source, filename=str(API_PATH))
    imported = _import_modules(tree)
    assert all(
        module != "app.core.event_bus" and not module.startswith("app.core.event_bus.")
        for module in imported
    )
    assert "app.core.event_bus" not in api_source


@pytest.mark.asyncio
async def test_health_exists_never_404():
    from app.main import app

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/event-graph/health")
    assert response.status_code in {200, 503}
    assert response.status_code != 404


@pytest.mark.asyncio
async def test_stream_yields_sse_line_after_publish():
    from app.main import app

    job_id = "job-sse-m12"

    async def publisher():
        await asyncio.sleep(0.05)
        await event_graph_bus.publish(
            job_id,
            "estrazione",
            "chunk_ready",
            {"n": 1},
        )
        await event_graph_bus.publish(
            job_id,
            "done",
            "pipeline_complete",
            {"n": 2},
        )

    publish_task = asyncio.create_task(publisher())
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "GET",
            f"/event-graph/stream?job_id={job_id}",
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            lines: list[str] = []
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    lines.append(line[len("data: ") :])
                if len(lines) >= 1:
                    break
    await publish_task
    assert lines
    payload = json.loads(lines[0])
    assert payload["job_id"] == job_id
    assert payload["stage"] in {"estrazione", "done"}


def test_main_includes_only_event_graph_router():
    source = MAIN_PATH.read_text(encoding="utf-8")
    assert "app.include_router(documents.router)" not in source
    assert "app.include_router(dreaming.router)" not in source
    assert "app.include_router(node_graph.router)" not in source
    assert "app.include_router(event_graph_api.router)" in source
    assert "async def lifespan" in source
    assert "from app.api import event_graph as event_graph_api" in source


def test_isolation_ast_pipeline_and_api():
    violations: list[str] = []
    for path in (PIPELINE_PATH, API_PATH):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for module in _import_modules(tree):
            if _is_forbidden_import(module):
                violations.append(f"{path}: {module}")
    assert violations == []


def test_bus_used_is_package_infra_bus():
    pipeline_source = PIPELINE_PATH.read_text(encoding="utf-8")
    api_source = API_PATH.read_text(encoding="utf-8")
    assert "app.pipeline.event_graph.infra.bus" in pipeline_source
    assert "app.pipeline.event_graph.infra.bus" in api_source
    assert "app.core.event_bus" not in pipeline_source
    assert "app.core.event_bus" not in api_source
    assert "from app.pipeline.event_graph.infra.bus import publish" in pipeline_source
