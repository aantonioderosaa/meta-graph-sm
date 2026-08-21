"""Fase 25: full context-layer chain + safe shutdown. FakeSession, no Docker/OpenAI."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core import event_bus
from app.models.agent_step import AgentStep
from app.models.structural_signal import StructuralSignalVerdict
from app.pipeline.context_agent import run_context_agent_for_job
from app.pipeline.context_layer_eval import stub_model_fn
from app.pipeline.context_layer_observability import (
    LIST_AGENT_SEARCH_RUNS_CYPHER,
    LIST_CONTEXT_LAYER_RUNS_CYPHER,
    MERGE_CONTEXT_LAYER_RUN_CYPHER,
)
from app.pipeline.metagraph_layer import list_context_layer_runs
from app.pipeline.pending_hypothesis import (
    MERGE_HYPOTHESIS_CYPHER,
    create_or_reinforce_hypothesis,
    listen_open_hypotheses,
    reset_promoted_queue,
    route_chunk_signal,
)
from app.pipeline.relevance_gate import FragmentSignal
from tests.test_acceptance_context_agent import AgentSession, FakeResult
from tests.test_pending_hypothesis import _signal

JOB_ID = "job-f25"
DOC_A = "doc-f25-a"
DOC_B = "doc-f25-b"
DOC_C = "doc-f25-c"

# Class-(c) paraphrase: no T2 marker; stub cue "non più da" → error (not
# quantifier, so the agent conclude path is write_node_relation).
DOC_A_TEXT = "il contratto è non più da rinnovare."
DOC_B_TEXT = "il contratto è non più da rinnovare quest'anno."


class ContextLayerSession(AgentSession):
    """Hypothesis + AgentSearchRun + ContextLayerRun store."""

    def __init__(self):
        super().__init__()
        self.gate_runs: dict[str, dict] = {}

    async def run(self, cypher, **kwargs):
        if cypher == MERGE_CONTEXT_LAYER_RUN_CYPHER:
            self.calls.append((cypher, kwargs))
            hid = kwargs["id"]
            existing = self.gate_runs.get(
                hid,
                {
                    "id": hid,
                    "job_id": kwargs.get("job_id"),
                    "timestamp": "2026-08-21T00:00:00",
                    "t1": 0,
                    "t2": 0,
                    "t3": 0,
                    "model_fallback": 0,
                    "promotions": 0,
                    "agent_runs": 0,
                    "agent_turns_used": 0,
                },
            )
            for key in (
                "t1",
                "t2",
                "t3",
                "model_fallback",
                "promotions",
                "agent_runs",
                "agent_turns_used",
            ):
                existing[key] = int(existing.get(key) or 0) + int(kwargs.get(key) or 0)
            existing["job_id"] = kwargs.get("job_id")
            self.gate_runs[hid] = existing
            return FakeResult([])
        if cypher == LIST_AGENT_SEARCH_RUNS_CYPHER:
            self.calls.append((cypher, kwargs))
            rows = [
                {
                    "id": run["id"],
                    "hypothesis_id": run["hypothesis_id"],
                    "verdict": run["verdict"],
                    "turns_used": run["turns_used"],
                    "timestamp": "2026-08-21T00:00:00",
                    "steps": run.get("steps"),
                }
                for run in self.agent_runs.values()
            ]
            return FakeResult(rows)
        if cypher == LIST_CONTEXT_LAYER_RUNS_CYPHER:
            self.calls.append((cypher, kwargs))
            return FakeResult([dict(row) for row in self.gate_runs.values()])
        return await AgentSession.run(self, cypher, **kwargs)


@pytest.fixture(autouse=True)
def _reset_layer(monkeypatch):
    monkeypatch.setattr("app.pipeline.embeddings.embed", lambda _text: [0.0] * 768)
    event_bus.reset_event_bus()
    reset_promoted_queue()
    yield
    event_bus.reset_event_bus()
    reset_promoted_queue()


async def _fallback_llm(system, user, model, temperature=0, job_id=None):
    assert model is StructuralSignalVerdict
    return await stub_model_fn(system, user)


def _scripted_agent(steps: list[AgentStep]):
    queue = list(steps)

    async def fake_call_structured(system, user, model, temperature=0, job_id=None):
        assert model is AgentStep
        if not queue:
            raise AssertionError("call_structured called more times than scripted")
        return queue.pop(0)

    return fake_call_structured


@pytest.mark.asyncio
async def test_full_chain_ingest_gate_hypothesis_reinforce_promote_agent_s0(
    monkeypatch,
):
    monkeypatch.setattr("app.core.config.settings.ENABLE_CONTEXT_LAYER", True)
    monkeypatch.setattr("app.pipeline.relevance_gate.call_structured", _fallback_llm)
    session = ContextLayerSession()
    writes: list[dict] = []

    async def spy_write(_session, **kwargs):
        writes.append(kwargs)

    monkeypatch.setattr("app.pipeline.ingestion.write_node_relation", spy_write)

    first = await route_chunk_signal(
        session,
        chunk_text=DOC_A_TEXT,
        s0_written=True,
        doc_id=DOC_A,
        job_id=JOB_ID,
    )
    assert first is not None
    assert first["status"] == "open"
    assert first["confidence"] == "low"
    assert first["promoted"] is False
    ingest_stats = session.gate_runs[JOB_ID]
    assert ingest_stats["model_fallback"] >= 1
    assert ingest_stats["t1"] >= 1
    assert ingest_stats["promotions"] == 0
    leftover = await create_or_reinforce_hypothesis(
        session,
        signal=_signal("tutti i cani sono usciti."),
        doc_id=DOC_A,
        job_id=JOB_ID,
    )
    assert leftover is not None
    leftover_id = leftover["id"]
    assert leftover["status"] == "open"
    assert leftover["promoted"] is False

    reinforced = await listen_open_hypotheses(
        session,
        doc_id=DOC_B,
        new_text=DOC_B_TEXT,
        job_id=JOB_ID,
    )
    promoted = [row for row in reinforced if row["id"] == first["id"]]
    assert promoted
    assert promoted[0]["confidence"] == "medium"
    assert promoted[0]["promoted"] is True
    assert leftover_id in session.hypotheses
    assert session.hypotheses[leftover_id]["status"] == "open"
    assert leftover_id != first["id"]

    monkeypatch.setattr(
        "app.pipeline.context_agent.call_structured",
        _scripted_agent(
            [
                AgentStep(
                    action="search_fulltext",
                    reasoning="cerco il contratto",
                    query="contratto",
                ),
                AgentStep(
                    action="conclude",
                    reasoning="testimoni foglia trovati",
                    candidate_ids=["n-contratto", "n-ruolo"],
                    evidence_span=DOC_B_TEXT,
                    witness_source="contratto",
                    witness_target="non più da rinnovare",
                    relation="rettifica",
                    kernel_parent="Spaziale",
                    doc_id=DOC_B,
                ),
            ]
        ),
    )

    outcomes = await run_context_agent_for_job(session, JOB_ID)

    assert len(outcomes) == 1
    assert outcomes[0].verdict == "confirmed"
    assert outcomes[0].turns_used == 2
    assert writes
    assert writes[0]["head_id"] == "n-contratto"
    assert writes[0]["tail_id"] == "n-ruolo"
    assert writes[0]["witness_source"] == "contratto"
    assert writes[0]["witness_target"] == "non più da rinnovare"
    assert session.hypotheses[first["id"]]["status"] == "confirmed"
    assert session.hypotheses[leftover_id]["status"] == "open"
    assert session.agent_runs
    run = next(iter(session.agent_runs.values()))
    assert run["verdict"] == "confirmed"
    assert run["turns_used"] == 2
    assert session.gate_runs[JOB_ID]["agent_runs"] == 1
    assert session.gate_runs[JOB_ID]["agent_turns_used"] == 2

    listed = await list_context_layer_runs(session)
    assert any(item.id == leftover_id for item in listed.open_hypotheses)
    assert all(item.id != first["id"] for item in listed.open_hypotheses)
    assert listed.agent_runs
    assert listed.agent_runs[0].verdict == "confirmed"
    assert any(item.model_fallback >= 1 for item in listed.gate_runs)

    monkeypatch.setattr("app.core.config.settings.ENABLE_CONTEXT_LAYER", False)
    n_calls = len(session.calls)
    n_hyps = len(session.hypotheses)
    n_writes = len(writes)

    after = await route_chunk_signal(
        session,
        chunk_text="Tutti i cani sono usciti.",
        s0_written=True,
        doc_id=DOC_C,
        job_id="job-f25-off",
    )
    listened = await listen_open_hypotheses(
        session,
        doc_id=DOC_C,
        new_text="Tutti i cani sono usciti.",
        job_id="job-f25-off",
    )
    agent_off = await run_context_agent_for_job(session, JOB_ID)

    assert after is None
    assert listened == []
    assert agent_off == []
    assert writes == writes[:n_writes]
    assert len(session.hypotheses) == n_hyps
    assert session.hypotheses[first["id"]]["status"] == "confirmed"
    assert session.hypotheses[leftover_id]["status"] == "open"
    new_cypher = [cy for cy, _ in session.calls[n_calls:]]
    assert new_cypher == []
    assert MERGE_HYPOTHESIS_CYPHER not in new_cypher
    assert not any("DELETE" in cy for cy in new_cypher)


@pytest.mark.asyncio
async def test_flag_off_after_layer_on_leaves_hypotheses_inert_never_deletes(
    monkeypatch,
):
    monkeypatch.setattr("app.core.config.settings.ENABLE_CONTEXT_LAYER", True)
    session = ContextLayerSession()
    open_hyp = await create_or_reinforce_hypothesis(
        session,
        signal=_signal("tutti i cani sono usciti."),
        doc_id=DOC_A,
        job_id=JOB_ID,
    )
    assert open_hyp is not None
    confirmed = await create_or_reinforce_hypothesis(
        session,
        signal=FragmentSignal(
            kind="t1",
            text="Fido è uscito dal cancello.",
            span="Fido è uscito dal cancello.",
            pair_entity_ids=(),
            marker_category=None,
            evidence_gap="predicate without pair",
            named_witnesses=("Fido",),
        ),
        doc_id=DOC_A,
        job_id=JOB_ID,
        named_witnesses=["Fido"],
    )
    assert confirmed is not None
    assert confirmed["promoted"] is True
    from app.pipeline.pending_hypothesis import resolve_hypothesis

    await resolve_hypothesis(session, confirmed["id"], "confirmed")

    source = Path(
        create_or_reinforce_hypothesis.__code__.co_filename
    ).read_text(encoding="utf-8")
    assert "DETACH DELETE" not in source
    assert "DELETE (h:PendingHypothesis" not in source
    assert "DELETE h" not in source

    monkeypatch.setattr("app.core.config.settings.ENABLE_CONTEXT_LAYER", False)
    n_calls = len(session.calls)
    hyp_ids = set(session.hypotheses)

    assert (
        await route_chunk_signal(
            session,
            chunk_text="Da allora ora è presidente.",
            s0_written=True,
            doc_id=DOC_C,
            job_id="job-off",
        )
        is None
    )
    assert (
        await listen_open_hypotheses(
            session, doc_id=DOC_C, new_text="Da allora ora è presidente.", job_id="job-off"
        )
        == []
    )
    assert await run_context_agent_for_job(session, "job-off") == []
    assert set(session.hypotheses) == hyp_ids
    assert session.hypotheses[open_hyp["id"]]["status"] == "open"
    assert session.hypotheses[confirmed["id"]]["status"] == "confirmed"
    assert session.calls[n_calls:] == []
    listed = await list_context_layer_runs(session)
    assert any(item.id == open_hyp["id"] for item in listed.open_hypotheses)
    assert all(item.id != confirmed["id"] for item in listed.open_hypotheses)
