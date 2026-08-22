"""Macrotasks 5–6: event triage + listen-window. FakeSession, no Neo4j/OpenAI."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from app.core.config import Settings
from app.pipeline.context_retrieval import NODE_RELATIONS_CYPHER, RetrievalHit
from app.pipeline.event_slots import (
    STAMP_SLOT_ON_LATEST_CYPHER,
    UPDATE_SLOT_EDGE_CYPHER,
)
from app.pipeline.event_triage import (
    EVENT_TRIAGE_MAX_SLOT_FANOUT,
    EVENT_TRIAGE_SOURCE_TEXT_CAP,
    FIND_BATCH_EVENTS_CYPHER,
    FIND_EVENT_SOURCE_CHUNK_TEXT_CYPHER,
    FIND_WAITING_EVENTS_CYPHER,
    MERGE_EVENT_TRIAGE_RUN_CYPHER,
    MERGE_PENDING_EVENT_CONTEXT_CYPHER,
    EventSlotItem,
    EventTriageAction,
    EventTriageStep,
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
        self.source_chunks: dict[str, list[str]] = {}
        self.neighbors: dict[str, list[str]] = {}
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

    def add_source_chunk(self, event_id: str, text: str) -> None:
        self.source_chunks.setdefault(event_id, []).append(text)


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
    if (
        cypher is FIND_EVENT_SOURCE_CHUNK_TEXT_CYPHER
        or cypher == FIND_EVENT_SOURCE_CHUNK_TEXT_CYPHER
    ):
        event_id = str(kwargs.get("id") or "")
        texts = getattr(graph, "source_chunks", {}).get(event_id) or []
        return [{"text": text} for text in texts]
    if cypher is NODE_RELATIONS_CYPHER or cypher == NODE_RELATIONS_CYPHER:
        node_id = str(kwargs.get("node_id") or "")
        rows: list[dict] = []
        seen: set[str] = set()
        for other_id in getattr(graph, "neighbors", {}).get(node_id, []):
            oid = str(other_id)
            if not oid or oid in seen:
                continue
            seen.add(oid)
            rows.append(
                {
                    "relation": "related",
                    "normalized_relation": "related",
                    "kernel_parent": None,
                    "is_latest": True,
                    "valid_time": None,
                    "system_time": None,
                    "witnesses_a": [],
                    "witnesses_b": [],
                    "provenance": None,
                    "witness_text": None,
                    "direction": "outgoing",
                    "other_id": oid,
                    "other_name": oid,
                }
            )
        for rel in getattr(graph, "relations", []) or []:
            head_id = str(rel.get("head_id") or "")
            tail_id = str(rel.get("tail_id") or "")
            other = ""
            if head_id == node_id:
                other = tail_id
            elif tail_id == node_id:
                other = head_id
            if not other or other in seen:
                continue
            seen.add(other)
            rows.append(
                {
                    "relation": rel.get("relation") or "related",
                    "normalized_relation": rel.get("normalized_relation") or "related",
                    "kernel_parent": rel.get("kernel_parent"),
                    "is_latest": rel.get("is_latest"),
                    "valid_time": None,
                    "system_time": None,
                    "witnesses_a": rel.get("witnesses_a") or [],
                    "witnesses_b": rel.get("witnesses_b") or [],
                    "provenance": None,
                    "witness_text": rel.get("witness_text"),
                    "direction": "outgoing" if head_id == node_id else "incoming",
                    "other_id": other,
                    "other_name": other,
                }
            )
        return rows
    return []


class SlotTriageGraph(SlotGraph):
    """SlotGraph plus triage audit so hallucinated ids hit the real writers."""

    def __init__(self) -> None:
        super().__init__()
        self.triage_runs: dict[str, dict] = {}
        self.pending: dict[str, dict] = {}
        self.source_chunks: dict[str, list[str]] = {}
        self.neighbors: dict[str, list[str]] = {}
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
            or cypher is FIND_EVENT_SOURCE_CHUNK_TEXT_CYPHER
            or cypher == FIND_EVENT_SOURCE_CHUNK_TEXT_CYPHER
            or cypher is NODE_RELATIONS_CYPHER
            or cypher == NODE_RELATIONS_CYPHER
        ):
            return FakeResult(_dispatch(self.graph, cypher, params))
        try:
            return FakeResult(slot_dispatch(self.graph, cypher, params))
        except AssertionError:
            return FakeResult([])


def _prelink(graph: object, event_id: str, *node_ids: str) -> None:
    neighbors = getattr(graph, "neighbors", None)
    if neighbors is None:
        graph.neighbors = {}  # type: ignore[attr-defined]
        neighbors = graph.neighbors  # type: ignore[attr-defined]
    existing = list(neighbors.get(event_id, []))
    for nid in node_ids:
        text = str(nid)
        if text and text not in existing:
            existing.append(text)
    neighbors[event_id] = existing


def _propose(
    *slots: EventSlotItem,
    reasoning: str = "propose",
    verified_no_change: bool = False,
) -> EventTriageStep:
    return EventTriageStep(
        action=EventTriageAction.propose,
        reasoning=reasoning,
        slots=list(slots),
        verified_no_change=verified_no_change,
    )


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
        assert model is EventTriageStep
        return _propose(_valid_item(), reasoning="stato fallato")

    monkeypatch.setattr("app.pipeline.event_triage.call_structured", fake_llm)

    graph = TriageGraph()
    graph.add_event(EVENT_ONE, summary="l'esperimento 5 era fallato")
    _prelink(graph, EVENT_ONE, HEAD, TAIL)
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
        assert model is EventTriageStep
        return _propose(_invented_item(), reasoning="invented")

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
        assert model is EventTriageStep
        if f"event_id={EVENT_A}" in user:
            raise TimeoutError("llm timeout")
        if f"event_id={EVENT_B}" in user:
            return _propose(_valid_item(), reasoning="ok")
        return _propose(reasoning="none")

    monkeypatch.setattr("app.pipeline.event_triage.call_structured", fake_llm)

    graph = TriageGraph()
    graph.add_event(EVENT_A, summary="evento A")
    graph.add_event(EVENT_B, summary="evento B")
    _prelink(graph, EVENT_B, HEAD, TAIL)
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
        assert model is EventTriageStep
        passes["n"] += 1
        if passes["n"] == 1:
            return _propose(reasoning="need more context about experiment 5")
        return _propose(_valid_item(), reasoning="stato fallato")

    monkeypatch.setattr("app.pipeline.event_triage.call_structured", fake_llm)

    graph = TriageGraph()
    graph.add_event(EVENT_ONE, summary="l'esperimento 5 era fallato")
    _prelink(graph, EVENT_ONE, HEAD, TAIL)
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
        assert model is EventTriageStep
        llm_calls.append(user)
        return _propose(reasoning="cannot resolve yet")

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
        assert model is EventTriageStep
        if f"event_id={EVENT_A}" in user:
            llm_order.append(EVENT_A)
        elif f"event_id={EVENT_B}" in user:
            llm_order.append(EVENT_B)
        return _propose(reasoning="still waiting")

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
        assert model is EventTriageStep
        return _propose(verified_no_change=True, reasoning="grafo già corretto")

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
        assert model is EventTriageStep
        return _propose(verified_no_change=False, reasoning="cannot resolve yet")

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
        assert model is EventTriageStep
        return _propose(
            _valid_item(),
            verified_no_change=True,
            reasoning="scrittura applicata",
        )

    monkeypatch.setattr("app.pipeline.event_triage.call_structured", fake_llm)

    graph = TriageGraph()
    graph.add_event(EVENT_ONE, summary="l'esperimento 5 era fallato")
    _prelink(graph, EVENT_ONE, HEAD, TAIL)
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
        assert model is EventTriageStep
        return _propose(
            _valid_item(),
            verified_no_change=True,
            reasoning="guessed ids that missed",
        )

    monkeypatch.setattr("app.pipeline.event_triage.call_structured", fake_llm)

    graph = TriageGraph()
    graph.add_event(EVENT_ONE, summary="l'esperimento 5 era fallato")
    _prelink(graph, EVENT_ONE, HEAD, TAIL)
    session = FakeSession(graph)

    await run_event_triage(session, "run-1", touched_ids=[EVENT_ONE])

    assert apply_calls == [1]
    assert graph.triage_runs[EVENT_ONE]["verdict"] != "confirmed"
    assert graph.triage_runs[EVENT_ONE]["verdict"] == "waiting"
    assert EVENT_ONE in graph.pending
    assert graph.pending[EVENT_ONE]["checks_without_progress"] == 1


def test_source_chunk_text_cypher_is_readonly():
    query = FIND_EVENT_SOURCE_CHUNK_TEXT_CYPHER
    folded = " ".join(query.upper().split())
    assert "MATCH" in folded
    assert "RETURN" in folded
    assert "DERIVED_FROM" in folded
    assert ":CHUNK" in folded
    for token in ("CREATE", "MERGE", "SET", "DELETE"):
        assert token not in folded
    assert EVENT_TRIAGE_SOURCE_TEXT_CAP >= 1000
    assert EVENT_TRIAGE_SOURCE_TEXT_CAP <= 8000


@pytest.mark.asyncio
async def test_source_chunk_phrase_reaches_prompt_when_summary_paraphrases(
    monkeypatch,
):
    llm_calls: list[str] = []

    async def fake_llm(_system, user, model, **_kwargs):
        llm_calls.append(user)
        assert model is EventTriageStep
        return _propose(reasoning="saw original phrasing")

    monkeypatch.setattr("app.pipeline.event_triage.call_structured", fake_llm)

    graph = TriageGraph()
    graph.add_event(
        EVENT_ONE,
        name="esperimento 5",
        summary="the fifth experiment did not succeed",
    )
    graph.add_source_chunk(EVENT_ONE, "Nel diario: l'esperimento 5 era fallato.")
    session = FakeSession(graph)

    await run_event_triage(session, "run-1", touched_ids=[EVENT_ONE])

    assert llm_calls
    prompt = llm_calls[0]
    assert "era fallato" in prompt
    assert "the fifth experiment did not succeed" in prompt
    assert "source_chunk_text=" in prompt
    assert any(call[0] == FIND_EVENT_SOURCE_CHUNK_TEXT_CYPHER for call in session.calls)
    assert graph.triage_runs[EVENT_ONE]["verdict"] == "waiting"


@pytest.mark.asyncio
async def test_missing_derived_from_still_triages(monkeypatch):
    llm_calls: list[str] = []

    async def fake_llm(_system, user, model, **_kwargs):
        llm_calls.append(user)
        assert model is EventTriageStep
        return _propose(reasoning="no source chunk")

    monkeypatch.setattr("app.pipeline.event_triage.call_structured", fake_llm)

    graph = TriageGraph()
    graph.add_event(EVENT_ONE, summary="bland paraphrase without original phrasing")
    session = FakeSession(graph)

    await run_event_triage(session, "run-1", touched_ids=[EVENT_ONE])

    assert llm_calls
    assert "source_chunk_text=" not in llm_calls[0]
    assert graph.triage_runs[EVENT_ONE]["verdict"] == "waiting"
    assert EVENT_ONE in graph.pending
    assert any(call[0] == FIND_EVENT_SOURCE_CHUNK_TEXT_CYPHER for call in session.calls)


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
        assert model is EventTriageStep
        return _propose(_valid_item(tail=ghost), reasoning="invented tail id")

    monkeypatch.setattr("app.pipeline.event_triage.call_structured", fake_llm)

    await run_event_triage(session, "run-1", touched_ids=[EVENT_ONE])

    assert graph.triage_runs[EVENT_ONE]["verdict"] != "confirmed"
    assert graph.triage_runs[EVENT_ONE]["verdict"] == "waiting"
    assert graph.relations == []
    assert not any(call[0] in WRITE_CYPHER for call in session.calls)
    assert ghost not in graph.nodes


@pytest.mark.asyncio
async def test_hallucinated_head_id_verdict_not_confirmed(
    stub_ingestion_side_effects, monkeypatch
):
    ghost = "hallucinated-head"
    graph, session = _seed_slot_triage(TAIL)

    async def fake_llm(_system, user, model, **_kwargs):
        assert model is EventTriageStep
        return _propose(_valid_item(head=ghost), reasoning="invented head id")

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
    _prelink(graph, EVENT_ONE, HEAD, TAIL)

    async def fake_llm(_system, user, model, **_kwargs):
        assert model is EventTriageStep
        return _propose(_valid_item(), reasoning="stato fallato")

    monkeypatch.setattr("app.pipeline.event_triage.call_structured", fake_llm)

    await run_event_triage(session, "run-1", touched_ids=[EVENT_ONE])

    assert graph.triage_runs[EVENT_ONE]["verdict"] == "confirmed"
    assert len(graph.relations) == 1
    assert graph.relations[0]["head_id"] == HEAD
    assert graph.relations[0]["tail_id"] == TAIL
    assert graph.relations[0]["kernel_parent"] == "Stato"
    assert any(call[0] is CREATE_NODE_RELATION_CYPHER for call in session.calls)


def _hit(node_id: str, name: str = "") -> RetrievalHit:
    return RetrievalHit(
        kind="node",
        score=1.0,
        id=node_id,
        index="node_summary_fulltext",
        name=name or node_id,
    )


@pytest.mark.asyncio
async def test_no_cost_regression_when_turn0_already_suffices(monkeypatch):
    search_calls: list[str] = []

    async def boom_search(*_args, **_kwargs):
        search_calls.append("called")
        raise AssertionError("search must not run when turn 0 already suffices")

    monkeypatch.setattr("app.pipeline.event_triage.search_fulltext", boom_search)
    monkeypatch.setattr("app.pipeline.event_triage.search_vector", boom_search)
    llm_calls: list[str] = []

    async def fake_llm(_system, user, model, **_kwargs):
        llm_calls.append(user)
        assert model is EventTriageStep
        return _propose(verified_no_change=True, reasoning="grafo già corretto")

    monkeypatch.setattr("app.pipeline.event_triage.call_structured", fake_llm)

    graph = TriageGraph()
    graph.add_event(EVENT_ONE, summary="l'esperimento 5 era fallato")
    _prelink(graph, EVENT_ONE, HEAD, TAIL)
    session = FakeSession(graph)

    await run_event_triage(session, "run-1", touched_ids=[EVENT_ONE])

    assert len(llm_calls) == 1
    assert search_calls == []
    assert graph.triage_runs[EVENT_ONE]["verdict"] == "confirmed"
    assert EVENT_ONE not in graph.pending
    assert not any(call[0] in WRITE_CYPHER for call in session.calls)


@pytest.mark.asyncio
async def test_zero_prelinked_search_then_propose_uses_observed_id(monkeypatch):
    applied = _stub_apply_success(monkeypatch)
    search_queries: list[str] = []

    async def fake_search(_session, query, **_kwargs):
        search_queries.append(query)
        return [_hit(HEAD, "esperimento 5")]

    monkeypatch.setattr("app.pipeline.event_triage.search_fulltext", fake_search)

    async def boom_vector(*_args, **_kwargs):
        raise AssertionError("this test scripts search_fulltext only")

    monkeypatch.setattr("app.pipeline.event_triage.search_vector", boom_vector)

    turns = {"n": 0}

    async def fake_llm(_system, user, model, **_kwargs):
        assert model is EventTriageStep
        turns["n"] += 1
        if turns["n"] == 1:
            return EventTriageStep(
                action=EventTriageAction.search_fulltext,
                reasoning="cerco l'esperimento 5 nominato nel testo",
                query="esperimento 5",
            )
        return _propose(
            _valid_item(head=EVENT_ONE, tail=HEAD),
            reasoning="slot sull'id osservato",
        )

    monkeypatch.setattr("app.pipeline.event_triage.call_structured", fake_llm)

    graph = TriageGraph()
    graph.add_event(EVENT_ONE, name="fallimento", summary="l'esperimento 5 era fallato")
    session = FakeSession(graph)

    await run_event_triage(session, "run-1", touched_ids=[EVENT_ONE])

    assert search_queries
    assert turns["n"] == 2
    assert applied
    assert applied[0].slot.head_id == EVENT_ONE
    assert applied[0].tail_id == HEAD
    assert graph.triage_runs[EVENT_ONE]["verdict"] == "confirmed"


@pytest.mark.asyncio
async def test_unobserved_id_dropped_not_confirmed(monkeypatch):
    apply_calls: list[object] = []

    async def spy_apply(*_args, **_kwargs) -> bool:
        apply_calls.append(1)
        return True

    monkeypatch.setattr("app.pipeline.event_triage.apply_validated_slot", spy_apply)

    async def fake_llm(_system, user, model, **_kwargs):
        assert model is EventTriageStep
        return _propose(
            _valid_item(head="invented-id", tail="invented-id"),
            reasoning="id mai osservato",
        )

    monkeypatch.setattr("app.pipeline.event_triage.call_structured", fake_llm)

    graph = TriageGraph()
    graph.add_event(EVENT_ONE, summary="l'esperimento 5 era fallato")
    session = FakeSession(graph)

    await run_event_triage(session, "run-1", touched_ids=[EVENT_ONE])

    assert apply_calls == []
    assert graph.triage_runs[EVENT_ONE]["verdict"] != "confirmed"
    assert graph.triage_runs[EVENT_ONE]["verdict"] == "waiting"
    assert EVENT_ONE in graph.pending


@pytest.mark.asyncio
async def test_turns_exhausted_without_propose_is_waiting(monkeypatch):
    apply_calls: list[object] = []

    async def boom_apply(*_args, **_kwargs) -> bool:
        apply_calls.append(1)
        raise AssertionError("no writes when turns exhaust without propose")

    monkeypatch.setattr("app.pipeline.event_triage.apply_validated_slot", boom_apply)

    async def fake_search(_session, query, **_kwargs):
        return []

    monkeypatch.setattr("app.pipeline.event_triage.search_fulltext", fake_search)
    monkeypatch.setattr(
        "app.pipeline.event_triage.settings.EVENT_TRIAGE_MAX_TURNS", 3
    )
    llm_calls: list[int] = []

    async def always_search(_system, user, model, **_kwargs):
        assert model is EventTriageStep
        llm_calls.append(1)
        return EventTriageStep(
            action=EventTriageAction.search_fulltext,
            reasoning="ancora cerco, non propongo",
            query="esperimento 5",
        )

    monkeypatch.setattr("app.pipeline.event_triage.call_structured", always_search)

    graph = TriageGraph()
    graph.add_event(EVENT_ONE, summary="evento irrisolvibile")
    session = FakeSession(graph)

    await run_event_triage(session, "run-1", touched_ids=[EVENT_ONE])

    assert len(llm_calls) == 3
    assert apply_calls == []
    assert graph.triage_runs[EVENT_ONE]["verdict"] == "waiting"
    assert EVENT_ONE in graph.pending
    assert graph.pending[EVENT_ONE]["missing_context"] == "turns exhausted"


@pytest.mark.asyncio
async def test_unobserved_id_mixed_with_observed_drops_only_guess(monkeypatch):
    applied = _stub_apply_success(monkeypatch)

    async def fake_search(_session, query, **_kwargs):
        return [_hit(HEAD, "esperimento 5"), _hit(TAIL, "fallato")]

    monkeypatch.setattr("app.pipeline.event_triage.search_fulltext", fake_search)

    turns = {"n": 0}

    async def fake_llm(_system, user, model, **_kwargs):
        assert model is EventTriageStep
        turns["n"] += 1
        if turns["n"] == 1:
            return EventTriageStep(
                action=EventTriageAction.search_fulltext,
                reasoning="cerco i referenti",
                query="esperimento 5 fallato",
            )
        return _propose(
            _valid_item(),
            _valid_item(head="invented-id", tail=TAIL),
            reasoning="un id osservato e uno inventato",
        )

    monkeypatch.setattr("app.pipeline.event_triage.call_structured", fake_llm)

    graph = TriageGraph()
    graph.add_event(EVENT_ONE, summary="l'esperimento 5 era fallato")
    session = FakeSession(graph)

    await run_event_triage(session, "run-1", touched_ids=[EVENT_ONE])

    assert turns["n"] == 2
    assert len(applied) == 1
    assert applied[0].slot.head_id == HEAD
    assert applied[0].tail_id == TAIL
    assert graph.triage_runs[EVENT_ONE]["verdict"] == "confirmed"
