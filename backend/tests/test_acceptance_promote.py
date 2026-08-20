"""Fase 5 acceptance: PROMOTE atomicity, idempotence, lift without merge, μ. No Docker."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from app.core.config import Settings
from app.models.kernel import KERNEL_VERSION, EntityKernelType
from app.pipeline import promote as promote_mod
from app.pipeline.concepts import kernel_catch_all_concept_id
from app.pipeline.promote import (
    CREATE_PROMOTED_CONCEPT_CYPHER,
    FIND_CLUSTER_MEMBERS_CYPHER,
    FIND_CLUSTER_RELATIONS_CYPHER,
    FIND_CONCEPTS_IN_CLUSTER_CYPHER,
    FIND_DIRECT_NODE_MEMBERS_CYPHER,
    FIND_EXISTING_PROMOTED_CYPHER,
    FIND_FIRST_LEVEL_CATCH_ALL_CYPHER,
    FIND_KERNEL_CATCH_ALL_CYPHER,
    FIND_PARENT_CYPHER,
    LIFT_EXTERNAL_RELATION_CYPHER,
    LINK_PROMOTED_ISA_CYPHER,
    LOOKUP_TYPE_MIGRATION_ALIAS_CYPHER,
    MERGE_TYPE_MIGRATION_ALIAS_CYPHER,
    MOVE_MEMBER_OF_CYPHER,
    _execute_write,
    is_promotable_parent,
    is_skipped_relation,
    kernel_catch_all_ids,
    promote,
    promote_clusters,
    promoted_concept_id,
    update_bundle,
)

JOB_ID = "job-f5-promote"
EXTERNAL_ID = "x-out"


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


class PromoteGraph:
    def __init__(self) -> None:
        self.concepts: dict[str, dict] = {}
        self.nodes: dict[str, dict] = {}
        self.member_of: dict[str, str] = {}
        self.isa: dict[str, str] = {}
        self.relations: list[dict] = []
        self.aliases: list[dict] = []

    def snapshot(self) -> dict:
        return {
            "concepts": copy.deepcopy(self.concepts),
            "nodes": copy.deepcopy(self.nodes),
            "member_of": dict(self.member_of),
            "isa": dict(self.isa),
            "relations": copy.deepcopy(self.relations),
            "aliases": copy.deepcopy(self.aliases),
        }

    def restore(self, snap: dict) -> None:
        self.concepts = copy.deepcopy(snap["concepts"])
        self.nodes = copy.deepcopy(snap["nodes"])
        self.member_of = dict(snap["member_of"])
        self.isa = dict(snap["isa"])
        self.relations = copy.deepcopy(snap["relations"])
        self.aliases = copy.deepcopy(snap["aliases"])


def _apply_write(graph: PromoteGraph, cypher: str, kwargs: dict) -> None:
    if cypher == CREATE_PROMOTED_CONCEPT_CYPHER:
        cid = kwargs["concept_id"]
        graph.concepts[cid] = {
            "id": cid,
            "name": kwargs["name"],
            "kernel_category": kwargs["kernel_category"],
            "parent_uri": kwargs["parent_uri"],
            "promoted": True,
            "kernel_version": kwargs["kernel_version"],
            "definition": kwargs["definition"],
            "embedding": list(kwargs["embedding"]),
        }
        return
    if cypher == LINK_PROMOTED_ISA_CYPHER:
        graph.isa[kwargs["concept_id"]] = kwargs["parent_id"]
        return
    if cypher == MOVE_MEMBER_OF_CYPHER:
        parent_id = kwargs["parent_id"]
        concept_id = kwargs["concept_id"]
        for nid in kwargs["node_ids"]:
            if graph.member_of.get(nid) == parent_id:
                graph.member_of[nid] = concept_id
        return
    if cypher == LIFT_EXTERNAL_RELATION_CYPHER:
        for edge in kwargs.get("edges") or []:
            graph.relations.append(dict(edge))
        return
    if cypher == MERGE_TYPE_MIGRATION_ALIAS_CYPHER:
        concept_id = kwargs["concept_id"]
        for old_type in kwargs.get("types") or []:
            key = (old_type, old_type, concept_id)
            if any(
                (a["old_type"], a["new_type"], a["concept_id"]) == key
                for a in graph.aliases
            ):
                continue
            graph.aliases.append(
                {
                    "old_type": old_type,
                    "new_type": old_type,
                    "concept_id": concept_id,
                    "frozen_at": "frozen",
                }
            )


def _read_graph(graph: PromoteGraph, cypher: str, kwargs: dict) -> list[dict]:
    if cypher == FIND_PARENT_CYPHER:
        parent = graph.concepts.get(kwargs["parent_id"])
        if parent is None:
            return []
        return [
            {
                "id": parent["id"],
                "name": parent.get("name"),
                "kernel_category": parent.get("kernel_category"),
                "isa_parent_id": graph.isa.get(parent["id"]),
            }
        ]
    if cypher == FIND_CONCEPTS_IN_CLUSTER_CYPHER:
        return [{"id": cid} for cid in kwargs["cluster_ids"] if cid in graph.concepts]
    if cypher == FIND_CLUSTER_MEMBERS_CYPHER:
        parent_id = kwargs["parent_id"]
        cluster = set(kwargs["cluster_ids"])
        rows = []
        for nid in cluster:
            node = graph.nodes.get(nid)
            if node is None or graph.member_of.get(nid) != parent_id:
                continue
            rows.append(
                {
                    "id": nid,
                    "name": node.get("name"),
                    "summary": node.get("summary"),
                    "kernel_category": node.get("kernel_category"),
                    "labels": ["Node"],
                }
            )
        return rows
    if cypher == FIND_EXISTING_PROMOTED_CYPHER:
        found = graph.concepts.get(kwargs["concept_id"])
        return [{"id": found["id"]}] if found is not None else []
    if cypher == FIND_CLUSTER_RELATIONS_CYPHER:
        cluster = set(kwargs["cluster_ids"])
        rows = []
        for rel in graph.relations:
            if rel["src_id"] in cluster or rel["tgt_id"] in cluster:
                rows.append(
                    {
                        "src_id": rel["src_id"],
                        "tgt_id": rel["tgt_id"],
                        "relation": rel.get("relation"),
                        "kernel_parent": rel.get("kernel_parent"),
                        "normalized_relation": rel.get("normalized_relation"),
                        "witnesses_a": list(rel.get("witnesses_a") or []),
                        "witnesses_b": list(rel.get("witnesses_b") or []),
                    }
                )
        return rows
    if cypher == FIND_KERNEL_CATCH_ALL_CYPHER:
        return [{"id": cid} for cid in kwargs["kernel_ids"] if cid in graph.concepts]
    if cypher == FIND_FIRST_LEVEL_CATCH_ALL_CYPHER:
        kernel = set(kwargs["kernel_ids"])
        return [
            {"id": cid}
            for cid, parent in graph.isa.items()
            if parent in kernel and cid in graph.concepts
        ]
    if cypher == FIND_DIRECT_NODE_MEMBERS_CYPHER:
        parent_id = kwargs["parent_id"]
        rows = []
        for nid, home in graph.member_of.items():
            if home != parent_id or nid not in graph.nodes or nid in graph.concepts:
                continue
            node = graph.nodes[nid]
            rows.append(
                {
                    "id": nid,
                    "name": node.get("name"),
                    "summary": node.get("summary"),
                    "kernel_category": node.get("kernel_category"),
                }
            )
        return rows
    if cypher == LOOKUP_TYPE_MIGRATION_ALIAS_CYPHER:
        return [
            dict(alias)
            for alias in graph.aliases
            if alias["old_type"] == kwargs["old_type"]
            and alias["concept_id"] == kwargs["concept_id"]
        ]
    return []


class FakeTxn:
    """Buffers writes; drops the buffer if the callback raises (F5.9 atomicity)."""

    def __init__(self, graph: PromoteGraph, *, fail_on_lift: bool = False) -> None:
        self.graph = graph
        self.fail_on_lift = fail_on_lift
        self.buffer: list[tuple[str, dict]] = []
        self.calls: list[tuple[str, dict]] = []

    async def run(self, cypher, **kwargs):
        self.calls.append((cypher, kwargs))
        if self.fail_on_lift and cypher == LIFT_EXTERNAL_RELATION_CYPHER:
            raise RuntimeError("simulated interrupt after MOVE_MEMBER_OF")
        self.buffer.append((cypher, kwargs))
        return FakeResult([])


class PromoteFakeSession:
    def __init__(self, graph: PromoteGraph | None = None, *, fail_on_lift: bool = False) -> None:
        self.graph = graph or PromoteGraph()
        self.fail_on_lift = fail_on_lift
        self.calls: list[tuple[str, dict]] = []

    async def run(self, cypher, **kwargs):
        self.calls.append((cypher, kwargs))
        return FakeResult(_read_graph(self.graph, cypher, kwargs))

    async def execute_write(self, fn):
        txn = FakeTxn(self.graph, fail_on_lift=self.fail_on_lift)
        try:
            result = await fn(txn)
        except Exception:
            txn.buffer.clear()
            self.calls.extend(txn.calls)
            raise
        for cypher, kwargs in txn.buffer:
            _apply_write(self.graph, cypher, kwargs)
        txn.buffer.clear()
        self.calls.extend(txn.calls)
        return result


class AdapterSession:
    """No execute_write — exercises _execute_write fallback (fn(session))."""

    def __init__(self, graph: PromoteGraph) -> None:
        self.graph = graph
        self.calls: list[tuple[str, dict]] = []

    async def run(self, cypher, **kwargs):
        self.calls.append((cypher, kwargs))
        if cypher in {
            CREATE_PROMOTED_CONCEPT_CYPHER,
            LINK_PROMOTED_ISA_CYPHER,
            MOVE_MEMBER_OF_CYPHER,
            LIFT_EXTERNAL_RELATION_CYPHER,
            MERGE_TYPE_MIGRATION_ALIAS_CYPHER,
        }:
            _apply_write(self.graph, cypher, kwargs)
            return FakeResult([])
        return FakeResult(_read_graph(self.graph, cypher, kwargs))


def _compact(cypher: str) -> str:
    return " ".join(cypher.split())


def _seed_cluster(
    graph: PromoteGraph,
    *,
    n: int = 5,
    category: EntityKernelType = EntityKernelType.Agente,
    parent_id: str | None = None,
    with_payload: bool = True,
    with_external: bool = True,
) -> tuple[str, list[str]]:
    catch_all = kernel_catch_all_concept_id(category)
    graph.concepts[catch_all] = {
        "id": catch_all,
        "name": category.value,
        "kernel_category": category.value,
        "promoted": True,
    }
    home = parent_id or catch_all
    if home != catch_all and home not in graph.concepts:
        graph.concepts[home] = {
            "id": home,
            "name": "first-level",
            "kernel_category": category.value,
            "promoted": True,
        }
        graph.isa[home] = catch_all
    ids: list[str] = []
    for i in range(n):
        nid = f"n-{home[-6:]}-{i}" if parent_id else f"n-{i}"
        graph.nodes[nid] = {
            "id": nid,
            "name": f"Player {i}",
            "summary": f"An agent named Player {i}.",
            "kernel_category": category.value,
        }
        graph.member_of[nid] = home
        ids.append(nid)
    if with_payload and n >= 4:
        graph.relations.append(
            {
                "src_id": ids[0],
                "tgt_id": ids[1],
                "relation": "plays_with",
                "kernel_parent": "SocialeIntenzionale",
                "normalized_relation": "plays_with",
                "witnesses_a": ["w-a0"],
                "witnesses_b": ["w-b1"],
            }
        )
        graph.relations.append(
            {
                "src_id": ids[2],
                "tgt_id": ids[3],
                "relation": "coached_by",
                "kernel_parent": "Partecipativa",
                "normalized_relation": "coached_by",
                "witnesses_a": ["w-a2"],
                "witnesses_b": ["w-b3"],
            }
        )
    if with_external:
        if EXTERNAL_ID not in graph.nodes:
            graph.nodes[EXTERNAL_ID] = {
                "id": EXTERNAL_ID,
                "name": "Club",
                "summary": "A club.",
                "kernel_category": EntityKernelType.CostruttoSociale.value,
            }
        graph.relations.append(
            {
                "src_id": ids[0],
                "tgt_id": EXTERNAL_ID,
                "relation": "plays_for",
                "kernel_parent": "SocialeIntenzionale",
                "normalized_relation": "plays_for",
                "witnesses_a": ["leaf-wit"],
                "witnesses_b": ["club-wit"],
            }
        )
        graph.relations.append(
            {
                "src_id": ids[0],
                "tgt_id": EXTERNAL_ID,
                "relation": "contradicts",
                "kernel_parent": "contradicts",
                "normalized_relation": "contradicts",
                "witnesses_a": ["c-wit"],
                "witnesses_b": ["x-wit"],
            }
        )
    return home, ids


@pytest.fixture
def embed_stub(monkeypatch):
    monkeypatch.setattr("app.pipeline.promote.embeddings.embed", lambda _t: [0.1] * 8)


@pytest.mark.asyncio
async def test_promote_twice_is_noop_same_concept_id(embed_stub):
    session = PromoteFakeSession()
    parent, ids = _seed_cluster(session.graph)
    expected = promoted_concept_id(parent, ids)

    first = await promote(session, parent, ids)
    lifts_after_first = [
        rel
        for rel in session.graph.relations
        if rel.get("src_id") == first and rel.get("tgt_id") == EXTERNAL_ID
    ]
    homes_after_first = dict(session.graph.member_of)
    concepts_after_first = set(session.graph.concepts)

    second = await promote(session, parent, list(reversed(ids)))

    assert first == expected
    assert second == first
    assert session.graph.concepts[first]["promoted"] is True
    assert session.graph.isa[first] == parent
    for nid in ids:
        assert session.graph.member_of[nid] == first
    assert dict(session.graph.member_of) == homes_after_first
    assert set(session.graph.concepts) == concepts_after_first
    lifts_after_second = [
        rel
        for rel in session.graph.relations
        if rel.get("src_id") == first and rel.get("tgt_id") == EXTERNAL_ID
    ]
    assert lifts_after_second == lifts_after_first


@pytest.mark.asyncio
async def test_interrupt_after_move_rolls_back_entire_txn(embed_stub):
    graph = PromoteGraph()
    parent, ids = _seed_cluster(graph)
    snap = graph.snapshot()
    session = PromoteFakeSession(graph, fail_on_lift=True)

    with pytest.raises(RuntimeError, match="simulated interrupt"):
        await promote(session, parent, ids)

    assert graph.snapshot() == snap
    for nid in ids:
        assert graph.member_of[nid] == parent
    assert promoted_concept_id(parent, ids) not in graph.concepts
    assert graph.aliases == []
    assert any(cypher == MOVE_MEMBER_OF_CYPHER for cypher, _kw in session.calls)
    assert any(cypher == LIFT_EXTERNAL_RELATION_CYPHER for cypher, _kw in session.calls)


@pytest.mark.asyncio
async def test_external_lift_creates_new_edge_without_merge(embed_stub):
    session = PromoteFakeSession()
    parent, ids = _seed_cluster(session.graph)
    s_id = promoted_concept_id(parent, ids)
    session.graph.relations.append(
        {
            "src_id": s_id,
            "tgt_id": EXTERNAL_ID,
            "relation": "plays_for",
            "kernel_parent": "SocialeIntenzionale",
            "normalized_relation": "plays_for",
            "witnesses_a": ["pre-existing"],
            "witnesses_b": ["club-wit"],
        }
    )

    result = await promote(session, parent, ids)

    assert result == s_id
    plays_for = [
        rel
        for rel in session.graph.relations
        if rel.get("src_id") == s_id
        and rel.get("tgt_id") == EXTERNAL_ID
        and rel.get("relation") == "plays_for"
    ]
    assert len(plays_for) == 2
    lifted = [rel for rel in plays_for if rel.get("lifted_from") == ids[0]]
    assert len(lifted) == 1
    assert ids[0] in lifted[0]["witnesses_a"]
    assert "leaf-wit" in lifted[0]["witnesses_a"]
    assert any(
        rel["src_id"] == ids[0] and rel["tgt_id"] == EXTERNAL_ID
        for rel in session.graph.relations
    )
    contradicts_lifted = [
        rel
        for rel in session.graph.relations
        if rel.get("src_id") == s_id and rel.get("relation") == "contradicts"
    ]
    assert contradicts_lifted == []


@pytest.mark.asyncio
async def test_internal_type_frozen_on_type_migration_alias(embed_stub):
    session = PromoteFakeSession()
    parent, ids = _seed_cluster(session.graph)

    s_id = await promote(session, parent, ids)

    lookup = await session.run(
        LOOKUP_TYPE_MIGRATION_ALIAS_CYPHER,
        old_type="plays_with",
        concept_id=s_id,
    )
    row = await lookup.single()
    assert row is not None
    assert row["old_type"] == "plays_with"
    assert row["new_type"] == "plays_with"
    assert row["concept_id"] == s_id
    assert row["frozen_at"]

    lookup2 = await session.run(
        LOOKUP_TYPE_MIGRATION_ALIAS_CYPHER,
        old_type="coached_by",
        concept_id=s_id,
    )
    row2 = await lookup2.single()
    assert row2 is not None
    assert row2["new_type"] == "coached_by"


@pytest.mark.asyncio
async def test_mdl_fail_writes_nothing(embed_stub):
    session = PromoteFakeSession()
    parent, ids = _seed_cluster(session.graph, n=2, with_payload=False)
    snap = session.graph.snapshot()

    result = await promote(session, parent, ids)

    assert result == ""
    assert session.graph.snapshot() == snap


@pytest.mark.asyncio
async def test_filter_cluster_writes_nothing(embed_stub):
    session = PromoteFakeSession()
    parent, ids = _seed_cluster(session.graph)
    snap = session.graph.snapshot()

    result = await promote(session, parent, ids, definition_kind="value_filter")

    assert result == ""
    assert session.graph.snapshot() == snap


@pytest.mark.asyncio
async def test_does_not_promote_concept_of_concepts(embed_stub):
    session = PromoteFakeSession()
    kernel = kernel_catch_all_concept_id(EntityKernelType.Agente)
    session.graph.concepts[kernel] = {
        "id": kernel,
        "name": "Agente",
        "kernel_category": EntityKernelType.Agente.value,
        "promoted": True,
    }
    child_ids = []
    for i in range(5):
        cid = f"genre-{i}"
        session.graph.concepts[cid] = {
            "id": cid,
            "name": f"Genre {i}",
            "kernel_category": EntityKernelType.Agente.value,
            "promoted": True,
        }
        session.graph.isa[cid] = kernel
        child_ids.append(cid)
    snap = session.graph.snapshot()

    result = await promote(session, kernel, child_ids)

    assert result == ""
    assert session.graph.snapshot() == snap


@pytest.mark.asyncio
async def test_deeper_than_first_level_is_noop(embed_stub):
    session = PromoteFakeSession()
    kernel = kernel_catch_all_concept_id(EntityKernelType.Agente)
    first = "genre-first"
    deeper = "genre-deeper"
    session.graph.concepts[kernel] = {
        "id": kernel,
        "name": "Agente",
        "kernel_category": EntityKernelType.Agente.value,
        "promoted": True,
    }
    session.graph.concepts[first] = {
        "id": first,
        "name": "Calciatore",
        "kernel_category": EntityKernelType.Agente.value,
        "promoted": True,
    }
    session.graph.isa[first] = kernel
    parent, ids = _seed_cluster(session.graph, parent_id=deeper)
    session.graph.isa[deeper] = first
    snap = session.graph.snapshot()

    result = await promote(session, parent, ids)

    assert result == ""
    assert session.graph.snapshot() == snap
    assert is_promotable_parent(kernel, None) is True
    assert is_promotable_parent(first, kernel) is False
    assert is_promotable_parent(deeper, first) is False


@pytest.mark.asyncio
async def test_first_level_parent_is_no_longer_allowed(embed_stub):
    """Capped at exactly one Concept layer under each kernel vertex (no clustering
    criterion yet — see is_promotable_parent's docstring). A first-level Concept
    (already promoted once) can no longer itself be a parent for a further
    promotion; this used to be allowed up to a second layer."""
    session = PromoteFakeSession()
    first = "genre-first"
    parent, ids = _seed_cluster(session.graph, parent_id=first)
    snap = session.graph.snapshot()

    result = await promote(session, parent, ids)

    assert result == ""
    assert session.graph.snapshot() == snap


@pytest.mark.asyncio
async def test_promote_clusters_publishes_and_respects_kill_switch(embed_stub, monkeypatch):
    published: list[dict] = []

    async def spy_publish(job_id, stage, event, payload):
        published.append(
            {"job_id": job_id, "stage": stage, "event": event, "payload": payload}
        )

    monkeypatch.setattr("app.pipeline.promote.event_bus.publish", spy_publish)
    monkeypatch.setattr("app.pipeline.promote.settings.ENABLE_PROMOTE", True)
    session = PromoteFakeSession()
    _seed_cluster(session.graph)

    written = await promote_clusters(session, JOB_ID)

    assert written == 1
    promoted_events = [m for m in published if m["event"] == "cluster_promoted"]
    assert len(promoted_events) == 1
    assert promoted_events[0]["stage"] == "promote_clusters"
    assert promoted_events[0]["payload"]["member_count"] == 5
    assert promoted_events[0]["payload"]["parent_id"] in kernel_catch_all_ids()

    monkeypatch.setattr("app.pipeline.promote.settings.ENABLE_PROMOTE", False)
    session2 = PromoteFakeSession()
    _seed_cluster(session2.graph)
    assert await promote_clusters(session2, JOB_ID) == 0
    assert session2.calls == []


@pytest.mark.asyncio
async def test_execute_write_adapter_without_method(embed_stub):
    graph = PromoteGraph()
    parent, ids = _seed_cluster(graph)
    session = AdapterSession(graph)

    s_id = await promote(session, parent, ids)

    assert s_id
    assert graph.isa[s_id] == parent
    for nid in ids:
        assert graph.member_of[nid] == s_id


@pytest.mark.asyncio
async def test_execute_write_adapter_calls_fn_directly():
    calls: list[str] = []

    class Bare:
        pass

    async def work(tx):
        calls.append("work")
        assert tx is session
        return "ok"

    session = Bare()
    result = await _execute_write(session, work)
    assert result == "ok"
    assert calls == ["work"]


def test_cypher_shapes_create_not_merge_and_skip_backbone():
    lift = _compact(LIFT_EXTERNAL_RELATION_CYPHER)
    assert "CREATE (src)-[:Relation" in lift
    assert "MERGE (src)-[:Relation" not in lift
    assert "DELETE" not in lift
    move = _compact(MOVE_MEMBER_OF_CYPHER)
    assert "DELETE old" in move
    assert "CREATE (n)-[:MEMBER_OF]->(s)" in move
    assert "promoted: true" in CREATE_PROMOTED_CONCEPT_CYPHER
    assert "kernel_version" in CREATE_PROMOTED_CONCEPT_CYPHER
    assert KERNEL_VERSION == "1.0.0"
    alias = _compact(MERGE_TYPE_MIGRATION_ALIAS_CYPHER)
    assert ":TypeMigrationAlias" in alias
    assert "old_type: old_type" in alias
    assert "new_type: old_type" in alias
    assert is_skipped_relation("CONTRADICTS")
    assert is_skipped_relation("same_as")
    assert is_skipped_relation("IS_A")
    assert is_skipped_relation("MEMBER_OF")
    assert is_skipped_relation("HAS_CONCEPT")
    assert is_skipped_relation("DERIVED_FROM")
    assert not is_skipped_relation("plays_for")
    members = _compact(FIND_DIRECT_NODE_MEMBERS_CYPHER)
    assert "MATCH (n:Node)-[:MEMBER_OF]->(c:Concept {id: $parent_id})" in members
    assert "NOT n:Concept" in members


def test_enable_promote_setting_default_false():
    """Off by default: no clustering criterion exists yet to split a catch-all's
    members into more than one sub-genre (see is_promotable_parent)."""
    assert Settings.model_fields["ENABLE_PROMOTE"].default is False


def test_update_bundle_is_create_lift_called_from_work():
    source = Path(promote_mod.__file__).read_text(encoding="utf-8")
    assert "async def update_bundle" in source
    assert "await update_bundle(" in source
    lift = _compact(LIFT_EXTERNAL_RELATION_CYPHER)
    assert "CREATE (src)-[:Relation" in lift
    assert "MERGE (src)-[:Relation" not in lift


@pytest.mark.asyncio
async def test_promote_invokes_update_bundle(embed_stub, monkeypatch):
    called: list[dict] = []
    orig = update_bundle

    async def spy(tx, *, promoted_concept_id, lift_edges):
        called.append({"id": promoted_concept_id, "n": len(lift_edges)})
        await orig(tx, promoted_concept_id=promoted_concept_id, lift_edges=lift_edges)

    monkeypatch.setattr(promote_mod, "update_bundle", spy)
    session = PromoteFakeSession()
    parent, ids = _seed_cluster(session.graph)
    s_id = await promote(session, parent, ids)
    assert called
    assert called[0]["id"] == s_id
    assert called[0]["n"] >= 1
    lifted = [
        rel
        for rel in session.graph.relations
        if rel.get("src_id") == s_id
        and rel.get("relation") == "plays_for"
        and rel.get("lifted_from")
    ]
    assert len(lifted) == 1
    assert "CREATE (src)-[:Relation" in LIFT_EXTERNAL_RELATION_CYPHER
