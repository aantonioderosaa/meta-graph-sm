"""Macrotask 8: event-triage non-regression on a fixed corpus.

FakeSession, no Neo4j/OpenAI. Quantifier (Fase 21.1) and retraction (Fase 21.2)
are called directly — they must not route through event triage.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings
from app.models.kernel import (
    AttributeKernelType,
    RelationKernelType,
    SpecialRelationType,
)
from app.pipeline.context_retrieval import NODE_RELATIONS_CYPHER
from app.pipeline.event_relation_resolution import SITUATION_NORMALIZED_RELATION
from app.pipeline.event_slots import Slot, slot_id_for
from app.pipeline.event_triage import (
    EVENT_TRIAGE_MAX_SLOT_FANOUT,
    EventSlotItem,
    EventTriageStep,
    run_event_triage,
)
from app.pipeline.quantifier_events import resolve_quantifier_scope
from app.pipeline.retraction import resolve_retraction_scope
from tests.test_acceptance_quantifier_events import (
    BOBI,
    COLLECTIVE_NAMES,
    CUCINA,
    FIDO,
    REX,
    SPOT,
    SceneSession,
    _closed_chunk,
    _three_dogs_scene,
)
from tests.test_event_slots import (
    FakeResult,
    SlotGraph,
)
from tests.test_event_slots import (
    FakeSession as SlotFakeSession,
)
from tests.test_event_slots import (
    _dispatch as slot_dispatch,
)
from tests.test_event_triage import (
    FIND_BATCH_EVENTS_CYPHER,
    FIND_WAITING_EVENTS_CYPHER,
    MERGE_EVENT_TRIAGE_RUN_CYPHER,
    MERGE_PENDING_EVENT_CONTEXT_CYPHER,
    _prelink,
    _propose,
)
from tests.test_event_triage import (
    _dispatch as triage_dispatch,
)

CLOSED_KERNEL = frozenset(
    {m.value for m in AttributeKernelType} | {m.value for m in RelationKernelType}
)
FAMIGLIA_B = frozenset(m.value for m in SpecialRelationType)

HEAD = "esperimento-5"
EVENT_FAILED = "evento-esperimento-5"
OLD_TAIL = "riuscito"
NEW_TAIL = "fallato"
OLD_SUMMARY = "ok"
FONTE = "fonte-narratore"
RUN_ID = "run-macrotask-8"

MIKE = "mike"
SAID_ONE = "mike-said-1"
SAID_TWO = "mike-said-2"
BELLO = "bellissimo"
STORY = "storia-raccontata"
STORY_MEMBER = "storia-membro-1"
COUSIN = "transitive-cousin"
COUSIN_TAIL = "cousin-stato-ok"
HUMANS = "concept-humans"
EVENT_MIKE = "evento-mike-storia"

TRIAGE_CYPHER = {
    FIND_BATCH_EVENTS_CYPHER,
    FIND_WAITING_EVENTS_CYPHER,
    MERGE_EVENT_TRIAGE_RUN_CYPHER,
    MERGE_PENDING_EVENT_CONTEXT_CYPHER,
}


class CorpusGraph(SlotGraph):
    def __init__(self) -> None:
        super().__init__()
        self.triage_runs: dict[str, dict] = {}
        self.pending: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        self.member_of: dict[str, str] = {}
        self.isa: dict[str, str] = {}
        self.neighbors: dict[str, list[str]] = {}

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


class FakeSession(SlotFakeSession):
    def __init__(self, graph: CorpusGraph | None = None) -> None:
        self.graph = graph or CorpusGraph()
        self.calls = self.graph.calls

    async def run(self, cypher: str, parameters: dict | None = None, **kwargs):
        params = dict(parameters or {})
        params.update(kwargs)
        self.graph.calls.append((cypher, params))
        self.calls = self.graph.calls
        return FakeResult(_corpus_dispatch(self.graph, cypher, params))


def _corpus_dispatch(graph: CorpusGraph, cypher: str, kwargs: dict) -> list[dict]:
    if (
        cypher in TRIAGE_CYPHER
        or cypher is NODE_RELATIONS_CYPHER
        or cypher == NODE_RELATIONS_CYPHER
    ):
        return triage_dispatch(graph, cypher, kwargs)
    try:
        return slot_dispatch(graph, cypher, kwargs)
    except AssertionError:
        return []


def _stamp_slot(
    graph: CorpusGraph,
    *,
    head_id: str,
    tail_id: str,
    kernel_parent: str,
    fonte_id: str,
    normalized_relation: str | None = None,
) -> str:
    relation = normalized_relation or kernel_parent
    slot = Slot(
        head_id=head_id,
        kernel_parent=kernel_parent,
        normalized_relation=relation,
        tail_id=tail_id,
    )
    sid = slot_id_for(slot)
    graph.relations.append(
        {
            "head_id": head_id,
            "tail_id": tail_id,
            "relation": relation,
            "normalized_relation": relation,
            "is_latest": True,
            "kernel_parent": kernel_parent,
            "witnesses_a": [fonte_id],
            "witnesses_b": [],
            "witness_source_ids": [fonte_id],
            "witness_target_ids": [],
            "witness_add_tags": [f"{fonte_id}:seed"],
            "slot_id": sid,
            "updates": None,
            "caused_by_event_id": "seed",
            "run_id": "seed",
            "created_at": graph.next_clock(),
        }
    )
    return sid


def _assert_closed_kernel_parents(graph: CorpusGraph, *, event_id: str | None = None) -> None:
    for rel in graph.relations:
        if event_id is not None and rel.get("caused_by_event_id") != event_id:
            continue
        kp = rel.get("kernel_parent")
        if not kp:
            continue
        assert kp in CLOSED_KERNEL, kp
        assert kp not in FAMIGLIA_B
        member = None
        try:
            member = AttributeKernelType(kp)
        except ValueError:
            member = RelationKernelType(kp)
        assert isinstance(member, (AttributeKernelType, RelationKernelType))


def _assert_no_delete(session: FakeSession | SceneSession) -> None:
    for cypher, _kwargs in session.calls:
        compact = " ".join(str(cypher).split()).upper()
        assert "DELETE" not in compact
        assert "MERGE_NODES" not in compact


def _written_ids(graph: CorpusGraph, event_id: str) -> set[str]:
    touched: set[str] = set()
    for rel in graph.relations:
        if rel.get("caused_by_event_id") != event_id:
            continue
        touched.add(str(rel["head_id"]))
        touched.add(str(rel["tail_id"]))
    return touched


def _item(**overrides: object) -> EventSlotItem:
    payload = {
        "head": HEAD,
        "kernel_parent": AttributeKernelType.Stato.value,
        "tail": NEW_TAIL,
        "verbo": "assert",
        "fonte": FONTE,
    }
    payload.update(overrides)
    return EventSlotItem.model_validate(payload)


@pytest.fixture
def stub_ingestion_side_effects(monkeypatch):
    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.pipeline.ingestion.deposit_from_asserted_fact", _noop)
    monkeypatch.setattr("app.pipeline.ingestion.embeddings.embed", lambda _t: [0.1] * 8)


def _enable_triage(monkeypatch) -> None:
    monkeypatch.setattr("app.pipeline.judge.settings.ENABLE_EVENT_TRIAGE", True)
    monkeypatch.setattr("app.core.config.settings.ENABLE_EVENT_TRIAGE", True)


@pytest.mark.asyncio
async def test_esperimento_5_era_fallato(stub_ingestion_side_effects, monkeypatch):
    _enable_triage(monkeypatch)

    async def fake_llm(_system, user, model, **_kwargs):
        assert model is EventTriageStep
        assert "esperimento 5" in user.casefold() or "fallato" in user.casefold()
        return _propose(
            _item(),
            reasoning="l'esperimento 5 era fallato: Stato → fallato",
        )

    monkeypatch.setattr("app.pipeline.event_triage.call_structured", fake_llm)

    graph = CorpusGraph()
    graph.add_node(HEAD, "esperimento 5", summary=OLD_SUMMARY)
    graph.add_node(OLD_TAIL, OLD_TAIL)
    graph.add_node(NEW_TAIL, NEW_TAIL)
    graph.add_event(
        EVENT_FAILED,
        name="l'esperimento 5 era fallato",
        summary="l'esperimento 5 era fallato",
    )
    _prelink(graph, EVENT_FAILED, HEAD, NEW_TAIL)
    _stamp_slot(
        graph,
        head_id=HEAD,
        tail_id=OLD_TAIL,
        kernel_parent=AttributeKernelType.Stato.value,
        fonte_id=FONTE,
    )
    session = FakeSession(graph)

    await run_event_triage(session, RUN_ID, touched_ids=[EVENT_FAILED])

    assert graph.triage_runs[EVENT_FAILED]["verdict"] == "confirmed"
    stato = [
        rel
        for rel in graph.relations
        if rel["head_id"] == HEAD
        and rel.get("kernel_parent") == AttributeKernelType.Stato.value
    ]
    latest = [rel for rel in stato if rel.get("is_latest") is True]
    assert len(latest) == 1
    assert latest[0]["tail_id"] == NEW_TAIL
    previous = next(rel for rel in stato if rel["tail_id"] == OLD_TAIL)
    assert previous["is_latest"] is False
    assert latest[0].get("updates") == OLD_TAIL
    revisions = graph.nodes[HEAD]["revisions"]
    assert revisions
    assert revisions[-1]["old_value"] == OLD_SUMMARY
    assert revisions[-1]["property"] == "summary"
    assert graph.nodes[HEAD]["summary"] == NEW_TAIL
    _assert_closed_kernel_parents(graph, event_id=EVENT_FAILED)
    _assert_no_delete(session)


@pytest.mark.asyncio
async def test_tutti_i_cani_sono_usciti_invariato_vs_fase_21(monkeypatch):
    assert Settings.model_fields["ENABLE_EVENT_TRIAGE"].default is False
    monkeypatch.setattr("app.pipeline.judge.settings.ENABLE_EVENT_TRIAGE", False)

    graph = _three_dogs_scene()
    session = SceneSession(graph)
    result = await resolve_quantifier_scope(session, _closed_chunk(), concept_hint="cane")

    assert result.closed is True
    assert result.event_id
    assert set(result.member_ids) == {FIDO, REX, BOBI}
    assert SPOT not in result.member_ids

    event = graph.nodes[result.event_id]
    assert event["type"] == "event"
    assert event["name"].casefold() not in COLLECTIVE_NAMES
    collective = [
        n
        for n in graph.nodes.values()
        if n.get("type") != "event" and str(n.get("name") or "").casefold() in COLLECTIVE_NAMES
    ]
    assert collective == []

    participates = [
        rel
        for rel in graph.relations
        if rel.get("normalized_relation") == SITUATION_NORMALIZED_RELATION
    ]
    assert len(participates) == 3
    assert {rel["to_id"] for rel in participates} == {FIDO, REX, BOBI}
    assert all(
        rel["kernel_parent"] == RelationKernelType.Partecipativa.value for rel in participates
    )
    for rel in graph.relations:
        if rel["to_id"] == CUCINA and rel["from_id"] in {FIDO, REX, BOBI}:
            assert rel["is_latest"] is False
        kp = rel.get("kernel_parent")
        if kp:
            assert kp in CLOSED_KERNEL, kp
            assert kp not in FAMIGLIA_B
    _assert_no_delete(session)

    source = Path(resolve_quantifier_scope.__code__.co_filename).read_text(encoding="utf-8")
    assert "event_triage" not in source
    assert "_task_event_triage" not in source
    assert "assert_slot" not in source
    assert "run_event_triage" not in source

    monkeypatch.setattr("app.pipeline.judge.settings.ENABLE_EVENT_TRIAGE", True)
    graph_on = _three_dogs_scene()
    session_on = SceneSession(graph_on)
    again = await resolve_quantifier_scope(
        session_on, _closed_chunk(), concept_hint="cane"
    )
    assert again.closed is True
    assert set(again.member_ids) == {FIDO, REX, BOBI}
    assert SPOT not in again.member_ids
    _assert_no_delete(session_on)


@pytest.mark.asyncio
async def test_mike_and_story_fanout_direct_members_only(
    stub_ingestion_side_effects, monkeypatch
):
    _enable_triage(monkeypatch)

    async def fake_llm(_system, user, model, **_kwargs):
        assert model is EventTriageStep
        return _propose(
            _item(
                head=SAID_ONE,
                kernel_parent=AttributeKernelType.Descrizione.value,
                tail=BELLO,
                verbo="assert",
                fonte=FONTE,
            ),
            _item(
                head=MIKE,
                kernel_parent=RelationKernelType.SocialeIntenzionale.value,
                tail=SAID_TWO,
                verbo="retract",
                fonte=FONTE,
            ),
            _item(
                head=STORY,
                kernel_parent=RelationKernelType.Compositiva.value,
                tail=STORY_MEMBER,
                verbo="retract",
                fonte=FONTE,
            ),
            reasoning="Mike's direct facts + the named story's members, never MEMBER_OF",
        )

    monkeypatch.setattr("app.pipeline.event_triage.call_structured", fake_llm)

    graph = CorpusGraph()
    for nid, name in (
        (MIKE, "Mike"),
        (SAID_ONE, "detto 1"),
        (SAID_TWO, "detto 2"),
        (BELLO, "bellissimo"),
        (STORY, "la storia raccontata"),
        (STORY_MEMBER, "membro storia"),
        (COUSIN, "cugino transitivo"),
        (COUSIN_TAIL, "ok"),
        (HUMANS, "umani"),
        ("detto-1-stato", "prima"),
    ):
        graph.add_node(nid, name)
    graph.member_of[MIKE] = HUMANS
    graph.member_of[COUSIN] = HUMANS
    graph.isa[HUMANS] = "concept-mammals"
    graph.add_event(
        EVENT_MIKE,
        name="tutto quello che ha detto Mike / la storia è falsa",
        summary="tutto quello che ha detto Mike era bellissimo; la storia raccontata è falsa",
    )
    _prelink(
        graph,
        EVENT_MIKE,
        SAID_ONE,
        BELLO,
        MIKE,
        SAID_TWO,
        STORY,
        STORY_MEMBER,
    )
    _stamp_slot(
        graph,
        head_id=SAID_ONE,
        tail_id="detto-1-stato",
        kernel_parent=AttributeKernelType.Descrizione.value,
        fonte_id=FONTE,
    )
    _stamp_slot(
        graph,
        head_id=MIKE,
        tail_id=SAID_TWO,
        kernel_parent=RelationKernelType.SocialeIntenzionale.value,
        fonte_id=FONTE,
    )
    _stamp_slot(
        graph,
        head_id=STORY,
        tail_id=STORY_MEMBER,
        kernel_parent=RelationKernelType.Compositiva.value,
        fonte_id=FONTE,
    )
    cousin_sid = _stamp_slot(
        graph,
        head_id=COUSIN,
        tail_id=COUSIN_TAIL,
        kernel_parent=AttributeKernelType.Stato.value,
        fonte_id=FONTE,
    )
    session = FakeSession(graph)

    await run_event_triage(session, RUN_ID, touched_ids=[EVENT_MIKE])

    assert graph.triage_runs[EVENT_MIKE]["verdict"] == "confirmed"
    direct = {MIKE, SAID_ONE, SAID_TWO, BELLO, STORY, STORY_MEMBER, "detto-1-stato"}
    touched = _written_ids(graph, EVENT_MIKE)
    assert touched
    assert touched <= direct
    assert COUSIN not in touched
    assert COUSIN_TAIL not in touched
    cousin_edges = [rel for rel in graph.relations if rel["head_id"] == COUSIN]
    assert len(cousin_edges) == 1
    assert cousin_edges[0]["is_latest"] is True
    assert cousin_edges[0]["slot_id"] == cousin_sid
    assert cousin_edges[0]["caused_by_event_id"] == "seed"

    applied = [rel for rel in graph.relations if rel.get("caused_by_event_id") == EVENT_MIKE]
    assert 1 <= len(applied) <= EVENT_TRIAGE_MAX_SLOT_FANOUT
    _assert_closed_kernel_parents(graph, event_id=EVENT_MIKE)
    for rel in applied:
        assert rel["kernel_parent"] in CLOSED_KERNEL
        assert rel["kernel_parent"] not in FAMIGLIA_B
    _assert_no_delete(session)

    retraction_src = Path(resolve_retraction_scope.__code__.co_filename).read_text(
        encoding="utf-8"
    )
    assert "run_event_triage" not in retraction_src
    assert "assert_slot" not in retraction_src


def test_written_kernel_parents_are_closed_enum_members():
    for kp in CLOSED_KERNEL:
        assert kp not in FAMIGLIA_B
        try:
            member: AttributeKernelType | RelationKernelType = AttributeKernelType(kp)
        except ValueError:
            member = RelationKernelType(kp)
        assert member.value == kp
    assert SpecialRelationType.contradicts.value not in CLOSED_KERNEL
    assert "EXPLAINED_BY" not in CLOSED_KERNEL
    assert EVENT_TRIAGE_MAX_SLOT_FANOUT >= 1


def test_enable_event_triage_default_still_false():
    assert Settings.model_fields["ENABLE_EVENT_TRIAGE"].default is False
    assert Settings.model_fields["ENABLE_CONTEXT_LAYER"].default is False
