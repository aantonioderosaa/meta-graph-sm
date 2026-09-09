"""M15 metrics.py §16 + :EventGraphRun + GET /event-graph/metrics (no Docker)."""

from __future__ import annotations

import ast
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport

from app.models.event_graph import ArcoEvento, EventoRisolto, QuarantenaItem, SottoGrafo
from app.pipeline.event_graph import RULESET_VERSION
from app.pipeline.event_graph.metrics import calcola, elenca_run, registra_run

BACKEND = Path(__file__).resolve().parents[1]
PACKAGE_DIR = BACKEND / "app" / "pipeline" / "event_graph"
METRICS_PATH = PACKAGE_DIR / "metrics.py"
PIPELINE_PATH = PACKAGE_DIR / "pipeline.py"
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


def _evento(
    event_id: str,
    *,
    lemma: str = "arrivare",
    piano: str = "PRIMO_PIANO",
    fuso_in: str | None = None,
    catena_id: str | None = None,
    catena_ruolo: str | None = None,
    catena_precedente_id: str | None = None,
) -> EventoRisolto:
    return EventoRisolto(
        id=event_id,
        lemma=lemma,
        piano=piano,  # type: ignore[arg-type]
        fuso_in=fuso_in,
        catena_id=catena_id,
        catena_ruolo=catena_ruolo,  # type: ignore[arg-type]
        catena_precedente_id=catena_precedente_id,
    )


def _arco(tipo: str, da_id: str, a_id: str, **props) -> ArcoEvento:
    return ArcoEvento(tipo=tipo, da_id=da_id, a_id=a_id, props=dict(props))


def _quar(motivo: str) -> QuarantenaItem:
    return QuarantenaItem(motivo=motivo, frammento=motivo)


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


def _zero_sottoconti() -> dict[str, int]:
    return {
        "ciclo_cronologico": 0,
        "ciclo_CAUSA": 0,
        "estrazione_fallita": 0,
        "malformazione": 0,
    }


def test_calcola_empty_sotto_zeros():
    metrics = calcola(SottoGrafo())
    assert metrics["tasso_quarantena"] == 0.0
    assert metrics["quarantena_sottoconti"] == _zero_sottoconti()
    assert metrics["quota_collegato"] == 0.0
    assert metrics["densita_contraddice"] == 0.0
    assert metrics["frazione_chunk_tempo_base_ereditato"] == 0.0
    assert metrics["catene_biforcate"] == 0
    assert metrics["distribuzione_esiti_coref"] == {
        "Fusione": 0,
        "Successione": 0,
        "Catena": 0,
    }
    assert metrics["quota_precede_dato_esplicito"] == 0.0
    assert metrics["archi_temporali_aggiunti"] == 0
    assert metrics["archi_temporali_superati"] == 0


def test_quarantena_sottoconti_ciclo_cronologico_vs_ciclo_causa():
    sotto = SottoGrafo()
    sotto.aggiungi(
        eventi=[_evento("ev-1")],
        quarantena=[
            _quar("ciclo cronologico: a->b->a"),
            _quar("ciclo_cronologico"),
            _quar("ciclo CAUSA"),
            _quar("ciclo CAUSA: x->y->x"),
            _quar("estrazione fallita"),
            _quar("estrazione_fallita"),
            _quar("ancora assente"),
            _quar("sogg assente"),
        ],
    )
    metrics = calcola(sotto)
    assert metrics["quarantena_sottoconti"] == {
        "ciclo_cronologico": 2,
        "ciclo_CAUSA": 2,
        "estrazione_fallita": 2,
        "malformazione": 2,
    }
    assert metrics["tasso_quarantena"] == 8 / 9


def test_quota_collegato_and_densita_contraddice():
    sotto = SottoGrafo()
    sotto.aggiungi(
        eventi=[
            _evento("a"),
            _evento("b", catena_ruolo="CONTRADDICE"),
            _evento("c"),
        ],
        archi=[
            _arco("COLLEGATO", "a", "b"),
            _arco("COLLEGATO", "b", "c"),
            _arco("CAUSA", "a", "c"),
            _arco("SOGG", "a", "m-1"),
        ],
    )
    metrics = calcola(sotto)
    assert metrics["quota_collegato"] == 2 / 3
    assert metrics["densita_contraddice"] == 1 / 3


def test_catene_biforcate_uses_chains_biforcazioni(monkeypatch):
    sotto = SottoGrafo()
    sotto.aggiungi(
        eventi=[
            _evento("A", catena_id="c1"),
            _evento("B", catena_id="c1", catena_ruolo="STESSO_EVENTO", catena_precedente_id="A"),
            _evento("C", catena_id="c1", catena_ruolo="STESSO_EVENTO", catena_precedente_id="A"),
        ],
    )
    called: list[SottoGrafo] = []
    real = __import__(
        "app.pipeline.event_graph.chains", fromlist=["biforcazioni"]
    ).biforcazioni

    def spy(graph):
        called.append(graph)
        return real(graph)

    monkeypatch.setattr("app.pipeline.event_graph.metrics.biforcazioni", spy)
    metrics = calcola(sotto)
    assert called
    assert called[0] is sotto
    assert metrics["catene_biforcate"] == 1
    assert metrics["catene_biforcate"] == len(real(sotto))


def test_distribuzione_esiti_coref_from_extra():
    sotto = SottoGrafo()
    sotto.aggiungi(
        eventi=[_evento("a", fuso_in="b"), _evento("b")],
        archi=[],
    )
    metrics = calcola(
        sotto,
        extra={"esiti_coref": {"Fusione": 2, "Successione": 1, "Catena": 3}},
    )
    assert metrics["distribuzione_esiti_coref"] == {
        "Fusione": 2,
        "Successione": 1,
        "Catena": 3,
    }


@pytest.mark.asyncio
async def test_registra_run_merge_no_delete_sets_versione_regole():
    session = FakeSession()
    sotto = SottoGrafo()
    sotto.aggiungi(eventi=[_evento("ev-1")])
    result = await registra_run(
        session, "job-m15", "doc-m15", RULESET_VERSION, sotto=sotto
    )
    assert result["versione_regole"] == RULESET_VERSION
    assert result["id"] == "job-m15"
    assert session.runs
    blob = " ".join(query for query, _ in session.runs)
    assert "MERGE (r:EventGraphRun" in blob
    assert "DELETE" not in blob.upper()
    assert "DETACH" not in blob.upper()
    assert any(params.get("ver") == RULESET_VERSION for _, params in session.runs)
    assert any(params.get("id") == "job-m15" for _, params in session.runs)
    assert any(params.get("doc") == "doc-m15" for _, params in session.runs)


@pytest.mark.asyncio
async def test_get_metrics_exists_with_stub_or_503(monkeypatch):
    session = FakeSession(
        rows=[
            {
                "r": {
                    "id": "job-1",
                    "documento": "doc-1",
                    "versione_regole": RULESET_VERSION,
                    "timestamp": "2026-01-01T00:00:00+00:00",
                }
            }
        ]
    )
    monkeypatch.setattr(
        "app.api.event_graph.get_driver",
        lambda: FakeDriver(session),
    )
    from app.main import app

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/event-graph/metrics")
    assert response.status_code in {200, 503}
    assert response.status_code != 404
    if response.status_code == 200:
        body = response.json()
        assert "runs" in body
        assert body["runs"][0]["id"] == "job-1"

    listed = await elenca_run(session)
    assert listed[0]["id"] == "job-1"
    assert any("MATCH (r:EventGraphRun)" in query for query, _ in session.runs)


def test_metrics_isolation_ast():
    source = METRICS_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(METRICS_PATH))
    violations = [
        f"{METRICS_PATH}: {module}"
        for module in _import_modules(tree)
        if _is_forbidden_import(module)
    ]
    assert violations == []
    assert "app.core" not in source
    assert "app.core.neo4j_client" not in source
    assert "sentence_transformers" not in source
    assert "DELETE" not in source.upper() or "no DELETE" in source or "No DELETE" in source


def test_pipeline_maybe_registra_run_imports_metrics():
    source = PIPELINE_PATH.read_text(encoding="utf-8")
    assert "from app.pipeline.event_graph.metrics import registra_run" in source
    assert "sotto=sotto" in source
    api_source = API_PATH.read_text(encoding="utf-8")
    assert '@router.get("/metrics")' in api_source
    assert "app.core.neo4j_client" not in api_source
