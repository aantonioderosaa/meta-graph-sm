"""Macrotask 7 named acceptance tests (Node/Concept NL query). No Docker.

Scenarios 1–8 of the query-fatti-vs-entità-eventi plan. Additive: does not
change assertions in test_node_query_engine.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.query import NodeQueryResponse
from app.pipeline import dreaming
from app.pipeline import node_query_engine as nqe
from app.pipeline.dreaming import run_dreaming_pipeline
from app.pipeline.node_ppr_projection import EXISTS_CYPHER
from app.pipeline.node_query_engine import NodeQueryAnswer, run_node_query

BACKEND_ROOT = Path(__file__).resolve().parents[1]
ENGINE_SOURCE = Path(nqe.__file__).read_text(encoding="utf-8")
DREAMING_SOURCE = Path(dreaming.__file__).read_text(encoding="utf-8")


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


class _SessionCM:
    def __init__(self, session: DispatchSession):
        self._session = session

    async def __aenter__(self) -> DispatchSession:
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class FakeDriver:
    def session(self) -> _SessionCM:
        return _SessionCM(DispatchSession())


def _compact(cypher: str) -> str:
    return " ".join(cypher.split())


def _count_cypher(session: DispatchSession, snippet: str) -> int:
    return sum(1 for cy, _kw in session.calls if snippet in cy)


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
    concept_holders: list[dict] | None = None,
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
        "concept_holders": concept_holders or [],
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


def _concept_bridge_session() -> DispatchSession:
    session = DispatchSession()
    session.on("concept_embedding", [{"id": "tech", "score": 0.93}])
    session.on(EXISTS_CYPHER, [{"exists": True}])
    session.on(
        "gds.pageRank.stream",
        [
            {"id": "tech", "labels": ["Concept"], "score": 0.9},
            {"id": "alice", "labels": ["Node"], "score": 0.5},
            {"id": "launch", "labels": ["Node"], "score": 0.4},
        ],
    )
    session.on(
        "OPTIONAL MATCH (n)-[r_out:Relation]->(out)",
        [
            _describe_row(
                node_id="tech",
                name="technology",
                node_type=None,
                labels=["Concept"],
                concept_holders=[
                    {"id": "alice", "name": "Alice"},
                    {"id": "launch", "name": "Product Launch"},
                ],
            ),
            _describe_row(
                node_id="alice",
                name="Alice",
                node_type="entity",
                labels=["Node"],
                concepts=[{"id": "tech", "name": "technology"}],
            ),
            _describe_row(
                node_id="launch",
                name="Product Launch",
                node_type="event",
                labels=["Node"],
                concepts=[{"id": "tech", "name": "technology"}],
            ),
        ],
    )
    session.on(
        "Relation|HAS_CONCEPT",
        [
            {"source": "alice", "target": "tech", "rel_type": "HAS_CONCEPT"},
            {"source": "launch", "target": "tech", "rel_type": "HAS_CONCEPT"},
        ],
    )
    return session


def _mario_acme_relation_session() -> DispatchSession:
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

    async def fake_llm(system, user, model, temperature=0, job_id=None):
        return NodeQueryAnswer(
            answer="Alice partecipa alla riunione.", cited_node_ids=["alice"]
        )

    monkeypatch.setattr(nqe, "call_structured", fake_llm)


async def _mention_names_llm(system, user, model, temperature=0, job_id=None):
    _ = system, model, temperature, job_id
    names = [name for name in ("Alice", "Bob") if name in user]
    cited = [nid for nid in ("alice", "bob") if f"[{nid}]" in user or nid in user]
    answer = (
        f"{' e '.join(names)} partecipano alla Riunione Q3."
        if names
        else "Nessun partecipante nel contesto."
    )
    return NodeQueryAnswer(answer=answer, cited_node_ids=cited)


@pytest.mark.asyncio
async def test_scenario1_entity_question_cites_entity():
    session = _alice_bob_session()
    response = await run_node_query(session, "chi è Alice?")
    used_ids = {n.id for n in response.nodes_used}
    assert "alice" in used_ids
    assert "alice" in response.cited_node_ids


@pytest.mark.asyncio
async def test_scenario2_event_participants_in_answer_and_subgraph(monkeypatch):
    monkeypatch.setattr(nqe, "call_structured", _mention_names_llm)
    session = _alice_bob_session()
    response = await run_node_query(session, "chi c'era alla Riunione Q3?")
    assert "Alice" in response.answer
    assert "Bob" in response.answer
    rel_types = {rel.type for rel in response.subgraph.relationships}
    assert "participates" in rel_types


@pytest.mark.asyncio
async def test_scenario2bis_ppr_reaches_unnamed_co_participant():
    session = _alice_bob_session()
    response = await run_node_query(session, "chi è Alice?")
    assert "bob" in {n.id for n in response.nodes_used}
    assert any("gds.pageRank.stream" in cy for cy, _kw in session.calls)
    assert not any("gds.graph.project" in cy for cy, _kw in session.calls)


@pytest.mark.asyncio
async def test_scenario2ter_relation_predicate_recall(monkeypatch):
    monkeypatch.setattr(nqe, "ENABLE_NODE_VECTOR", False)
    monkeypatch.setattr(nqe, "ENABLE_CONCEPT_VECTOR", False)
    monkeypatch.setattr(nqe, "ENABLE_NODE_CONCEPT_FULLTEXT", False)

    async def fake_llm(system, user, model, temperature=0, job_id=None):
        return NodeQueryAnswer(answer="Mario ha fondato Acme.", cited_node_ids=["mario"])

    monkeypatch.setattr(nqe, "call_structured", fake_llm)

    session = _mario_acme_relation_session()
    response = await run_node_query(session, "chi ha fondato")
    assert response.nodes_used
    assert {n.id for n in response.nodes_used} >= {"mario", "acme"}


@pytest.mark.asyncio
async def test_scenario3_concept_bridge_returns_entity_and_event():
    session = _concept_bridge_session()
    response = await run_node_query(session, "parlami di technology")
    assert response.concepts_used
    assert "tech" in {c.id for c in response.concepts_used}
    types = {n.type for n in response.nodes_used}
    assert "entity" in types
    assert "event" in types
    used_ids = {n.id for n in response.nodes_used}
    assert "alice" in used_ids
    assert "launch" in used_ids


def test_scenario4_node_query_response_has_no_fact_fields():
    assert "nodes_used" in NodeQueryResponse.model_fields
    assert "facts_used" not in NodeQueryResponse.model_fields
    assert ":Fact" not in ENGINE_SOURCE
    assert ":Chunk" not in ENGINE_SOURCE
    import_lines = [
        line.strip()
        for line in ENGINE_SOURCE.splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    assert not any("query_engine" in line for line in import_lines)


@pytest.mark.asyncio
async def test_scenario5_empty_node_graph_ignores_facts():
    session = DispatchSession()
    response = await run_node_query(session, "chi è Alice?")
    assert response.nodes_used == []
    assert response.concepts_used == []
    assert "nessuna informazione trovata" in response.answer.lower()
    joined = "\n".join(cy for cy, _kw in session.calls)
    assert ":Fact" not in joined
    assert ":Chunk" not in joined
    assert not any("gds.pageRank.stream" in cy for cy, _kw in session.calls)


def test_scenario6_fact_query_modules_removed():
    assert not (BACKEND_ROOT / "app" / "pipeline" / "query_engine.py").exists()
    assert not (BACKEND_ROOT / "app" / "api" / "query.py").exists()


@pytest.mark.asyncio
async def test_scenario7_question_path_cost(monkeypatch):
    rerank_calls: list[int] = []
    llm_calls: list[int] = []

    def spy_rerank(question, descriptions):
        rerank_calls.append(len(descriptions))
        return [0.5] * len(descriptions)

    async def spy_llm(system, user, model, temperature=0, job_id=None):
        llm_calls.append(1)
        return NodeQueryAnswer(
            answer="Alice partecipa alla riunione.", cited_node_ids=["alice"]
        )

    monkeypatch.setattr(nqe, "_predict_rerank", spy_rerank)
    monkeypatch.setattr(nqe, "call_structured", spy_llm)

    session = _alice_bob_session()
    await run_node_query(session, "chi è Alice?")

    assert _count_cypher(session, "node_embedding") == 1
    assert _count_cypher(session, "concept_embedding") == 1
    assert _count_cypher(session, "relation_embedding") == 1
    assert _count_cypher(session, "node_concept_fulltext") == 1
    assert _count_cypher(session, "relation_fulltext") == 1
    assert _count_cypher(session, "gds.pageRank.stream") == 1
    assert _count_cypher(session, "gds.graph.project") == 0
    compact_calls = [_compact(cy) for cy, _kw in session.calls]
    assert not any("MATCH (n:Node) RETURN n" in cy for cy in compact_calls)
    assert len(rerank_calls) == 1
    assert llm_calls == [1]


@pytest.mark.asyncio
async def test_scenario8_projection_refresh_once_per_dreaming_not_per_query(monkeypatch):
    session = _alice_bob_session()
    await run_node_query(session, "chi è Alice?")
    await run_node_query(session, "chi è Alice?")
    assert not any("project.cypher" in cy for cy, _kw in session.calls)
    assert not any("gds.graph.drop" in cy for cy, _kw in session.calls)

    live_lines = [
        line
        for line in DREAMING_SOURCE.splitlines()
        if "refresh_ppr_projection" in line and not line.lstrip().startswith("#")
    ]
    assert len(live_lines) == 1

    refresh_calls: list[str] = []

    async def fake_nodes(driver, job_id: str, **_kwargs) -> set[str]:
        _ = driver, job_id
        return set()

    async def fake_rel_reconcile(node_ids: list[str]) -> int:
        _ = node_ids
        return 0

    async def fake_refresh(_session) -> None:
        refresh_calls.append("refresh")

    async def fake_judge(_session, job_id: str, **_kwargs):
        from app.pipeline.judge import JudgeStats

        _ = job_id
        return JudgeStats()

    async def spy_publish(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr("app.pipeline.dreaming.get_driver", lambda: FakeDriver())
    monkeypatch.setattr(
        "app.pipeline.dreaming.reconcile.reconcile_scoped_relations",
        fake_rel_reconcile,
    )
    monkeypatch.setattr("app.pipeline.dreaming._run_node_phases", fake_nodes)
    monkeypatch.setattr(
        "app.pipeline.dreaming.node_ppr_projection.refresh_ppr_projection",
        fake_refresh,
    )
    monkeypatch.setattr("app.pipeline.dreaming.run_judge", fake_judge)
    monkeypatch.setattr("app.pipeline.dreaming.event_bus.publish", spy_publish)
    monkeypatch.setattr("app.pipeline.dreaming.get_token_usage", lambda _job: 0)

    await run_dreaming_pipeline("job-q7-acceptance")
    assert refresh_calls == ["refresh"]
