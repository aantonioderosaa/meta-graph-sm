"""Macrotask 1: slot_id + witness OR-Set. FakeSession, no Neo4j, no OpenAI."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.kernel import AttributeKernelType, RelationKernelType
from app.pipeline.event_slots import (
    FIND_CONFLICTING_LATEST_CYPHER,
    FIND_SLOT_RELATIONS_CYPHER,
    STAMP_SLOT_ON_LATEST_CYPHER,
    UPDATE_SLOT_EDGE_CYPHER,
    Slot,
    assert_slot,
    retract_slot,
    slot_discriminant,
    slot_id,
    slot_id_for,
)
from app.pipeline.ingestion import CREATE_CONTRADICTS_CYPHER, CREATE_NODE_RELATION_CYPHER

HEAD = "esperimento-5"
FONTE = "fonte-mike"
FAILED = "tail-failed"
OK = "tail-ok"
PLACE = "tail-lab"
OTHER = "tail-home"
MODULE_PATH = Path(assert_slot.__code__.co_filename)


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


class SlotGraph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.relations: list[dict] = []
        self.contradicts: list[dict] = []
        self._clock = 0

    def add_node(self, node_id: str, name: str = "") -> None:
        self.nodes[node_id] = {"id": node_id, "name": name or node_id}

    def next_clock(self) -> int:
        self._clock += 1
        return self._clock


class FakeSession:
    def __init__(self, graph: SlotGraph | None = None) -> None:
        self.graph = graph or SlotGraph()
        self.calls: list[tuple[str, dict]] = []

    async def run(self, cypher: str, parameters: dict | None = None, **kwargs):
        params = dict(parameters or {})
        params.update(kwargs)
        self.calls.append((cypher, params))
        return FakeResult(_dispatch(self.graph, cypher, params))


def _dispatch(graph: SlotGraph, cypher: str, kwargs: dict) -> list[dict]:
    if cypher is CREATE_NODE_RELATION_CYPHER:
        if kwargs["head_id"] not in graph.nodes or kwargs["tail_id"] not in graph.nodes:
            return []
        graph.relations.append(
            {
                "head_id": kwargs["head_id"],
                "tail_id": kwargs["tail_id"],
                "relation": kwargs.get("relation"),
                "normalized_relation": kwargs.get("normalized_relation"),
                "is_latest": True,
                "kernel_parent": kwargs.get("kernel_parent"),
                "witnesses_a": list(kwargs.get("witnesses_a") or []),
                "witnesses_b": list(kwargs.get("witnesses_b") or []),
                "witness_source_ids": [],
                "witness_target_ids": [],
                "witness_add_tags": [],
                "slot_id": None,
                "created_at": graph.next_clock(),
            }
        )
        return []
    if cypher is STAMP_SLOT_ON_LATEST_CYPHER:
        matches = [
            rel
            for rel in graph.relations
            if rel["head_id"] == kwargs["head_id"]
            and rel["tail_id"] == kwargs["tail_id"]
            and rel.get("kernel_parent") == kwargs["kernel_parent"]
            and (rel.get("normalized_relation") or "") == kwargs["normalized_relation"]
            and rel.get("is_latest") is True
            and not rel.get("slot_id")
        ]
        matches.sort(key=lambda rel: rel.get("created_at") or 0, reverse=True)
        if matches:
            rel = matches[0]
            rel["slot_id"] = kwargs["slot_id"]
            rel["witness_source_ids"] = list(kwargs.get("witness_source_ids") or [])
            rel["witness_target_ids"] = list(kwargs.get("witness_target_ids") or [])
            rel["witness_add_tags"] = list(kwargs.get("witness_add_tags") or [])
            rel["witnesses_a"] = list(kwargs.get("witnesses_a") or [])
            rel["witnesses_b"] = list(kwargs.get("witnesses_b") or [])
        return []
    if cypher is FIND_SLOT_RELATIONS_CYPHER:
        return [
            {
                "tail_id": rel["tail_id"],
                "is_latest": rel.get("is_latest"),
                "witness_source_ids": list(rel.get("witness_source_ids") or []),
                "witness_target_ids": list(rel.get("witness_target_ids") or []),
                "witness_add_tags": list(rel.get("witness_add_tags") or []),
                "witnesses_a": list(rel.get("witnesses_a") or []),
                "witnesses_b": list(rel.get("witnesses_b") or []),
                "relation": rel.get("relation"),
                "kernel_parent": rel.get("kernel_parent"),
                "normalized_relation": rel.get("normalized_relation"),
            }
            for rel in graph.relations
            if rel["head_id"] == kwargs["head_id"] and rel.get("slot_id") == kwargs["slot_id"]
        ]
    if cypher is UPDATE_SLOT_EDGE_CYPHER:
        for rel in graph.relations:
            if (
                rel["head_id"] == kwargs["head_id"]
                and rel["tail_id"] == kwargs["tail_id"]
                and rel.get("slot_id") == kwargs["slot_id"]
            ):
                rel["witness_source_ids"] = list(kwargs.get("witness_source_ids") or [])
                rel["witness_target_ids"] = list(kwargs.get("witness_target_ids") or [])
                rel["witness_add_tags"] = list(kwargs.get("witness_add_tags") or [])
                rel["witnesses_a"] = list(kwargs.get("witnesses_a") or [])
                rel["witnesses_b"] = list(kwargs.get("witnesses_b") or [])
                rel["is_latest"] = bool(kwargs["is_latest"])
        return []
    if cypher is FIND_CONFLICTING_LATEST_CYPHER:
        for rel in graph.relations:
            if (
                rel["head_id"] == kwargs["head_id"]
                and rel.get("kernel_parent") == kwargs["kernel_parent"]
                and rel.get("is_latest") is True
                and rel["tail_id"] != kwargs["tail_id"]
            ):
                return [
                    {
                        "tail_id": rel["tail_id"],
                        "relation": rel.get("relation"),
                        "kernel_parent": rel.get("kernel_parent"),
                    }
                ]
        return []
    if cypher is CREATE_CONTRADICTS_CYPHER:
        graph.contradicts.append(
            {
                "left_id": kwargs["left_id"],
                "right_id": kwargs["right_id"],
                "subject_id": kwargs["subject_id"],
                "relation": kwargs.get("relation"),
                "kernel_parent": kwargs.get("kernel_parent"),
            }
        )
        return []
    raise AssertionError(f"unexpected cypher:\n{cypher}")


def _attr_slot(tail_id: str | None = None) -> Slot:
    return Slot(
        head_id=HEAD,
        kernel_parent=AttributeKernelType.Stato.value,
        normalized_relation="has_state",
        tail_id=tail_id,
    )


def _rel_slot(tail_id: str) -> Slot:
    return Slot(
        head_id=HEAD,
        kernel_parent=RelationKernelType.Spaziale.value,
        normalized_relation="located_in",
        tail_id=tail_id,
    )


def _seed(*node_ids: str) -> SlotGraph:
    graph = SlotGraph()
    graph.add_node(HEAD, "esperimento 5")
    for node_id in node_ids:
        graph.add_node(node_id)
    return graph


@pytest.fixture
def stub_ingestion_side_effects(monkeypatch):
    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.pipeline.ingestion.deposit_from_asserted_fact", _noop)
    monkeypatch.setattr("app.pipeline.ingestion.embeddings.embed", lambda _t: [0.1] * 8)


def test_attribute_vs_relation_discriminant():
    attr_failed = slot_id(
        HEAD,
        AttributeKernelType.Stato,
        slot_discriminant(AttributeKernelType.Stato, "has_state", FAILED),
    )
    attr_ok = slot_id(
        HEAD,
        AttributeKernelType.Stato,
        slot_discriminant(AttributeKernelType.Stato, "has_state", OK),
    )
    assert attr_failed == attr_ok
    a = Slot(HEAD, AttributeKernelType.Stato.value, "has_state", tail_id=FAILED)
    b = Slot(HEAD, AttributeKernelType.Stato.value, "has_state", tail_id=OK)
    assert slot_id_for(a) == slot_id_for(b)

    rel_lab = slot_id(
        HEAD,
        RelationKernelType.Spaziale,
        slot_discriminant(RelationKernelType.Spaziale, "located_in", PLACE),
    )
    rel_home = slot_id(
        HEAD,
        RelationKernelType.Spaziale,
        slot_discriminant(RelationKernelType.Spaziale, "located_in", OTHER),
    )
    assert rel_lab != rel_home


def test_slot_id_is_stable_and_not_concept_suffixed():
    first = slot_id(HEAD, AttributeKernelType.Stato, "has_state")
    second = slot_id(HEAD, "Stato", "has_state")
    assert first == second
    assert not first.endswith("_concept")
    assert len(first) == 64


def test_event_slots_module_has_no_delete_keyword():
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "DELETE" not in source


def test_d2_cypher_is_parameterized_constants():
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "session.run(f\"" not in source
    assert "session.run(f'" not in source
    assert ".format(" not in source
    for name, query in (
        ("FIND_SLOT_RELATIONS_CYPHER", FIND_SLOT_RELATIONS_CYPHER),
        ("STAMP_SLOT_ON_LATEST_CYPHER", STAMP_SLOT_ON_LATEST_CYPHER),
        ("UPDATE_SLOT_EDGE_CYPHER", UPDATE_SLOT_EDGE_CYPHER),
        ("FIND_CONFLICTING_LATEST_CYPHER", FIND_CONFLICTING_LATEST_CYPHER),
    ):
        assert "$" in query, name
        assert query.count("{") == query.count("}")


@pytest.mark.asyncio
async def test_double_assert_same_slot_fonte_one_witness_id(stub_ingestion_side_effects):
    graph = _seed(FAILED)
    session = FakeSession(graph)
    slot = _attr_slot(FAILED)
    await assert_slot(session, slot=slot, tail_id_or_value=FAILED, fonte_id=FONTE)
    await assert_slot(session, slot=slot, tail_id_or_value=FAILED, fonte_id=FONTE)

    assert len(graph.relations) == 1
    ids = graph.relations[0]["witness_source_ids"]
    assert ids.count(FONTE) == 1
    assert len(ids) == 1
    assert graph.relations[0]["is_latest"] is True
    creates = [cy for cy, _ in session.calls if cy is CREATE_NODE_RELATION_CYPHER]
    assert len(creates) == 1


@pytest.mark.asyncio
async def test_retract_never_asserted_is_noop(stub_ingestion_side_effects):
    graph = _seed(FAILED)
    session = FakeSession(graph)
    slot = _attr_slot(FAILED)
    await assert_slot(session, slot=slot, tail_id_or_value=FAILED, fonte_id=FONTE)
    before = graph.relations[0]["is_latest"]
    before_ids = list(graph.relations[0]["witness_source_ids"])
    updates_before = sum(1 for cy, _ in session.calls if cy is UPDATE_SLOT_EDGE_CYPHER)

    await retract_slot(session, slot=slot, fonte_id="fonte-never-seen")

    assert graph.relations[0]["is_latest"] is before
    assert graph.relations[0]["witness_source_ids"] == before_ids
    updates_after = sum(1 for cy, _ in session.calls if cy is UPDATE_SLOT_EDGE_CYPHER)
    assert updates_after == updates_before


@pytest.mark.asyncio
async def test_retract_unknown_slot_is_noop_no_exception(stub_ingestion_side_effects):
    session = FakeSession(_seed(FAILED))
    await retract_slot(session, slot=_attr_slot(FAILED), fonte_id=FONTE)
    assert session.graph.relations == []


@pytest.mark.asyncio
async def test_retract_then_reassert_resurrects_slot(stub_ingestion_side_effects):
    graph = _seed(FAILED)
    session = FakeSession(graph)
    slot = _attr_slot(FAILED)
    await assert_slot(session, slot=slot, tail_id_or_value=FAILED, fonte_id=FONTE)
    await retract_slot(session, slot=slot, fonte_id=FONTE)

    assert len(graph.relations) == 1
    assert graph.relations[0]["is_latest"] is False
    assert FONTE not in graph.relations[0]["witness_source_ids"]

    await assert_slot(session, slot=slot, tail_id_or_value=FAILED, fonte_id=FONTE)

    assert len(graph.relations) == 1
    assert graph.relations[0]["is_latest"] is True
    assert graph.relations[0]["witness_source_ids"].count(FONTE) == 1
    creates = [cy for cy, _ in session.calls if cy is CREATE_NODE_RELATION_CYPHER]
    assert len(creates) == 1


@pytest.mark.asyncio
async def test_empty_fonte_id_raises_on_assert_and_retract(stub_ingestion_side_effects):
    session = FakeSession(_seed(FAILED))
    slot = _attr_slot(FAILED)
    with pytest.raises(ValueError, match="fonte_id"):
        await assert_slot(session, slot=slot, tail_id_or_value=FAILED, fonte_id="")
    with pytest.raises(ValueError, match="fonte_id"):
        await assert_slot(session, slot=slot, tail_id_or_value=FAILED, fonte_id="   ")
    with pytest.raises(ValueError, match="fonte_id"):
        await retract_slot(session, slot=slot, fonte_id="")
    with pytest.raises(ValueError, match="fonte_id"):
        await retract_slot(session, slot=slot, fonte_id=None)  # type: ignore[arg-type]
    assert session.graph.relations == []


@pytest.mark.asyncio
async def test_attribute_different_tails_share_slot_id_and_may_create(
    stub_ingestion_side_effects,
):
    graph = _seed(FAILED, OK)
    session = FakeSession(graph)
    slot = _attr_slot()
    await assert_slot(session, slot=slot, tail_id_or_value=FAILED, fonte_id=FONTE)
    await assert_slot(session, slot=slot, tail_id_or_value=OK, fonte_id="fonte-other")

    assert len(graph.relations) == 2
    slot_ids = {rel["slot_id"] for rel in graph.relations}
    assert len(slot_ids) == 1
    assert all(rel["is_latest"] is True for rel in graph.relations)


@pytest.mark.asyncio
async def test_relation_different_tails_are_two_slots(stub_ingestion_side_effects):
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

    assert len(graph.relations) == 2
    slot_ids = {rel["slot_id"] for rel in graph.relations}
    assert len(slot_ids) == 2
    assert all(rel["witness_source_ids"] == [FONTE] for rel in graph.relations)


@pytest.mark.asyncio
async def test_assert_queries_are_known_constants(stub_ingestion_side_effects):
    graph = _seed(FAILED)
    session = FakeSession(graph)
    await assert_slot(session, slot=_attr_slot(FAILED), tail_id_or_value=FAILED, fonte_id=FONTE)
    allowed = {
        FIND_SLOT_RELATIONS_CYPHER,
        CREATE_NODE_RELATION_CYPHER,
        STAMP_SLOT_ON_LATEST_CYPHER,
        UPDATE_SLOT_EDGE_CYPHER,
        FIND_CONFLICTING_LATEST_CYPHER,
        CREATE_CONTRADICTS_CYPHER,
    }
    assert all(cypher in allowed for cypher, _ in session.calls)
