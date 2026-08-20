"""Fase 22: ReAct context agent. FakeSession + fake call_structured. No Docker/OpenAI."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core import event_bus
from app.models.agent_step import AgentStep
from app.pipeline.context_agent import (
    MERGE_AGENT_SEARCH_RUN_CYPHER,
    run_context_agent,
    run_context_agent_for_job,
)
from app.pipeline.context_retrieval import RetrievalHit
from app.pipeline.dreaming import run_dreaming_pipeline
from app.pipeline.judge import JudgeStats
from app.pipeline.pending_hypothesis import (
    READ_HYPOTHESIS_CYPHER,
    RESOLVE_HYPOTHESIS_CYPHER,
    SET_EVIDENCE_GAP_CYPHER,
    create_or_reinforce_hypothesis,
    drain_promoted_queue,
    enqueue_promoted,
    reset_promoted_queue,
)
from tests.test_dreaming_nodes import FakeDriver
from tests.test_pending_hypothesis import HypothesisSession

JOB_ID = "job-f22"
DOC_ID = "doc-f22"
HYP_ID = "hyp-f22"


class FakeResult:
    def __init__(self, records: list[dict] | None = None):
        self._records = records or []

    async def single(self):
        return self._records[0] if self._records else None

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for record in self._records:
            yield record


class AgentSession(HypothesisSession):
    """Hypothesis store plus :AgentSearchRun MERGE capture."""

    def __init__(self):
        super().__init__()
        self.agent_runs: dict[str, dict] = {}

    async def run(self, cypher, **kwargs):
        if cypher == MERGE_AGENT_SEARCH_RUN_CYPHER:
            self.calls.append((cypher, kwargs))
            self.agent_runs[kwargs["id"]] = dict(kwargs)
            return FakeResult([])
        return await HypothesisSession.run(self, cypher, **kwargs)


def _seed_open_hyp(session: AgentSession, **props) -> dict:
    row = {
        "id": HYP_ID,
        "claim_target": "fido",
        "evidence_span": ["Fido è uscito dal cancello."],
        "witness_fragments": ["Fido"],
        "evidence_gap": "predicate without pair",
        "confidence": "high",
        "status": "open",
        "marker_category": None,
        "kind": "t1",
        "origin_doc_id": DOC_ID,
        "origin_doc_count": 1,
        "listen_count": 0,
        "promoted": True,
        **props,
    }
    session.hypotheses[row["id"]] = row
    return row


@pytest.fixture(autouse=True)
def _reset_layer(monkeypatch):
    monkeypatch.setattr("app.pipeline.embeddings.embed", lambda _text: [0.0] * 768)
    event_bus.reset_event_bus()
    reset_promoted_queue()
    yield
    event_bus.reset_event_bus()
    reset_promoted_queue()


def _scripted_llm(steps: list[AgentStep]):
    queue = list(steps)

    async def fake_call_structured(system, user, model, temperature=0, job_id=None):
        assert model is AgentStep
        assert system
        assert user
        if not queue:
            raise AssertionError("call_structured called more times than scripted")
        return queue.pop(0)

    return fake_call_structured


@pytest.mark.asyncio
async def test_promotion_agent_writes_via_write_node_relation(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.ENABLE_CONTEXT_LAYER", True)
    session = AgentSession()
    _seed_open_hyp(session)
    writes: list[dict] = []

    async def spy_write(_session, **kwargs):
        writes.append(kwargs)

    async def fake_search(_session, text, *, k=10):
        assert "Fido" in text or "fido" in text.casefold()
        return [
            RetrievalHit(
                kind="node",
                score=1.0,
                id="n-fido",
                index="node_summary_fulltext",
                name="Fido",
            )
        ]

    monkeypatch.setattr(
        "app.pipeline.context_agent.call_structured",
        _scripted_llm(
            [
                AgentStep(action="search_fulltext", reasoning="cerco Fido", query="Fido"),
                AgentStep(
                    action="conclude",
                    reasoning="testimoni foglia trovati",
                    candidate_ids=["n-fido", "n-fuori"],
                    evidence_span="Fido è uscito dal cancello",
                    witness_source="Fido",
                    witness_target="è uscito dal cancello",
                    relation="è uscito",
                    kernel_parent="Spaziale",
                    doc_id=DOC_ID,
                ),
            ]
        ),
    )
    monkeypatch.setattr("app.pipeline.context_agent.search_fulltext", fake_search)
    monkeypatch.setattr("app.pipeline.ingestion.write_node_relation", spy_write)

    enqueue_promoted(JOB_ID, HYP_ID)
    outcomes = await run_context_agent_for_job(session, JOB_ID)

    assert len(outcomes) == 1
    assert outcomes[0].verdict == "confirmed"
    assert outcomes[0].turns_used == 2
    assert writes
    assert writes[0]["head_id"] == "n-fido"
    assert writes[0]["tail_id"] == "n-fuori"
    assert writes[0]["witness_source"] == "Fido"
    assert writes[0]["witness_target"] == "è uscito dal cancello"
    assert session.hypotheses[HYP_ID]["status"] == "confirmed"
    assert session.agent_runs
    run = next(iter(session.agent_runs.values()))
    assert run["hypothesis_id"] == HYP_ID
    assert run["verdict"] == "confirmed"
    assert run["turns_used"] == 2
    assert drain_promoted_queue(JOB_ID) == []


@pytest.mark.asyncio
async def test_turns_exhausted_fallback_keeps_open_and_writes_run(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.ENABLE_CONTEXT_LAYER", True)
    monkeypatch.setattr("app.core.config.settings.CONTEXT_AGENT_MAX_TURNS", 2)
    session = AgentSession()
    _seed_open_hyp(session, evidence_gap="scope non chiuso")
    llm_calls: list[str] = []

    async def always_search(system, user, model, temperature=0, job_id=None):
        llm_calls.append(user)
        return AgentStep(action="search_fulltext", reasoning="ancora cerco", query="cani")

    async def fake_search(_session, text, *, k=10):
        return []

    monkeypatch.setattr("app.pipeline.context_agent.call_structured", always_search)
    monkeypatch.setattr("app.pipeline.context_agent.search_fulltext", fake_search)

    out = await run_context_agent(session, HYP_ID, job_id=JOB_ID)

    assert out.verdict == "turns_exhausted"
    assert out.turns_used == 2
    assert len(llm_calls) == 2
    assert session.hypotheses[HYP_ID]["status"] == "open"
    gap = session.hypotheses[HYP_ID]["evidence_gap"]
    assert "quali" in gap.casefold()
    assert session.agent_runs
    run = next(iter(session.agent_runs.values()))
    assert run["verdict"] == "turns_exhausted"
    assert run["turns_used"] == 2
    assert "search_fulltext" in run["steps"]


@pytest.mark.asyncio
async def test_flag_off_zero_call_structured_and_no_agent_run(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.ENABLE_CONTEXT_LAYER", False)
    session = AgentSession()
    _seed_open_hyp(session)
    enqueue_promoted(JOB_ID, HYP_ID)
    llm_calls: list[str] = []

    async def boom(*_args, **_kwargs):
        llm_calls.append("called")
        raise AssertionError("call_structured must not run when flag is off")

    monkeypatch.setattr("app.pipeline.context_agent.call_structured", boom)

    outcomes = await run_context_agent_for_job(session, JOB_ID)

    assert outcomes == []
    assert llm_calls == []
    assert session.agent_runs == {}
    assert session.hypotheses[HYP_ID]["status"] == "open"
    # queue is left untouched when the flag is off
    assert drain_promoted_queue(JOB_ID) == [HYP_ID]


@pytest.mark.asyncio
async def test_conclude_without_witnesses_writes_no_s0(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.ENABLE_CONTEXT_LAYER", True)
    session = AgentSession()
    _seed_open_hyp(session)
    writes: list[dict] = []

    async def spy_write(_session, **kwargs):
        writes.append(kwargs)

    monkeypatch.setattr(
        "app.pipeline.context_agent.call_structured",
        _scripted_llm(
            [
                AgentStep(
                    action="conclude",
                    reasoning="non ho testimoni foglia",
                    candidate_ids=["n-fido", "n-fuori"],
                    evidence_span="i cani sono usciti",
                )
            ]
        ),
    )
    monkeypatch.setattr("app.pipeline.ingestion.write_node_relation", spy_write)

    out = await run_context_agent(session, HYP_ID, job_id=JOB_ID)

    assert out.verdict == "insufficient_evidence"
    assert writes == []
    assert session.hypotheses[HYP_ID]["status"] == "open"
    assert "quali" in session.hypotheses[HYP_ID]["evidence_gap"].casefold()
    assert session.agent_runs
    assert next(iter(session.agent_runs.values()))["verdict"] == "insufficient_evidence"


@pytest.mark.asyncio
async def test_llm_error_fallback_never_raises(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.ENABLE_CONTEXT_LAYER", True)
    session = AgentSession()
    _seed_open_hyp(session)

    async def boom(*_args, **_kwargs):
        raise RuntimeError("openai down")

    monkeypatch.setattr("app.pipeline.context_agent.call_structured", boom)

    out = await run_context_agent(session, HYP_ID, job_id=JOB_ID)

    assert out.verdict == "error"
    assert session.hypotheses[HYP_ID]["status"] == "open"
    assert session.agent_runs
    assert next(iter(session.agent_runs.values()))["verdict"] == "error"


@pytest.mark.asyncio
async def test_conclude_quantifier_uses_f21_helper(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.ENABLE_CONTEXT_LAYER", True)
    session = AgentSession()
    _seed_open_hyp(session, marker_category="quantifier", claim_target="cane")
    called: list[str] = []

    async def fake_quantifier(_session, chunk, concept_hint=None):
        called.append(concept_hint or "")
        from app.pipeline.quantifier_events import QuantifierScopeResult

        return QuantifierScopeResult(
            closed=True,
            event_id="ev-out",
            member_ids=("n-fido", "n-rex"),
            hypothesis_id=None,
            genre_hint="cane",
        )

    monkeypatch.setattr(
        "app.pipeline.context_agent.call_structured",
        _scripted_llm(
            [
                AgentStep(
                    action="conclude",
                    reasoning="scope chiuso sui tre cani",
                    evidence_span="Tutti i cani sono usciti.",
                    doc_id=DOC_ID,
                )
            ]
        ),
    )
    monkeypatch.setattr(
        "app.pipeline.quantifier_events.resolve_quantifier_scope", fake_quantifier
    )

    out = await run_context_agent(session, HYP_ID, job_id=JOB_ID)

    assert called == ["cane"]
    assert out.verdict == "confirmed"


@pytest.mark.asyncio
async def test_promotion_enqueues_for_agent(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.ENABLE_CONTEXT_LAYER", True)
    session = HypothesisSession()
    rec = await create_or_reinforce_hypothesis(
        session,
        claim_target="fido-rex",
        evidence_span="Fido è uscito dal cancello.",
        named_witnesses=["Fido"],
        doc_id=DOC_ID,
        job_id=JOB_ID,
    )
    assert rec is not None
    assert rec["promoted"] is True
    assert rec["id"] in drain_promoted_queue(JOB_ID)


@pytest.mark.asyncio
async def test_empty_queue_zero_agent_calls(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.ENABLE_CONTEXT_LAYER", True)
    session = AgentSession()
    llm_calls: list[str] = []

    async def boom(*_args, **_kwargs):
        llm_calls.append("called")
        raise AssertionError("empty queue must not call the LLM")

    monkeypatch.setattr("app.pipeline.context_agent.call_structured", boom)
    outcomes = await run_context_agent_for_job(session, JOB_ID)
    assert outcomes == []
    assert llm_calls == []
    assert session.agent_runs == {}


@pytest.mark.asyncio
async def test_dreaming_hook_runs_agent_after_judge_when_flag_on(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.ENABLE_CONTEXT_LAYER", True)
    log: list[str] = []
    agent_jobs: list[str] = []

    async def fake_nodes(driver, job_id: str, **_kwargs) -> set[str]:
        log.append("nodes")
        return set()

    async def fake_rel_reconcile(node_ids: list[str]) -> int:
        log.append("rel_reconcile")
        return 0

    async def fake_judge(_session, job_id: str, **_kwargs):
        log.append("judge")
        return JudgeStats()

    async def spy_agent(session, job_id: str):
        log.append("agent")
        agent_jobs.append(job_id)
        assert "judge" in log
        assert "ppr" not in log
        return []

    async def fake_refresh(_session) -> None:
        log.append("ppr")

    enqueue_promoted(JOB_ID, HYP_ID)
    monkeypatch.setattr("app.pipeline.dreaming.get_driver", lambda: FakeDriver())
    monkeypatch.setattr("app.pipeline.dreaming._run_node_phases", fake_nodes)
    monkeypatch.setattr(
        "app.pipeline.dreaming.reconcile.reconcile_scoped_relations", fake_rel_reconcile
    )
    monkeypatch.setattr("app.pipeline.dreaming.run_judge", fake_judge)
    monkeypatch.setattr("app.pipeline.dreaming.run_context_agent_for_job", spy_agent)
    monkeypatch.setattr(
        "app.pipeline.dreaming.node_ppr_projection.refresh_ppr_projection", fake_refresh
    )
    monkeypatch.setattr("app.pipeline.dreaming.event_bus.publish", _noop_publish)
    monkeypatch.setattr("app.pipeline.dreaming.get_token_usage", lambda _job: 0)

    await run_dreaming_pipeline(JOB_ID)

    assert log == ["nodes", "rel_reconcile", "judge", "agent", "ppr"]
    assert agent_jobs == [JOB_ID]


@pytest.mark.asyncio
async def test_dreaming_hook_flag_off_does_not_call_agent(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.ENABLE_CONTEXT_LAYER", False)
    log: list[str] = []

    async def fake_nodes(driver, job_id: str, **_kwargs) -> set[str]:
        log.append("nodes")
        return set()

    async def fake_rel_reconcile(node_ids: list[str]) -> int:
        log.append("rel_reconcile")
        return 0

    async def fake_judge(_session, job_id: str, **_kwargs):
        log.append("judge")
        return JudgeStats()

    async def boom_agent(*_args, **_kwargs):
        raise AssertionError("context agent must not run when flag is off")

    async def fake_refresh(_session) -> None:
        log.append("ppr")

    enqueue_promoted(JOB_ID, HYP_ID)
    monkeypatch.setattr("app.pipeline.dreaming.get_driver", lambda: FakeDriver())
    monkeypatch.setattr("app.pipeline.dreaming._run_node_phases", fake_nodes)
    monkeypatch.setattr(
        "app.pipeline.dreaming.reconcile.reconcile_scoped_relations", fake_rel_reconcile
    )
    monkeypatch.setattr("app.pipeline.dreaming.run_judge", fake_judge)
    monkeypatch.setattr("app.pipeline.dreaming.run_context_agent_for_job", boom_agent)
    monkeypatch.setattr(
        "app.pipeline.dreaming.node_ppr_projection.refresh_ppr_projection", fake_refresh
    )
    monkeypatch.setattr("app.pipeline.dreaming.event_bus.publish", _noop_publish)
    monkeypatch.setattr("app.pipeline.dreaming.get_token_usage", lambda _job: 0)

    await run_dreaming_pipeline(JOB_ID)

    assert log == ["nodes", "rel_reconcile", "judge", "ppr"]


def test_agent_never_emits_node_or_relation_cypher():
    source = Path(run_context_agent.__code__.co_filename).read_text(encoding="utf-8")
    assert "CREATE (n:Node" not in source
    assert "MERGE (n:Node" not in source
    assert "-[:Relation" not in source
    assert "DELETE n" not in source
    assert "Semaphore" not in source
    assert "AgentSearchRun" in source
    assert "call_structured" in source


def test_empty_queue_is_zero_agent_calls_constant():
    reset_promoted_queue()
    assert drain_promoted_queue(JOB_ID) == []


async def _noop_publish(*_args, **_kwargs) -> None:
    return None


def test_read_and_gap_cypher_are_hypothesis_only():
    compact_read = " ".join(READ_HYPOTHESIS_CYPHER.split())
    compact_gap = " ".join(SET_EVIDENCE_GAP_CYPHER.split())
    compact_resolve = " ".join(RESOLVE_HYPOTHESIS_CYPHER.split())
    assert "PendingHypothesis" in compact_read
    assert "SET h.evidence_gap" in compact_gap
    assert "SET h.status" in compact_resolve
    assert ":Relation" not in compact_gap
