"""Fase 18 acceptance: incremental ingest freshness + scoped comparison. No Docker."""

from __future__ import annotations

import pytest

from app.pipeline.entity_relation_resolution import (
    APPLY_DIFFERENT_TAIL_SUPERSEDES_CYPHER,
    APPLY_DIFFERENT_TAIL_UPDATED_BY_CYPHER,
    FIND_DIFFERENT_TAIL_PAIRS_CYPHER,
    reconcile_different_tail_pairs,
)
from app.pipeline.ingestion import CREATE_CONTRADICTS_CYPHER
from app.pipeline.judge import FIND_MISSED_CONTRADICTIONS_CYPHER, run_judge
from app.pipeline.node_resolution import (
    COPY_MISSING_KERNEL_CATEGORY_CYPHER,
    FIND_MERGED_INTO_CYPHER,
    PROMOTE_NEWER_SUMMARY_CYPHER,
    READ_NODE_SNAPSHOT_CYPHER,
    SET_MERGED_INTO_CYPHER,
    merge_nodes,
    node_history,
)
from tests.test_acceptance_judge import JudgeGraph

JOB_ID = "job-graph-update-f18"


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


class GraphUpdateSession:
    """In-memory graph for merge freshness, history, and different-tail compare."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.relations: list[dict] = []
        self.famiglia: list[dict] = []
        self.calls: list[tuple[str, dict]] = []

    def add_node(self, node_id: str, **props) -> None:
        self.nodes[node_id] = {"id": node_id, **props}

    def add_relation(self, src_id: str, dst_id: str, **props) -> None:
        row = {
            "id": props.get("id") or f"rel-{src_id}-{dst_id}-{len(self.relations)}",
            "src": src_id,
            "dst": dst_id,
            "is_latest": True,
            **props,
        }
        self.relations.append(row)

    def _has_famiglia(self, a: str, b: str, rel_type: str) -> bool:
        return any(
            edge["rel_type"] == rel_type and {edge["src"], edge["dst"]} == {a, b}
            for edge in self.famiglia
        )

    def add_famiglia(self, src_id: str, rel_type: str, dst_id: str, **props) -> None:
        self.famiglia.append(
            {"src": src_id, "dst": dst_id, "rel_type": rel_type, "props": dict(props)}
        )

    def _node_snapshot(self, node_id: str) -> dict | None:
        node = self.nodes.get(node_id)
        if node is None:
            return None
        return {
            "id": node["id"],
            "summary": node.get("summary") or "",
            "created_at": node.get("created_at"),
        }

    async def run(self, cypher, **kwargs):
        self.calls.append((cypher, kwargs))
        if cypher == SET_MERGED_INTO_CYPHER:
            dup = self.nodes.get(kwargs["dup_id"])
            if dup is not None:
                dup["merged_into"] = kwargs["canon_id"]
            return FakeResult([])

        if cypher == PROMOTE_NEWER_SUMMARY_CYPHER:
            canon = self.nodes.get(kwargs["canon_id"])
            dup = self.nodes.get(kwargs["dup_id"])
            if canon is None or dup is None:
                return FakeResult([])
            newest = dup.get("summary") or ""
            dup_created = dup.get("created_at")
            canon_created = canon.get("created_at")
            if not newest or dup_created is None:
                return FakeResult([])
            if canon_created is not None and not (dup_created > canon_created):
                return FakeResult([])
            previous = canon.get("summary") or ""
            canon["summary"] = newest
            dup["summary"] = newest if not previous else previous
            return FakeResult([])

        if cypher == COPY_MISSING_KERNEL_CATEGORY_CYPHER:
            canon = self.nodes.get(kwargs["canon_id"])
            dup = self.nodes.get(kwargs["dup_id"])
            if canon is None or dup is None:
                return FakeResult([])
            if not (canon.get("kernel_category") or "") and (dup.get("kernel_category") or ""):
                canon["kernel_category"] = dup["kernel_category"]
            return FakeResult([])

        if cypher == READ_NODE_SNAPSHOT_CYPHER:
            row = self._node_snapshot(kwargs["node_id"])
            return FakeResult([row] if row is not None else [])

        if cypher == FIND_MERGED_INTO_CYPHER:
            parent = kwargs["canon_id"]
            rows = [
                self._node_snapshot(nid)
                for nid, node in self.nodes.items()
                if node.get("merged_into") == parent
            ]
            return FakeResult([row for row in rows if row is not None])

        if cypher == FIND_DIFFERENT_TAIL_PAIRS_CYPHER:
            touched = {str(nid) for nid in (kwargs.get("touched_ids") or []) if nid}
            rows = []
            latest = [rel for rel in self.relations if rel.get("is_latest", True)]
            for i, left in enumerate(latest):
                for right in latest[i + 1 :]:
                    if left["src"] != right["src"]:
                        continue
                    if touched and str(left["src"]) not in touched:
                        continue
                    t1, t2 = left["dst"], right["dst"]
                    if t1 == t2:
                        continue
                    first, second = (left, right) if str(t1) < str(t2) else (right, left)
                    t1, t2 = first["dst"], second["dst"]
                    kp1 = first.get("kernel_parent") or ""
                    kp2 = second.get("kernel_parent") or ""
                    if kp1 != kp2:
                        continue
                    if self._has_famiglia(t1, t2, "CONTRADICTS"):
                        continue
                    if self._has_famiglia(t1, t2, "SUPERSEDES"):
                        continue
                    if self._has_famiglia(t1, t2, "UPDATED_BY"):
                        continue
                    rows.append(
                        {
                            "head_id": first["src"],
                            "tail_a": t1,
                            "tail_b": t2,
                            "relation_a": first.get("relation") or "",
                            "relation_b": second.get("relation") or "",
                            "created_a": first.get("created_at"),
                            "created_b": second.get("created_at"),
                            "kernel_parent": kp1,
                        }
                    )
            return FakeResult(rows)

        if cypher == APPLY_DIFFERENT_TAIL_SUPERSEDES_CYPHER:
            self._apply_different_tail(kwargs, "SUPERSEDES")
            return FakeResult([])

        if cypher == APPLY_DIFFERENT_TAIL_UPDATED_BY_CYPHER:
            self._apply_different_tail(kwargs, "UPDATED_BY")
            return FakeResult([])

        if cypher == CREATE_CONTRADICTS_CYPHER:
            self.add_famiglia(
                kwargs["left_id"],
                "CONTRADICTS",
                kwargs["right_id"],
                subject_id=kwargs.get("subject_id"),
                relation=kwargs.get("relation"),
                kernel_parent=kwargs.get("kernel_parent"),
            )
            return FakeResult([])

        return FakeResult([])

    def _apply_different_tail(self, kwargs: dict, rel_type: str) -> None:
        old_tail = kwargs["old_tail_id"]
        new_tail = kwargs["new_tail_id"]
        head = kwargs["head_id"]
        kernel = kwargs.get("kernel_parent") or ""
        old_relation = kwargs.get("old_relation") or ""
        new_relation = kwargs.get("new_relation") or ""
        for rel in self.relations:
            if (
                rel["src"] == head
                and rel["dst"] == old_tail
                and rel.get("is_latest", True)
                and (rel.get("kernel_parent") or "") == kernel
                and (rel.get("relation") or "") == old_relation
            ):
                rel["is_latest"] = False
            if (
                rel["src"] == head
                and rel["dst"] == new_tail
                and (rel.get("kernel_parent") or "") == kernel
                and (rel.get("relation") or "") == new_relation
            ):
                pass
        if not self._has_famiglia(old_tail, new_tail, rel_type):
            self.add_famiglia(
                new_tail,
                rel_type,
                old_tail,
                subject_id=head,
            )


class QueueSession:
    """Minimal queue FakeSession for counting comparison queries."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def run(self, cypher, **kwargs):
        self.calls.append((cypher, kwargs))
        return FakeResult([])


def _is_missed_query(cypher: str) -> bool:
    return cypher == FIND_MISSED_CONTRADICTIONS_CYPHER


@pytest.mark.asyncio
async def test_a_canonical_summary_is_newest_previous_via_history():
    session = GraphUpdateSession()
    session.add_node(
        "alice-canon",
        name="Alice",
        summary="Alice works at Acme",
        created_at="2020-01-01T00:00:00",
        kernel_category="Agente",
    )
    session.add_node(
        "alice-dup",
        name="Alice",
        summary="Alice now works at Globex",
        created_at="2024-06-01T00:00:00",
        kernel_category="Agente",
    )

    await merge_nodes(session, "alice-dup", "alice-canon")

    assert session.nodes["alice-canon"]["summary"] == "Alice now works at Globex"
    assert session.nodes["alice-dup"]["merged_into"] == "alice-canon"
    assert "alice-dup" in session.nodes
    history = await node_history(session, "alice-canon")
    summaries = {snap.summary for snap in history}
    assert "Alice now works at Globex" in summaries
    assert "Alice works at Acme" in summaries
    assert [snap.id for snap in history] == ["alice-canon", "alice-dup"]
    assert not any("DETACH DELETE" in cypher for cypher, _ in session.calls)
    assert not any("DELETE (n:Node" in cypher for cypher, _ in session.calls)


@pytest.mark.asyncio
async def test_a_history_walks_fused_then_fused_again():
    session = GraphUpdateSession()
    session.add_node(
        "canon",
        summary="v1",
        created_at="2020-01-01T00:00:00",
    )
    session.add_node(
        "mid",
        summary="v2",
        created_at="2021-01-01T00:00:00",
        merged_into="canon",
    )
    session.add_node(
        "leaf",
        summary="v3",
        created_at="2022-01-01T00:00:00",
        merged_into="mid",
    )

    history = await node_history(session, "canon")
    assert [snap.id for snap in history] == ["canon", "mid", "leaf"]
    assert [snap.summary for snap in history] == ["v1", "v2", "v3"]


@pytest.mark.asyncio
async def test_b_succession_marker_resolves_in_batch_without_judge():
    session = GraphUpdateSession()
    session.add_node("mario")
    session.add_node("acme")
    session.add_node("globex")
    session.add_relation(
        "mario",
        "acme",
        relation="Mario lavora ad Acme",
        kernel_parent="Partecipativa",
        is_latest=True,
        created_at="2020-01-01T00:00:00",
    )
    session.add_relation(
        "mario",
        "globex",
        relation="da allora Mario ora è dipendente Globex",
        kernel_parent="Partecipativa",
        is_latest=True,
        created_at="2024-01-01T00:00:00",
    )
    relation_count_before = len(session.relations)
    node_ids_before = set(session.nodes)

    applied = await reconcile_different_tail_pairs(session, {"mario"})

    assert applied == 1
    assert session._has_famiglia("acme", "globex", "SUPERSEDES")
    assert not session._has_famiglia("acme", "globex", "CONTRADICTS")
    old = next(rel for rel in session.relations if rel["dst"] == "acme")
    neu = next(rel for rel in session.relations if rel["dst"] == "globex")
    assert old["is_latest"] is False
    assert neu["is_latest"] is True
    assert len(session.relations) == relation_count_before
    assert set(session.nodes) == node_ids_before
    assert not any("DELETE" in cypher and ":Node" in cypher for cypher, _ in session.calls)
    assert not any(
        "DELETE" in cypher and ":Relation" in cypher and "SET old.is_latest" not in cypher
        for cypher, _ in session.calls
    )
    assert not any(_is_missed_query(cypher) for cypher, _ in session.calls)


@pytest.mark.asyncio
async def test_c_missed_contradictions_touched_ids_skips_outside_batch():
    graph = JudgeGraph()
    graph.add_node("batch-head")
    graph.add_node("t-batch-a")
    graph.add_node("t-batch-b")
    graph.add_node("other-head")
    graph.add_node("t-other-a")
    graph.add_node("t-other-b")
    graph.add_relation(
        "batch-head",
        "t-batch-a",
        relation="Fonte A: ha vinto il torneo nel 2010.",
        kernel_parent="Temporale",
        is_latest=True,
    )
    graph.add_relation(
        "batch-head",
        "t-batch-b",
        relation="Fonte B: ha vinto il torneo nel 2011.",
        kernel_parent="Temporale",
        is_latest=True,
    )
    graph.add_relation(
        "other-head",
        "t-other-a",
        relation="Fonte A: ha vinto il torneo nel 2010.",
        kernel_parent="Temporale",
        is_latest=True,
    )
    graph.add_relation(
        "other-head",
        "t-other-b",
        relation="Fonte B: ha vinto il torneo nel 2011.",
        kernel_parent="Temporale",
        is_latest=True,
    )

    stats = await run_judge(graph, JOB_ID, touched_ids=["batch-head"])

    missed_calls = [kw for cy, kw in graph.calls if _is_missed_query(cy)]
    assert missed_calls
    assert missed_calls[0]["touched_ids"] == ["batch-head"]
    assert stats.missed_contradictions == 1
    assert graph._has_famiglia("t-batch-a", "t-batch-b", "CONTRADICTS")
    assert not graph._has_famiglia("t-other-a", "t-other-b", "CONTRADICTS")


@pytest.mark.asyncio
async def test_c_omitted_touched_ids_still_full_scans():
    graph = JudgeGraph()
    graph.add_node("h1")
    graph.add_node("a1")
    graph.add_node("a2")
    graph.add_node("h2")
    graph.add_node("b1")
    graph.add_node("b2")
    graph.add_relation("h1", "a1", relation="nel 2010", kernel_parent="Temporale")
    graph.add_relation("h1", "a2", relation="nel 2011", kernel_parent="Temporale")
    graph.add_relation("h2", "b1", relation="nel 2010", kernel_parent="Temporale")
    graph.add_relation("h2", "b2", relation="nel 2011", kernel_parent="Temporale")

    stats = await run_judge(graph, JOB_ID)

    missed_calls = [kw for cy, kw in graph.calls if _is_missed_query(cy)]
    assert missed_calls
    assert missed_calls[0]["touched_ids"] == []
    assert stats.missed_contradictions == 2
    assert graph._has_famiglia("a1", "a2", "CONTRADICTS")
    assert graph._has_famiglia("b1", "b2", "CONTRADICTS")


@pytest.mark.asyncio
async def test_d_empty_touched_short_circuits_zero_queries():
    session = QueueSession()
    applied = await reconcile_different_tail_pairs(session, set())
    assert applied == 0
    assert session.calls == []


@pytest.mark.asyncio
async def test_d_non_overlapping_touched_does_not_compare_prior_pairs():
    session = GraphUpdateSession()
    session.add_node("weah")
    session.add_node("t-2010")
    session.add_node("t-2011")
    session.add_node("unrelated")
    session.add_relation(
        "weah",
        "t-2010",
        relation="ha vinto nel 2010",
        kernel_parent="Temporale",
        is_latest=True,
        created_at="2020-01-01T00:00:00",
    )
    session.add_relation(
        "weah",
        "t-2011",
        relation="ha vinto nel 2011",
        kernel_parent="Temporale",
        is_latest=True,
        created_at="2021-01-01T00:00:00",
    )

    applied = await reconcile_different_tail_pairs(session, {"unrelated"})

    assert applied == 0
    find_calls = [kw for cy, kw in session.calls if cy == FIND_DIFFERENT_TAIL_PAIRS_CYPHER]
    assert len(find_calls) == 1
    assert find_calls[0]["touched_ids"] == ["unrelated"]
    assert session.famiglia == []
    writes = [
        cy
        for cy, _ in session.calls
        if cy
        in {
            APPLY_DIFFERENT_TAIL_SUPERSEDES_CYPHER,
            APPLY_DIFFERENT_TAIL_UPDATED_BY_CYPHER,
            CREATE_CONTRADICTS_CYPHER,
        }
    ]
    assert writes == []


def test_no_new_delete_of_node_in_f18_helpers():
    from pathlib import Path

    sources = [
        Path(__file__).resolve().parents[1] / "app/pipeline/entity_relation_resolution.py",
        Path(__file__).resolve().parents[1] / "app/pipeline/node_resolution.py",
    ]
    different_tail = Path(sources[0]).read_text(encoding="utf-8")
    assert "APPLY_DIFFERENT_TAIL_SUPERSEDES_CYPHER" in different_tail
    assert "DELETE" not in APPLY_DIFFERENT_TAIL_SUPERSEDES_CYPHER
    assert "DELETE" not in APPLY_DIFFERENT_TAIL_UPDATED_BY_CYPHER
    assert "SET old.is_latest = false" in APPLY_DIFFERENT_TAIL_SUPERSEDES_CYPHER
    assert "CREATE (new_tail)-[:SUPERSEDES" in APPLY_DIFFERENT_TAIL_SUPERSEDES_CYPHER
    assert "WHERE h.id IN $touched_ids" in FIND_DIFFERENT_TAIL_PAIRS_CYPHER
    assert "$touched_ids" in FIND_MISSED_CONTRADICTIONS_CYPHER
    assert "size($touched_ids) = 0" in FIND_MISSED_CONTRADICTIONS_CYPHER
    node_src = Path(sources[1]).read_text(encoding="utf-8")
    assert "PROMOTE_NEWER_SUMMARY_CYPHER" in node_src
    assert "DELETE (dup" not in node_src
    assert "DETACH DELETE" not in node_src
