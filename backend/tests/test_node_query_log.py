"""Unit tests for NodeQueryLog persistence (Macrotask 4). No Docker."""

from __future__ import annotations

import json

import pytest

from app.models.query import NodeQueryResponse
from app.pipeline import node_query_engine as nqe
from app.pipeline import node_query_log
from app.pipeline.node_query_engine import NodeQueryAnswer, run_node_query
from app.pipeline.node_query_log import (
    GET_LOG_CYPHER,
    LINK_USED_CONCEPTS_CYPHER,
    LINK_USED_NODES_CYPHER,
    LIST_LOGS_CYPHER,
    WRITE_LOG_CYPHER,
    get_node_query_log_detail,
    list_node_query_logs,
    write_node_query_log,
)
from tests.test_node_query_engine import _alice_bob_session


class FakeResult:
    def __init__(self, records: list[dict]):
        self._records = records
        self._i = 0

    async def single(self):
        return self._records[0] if self._records else None

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._i >= len(self._records):
            raise StopAsyncIteration
        record = self._records[self._i]
        self._i += 1
        return record


class LabelStoreSession:
    """In-memory store keyed by node label so listing cannot cross QueryLog."""

    def __init__(self):
        self.by_label: dict[str, list[dict]] = {}
        self.calls: list[tuple[str, dict]] = []

    async def run(self, cypher, **kwargs):
        self.calls.append((cypher, kwargs))
        compact = " ".join(cypher.split())
        if "CREATE (q:NodeQueryLog" in compact:
            self.by_label.setdefault("NodeQueryLog", []).append(
                {
                    "id": kwargs["id"],
                    "text": kwargs["text"],
                    "created_at": "2026-01-02T00:00:00",
                }
            )
            return FakeResult([])
        if "CREATE (q:QueryLog" in compact:
            self.by_label.setdefault("QueryLog", []).append(
                {
                    "id": kwargs["id"],
                    "text": kwargs["text"],
                    "created_at": "2026-01-01T00:00:00",
                }
            )
            return FakeResult([])
        if "MATCH (q:NodeQueryLog)" in compact and "RETURN q.id AS id" in compact:
            rows = list(reversed(self.by_label.get("NodeQueryLog", [])))
            return FakeResult(rows[: kwargs.get("limit", 20)])
        if "MATCH (q:QueryLog)" in compact and "RETURN q.id AS id" in compact:
            rows = list(reversed(self.by_label.get("QueryLog", [])))
            return FakeResult(rows[: kwargs.get("limit", 20)])
        return FakeResult([])


class DispatchSession:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.handlers: list[tuple[str, object]] = []

    def on(self, snippet: str, records) -> None:
        self.handlers.append((snippet, records))

    async def run(self, cypher, **kwargs):
        self.calls.append((cypher, kwargs))
        for snippet, records in self.handlers:
            if snippet in cypher:
                data = records(kwargs) if callable(records) else records
                return FakeResult(list(data))
        return FakeResult([])


def test_write_cypher_creates_node_query_log_not_query_log():
    assert "CREATE (q:NodeQueryLog" in WRITE_LOG_CYPHER
    assert "CREATE (q:QueryLog" not in WRITE_LOG_CYPHER
    assert "cited_node_ids" in WRITE_LOG_CYPHER
    assert ":Fact" not in WRITE_LOG_CYPHER


def test_list_cypher_matches_only_node_query_log():
    assert "MATCH (q:NodeQueryLog)" in LIST_LOGS_CYPHER
    assert "MATCH (q:QueryLog)" not in LIST_LOGS_CYPHER
    assert "MATCH (q:QueryLog)" not in GET_LOG_CYPHER
    assert "MATCH (q:NodeQueryLog {id: $id})" in GET_LOG_CYPHER
    assert "merged_into IS NULL" in GET_LOG_CYPHER
    assert ":Fact" not in LIST_LOGS_CYPHER
    assert ":USED" in LINK_USED_NODES_CYPHER
    assert ":Node" in LINK_USED_NODES_CYPHER
    assert ":Concept" in LINK_USED_CONCEPTS_CYPHER


@pytest.mark.asyncio
async def test_listing_never_crosses_query_log_labels():
    session = LabelStoreSession()
    await write_node_query_log(
        session,
        query_id="nql-1",
        text="chi è Alice?",
        answer="Alice.",
        cited_node_ids=["alice"],
        node_ids=["alice"],
        concept_ids=[],
    )

    node_items = await list_node_query_logs(session, limit=20)

    assert [item.id for item in node_items] == ["nql-1"]
    assert [item.text for item in node_items] == ["chi è Alice?"]
    joined = "\n".join(cy for cy, _kw in session.calls)
    assert "MATCH (q:QueryLog)" not in joined
    assert "NodeQueryLog" in joined


@pytest.mark.asyncio
async def test_get_node_query_log_detail_missing_id_returns_none():
    session = DispatchSession()
    detail = await get_node_query_log_detail(session, "does-not-exist")
    assert detail is None


@pytest.mark.asyncio
async def test_get_node_query_log_detail_rebuilds_response():
    session = DispatchSession()
    session.on(
        "OPTIONAL MATCH (q)-[:USED]->(n:Node)",
        [
            {
                "q": {"answer": "Alice è un'entità.", "cited_node_ids": ["alice"]},
                "node_ids": ["alice"],
                "concept_ids": ["leadership"],
            }
        ],
    )
    session.on(
        "labels(n) AS labels",
        [
            {
                "id": "alice",
                "name": "Alice",
                "type": "entity",
                "labels": ["Node"],
            },
            {
                "id": "leadership",
                "name": "Leadership",
                "type": None,
                "labels": ["Concept"],
            },
        ],
    )
    session.on(
        "Relation|HAS_CONCEPT",
        [
            {
                "source": "alice",
                "target": "leadership",
                "rel_type": "HAS_CONCEPT",
            }
        ],
    )

    detail = await get_node_query_log_detail(session, "nql-1")
    assert isinstance(detail, NodeQueryResponse)
    assert detail.answer == "Alice è un'entità."
    assert detail.cited_node_ids == ["alice"]
    assert [n.id for n in detail.nodes_used] == ["alice"]
    assert detail.nodes_used[0].name == "Alice"
    assert detail.nodes_used[0].type == "entity"
    assert [c.id for c in detail.concepts_used] == ["leadership"]
    assert detail.concepts_used[0].name == "Leadership"
    assert {n.id for n in detail.subgraph.nodes} == {"alice", "leadership"}
    assert detail.subgraph.relationships[0].type == "HAS_CONCEPT"
    assert detail.citations == []


@pytest.mark.asyncio
async def test_get_detail_skips_merged_nodes_not_in_load():
    session = DispatchSession()
    session.on(
        "OPTIONAL MATCH (q)-[:USED]->(n:Node)",
        [
            {
                "q": {"answer": "ok", "cited_node_ids": ["alice"]},
                "node_ids": ["alice", "alice-dup"],
                "concept_ids": [],
            }
        ],
    )
    session.on(
        "labels(n) AS labels",
        [
            {
                "id": "alice",
                "name": "Alice",
                "type": "entity",
                "labels": ["Node"],
            }
        ],
    )
    session.on("Relation|HAS_CONCEPT", [])

    detail = await get_node_query_log_detail(session, "nql-merged")
    assert detail is not None
    assert [n.id for n in detail.nodes_used] == ["alice"]
    assert "alice-dup" not in {n.id for n in detail.subgraph.nodes}


@pytest.mark.asyncio
async def test_failed_log_write_does_not_fail_run_node_query(monkeypatch):
    async def boom(*_args, **_kwargs):
        raise RuntimeError("neo4j write failed")

    monkeypatch.setattr(node_query_log, "write_node_query_log", boom)
    monkeypatch.setattr(nqe.embeddings, "embed", lambda text: [0.1] * 768)
    monkeypatch.setattr(
        nqe,
        "_predict_rerank",
        lambda question, descriptions: [0.5] * len(descriptions),
    )

    async def fake_llm(system, user, model, temperature=0, job_id=None):
        return NodeQueryAnswer(
            answer="Alice partecipa alla riunione.", cited_node_ids=["alice"]
        )

    monkeypatch.setattr(nqe, "call_structured", fake_llm)

    session = _alice_bob_session()
    response = await run_node_query(session, "chi è Alice?")
    assert isinstance(response, NodeQueryResponse)
    assert response.nodes_used
    assert {n.id for n in response.nodes_used} >= {"alice"}


@pytest.mark.asyncio
async def test_get_detail_reconstructs_persisted_citations():
    from app.models.query import DerivationStep, QueryCitation

    citations = [
        QueryCitation(id="alice", epistemic_status="asserted"),
        QueryCitation(
            id="alice|coached_by|x",
            epistemic_status="derived",
            derivation_chain=[
                DerivationStep(kind="s0", detail="alice-[teammate]->mid"),
                DerivationStep(kind="s1", detail="giocatore -coached_by-> coach"),
            ],
        ),
    ]
    session = DispatchSession()
    session.on(
        "OPTIONAL MATCH (q)-[:USED]->(n:Node)",
        [
            {
                "q": {
                    "answer": "ipotesi.",
                    "cited_node_ids": ["alice"],
                    "citations_json": json.dumps(
                        [c.model_dump() for c in citations], ensure_ascii=False
                    ),
                },
                "node_ids": ["alice"],
                "concept_ids": [],
            }
        ],
    )
    session.on(
        "labels(n) AS labels",
        [{"id": "alice", "name": "Alice", "type": "entity", "labels": ["Node"]}],
    )
    session.on("Relation|HAS_CONCEPT", [])

    detail = await get_node_query_log_detail(session, "nql-cite")
    assert detail is not None
    assert len(detail.citations) == 2
    assert detail.citations[0].epistemic_status == "asserted"
    assert detail.citations[1].epistemic_status == "derived"
    assert detail.citations[1].derivation_chain
    assert detail.citations[1].derivation_chain[0].kind == "s0"
