"""Macrotask 1–3: slot OR-Set, provenance, node revisions. FakeSession, no Neo4j."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.kernel import AttributeKernelType, RelationKernelType
from app.pipeline.event_slots import (
    APPEND_NODE_REVISION_CYPHER,
    FIND_CONFLICTING_LATEST_CYPHER,
    FIND_SLOT_RELATIONS_CYPHER,
    MATCH_BOTH_NODES_CYPHER,
    READ_NODE_SCALAR_CYPHER,
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
from app.pipeline.reconcile import (
    FIND_ATTRIBUTE_SLOT_EDGES_CYPHER,
    SET_ATTRIBUTE_SLOT_IS_LATEST_CYPHER,
    SET_ATTRIBUTE_SLOT_UPDATES_CYPHER,
)

HEAD = "esperimento-5"
FONTE = "fonte-mike"
FAILED = "tail-failed"
OK = "tail-ok"
PLACE = "tail-lab"
OTHER = "tail-home"
EVENT_ID = "evento-1"
RUN_ID = "run-1"
PROV = {"caused_by_event_id": EVENT_ID, "run_id": RUN_ID}
MODULE_PATH = Path(assert_slot.__code__.co_filename)
PIPELINE = MODULE_PATH.parent


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

    def add_node(self, node_id: str, name: str = "", **extra) -> None:
        node = {"id": node_id, "name": name or node_id, "revisions": []}
        node.update(extra)
        self.nodes[node_id] = node

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
    if cypher is MATCH_BOTH_NODES_CYPHER:
        head_id = kwargs["head_id"]
        tail_id = kwargs["tail_id"]
        if head_id in graph.nodes and tail_id in graph.nodes:
            return [{"head_id": head_id, "tail_id": tail_id}]
        return []
    if cypher is CREATE_NODE_RELATION_CYPHER:
        # Neo4j MATCH (h:Node), (t:Node) CREATE writes 0 rows if either is absent.
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
                "updates": None,
                "caused_by_event_id": None,
                "run_id": None,
                "created_at": graph.next_clock(),
            }
        )
        return []
    if cypher is STAMP_SLOT_ON_LATEST_CYPHER:
        if kwargs["head_id"] not in graph.nodes or kwargs["tail_id"] not in graph.nodes:
            return []
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
            rel["caused_by_event_id"] = kwargs.get("caused_by_event_id")
            rel["run_id"] = kwargs.get("run_id")
        return []
    if cypher is FIND_SLOT_RELATIONS_CYPHER:
        if kwargs["head_id"] not in graph.nodes:
            return []
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
        if kwargs["head_id"] not in graph.nodes or kwargs["tail_id"] not in graph.nodes:
            return []
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
                rel["caused_by_event_id"] = kwargs.get("caused_by_event_id")
                rel["run_id"] = kwargs.get("run_id")
        return []
    if cypher is FIND_ATTRIBUTE_SLOT_EDGES_CYPHER:
        slot_id_filter = kwargs.get("slot_id")
        kernels = set(kwargs.get("attribute_kernels") or [])
        head_ids = set(kwargs.get("head_ids") or [])
        return [
            {
                "head_id": rel["head_id"],
                "tail_id": rel["tail_id"],
                "slot_id": rel.get("slot_id"),
                "kernel_parent": rel.get("kernel_parent"),
                "is_latest": rel.get("is_latest"),
                "created_at": rel.get("created_at"),
                "updates": rel.get("updates"),
            }
            for rel in graph.relations
            if rel["head_id"] in head_ids
            and rel.get("kernel_parent") in kernels
            and (slot_id_filter is None or rel.get("slot_id") == slot_id_filter)
        ]
    if cypher is SET_ATTRIBUTE_SLOT_IS_LATEST_CYPHER:
        slot_id_filter = kwargs.get("slot_id")
        for rel in graph.relations:
            if (
                rel["head_id"] == kwargs["head_id"]
                and rel["tail_id"] == kwargs["tail_id"]
                and rel.get("kernel_parent") == kwargs["kernel_parent"]
                and (slot_id_filter is None or rel.get("slot_id") == slot_id_filter)
            ):
                rel["is_latest"] = bool(kwargs["is_latest"])
        return []
    if cypher is SET_ATTRIBUTE_SLOT_UPDATES_CYPHER:
        slot_id_filter = kwargs.get("slot_id")
        for rel in graph.relations:
            if (
                rel["head_id"] == kwargs["head_id"]
                and rel["tail_id"] == kwargs["tail_id"]
                and rel.get("kernel_parent") == kwargs["kernel_parent"]
                and (slot_id_filter is None or rel.get("slot_id") == slot_id_filter)
            ):
                rel["updates"] = kwargs.get("updates")
        return []
    if cypher is READ_NODE_SCALAR_CYPHER:
        node = graph.nodes.get(kwargs["node_id"])
        if not node:
            return []
        prop = kwargs["property_name"]
        revisions = node.get("revisions")
        return [
            {
                "current_value": node.get(prop),
                "revisions": list(revisions) if revisions is not None else None,
            }
        ]
    if cypher is APPEND_NODE_REVISION_CYPHER:
        node = graph.nodes.get(kwargs["node_id"])
        if not node:
            return []
        prop = kwargs["property_name"]
        revisions = list(node.get("revisions") or [])
        revision = dict(kwargs["revision"])
        revisions.append(revision)
        node["revisions"] = revisions
        node[prop] = kwargs["new_value"]
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


def _edges_missing_provenance(graph: SlotGraph) -> list[dict]:
    missing: list[dict] = []
    for rel in graph.relations:
        event = rel.get("caused_by_event_id")
        rid = rel.get("run_id")
        if event is None or not str(event).strip() or rid is None or not str(rid).strip():
            missing.append(rel)
    return missing


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
        ("MATCH_BOTH_NODES_CYPHER", MATCH_BOTH_NODES_CYPHER),
        ("STAMP_SLOT_ON_LATEST_CYPHER", STAMP_SLOT_ON_LATEST_CYPHER),
        ("UPDATE_SLOT_EDGE_CYPHER", UPDATE_SLOT_EDGE_CYPHER),
        ("FIND_CONFLICTING_LATEST_CYPHER", FIND_CONFLICTING_LATEST_CYPHER),
        ("READ_NODE_SCALAR_CYPHER", READ_NODE_SCALAR_CYPHER),
        ("APPEND_NODE_REVISION_CYPHER", APPEND_NODE_REVISION_CYPHER),
    ):
        assert "$" in query, name
        assert query.count("{") == query.count("}")


@pytest.mark.asyncio
async def test_double_assert_same_slot_fonte_one_witness_id(stub_ingestion_side_effects):
    graph = _seed(FAILED)
    session = FakeSession(graph)
    slot = _attr_slot(FAILED)
    first = await assert_slot(
        session, slot=slot, tail_id_or_value=FAILED, fonte_id=FONTE, **PROV
    )
    second = await assert_slot(
        session, slot=slot, tail_id_or_value=FAILED, fonte_id=FONTE, **PROV
    )
    assert first is True
    assert second is True

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
    await assert_slot(session, slot=slot, tail_id_or_value=FAILED, fonte_id=FONTE, **PROV)
    before = graph.relations[0]["is_latest"]
    before_ids = list(graph.relations[0]["witness_source_ids"])
    updates_before = sum(1 for cy, _ in session.calls if cy is UPDATE_SLOT_EDGE_CYPHER)

    wrote = await retract_slot(session, slot=slot, fonte_id="fonte-never-seen", **PROV)
    assert wrote is False

    assert graph.relations[0]["is_latest"] is before
    assert graph.relations[0]["witness_source_ids"] == before_ids
    updates_after = sum(1 for cy, _ in session.calls if cy is UPDATE_SLOT_EDGE_CYPHER)
    assert updates_after == updates_before


@pytest.mark.asyncio
async def test_retract_unknown_slot_is_noop_no_exception(stub_ingestion_side_effects):
    session = FakeSession(_seed(FAILED))
    wrote = await retract_slot(session, slot=_attr_slot(FAILED), fonte_id=FONTE, **PROV)
    assert wrote is False
    assert session.graph.relations == []


@pytest.mark.asyncio
async def test_retract_then_reassert_resurrects_slot(stub_ingestion_side_effects):
    graph = _seed(FAILED)
    session = FakeSession(graph)
    slot = _attr_slot(FAILED)
    await assert_slot(session, slot=slot, tail_id_or_value=FAILED, fonte_id=FONTE, **PROV)
    await retract_slot(session, slot=slot, fonte_id=FONTE, **PROV)

    assert len(graph.relations) == 1
    assert graph.relations[0]["is_latest"] is False
    assert FONTE not in graph.relations[0]["witness_source_ids"]

    await assert_slot(session, slot=slot, tail_id_or_value=FAILED, fonte_id=FONTE, **PROV)

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
        await assert_slot(session, slot=slot, tail_id_or_value=FAILED, fonte_id="", **PROV)
    with pytest.raises(ValueError, match="fonte_id"):
        await assert_slot(session, slot=slot, tail_id_or_value=FAILED, fonte_id="   ", **PROV)
    with pytest.raises(ValueError, match="fonte_id"):
        await retract_slot(session, slot=slot, fonte_id="", **PROV)
    with pytest.raises(ValueError, match="fonte_id"):
        await retract_slot(session, slot=slot, fonte_id=None, **PROV)  # type: ignore[arg-type]
    assert session.graph.relations == []


@pytest.mark.asyncio
async def test_attribute_different_tails_share_slot_id_and_may_create(
    stub_ingestion_side_effects,
):
    graph = _seed(FAILED, OK)
    session = FakeSession(graph)
    slot = _attr_slot()
    await assert_slot(session, slot=slot, tail_id_or_value=FAILED, fonte_id=FONTE, **PROV)
    await assert_slot(session, slot=slot, tail_id_or_value=OK, fonte_id="fonte-other", **PROV)

    assert len(graph.relations) == 2
    slot_ids = {rel["slot_id"] for rel in graph.relations}
    assert len(slot_ids) == 1
    latest = [rel for rel in graph.relations if rel["is_latest"] is True]
    assert len(latest) == 1
    assert latest[0]["tail_id"] == OK
    previous = next(rel for rel in graph.relations if rel["tail_id"] == FAILED)
    assert previous["is_latest"] is False
    assert latest[0].get("updates") == FAILED
    assert all(rel["normalized_relation"] == "has_state" for rel in graph.relations)
    assert all(rel["kernel_parent"] == AttributeKernelType.Stato.value for rel in graph.relations)


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
        **PROV,
    )
    await assert_slot(
        session,
        slot=_rel_slot(OTHER),
        tail_id_or_value=OTHER,
        fonte_id=FONTE,
        relation="located_in",
        **PROV,
    )

    assert len(graph.relations) == 2
    slot_ids = {rel["slot_id"] for rel in graph.relations}
    assert len(slot_ids) == 2
    assert all(rel["witness_source_ids"] == [FONTE] for rel in graph.relations)


@pytest.mark.asyncio
async def test_assert_queries_are_known_constants(stub_ingestion_side_effects):
    graph = _seed(FAILED)
    session = FakeSession(graph)
    await assert_slot(
        session, slot=_attr_slot(FAILED), tail_id_or_value=FAILED, fonte_id=FONTE, **PROV
    )
    allowed = {
        MATCH_BOTH_NODES_CYPHER,
        FIND_SLOT_RELATIONS_CYPHER,
        CREATE_NODE_RELATION_CYPHER,
        STAMP_SLOT_ON_LATEST_CYPHER,
        UPDATE_SLOT_EDGE_CYPHER,
        FIND_CONFLICTING_LATEST_CYPHER,
        CREATE_CONTRADICTS_CYPHER,
        FIND_ATTRIBUTE_SLOT_EDGES_CYPHER,
        SET_ATTRIBUTE_SLOT_IS_LATEST_CYPHER,
        SET_ATTRIBUTE_SLOT_UPDATES_CYPHER,
        READ_NODE_SCALAR_CYPHER,
        APPEND_NODE_REVISION_CYPHER,
    }
    assert all(cypher in allowed for cypher, _ in session.calls)


def test_fase_path_does_not_call_slot_writes_or_revisions():
    for name in (
        "dreaming.py",
        "ingestion.py",
        "judge.py",
    ):
        text = (PIPELINE / name).read_text(encoding="utf-8")
        assert "assert_slot" not in text
        assert "retract_slot" not in text
        assert "append_node_revision" not in text


@pytest.mark.asyncio
async def test_assert_and_retract_stamp_non_null_provenance(stub_ingestion_side_effects):
    graph = _seed(FAILED)
    session = FakeSession(graph)
    slot = _attr_slot(FAILED)
    await assert_slot(session, slot=slot, tail_id_or_value=FAILED, fonte_id=FONTE, **PROV)

    assert graph.relations[0]["caused_by_event_id"] == EVENT_ID
    assert graph.relations[0]["run_id"] == RUN_ID
    assert _edges_missing_provenance(graph) == []

    await retract_slot(
        session,
        slot=slot,
        fonte_id=FONTE,
        caused_by_event_id="evento-retract",
        run_id="run-retract",
    )
    assert graph.relations[0]["caused_by_event_id"] == "evento-retract"
    assert graph.relations[0]["run_id"] == "run-retract"
    assert _edges_missing_provenance(graph) == []


@pytest.mark.asyncio
async def test_empty_caused_by_event_id_and_run_id_raise(stub_ingestion_side_effects):
    session = FakeSession(_seed(FAILED))
    slot = _attr_slot(FAILED)
    with pytest.raises(ValueError, match="caused_by_event_id"):
        await assert_slot(
            session,
            slot=slot,
            tail_id_or_value=FAILED,
            fonte_id=FONTE,
            caused_by_event_id="",
            run_id=RUN_ID,
        )
    with pytest.raises(ValueError, match="caused_by_event_id"):
        await assert_slot(
            session,
            slot=slot,
            tail_id_or_value=FAILED,
            fonte_id=FONTE,
            caused_by_event_id="   ",
            run_id=RUN_ID,
        )
    with pytest.raises(ValueError, match="run_id"):
        await assert_slot(
            session,
            slot=slot,
            tail_id_or_value=FAILED,
            fonte_id=FONTE,
            caused_by_event_id=EVENT_ID,
            run_id="",
        )
    with pytest.raises(ValueError, match="caused_by_event_id"):
        await retract_slot(
            session,
            slot=slot,
            fonte_id=FONTE,
            caused_by_event_id=None,  # type: ignore[arg-type]
            run_id=RUN_ID,
        )
    with pytest.raises(ValueError, match="run_id"):
        await retract_slot(
            session,
            slot=slot,
            fonte_id=FONTE,
            caused_by_event_id=EVENT_ID,
            run_id="  ",
        )
    assert session.graph.relations == []


@pytest.mark.asyncio
async def test_fake_session_create_requires_both_endpoint_nodes():
    graph = SlotGraph()
    graph.add_node(HEAD)
    session = FakeSession(graph)
    await session.run(
        CREATE_NODE_RELATION_CYPHER,
        head_id=HEAD,
        tail_id="ghost-tail",
        relation="has_state",
        normalized_relation="has_state",
        embedding=None,
        kernel_parent=AttributeKernelType.Stato.value,
        witnesses_a=[],
        witnesses_b=[],
        witness_text="",
        valid_time=None,
        provenance=None,
    )
    assert graph.relations == []
    await session.run(
        CREATE_NODE_RELATION_CYPHER,
        head_id="ghost-head",
        tail_id=FAILED,
        relation="has_state",
        normalized_relation="has_state",
        embedding=None,
        kernel_parent=AttributeKernelType.Stato.value,
        witnesses_a=[],
        witnesses_b=[],
        witness_text="",
        valid_time=None,
        provenance=None,
    )
    assert graph.relations == []


@pytest.mark.parametrize("missing", ["head", "tail"])
@pytest.mark.asyncio
async def test_assert_missing_endpoint_returns_false_skips_write(
    stub_ingestion_side_effects, missing: str
):
    graph = _seed(FAILED) if missing == "head" else _seed()
    session = FakeSession(graph)
    slot = (
        Slot(
            head_id="ghost-head",
            kernel_parent=AttributeKernelType.Stato.value,
            normalized_relation="has_state",
            tail_id=FAILED,
        )
        if missing == "head"
        else _attr_slot("ghost-tail")
    )
    tail = FAILED if missing == "head" else "ghost-tail"

    wrote = await assert_slot(
        session, slot=slot, tail_id_or_value=tail, fonte_id=FONTE, **PROV
    )

    assert wrote is False
    assert graph.relations == []
    write_cypher = {
        CREATE_NODE_RELATION_CYPHER,
        STAMP_SLOT_ON_LATEST_CYPHER,
        UPDATE_SLOT_EDGE_CYPHER,
    }
    assert not any(cy in write_cypher for cy, _ in session.calls)
    assert any(cy is MATCH_BOTH_NODES_CYPHER for cy, _ in session.calls)


@pytest.mark.asyncio
async def test_retract_existing_fonte_returns_true_and_updates(
    stub_ingestion_side_effects,
):
    graph = _seed(FAILED)
    session = FakeSession(graph)
    slot = _attr_slot(FAILED)
    assert await assert_slot(
        session, slot=slot, tail_id_or_value=FAILED, fonte_id=FONTE, **PROV
    )
    updates_before = sum(1 for cy, _ in session.calls if cy is UPDATE_SLOT_EDGE_CYPHER)

    wrote = await retract_slot(session, slot=slot, fonte_id=FONTE, **PROV)

    assert wrote is True
    assert graph.relations[0]["is_latest"] is False
    assert FONTE not in graph.relations[0]["witness_source_ids"]
    updates_after = sum(1 for cy, _ in session.calls if cy is UPDATE_SLOT_EDGE_CYPHER)
    assert updates_after == updates_before + 1

