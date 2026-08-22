"""Macrotasks 5–6: event triage + listen-window. FakeSession, no Neo4j/OpenAI."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from app.core.config import Settings
from app.pipeline.event_slots import (
    MATCH_BOTH_NODES_CYPHER,
    STAMP_SLOT_ON_LATEST_CYPHER,
    UPDATE_SLOT_EDGE_CYPHER,
)
from app.pipeline.event_triage import (
    EVENT_TRIAGE_MAX_SLOT_FANOUT,
    FIND_BATCH_EVENTS_CYPHER,
    FIND_WAITING_EVENTS_CYPHER,
    MERGE_EVENT_TRIAGE_RUN_CYPHER,
    MERGE_PENDING_EVENT_CONTEXT_CYPHER,
    EventSlotItem,
    EventSlotProposal,
    run_event_triage,
)
from app.pipeline.ingestion import CREATE_NODE_RELATION_CYPHER
from app.pipeline.judge import (
    FIND_BLURRED_RELATIONS_CYPHER,
    FIND_CONTRADICTS_PAIRS_CYPHER,
    FIND_EQUIVALENT_CONCEPT_PAIRS_CYPHER,
    FIND_MISSED_CONTRADICTIONS_CYPHER,
    FIND_POSSIBLY_SAME_AS_CYPHER,
    MERGE_JUDGE_RUN_CYPHER,
    _task_event_triage,
    run_judge,
)
from tests.test_acceptance_judge import JOB_ID, JudgeGraph
from tests.test_event_slots import (
    SlotGraph,
)
from tests.test_event_slots import (
    _dispatch as slot_dispatch,
)

EVENT_A = "event-a"
EVENT_B = "event-b"
EVENT_ONE = "evento-1"
HEAD = "esperimento-5"
TAIL = "tail-failed"
FONTE = "fonte-mike"
WRITE_CYPHER = {
    CREATE_NODE_RELATION_CYPHER,
    STAMP_SLOT_ON_LATEST_CYPHER,
    UPDATE_SLOT_EDGE_CYPHER,
}
EMPTY_JUDGE_CYPHER = [
    FIND_BLURRED_RELATIONS_CYPHER,
    FIND_EQUIVALENT_CONCEPT_PAIRS_CYPHER,
    FIND_POSSIBLY_SAME_AS_CYPHER,
    FIND_MISSED_CONTRADICTIONS_CYPHER,
    FIND_CONTRADICTS_PAIRS_CYPHER,
    MERGE_JUDGE_RUN_CYPHER,
]


class FakeResult:
    def __init__(self, records: list[dict] | None = None):
        self._records = records or []

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for record in self._records:
            yield record

    async def single(self):
        return self._records[0] if self._records else None

    async def consume(self):
        return None


class TriageGraph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.triage_runs: dict[str, dict] = {}
        self.pending: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []

    def add_event(self, event_id: str, **props) -> None:
        row = {
            "id": event_id,
            "name": props.get("name") or event_id,
            "summary": props.get("summary") or "",
            "kernel_category": props.get("kernel_category", "Evento"),
            "type": props.get("type", "event"),
        }
        row.update(props)
        self.nodes[event_id] = row


class FakeSession:
    def __init__(self, graph: TriageGraph | None = None) -> None:
        self.graph = graph or TriageGraph()
        self.calls = self.graph.calls

    async def run(self, cypher: str, parameters: dict | None = None, **kwargs):
        params = dict(parameters or {})
        params.update(kwargs)
        self.graph.calls.append((cypher, params))
        return FakeResult(_dispatch(self.graph, cypher, params))


def _dispatch(graph: TriageGraph, cypher: str, kwargs: dict) -> list[dict]:
    if cypher is FIND_BATCH_EVENTS_CYPHER or cypher == FIND_BATCH_EVENTS_CYPHER:
        touched = [str(i) for i in (kwargs.get("touched_ids") or [])]
        rows = []
        for nid, node in graph.nodes.items():
            if not (
                node.get("kernel_category") == "Evento" or node.get("type") == "event"
            ):
                continue
            if touched and nid not in touched:
                continue
            run = graph.triage_runs.get(nid)
            if run and run.get("verdict") in {"confirmed", "incomplete"}:
                continue
            rows.append(
                {
                    "event_id": nid,
                    "name": node.get("name"),
                    "summary": node.get("summary"),
                    "kernel_category": node.get("kernel_category"),
                    "type": node.get("type"),
                }
            )
        return rows
    if cypher is FIND_WAITING_EVENTS_CYPHER or cypher == FIND_WAITING_EVENTS_CYPHER:
        rows = []
        for event_id, _pending in graph.pending.items():
            run = graph.triage_runs.get(event_id)
            if run and run.get("verdict") in {"confirmed", "incomplete"}:
                continue
            node = graph.nodes.get(event_id) or {}
            rows.append(
                {
                    "event_id": event_id,
                    "name": node.get("name"),
                    "summary": node.get("summary"),
                    "kernel_category": node.get("kernel_category"),
                    "type": node.get("type"),
                }
            )
        return rows
    if cypher is MERGE_EVENT_TRIAGE_RUN_CYPHER or cypher == MERGE_EVENT_TRIAGE_RUN_CYPHER:
        event_id = str(kwargs["event_id"])
        graph.triage_runs[event_id] = {
            "id": event_id,
            "event_id": event_id,
            "verdict": kwargs["verdict"],
            "run_id": kwargs["run_id"],
        }
        return []
    if (
        cypher is MERGE_PENDING_EVENT_CONTEXT_CYPHER
        or cypher == MERGE_PENDING_EVENT_CONTEXT_CYPHER
    ):
        event_id = str(kwargs["event_id"])
        existing = graph.pending.get(event_id) or {}
        checks = int(existing.get("checks_without_progress") or 0) + 1
        graph.pending[event_id] = {
            "event_id": event_id,
            "missing_context": kwargs.get("missing_context") or "",
            "first_seen_run_id": existing.get("first_seen_run_id") or kwargs["run_id"],
            "last_checked_run_id": kwargs["run_id"],
            "checks_without_progress": checks,
        }
        return [{"checks_without_progress": checks}]
    return []


class SlotTriageGraph(SlotGraph):
    """SlotGraph plus triage audit so hallucinated ids hit the real writers."""

    def __init__(self) -> None:
        super().__init__()
        self.triage_runs: dict[str, dict] = {}
        self.pending: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []

    def add_event(self, event_id: str, **props) -> None:
        row = {
            "id": event_id,
            "name": props.get("name") or event_id,
            "summary": props.get("summary") or "",
            "kernel_category": props.get("kernel_category", "Evento"),
            "type": props.get("type", "event"),
            "revisions": [],
        }
        row.update(props)
        self.nodes[event_id] = row


class SlotTriageSession:
    def __init__(self, graph: SlotTriageGraph) -> None:
        self.graph = graph
        self.calls = graph.calls

    async def run(self, cypher: str, parameters: dict | None = None, **kwargs):
        params = dict(parameters or {})
        params.update(kwargs)
        self.graph.calls.append((cypher, params))
        self.calls = self.graph.calls
        if (
            cypher is FIND_BATCH_EVENTS_CYPHER
            or cypher == FIND_BATCH_EVENTS_CYPHER
            or cypher is FIND_WAITING_EVENTS_CYPHER
            or cypher == FIND_WAITING_EVENTS_CYPHER
            or cypher is MERGE_EVENT_TRIAGE_RUN_CYPHER
            or cypher == MERGE_EVENT_TRIAGE_RUN_CYPHER
            or cypher is MERGE_PENDING_EVENT_CONTEXT_CYPHER
            or cypher == MERGE_PENDING_EVENT_CONTEXT_CYPHER
        ):
            return FakeResult(_dispatch(self.graph, cypher, params))
        try:
            return FakeResult(slot_dispatch(self.graph, cypher, params))
        except AssertionError:
            return FakeResult([])


def _valid_item(**overrides: object) -> EventSlotItem:
    payload = {
        "head": HEAD,
        "kernel_parent": "Stato",
        "tail": TAIL,
        "verbo": "assert",
        "fonte": FONTE,
    }
    payload.update(overrides)
    return EventSlotItem.model_validate(payload)


def _invented_item() -> EventSlotItem:
    return EventSlotItem(
        head=HEAD,
        kernel_parent="EXPLAINED_BY",
        tail=TAIL,
        verbo="assert",
        fonte=FONTE,
    )


def _enable_triage(monkeypatch) -> None:
    monkeypatch.setattr("app.pipeline.judge.settings.ENABLE_EVENT_TRIAGE", True)


def _stub_apply_success(monkeypatch) -> list:
    applied: list[object] = []

    async def fake_apply(_session, validated, **_kwargs) -> bool:
        applied.append(validated)
        return validated is not None

    monkeypatch.setattr("app.pipeline.event_triage.apply_validated_slot", fake_apply)
    return applied


@pytest.mark.asyncio
async def test_flag_off_run_judge_does_not_call_triage(monkeypatch):
    assert Settings.model_fields["ENABLE_EVENT_TRIAGE"].default is False
    monkeypatch.setattr("app.pipeline.judge.settings.ENABLE_EVENT_TRIAGE", False)

    async def boom(*_args, **_kwargs):
        raise AssertionError("_task_event_triage must not run when flag is off")

    monkeypatch.setattr("app.pipeline.judge._task_event_triage", boom)

    graph = JudgeGraph()
    await run_judge(graph, JOB_ID)

    cyphers = [call[0] for call in graph.calls]
    assert cyphers == EMPTY_JUDGE_CYPHER
    joined = "\n".join(cyphers)
    assert "EventTriageRun" not in joined
    assert "PendingEventContext" not in joined
    assert "kernel_category = 'Evento'" not in joined


@pytest.mark.asyncio
async def test_flag_on_one_evento_one_triage_run_idempotent(monkeypatch):
    _enable_triage(monkeypatch)
    _stub_apply_success(monkeypatch)
    llm_calls: list[str] = []

    async def fake_llm(_system, user, model, **_kwargs):
        llm_calls.append(user)
        assert model is EventSlotProposal
        return EventSlotProposal(slots=[_valid_item()], reasoning="stato fallato")

    monkeypatch.setattr("app.pipeline.event_triage.call_structured", fake_llm)

    graph = TriageGraph()
    graph.add_event(EVENT_ONE, summary="l'esperimento 5 era fallato")
    session = FakeSession(graph)

    await run_judge(session, JOB_ID, touched_ids=[EVENT_ONE])
    assert list(graph.triage_runs) == [EVENT_ONE]
    assert graph.triage_runs[EVENT_ONE]["verdict"] == "confirmed"
    assert len(llm_calls) == 1

    await run_judge(session, JOB_ID, touched_ids=[EVENT_ONE])
    assert list(graph.triage_runs) == [EVENT_ONE]
    assert graph.triage_runs[EVENT_ONE]["verdict"] == "confirmed"
    assert len(llm_calls) == 1
    merge_runs = [
        call for call in graph.calls if call[0] == MERGE_EVENT_TRIAGE_RUN_CYPHER
    ]
    assert len(merge_runs) == 1


@pytest.mark.asyncio
async def test_invented_kernel_parent_zero_writes_not_confirmed(monkeypatch):
    _enable_triage(monkeypatch)
    apply_calls: list[object] = []

    async def boom_apply(*_args, **_kwargs) -> bool:
        apply_calls.append(1)
        raise AssertionError("apply_validated_slot must not run for invented kernel")

    monkeypatch.setattr("app.pipeline.event_triage.apply_validated_slot", boom_apply)

    async def fake_llm(_system, user, model, **_kwargs):
        assert model is EventSlotProposal
        return EventSlotProposal(slots=[_invented_item()], reasoning="invented")

    monkeypatch.setattr("app.pipeline.event_triage.call_structured", fake_llm)

    graph = TriageGraph()
    graph.add_event(EVENT_ONE, summary="l'esperimento 5 era fallato")
    session = FakeSession(graph)

    await run_judge(session, JOB_ID, touched_ids=[EVENT_ONE])

    assert apply_calls == []
    assert not any(call[0] in WRITE_CYPHER for call in session.calls)
    assert graph.triage_runs[EVENT_ONE]["verdict"] == "waiting"
    assert EVENT_ONE in graph.pending


@pytest.mark.asyncio
async def test_llm_error_on_one_event_does_not_block_the_other(monkeypatch):
    _enable_triage(monkeypatch)
    _stub_apply_success(monkeypatch)

    async def fake_llm(_system, user, model, **_kwargs):
        assert model is EventSlotProposal
        if f"event_id={EVENT_A}" in user:
            raise TimeoutError("llm timeout")
        if f"event_id={EVENT_B}" in user:
            return EventSlotProposal(slots=[_valid_item()], reasoning="ok")
        return EventSlotProposal(slots=[])

    monkeypatch.setattr("app.pipeline.event_triage.call_structured", fake_llm)

    graph = TriageGraph()
    graph.add_event(EVENT_A, summary="evento A")
    graph.add_event(EVENT_B, summary="evento B")
    session = FakeSession(graph)

    stats = await run_judge(session, JOB_ID, touched_ids=[EVENT_A, EVENT_B])
    assert stats is not None
    assert graph.triage_runs[EVENT_A]["verdict"] == "waiting"
    assert graph.triage_runs[EVENT_B]["verdict"] == "confirmed"
    assert EVENT_A in graph.pending
    assert EVENT_B not in graph.pending


def test_task_event_triage_has_no_adhoc_mutating_cypher():
    wrapper = inspect.getsource(_task_event_triage)
    for token in ("CREATE", "MERGE", "DELETE", "SET "):
        assert token not in wrapper
    assert "run_event_triage" in wrapper

    module_path = Path(run_event_triage.__code__.co_filename)
    source = module_path.read_text(encoding="utf-8")
    assert "apply_validated_slot" in source
    assert "validate_slot_proposal" in source
    assert "merge_nodes" not in source
    assert "DELETE" not in source
    assert EVENT_TRIAGE_MAX_SLOT_FANOUT >= 1

    tree = ast.parse(source)

    class _RunVisitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            is_run = (
                isinstance(func, ast.Attribute)
                and func.attr == "run"
                and isinstance(func.value, ast.Name)
                and func.value.id == "session"
            )
            if is_run:
                assert node.args, "session.run missing query argument"
                first = node.args[0]
                assert isinstance(first, ast.Name), ast.dump(first)
                assert first.id.endswith("_CYPHER")
            self.generic_visit(node)

    _RunVisitor().visit(tree)
    assert "MERGE_EVENT_TRIAGE_RUN_CYPHER" in source
    assert "EventTriageRun" in source
    assert "PENDING_HYPOTHESIS_LISTEN_WINDOW" in source
    assert "EVENT_TRIAGE_LISTEN_WINDOW" not in source
    assert "checks_without_progress" in source
    assert "incomplete" in source


@pytest.mark.asyncio
async def test_waiting_event_confirms_on_later_pass_when_slot_applies(monkeypatch):
    _stub_apply_success(monkeypatch)
    passes = {"n": 0}

    async def fake_llm(_system, user, model, **_kwargs):
        assert model is EventSlotProposal
        passes["n"] += 1
        if passes["n"] == 1:
            return EventSlotProposal(
                slots=[], reasoning="need more context about experiment 5"
            )
        return EventSlotProposal(slots=[_valid_item()], reasoning="stato fallato")

    monkeypatch.setattr("app.pipeline.event_triage.call_structured", fake_llm)

    graph = TriageGraph()
    graph.add_event(EVENT_ONE, summary="l'esperimento 5 era fallato")
    session = FakeSession(graph)

    await run_event_triage(session, "run-1", touched_ids=[EVENT_ONE])
    assert graph.triage_runs[EVENT_ONE]["verdict"] == "waiting"
    assert graph.pending[EVENT_ONE]["checks_without_progress"] == 1
    assert graph.pending[EVENT_ONE]["missing_context"] == (
        "need more context about experiment 5"
    )
    assert graph.pending[EVENT_ONE]["first_seen_run_id"] == "run-1"

    await run_event_triage(session, "run-2", touched_ids=[EVENT_ONE])
    assert graph.triage_runs[EVENT_ONE]["verdict"] == "confirmed"
    assert EVENT_ONE in graph.pending
    assert graph.pending[EVENT_ONE]["checks_without_progress"] == 1
    assert passes["n"] == 2


@pytest.mark.asyncio
async def test_listen_window_empty_checks_become_incomplete_and_are_not_retried(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.pipeline.event_triage.settings.PENDING_HYPOTHESIS_LISTEN_WINDOW", 2
    )
    llm_calls: list[str] = []

    async def fake_llm(_system, user, model, **_kwargs):
        assert model is EventSlotProposal
        llm_calls.append(user)
        return EventSlotProposal(slots=[], reasoning="cannot resolve yet")

    monkeypatch.setattr("app.pipeline.event_triage.call_structured", fake_llm)

    graph = TriageGraph()
    graph.add_event(EVENT_ONE, summary="evento irrisolvibile")
    session = FakeSession(graph)

    await run_event_triage(session, "run-1", touched_ids=[EVENT_ONE])
    assert graph.triage_runs[EVENT_ONE]["verdict"] == "waiting"
    assert graph.pending[EVENT_ONE]["checks_without_progress"] == 1

    await run_event_triage(session, "run-2", touched_ids=[EVENT_ONE])
    assert graph.triage_runs[EVENT_ONE]["verdict"] == "incomplete"
    assert graph.pending[EVENT_ONE]["checks_without_progress"] == 2
    assert graph.pending[EVENT_ONE]["missing_context"] == "cannot resolve yet"
    assert EVENT_ONE in graph.pending

    llm_calls.clear()
    await run_event_triage(session, "run-3", touched_ids=[EVENT_ONE])
    assert llm_calls == []
    assert graph.triage_runs[EVENT_ONE]["verdict"] == "incomplete"
    assert EVENT_ONE in graph.pending


@pytest.mark.asyncio
async def test_waiting_events_are_attempted_before_new_batch_events(monkeypatch):
    _stub_apply_success(monkeypatch)
    llm_order: list[str] = []

    async def fake_llm(_system, user, model, **_kwargs):
        assert model is EventSlotProposal
        if f"event_id={EVENT_A}" in user:
            llm_order.append(EVENT_A)
        elif f"event_id={EVENT_B}" in user:
            llm_order.append(EVENT_B)
        return EventSlotProposal(slots=[], reasoning="still waiting")

    monkeypatch.setattr("app.pipeline.event_triage.call_structured", fake_llm)

    graph = TriageGraph()
    graph.add_event(EVENT_A, summary="evento in attesa")
    graph.add_event(EVENT_B, summary="evento nuovo del batch")
    graph.pending[EVENT_A] = {
        "event_id": EVENT_A,
        "missing_context": "prior gap",
        "first_seen_run_id": "run-0",
        "last_checked_run_id": "run-0",
        "checks_without_progress": 1,
    }
    graph.triage_runs[EVENT_A] = {
        "id": EVENT_A,
        "event_id": EVENT_A,
        "verdict": "waiting",
        "run_id": "run-0",
    }
    session = FakeSession(graph)

    await run_event_triage(session, "run-1", touched_ids=[EVENT_B])

    assert llm_order == [EVENT_A, EVENT_B]
    find_calls = [
        call[0]
        for call in graph.calls
        if call[0] in {FIND_WAITING_EVENTS_CYPHER, FIND_BATCH_EVENTS_CYPHER}
    ]
    assert find_calls[:2] == [FIND_WAITING_EVENTS_CYPHER, FIND_BATCH_EVENTS_CYPHER]


@pytest.mark.asyncio
async def test_verified_no_change_empty_slots_confirms_without_writes_or_window_check(
    monkeypatch,
):
    apply_calls: list[object] = []

    async def boom_apply(*_args, **_kwargs) -> bool:
        apply_calls.append(1)
        raise AssertionError("apply_validated_slot must not run when slots are empty")

    monkeypatch.setattr("app.pipeline.event_triage.apply_validated_slot", boom_apply)

    async def fake_llm(_system, user, model, **_kwargs):
        assert model is EventSlotProposal
        return EventSlotProposal(
            slots=[],
            verified_no_change=True,
            reasoning="grafo già corretto",
        )

    monkeypatch.setattr("app.pipeline.event_triage.call_structured", fake_llm)

    graph = TriageGraph()
    graph.add_event(EVENT_ONE, summary="l'esperimento 5 era fallato")
    session = FakeSession(graph)

    await run_event_triage(session, "run-1", touched_ids=[EVENT_ONE])

    assert graph.triage_runs[EVENT_ONE]["verdict"] == "confirmed"
    assert apply_calls == []
    assert not any(call[0] in WRITE_CYPHER for call in session.calls)
    pending_merges = [
        call for call in graph.calls if call[0] == MERGE_PENDING_EVENT_CONTEXT_CYPHER
    ]
    assert pending_merges == []
    assert EVENT_ONE not in graph.pending
    audit_merges = [
        call for call in graph.calls if call[0] == MERGE_EVENT_TRIAGE_RUN_CYPHER
    ]
    assert len(audit_merges) == 1


@pytest.mark.asyncio
async def test_verified_no_change_false_empty_slots_still_waiting(monkeypatch):
    apply_calls: list[object] = []

    async def boom_apply(*_args, **_kwargs) -> bool:
        apply_calls.append(1)
        raise AssertionError("apply_validated_slot must not run when slots are empty")

    monkeypatch.setattr("app.pipeline.event_triage.apply_validated_slot", boom_apply)

    async def fake_llm(_system, user, model, **_kwargs):
        assert model is EventSlotProposal
        return EventSlotProposal(
            slots=[],
            verified_no_change=False,
            reasoning="cannot resolve yet",
        )

    monkeypatch.setattr("app.pipeline.event_triage.call_structured", fake_llm)

    graph = TriageGraph()
    graph.add_event(EVENT_ONE, summary="evento irrisolvibile")
    session = FakeSession(graph)

    await run_event_triage(session, "run-1", touched_ids=[EVENT_ONE])

    assert apply_calls == []
    assert graph.triage_runs[EVENT_ONE]["verdict"] == "waiting"
    assert EVENT_ONE in graph.pending
    assert graph.pending[EVENT_ONE]["checks_without_progress"] == 1
    pending_merges = [
        call for call in graph.calls if call[0] == MERGE_PENDING_EVENT_CONTEXT_CYPHER
    ]
    assert len(pending_merges) == 1


@pytest.mark.asyncio
async def test_applied_slot_confirms_regardless_of_verified_no_change(monkeypatch):
    applied = _stub_apply_success(monkeypatch)

    async def fake_llm(_system, user, model, **_kwargs):
        assert model is EventSlotProposal
        return EventSlotProposal(
            slots=[_valid_item()],
            verified_no_change=True,
            reasoning="scrittura applicata",
        )

    monkeypatch.setattr("app.pipeline.event_triage.call_structured", fake_llm)

    graph = TriageGraph()
    graph.add_event(EVENT_ONE, summary="l'esperimento 5 era fallato")
    session = FakeSession(graph)

    await run_event_triage(session, "run-1", touched_ids=[EVENT_ONE])

    assert applied
    assert graph.triage_runs[EVENT_ONE]["verdict"] == "confirmed"
    assert EVENT_ONE not in graph.pending
    pending_merges = [
        call for call in graph.calls if call[0] == MERGE_PENDING_EVENT_CONTEXT_CYPHER
    ]
    assert pending_merges == []


@pytest.mark.asyncio
async def test_verified_no_change_ignored_when_proposed_slots_all_fail(monkeypatch):
    apply_calls: list[object] = []

    async def miss_apply(*_args, **_kwargs) -> bool:
        apply_calls.append(1)
        return False

    monkeypatch.setattr("app.pipeline.event_triage.apply_validated_slot", miss_apply)

    async def fake_llm(_system, user, model, **_kwargs):
        assert model is EventSlotProposal
        return EventSlotProposal(
            slots=[_valid_item()],
            verified_no_change=True,
            reasoning="guessed ids that missed",
        )

    monkeypatch.setattr("app.pipeline.event_triage.call_structured", fake_llm)

    graph = TriageGraph()
    graph.add_event(EVENT_ONE, summary="l'esperimento 5 era fallato")
    session = FakeSession(graph)

    await run_event_triage(session, "run-1", touched_ids=[EVENT_ONE])

    assert apply_calls == [1]
    assert graph.triage_runs[EVENT_ONE]["verdict"] != "confirmed"
    assert graph.triage_runs[EVENT_ONE]["verdict"] == "waiting"
    assert EVENT_ONE in graph.pending
    assert graph.pending[EVENT_ONE]["checks_without_progress"] == 1


@pytest.fixture
def stub_ingestion_side_effects(monkeypatch):
    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.pipeline.ingestion.deposit_from_asserted_fact", _noop)
    monkeypatch.setattr("app.pipeline.ingestion.embeddings.embed", lambda _t: [0.1] * 8)


def _seed_slot_triage(*extra_node_ids: str) -> tuple[SlotTriageGraph, SlotTriageSession]:
    graph = SlotTriageGraph()
    graph.add_node(HEAD, "esperimento 5")
    for node_id in extra_node_ids:
        graph.add_node(node_id)
    graph.add_event(EVENT_ONE, summary="l'esperimento 5 era fallato")
    return graph, SlotTriageSession(graph)


@pytest.mark.asyncio
async def test_hallucinated_tail_id_verdict_not_confirmed(
    stub_ingestion_side_effects, monkeypatch
):
    ghost = "hallucinated-tail"
    graph, session = _seed_slot_triage(TAIL)

    async def fake_llm(_system, user, model, **_kwargs):
        assert model is EventSlotProposal
        return EventSlotProposal(
            slots=[_valid_item(tail=ghost)],
            reasoning="invented tail id",
        )

    monkeypatch.setattr("app.pipeline.event_triage.call_structured", fake_llm)

    await run_event_triage(session, "run-1", touched_ids=[EVENT_ONE])

    assert graph.triage_runs[EVENT_ONE]["verdict"] != "confirmed"
    assert graph.triage_runs[EVENT_ONE]["verdict"] == "waiting"
    assert graph.relations == []
    assert not any(call[0] in WRITE_CYPHER for call in session.calls)
    assert any(call[0] is MATCH_BOTH_NODES_CYPHER for call in session.calls)
    assert ghost not in graph.nodes


@pytest.mark.asyncio
async def test_hallucinated_head_id_verdict_not_confirmed(
    stub_ingestion_side_effects, monkeypatch
):
    ghost = "hallucinated-head"
    graph, session = _seed_slot_triage(TAIL)

    async def fake_llm(_system, user, model, **_kwargs):
        assert model is EventSlotProposal
        return EventSlotProposal(
            slots=[_valid_item(head=ghost)],
            reasoning="invented head id",
        )

    monkeypatch.setattr("app.pipeline.event_triage.call_structured", fake_llm)

    await run_event_triage(session, "run-1", touched_ids=[EVENT_ONE])

    assert graph.triage_runs[EVENT_ONE]["verdict"] != "confirmed"
    assert graph.relations == []
    assert not any(call[0] in WRITE_CYPHER for call in session.calls)


@pytest.mark.asyncio
async def test_existing_nodes_real_assert_confirms(
    stub_ingestion_side_effects, monkeypatch
):
    graph, session = _seed_slot_triage(TAIL)

    async def fake_llm(_system, user, model, **_kwargs):
        assert model is EventSlotProposal
        return EventSlotProposal(slots=[_valid_item()], reasoning="stato fallato")

    monkeypatch.setattr("app.pipeline.event_triage.call_structured", fake_llm)

    await run_event_triage(session, "run-1", touched_ids=[EVENT_ONE])

    assert graph.triage_runs[EVENT_ONE]["verdict"] == "confirmed"
    assert len(graph.relations) == 1
    assert graph.relations[0]["head_id"] == HEAD
    assert graph.relations[0]["tail_id"] == TAIL
    assert graph.relations[0]["kernel_parent"] == "Stato"
    assert any(call[0] is CREATE_NODE_RELATION_CYPHER for call in session.calls)
