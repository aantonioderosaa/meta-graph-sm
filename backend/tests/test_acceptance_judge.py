"""Fase 10 acceptance: judge post-batch pass (anti_blur + equivalent_to). No Docker."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from app.core.config import Settings
from app.pipeline.dreaming import run_dreaming_pipeline
from app.pipeline.judge import (
    FIND_BLURRED_RELATIONS_CYPHER,
    FIND_EQUIVALENT_CONCEPT_PAIRS_CYPHER,
    MARK_ABSORBED_CONCEPT_CYPHER,
    MARK_BLURRED_RELATION_CYPHER,
    MERGE_EQUIVALENT_TO_CYPHER,
    MERGE_JUDGE_RUN_CYPHER,
    MOVE_ABSORBED_MEMBER_OF_CYPHER,
    JudgeStats,
    cosine,
    run_judge,
    split_blurred_relation,
)
from tests.test_dreaming_nodes import FakeDriver, FakeSession

JOB_ID = "job-judge-f10"


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


class JudgeGraph:
    """Dedicated in-memory graph for one judge scenario (not a shared fixture)."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.concepts: dict[str, dict] = {}
        self.relations: list[dict] = []
        self.member_of: dict[str, dict] = {}
        self.famiglia: list[dict] = []
        self.judge_runs: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []

    def add_node(self, node_id: str, **props) -> None:
        self.nodes[node_id] = {"id": node_id, **props}

    def add_concept(self, concept_id: str, **props) -> None:
        self.concepts[concept_id] = {"id": concept_id, **props}

    def add_relation(self, src_id: str, dst_id: str, **props) -> None:
        rel_id = props.get("id") or f"rel-{src_id}-{dst_id}-{len(self.relations)}"
        row = {"id": rel_id, "src": src_id, "dst": dst_id, "is_latest": True, **props}
        row["id"] = rel_id
        self.relations.append(row)

    def set_member_of(self, node_id: str, concept_id: str, **props) -> None:
        self.member_of[node_id] = {"concept_id": concept_id, **props}

    def add_famiglia(self, src_id: str, rel_type: str, dst_id: str, **props) -> None:
        self.famiglia.append(
            {"src": src_id, "dst": dst_id, "rel_type": rel_type, "props": dict(props)}
        )

    def _has_famiglia(self, a: str, b: str, rel_type: str) -> bool:
        for edge in self.famiglia:
            if edge["rel_type"] != rel_type:
                continue
            if {edge["src"], edge["dst"]} == {a, b}:
                return True
        return False

    async def run(self, cypher, **kwargs):
        self.calls.append((cypher, kwargs))
        if cypher == FIND_BLURRED_RELATIONS_CYPHER:
            rows = []
            for rel in self.relations:
                wa = list(rel.get("witnesses_a") or [])
                wb = list(rel.get("witnesses_b") or [])
                if len(wa) > 1 and len(wb) > 1:
                    rows.append(
                        {
                            "rel_id": rel["id"],
                            "witnesses_a": wa,
                            "witnesses_b": wb,
                        }
                    )
            return FakeResult(rows)

        if cypher == MARK_BLURRED_RELATION_CYPHER:
            rel_id = kwargs["rel_id"]
            for rel in self.relations:
                if rel["id"] == rel_id:
                    rel["needs_reverify"] = True
                    rel["witnesses_a"] = []
                    rel["witnesses_b"] = []
            return FakeResult([])

        if cypher == FIND_EQUIVALENT_CONCEPT_PAIRS_CYPHER:
            ids = sorted(self.concepts)
            rows = []
            for i, id_a in enumerate(ids):
                a = self.concepts[id_a]
                if not a.get("promoted"):
                    continue
                for id_b in ids[i + 1 :]:
                    b = self.concepts[id_b]
                    if not b.get("promoted"):
                        continue
                    if a.get("kernel_category") != b.get("kernel_category"):
                        continue
                    if a.get("parent_uri") != b.get("parent_uri"):
                        continue
                    if a.get("parent_uri") is None:
                        continue
                    if self._has_famiglia(id_a, id_b, "EQUIVALENT_TO"):
                        continue
                    rows.append(
                        {
                            "id_a": id_a,
                            "embedding_a": a.get("embedding"),
                            "id_b": id_b,
                            "embedding_b": b.get("embedding"),
                        }
                    )
            return FakeResult(rows)

        if cypher == MERGE_EQUIVALENT_TO_CYPHER:
            src_id, dst_id = kwargs["src_id"], kwargs["dst_id"]
            if not self._has_famiglia(src_id, dst_id, "EQUIVALENT_TO"):
                self.add_famiglia(src_id, "EQUIVALENT_TO", dst_id)
            return FakeResult([])

        if cypher == MOVE_ABSORBED_MEMBER_OF_CYPHER:
            absorbed_id = kwargs["absorbed_id"]
            survivor_id = kwargs["survivor_id"]
            for node_id, home in list(self.member_of.items()):
                if home.get("concept_id") == absorbed_id:
                    self.member_of[node_id] = {
                        "concept_id": survivor_id,
                        "absorbed_from": absorbed_id,
                    }
            if absorbed_id in self.concepts:
                self.concepts[absorbed_id]["absorbed_from"] = survivor_id
            return FakeResult([])

        if cypher == MARK_ABSORBED_CONCEPT_CYPHER:
            absorbed_id = kwargs["absorbed_id"]
            if absorbed_id in self.concepts:
                self.concepts[absorbed_id]["absorbed_from"] = kwargs["survivor_id"]
            return FakeResult([])

        if cypher == MERGE_JUDGE_RUN_CYPHER:
            self.judge_runs[kwargs["id"]] = dict(kwargs)
            return FakeResult([])

        return FakeResult([])


def _vec_at_cosine(target: float) -> list[float]:
    y = math.sqrt(max(0.0, 1.0 - target * target))
    return [target, y]


async def _async_zero(*_args, **_kwargs) -> int:
    return 0


def test_flag_defaults():
    assert Settings.model_fields["ENABLE_JUDGE"].default is True
    assert Settings.model_fields["BACKBONE_COLLAPSE_THRESHOLD"].default == 0.90


def test_judge_has_no_new_write_primitives():
    text = Path(__file__).resolve().parents[1].joinpath("app/pipeline/judge.py").read_text(
        encoding="utf-8"
    )
    assert "merge_nodes" not in text
    assert "CREATE (n:Relation" not in text
    assert "JudgeStats" in text
    assert "EQUIVALENT_TO" in text


def test_split_blurred_relation_cartesian():
    pairs = split_blurred_relation(
        {"witnesses_a": ["wa1", "wa2"], "witnesses_b": ["wb1", "wb2"]}
    )
    assert pairs == [("wa1", "wb1"), ("wa1", "wb2"), ("wa2", "wb1"), ("wa2", "wb2")]
    assert split_blurred_relation({"witnesses_a": ["only"], "witnesses_b": ["a", "b"]}) == []


@pytest.mark.asyncio
async def test_anti_blur_splits_and_requeues():
    graph = JudgeGraph()
    graph.add_node("head")
    graph.add_node("tail")
    graph.add_relation(
        "head",
        "tail",
        id="blur-1",
        witnesses_a=["wa1", "wa2"],
        witnesses_b=["wb1", "wb2"],
        relation="plays_for",
    )
    requeued: list[tuple[str, str]] = []

    async def capture(wa: str, wb: str) -> None:
        requeued.append((wa, wb))

    stats = await run_judge(graph, JOB_ID, on_requeue=capture)

    assert stats.anti_blur >= 1
    assert len(requeued) == 4
    assert ("wa1", "wb1") in requeued
    blur = next(rel for rel in graph.relations if rel["id"] == "blur-1")
    assert blur["needs_reverify"] is True
    assert blur["witnesses_a"] == []
    assert blur["witnesses_b"] == []
    assert JOB_ID in graph.judge_runs


@pytest.mark.asyncio
async def test_equivalent_to_collapses_and_moves_member_of():
    graph = JudgeGraph()
    emb_a = [1.0, 0.0]
    emb_b = _vec_at_cosine(0.95)
    assert cosine(emb_a, emb_b) == pytest.approx(0.95)
    graph.add_concept(
        "concept-a",
        promoted=True,
        kernel_category="Agente",
        parent_uri="parent-1",
        embedding=emb_a,
        name="calciatore",
    )
    graph.add_concept(
        "concept-b",
        promoted=True,
        kernel_category="Agente",
        parent_uri="parent-1",
        embedding=emb_b,
        name="giocatore",
    )
    graph.add_node("n-mario", name="Mario")
    graph.set_member_of("n-mario", "concept-b")

    stats = await run_judge(graph, JOB_ID)

    assert stats.equivalent_to >= 1
    assert graph._has_famiglia("concept-a", "concept-b", "EQUIVALENT_TO")
    assert "concept-b" in graph.concepts
    home = graph.member_of["n-mario"]
    assert home["concept_id"] == "concept-a"
    assert home["absorbed_from"] == "concept-b"
    assert graph.concepts["concept-b"]["absorbed_from"] == "concept-a"


@pytest.mark.asyncio
async def test_dreaming_pipeline_stubbed_judge_emits_complete(monkeypatch):
    published: list[dict] = []

    async def fake_nodes(*_args, **_kwargs) -> set[str]:
        return set()

    async def fake_judge(*_args, **_kwargs) -> JudgeStats:
        return JudgeStats()

    async def spy_publish(job_id, stage, event, payload):
        published.append({"stage": stage, "event": event, "payload": payload})

    monkeypatch.setattr("app.pipeline.dreaming.get_driver", lambda: FakeDriver())
    monkeypatch.setattr("app.pipeline.dreaming._run_node_phases", fake_nodes)
    monkeypatch.setattr(
        "app.pipeline.dreaming.reconcile.reconcile_scoped_relations",
        _async_zero,
    )
    monkeypatch.setattr("app.pipeline.dreaming.run_judge", fake_judge)
    monkeypatch.setattr(
        "app.pipeline.dreaming.node_ppr_projection.refresh_ppr_projection",
        _async_zero,
    )
    monkeypatch.setattr("app.pipeline.dreaming.event_bus.publish", spy_publish)
    monkeypatch.setattr("app.pipeline.dreaming.get_token_usage", lambda _job: 0)

    await run_dreaming_pipeline(JOB_ID)

    assert any(m["event"] == "pipeline_complete" for m in published)
    assert any(m["event"] == "judge_complete" for m in published)


@pytest.mark.asyncio
async def test_dreaming_pipeline_real_judge_writes_judgerun(monkeypatch):
    published: list[dict] = []
    judge_session = FakeSession()

    async def fake_nodes(*_args, **_kwargs) -> set[str]:
        return set()

    async def spy_publish(job_id, stage, event, payload):
        published.append({"stage": stage, "event": event, "payload": payload})

    driver = FakeDriver([judge_session, FakeSession()])
    monkeypatch.setattr("app.pipeline.dreaming.get_driver", lambda: driver)
    monkeypatch.setattr("app.pipeline.dreaming._run_node_phases", fake_nodes)
    monkeypatch.setattr(
        "app.pipeline.dreaming.reconcile.reconcile_scoped_relations",
        _async_zero,
    )
    monkeypatch.setattr(
        "app.pipeline.dreaming.node_ppr_projection.refresh_ppr_projection",
        _async_zero,
    )
    monkeypatch.setattr("app.pipeline.dreaming.event_bus.publish", spy_publish)
    monkeypatch.setattr("app.pipeline.dreaming.get_token_usage", lambda _job: 0)

    await run_dreaming_pipeline(JOB_ID)

    assert any(m["event"] == "pipeline_complete" for m in published)
    assert any(call[0] == MERGE_JUDGE_RUN_CYPHER for call in judge_session.calls)
    kwargs = next(kw for cy, kw in judge_session.calls if cy == MERGE_JUDGE_RUN_CYPHER)
    assert kwargs["id"] == JOB_ID
    assert kwargs["batch_id"] == JOB_ID
    assert kwargs["anti_blur"] == 0


@pytest.mark.asyncio
async def test_dreaming_pipeline_judge_failure_still_completes(monkeypatch):
    published: list[dict] = []

    async def fake_nodes(*_args, **_kwargs) -> set[str]:
        return set()

    async def boom_judge(*_args, **_kwargs):
        raise RuntimeError("judge exploded")

    async def spy_publish(job_id, stage, event, payload):
        published.append({"stage": stage, "event": event, "payload": payload})

    monkeypatch.setattr("app.pipeline.dreaming.get_driver", lambda: FakeDriver())
    monkeypatch.setattr("app.pipeline.dreaming._run_node_phases", fake_nodes)
    monkeypatch.setattr(
        "app.pipeline.dreaming.reconcile.reconcile_scoped_relations",
        _async_zero,
    )
    monkeypatch.setattr("app.pipeline.dreaming.run_judge", boom_judge)
    monkeypatch.setattr(
        "app.pipeline.dreaming.node_ppr_projection.refresh_ppr_projection",
        _async_zero,
    )
    monkeypatch.setattr("app.pipeline.dreaming.event_bus.publish", spy_publish)
    monkeypatch.setattr("app.pipeline.dreaming.get_token_usage", lambda _job: 0)

    await run_dreaming_pipeline(JOB_ID)

    assert any(m["event"] == "pipeline_complete" for m in published)
    failed = [m for m in published if m["event"] == "llm_call_failed"]
    assert any(m["stage"] == "judge" for m in failed)
