"""Unit tests for the Node/Concept NL query engine (Macrotask 3). No Docker."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.query import NodeQueryResponse
from app.pipeline import node_query_engine as nqe
from app.pipeline.node_ppr_projection import EXISTS_CYPHER
from app.pipeline.node_query_engine import (
    Candidate,
    NodeQueryAnswer,
    assemble_context,
    hybrid_seed,
    rerank_candidates,
    rrf_fuse,
    run_node_query,
)

ENGINE_SOURCE = Path(nqe.__file__).read_text(encoding="utf-8")


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
    concepts: list[dict] | None = None,
    source_doc_ids: list[str] | None = None,
):
    return {
        "id": node_id,
        "name": name,
        "type": node_type,
        "labels": labels,
        "out_rels": out_rels or [],
        "in_rels": in_rels or [],
        "concepts": concepts or [],
        "source_doc_ids": source_doc_ids or [],
        "concept_holders": [],
    }


def _alice_bob_session() -> DispatchSession:
    session = DispatchSession()
    session.on(
        "node_embedding",
        [{"id": "alice", "score": 0.92}],
    )
    session.on(EXISTS_CYPHER, [{"exists": True}])
    session.on(
        "gds.pageRank.stream",
        [
            {"id": "alice", "labels": ["Node"], "score": 0.9},
            {"id": "meeting", "labels": ["Node"], "score": 0.4},
            {"id": "bob", "labels": ["Node"], "score": 0.2},
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
                in_rels=[
                    {
                        "rel": "is participated by",
                        "name": "Riunione Q3",
                        "id": "meeting",
                    }
                ],
            ),
            _describe_row(
                node_id="meeting",
                name="Riunione Q3",
                node_type="event",
                labels=["Node"],
                out_rels=[
                    {"rel": "is participated by", "name": "Alice", "id": "alice"},
                    {"rel": "is participated by", "name": "Bob", "id": "bob"},
                ],
            ),
            _describe_row(
                node_id="bob",
                name="Bob",
                node_type="entity",
                labels=["Node"],
                in_rels=[
                    {
                        "rel": "is participated by",
                        "name": "Riunione Q3",
                        "id": "meeting",
                    }
                ],
            ),
        ],
    )
    session.on(
        "Relation|HAS_CONCEPT",
        [
            {
                "source": "meeting",
                "target": "alice",
                "rel_type": "participates",
            },
            {
                "source": "meeting",
                "target": "bob",
                "rel_type": "participates",
            },
        ],
    )
    return session


@pytest.fixture(autouse=True)
def _stub_models(monkeypatch):
    monkeypatch.setattr(nqe.embeddings, "embed", lambda text: [0.1] * 768)
    monkeypatch.setattr(
        nqe,
        "_predict_rerank",
        lambda question, descriptions: [0.5] * len(descriptions),
    )

    async def fake_llm(system, user, model, temperature=0, job_id=None):
        return NodeQueryAnswer(answer="Alice partecipa alla riunione.", cited_node_ids=["alice"])

    monkeypatch.setattr(nqe, "call_structured", fake_llm)


def test_rrf_overlap_beats_single_channel_first():
    scores = rrf_fuse([["A", "B"], ["X", "B"]])
    assert scores["B"] > scores["A"]
    assert scores["B"] > scores["X"]


def test_similarity_threshold_drops_weak_vector_hits():
    kept = nqe._filter_vector_hits(
        [{"id": "weak", "score": 0.3}, {"id": "strong", "score": 0.8}],
        threshold=0.5,
    )
    assert [row["id"] for row in kept] == ["strong"]


@pytest.mark.asyncio
async def test_hybrid_seed_applies_threshold_before_rrf():
    session = DispatchSession()
    session.on("node_embedding", [{"id": "weak", "score": 0.3}])
    seeds = await hybrid_seed(
        session, text="domanda irrilevante", embedding=[0.0] * 768, threshold=0.5
    )
    assert "weak" not in seeds
    assert seeds == []


@pytest.mark.asyncio
async def test_relation_channel_alone_produces_seeds(monkeypatch):
    monkeypatch.setattr(nqe, "ENABLE_NODE_VECTOR", False)
    monkeypatch.setattr(nqe, "ENABLE_CONCEPT_VECTOR", False)
    monkeypatch.setattr(nqe, "ENABLE_NODE_CONCEPT_FULLTEXT", False)
    monkeypatch.setattr(nqe, "ENABLE_RELATION_FULLTEXT", False)
    session = DispatchSession()
    session.on(
        "relation_embedding",
        [{"start_id": "mario", "end_id": "acme", "score": 0.88}],
    )
    seeds = await hybrid_seed(
        session, text="chi ha fondato", embedding=[0.2] * 768
    )
    assert "mario" in seeds
    assert "acme" in seeds


def test_rerank_changes_ppr_order_and_top_n(monkeypatch):
    monkeypatch.setattr(nqe, "RERANK_TOP_N", 2)
    candidates = [
        Candidate(id="alice", labels=["Node"], ppr_score=0.9, description="Alice"),
        Candidate(id="meeting", labels=["Node"], ppr_score=0.5, description="Meeting"),
        Candidate(id="bob", labels=["Node"], ppr_score=0.1, description="Bob"),
    ]
    monkeypatch.setattr(
        nqe,
        "_predict_rerank",
        lambda question, descriptions: [0.1, 0.2, 0.95],
    )
    ranked = rerank_candidates("chi c'era?", candidates, top_n=2)
    assert [c.id for c in ranked] == ["bob", "meeting"]
    assert "alice" not in {c.id for c in ranked}


def test_assemble_context_never_strips_relation_text():
    rel_block = (
        '[alice] Alice (entity) — relazioni: "lavora presso" → Acme\n'
        "    concetti: leadership"
    )
    candidates = [
        Candidate(id="alice", labels=["Node"], ppr_score=1.0, description=rel_block),
        Candidate(id="other", labels=["Node"], ppr_score=0.5, description="x" * 50),
    ]
    kept = assemble_context(candidates, max_chars=len(rel_block) + 10)
    assert kept[0].description == rel_block
    assert "lavora presso" in kept[0].description


def test_engine_has_no_unfiltered_scan_or_sync_project():
    compact = " ".join(ENGINE_SOURCE.split())
    assert "MATCH (n:Node) RETURN n" not in compact
    assert "gds.graph.project" not in nqe.PPR_STREAM_CYPHER
    assert "gds.graph.project" not in nqe.NODE_VECTOR_CYPHER
    assert ":Fact" not in ENGINE_SOURCE
    assert ":Chunk" not in ENGINE_SOURCE
    assert "ensure_ppr_projection" in ENGINE_SOURCE
    assert "refresh_ppr_projection" not in ENGINE_SOURCE


def test_seed_and_ppr_queries_exclude_merged_into():
    assert "merged_into IS NULL" in nqe.NODE_VECTOR_CYPHER
    assert "merged_into IS NULL" in nqe.RELATION_VECTOR_CYPHER
    assert "merged_into IS NULL" in nqe.NODE_CONCEPT_FULLTEXT_CYPHER
    assert "merged_into IS NULL" in nqe.DESCRIBE_CYPHER
    assert "merged_into IS NULL" in nqe.SEED_FALLBACK_CYPHER


@pytest.mark.asyncio
async def test_question_on_entity_fills_nodes_used():
    session = _alice_bob_session()
    response = await run_node_query(session, "chi è Alice?")
    assert isinstance(response, NodeQueryResponse)
    assert {n.id for n in response.nodes_used} >= {"alice"}
    assert "Alice" in " ".join(n.name for n in response.nodes_used)
    assert "Alice" in response.answer or response.cited_node_ids


@pytest.mark.asyncio
async def test_ppr_mediated_event_includes_bob():
    session = _alice_bob_session()
    response = await run_node_query(session, "chi è Alice?")
    assert "bob" in {n.id for n in response.nodes_used}
    ppr_calls = [kw for cy, kw in session.calls if "gds.pageRank.stream" in cy]
    assert ppr_calls
    assert "alice" in ppr_calls[0]["seedIds"]
    assert not any("gds.graph.project" in cy for cy, _kw in session.calls)


@pytest.mark.asyncio
async def test_relation_only_query_fills_nodes_used(monkeypatch):
    monkeypatch.setattr(nqe, "ENABLE_NODE_VECTOR", False)
    monkeypatch.setattr(nqe, "ENABLE_CONCEPT_VECTOR", False)
    monkeypatch.setattr(nqe, "ENABLE_NODE_CONCEPT_FULLTEXT", False)

    async def fake_llm(system, user, model, temperature=0, job_id=None):
        return NodeQueryAnswer(answer="Mario ha fondato Acme.", cited_node_ids=["mario"])

    monkeypatch.setattr(nqe, "call_structured", fake_llm)

    session = DispatchSession()
    session.on(
        "relation_embedding",
        [{"start_id": "mario", "end_id": "acme", "score": 0.91}],
    )
    session.on(
        "relation_fulltext",
        [{"start_id": "mario", "end_id": "acme", "score": 4.2}],
    )
    session.on(EXISTS_CYPHER, [{"exists": True}])
    session.on(
        "gds.pageRank.stream",
        [
            {"id": "mario", "labels": ["Node"], "score": 0.8},
            {"id": "acme", "labels": ["Node"], "score": 0.7},
        ],
    )
    session.on(
        "OPTIONAL MATCH (n)-[r_out:Relation]->(out)",
        [
            _describe_row(
                node_id="mario",
                name="Mario",
                node_type="entity",
                labels=["Node"],
                out_rels=[{"rel": "ha fondato", "name": "Acme", "id": "acme"}],
            ),
            _describe_row(
                node_id="acme",
                name="Acme",
                node_type="entity",
                labels=["Node"],
                in_rels=[{"rel": "ha fondato", "name": "Mario", "id": "mario"}],
            ),
        ],
    )
    response = await run_node_query(session, "chi ha fondato")
    assert response.nodes_used
    assert {n.id for n in response.nodes_used} >= {"mario", "acme"}
    vector_indexes = [
        cy
        for cy, _kw in session.calls
        if "vector.queryNodes" in cy or "vector.queryRelationships" in cy
    ]
    assert any("relation_embedding" in cy for cy in vector_indexes)
    assert not any("node_embedding" in cy for cy in vector_indexes)
    assert not any("concept_embedding" in cy for cy in vector_indexes)


@pytest.mark.asyncio
async def test_merged_node_never_enters_nodes_used():
    session = DispatchSession()
    session.on("node_embedding", [{"id": "alice", "score": 0.9}])
    session.on(EXISTS_CYPHER, [{"exists": True}])
    session.on(
        "gds.pageRank.stream",
        [
            {"id": "alice", "labels": ["Node"], "score": 0.9},
            {"id": "alice-dup", "labels": ["Node"], "score": 0.8},
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
            )
        ],
    )
    response = await run_node_query(session, "chi è Alice?")
    used_ids = {n.id for n in response.nodes_used}
    assert "alice" in used_ids
    assert "alice-dup" not in used_ids


@pytest.mark.asyncio
async def test_far_query_does_not_seed_from_top_k_below_threshold():
    session = DispatchSession()
    session.on("node_embedding", [{"id": "spurious", "score": 0.2}])
    session.on("concept_embedding", [{"id": "also-spurious", "score": 0.1}])
    session.on(
        "relation_embedding",
        [{"start_id": "a", "end_id": "b", "score": 0.05}],
    )
    response = await run_node_query(session, "zzzz-unrelated-query")
    assert response.nodes_used == []
    assert response.concepts_used == []
    assert "nessuna informazione trovata" in response.answer.lower()
    assert not any("gds.pageRank.stream" in cy for cy, _kw in session.calls)


@pytest.mark.asyncio
async def test_ensure_projection_not_rebuild_when_present():
    session = _alice_bob_session()
    await run_node_query(session, "chi è Alice?")
    assert any("gds.graph.exists" in cy for cy, _kw in session.calls)
    assert not any("gds.graph.drop" in cy for cy, _kw in session.calls)
    assert not any("project.cypher" in cy for cy, _kw in session.calls)


def test_prompt_forbids_ids_and_keeps_relations():
    system, user = nqe.build_node_query_answer_prompt(
        "chi è Alice?",
        [
            '[alice] Alice (entity) — relazioni: "lavora presso" → Acme\n'
            "    concetti: leadership"
        ],
    )
    assert system == nqe.NODE_ANSWER_SYSTEM_PROMPT
    assert "cited_node_ids" in system
    assert "lavora presso" in user
    assert "chi è Alice?" in user
