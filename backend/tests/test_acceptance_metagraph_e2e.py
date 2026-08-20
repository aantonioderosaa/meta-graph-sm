"""Fase 14.1: Metagraph end-to-end on a fixed mini-corpus. No Docker, no OpenAI."""

from __future__ import annotations

import inspect
import re

import pytest

from app.models.kernel import EntityKernelType, RelationKernelType
from app.models.query import NodeSubgraph, NodeSubgraphRelationship
from app.pipeline.concepts import kernel_catch_all_concept_id
from app.pipeline.connectivity_rules import (
    MERGE_CONNECTIVITY_RULE_CYPHER,
    READ_CONCEPT_ANCESTORS_CYPHER,
    READ_NODE_TYPE_TOKEN_CYPHER,
)
from app.pipeline.entity_relation_resolution import (
    APPLY_SUPERSEDES_CYPHER,
    APPLY_UPDATED_BY_CYPHER,
)
from app.pipeline.identity_resolution import (
    LINK_SAME_AS_CYPHER,
    MARK_NOT_SAME_AS_CYPHER,
    MERGE_IDENTITY_NODE_CYPHER,
    SAME_AS,
    UNLINK_FACET_CYPHER,
    ensure_identity_node,
    identity_uri_from_facet_ids,
    link_as_facet,
)
from app.pipeline.ingestion import (
    CREATE_CONTRADICTS_CYPHER,
    CREATE_NODE_CYPHER,
    CREATE_NODE_RELATION_CYPHER,
    write_contradicts,
    write_node,
    write_node_relation,
)
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
    run_judge,
)
from app.pipeline.node_query_engine import (
    LOAD_CONNECTIVITY_RULES_CYPHER,
    LOAD_ISA_EDGES_CYPHER,
    LOAD_LEAF_S0_RELATIONS_CYPHER,
    LOAD_NODE_TYPE_TOKENS_CYPHER,
    derive_candidate_links,
    label_query_citations,
)
from app.pipeline.promote import (
    CREATE_PROMOTED_CONCEPT_CYPHER,
    FIND_CLUSTER_MEMBERS_CYPHER,
    FIND_CLUSTER_RELATIONS_CYPHER,
    FIND_CONCEPTS_IN_CLUSTER_CYPHER,
    FIND_EXISTING_PROMOTED_CYPHER,
    FIND_PARENT_CYPHER,
    LIFT_EXTERNAL_RELATION_CYPHER,
    LINK_PROMOTED_ISA_CYPHER,
    MERGE_TYPE_MIGRATION_ALIAS_CYPHER,
    MOVE_MEMBER_OF_CYPHER,
    promote,
)

# ---------------------------------------------------------------------------
# Fixed mini-corpus (constants — no LLM, no live ingest)
# ---------------------------------------------------------------------------

JOB_ID = "job-f14-e2e"
CHUNK_ID = "chunk-e2e-1"
CORPUS_TEXT = (
    "Mario Rossi è titolare della ditta individuale Rossi Snc. "
    "Mario Rossi allena i calciatori della squadra locale. "
    "Nel 2010 Mario Rossi ha vinto il torneo cittadino. "
    "Fonte B: Mario Rossi ha vinto il torneo cittadino nel 2011. "
    "Dal 2018 Mario Rossi è presidente della società. "
    "In realtà mi sono sbagliato sul ruolo di consulente: non dal 2016 ma dal 2017."
)

MARIO_ID = "mario-rossi-agente"
DITTA_ID = "rossi-snc-sociale"
CLUB_ID = "squadra-locale"
YEAR_2010_ID = "torneo-2010"
YEAR_2011_ID = "torneo-2011"
ROLE_ID = "ruolo-societa"
CONSULT_ID = "ruolo-consulente"
ALICE_ID = "alice-giocatore"
MID_ID = "mid-giocatore"
COACH_ID = "coach-x"
PLAYER_IDS = tuple(f"player-{i}" for i in range(5))

GIOCATORE_ID = "giocatore"
COACH_CONCEPT_ID = "coach"
PERSONA_CONCEPT_ID = "persona"

_RELATION_WRITE_RE = re.compile(
    r"\b(?:CREATE|MERGE)\b[\s\S]{0,240}:Relation\b",
    re.IGNORECASE,
)

_ENTITY_VALUES = {m.value for m in EntityKernelType}
_RELATION_VALUES = {m.value for m in RelationKernelType}


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


class MetaGraph:
    """In-memory graph interpreting ingest / promote / identity / judge / S2 Cypher."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.concepts: dict[str, dict] = {}
        self.identity_nodes: dict[str, dict] = {}
        self.chunks: dict[str, dict] = {}
        self.member_of: dict[str, str] = {}
        self.member_of_meta: dict[str, dict] = {}
        self.isa: dict[str, str] = {}
        self.relations: list[dict] = []
        self.rules: dict[tuple[str, str, str], dict] = {}
        self.famiglia: list[dict] = []
        self.judge_runs: dict[str, dict] = {}
        self.aliases: list[dict] = []
        self.derived_from: list[tuple[str, str]] = []

    def _src(self, rel: dict) -> str:
        return str(rel.get("src_id") or rel.get("src") or "")

    def _tgt(self, rel: dict) -> str:
        return str(rel.get("tgt_id") or rel.get("dst") or "")

    def _has_famiglia(self, a: str, b: str, rel_type: str) -> bool:
        for edge in self.famiglia:
            if edge["rel_type"] != rel_type:
                continue
            if {edge["src"], edge["dst"]} == {a, b}:
                return True
        return False

    def _add_famiglia(self, src_id: str, rel_type: str, dst_id: str, **props) -> None:
        if rel_type in {SAME_AS} or not self._has_famiglia(src_id, dst_id, rel_type):
            self.famiglia.append(
                {"src": src_id, "dst": dst_id, "rel_type": rel_type, "props": dict(props)}
            )

    def _drop_famiglia(self, a: str, b: str, rel_type: str) -> None:
        self.famiglia = [
            edge
            for edge in self.famiglia
            if not (
                edge["rel_type"] == rel_type and {edge["src"], edge["dst"]} == {a, b}
            )
        ]

    def _ancestors(self, concept_id: str) -> list[dict]:
        rows: list[dict] = []
        current = concept_id
        seen: set[str] = set()
        hops = 0
        while current in self.isa and current not in seen:
            seen.add(current)
            current = self.isa[current]
            hops += 1
            concept = self.concepts.get(current, {})
            rows.append({"id": current, "name": concept.get("name"), "hops": hops})
        return rows


def _apply_read(graph: MetaGraph, cypher: str, kwargs: dict) -> list[dict]:
    if cypher == CREATE_NODE_CYPHER:
        nid = kwargs["id"]
        graph.nodes[nid] = {
            "id": nid,
            "name": kwargs.get("name"),
            "type": kwargs.get("type"),
            "summary": kwargs.get("summary") or "",
            "kernel_category": kwargs.get("kernel_category"),
            "embedding": kwargs.get("emb"),
            "merged_into": None,
        }
        chunk_id = kwargs.get("chunk_id")
        if chunk_id:
            graph.derived_from.append((nid, chunk_id))
        return [{"id": nid}]
    if cypher == CREATE_NODE_RELATION_CYPHER:
        rel_id = f"rel-{len(graph.relations)}"
        graph.relations.append(
            {
                "id": rel_id,
                "src_id": kwargs["head_id"],
                "tgt_id": kwargs["tail_id"],
                "src": kwargs["head_id"],
                "dst": kwargs["tail_id"],
                "relation": kwargs.get("relation"),
                "kernel_parent": kwargs.get("kernel_parent"),
                "normalized_relation": kwargs.get("normalized_relation"),
                "witnesses_a": list(kwargs.get("witnesses_a") or []),
                "witnesses_b": list(kwargs.get("witnesses_b") or []),
                "valid_time": kwargs.get("valid_time"),
                "provenance": kwargs.get("provenance"),
                "is_latest": True,
                "lifted_from": None,
            }
        )
        return []
    if cypher == CREATE_CONTRADICTS_CYPHER:
        graph._add_famiglia(
            kwargs["left_id"],
            "CONTRADICTS",
            kwargs["right_id"],
            subject_id=kwargs.get("subject_id"),
            relation=kwargs.get("relation"),
            kernel_parent=kwargs.get("kernel_parent"),
        )
        return []
    if cypher == READ_NODE_TYPE_TOKEN_CYPHER:
        node = graph.nodes.get(kwargs["node_id"])
        if node is None:
            return []
        concept_id = graph.member_of.get(node["id"])
        concept = graph.concepts.get(concept_id) if concept_id else None
        return [
            {
                "kernel_category": node.get("kernel_category"),
                "concept_id": concept_id,
                "concept_name": concept.get("name") if concept else None,
            }
        ]
    if cypher == READ_CONCEPT_ANCESTORS_CYPHER:
        return graph._ancestors(kwargs["concept_id"])
    if cypher == MERGE_CONNECTIVITY_RULE_CYPHER:
        key = (
            kwargs["source_category"],
            kwargs["relation_type"],
            kwargs["target_category"],
        )
        origin = kwargs["origin_id"]
        existing = graph.rules.get(key)
        if existing is None:
            graph.rules[key] = {
                "source_category": key[0],
                "relation_type": key[1],
                "target_category": key[2],
                "origin_fact_ids": [origin],
                "generalization_level": kwargs["generalization_level"],
            }
        elif origin not in existing["origin_fact_ids"]:
            existing["origin_fact_ids"] = existing["origin_fact_ids"] + [origin]
        return []
    if cypher == LOAD_CONNECTIVITY_RULES_CYPHER:
        return [dict(rule) for rule in graph.rules.values()]
    if cypher == LOAD_LEAF_S0_RELATIONS_CYPHER:
        rows = []
        for rel in graph.relations:
            if rel.get("lifted_from"):
                continue
            src, tgt = graph._src(rel), graph._tgt(rel)
            if src not in graph.nodes or tgt not in graph.nodes:
                continue
            rows.append(
                {
                    "src_id": src,
                    "tgt_id": tgt,
                    "relation": rel.get("relation"),
                    "kernel_parent": rel.get("kernel_parent"),
                    "normalized_relation": rel.get("normalized_relation"),
                }
            )
        return rows
    if cypher == LOAD_NODE_TYPE_TOKENS_CYPHER:
        rows = []
        for nid, node in graph.nodes.items():
            if node.get("merged_into") is not None:
                continue
            concept_id = graph.member_of.get(nid)
            concept = graph.concepts.get(concept_id) if concept_id else None
            rows.append(
                {
                    "id": nid,
                    "kernel_category": node.get("kernel_category"),
                    "concept_id": concept_id,
                    "concept_name": concept.get("name") if concept else None,
                }
            )
        return rows
    if cypher == LOAD_ISA_EDGES_CYPHER:
        return [
            {"child_id": child, "parent_id": parent} for child, parent in graph.isa.items()
        ]
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
            src, tgt = graph._src(rel), graph._tgt(rel)
            if src in cluster or tgt in cluster:
                rows.append(
                    {
                        "src_id": src,
                        "tgt_id": tgt,
                        "relation": rel.get("relation"),
                        "kernel_parent": rel.get("kernel_parent"),
                        "normalized_relation": rel.get("normalized_relation"),
                        "witnesses_a": list(rel.get("witnesses_a") or []),
                        "witnesses_b": list(rel.get("witnesses_b") or []),
                    }
                )
        return rows
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
        return [{"id": cid}]
    if cypher == LINK_PROMOTED_ISA_CYPHER:
        graph.isa[kwargs["concept_id"]] = kwargs["parent_id"]
        return []
    if cypher == MOVE_MEMBER_OF_CYPHER:
        parent_id = kwargs["parent_id"]
        concept_id = kwargs["concept_id"]
        for nid in kwargs["node_ids"]:
            if graph.member_of.get(nid) == parent_id:
                graph.member_of[nid] = concept_id
        return []
    if cypher == LIFT_EXTERNAL_RELATION_CYPHER:
        for edge in kwargs.get("edges") or []:
            row = dict(edge)
            row.setdefault("src", row.get("src_id"))
            row.setdefault("dst", row.get("tgt_id"))
            row.setdefault("is_latest", True)
            row.setdefault("id", f"rel-lift-{len(graph.relations)}")
            graph.relations.append(row)
        return []
    if cypher == MERGE_TYPE_MIGRATION_ALIAS_CYPHER:
        concept_id = kwargs["concept_id"]
        for old_type in kwargs.get("types") or []:
            key = (old_type, old_type, concept_id)
            if any(
                (a["old_type"], a["new_type"], a["concept_id"]) == key for a in graph.aliases
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
        return []
    if cypher == MERGE_IDENTITY_NODE_CYPHER:
        uri = kwargs["uri"]
        summary = kwargs.get("canonical_summary") or ""
        existing = graph.identity_nodes.get(uri)
        if existing is None:
            graph.identity_nodes[uri] = {"uri": uri, "canonical_summary": summary}
        else:
            existing["canonical_summary"] = summary
        return [{"uri": uri}]
    if cypher == LINK_SAME_AS_CYPHER:
        facet_id = kwargs["facet_node_id"]
        identity_id = kwargs["identity_id"]
        if facet_id in graph.nodes and identity_id in graph.identity_nodes:
            graph._add_famiglia(facet_id, SAME_AS, identity_id)
        return []
    if cypher == UNLINK_FACET_CYPHER:
        graph._drop_famiglia(kwargs["facet_node_id"], kwargs["identity_id"], SAME_AS)
        return []
    if cypher == APPLY_SUPERSEDES_CYPHER:
        _apply_temporal(graph, kwargs, "SUPERSEDES")
        return []
    if cypher == APPLY_UPDATED_BY_CYPHER:
        _apply_temporal(graph, kwargs, "UPDATED_BY")
        return []
    if cypher == FIND_BLURRED_RELATIONS_CYPHER:
        rows = []
        for rel in graph.relations:
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
        return rows
    if cypher == MARK_BLURRED_RELATION_CYPHER:
        for rel in graph.relations:
            if rel["id"] == kwargs["rel_id"]:
                rel["needs_reverify"] = True
                rel["witnesses_a"] = []
                rel["witnesses_b"] = []
        return []
    if cypher == FIND_EQUIVALENT_CONCEPT_PAIRS_CYPHER:
        ids = sorted(graph.concepts)
        rows = []
        for i, id_a in enumerate(ids):
            a = graph.concepts[id_a]
            if not a.get("promoted"):
                continue
            for id_b in ids[i + 1 :]:
                b = graph.concepts[id_b]
                if not b.get("promoted"):
                    continue
                if a.get("kernel_category") != b.get("kernel_category"):
                    continue
                if a.get("parent_uri") != b.get("parent_uri"):
                    continue
                if a.get("parent_uri") is None:
                    continue
                if graph._has_famiglia(id_a, id_b, "EQUIVALENT_TO"):
                    continue
                rows.append(
                    {
                        "id_a": id_a,
                        "embedding_a": a.get("embedding"),
                        "id_b": id_b,
                        "embedding_b": b.get("embedding"),
                    }
                )
        return rows
    if cypher == MERGE_EQUIVALENT_TO_CYPHER:
        graph._add_famiglia(kwargs["src_id"], "EQUIVALENT_TO", kwargs["dst_id"])
        return []
    if cypher == MOVE_ABSORBED_MEMBER_OF_CYPHER:
        absorbed_id = kwargs["absorbed_id"]
        survivor_id = kwargs["survivor_id"]
        for node_id, home in list(graph.member_of.items()):
            if home == absorbed_id:
                graph.member_of[node_id] = survivor_id
                graph.member_of_meta[node_id] = {"absorbed_from": absorbed_id}
        if absorbed_id in graph.concepts:
            graph.concepts[absorbed_id]["absorbed_from"] = survivor_id
        return []
    if cypher == MARK_ABSORBED_CONCEPT_CYPHER:
        absorbed_id = kwargs["absorbed_id"]
        if absorbed_id in graph.concepts:
            graph.concepts[absorbed_id]["absorbed_from"] = kwargs["survivor_id"]
        return []
    if cypher == FIND_PROMOTED_CHILDREN_CYPHER:
        parent_id = kwargs["parent_id"]
        rows = []
        for child_id, parent in graph.isa.items():
            if parent != parent_id:
                continue
            child = graph.concepts.get(child_id) or {}
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
        return rows
    if cypher == FIND_PARENT_MEMBERS_CYPHER:
        parent_id = kwargs["parent_id"]
        rows = []
        for node_id, home in graph.member_of.items():
            if home != parent_id:
                continue
            node = graph.nodes.get(node_id) or {"id": node_id}
            rows.append(
                {
                    "id": node_id,
                    "name": node.get("name"),
                    "summary": node.get("summary"),
                    "kernel_category": node.get("kernel_category"),
                }
            )
        return rows
    if cypher == MOVE_MEMBER_OF_TO_CHILD_CYPHER:
        node_id = kwargs["node_id"]
        if graph.member_of.get(node_id) == kwargs["parent_id"]:
            graph.member_of[node_id] = kwargs["child_id"]
        return []
    if cypher == FIND_POSSIBLY_SAME_AS_CYPHER:
        return []
    if cypher == DELETE_POSSIBLY_SAME_AS_CYPHER:
        graph._drop_famiglia(kwargs["src_id"], kwargs["dst_id"], "POSSIBLY_SAME_AS")
        return []
    if cypher == FIND_MISSED_CONTRADICTIONS_CYPHER or (
        "NOT (t1)-[:CONTRADICTS]-(t2)" in cypher and "$touched_ids" in cypher
    ):
        touched = {str(nid) for nid in (kwargs.get("touched_ids") or []) if nid}
        rows = []
        latest = [rel for rel in graph.relations if rel.get("is_latest", True)]
        for i, left in enumerate(latest):
            for right in latest[i + 1 :]:
                if graph._src(left) != graph._src(right):
                    continue
                t1, t2 = graph._tgt(left), graph._tgt(right)
                if t1 == t2:
                    continue
                first, second = (left, right) if t1 < t2 else (right, left)
                t1, t2 = graph._tgt(first), graph._tgt(second)
                kp1 = first.get("kernel_parent") or ""
                kp2 = second.get("kernel_parent") or ""
                if kp1 != kp2:
                    continue
                if graph._has_famiglia(t1, t2, "CONTRADICTS"):
                    continue
                if graph._has_famiglia(t1, t2, "SUPERSEDES"):
                    continue
                if graph._has_famiglia(t1, t2, "UPDATED_BY"):
                    continue
                head = graph._src(first)
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
        return rows
    if cypher == FIND_CONTRADICTS_PAIRS_CYPHER:
        rows = []
        for edge in graph.famiglia:
            if edge["rel_type"] != "CONTRADICTS":
                continue
            left_id, right_id = edge["src"], edge["dst"]
            text_a = ""
            text_b = ""
            head = edge["props"].get("subject_id") or ""
            for rel in graph.relations:
                if rel.get("is_latest", True) and graph._tgt(rel) == left_id:
                    text_a = rel.get("relation") or ""
                    head = head or graph._src(rel)
                if rel.get("is_latest", True) and graph._tgt(rel) == right_id:
                    text_b = rel.get("relation") or ""
                    head = head or graph._src(rel)
            rows.append(
                {
                    "left_id": left_id,
                    "right_id": right_id,
                    "subject_id": head,
                    "text_a": text_a,
                    "text_b": text_b,
                }
            )
        return rows
    if cypher == CREATE_SUPERSEDES_BETWEEN_CYPHER:
        graph._add_famiglia(
            kwargs["left_id"],
            "SUPERSEDES",
            kwargs["right_id"],
            subject_id=kwargs.get("subject_id"),
        )
        return []
    if cypher == CREATE_UPDATED_BY_BETWEEN_CYPHER:
        graph._add_famiglia(
            kwargs["left_id"],
            "UPDATED_BY",
            kwargs["right_id"],
            subject_id=kwargs.get("subject_id"),
        )
        return []
    if cypher == DELETE_CONTRADICTS_BETWEEN_CYPHER:
        graph._drop_famiglia(kwargs["left_id"], kwargs["right_id"], "CONTRADICTS")
        return []
    if cypher == MERGE_JUDGE_RUN_CYPHER:
        graph.judge_runs[kwargs["id"]] = dict(kwargs)
        return []
    if cypher == MARK_NOT_SAME_AS_CYPHER:
        graph._add_famiglia(kwargs["src_id"], "NOT_SAME_AS", kwargs["dst_id"])
        return []
    return []


def _apply_temporal(graph: MetaGraph, kwargs: dict, rel_type: str) -> None:
    new_id = kwargs["new_rel_id"]
    old_id = kwargs["old_rel_id"]
    tail_id = kwargs["tail_id"]
    for rel in graph.relations:
        if rel.get("id") == old_id:
            rel["is_latest"] = False
        if rel.get("id") == new_id:
            rel["normalized_relation"] = "updates"
    graph._add_famiglia(
        tail_id,
        rel_type,
        tail_id,
        subject_id=kwargs.get("head_id"),
        new_rel_id=new_id,
        old_rel_id=old_id,
    )


class MetaGraphSession:
    def __init__(self, graph: MetaGraph | None = None) -> None:
        self.graph = graph or MetaGraph()
        self.calls: list[tuple[str, dict]] = []
        self.relation_writes = 0

    async def run(self, cypher, **kwargs):
        self.calls.append((cypher, kwargs))
        if _RELATION_WRITE_RE.search(cypher or ""):
            self.relation_writes += 1
        return FakeResult(_apply_read(self.graph, cypher, kwargs))

    async def execute_write(self, fn):
        txn = _WriteTxn(self)
        result = await fn(txn)
        for cypher, kwargs in txn.buffer:
            _apply_read(self.graph, cypher, kwargs)
        self.calls.extend(txn.calls)
        return result


class _WriteTxn:
    def __init__(self, session: MetaGraphSession) -> None:
        self.session = session
        self.buffer: list[tuple[str, dict]] = []
        self.calls: list[tuple[str, dict]] = []

    async def run(self, cypher, **kwargs):
        self.calls.append((cypher, kwargs))
        self.buffer.append((cypher, kwargs))
        return FakeResult([])


def _rel_id(session: MetaGraphSession, src: str, tgt: str, relation: str) -> str:
    for rel in session.graph.relations:
        if (
            session.graph._src(rel) == src
            and session.graph._tgt(rel) == tgt
            and rel.get("relation") == relation
        ):
            return str(rel["id"])
    raise AssertionError(f"missing relation {src}-{relation}->{tgt}")


@pytest.fixture
def e2e_stubs(monkeypatch):
    monkeypatch.setattr("app.pipeline.ingestion.embeddings.embed", lambda _t: [0.1] * 8)
    monkeypatch.setattr("app.pipeline.promote.embeddings.embed", lambda _t: [0.1] * 8)
    monkeypatch.setattr("app.pipeline.promote.settings.OPENAI_API_KEY", "")
    monkeypatch.setattr(
        "app.pipeline.identity_resolution.settings.ENABLE_FACET_IDENTITY",
        True,
    )


async def _seed_ingest(session: MetaGraphSession) -> None:
    session.graph.chunks[CHUNK_ID] = {"id": CHUNK_ID, "text": CORPUS_TEXT}
    specs = [
        (MARIO_ID, "Mario Rossi", "Agente", "Titolare e calciatore."),
        (DITTA_ID, "Rossi Snc", "CostruttoSociale", "Ditta individuale."),
        (CLUB_ID, "Squadra locale", "CostruttoSociale", "Club."),
        (YEAR_2010_ID, "Torneo 2010", "EntitaTemporale", "Edizione 2010."),
        (YEAR_2011_ID, "Torneo 2011", "EntitaTemporale", "Edizione 2011."),
        (ROLE_ID, "Ruolo societa", "EntitaAstratta", "Ruolo in societa."),
        (CONSULT_ID, "Ruolo consulente", "EntitaAstratta", "Ruolo consulente."),
        (ALICE_ID, "Alice", "Agente", "Giocatrice."),
        (MID_ID, "Mid", "Agente", "Compagna di squadra."),
        (COACH_ID, "Coach X", "Agente", "Allenatore."),
    ]
    for nid, name, category, summary in specs:
        await write_node(
            session,
            node_id=nid,
            name=name,
            node_type="entity",
            chunk_id=CHUNK_ID,
            embedding=[0.1] * 8,
            job_id=JOB_ID,
            summary=summary,
            kernel_category=category,
        )
    for i, pid in enumerate(PLAYER_IDS):
        await write_node(
            session,
            node_id=pid,
            name=f"Player {i}",
            node_type="entity",
            chunk_id=CHUNK_ID,
            embedding=[0.1] * 8,
            job_id=JOB_ID,
            summary=f"An agent named Player {i}.",
            kernel_category=EntityKernelType.Agente.value,
        )


def _seed_backbone(graph: MetaGraph) -> str:
    kernel = kernel_catch_all_concept_id(EntityKernelType.Agente)
    graph.concepts[kernel] = {
        "id": kernel,
        "name": "Agente",
        "kernel_category": "Agente",
        "promoted": True,
    }
    graph.concepts[GIOCATORE_ID] = {"id": GIOCATORE_ID, "name": "Giocatore"}
    graph.concepts[COACH_CONCEPT_ID] = {"id": COACH_CONCEPT_ID, "name": "Coach"}
    graph.concepts[PERSONA_CONCEPT_ID] = {"id": PERSONA_CONCEPT_ID, "name": "Persona"}
    graph.isa[GIOCATORE_ID] = PERSONA_CONCEPT_ID
    graph.isa[COACH_CONCEPT_ID] = PERSONA_CONCEPT_ID
    graph.isa[PERSONA_CONCEPT_ID] = kernel
    for pid in PLAYER_IDS:
        graph.member_of[pid] = kernel
    graph.member_of[ALICE_ID] = GIOCATORE_ID
    graph.member_of[MID_ID] = GIOCATORE_ID
    graph.member_of[COACH_ID] = COACH_CONCEPT_ID
    graph.member_of[MARIO_ID] = kernel
    kernel_soc = kernel_catch_all_concept_id(EntityKernelType.CostruttoSociale)
    graph.concepts[kernel_soc] = {
        "id": kernel_soc,
        "name": "CostruttoSociale",
        "kernel_category": "CostruttoSociale",
        "promoted": True,
    }
    graph.member_of[DITTA_ID] = kernel_soc
    graph.member_of[CLUB_ID] = kernel_soc
    return kernel


async def _write_facts(session: MetaGraphSession) -> None:
    await write_node_relation(
        session,
        head_id=PLAYER_IDS[0],
        tail_id=PLAYER_IDS[1],
        relation="plays_with",
        normalized_relation="plays_with",
        kernel_parent=RelationKernelType.SocialeIntenzionale,
        head_name="Player 0",
        tail_name="Player 1",
        witness_source="Player 0",
        witness_target="Player 1",
    )
    await write_node_relation(
        session,
        head_id=PLAYER_IDS[2],
        tail_id=PLAYER_IDS[3],
        relation="coached_by",
        normalized_relation="coached_by",
        kernel_parent=RelationKernelType.Partecipativa,
        head_name="Player 2",
        tail_name="Player 3",
        witness_source="Player 2",
        witness_target="Player 3",
    )
    await write_node_relation(
        session,
        head_id=PLAYER_IDS[4],
        tail_id=CLUB_ID,
        relation="plays_for",
        normalized_relation="plays_for",
        kernel_parent=RelationKernelType.SocialeIntenzionale,
        head_name="Player 4",
        tail_name="Squadra locale",
        witness_source="Player 4",
        witness_target="Squadra",
    )
    await write_node_relation(
        session,
        head_id=ALICE_ID,
        tail_id=MID_ID,
        relation="teammate",
        normalized_relation="teammate",
        kernel_parent=RelationKernelType.SocialeIntenzionale,
        head_name="Alice",
        tail_name="Mid",
        witness_source="Alice",
        witness_target="Mid",
    )
    await write_node_relation(
        session,
        head_id=MID_ID,
        tail_id=COACH_ID,
        relation="coached_by",
        normalized_relation="coached_by",
        kernel_parent=RelationKernelType.Partecipativa,
        head_name="Mid",
        tail_name="Coach X",
        witness_source="Mid",
        witness_target="Coach X",
    )
    await write_node_relation(
        session,
        head_id=MARIO_ID,
        tail_id=YEAR_2010_ID,
        relation="Fonte A: ha vinto il torneo nel 2010",
        normalized_relation="won",
        kernel_parent=RelationKernelType.Partecipativa,
        head_name="Mario Rossi",
        tail_name="Torneo 2010",
        witness_source="Fonte A",
        witness_target="torneo 2010",
    )
    await write_node_relation(
        session,
        head_id=MARIO_ID,
        tail_id=YEAR_2011_ID,
        relation="Fonte B: ha vinto il torneo nel 2011",
        normalized_relation="won",
        kernel_parent=RelationKernelType.Partecipativa,
        head_name="Mario Rossi",
        tail_name="Torneo 2011",
        witness_source="Fonte B",
        witness_target="torneo 2011",
    )
    await write_node_relation(
        session,
        head_id=MARIO_ID,
        tail_id=ROLE_ID,
        relation="Weah era calciatore",
        normalized_relation="was",
        kernel_parent=RelationKernelType.SocialeIntenzionale,
        head_name="Mario Rossi",
        tail_name="Ruolo",
        witness_source="archivio",
        witness_target="ruolo",
        valid_time="2010",
    )
    await write_node_relation(
        session,
        head_id=MARIO_ID,
        tail_id=ROLE_ID,
        relation="Dal 2018 Mario Rossi è presidente",
        normalized_relation="is",
        kernel_parent=RelationKernelType.SocialeIntenzionale,
        head_name="Mario Rossi",
        tail_name="Ruolo",
        witness_source="archivio",
        witness_target="ruolo",
        valid_time="2018",
    )
    await write_node_relation(
        session,
        head_id=MARIO_ID,
        tail_id=CONSULT_ID,
        relation="consulente dal 2016",
        normalized_relation="consults",
        kernel_parent=RelationKernelType.SocialeIntenzionale,
        head_name="Mario Rossi",
        tail_name="Consulente",
        witness_source="nota",
        witness_target="ruolo",
    )
    await write_node_relation(
        session,
        head_id=MARIO_ID,
        tail_id=CONSULT_ID,
        relation="In realtà mi sono sbagliato, non dal 2016 ma dal 2017",
        normalized_relation="consults",
        kernel_parent=RelationKernelType.SocialeIntenzionale,
        head_name="Mario Rossi",
        tail_name="Consulente",
        witness_source="correzione",
        witness_target="ruolo",
    )


def _assert_closed_vocab(graph: MetaGraph) -> None:
    for node in graph.nodes.values():
        cat = node.get("kernel_category")
        assert cat in _ENTITY_VALUES, cat
    for concept in graph.concepts.values():
        cat = concept.get("kernel_category")
        if cat:
            assert cat in _ENTITY_VALUES, cat
    for rel in graph.relations:
        if rel.get("lifted_from"):
            continue
        parent = rel.get("kernel_parent")
        assert parent in _RELATION_VALUES, parent


@pytest.mark.asyncio
async def test_metagraph_e2e_fixed_corpus_pipeline(e2e_stubs):
    session = MetaGraphSession()

    await _seed_ingest(session)
    assert session.graph.derived_from
    assert all(chunk == CHUNK_ID for _nid, chunk in session.graph.derived_from)

    kernel = _seed_backbone(session.graph)
    await _write_facts(session)
    await write_contradicts(
        session,
        left_id=YEAR_2010_ID,
        right_id=YEAR_2011_ID,
        subject_id=MARIO_ID,
        relation="won",
        kernel_parent=RelationKernelType.Partecipativa.value,
    )

    promoted_id = await promote(session, kernel, list(PLAYER_IDS))
    assert promoted_id
    assert session.graph.concepts[promoted_id]["promoted"] is True
    assert session.graph.concepts[promoted_id]["kernel_category"] == "Agente"
    for pid in PLAYER_IDS:
        assert session.graph.member_of[pid] == promoted_id

    uri = identity_uri_from_facet_ids([MARIO_ID, DITTA_ID])
    await ensure_identity_node(
        session, uri=uri, canonical_summary="Ditta individuale Mario Rossi"
    )
    await link_as_facet(session, uri, MARIO_ID)
    await link_as_facet(session, uri, DITTA_ID)
    assert session.graph.nodes[MARIO_ID]["kernel_category"] == "Agente"
    assert session.graph.nodes[DITTA_ID]["kernel_category"] == "CostruttoSociale"
    assert MARIO_ID in session.graph.nodes and DITTA_ID in session.graph.nodes
    same_as = [
        e
        for e in session.graph.famiglia
        if e["rel_type"] == SAME_AS and e["dst"] == uri
    ]
    assert {e["src"] for e in same_as} == {MARIO_ID, DITTA_ID}

    old_role = _rel_id(session, MARIO_ID, ROLE_ID, "Weah era calciatore")
    new_role = _rel_id(
        session, MARIO_ID, ROLE_ID, "Dal 2018 Mario Rossi è presidente"
    )
    await session.run(
        APPLY_SUPERSEDES_CYPHER,
        head_id=MARIO_ID,
        tail_id=ROLE_ID,
        new_rel_id=new_role,
        old_rel_id=old_role,
    )
    old_consult = _rel_id(session, MARIO_ID, CONSULT_ID, "consulente dal 2016")
    new_consult = _rel_id(
        session,
        MARIO_ID,
        CONSULT_ID,
        "In realtà mi sono sbagliato, non dal 2016 ma dal 2017",
    )
    await session.run(
        APPLY_UPDATED_BY_CYPHER,
        head_id=MARIO_ID,
        tail_id=CONSULT_ID,
        new_rel_id=new_consult,
        old_rel_id=old_consult,
    )
    assert any(e["rel_type"] == "SUPERSEDES" for e in session.graph.famiglia)
    assert any(e["rel_type"] == "UPDATED_BY" for e in session.graph.famiglia)

    win_latest = [
        rel
        for rel in session.graph.relations
        if session.graph._src(rel) == MARIO_ID
        and session.graph._tgt(rel) in {YEAR_2010_ID, YEAR_2011_ID}
    ]
    assert all(rel.get("is_latest") is True for rel in win_latest)
    assert session.graph._has_famiglia(YEAR_2010_ID, YEAR_2011_ID, "CONTRADICTS")

    writes_before_judge = session.relation_writes
    stats = await run_judge(session, JOB_ID, promoted_parent_ids=[promoted_id])
    assert JOB_ID in session.graph.judge_runs
    assert stats is not None
    assert session.graph._has_famiglia(YEAR_2010_ID, YEAR_2011_ID, "CONTRADICTS")

    derive_calls_before = len(session.calls)
    links = await derive_candidate_links(session, source_id=ALICE_ID, target_id=COACH_ID)
    derive_cyphers = [cy for cy, _ in session.calls[derive_calls_before:]]
    assert links
    assert links[0].derivation_chain
    for cypher in derive_cyphers:
        assert _RELATION_WRITE_RE.search(cypher) is None
        assert "CREATE" not in cypher
        assert "MERGE" not in cypher
    assert session.relation_writes == writes_before_judge

    subgraph = NodeSubgraph(
        nodes=[],
        relationships=[
            NodeSubgraphRelationship(source=ALICE_ID, target=MID_ID, type="teammate"),
            NodeSubgraphRelationship(source=MID_ID, target=COACH_ID, type="coached_by"),
        ],
    )
    citations = label_query_citations(
        cited_node_ids=[ALICE_ID, COACH_ID],
        subgraph=subgraph,
        derived_links=links,
        context_ids=[ALICE_ID, COACH_ID],
    )
    derived = [c for c in citations if c.epistemic_status == "derived"]
    asserted = [c for c in citations if c.epistemic_status == "asserted"]
    assert derived
    assert derived[0].derivation_chain
    assert asserted
    assert any(c.id == ALICE_ID and c.epistemic_status == "asserted" for c in citations)

    _assert_closed_vocab(session.graph)
    assert len(RelationKernelType) == 6
    assert {n["kernel_category"] for n in session.graph.nodes.values()} <= _ENTITY_VALUES


def test_derive_candidate_links_source_never_writes_relation():
    from app.pipeline import node_query_engine as nqe

    source = inspect.getsource(nqe.derive_candidate_links)
    assert _RELATION_WRITE_RE.search(source) is None
    for cypher in (
        nqe.LOAD_CONNECTIVITY_RULES_CYPHER,
        nqe.LOAD_LEAF_S0_RELATIONS_CYPHER,
        nqe.LOAD_NODE_TYPE_TOKENS_CYPHER,
        nqe.LOAD_ISA_EDGES_CYPHER,
    ):
        assert _RELATION_WRITE_RE.search(cypher) is None
        assert "CREATE" not in cypher
        assert "MERGE" not in cypher
