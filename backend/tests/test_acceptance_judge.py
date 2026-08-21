"""Fase 10 acceptance: judge post-batch pass, six isolated tasks. No Docker."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from app.core.config import Settings
from app.pipeline.concepts import MERGE_MEMBER_OF_CYPHER
from app.pipeline.dreaming import run_dreaming_pipeline
from app.pipeline.generic_instances import (
    DELETE_NODE_MEMBER_OF_CYPHER,
    ENSURE_GENERIC_INSTANCE_CYPHER,
    SET_GENERIC_OBSERVATION_COUNT_CYPHER,
)
from app.pipeline.identity_resolution import (
    LINK_SAME_AS_CYPHER,
    MARK_NOT_SAME_AS_CYPHER,
    MERGE_IDENTITY_NODE_CYPHER,
    SAME_AS,
    cosine,
)
from app.pipeline.ingestion import CREATE_CONTRADICTS_CYPHER
from app.pipeline.judge import (
    CREATE_SUPERSEDES_BETWEEN_CYPHER,
    CREATE_UPDATED_BY_BETWEEN_CYPHER,
    DELETE_CONTRADICTS_BETWEEN_CYPHER,
    DELETE_POSSIBLY_SAME_AS_CYPHER,
    FIND_BLURRED_RELATIONS_CYPHER,
    FIND_CONTRADICTS_PAIRS_CYPHER,
    FIND_EQUIVALENT_CONCEPT_PAIRS_CYPHER,
    FIND_MISSED_CONTRADICTIONS_CYPHER,
    FIND_PARENT_MEMBERS_CYPHER,
    FIND_POSSIBLY_SAME_AS_CYPHER,
    FIND_PROMOTED_CHILDREN_CYPHER,
    MARK_ABSORBED_CONCEPT_CYPHER,
    MARK_BLURRED_RELATION_CYPHER,
    MERGE_EQUIVALENT_TO_CYPHER,
    MERGE_JUDGE_RUN_CYPHER,
    MOVE_ABSORBED_MEMBER_OF_CYPHER,
    MOVE_MEMBER_OF_TO_CHILD_CYPHER,
    IdentityVerdict,
    JudgeStats,
    run_judge,
    split_blurred_relation,
)
from app.pipeline.node_resolution import (
    COLLAPSE_INCOMING_RELATIONS_CYPHER,
    COLLAPSE_OUTGOING_RELATIONS_CYPHER,
    COPY_DERIVED_FROM_CYPHER,
    COPY_HAS_CONCEPT_CYPHER,
    CREATE_INCOMING_ON_CANON_CYPHER,
    CREATE_OUTGOING_ON_CANON_CYPHER,
    DELETE_DUP_RELATIONS_CYPHER,
    READ_INCOMING_RELATIONS_CYPHER,
    READ_OUTGOING_RELATIONS_CYPHER,
    SET_MERGED_INTO_CYPHER,
)
from tests.test_dreaming_nodes import FakeDriver, FakeSession

JOB_ID = "job-judge-f10"


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


class JudgeGraph:
    """Dedicated in-memory graph for one judge scenario (not a shared fixture)."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.concepts: dict[str, dict] = {}
        self.identity_nodes: dict[str, dict] = {}
        self.relations: list[dict] = []
        self.member_of: dict[str, dict] = {}
        self.isa: dict[str, str] = {}
        self.famiglia: list[dict] = []
        self.judge_runs: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        self.has_concept: dict[str, list[str]] = {}
        self.derived_from: dict[str, list[str]] = {}

    def add_node(self, node_id: str, **props) -> None:
        self.nodes[node_id] = {"id": node_id, **props}

    def add_concept(self, concept_id: str, **props) -> None:
        self.concepts[concept_id] = {"id": concept_id, **props}

    def add_relation(self, src_id: str, dst_id: str, **props) -> None:
        rel_id = props.get("id") or f"rel-{src_id}-{dst_id}-{len(self.relations)}"
        row = {"id": rel_id, "src": src_id, "dst": dst_id, "is_latest": True, **props}
        row["id"] = rel_id
        self.relations.append(row)

    def set_member_of(self, node_id: str, concept_id: str, **props) -> None:
        self.member_of[node_id] = {"concept_id": concept_id, **props}

    def set_isa(self, child_id: str, parent_id: str) -> None:
        self.isa[child_id] = parent_id

    def add_famiglia(self, src_id: str, rel_type: str, dst_id: str, **props) -> None:
        self.famiglia.append(
            {"src": src_id, "dst": dst_id, "rel_type": rel_type, "props": dict(props)}
        )

    def _has_famiglia(self, a: str, b: str, rel_type: str) -> bool:
        for edge in self.famiglia:
            if edge["rel_type"] != rel_type:
                continue
            if {edge["src"], edge["dst"]} == {a, b}:
                return True
        return False

    def _drop_famiglia(self, a: str, b: str, rel_type: str) -> None:
        self.famiglia = [
            edge
            for edge in self.famiglia
            if not (
                edge["rel_type"] == rel_type and {edge["src"], edge["dst"]} == {a, b}
            )
        ]

    async def run(self, cypher, **kwargs):
        self.calls.append((cypher, kwargs))
        if cypher == FIND_BLURRED_RELATIONS_CYPHER:
            rows = []
            for rel in self.relations:
                wa = list(rel.get("witnesses_a") or [])
                wb = list(rel.get("witnesses_b") or [])
                if len(wa) > 1 and len(wb) > 1:
                    rows.append(
                        {
                            "rel_id": rel["id"],
                            "witnesses_a": wa,
                            "witnesses_b": wb,
                        }
                    )
            return FakeResult(rows)

        if cypher == MARK_BLURRED_RELATION_CYPHER:
            rel_id = kwargs["rel_id"]
            for rel in self.relations:
                if rel["id"] == rel_id:
                    rel["needs_reverify"] = True
                    rel["witnesses_a"] = []
                    rel["witnesses_b"] = []
            return FakeResult([])

        if cypher == FIND_EQUIVALENT_CONCEPT_PAIRS_CYPHER:
            ids = sorted(self.concepts)
            rows = []
            for i, id_a in enumerate(ids):
                a = self.concepts[id_a]
                if not a.get("promoted"):
                    continue
                for id_b in ids[i + 1 :]:
                    b = self.concepts[id_b]
                    if not b.get("promoted"):
                        continue
                    if a.get("kernel_category") != b.get("kernel_category"):
                        continue
                    if a.get("parent_uri") != b.get("parent_uri"):
                        continue
                    if a.get("parent_uri") is None:
                        continue
                    if self._has_famiglia(id_a, id_b, "EQUIVALENT_TO"):
                        continue
                    rows.append(
                        {
                            "id_a": id_a,
                            "embedding_a": a.get("embedding"),
                            "id_b": id_b,
                            "embedding_b": b.get("embedding"),
                        }
                    )
            return FakeResult(rows)

        if cypher == MERGE_EQUIVALENT_TO_CYPHER:
            src_id, dst_id = kwargs["src_id"], kwargs["dst_id"]
            if not self._has_famiglia(src_id, dst_id, "EQUIVALENT_TO"):
                self.add_famiglia(src_id, "EQUIVALENT_TO", dst_id)
            return FakeResult([])

        if cypher == MOVE_ABSORBED_MEMBER_OF_CYPHER:
            absorbed_id = kwargs["absorbed_id"]
            survivor_id = kwargs["survivor_id"]
            for node_id, home in list(self.member_of.items()):
                if home.get("concept_id") == absorbed_id:
                    self.member_of[node_id] = {
                        "concept_id": survivor_id,
                        "absorbed_from": absorbed_id,
                    }
            if absorbed_id in self.concepts:
                self.concepts[absorbed_id]["absorbed_from"] = survivor_id
            return FakeResult([])

        if cypher == MARK_ABSORBED_CONCEPT_CYPHER:
            absorbed_id = kwargs["absorbed_id"]
            if absorbed_id in self.concepts:
                self.concepts[absorbed_id]["absorbed_from"] = kwargs["survivor_id"]
            return FakeResult([])

        if cypher == FIND_PROMOTED_CHILDREN_CYPHER:
            parent_id = kwargs["parent_id"]
            rows = []
            for child_id, parent in self.isa.items():
                if parent != parent_id:
                    continue
                child = self.concepts.get(child_id) or {}
                if not child.get("promoted"):
                    continue
                rows.append(
                    {
                        "child_id": child_id,
                        "name": child.get("name"),
                        "definition": child.get("definition"),
                        "summary": child.get("summary"),
                        "kernel_category": child.get("kernel_category"),
                    }
                )
            return FakeResult(rows)

        if cypher == FIND_PARENT_MEMBERS_CYPHER:
            parent_id = kwargs["parent_id"]
            rows = []
            for node_id, home in self.member_of.items():
                if home.get("concept_id") != parent_id:
                    continue
                node = self.nodes.get(node_id) or {"id": node_id}
                rows.append(
                    {
                        "id": node_id,
                        "name": node.get("name"),
                        "summary": node.get("summary"),
                        "kernel_category": node.get("kernel_category"),
                        "is_generic": node.get("is_generic"),
                        "merged_into": node.get("merged_into"),
                        "generic_observation_count": node.get(
                            "generic_observation_count"
                        ),
                    }
                )
            return FakeResult(rows)

        if cypher == MOVE_MEMBER_OF_TO_CHILD_CYPHER:
            node_id = kwargs["node_id"]
            parent_id = kwargs["parent_id"]
            child_id = kwargs["child_id"]
            home = self.member_of.get(node_id)
            if home and home.get("concept_id") == parent_id:
                self.member_of[node_id] = {"concept_id": child_id}
            return FakeResult([])

        if cypher == FIND_POSSIBLY_SAME_AS_CYPHER:
            rows = []
            for edge in self.famiglia:
                if edge["rel_type"] != "POSSIBLY_SAME_AS":
                    continue
                a = self.nodes.get(edge["src"], {"id": edge["src"]})
                b = self.nodes.get(edge["dst"], {"id": edge["dst"]})
                rows.append(
                    {
                        "id_a": a.get("id"),
                        "name_a": a.get("name"),
                        "summary_a": a.get("summary"),
                        "kernel_a": a.get("kernel_category"),
                        "created_a": a.get("created_at"),
                        "id_b": b.get("id"),
                        "name_b": b.get("name"),
                        "summary_b": b.get("summary"),
                        "kernel_b": b.get("kernel_category"),
                        "created_b": b.get("created_at"),
                    }
                )
            return FakeResult(rows)

        if cypher == DELETE_POSSIBLY_SAME_AS_CYPHER:
            self._drop_famiglia(kwargs["src_id"], kwargs["dst_id"], "POSSIBLY_SAME_AS")
            return FakeResult([])

        if cypher == FIND_MISSED_CONTRADICTIONS_CYPHER or (
            "NOT (t1)-[:CONTRADICTS]-(t2)" in cypher and "$touched_ids" in cypher
        ):
            touched = {str(nid) for nid in (kwargs.get("touched_ids") or []) if nid}
            rows = []
            latest = [rel for rel in self.relations if rel.get("is_latest", True)]
            for i, left in enumerate(latest):
                for right in latest[i + 1 :]:
                    if left["src"] != right["src"]:
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
                    head = first["src"]
                    if touched and not (
                        str(head) in touched or str(t1) in touched or str(t2) in touched
                    ):
                        continue
                    rows.append(
                        {
                            "head_id": head,
                            "tail_a": t1,
                            "tail_b": t2,
                            "relation": first.get("relation") or "",
                            "kernel_parent": kp1,
                        }
                    )
            return FakeResult(rows)

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

        if cypher == FIND_CONTRADICTS_PAIRS_CYPHER:
            rows = []
            for edge in self.famiglia:
                if edge["rel_type"] != "CONTRADICTS":
                    continue
                left_id, right_id = edge["src"], edge["dst"]
                text_a = ""
                text_b = ""
                head = edge["props"].get("subject_id") or ""
                for rel in self.relations:
                    if rel.get("is_latest", True) and rel["dst"] == left_id:
                        text_a = rel.get("relation") or ""
                        head = head or rel["src"]
                    if rel.get("is_latest", True) and rel["dst"] == right_id:
                        text_b = rel.get("relation") or ""
                        head = head or rel["src"]
                left = self.nodes.get(left_id, {})
                right = self.nodes.get(right_id, {})
                rows.append(
                    {
                        "left_id": left_id,
                        "right_id": right_id,
                        "subject_id": head,
                        "text_a": text_a or left.get("summary") or left.get("name") or "",
                        "text_b": text_b or right.get("summary") or right.get("name") or "",
                    }
                )
            return FakeResult(rows)

        if cypher == CREATE_SUPERSEDES_BETWEEN_CYPHER:
            self.add_famiglia(
                kwargs["left_id"],
                "SUPERSEDES",
                kwargs["right_id"],
                subject_id=kwargs.get("subject_id"),
            )
            return FakeResult([])

        if cypher == CREATE_UPDATED_BY_BETWEEN_CYPHER:
            self.add_famiglia(
                kwargs["left_id"],
                "UPDATED_BY",
                kwargs["right_id"],
                subject_id=kwargs.get("subject_id"),
            )
            return FakeResult([])

        if cypher == DELETE_CONTRADICTS_BETWEEN_CYPHER:
            self._drop_famiglia(kwargs["left_id"], kwargs["right_id"], "CONTRADICTS")
            return FakeResult([])

        if cypher == MERGE_JUDGE_RUN_CYPHER:
            self.judge_runs[kwargs["id"]] = dict(kwargs)
            return FakeResult([])

        if cypher == MERGE_IDENTITY_NODE_CYPHER:
            uri = kwargs["uri"]
            summary = kwargs.get("canonical_summary") or ""
            existing = self.identity_nodes.get(uri)
            if existing is None:
                self.identity_nodes[uri] = {"uri": uri, "canonical_summary": summary}
            else:
                existing["canonical_summary"] = summary
            return FakeResult([{"uri": uri}])

        if cypher == LINK_SAME_AS_CYPHER:
            facet_id = kwargs["facet_node_id"]
            identity_id = kwargs["identity_id"]
            if facet_id in self.nodes and identity_id in self.identity_nodes:
                self.add_famiglia(facet_id, SAME_AS, identity_id)
            return FakeResult([])

        if cypher == MARK_NOT_SAME_AS_CYPHER:
            self.add_famiglia(kwargs["src_id"], "NOT_SAME_AS", kwargs["dst_id"])
            return FakeResult([])

        if cypher == SET_GENERIC_OBSERVATION_COUNT_CYPHER:
            node = self.nodes.get(kwargs["node_id"])
            if node is not None:
                node["generic_observation_count"] = kwargs["count"]
            return FakeResult([])

        if cypher == ENSURE_GENERIC_INSTANCE_CYPHER:
            node_id = kwargs["node_id"]
            existing = self.nodes.get(node_id)
            if existing is None:
                self.nodes[node_id] = {
                    "id": node_id,
                    "name": kwargs.get("name"),
                    "type": kwargs.get("type", "entity"),
                    "is_generic": True,
                    "kernel_category": kwargs.get("kernel_category"),
                    "summary": kwargs.get("summary"),
                }
            else:
                existing["is_generic"] = True
                if kwargs.get("type"):
                    existing["type"] = kwargs["type"]
                if kwargs.get("kernel_category"):
                    existing["kernel_category"] = kwargs["kernel_category"]
            return FakeResult([{"id": node_id}])

        if cypher == MERGE_MEMBER_OF_CYPHER:
            self.set_member_of(kwargs["node_id"], kwargs["concept_id"])
            return FakeResult([])

        if cypher == DELETE_NODE_MEMBER_OF_CYPHER:
            self.member_of.pop(kwargs["node_id"], None)
            return FakeResult([])

        if cypher == READ_OUTGOING_RELATIONS_CYPHER:
            dup_id, canon_id = kwargs["dup_id"], kwargs["canon_id"]
            rows = []
            for rel in self.relations:
                if rel["src"] == dup_id and rel["dst"] != canon_id:
                    props = {
                        k: v
                        for k, v in rel.items()
                        if k not in {"src", "dst"}
                    }
                    rows.append({"r": props, "other_id": rel["dst"]})
            return FakeResult(rows)

        if cypher == READ_INCOMING_RELATIONS_CYPHER:
            dup_id, canon_id = kwargs["dup_id"], kwargs["canon_id"]
            rows = []
            for rel in self.relations:
                if rel["dst"] == dup_id and rel["src"] != canon_id:
                    props = {
                        k: v
                        for k, v in rel.items()
                        if k not in {"src", "dst"}
                    }
                    rows.append({"r": props, "other_id": rel["src"]})
            return FakeResult(rows)

        if cypher == CREATE_OUTGOING_ON_CANON_CYPHER:
            props = dict(kwargs.get("props") or {})
            self.add_relation(kwargs["canon_id"], kwargs["other_id"], **props)
            return FakeResult([])

        if cypher == CREATE_INCOMING_ON_CANON_CYPHER:
            props = dict(kwargs.get("props") or {})
            self.add_relation(kwargs["other_id"], kwargs["canon_id"], **props)
            return FakeResult([])

        if cypher == DELETE_DUP_RELATIONS_CYPHER:
            dup_id = kwargs["dup_id"]
            self.relations = [
                rel
                for rel in self.relations
                if rel["src"] != dup_id and rel["dst"] != dup_id
            ]
            return FakeResult([])

        if cypher == COPY_HAS_CONCEPT_CYPHER:
            dup_id, canon_id = kwargs["dup_id"], kwargs["canon_id"]
            concepts = self.has_concept.pop(dup_id, [])
            dest = self.has_concept.setdefault(canon_id, [])
            for concept_id in concepts:
                if concept_id not in dest:
                    dest.append(concept_id)
            return FakeResult([])

        if cypher == COPY_DERIVED_FROM_CYPHER:
            dup_id, canon_id = kwargs["dup_id"], kwargs["canon_id"]
            chunks = self.derived_from.pop(dup_id, [])
            dest = self.derived_from.setdefault(canon_id, [])
            for chunk_id in chunks:
                if chunk_id not in dest:
                    dest.append(chunk_id)
            return FakeResult([])

        if cypher == SET_MERGED_INTO_CYPHER:
            node = self.nodes.get(kwargs["dup_id"])
            if node is not None:
                node["merged_into"] = kwargs["canon_id"]
            return FakeResult([])

        if cypher in (
            COLLAPSE_OUTGOING_RELATIONS_CYPHER,
            COLLAPSE_INCOMING_RELATIONS_CYPHER,
        ):
            canon_id = kwargs["canon_id"]
            outgoing = cypher == COLLAPSE_OUTGOING_RELATIONS_CYPHER
            grouped: dict[tuple[str, str], list[dict]] = {}
            for rel in self.relations:
                other = rel["dst"] if outgoing else rel["src"]
                endpoint = rel["src"] if outgoing else rel["dst"]
                if endpoint != canon_id:
                    continue
                key = (other, str(rel.get("normalized_relation") or rel.get("relation") or ""))
                grouped.setdefault(key, []).append(rel)
            drop_ids: set[int] = set()
            for rels in grouped.values():
                if len(rels) <= 1:
                    continue
                ordered = sorted(
                    rels, key=lambda r: str(r.get("created_at") or ""), reverse=True
                )
                for extra in ordered[1:]:
                    drop_ids.add(id(extra))
            if drop_ids:
                self.relations = [rel for rel in self.relations if id(rel) not in drop_ids]
            return FakeResult([])

        return FakeResult([])


def _vec_at_cosine(target: float) -> list[float]:
    y = math.sqrt(max(0.0, 1.0 - target * target))
    return [target, y]


async def _async_zero(*_args, **_kwargs) -> int:
    return 0


def test_flag_defaults():
    assert Settings.model_fields["ENABLE_JUDGE"].default is True
    assert Settings.model_fields["BACKBONE_COLLAPSE_THRESHOLD"].default == 0.90


def test_judge_has_no_new_write_primitives():
    text = Path(__file__).resolve().parents[1].joinpath("app/pipeline/judge.py").read_text(
        encoding="utf-8"
    )
    assert "merge_nodes" not in text
    assert "CREATE (n:Relation" not in text
    assert "JudgeStats" in text
    assert "EQUIVALENT_TO" in text
    assert "link_as_facet" in text


def test_split_blurred_relation_cartesian():
    pairs = split_blurred_relation(
        {"witnesses_a": ["wa1", "wa2"], "witnesses_b": ["wb1", "wb2"]}
    )
    assert pairs == [("wa1", "wb1"), ("wa1", "wb2"), ("wa2", "wb1"), ("wa2", "wb2")]
    assert split_blurred_relation({"witnesses_a": ["only"], "witnesses_b": ["a", "b"]}) == []


@pytest.mark.asyncio
async def test_anti_blur_splits_and_requeues():
    graph = JudgeGraph()
    graph.add_node("head")
    graph.add_node("tail")
    graph.add_relation(
        "head",
        "tail",
        id="blur-1",
        witnesses_a=["wa1", "wa2"],
        witnesses_b=["wb1", "wb2"],
        relation="plays_for",
    )
    requeued: list[tuple[str, str]] = []

    async def capture(wa: str, wb: str) -> None:
        requeued.append((wa, wb))

    stats = await run_judge(graph, JOB_ID, on_requeue=capture)

    assert stats.anti_blur >= 1
    assert len(requeued) == 4
    assert ("wa1", "wb1") in requeued
    blur = next(rel for rel in graph.relations if rel["id"] == "blur-1")
    assert blur["needs_reverify"] is True
    assert blur["witnesses_a"] == []
    assert blur["witnesses_b"] == []
    assert JOB_ID in graph.judge_runs


@pytest.mark.asyncio
async def test_equivalent_to_collapses_and_moves_member_of():
    graph = JudgeGraph()
    emb_a = [1.0, 0.0]
    emb_b = _vec_at_cosine(0.95)
    assert cosine(emb_a, emb_b) == pytest.approx(0.95)
    graph.add_concept(
        "concept-a",
        promoted=True,
        kernel_category="Agente",
        parent_uri="parent-1",
        embedding=emb_a,
        name="calciatore",
    )
    graph.add_concept(
        "concept-b",
        promoted=True,
        kernel_category="Agente",
        parent_uri="parent-1",
        embedding=emb_b,
        name="giocatore",
    )
    graph.add_node("n-mario", name="Mario")
    graph.set_member_of("n-mario", "concept-b")

    stats = await run_judge(graph, JOB_ID)

    assert stats.equivalent_to >= 1
    assert graph._has_famiglia("concept-a", "concept-b", "EQUIVALENT_TO")
    assert "concept-b" in graph.concepts
    home = graph.member_of["n-mario"]
    assert home["concept_id"] == "concept-a"
    assert home["absorbed_from"] == "concept-b"
    assert graph.concepts["concept-b"]["absorbed_from"] == "concept-a"


@pytest.mark.asyncio
async def test_reraffine_moves_matching_member_only():
    graph = JudgeGraph()
    graph.add_concept("parent-p", promoted=True, name="giocatore", kernel_category="Agente")
    graph.add_concept(
        "child-s",
        promoted=True,
        name="portiere",
        kernel_category="Agente",
        definition="portiere",
    )
    graph.set_isa("child-s", "parent-p")
    graph.add_node("n-match", name="portiere", summary="portiere", kernel_category="Agente")
    graph.add_node("n-stay", name="attaccante", summary="attaccante", kernel_category="Agente")
    graph.set_member_of("n-match", "parent-p")
    graph.set_member_of("n-stay", "parent-p")

    stats = await run_judge(graph, JOB_ID, promoted_parent_ids=["parent-p"])

    assert stats.reraffine >= 1
    assert graph.member_of["n-match"]["concept_id"] == "child-s"
    assert graph.member_of["n-stay"]["concept_id"] == "parent-p"


@pytest.mark.asyncio
async def test_identity_same_as_via_link_as_facet(monkeypatch):
    graph = JudgeGraph()
    graph.add_node(
        "mario-calcio",
        name="Mario Rossi",
        summary="calciatore",
        kernel_category="Agente",
    )
    graph.add_node(
        "mario-tv",
        name="Mario Rossi",
        summary="opinione tv",
        kernel_category="Agente",
    )
    graph.add_famiglia("mario-calcio", "POSSIBLY_SAME_AS", "mario-tv")
    merges: list[tuple[str, str]] = []

    async def fake_merge(*_args, **_kwargs) -> None:
        merges.append(("called", "merge"))

    async def fake_llm(*_args, **_kwargs):
        return IdentityVerdict(decision="same_as")

    monkeypatch.setattr("app.pipeline.node_resolution.merge_nodes", fake_merge)
    monkeypatch.setattr("app.pipeline.judge.call_structured", fake_llm)

    stats = await run_judge(graph, JOB_ID)

    assert stats.identity >= 1
    assert merges == []
    assert not graph._has_famiglia("mario-calcio", "mario-tv", "POSSIBLY_SAME_AS")
    identity_uris = list(graph.identity_nodes)
    assert identity_uris
    uri = identity_uris[0]
    assert graph._has_famiglia("mario-calcio", uri, SAME_AS)
    assert graph._has_famiglia("mario-tv", uri, SAME_AS)
    assert not any("merged_into" in cypher for cypher, _kw in graph.calls)


@pytest.mark.asyncio
async def test_missed_contradiction_creates_contradicts():
    graph = JudgeGraph()
    graph.add_node("weah")
    graph.add_node("tail-2010", name="2010")
    graph.add_node("tail-2011", name="2011")
    graph.add_relation(
        "weah",
        "tail-2010",
        relation="Fonte A: ha vinto il torneo nel 2010.",
        kernel_parent="Temporale",
        is_latest=True,
    )
    graph.add_relation(
        "weah",
        "tail-2011",
        relation="Fonte B: ha vinto il torneo nel 2011.",
        kernel_parent="Temporale",
        is_latest=True,
    )

    stats = await run_judge(graph, JOB_ID)

    assert stats.missed_contradictions >= 1
    assert graph._has_famiglia("tail-2010", "tail-2011", "CONTRADICTS")
    assert len(graph.relations) == 2


@pytest.mark.asyncio
async def test_temporal_reclassifies_contradicts_keeps_facts(monkeypatch):
    graph = JudgeGraph()
    graph.add_node("head")
    graph.add_node("tail-old", name="old")
    graph.add_node("tail-new", name="new")
    graph.add_relation(
        "head",
        "tail-old",
        relation="X ha vinto nel 2010.",
        kernel_parent="Temporale",
        is_latest=True,
    )
    graph.add_relation(
        "head",
        "tail-new",
        relation="In realtà mi sono sbagliato, non nel 2010 ma nel 2011.",
        kernel_parent="Temporale",
        is_latest=True,
    )
    graph.add_famiglia("tail-old", "CONTRADICTS", "tail-new", subject_id="head")

    monkeypatch.setattr(
        "app.pipeline.judge.classify_temporal_pair",
        lambda *_args, **_kwargs: "updated_by",
    )

    before_rels = list(graph.relations)
    stats = await run_judge(graph, JOB_ID)

    assert stats.temporal >= 1
    assert graph._has_famiglia("tail-old", "tail-new", "UPDATED_BY")
    assert not graph._has_famiglia("tail-old", "tail-new", "CONTRADICTS")
    assert graph.relations == before_rels
    assert all(rel.get("is_latest", True) for rel in graph.relations)


@pytest.mark.asyncio
async def test_dreaming_pipeline_stubbed_judge_emits_complete(monkeypatch):
    published: list[dict] = []

    async def fake_nodes(*_args, **_kwargs) -> set[str]:
        return set()

    async def fake_judge(*_args, **_kwargs) -> JudgeStats:
        return JudgeStats()

    async def spy_publish(job_id, stage, event, payload):
        published.append({"stage": stage, "event": event, "payload": payload})

    monkeypatch.setattr("app.pipeline.dreaming.get_driver", lambda: FakeDriver())
    monkeypatch.setattr("app.pipeline.dreaming._run_node_phases", fake_nodes)
    monkeypatch.setattr(
        "app.pipeline.dreaming.reconcile.reconcile_scoped_relations",
        _async_zero,
    )
    monkeypatch.setattr("app.pipeline.dreaming.run_judge", fake_judge)
    monkeypatch.setattr(
        "app.pipeline.dreaming.node_ppr_projection.refresh_ppr_projection",
        _async_zero,
    )
    monkeypatch.setattr("app.pipeline.dreaming.event_bus.publish", spy_publish)
    monkeypatch.setattr("app.pipeline.dreaming.get_token_usage", lambda _job: 0)

    await run_dreaming_pipeline(JOB_ID)

    assert any(m["event"] == "pipeline_complete" for m in published)
    assert any(m["event"] == "judge_complete" for m in published)


@pytest.mark.asyncio
async def test_dreaming_pipeline_real_judge_writes_judgerun(monkeypatch):
    published: list[dict] = []
    judge_session = FakeSession()

    async def fake_nodes(*_args, **_kwargs) -> set[str]:
        return set()

    async def spy_publish(job_id, stage, event, payload):
        published.append({"stage": stage, "event": event, "payload": payload})

    driver = FakeDriver([judge_session, FakeSession()])
    monkeypatch.setattr("app.pipeline.dreaming.get_driver", lambda: driver)
    monkeypatch.setattr("app.pipeline.dreaming._run_node_phases", fake_nodes)
    monkeypatch.setattr(
        "app.pipeline.dreaming.reconcile.reconcile_scoped_relations",
        _async_zero,
    )
    monkeypatch.setattr(
        "app.pipeline.dreaming.node_ppr_projection.refresh_ppr_projection",
        _async_zero,
    )
    monkeypatch.setattr("app.pipeline.dreaming.event_bus.publish", spy_publish)
    monkeypatch.setattr("app.pipeline.dreaming.get_token_usage", lambda _job: 0)

    await run_dreaming_pipeline(JOB_ID)

    assert any(m["event"] == "pipeline_complete" for m in published)
    assert any(call[0] == MERGE_JUDGE_RUN_CYPHER for call in judge_session.calls)
    kwargs = next(kw for cy, kw in judge_session.calls if cy == MERGE_JUDGE_RUN_CYPHER)
    assert kwargs["id"] == JOB_ID
    assert kwargs["batch_id"] == JOB_ID
    assert kwargs["anti_blur"] == 0


@pytest.mark.asyncio
async def test_dreaming_pipeline_judge_failure_still_completes(monkeypatch):
    published: list[dict] = []

    async def fake_nodes(*_args, **_kwargs) -> set[str]:
        return set()

    async def boom_judge(*_args, **_kwargs):
        raise RuntimeError("judge exploded")

    async def spy_publish(job_id, stage, event, payload):
        published.append({"stage": stage, "event": event, "payload": payload})

    monkeypatch.setattr("app.pipeline.dreaming.get_driver", lambda: FakeDriver())
    monkeypatch.setattr("app.pipeline.dreaming._run_node_phases", fake_nodes)
    monkeypatch.setattr(
        "app.pipeline.dreaming.reconcile.reconcile_scoped_relations",
        _async_zero,
    )
    monkeypatch.setattr("app.pipeline.dreaming.run_judge", boom_judge)
    monkeypatch.setattr(
        "app.pipeline.dreaming.node_ppr_projection.refresh_ppr_projection",
        _async_zero,
    )
    monkeypatch.setattr("app.pipeline.dreaming.event_bus.publish", spy_publish)
    monkeypatch.setattr("app.pipeline.dreaming.get_token_usage", lambda _job: 0)

    await run_dreaming_pipeline(JOB_ID)

    assert any(m["event"] == "pipeline_complete" for m in published)
    failed = [m for m in published if m["event"] == "llm_call_failed"]
    assert any(m["stage"] == "judge" for m in failed)
