"""GET /event-graph/documents + DELETE /event-graph/graph (no Docker)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.models.event_graph import EventQuerySpec
from app.pipeline.event_graph.infra import bus as event_graph_bus
from app.pipeline.event_graph.persistence import elenca_documenti, wipe_grafo
from app.pipeline.event_graph.query_structured import (
    QueryResult,
    elenca_query,
    registra_query,
    reset_query_history,
)


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


@pytest.mark.asyncio
async def test_elenca_documenti_maps_peso_formato_anteprima():
    testo = "x" * 200
    session = FakeSession(
        rows=[
            {
                "id": "sole-vento",
                "testo": testo,
                "formato": "txt",
                "updated_at": "2026-09-16T10:00:00+00:00",
                "n_eventi": 12,
            }
        ]
    )
    docs = await elenca_documenti(session)
    assert len(docs) == 1
    doc = docs[0]
    assert doc["id"] == "sole-vento"
    assert doc["formato"] == "txt"
    assert doc["caratteri"] == len(testo)
    assert doc["bytes"] == len(testo.encode("utf-8"))
    assert doc["n_eventi"] == 12
    assert doc["anteprima"].endswith("…")
    assert "DETACH" not in session.runs[0][0]


@pytest.mark.asyncio
async def test_elenca_documenti_defaults_formato_txt_when_missing():
    session = FakeSession(rows=[{"id": "doc-a", "testo": "ciao", "n_eventi": 0}])
    docs = await elenca_documenti(session)
    assert docs[0]["formato"] == "txt"
    assert docs[0]["caratteri"] == 4
    assert docs[0]["anteprima"] == "ciao"


@pytest.mark.asyncio
async def test_wipe_grafo_detach_deletes_all_nodes():
    session = FakeSession()
    query = await wipe_grafo(session)
    assert "DETACH DELETE" in query
    assert session.runs
    assert "DETACH DELETE" in session.runs[0][0]


@pytest.mark.asyncio
async def test_get_documents_and_delete_graph(monkeypatch):
    reset_query_history()
    session = FakeSession(
        rows=[
            {
                "id": "doc-1",
                "testo": "Mario arrivò.",
                "formato": "txt",
                "n_eventi": 1,
            }
        ]
    )
    monkeypatch.setattr(
        "app.api.event_graph.get_driver", lambda: FakeDriver(session)
    )
    registra_query(
        EventQuerySpec(testo="vento"),
        QueryResult(eventi=[{"id": "e1"}]),
        modo="nl",
    )
    assert elenca_query()

    await event_graph_bus.publish(
        "job-wipe", "macro", "macro_start", {"doc_id": "doc-1"}
    )
    assert event_graph_bus.elenca_job()

    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        listed = await client.get("/event-graph/documents")
        wiped = await client.delete("/event-graph/graph")

    assert listed.status_code == 200
    body = listed.json()
    assert body["documents"][0]["id"] == "doc-1"
    assert body["documents"][0]["formato"] == "txt"
    assert body["documents"][0]["caratteri"] == len("Mario arrivò.")
    assert wiped.status_code == 200
    assert wiped.json() == {"deleted": True}
    assert any("DETACH DELETE" in query for query, _ in session.runs)
    assert elenca_query() == []
    assert event_graph_bus.subscriber_count() == 0
    assert event_graph_bus.elenca_job() == []
    reset_query_history()
