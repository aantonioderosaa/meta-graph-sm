"""Unit tests for Node stages in the dreaming cycle (Macrotask 5). No Docker."""

from __future__ import annotations

import pytest

from app.core.llm_client import LLMValidationError
from app.pipeline.dreaming import (
    FIND_FRESH_ENTITIES_CYPHER,
    MARK_NODE_DREAMED_CYPHER,
    _classify_entity_relations,
    _resolve_and_classify_events,
    _resolve_fresh_entities,
    _run_node_phases,
    run_dreaming_pipeline,
)

JOB_ID = "job-m5-nodes"
EMBEDDING = [0.1, 0.2, 0.3]


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

    async def consume(self):
        return None


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


class _SessionCM:
    def __init__(self, session: FakeSession):
        self._session = session

    async def __aenter__(self) -> FakeSession:
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class FakeDriver:
    def __init__(self, sessions: list[FakeSession] | None = None):
        self._preset = list(sessions) if sessions is not None else None
        self.created: list[FakeSession] = []

    def session(self) -> _SessionCM:
        if self._preset is not None:
            sess = self._preset.pop(0)
        else:
            sess = FakeSession()
        self.created.append(sess)
        return _SessionCM(sess)


def _compact(cypher: str) -> str:
    return " ".join(cypher.split())


def _three_entities() -> list[dict]:
    return [
        {"id": "e-alice-1", "name": "Alice", "embedding": EMBEDDING},
        {"id": "e-alice-2", "name": "Alice dup", "embedding": EMBEDDING},
        {"id": "e-bob", "name": "Bob", "embedding": EMBEDDING},
    ]


CANON = "e-alice-1"


async def _fake_resolve_two_dups(_session, node_id, node_type, name, embedding, job_id):
    assert node_type == "entity"
    assert job_id == JOB_ID
    if node_id in {"e-alice-1", "e-alice-2"}:
        return CANON
    return node_id


@pytest.mark.asyncio
async def test_phase1_runs_before_phase2_gather(monkeypatch):
    log: list[str] = []

    async def phase1(_session, job_id: str) -> set[str]:
        assert job_id == JOB_ID
        log.append("p1")
        return {"e1"}

    async def backbone(_session, job_id: str) -> int:
        assert job_id == JOB_ID
        assert "p1" in log
        assert "p2_rel" not in log
        assert "p2_ev" not in log
        log.append("bb")
        return 0

    async def promote(_session, job_id: str, **_kwargs) -> int:
        assert job_id == JOB_ID
        assert "bb" in log
        assert "p2_rel" not in log
        assert "p2_ev" not in log
        log.append("pr")
        return 0

    async def rels(_session, job_id: str, touched: set[str]) -> int:
        assert "p1" in log
        assert "bb" in log
        log.append("p2_rel")
        assert touched == {"e1"}
        return 1

    async def events(_session, job_id: str) -> set[str]:
        assert "p1" in log
        assert "bb" in log
        log.append("p2_ev")
        return {"ev1"}

    monkeypatch.setattr("app.pipeline.dreaming._resolve_fresh_entities", phase1)
    monkeypatch.setattr("app.pipeline.dreaming.classify_and_grow_backbone", backbone)
    monkeypatch.setattr("app.pipeline.dreaming.promote_clusters", promote)
    monkeypatch.setattr("app.pipeline.dreaming._classify_entity_relations", rels)
    monkeypatch.setattr("app.pipeline.dreaming._resolve_and_classify_events", events)

    touched = await _run_node_phases(FakeDriver(), JOB_ID)

    assert log[0] == "p1"
    assert log[1] == "bb"
    assert log[2] == "pr"
    assert "p2_rel" in log
    assert "p2_ev" in log
    assert set(log[3:]) == {"p2_rel", "p2_ev"}
    assert touched == {"e1", "ev1"}


@pytest.mark.asyncio
async def test_node_merged_published_when_canon_differs(monkeypatch):
    session = FakeSession()
    session.enqueue(_three_entities())
    monkeypatch.setattr(
        "app.pipeline.dreaming.node_resolution.resolve_node",
        _fake_resolve_two_dups,
    )
    published: list[dict] = []

    async def spy_publish(job_id, stage, event, payload):
        published.append(
            {"job_id": job_id, "stage": stage, "event": event, "payload": payload}
        )

    monkeypatch.setattr("app.pipeline.dreaming.event_bus.publish", spy_publish)

    touched = await _resolve_fresh_entities(session, JOB_ID)

    merges = [m for m in published if m["event"] == "node_merged"]
    assert len(merges) == 1
    assert merges[0]["stage"] == "entity_resolution"
    assert merges[0]["payload"] == {"dup_id": "e-alice-2", "canon_id": CANON}
    assert touched == {"e-alice-1", "e-alice-2", "e-bob"}


@pytest.mark.asyncio
async def test_dreamed_cypher_includes_processed_ids(monkeypatch):
    session = FakeSession()
    session.enqueue(_three_entities())
    monkeypatch.setattr(
        "app.pipeline.dreaming.node_resolution.resolve_node",
        _fake_resolve_two_dups,
    )
    monkeypatch.setattr("app.pipeline.dreaming.event_bus.publish", _noop_publish)

    await _resolve_fresh_entities(session, JOB_ID)

    assert session.calls[0][0] == FIND_FRESH_ENTITIES_CYPHER
    mark_calls = [call for call in session.calls if call[0] == MARK_NODE_DREAMED_CYPHER]
    marked: set[str] = set()
    for _cypher, kwargs in mark_calls:
        marked.update(kwargs["node_ids"])
    assert marked == {"e-alice-1", "e-alice-2", "e-bob"}
    compact = _compact(MARK_NODE_DREAMED_CYPHER)
    assert "MATCH (n:Node {id: nid})" in compact
    assert "n.dreamed = true" in compact


@pytest.mark.asyncio
async def test_empty_fresh_entities_skips_resolve_node(monkeypatch):
    session = FakeSession()
    session.enqueue([])
    calls: list[str] = []

    async def boom_resolve(*_args, **_kwargs):
        calls.append("resolve_node")
        raise AssertionError("resolve_node must not run when there are no fresh entities")

    monkeypatch.setattr("app.pipeline.dreaming.node_resolution.resolve_node", boom_resolve)

    touched = await _resolve_fresh_entities(session, JOB_ID)

    assert touched == set()
    assert calls == []
    assert session.calls[0][0] == FIND_FRESH_ENTITIES_CYPHER


@pytest.mark.asyncio
async def test_llm_failure_isolated_continues(monkeypatch):
    session = FakeSession()
    session.enqueue(_three_entities())
    published: list[dict] = []

    async def flaky_resolve(_session, node_id, *_args, **_kwargs):
        if node_id == "e-alice-2":
            raise LLMValidationError("bad json")
        return node_id

    async def spy_publish(job_id, stage, event, payload):
        published.append({"stage": stage, "event": event, "payload": payload})

    monkeypatch.setattr("app.pipeline.dreaming.node_resolution.resolve_node", flaky_resolve)
    monkeypatch.setattr("app.pipeline.dreaming.event_bus.publish", spy_publish)

    touched = await _resolve_fresh_entities(session, JOB_ID)

    failures = [m for m in published if m["event"] == "llm_call_failed"]
    assert len(failures) == 1
    assert failures[0]["stage"] == "entity_resolution"
    assert failures[0]["payload"]["item_id"] == "e-alice-2"
    assert touched == {"e-alice-1", "e-bob"}
    mark_calls = [call for call in session.calls if call[0] == MARK_NODE_DREAMED_CYPHER]
    marked: set[str] = set()
    for _cypher, kwargs in mark_calls:
        marked.update(kwargs["node_ids"])
    assert "e-alice-2" not in marked
    assert marked == {"e-alice-1", "e-bob"}


@pytest.mark.asyncio
async def test_event_merge_publishes_and_marks_dreamed(monkeypatch):
    session = FakeSession()
    session.enqueue(
        [
            {"id": "ev-new", "name": "Kickoff", "embedding": EMBEDDING},
            {"id": "ev-other", "name": "Other", "embedding": EMBEDDING},
        ]
    )
    published: list[dict] = []

    async def fake_resolve_event(_session, event_id, name, embedding, job_id):
        if event_id == "ev-new":
            return "ev-canon"
        return event_id

    async def spy_publish(job_id, stage, event, payload):
        published.append({"stage": stage, "event": event, "payload": payload})

    monkeypatch.setattr(
        "app.pipeline.dreaming.event_relation_resolution.resolve_event",
        fake_resolve_event,
    )
    monkeypatch.setattr("app.pipeline.dreaming.event_bus.publish", spy_publish)

    touched = await _resolve_and_classify_events(session, JOB_ID)

    merges = [m for m in published if m["event"] == "node_merged"]
    assert len(merges) == 1
    assert merges[0]["stage"] == "event_resolution_and_classification"
    assert merges[0]["payload"] == {"dup_id": "ev-new", "canon_id": "ev-canon"}
    assert touched == {"ev-new", "ev-canon", "ev-other"}
    mark_calls = [call for call in session.calls if call[0] == MARK_NODE_DREAMED_CYPHER]
    marked: set[str] = set()
    for _cypher, kwargs in mark_calls:
        marked.update(kwargs["node_ids"])
    assert marked == {"ev-new", "ev-canon", "ev-other"}


@pytest.mark.asyncio
async def test_classify_entity_relations_publishes_outcomes(monkeypatch):
    published: list[dict] = []

    async def fake_resolve(
        _session,
        job_id,
        touched_entity_ids,
        *,
        on_classified=None,
        on_error=None,
    ):
        assert job_id == JOB_ID
        assert touched_entity_ids == {"e1"}
        if on_classified is not None:
            await on_classified("updates", "e1", "e2")
            await on_classified("none", "e1", "e3")
        return 2

    async def spy_publish(job_id, stage, event, payload):
        published.append({"stage": stage, "event": event, "payload": payload})

    monkeypatch.setattr(
        "app.pipeline.dreaming.entity_relation_resolution.resolve_fresh_entity_relations",
        fake_resolve,
    )
    monkeypatch.setattr("app.pipeline.dreaming.event_bus.publish", spy_publish)

    count = await _classify_entity_relations(FakeSession(), JOB_ID, {"e1"})

    assert count == 2
    classified = [m for m in published if m["event"] == "node_relation_classified"]
    assert len(classified) == 2
    assert classified[0] == {
        "stage": "entity_relation_classification",
        "event": "node_relation_classified",
        "payload": {"type": "updates", "src": "e1", "tgt": "e2"},
    }


@pytest.mark.asyncio
async def test_run_dreaming_pipeline_calls_node_helpers_and_completes(monkeypatch):
    log: list[str] = []
    published: list[dict] = []

    async def fake_nodes(driver, job_id: str, **_kwargs) -> set[str]:
        log.append("nodes")
        assert job_id == JOB_ID
        return set()

    async def fake_rel_reconcile(node_ids: list[str]) -> int:
        log.append("rel_reconcile")
        assert node_ids == []
        return 0

    async def fake_refresh(_session) -> None:
        log.append("ppr")

    async def fake_judge(_session, job_id: str, **_kwargs):
        from app.pipeline.judge import JudgeStats

        log.append("judge")
        assert job_id == JOB_ID
        return JudgeStats()

    async def spy_publish(job_id, stage, event, payload):
        published.append({"stage": stage, "event": event, "payload": payload})

    monkeypatch.setattr("app.pipeline.dreaming.get_driver", lambda: FakeDriver())
    monkeypatch.setattr(
        "app.pipeline.dreaming.reconcile.reconcile_scoped_relations",
        fake_rel_reconcile,
    )
    monkeypatch.setattr("app.pipeline.dreaming._run_node_phases", fake_nodes)
    monkeypatch.setattr("app.pipeline.dreaming.run_judge", fake_judge)
    monkeypatch.setattr(
        "app.pipeline.dreaming.node_ppr_projection.refresh_ppr_projection",
        fake_refresh,
    )
    monkeypatch.setattr("app.pipeline.dreaming.event_bus.publish", spy_publish)
    monkeypatch.setattr("app.pipeline.dreaming.get_token_usage", lambda _job: 0)

    stats = await run_dreaming_pipeline(JOB_ID)

    assert log == ["nodes", "rel_reconcile", "judge", "ppr"]
    complete = [m for m in published if m["event"] == "pipeline_complete"]
    assert len(complete) == 1
    assert complete[0]["stage"] == "done"
    payload_stats = complete[0]["payload"]["stats"]
    assert payload_stats["node_drift_count"] == 0
    assert "facts_processed" not in payload_stats
    assert "groups" not in payload_stats
    judge_done = [m for m in published if m["event"] == "judge_complete"]
    assert len(judge_done) == 1
    assert judge_done[0]["stage"] == "judge"
    drift = [m for m in published if m["event"] == "drift_check"]
    assert len(drift) == 1
    assert drift[0]["stage"] == "reconciliation"
    assert stats.node_drift_count == 0


@pytest.mark.asyncio
async def test_empty_fresh_entities_pipeline_reaches_complete(monkeypatch):
    """No fresh entities → resolve_node never called, pipeline_complete still fires."""
    resolve_calls: list[str] = []
    published: list[dict] = []

    async def boom_resolve(*_args, **_kwargs):
        resolve_calls.append("resolve_node")
        raise AssertionError("resolve_node must not run")

    async def spy_publish(job_id, stage, event, payload):
        published.append({"event": event, "payload": payload})

    entity_session = FakeSession()
    entity_session.enqueue([])
    event_session = FakeSession()
    event_session.enqueue([])
    driver = FakeDriver(
        [
            entity_session,  # phase 1
            FakeSession(),  # backbone classification
            FakeSession(),  # promote_clusters
            FakeSession(),  # relations
            event_session,  # events
            FakeSession(),  # judge
            FakeSession(),  # ppr projection refresh
        ]
    )

    monkeypatch.setattr("app.pipeline.dreaming.get_driver", lambda: driver)
    monkeypatch.setattr("app.pipeline.dreaming.node_resolution.resolve_node", boom_resolve)
    monkeypatch.setattr(
        "app.pipeline.dreaming.classify_and_grow_backbone",
        _async_zero,
    )
    monkeypatch.setattr(
        "app.pipeline.dreaming.promote_clusters",
        _async_zero,
    )
    monkeypatch.setattr(
        "app.pipeline.dreaming.reconcile.reconcile_scoped_relations",
        _async_zero,
    )
    monkeypatch.setattr("app.pipeline.dreaming.event_bus.publish", spy_publish)
    monkeypatch.setattr("app.pipeline.dreaming.get_token_usage", lambda _job: 0)

    async def fake_rels(_session, job_id, touched, *, on_classified=None, on_error=None):
        return 0

    monkeypatch.setattr(
        "app.pipeline.dreaming.entity_relation_resolution.resolve_fresh_entity_relations",
        fake_rels,
    )

    await run_dreaming_pipeline(JOB_ID)

    assert resolve_calls == []
    assert any(m["event"] == "pipeline_complete" for m in published)


def test_fresh_entity_cypher_is_scoped():
    compact = _compact(FIND_FRESH_ENTITIES_CYPHER)
    assert "type:'entity'" in compact or "type: 'entity'" in compact
    assert "dreamed:false" in compact or "dreamed: false" in compact
    assert "merged_into IS NULL" in FIND_FRESH_ENTITIES_CYPHER
    assert "MATCH (n:Node) RETURN n" not in compact


async def _noop_publish(*_args, **_kwargs) -> None:
    return None


async def _async_zero(*_args, **_kwargs) -> int:
    return 0
