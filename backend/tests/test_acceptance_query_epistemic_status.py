"""Fase 11 acceptance: coarse-to-fine planner and S0/S2 citation labels.

No Docker. DispatchSession style matches test_acceptance_node_query.py.
"""

from __future__ import annotations

import re

import pytest

from app.models.query import NodeQueryResponse, NodeSubgraph, QueryCitation
from app.pipeline import node_query_engine as nqe
from app.pipeline.node_ppr_projection import EXISTS_CYPHER
from app.pipeline.node_query_engine import NodeQueryAnswer, run_node_query

_RELATION_WRITE_RE = re.compile(
    r"\b(?:CREATE|MERGE)\b[\s\S]{0,240}:Relation\b",
    re.IGNORECASE,
)


class FakeResult:
    def __init__(self, records: list[dict]):
        self._records = records
        self._i = 0

    async def single(self):
        return self._records[0] if self._records else None

    async def consume(self):
        return None

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._i >= len(self._records):
            raise StopAsyncIteration
        record = self._records[self._i]
        self._i += 1
        return record


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


def _describe_row(
    *,
    node_id: str,
    name: str,
    node_type: str | None,
    labels: list[str],
    out_rels: list[dict] | None = None,
    in_rels: list[dict] | None = None,
):
    return {
        "id": node_id,
        "name": name,
        "type": node_type,
        "labels": labels,
        "out_rels": out_rels or [],
        "in_rels": in_rels or [],
        "concepts": [],
        "source_doc_ids": [],
        "concept_holders": [],
    }


def _asserted_session() -> DispatchSession:
    session = DispatchSession()
    session.on("node_embedding", [{"id": "alice", "score": 0.92}])
    session.on(EXISTS_CYPHER, [{"exists": True}])
    session.on(
        "gds.pageRank.stream",
        [
            {"id": "alice", "labels": ["Node"], "score": 0.9},
            {"id": "acme", "labels": ["Node"], "score": 0.8},
        ],
    )
    session.on(
        "OPTIONAL MATCH (n)-[r_out:Relation]->(out)",
        [
            _describe_row(
                node_id="alice",
                name="Alice",
                node_type="entity",
                labels=["Node"],
                out_rels=[{"rel": "lavora presso", "name": "Acme", "id": "acme"}],
            ),
            _describe_row(
                node_id="acme",
                name="Acme",
                node_type="entity",
                labels=["Node"],
                in_rels=[{"rel": "lavora presso", "name": "Alice", "id": "alice"}],
            ),
        ],
    )
    session.on(
        "Relation|HAS_CONCEPT",
        [{"source": "alice", "target": "acme", "rel_type": "works_at"}],
    )
    return session


def _derived_session() -> DispatchSession:
    session = DispatchSession()
    session.on("node_embedding", [{"id": "alice", "score": 0.94}])
    session.on(EXISTS_CYPHER, [{"exists": True}])
    session.on(
        "gds.pageRank.stream",
        [
            {"id": "alice", "labels": ["Node"], "score": 0.9},
            {"id": "mid", "labels": ["Node"], "score": 0.6},
            {"id": "x", "labels": ["Node"], "score": 0.5},
        ],
    )
    session.on(
        "OPTIONAL MATCH (n)-[r_out:Relation]->(out)",
        [
            _describe_row(
                node_id="alice",
                name="Alice",
                node_type="entity",
                labels=["Node"],
                out_rels=[{"rel": "teammate", "name": "Mid", "id": "mid"}],
            ),
            _describe_row(
                node_id="mid",
                name="Mid",
                node_type="entity",
                labels=["Node"],
                in_rels=[{"rel": "teammate", "name": "Alice", "id": "alice"}],
                out_rels=[{"rel": "coached_by", "name": "X", "id": "x"}],
            ),
            _describe_row(
                node_id="x",
                name="X",
                node_type="entity",
                labels=["Node"],
                in_rels=[{"rel": "coached_by", "name": "Mid", "id": "mid"}],
            ),
        ],
    )
    session.on(
        "Relation|HAS_CONCEPT",
        [
            {"source": "alice", "target": "mid", "rel_type": "teammate"},
            {"source": "mid", "target": "x", "rel_type": "coached_by"},
        ],
    )
    session.on(
        "r.origin_fact_ids",
        [
            {
                "source_category": "giocatore",
                "relation_type": "coached_by",
                "target_category": "coach",
                "generalization_level": 0,
                "origin_fact_ids": ["seed"],
            }
        ],
    )
    session.on(
        "r.lifted_from IS NULL",
        [
            {
                "src_id": "alice",
                "tgt_id": "mid",
                "relation": "teammate",
                "kernel_parent": "SocialeIntenzionale",
                "normalized_relation": None,
            },
            {
                "src_id": "mid",
                "tgt_id": "x",
                "relation": "coached_by",
                "kernel_parent": "Partecipativa",
                "normalized_relation": None,
            },
        ],
    )
    session.on(
        "n.kernel_category AS kernel_category",
        [
            {
                "id": "alice",
                "kernel_category": "Agente",
                "concept_id": "giocatore",
                "concept_name": "Giocatore",
            },
            {
                "id": "mid",
                "kernel_category": "Agente",
                "concept_id": "giocatore",
                "concept_name": "Giocatore",
            },
            {
                "id": "x",
                "kernel_category": "Agente",
                "concept_id": "coach",
                "concept_name": "Coach",
            },
        ],
    )
    session.on("child.id AS child_id", [])
    return session


@pytest.fixture(autouse=True)
def _stub_models(monkeypatch):
    monkeypatch.setattr(nqe.embeddings, "embed", lambda text: [0.1] * 768)
    monkeypatch.setattr(
        nqe,
        "_predict_rerank",
        lambda question, descriptions: [0.5] * len(descriptions),
    )
    monkeypatch.setattr(nqe, "_driver_or_none", lambda: None)


@pytest.mark.asyncio
async def test_asserted_only_citations_have_no_derivation_chain(monkeypatch):
    async def fake_llm(system, user, model, temperature=0, job_id=None):
        _ = system, user, model, temperature, job_id
        return NodeQueryAnswer(
            answer="Alice lavora presso Acme.",
            cited_node_ids=["alice", "acme"],
        )

    monkeypatch.setattr(nqe, "call_structured", fake_llm)
    session = _asserted_session()
    response = await run_node_query(session, "dove lavora Alice?")
    assert isinstance(response, NodeQueryResponse)
    assert response.citations
    assert all(c.epistemic_status == "asserted" for c in response.citations)
    assert all(not c.derivation_chain for c in response.citations)
    assert not any(
        rel.source == "alice" and rel.target == "acme" and "derived" in rel.type.lower()
        for rel in response.subgraph.relationships
    )


@pytest.mark.asyncio
async def test_derived_cross_domain_citation_has_readable_chain(monkeypatch):
    async def fake_llm(system, user, model, temperature=0, job_id=None):
        _ = model, temperature, job_id
        assert "Ipotesi S2" in user
        return NodeQueryAnswer(
            answer="Alice è ipoteticamente allieva di X.",
            cited_node_ids=["alice", "x"],
        )

    monkeypatch.setattr(nqe, "call_structured", fake_llm)
    session = _derived_session()
    response = await run_node_query(session, "Alice è allenata da X?")
    derived = [c for c in response.citations if c.epistemic_status == "derived"]
    assert derived
    chain = derived[0].derivation_chain
    assert chain
    assert {step.kind for step in chain} >= {"s0", "s1"}
    assert any("coached_by" in step.detail for step in chain)
    pairs = {(rel.source, rel.target, rel.type) for rel in response.subgraph.relationships}
    assert ("alice", "x", "coached_by") not in pairs
    for cypher, _kw in session.calls:
        assert _RELATION_WRITE_RE.search(cypher) is None
        compact = " ".join(cypher.split())
        assert "CREATE" not in compact or "NodeQueryLog" in compact
        assert "MERGE" not in compact or ":USED" in compact


@pytest.mark.asyncio
async def test_planner_runs_before_hybrid_on_two_kernel_types(monkeypatch):
    async def fake_llm(system, user, model, temperature=0, job_id=None):
        _ = system, user, model, temperature, job_id
        return NodeQueryAnswer(answer="Alice lavora presso Acme.", cited_node_ids=["alice"])

    monkeypatch.setattr(nqe, "call_structured", fake_llm)
    session = _asserted_session()
    await run_node_query(session, "quale Persona collabora con quale Organizzazione?")
    rule_idx = next(
        i for i, (cy, _kw) in enumerate(session.calls) if "r.source_category IN $cats" in cy
    )
    vector_idx = next(
        i for i, (cy, _kw) in enumerate(session.calls) if "node_embedding" in cy
    )
    assert rule_idx < vector_idx


def test_node_query_response_still_validates_without_citations():
    response = NodeQueryResponse(
        answer="ok",
        nodes_used=[],
        subgraph=NodeSubgraph(nodes=[], relationships=[]),
    )
    assert response.citations == []
    QueryCitation(id="n1", epistemic_status="asserted")


@pytest.mark.asyncio
async def test_empty_graph_citations_are_empty(monkeypatch):
    async def fake_llm(system, user, model, temperature=0, job_id=None):
        raise AssertionError("LLM must not run on empty seeds")

    monkeypatch.setattr(nqe, "call_structured", fake_llm)
    session = DispatchSession()
    response = await run_node_query(session, "zzzz-unrelated-query")
    assert response.citations == []
    assert response.cited_node_ids == []
