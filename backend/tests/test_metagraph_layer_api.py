"""FakeSession + HTTP tests for Metagraph layer GET/POST endpoints (Fase 12)."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.metagraph import list_event_incompleteness_endpoint
from app.api.schemas import (
    ConnectivityRuleListResponse,
    ContradictionListResponse,
    EventIncompletenessListResponse,
    JudgeRunListResponse,
)
from app.core.neo4j_client import get_neo4j_session
from app.main import app
from app.pipeline.metagraph_layer import (
    LIST_CONNECTIVITY_RULES_CYPHER,
    LIST_CONTRADICTIONS_CYPHER,
    LIST_EVENT_INCOMPLETENESS_CYPHER,
    LIST_JUDGE_RUNS_CYPHER,
    list_connectivity_rules,
    list_contradictions,
    list_event_incompleteness,
    list_judge_runs,
)


class FakeResult:
    def __init__(self, records: list[dict]):
        self._records = records

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for record in self._records:
            yield record

    async def single(self):
        return self._records[0] if self._records else None


class FakeSession:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.queue: list[list[dict]] = []

    def enqueue(self, records: list[dict]) -> None:
        self.queue.append(records)

    async def run(self, cypher, **kwargs):
        self.calls.append((cypher, kwargs))
        records = self.queue.pop(0) if self.queue else []
        return FakeResult(records)


@pytest.fixture
async def client():
    async def fake_session() -> AsyncIterator[AsyncMock]:
        yield AsyncMock()

    app.dependency_overrides[get_neo4j_session] = fake_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_neo4j_session, None)


@pytest.mark.asyncio
async def test_list_contradictions_never_filters():
    session = FakeSession()
    session.enqueue(
        [
            {
                "id": "c-1",
                "left_id": "tail-2010",
                "left_name": "2010",
                "right_id": "tail-2011",
                "right_name": "2011",
                "subject_id": "head",
            }
        ]
    )

    body = await list_contradictions(session)

    assert session.calls[0][0] == LIST_CONTRADICTIONS_CYPHER
    assert "is_latest" not in LIST_CONTRADICTIONS_CYPHER
    assert "WHERE" not in LIST_CONTRADICTIONS_CYPHER.split("RETURN")[0]
    assert len(body.items) == 1
    assert body.items[0].left_id == "tail-2010"
    assert body.items[0].right_id == "tail-2011"
    assert body.items[0].subject_id == "head"


@pytest.mark.asyncio
async def test_list_connectivity_rules_exposes_origin_count():
    session = FakeSession()
    session.enqueue(
        [
            {
                "source_category": "Agente",
                "relation_type": "works_at",
                "target_category": "CostruttoSociale",
                "generalization_level": 0,
                "origin_count": 3,
            }
        ]
    )

    body = await list_connectivity_rules(session)

    assert session.calls[0][0] == LIST_CONNECTIVITY_RULES_CYPHER
    assert body.items[0].origin_count == 3
    assert body.items[0].relation_type == "works_at"


@pytest.mark.asyncio
async def test_list_judge_runs_newest_first_shape():
    session = FakeSession()
    session.enqueue(
        [
            {
                "id": "jr-2",
                "batch_id": "b2",
                "timestamp": "2026-08-18T12:00:00",
                "anti_blur": 1,
                "equivalent_to": 0,
                "reraffine": 2,
                "temporal": 3,
            }
        ]
    )

    body = await list_judge_runs(session)

    assert session.calls[0][0] == LIST_JUDGE_RUNS_CYPHER
    assert "ORDER BY j.timestamp DESC" in LIST_JUDGE_RUNS_CYPHER
    assert body.items[0].id == "jr-2"
    assert body.items[0].anti_blur == 1
    assert body.items[0].temporal == 3


@pytest.mark.asyncio
async def test_list_event_incompleteness_is_read_only_and_incomplete_only():
    session = FakeSession()
    session.enqueue(
        [
            {
                "event_id": "evt-wait",
                "text": "waiting span",
                "missing_context": "need more",
                "first_seen_run_id": "run-1",
                "checks_without_progress": 1,
                "incomplete_at": "2026-08-20T10:00:00",
                "verdict": "waiting",
            },
            {
                "event_id": "evt-inc",
                "text": "esperimento 5 era fallato",
                "missing_context": "stato unknown",
                "first_seen_run_id": "run-2",
                "checks_without_progress": 5,
                "incomplete_at": "2026-08-21T12:00:00",
                "verdict": "incomplete",
            },
            {
                "event_id": "evt-ok",
                "text": "confirmed event",
                "missing_context": None,
                "first_seen_run_id": "run-0",
                "checks_without_progress": 0,
                "incomplete_at": "2026-08-19T09:00:00",
                "verdict": "confirmed",
            },
        ]
    )

    body = await list_event_incompleteness(session)

    assert session.calls[0][0] == LIST_EVENT_INCOMPLETENESS_CYPHER
    query = LIST_EVENT_INCOMPLETENESS_CYPHER.upper()
    for token in ("CREATE", "MERGE", "DELETE"):
        assert token not in query
    assert "SET " not in query
    handler_src = inspect.getsource(list_event_incompleteness)
    for token in ("CREATE", "MERGE", "DELETE", "SET "):
        assert token not in handler_src
    assert "call_structured" not in handler_src
    endpoint_src = inspect.getsource(list_event_incompleteness_endpoint)
    for token in ("CREATE", "MERGE", "DELETE", "SET "):
        assert token not in endpoint_src
    assert "verdict = 'incomplete'" in LIST_EVENT_INCOMPLETENESS_CYPHER
    assert len(body.items) == 1
    item = body.items[0]
    assert item.event_id == "evt-inc"
    assert item.text == "esperimento 5 era fallato"
    assert item.missing_context == "stato unknown"
    assert item.first_seen_run_id == "run-2"
    assert item.checks_without_progress == 5
    assert item.incomplete_at == "2026-08-21T12:00:00"
    assert item.timestamp == item.incomplete_at


@pytest.mark.asyncio
async def test_list_event_incompleteness_empty_graph_is_empty_items():
    session = FakeSession()
    session.enqueue([])

    body = await list_event_incompleteness(session)

    assert body.items == []
    assert session.calls[0][0] == LIST_EVENT_INCOMPLETENESS_CYPHER


@pytest.mark.asyncio
async def test_http_event_incompleteness_empty_graph_is_200_not_500():
    session = FakeSession()
    session.enqueue([])

    async def fake_session() -> AsyncIterator[FakeSession]:
        yield session

    app.dependency_overrides[get_neo4j_session] = fake_session
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/graph/event-incompleteness")
    finally:
        app.dependency_overrides.pop(get_neo4j_session, None)
    assert resp.status_code == 200
    assert resp.json() == {"items": []}


@pytest.mark.asyncio
async def test_http_contradictions_rules_judge(client: AsyncClient):
    async def mock_contra(session) -> ContradictionListResponse:
        _ = session
        return ContradictionListResponse(items=[])

    async def mock_rules(session) -> ConnectivityRuleListResponse:
        _ = session
        return ConnectivityRuleListResponse(items=[])

    async def mock_judge(session) -> JudgeRunListResponse:
        _ = session
        return JudgeRunListResponse(items=[])

    async def mock_incomplete(session) -> EventIncompletenessListResponse:
        _ = session
        return EventIncompletenessListResponse(items=[])

    with (
        patch(
            "app.api.metagraph.metagraph_layer.list_contradictions", mock_contra
        ),
        patch(
            "app.api.metagraph.metagraph_layer.list_connectivity_rules", mock_rules
        ),
        patch("app.api.metagraph.metagraph_layer.list_judge_runs", mock_judge),
        patch(
            "app.api.metagraph.metagraph_layer.list_event_incompleteness",
            mock_incomplete,
        ),
    ):
        contra = await client.get("/graph/contradictions")
        rules = await client.get("/graph/connectivity-rules")
        judge = await client.get("/graph/judge-runs")
        incomplete = await client.get("/graph/event-incompleteness")
        assert contra.status_code == 200
        assert rules.status_code == 200
        assert judge.status_code == 200
        assert incomplete.status_code == 200
        assert contra.json() == {"items": []}
        assert rules.json() == {"items": []}
        assert judge.json() == {"items": []}
        assert incomplete.json() == {"items": []}


def test_openapi_registers_metagraph_routes():
    paths = app.openapi()["paths"]
    assert "/graph/contradictions" in paths
    assert "/graph/connectivity-rules" in paths
    assert "/graph/judge-runs" in paths
    assert "/graph/event-incompleteness" in paths
    assert "get" in paths["/graph/event-incompleteness"]
    assert "post" not in paths["/graph/event-incompleteness"]
    assert "/graph/identities" not in paths
