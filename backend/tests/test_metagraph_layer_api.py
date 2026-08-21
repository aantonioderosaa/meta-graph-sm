"""FakeSession + HTTP tests for Metagraph layer GET/POST endpoints (Fase 12)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.schemas import (
    ConnectivityRuleListResponse,
    ContextLayerRunsResponse,
    ContradictionListResponse,
    IdentityItem,
    IdentityListResponse,
    JudgeRunListResponse,
    UnlinkFacetResponse,
)
from app.core.neo4j_client import get_neo4j_session
from app.main import app
from app.pipeline import metagraph_layer
from app.pipeline.identity_resolution import UNLINK_FACET_CYPHER
from app.pipeline.metagraph_layer import (
    GET_IDENTITY_CYPHER,
    LIST_CONNECTIVITY_RULES_CYPHER,
    LIST_CONTRADICTIONS_CYPHER,
    LIST_IDENTITIES_CYPHER,
    LIST_JUDGE_RUNS_CYPHER,
    get_identity,
    list_connectivity_rules,
    list_context_layer_runs,
    list_contradictions,
    list_identities,
    list_judge_runs,
    unlink_identity_facet,
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
async def test_list_identities_groups_facets():
    session = FakeSession()
    session.enqueue(
        [
            {
                "uri": "identity:alice:Agente",
                "facet_id": "alice-ceo",
                "facet_name": "Alice CEO",
                "kernel_category": "Agente",
            },
            {
                "uri": "identity:alice:Agente",
                "facet_id": "alice-author",
                "facet_name": "Alice autrice",
                "kernel_category": "Agente",
            },
        ]
    )

    body = await list_identities(session)

    assert session.calls[0][0] == LIST_IDENTITIES_CYPHER
    assert len(body.items) == 1
    assert body.items[0].uri == "identity:alice:Agente"
    assert [f.id for f in body.items[0].facets] == ["alice-ceo", "alice-author"]


@pytest.mark.asyncio
async def test_get_identity_missing_returns_empty_facets():
    session = FakeSession()
    session.enqueue([])

    item = await get_identity(session, "identity:missing:Agente")

    assert item.uri == "identity:missing:Agente"
    assert item.facets == []
    assert session.calls[0][0] == GET_IDENTITY_CYPHER


@pytest.mark.asyncio
async def test_unlink_facet_calls_identity_resolution_not_merge():
    session = FakeSession()
    session.enqueue([])

    result = await unlink_identity_facet(
        session, "identity:alice:Agente", "alice-ceo"
    )

    assert result.unlinked is True
    assert result.identity_uri == "identity:alice:Agente"
    assert result.facet_node_id == "alice-ceo"
    assert session.calls[0][0] == UNLINK_FACET_CYPHER
    assert session.calls[0][1]["facet_node_id"] == "alice-ceo"
    assert session.calls[0][1]["identity_id"] == "identity:alice:Agente"
    blob = " ".join(cypher for cypher, _ in session.calls)
    assert "merged_into" not in blob
    assert "DETACH DELETE" not in blob


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
                "identity": 1,
                "missed_contradictions": 0,
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
async def test_list_context_layer_runs_empty_graph():
    session = FakeSession()

    body = await list_context_layer_runs(session)

    assert body.agent_runs == []
    assert body.open_hypotheses == []
    assert body.gate_runs == []
    assert len(session.calls) == 3


@pytest.mark.asyncio
async def test_list_context_layer_runs_shape():
    session = FakeSession()
    session.enqueue(
        [
            {
                "id": "agentrun:job:hyp-1",
                "hypothesis_id": "hyp-1",
                "verdict": "confirmed",
                "turns_used": 2,
                "timestamp": "2026-08-21T12:00:00",
                "steps": '[{"action":"conclude"}]',
            }
        ]
    )
    session.enqueue(
        [
            {
                "id": "hyp-open",
                "claim_target": "cani",
                "confidence": "low",
                "status": "open",
                "marker_category": "quantifier",
                "kind": "t2",
                "origin_doc_id": "doc-a",
                "listen_count": 0,
                "promoted": False,
                "evidence_gap": "quantifier scope not closed",
            }
        ]
    )
    session.enqueue(
        [
            {
                "id": "job-1",
                "job_id": "job-1",
                "timestamp": "2026-08-21T12:00:00",
                "t1": 1,
                "t2": 2,
                "t3": 0,
                "model_fallback": 1,
                "promotions": 1,
                "agent_runs": 1,
                "agent_turns_used": 2,
            }
        ]
    )

    body = await list_context_layer_runs(session)

    assert body.agent_runs[0].hypothesis_id == "hyp-1"
    assert body.agent_runs[0].verdict == "confirmed"
    assert body.agent_runs[0].turns_used == 2
    assert body.open_hypotheses[0].id == "hyp-open"
    assert body.open_hypotheses[0].status == "open"
    assert body.gate_runs[0].t1 == 1
    assert body.gate_runs[0].t2 == 2
    assert body.gate_runs[0].model_fallback == 1
    assert body.gate_runs[0].promotions == 1
    assert body.gate_runs[0].agent_runs == 1


@pytest.mark.asyncio
async def test_http_identities_and_unlink(client: AsyncClient):
    async def mock_list(session) -> IdentityListResponse:
        _ = session
        return IdentityListResponse(
            items=[
                IdentityItem(
                    uri="identity:alice:Agente",
                    facets=[],
                )
            ]
        )

    async def mock_unlink(session, uri: str, facet_node_id: str) -> UnlinkFacetResponse:
        _ = session
        return UnlinkFacetResponse(
            unlinked=True, identity_uri=uri, facet_node_id=facet_node_id
        )

    with (
        patch.object(metagraph_layer, "list_identities", mock_list),
        patch("app.api.metagraph.metagraph_layer.list_identities", mock_list),
        patch(
            "app.api.metagraph.metagraph_layer.unlink_identity_facet", mock_unlink
        ),
    ):
        listed = await client.get("/graph/identities")
        assert listed.status_code == 200
        assert listed.json()["items"][0]["uri"] == "identity:alice:Agente"

        unlinked = await client.post(
            "/graph/identities/identity:alice:Agente/unlink",
            json={"facet_node_id": "alice-ceo"},
        )
        assert unlinked.status_code == 200
        body = unlinked.json()
        assert body["unlinked"] is True
        assert body["facet_node_id"] == "alice-ceo"


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

    with (
        patch(
            "app.api.metagraph.metagraph_layer.list_contradictions", mock_contra
        ),
        patch(
            "app.api.metagraph.metagraph_layer.list_connectivity_rules", mock_rules
        ),
        patch("app.api.metagraph.metagraph_layer.list_judge_runs", mock_judge),
    ):
        contra = await client.get("/graph/contradictions")
        rules = await client.get("/graph/connectivity-rules")
        judge = await client.get("/graph/judge-runs")
        assert contra.status_code == 200
        assert rules.status_code == 200
        assert judge.status_code == 200
        assert contra.json() == {"items": []}
        assert rules.json() == {"items": []}
        assert judge.json() == {"items": []}


@pytest.mark.asyncio
async def test_http_context_layer_runs(client: AsyncClient):
    async def mock_runs(session) -> ContextLayerRunsResponse:
        _ = session
        return ContextLayerRunsResponse(
            agent_runs=[],
            open_hypotheses=[],
            gate_runs=[],
        )

    with patch(
        "app.api.metagraph.metagraph_layer.list_context_layer_runs", mock_runs
    ):
        listed = await client.get("/graph/context-layer/runs")
        assert listed.status_code == 200
        assert listed.json() == {
            "agent_runs": [],
            "open_hypotheses": [],
            "gate_runs": [],
        }


def test_openapi_registers_metagraph_routes():
    paths = app.openapi()["paths"]
    assert "/graph/identities" in paths
    assert "/graph/identities/{uri}" in paths
    assert "/graph/identities/{uri}/unlink" in paths
    assert "/graph/contradictions" in paths
    assert "/graph/connectivity-rules" in paths
    assert "/graph/judge-runs" in paths
    assert "/graph/context-layer/runs" in paths
    assert "get" in paths["/graph/identities"]
    assert "get" in paths["/graph/context-layer/runs"]
    assert "post" in paths["/graph/identities/{uri}/unlink"]
