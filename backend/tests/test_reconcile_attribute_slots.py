"""Macrotask 2: attribute LWW by (head, kernel_parent). FakeSession, no Neo4j."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.kernel import AttributeKernelType, RelationKernelType
from app.pipeline.event_slots import assert_slot, slot_id_for
from app.pipeline.reconcile import (
    FIND_ATTRIBUTE_SLOT_EDGES_CYPHER,
    RECONCILE_SCOPED_RELATIONS_CYPHER,
    SET_ATTRIBUTE_SLOT_IS_LATEST_CYPHER,
    SET_ATTRIBUTE_SLOT_UPDATES_CYPHER,
    reconcile_scoped_attribute_slots,
    reconcile_scoped_relations,
)
from tests.test_event_slots import (
    FAILED,
    FONTE,
    HEAD,
    OK,
    OTHER,
    PLACE,
    FakeSession,
    _attr_slot,
    _rel_slot,
    _seed,
)

RECONCILE_PATH = Path(reconcile_scoped_attribute_slots.__code__.co_filename)
PIPELINE = RECONCILE_PATH.parent


@pytest.fixture
def stub_ingestion_side_effects(monkeypatch):
    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.pipeline.ingestion.deposit_from_asserted_fact", _noop)
    monkeypatch.setattr("app.pipeline.ingestion.embeddings.embed", lambda _t: [0.1] * 8)


def _compact(cypher: str) -> str:
    return " ".join(cypher.split())


def test_attribute_reconcile_cypher_does_not_clobber_type_or_delete():
    source = RECONCILE_PATH.read_text(encoding="utf-8")
    assert "DELETE" not in source
    assert "merge_nodes" not in source
    for query in (
        FIND_ATTRIBUTE_SLOT_EDGES_CYPHER,
        SET_ATTRIBUTE_SLOT_IS_LATEST_CYPHER,
        SET_ATTRIBUTE_SLOT_UPDATES_CYPHER,
    ):
        assert "$" in query
        assert "DELETE" not in query
        assert "normalized_relation = 'updates'" not in query
        assert "SET r.normalized_relation" not in query
        assert "SET r.kernel_parent" not in query


def test_existing_pair_reconcile_cypher_unchanged():
    compact = _compact(RECONCILE_SCOPED_RELATIONS_CYPHER)
    assert "WITH a, b, collect(r) AS rels" in compact
    assert "normalized_relation = 'updates'" in compact
    assert "a.id IN $node_ids OR b.id IN $node_ids" in compact
    assert "kernel_parent" not in RECONCILE_SCOPED_RELATIONS_CYPHER
    assert "slot_id" not in RECONCILE_SCOPED_RELATIONS_CYPHER


def test_fase_path_does_not_call_attribute_reconcile():
    for name in ("dreaming.py", "ingestion.py", "judge.py"):
        text = (PIPELINE / name).read_text(encoding="utf-8")
        assert "reconcile_scoped_attribute_slots" not in text


@pytest.mark.asyncio
async def test_empty_head_ids_is_noop_without_run():
    session = FakeSession(_seed(FAILED))
    drift = await reconcile_scoped_attribute_slots(session, head_ids=[])
    assert drift == 0
    assert session.calls == []


@pytest.mark.asyncio
async def test_two_attribute_asserts_lww_and_updates_link(stub_ingestion_side_effects):
    graph = _seed(FAILED, OK)
    session = FakeSession(graph)
    slot = _attr_slot()
    await assert_slot(session, slot=slot, tail_id_or_value=FAILED, fonte_id=FONTE)
    await assert_slot(session, slot=slot, tail_id_or_value=OK, fonte_id="fonte-other")

    assert len(graph.relations) == 2
    latest = [rel for rel in graph.relations if rel["is_latest"] is True]
    previous = [rel for rel in graph.relations if rel["is_latest"] is False]
    assert len(latest) == 1
    assert len(previous) == 1
    assert latest[0]["tail_id"] == OK
    assert previous[0]["tail_id"] == FAILED
    assert latest[0].get("updates") == FAILED
    assert latest[0]["normalized_relation"] == "has_state"
    assert previous[0]["normalized_relation"] == "has_state"
    assert latest[0]["kernel_parent"] == AttributeKernelType.Stato.value
    assert previous[0]["kernel_parent"] == AttributeKernelType.Stato.value
    assert {rel["slot_id"] for rel in graph.relations} == {slot_id_for(slot, OK)}


@pytest.mark.asyncio
async def test_direct_reconcile_on_two_latest_attribute_edges():
    graph = _seed(FAILED, OK)
    sid = slot_id_for(_attr_slot(), FAILED)
    graph.relations.extend(
        [
            {
                "head_id": HEAD,
                "tail_id": FAILED,
                "relation": "has_state",
                "normalized_relation": "has_state",
                "is_latest": True,
                "kernel_parent": AttributeKernelType.Stato.value,
                "slot_id": sid,
                "updates": None,
                "created_at": 1,
            },
            {
                "head_id": HEAD,
                "tail_id": OK,
                "relation": "has_state",
                "normalized_relation": "has_state",
                "is_latest": True,
                "kernel_parent": AttributeKernelType.Stato.value,
                "slot_id": sid,
                "updates": None,
                "created_at": 2,
            },
        ]
    )
    session = FakeSession(graph)
    drift = await reconcile_scoped_attribute_slots(session, head_ids=[HEAD], slot_id=sid)

    assert drift == 1
    by_tail = {rel["tail_id"]: rel for rel in graph.relations}
    assert by_tail[OK]["is_latest"] is True
    assert by_tail[FAILED]["is_latest"] is False
    assert by_tail[OK]["updates"] == FAILED
    assert by_tail[OK]["normalized_relation"] == "has_state"
    assert len(graph.relations) == 2

    second = await reconcile_scoped_attribute_slots(session, head_ids=[HEAD], slot_id=sid)
    assert second == 0
    assert by_tail[OK]["is_latest"] is True
    assert by_tail[FAILED]["is_latest"] is False


@pytest.mark.asyncio
async def test_single_latest_edge_is_noop(stub_ingestion_side_effects):
    graph = _seed(FAILED)
    session = FakeSession(graph)
    await assert_slot(
        session, slot=_attr_slot(FAILED), tail_id_or_value=FAILED, fonte_id=FONTE
    )
    set_latest = sum(1 for cy, _ in session.calls if cy is SET_ATTRIBUTE_SLOT_IS_LATEST_CYPHER)
    set_updates = sum(1 for cy, _ in session.calls if cy is SET_ATTRIBUTE_SLOT_UPDATES_CYPHER)
    assert graph.relations[0]["is_latest"] is True
    assert set_latest == 0
    assert set_updates == 0


@pytest.mark.asyncio
async def test_relation_kernel_slots_are_not_collapsed(stub_ingestion_side_effects):
    graph = _seed(PLACE, OTHER)
    session = FakeSession(graph)
    await assert_slot(
        session,
        slot=_rel_slot(PLACE),
        tail_id_or_value=PLACE,
        fonte_id=FONTE,
        relation="located_in",
    )
    await assert_slot(
        session,
        slot=_rel_slot(OTHER),
        tail_id_or_value=OTHER,
        fonte_id=FONTE,
        relation="located_in",
    )
    drift = await reconcile_scoped_attribute_slots(session, head_ids=[HEAD])

    assert drift == 0
    assert len(graph.relations) == 2
    assert len({rel["slot_id"] for rel in graph.relations}) == 2
    assert all(rel["is_latest"] is True for rel in graph.relations)
    assert all(rel.get("updates") in (None, "") for rel in graph.relations)
    assert all(rel["kernel_parent"] == RelationKernelType.Spaziale.value for rel in graph.relations)
    find_calls = [kw for cy, kw in session.calls if cy is FIND_ATTRIBUTE_SLOT_EDGES_CYPHER]
    assert find_calls[-1]["head_ids"] == [HEAD]


@pytest.mark.asyncio
async def test_scoped_relations_reconcile_does_not_see_attribute_value_change(
    monkeypatch, stub_ingestion_side_effects
):
    graph = _seed(FAILED, OK)
    session = FakeSession(graph)
    slot = _attr_slot()
    await assert_slot(session, slot=slot, tail_id_or_value=FAILED, fonte_id=FONTE)
    await assert_slot(session, slot=slot, tail_id_or_value=OK, fonte_id="fonte-other")

    recorded: list[tuple[str, dict]] = []

    class PairResult:
        async def single(self):
            return {"driftCount": 0}

    class PairSession:
        async def run(self, cypher, **kwargs):
            recorded.append((cypher, kwargs))
            return PairResult()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class PairDriver:
        def session(self):
            return PairSession()

    monkeypatch.setattr("app.pipeline.reconcile.get_driver", lambda: PairDriver())
    count = await reconcile_scoped_relations([HEAD, FAILED, OK])
    assert count == 0
    assert recorded[0][0] is RECONCILE_SCOPED_RELATIONS_CYPHER
    by_tail = {rel["tail_id"]: rel for rel in graph.relations}
    assert by_tail[OK]["is_latest"] is True
    assert by_tail[FAILED]["is_latest"] is False
