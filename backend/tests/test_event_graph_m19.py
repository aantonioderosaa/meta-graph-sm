"""M19: catalog.py + GET /catalog /stats /graph (no Docker)."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import get_args

import httpx
import pytest
from httpx import ASGITransport

from app.models.event_graph import (
    Fattualita,
    PianoNarrativo,
    TempoVerbale,
    TipoRelazione,
)
from app.pipeline.event_graph.catalog import (
    catalogo,
    dettaglio_arco,
    dettaglio_nodo,
    grafo,
    stats,
)

BACKEND = Path(__file__).resolve().parents[1]
PACKAGE = BACKEND / "app" / "pipeline" / "event_graph"
CATALOG_PATH = PACKAGE / "catalog.py"
API_PATH = BACKEND / "app" / "api" / "event_graph.py"
LEGACY_FILES = (
    BACKEND / "app" / "core" / "event_bus.py",
    BACKEND / "app" / "core" / "neo4j_client.py",
    BACKEND / "app" / "pipeline" / "ingestion.py",
)


class FakeSession:
    def __init__(self, rows=None, mapping=None):
        self.rows = rows or []
        self.mapping = mapping or {}
        self.runs = []

    def _resolve(self, query: str):
        for key, rows in self.mapping.items():
            if key in query:
                return rows
        return self.rows

    async def run(self, query, parameters=None, **params):
        merged = dict(params)
        if parameters is not None and isinstance(parameters, dict):
            merged = {**parameters, **merged}
        self.runs.append((query, merged))
        rows = self._resolve(query)

        class R:
            def data(self_inner):
                return rows

            def single(self_inner):
                return rows[0] if rows else None

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


def _archi_tipi(payload: dict) -> set[str]:
    arches = payload["arches"]
    found: set[str] = set()
    groups = arches.values() if isinstance(arches, dict) else arches
    for group in groups:
        entries = [group] if isinstance(group, dict) and "tipo" in group else group
        for entry in entries:
            found.add(entry["tipo"])
    return found


def test_catalogo_covers_all_tipo_relazione():
    payload = catalogo()
    found = _archi_tipi(payload)
    expected = set(get_args(TipoRelazione))
    assert "SATELLITE_DI" in expected
    assert found == expected
    arches = payload["arches"]
    for famiglia in (
        "argomentali",
        "dizionario",
        "temporale",
        "placeholder",
        "struttura",
    ):
        assert famiglia in arches
        assert arches[famiglia]
        for entry in arches[famiglia]:
            assert entry["famiglia"] == famiglia
            assert entry["direzione"]
            assert entry["significato"]
    assert "catena" not in arches
    node_ids = {node["id"] for node in payload["nodes"]}
    assert node_ids == {"Evento", "Menzione", "Quarantena"}
    traits = payload["traits"]
    assert "catena" in traits
    assert set(traits["catena"]["ruoli"]) == {
        "STESSO_EVENTO",
        "AGGIORNA",
        "CONTRADDICE",
    }
    assert traits["tempo"] == list(get_args(TempoVerbale))
    assert traits["polarita"] == ["affermata", "negata"]
    assert False in traits["modalizzato"] and True in traits["modalizzato"]
    assert False in traits["iterativita"] and True in traits["iterativita"]
    assert traits["fattualita"] == list(get_args(Fattualita))
    assert traits["piano"] == list(get_args(PianoNarrativo))
    fonte = traits["fonte"]
    fonte_blob = " ".join(fonte) if isinstance(fonte, list) else str(fonte)
    assert "NARRATORE" in fonte_blob
    assert "menzione" in fonte_blob


def test_catalogo_does_not_hit_session():
    session = FakeSession(rows=[{"should": "not be read"}])
    catalogo()
    assert session.runs == []
    assert catalogo.__code__.co_argcount == 0


@pytest.mark.asyncio
async def test_stats_fakesession_maps_counts():
    session = FakeSession(
        mapping={
            "count(e)": [{"count(e)": 4}],
            "count(m)": [{"count(m)": 7}],
            "count(q)": [{"count(q)": 1}],
            "type(r)": [{"t": "CAUSA", "n": 2}, {"t": "PRECEDE", "n": 3}],
            "e.piano": [{"p": "PRIMO_PIANO", "n": 3}, {"p": "SFONDO", "n": 1}],
        }
    )
    result = await stats(session)
    assert result["nodi"] == {"Evento": 4, "Menzione": 7, "Quarantena": 1}
    assert result["archi"]["CAUSA"] == 2
    assert result["archi"]["PRECEDE"] == 3
    assert result["archi"]["SOGG"] == 0
    assert result["tratti"]["piano"]["PRIMO_PIANO"] == 3
    assert result["tratti"]["piano"]["SFONDO"] == 1
    assert result["tratti"]["piano"]["FUORI_LINEA"] == 0
    blob = " ".join(query for query, _ in session.runs)
    assert "MATCH (e:Evento) RETURN count(e)" in blob
    assert "MATCH (m:Menzione) RETURN count(m)" in blob
    assert "MATCH (q:Quarantena) RETURN count(q)" in blob
    assert "type(r)" in blob
    assert "e.piano" in blob


@pytest.mark.asyncio
async def test_grafo_fakesession_cytoscape_shape():
    session = FakeSession(
        rows=[
            {
                "id": "ev-1",
                "label": "arrivare",
                "tipo": "Evento",
                "piano": "PRIMO_PIANO",
                "fattualita": "FATTUALE",
                "documento": "doc-1",
            },
            {
                "id": "ev-fused",
                "label": "andare",
                "tipo": "Evento",
                "piano": "SFONDO",
                "fattualita": "FATTUALE",
                "documento": "doc-1",
                "fuso_in": "ev-1",
            },
            {
                "id": "m-1",
                "label": "Mario",
                "tipo": "Menzione",
                "documento": "doc-1",
            },
            {
                "id": "r-1",
                "source": "ev-1",
                "target": "m-1",
                "tipo": "SOGG",
                "base": None,
                "segnale": None,
                "superato_da": None,
                "conflitto": None,
            },
        ]
    )
    result = await grafo(session, documento="doc-1", piano="PRIMO_PIANO", lemma="arrivare")
    nodes = result["elements"]["nodes"]
    edges = result["elements"]["edges"]
    ids = {node["data"]["id"] for node in nodes}
    assert "ev-1" in ids
    assert "m-1" in ids
    assert "ev-fused" not in ids
    node = next(item for item in nodes if item["data"]["id"] == "ev-1")
    assert node["data"]["label"] == "arrivare"
    assert node["data"]["tipo"] == "Evento"
    assert node["data"]["piano"] == "PRIMO_PIANO"
    assert node["data"]["fattualita"] == "FATTUALE"
    assert node["data"]["documento"] == "doc-1"
    assert edges[0]["data"]["id"] == "r-1"
    assert edges[0]["data"]["source"] == "ev-1"
    assert edges[0]["data"]["target"] == "m-1"
    assert edges[0]["data"]["tipo"] == "SOGG"
    blob = " ".join(query for query, _ in session.runs)
    assert "fuso_in" in blob
    assert any(params.get("documento") == "doc-1" for _, params in session.runs)
    assert any(params.get("piano") == "PRIMO_PIANO" for _, params in session.runs)
    assert any(params.get("lemma") == "arrivare" for _, params in session.runs)


@pytest.mark.asyncio
async def test_grafo_drops_edges_whose_endpoint_is_not_a_returned_node():
    """Regression: a macro :Zona<->:Zona SEQUENZA arc (Addendum 2 M2) must
    never surface here — this view only ever returns Evento/Menzione/
    Quarantena nodes, so an edge pointing at a Zona id would otherwise reach
    Cytoscape with a source/target absent from `nodes` and crash the panel
    ("Can not create edge ... with nonexistant source").
    """
    session = FakeSession(
        mapping={
            "MATCH (e:Evento)": [
                {
                    "id": "ev-1",
                    "label": "arrivare",
                    "tipo": "Evento",
                    "piano": "PRIMO_PIANO",
                    "fattualita": "FATTUALE",
                    "documento": "doc-1",
                },
            ],
            "MATCH (a)-[r]->(b)": [
                {
                    "id": "seq-zona",
                    "source": "zona-1",
                    "target": "zona-2",
                    "tipo": "SEQUENZA",
                    "base": None,
                    "segnale": None,
                    "superato_da": None,
                    "conflitto": None,
                },
            ],
        }
    )
    result = await grafo(session)
    ids = {node["data"]["id"] for node in result["elements"]["nodes"]}
    assert ids == {"ev-1"}
    assert result["elements"]["edges"] == []


@pytest.mark.asyncio
async def test_dettaglio_nodo_returns_full_property_map():
    session = FakeSession(
        rows=[
            {
                "props": {
                    "id": "ev-1",
                    "lemma": "arrivare",
                    "piano": "PRIMO_PIANO",
                    "e_testa": True,
                    "modalita": "fattuale",
                    "offset_inizio": 120,
                    "offset_fine": 128,
                },
                "labels": ["Evento"],
            }
        ]
    )
    result = await dettaglio_nodo(session, "ev-1")
    assert result is not None
    assert result["id"] == "ev-1"
    assert result["labels"] == ["Evento"]
    # every field present in the stored node comes back, unprojected —
    # including ones this test never needs to know the name of in advance.
    assert result["proprieta"]["e_testa"] is True
    assert result["proprieta"]["offset_inizio"] == 120


@pytest.mark.asyncio
async def test_dettaglio_nodo_none_when_absent():
    session = FakeSession(rows=[{"props": None, "labels": []}])
    assert await dettaglio_nodo(session, "ghost") is None


@pytest.mark.asyncio
async def test_dettaglio_arco_returns_full_property_map_and_endpoints():
    session = FakeSession(
        rows=[
            {
                "props": {"id": "r-1", "base": "dato_esplicito", "confidenza": 0.82},
                "tipo": "PRECEDE",
                "source_id": "ev-1",
                "source_labels": ["Evento"],
                "source_label": "arrivare",
                "target_id": "ev-2",
                "target_labels": ["Evento"],
                "target_label": "partire",
            }
        ]
    )
    result = await dettaglio_arco(session, "r-1")
    assert result is not None
    assert result["tipo"] == "PRECEDE"
    assert result["proprieta"]["confidenza"] == 0.82
    assert result["source"] == {"id": "ev-1", "labels": ["Evento"], "label": "arrivare"}
    assert result["target"] == {"id": "ev-2", "labels": ["Evento"], "label": "partire"}


@pytest.mark.asyncio
async def test_dettaglio_arco_none_when_absent():
    session = FakeSession(rows=[{"props": None, "tipo": None}])
    assert await dettaglio_arco(session, "ghost") is None


@pytest.mark.asyncio
async def test_get_nodo_and_arco_endpoints_200_and_404(monkeypatch):
    from app.main import app

    session = FakeSession(
        mapping={
            "MATCH (n {id: $id})": [
                {"props": {"id": "ev-1", "lemma": "arrivare"}, "labels": ["Evento"]}
            ],
            "MATCH (a)-[r {id: $id}]->(b)": [
                {
                    "props": {"id": "r-1"},
                    "tipo": "PRECEDE",
                    "source_id": "ev-1",
                    "source_labels": ["Evento"],
                    "source_label": "arrivare",
                    "target_id": "ev-2",
                    "target_labels": ["Evento"],
                    "target_label": "partire",
                }
            ],
        }
    )
    monkeypatch.setattr(
        "app.api.event_graph.get_driver", lambda: FakeDriver(session)
    )
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        ok_node = await client.get("/event-graph/nodo/ev-1")
        ok_arc = await client.get("/event-graph/arco/r-1")

    assert ok_node.status_code == 200
    assert ok_node.json()["proprieta"]["lemma"] == "arrivare"
    assert ok_arc.status_code == 200
    assert ok_arc.json()["tipo"] == "PRECEDE"

    empty = FakeSession(rows=[])
    monkeypatch.setattr(
        "app.api.event_graph.get_driver", lambda: FakeDriver(empty)
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        missing_node = await client.get("/event-graph/nodo/ghost")
        missing_arc = await client.get("/event-graph/arco/ghost")
    assert missing_node.status_code == 404
    assert missing_arc.status_code == 404


@pytest.mark.asyncio
async def test_get_catalog_200_without_driver():
    from app.main import app

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/event-graph/catalog")
    assert response.status_code == 200
    body = response.json()
    assert _archi_tipi(body) == set(get_args(TipoRelazione))
    assert {node["id"] for node in body["nodes"]} == {
        "Evento",
        "Menzione",
        "Quarantena",
    }


@pytest.mark.asyncio
async def test_get_stats_503_without_driver():
    from app.main import app

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/event-graph/stats")
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_get_stats_200_with_stub(monkeypatch):
    session = FakeSession(
        mapping={
            "count(e)": [{"count(e)": 2}],
            "count(m)": [{"count(m)": 3}],
            "count(q)": [{"count(q)": 0}],
            "type(r)": [{"t": "CAUSA", "n": 1}],
            "e.piano": [{"p": "PRIMO_PIANO", "n": 2}],
        }
    )
    monkeypatch.setattr(
        "app.api.event_graph.get_driver", lambda: FakeDriver(session)
    )
    from app.main import app

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/event-graph/stats")
    assert response.status_code == 200
    body = response.json()
    assert body["nodi"]["Evento"] == 2
    assert body["nodi"]["Menzione"] == 3
    assert body["archi"]["CAUSA"] == 1
    assert body["tratti"]["piano"]["PRIMO_PIANO"] == 2


@pytest.mark.asyncio
async def test_get_graph_200_with_stub_excludes_fused(monkeypatch):
    session = FakeSession(
        rows=[
            {
                "id": "live",
                "label": "arrivare",
                "tipo": "Evento",
                "piano": "PRIMO_PIANO",
                "fattualita": "FATTUALE",
                "documento": "doc-1",
            },
            {
                "id": "fused",
                "label": "fuso",
                "tipo": "Evento",
                "piano": "SFONDO",
                "fattualita": "FATTUALE",
                "documento": "doc-1",
                "fuso_in": "live",
            },
            {
                "id": "m-1",
                "label": "Mario",
                "tipo": "Menzione",
                "documento": "doc-1",
            },
            {
                "id": "rel-1",
                "source": "live",
                "target": "m-1",
                "tipo": "SOGG",
            },
        ]
    )
    monkeypatch.setattr(
        "app.api.event_graph.get_driver", lambda: FakeDriver(session)
    )
    from app.main import app

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/event-graph/graph",
            params={"documento": "doc-1", "piano": "PRIMO_PIANO", "lemma": "arrivare"},
        )
    assert response.status_code == 200
    body = response.json()
    ids = {node["data"]["id"] for node in body["elements"]["nodes"]}
    assert "live" in ids
    assert "fused" not in ids
    edge = body["elements"]["edges"][0]["data"]
    assert edge["id"] == "rel-1"
    assert edge["source"] == "live"
    assert edge["target"] == "m-1"
    blob = " ".join(query for query, _ in session.runs)
    assert "fuso_in" in blob
    assert any(params.get("documento") == "doc-1" for _, params in session.runs)


def test_catalog_isolation_ast():
    source = CATALOG_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(CATALOG_PATH))
    imported = _import_modules(tree)
    violations = [
        f"{CATALOG_PATH}: {module}"
        for module in imported
        if _is_forbidden_import(module)
    ]
    assert violations == []
    assert "app.core" not in source
    assert "app.core.neo4j_client" not in source
    assert "sentence_transformers" not in source
    assert "graphdatascience" not in source.lower()
    assert not any("gds" in module.lower() for module in imported)
    assert "app.models.event_graph" in imported
    api_source = API_PATH.read_text(encoding="utf-8")
    assert '@router.get("/catalog")' in api_source
    assert '@router.get("/stats")' in api_source
    assert '@router.get("/graph")' in api_source
    assert "app.core.neo4j_client" not in api_source


def test_legacy_files_untouched():
    for path in LEGACY_FILES:
        assert path.is_file(), f"legacy file missing: {path}"
