"""Unit tests for incremental Node dedup (Macrotask 3). No Docker."""

from __future__ import annotations

import pytest

from app.models.node_extraction import NodeDedupResult
from app.pipeline.node_resolution import (
    COLLAPSE_INCOMING_RELATIONS_CYPHER,
    COLLAPSE_OUTGOING_RELATIONS_CYPHER,
    COPY_DERIVED_FROM_CYPHER,
    COPY_HAS_CONCEPT_CYPHER,
    CREATE_OUTGOING_ON_CANON_CYPHER,
    DELETE_DUP_RELATIONS_CYPHER,
    FIND_EXACT_NAME_CYPHER,
    FIND_NODE_CANDIDATES_CYPHER,
    HIGH_CONFIDENCE_SCORE,
    SET_MERGED_INTO_CYPHER,
    NodeCandidate,
    classify_node_duplicate,
    find_node_candidates,
    merge_nodes,
    resolve_node,
)

JOB_ID = "job-resolve-1"
EMBEDDING = [0.1, 0.2, 0.3]


class FakeRel:
    def __init__(self, **props):
        self._props = props

    def items(self):
        return self._props.items()

    def keys(self):
        return self._props.keys()

    def get(self, key, default=None):
        return self._props.get(key, default)

    def __getitem__(self, key):
        return self._props[key]


class FakeResult:
    def __init__(self, records: list[dict]):
        self._records = records

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for record in self._records:
            yield record


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


def _compact(cypher: str) -> str:
    return " ".join(cypher.split())


async def _boom_llm(*_args, **_kwargs):
    raise AssertionError("LLM / call_structured must not be called on this fast-path")


def _spy_merge(monkeypatch) -> list[tuple[str, str]]:
    merges: list[tuple[str, str]] = []

    async def fake_merge(_session, dup_id: str, canon_id: str) -> None:
        merges.append((dup_id, canon_id))

    monkeypatch.setattr("app.pipeline.node_resolution.merge_nodes", fake_merge)
    return merges


@pytest.mark.asyncio
async def test_exact_name_skips_llm_and_merges(monkeypatch):
    session = FakeSession()
    session.enqueue([{"id": "alice-canon", "name": "Alice"}])
    monkeypatch.setattr("app.pipeline.node_resolution.call_structured", _boom_llm)
    merges = _spy_merge(monkeypatch)

    result = await resolve_node(
        session,
        node_id="alice-new",
        node_type="entity",
        name="Alice",
        embedding=EMBEDDING,
        job_id=JOB_ID,
    )

    assert result == "alice-canon"
    assert merges == [("alice-new", "alice-canon")]
    assert len(session.calls) == 1
    cypher, kwargs = session.calls[0]
    assert cypher == FIND_EXACT_NAME_CYPHER
    assert kwargs["name"] == "Alice"
    assert kwargs["type"] == "entity"
    assert kwargs["node_id"] == "alice-new"
    assert not any("vector.queryNodes" in call[0] for call in session.calls)


@pytest.mark.asyncio
async def test_vector_llm_confirms_duplicate(monkeypatch):
    """No exact name; score 0.85 < 0.90 so LLM decides Obama == Barack Obama."""
    session = FakeSession()
    session.enqueue([])
    session.enqueue([{"id": "obama-1", "name": "Barack Obama", "score": 0.85}])

    async def fake_llm(system, user, model, temperature=0, job_id=None):
        assert model is NodeDedupResult
        assert temperature == 0
        assert job_id == JOB_ID
        assert "Obama" in user
        assert "obama-1" in user
        return NodeDedupResult(duplicate_of="obama-1")

    monkeypatch.setattr("app.pipeline.node_resolution.call_structured", fake_llm)
    merges = _spy_merge(monkeypatch)

    result = await resolve_node(
        session,
        node_id="obama-new",
        node_type="entity",
        name="Obama",
        embedding=EMBEDDING,
        job_id=JOB_ID,
    )

    assert result == "obama-1"
    assert merges == [("obama-new", "obama-1")]
    vector_call = next(call for call in session.calls if "vector.queryNodes" in call[0])
    assert vector_call[1]["type"] == "entity"


@pytest.mark.asyncio
async def test_high_confidence_single_candidate_skips_llm(monkeypatch):
    session = FakeSession()
    session.enqueue([])
    session.enqueue([{"id": "alice-1", "name": "Alice Smith", "score": HIGH_CONFIDENCE_SCORE}])
    monkeypatch.setattr("app.pipeline.node_resolution.call_structured", _boom_llm)
    merges = _spy_merge(monkeypatch)

    result = await resolve_node(
        session,
        node_id="alice-new",
        node_type="entity",
        name="Alice S.",
        embedding=EMBEDDING,
        job_id=JOB_ID,
    )

    assert result == "alice-1"
    assert merges == [("alice-new", "alice-1")]


@pytest.mark.asyncio
async def test_two_high_confidence_candidates_use_llm(monkeypatch):
    session = FakeSession()
    session.enqueue([])
    session.enqueue(
        [
            {"id": "a-1", "name": "Alice", "score": 0.92},
            {"id": "a-2", "name": "Alicia", "score": 0.91},
        ]
    )
    llm_calls: list[str] = []

    async def fake_llm(*_args, **_kwargs):
        llm_calls.append("called")
        return NodeDedupResult(duplicate_of="a-1")

    monkeypatch.setattr("app.pipeline.node_resolution.call_structured", fake_llm)
    merges = _spy_merge(monkeypatch)

    result = await resolve_node(
        session,
        node_id="alice-new",
        node_type="entity",
        name="Alice",
        embedding=EMBEDDING,
        job_id=JOB_ID,
    )

    assert llm_calls == ["called"]
    assert result == "a-1"
    assert merges == [("alice-new", "a-1")]


@pytest.mark.asyncio
async def test_no_candidates_returns_node_id_unchanged(monkeypatch):
    session = FakeSession()
    session.enqueue([])
    session.enqueue([])
    monkeypatch.setattr("app.pipeline.node_resolution.call_structured", _boom_llm)
    merges = _spy_merge(monkeypatch)

    result = await resolve_node(
        session,
        node_id="solo-1",
        node_type="entity",
        name="Unique",
        embedding=EMBEDDING,
        job_id=JOB_ID,
    )

    assert result == "solo-1"
    assert merges == []


def test_candidate_cypher_filters_by_type():
    """An event with a near-identical embedding is excluded by candidate.type = $type."""
    assert "candidate.type = $type" in FIND_NODE_CANDIDATES_CYPHER
    assert "type: $type" in FIND_EXACT_NAME_CYPHER
    assert "c.merged_into IS NULL" in FIND_EXACT_NAME_CYPHER


@pytest.mark.asyncio
async def test_vector_query_passes_requested_entity_type():
    session = FakeSession()
    session.enqueue([])
    session.enqueue([])

    await find_node_candidates(
        session,
        node_id="n-1",
        node_type="entity",
        embedding=EMBEDDING,
        name="Alice",
    )

    exact_cypher, exact_kwargs = session.calls[0]
    assert exact_cypher == FIND_EXACT_NAME_CYPHER
    assert exact_kwargs["type"] == "entity"
    assert exact_kwargs["name"] == "Alice"

    vector_cypher, vector_kwargs = session.calls[1]
    assert vector_cypher == FIND_NODE_CANDIDATES_CYPHER
    assert vector_kwargs["type"] == "entity"
    assert "candidate.type = $type" in vector_cypher


def test_no_full_graph_scan_in_candidate_queries():
    assert "db.index.vector.queryNodes" in FIND_NODE_CANDIDATES_CYPHER
    assert "MATCH (n:Node) RETURN n" not in _compact(FIND_NODE_CANDIDATES_CYPHER)
    assert "MATCH (n:Node) RETURN n" not in _compact(FIND_EXACT_NAME_CYPHER)
    exact = FIND_EXACT_NAME_CYPHER
    assert "$name" in exact
    assert "$type" in exact or "c.type" in exact
    assert "merged_into IS NULL" in exact


@pytest.mark.asyncio
async def test_classify_ignores_hallucinated_ids(monkeypatch):
    async def fake_llm(*_args, **_kwargs):
        return NodeDedupResult(duplicate_of="not-in-candidates")

    monkeypatch.setattr("app.pipeline.node_resolution.call_structured", fake_llm)

    result = await classify_node_duplicate(
        "Obama",
        [NodeCandidate(id="obama-1", name="Barack Obama", score=0.85)],
        JOB_ID,
    )

    assert result.duplicate_of is None


@pytest.mark.asyncio
async def test_classify_keeps_id_in_candidate_set(monkeypatch):
    async def fake_llm(*_args, **_kwargs):
        return NodeDedupResult(duplicate_of="obama-1")

    monkeypatch.setattr("app.pipeline.node_resolution.call_structured", fake_llm)

    result = await classify_node_duplicate(
        "Obama",
        [NodeCandidate(id="obama-1", name="Barack Obama", score=0.85)],
        JOB_ID,
    )

    assert result.duplicate_of == "obama-1"


@pytest.mark.asyncio
async def test_merge_nodes_same_id_is_noop():
    session = FakeSession()
    await merge_nodes(session, "same", "same")
    assert session.calls == []


@pytest.mark.asyncio
async def test_merge_nodes_redirects_relation_and_sets_merged_into():
    session = FakeSession()
    session.enqueue(
        [
            {
                "r": FakeRel(relation="works at", normalized_relation=None, is_latest=True),
                "other_id": "node-x",
            }
        ]
    )

    await merge_nodes(session, "dup", "canon")

    create_calls = [
        call
        for call in session.calls
        if "CREATE" in call[0] and ":Relation" in call[0] and "canon" in call[0]
    ]
    assert create_calls, "expected CREATE of equivalent Relation on canon"
    create_cypher, create_kwargs = create_calls[0]
    assert create_cypher == CREATE_OUTGOING_ON_CANON_CYPHER
    assert create_kwargs["canon_id"] == "canon"
    assert create_kwargs["other_id"] == "node-x"
    assert create_kwargs["props"]["relation"] == "works at"
    assert create_kwargs["props"]["normalized_relation"] is None
    assert create_kwargs["props"]["is_latest"] is True

    assert any(
        call[0] == DELETE_DUP_RELATIONS_CYPHER and call[1]["dup_id"] == "dup"
        for call in session.calls
    )
    assert any(
        call[0] == SET_MERGED_INTO_CYPHER
        and call[1]["dup_id"] == "dup"
        and call[1]["canon_id"] == "canon"
        for call in session.calls
    )
    assert any(call[0] == COPY_HAS_CONCEPT_CYPHER for call in session.calls)
    assert any(call[0] == COPY_DERIVED_FROM_CYPHER for call in session.calls)
    assert any(call[0] == COLLAPSE_OUTGOING_RELATIONS_CYPHER for call in session.calls)
    assert any(call[0] == COLLAPSE_INCOMING_RELATIONS_CYPHER for call in session.calls)


def test_merge_cypher_clears_dup_edges_and_collapses_duplicates():
    """After merge: edges live on canon; dup keeps merged_into and zero own edges."""
    assert "CREATE (canon)-[nr:Relation]->(other)" in _compact(CREATE_OUTGOING_ON_CANON_CYPHER)
    assert "DELETE r" in DELETE_DUP_RELATIONS_CYPHER
    assert "HAS_CONCEPT" in COPY_HAS_CONCEPT_CYPHER
    assert "DELETE hc" in COPY_HAS_CONCEPT_CYPHER
    assert "DERIVED_FROM" in COPY_DERIVED_FROM_CYPHER
    assert "DELETE df" in COPY_DERIVED_FROM_CYPHER
    assert "merged_into" in SET_MERGED_INTO_CYPHER
    for collapse in (COLLAPSE_OUTGOING_RELATIONS_CYPHER, COLLAPSE_INCOMING_RELATIONS_CYPHER):
        assert "collect(r)" in collapse
        assert "DELETE r" in collapse
        assert "ORDER BY r.created_at DESC" in collapse
        assert "coalesce(r.normalized_relation, r.relation)" in collapse
