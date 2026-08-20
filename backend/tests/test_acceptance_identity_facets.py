"""Fase 8 acceptance: identity + facets, reversible, merge_nodes behind flag."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from app.core.config import Settings
from app.pipeline.identity_resolution import (
    LINK_POSSIBLY_SAME_AS_CYPHER,
    LINK_SAME_AS_CYPHER,
    LOAD_NODE_IDENTITY_FIELDS_CYPHER,
    LOAD_NOT_SAME_AS_NEIGHBORS_CYPHER,
    LOAD_OTHER_NODES_IDENTITY_FIELDS_CYPHER,
    MARK_NOT_SAME_AS_CYPHER,
    MERGE_IDENTITY_NODE_CYPHER,
    NOT_SAME_AS,
    POSSIBLY_SAME_AS,
    SAME_AS,
    UNLINK_FACET_CYPHER,
    cosine,
    ensure_identity_node,
    generate_identity_candidates,
    generate_identity_candidates_from_session,
    identity_uri_from_facet_ids,
    identity_uri_from_name,
    link_as_facet,
    link_possibly_same_as,
    mark_not_same_as,
    normalize_identity_name,
    unlink_facet,
)
from app.pipeline.node_resolution import (
    DELETE_DUP_RELATIONS_CYPHER,
    FIND_EXACT_NAME_CYPHER,
    SET_MERGED_INTO_CYPHER,
    merge_nodes,
    resolve_node,
)

JOB_ID = "job-identity-f8"
EMBEDDING = [0.1, 0.2, 0.3]
TIMESTAMP_KEYS = frozenset({"created_at", "updated_at", "system_time", "timestamp"})


class FakeRel:
    def __init__(self, **props):
        self._props = props

    def items(self):
        return self._props.items()

    def keys(self):
        return self._props.keys()

    def get(self, key, default=None):
        return self._props.get(key, default)

    def __getitem__(self, key):
        return self._props[key]


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


class FakeSession:
    """Queue-based FakeSession (same pattern as test_node_resolution.py)."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.queue: list[list[dict]] = []

    def enqueue(self, records: list[dict]) -> None:
        self.queue.append(records)

    async def run(self, cypher, **kwargs):
        self.calls.append((cypher, kwargs))
        records = self.queue.pop(0) if self.queue else []
        return FakeResult(records)


def _unit(vec: tuple[float, float]) -> list[float]:
    x, y = vec
    return [x, y]


def _vec_at_cosine(target: float) -> list[float]:
    """Unit vector whose cosine with [1, 0] is ``target``."""
    y = math.sqrt(max(0.0, 1.0 - target * target))
    return [target, y]


def _strip_props(props: dict) -> dict:
    return {k: v for k, v in props.items() if k not in TIMESTAMP_KEYS}


def _edge_key(edge: dict) -> tuple:
    props = tuple(sorted(_strip_props(edge.get("props") or {}).items()))
    return (
        edge["src_kind"],
        edge["src_id"],
        edge["rel_type"],
        edge["dst_kind"],
        edge["dst_id"],
        props,
    )


class IdentityGraph:
    """In-memory graph that interprets identity Cypher (no Docker)."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.identity_nodes: dict[str, dict] = {}
        self.edges: list[dict] = []
        self.calls: list[tuple[str, dict]] = []

    def add_node(self, node_id: str, **props) -> None:
        row = {"id": node_id, **props}
        self.nodes[node_id] = row

    def add_edge(
        self,
        src_kind: str,
        src_id: str,
        rel_type: str,
        dst_kind: str,
        dst_id: str,
        **props,
    ) -> None:
        self.edges.append(
            {
                "src_kind": src_kind,
                "src_id": src_id,
                "rel_type": rel_type,
                "dst_kind": dst_kind,
                "dst_id": dst_id,
                "props": dict(props),
            }
        )

    def snapshot(self) -> tuple:
        nodes = {nid: _strip_props(dict(props)) for nid, props in self.nodes.items()}
        identities = {
            uri: _strip_props(dict(props)) for uri, props in self.identity_nodes.items()
        }
        edges = tuple(sorted(_edge_key(edge) for edge in self.edges))
        return (nodes, identities, edges)

    def _has_edge(
        self,
        src_kind: str,
        src_id: str,
        rel_type: str,
        dst_kind: str,
        dst_id: str,
    ) -> bool:
        for edge in self.edges:
            if (
                edge["src_kind"] == src_kind
                and edge["src_id"] == src_id
                and edge["rel_type"] == rel_type
                and edge["dst_kind"] == dst_kind
                and edge["dst_id"] == dst_id
            ):
                return True
        return False

    def _merge_edge(
        self,
        src_kind: str,
        src_id: str,
        rel_type: str,
        dst_kind: str,
        dst_id: str,
    ) -> None:
        if not self._has_edge(src_kind, src_id, rel_type, dst_kind, dst_id):
            self.add_edge(src_kind, src_id, rel_type, dst_kind, dst_id)

    def _node_row(self, node_id: str) -> dict:
        node = self.nodes[node_id]
        return {
            "id": node["id"],
            "name": node.get("name"),
            "kernel_category": node.get("kernel_category"),
            "summary_embedding": node.get("summary_embedding", node.get("embedding")),
            "aliases": node.get("aliases"),
        }

    def enqueue(self, records: list[dict]) -> None:
        """API compatibility with FakeSession; unused — Cypher is interpreted."""

    async def run(self, cypher, **kwargs):
        self.calls.append((cypher, kwargs))
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
                self._merge_edge("Node", facet_id, SAME_AS, "IdentityNode", identity_id)
            return FakeResult([])

        if cypher == UNLINK_FACET_CYPHER:
            facet_id = kwargs["facet_node_id"]
            identity_id = kwargs["identity_id"]
            self.edges = [
                edge
                for edge in self.edges
                if not (
                    edge["rel_type"] in {SAME_AS, POSSIBLY_SAME_AS}
                    and {
                        (edge["src_kind"], edge["src_id"]),
                        (edge["dst_kind"], edge["dst_id"]),
                    }
                    == {("Node", facet_id), ("IdentityNode", identity_id)}
                )
            ]
            return FakeResult([])

        if cypher == LINK_POSSIBLY_SAME_AS_CYPHER:
            src_id, dst_id = kwargs["src_id"], kwargs["dst_id"]
            if src_id in self.nodes and dst_id in self.nodes:
                self._merge_edge("Node", src_id, POSSIBLY_SAME_AS, "Node", dst_id)
            return FakeResult([])

        if cypher == MARK_NOT_SAME_AS_CYPHER:
            src_id, dst_id = kwargs["src_id"], kwargs["dst_id"]
            if src_id in self.nodes and dst_id in self.nodes:
                self._merge_edge("Node", src_id, NOT_SAME_AS, "Node", dst_id)
            return FakeResult([])

        if cypher == LOAD_NODE_IDENTITY_FIELDS_CYPHER:
            node_id = kwargs["node_id"]
            node = self.nodes.get(node_id)
            if node is None or node.get("merged_into") is not None:
                return FakeResult([])
            return FakeResult([self._node_row(node_id)])

        if cypher == LOAD_OTHER_NODES_IDENTITY_FIELDS_CYPHER:
            node_id = kwargs["node_id"]
            rows = [
                self._node_row(nid)
                for nid, node in self.nodes.items()
                if nid != node_id and node.get("merged_into") is None
            ]
            return FakeResult(rows)

        if cypher == LOAD_NOT_SAME_AS_NEIGHBORS_CYPHER:
            node_id = kwargs["node_id"]
            ids: list[dict] = []
            for edge in self.edges:
                if edge["rel_type"] != NOT_SAME_AS:
                    continue
                ends = {edge["src_id"], edge["dst_id"]}
                if node_id in ends:
                    other = next(eid for eid in ends if eid != node_id)
                    ids.append({"id": other})
            return FakeResult(ids)

        if cypher == FIND_EXACT_NAME_CYPHER:
            name = kwargs["name"]
            node_type = kwargs["type"]
            node_id = kwargs["node_id"]
            rows = [
                {"id": nid, "name": node.get("name")}
                for nid, node in self.nodes.items()
                if nid != node_id
                and node.get("name") == name
                and node.get("type") == node_type
                and node.get("merged_into") is None
            ]
            return FakeResult(rows)

        return FakeResult([])


def _spy_merge(monkeypatch) -> list[tuple[str, str]]:
    merges: list[tuple[str, str]] = []

    async def fake_merge(_session, dup_id: str, canon_id: str) -> None:
        merges.append((dup_id, canon_id))

    monkeypatch.setattr("app.pipeline.node_resolution.merge_nodes", fake_merge)
    return merges


def _cypher_has_rel(cypher: str, rel: str) -> bool:
    return f":{rel}" in cypher or f"[:{rel}" in cypher


def _calls_write_same_as(calls: list[tuple[str, dict]]) -> bool:
    return any(
        _cypher_has_rel(cypher, SAME_AS) and not _cypher_has_rel(cypher, POSSIBLY_SAME_AS)
        for cypher, _ in calls
    )


def test_flag_defaults_off():
    assert Settings.model_fields["ENABLE_FACET_IDENTITY"].default is False
    assert Settings.model_fields["IDENTITY_BLOCK_THRESHOLD"].default == 0.82


def test_identity_uri_scheme():
    assert identity_uri_from_name("José Rossi!", "Agente") == "identity:jose rossi:Agente"
    assert identity_uri_from_facet_ids(["b", "a"]) == "identity:a,b"


def test_normalize_identity_name_strips_punctuation_and_accents():
    assert normalize_identity_name("  José Rossi! ") == "jose rossi"
    assert normalize_identity_name("ALICE") == "alice"


def test_cosine_identical_orthogonal_empty():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine([], [1.0]) == 0.0
    assert cosine([1.0], [1.0, 0.0]) == 0.0


def test_link_as_facet_cypher_is_non_destructive():
    compact = " ".join(LINK_SAME_AS_CYPHER.split())
    assert "MERGE (facet)-[:SAME_AS]->(identity)" in compact
    assert "DELETE" not in LINK_SAME_AS_CYPHER
    assert "merged_into" not in LINK_SAME_AS_CYPHER
    assert "HAS_CONCEPT" not in LINK_SAME_AS_CYPHER
    assert "DERIVED_FROM" not in LINK_SAME_AS_CYPHER
    assert ":Relation" not in LINK_SAME_AS_CYPHER
    unlink = " ".join(UNLINK_FACET_CYPHER.split())
    assert "SAME_AS|POSSIBLY_SAME_AS" in unlink
    assert "DELETE r" in unlink
    assert ":Relation" not in UNLINK_FACET_CYPHER
    assert "HAS_CONCEPT" not in UNLINK_FACET_CYPHER
    assert "DERIVED_FROM" not in UNLINK_FACET_CYPHER


@pytest.mark.asyncio
async def test_link_as_facet_keeps_facet_relations():
    graph = IdentityGraph()
    graph.add_node("mario-calcio", name="Mario", kernel_category="Agente", type="entity")
    graph.add_node("club-x", name="Club X", kernel_category="CostruttoSociale", type="entity")
    graph.add_edge(
        "Node",
        "mario-calcio",
        "Relation",
        "Node",
        "club-x",
        relation="plays_for",
        kernel_parent="Partecipativa",
    )
    graph.add_edge("Node", "mario-calcio", "HAS_CONCEPT", "Concept", "concept-calcio")
    graph.add_edge("Node", "mario-calcio", "DERIVED_FROM", "Chunk", "chunk-1")

    uri = await ensure_identity_node(
        graph, uri=identity_uri_from_name("Mario", "Agente"), canonical_summary="Mario"
    )
    before_facet_edges = [e for e in graph.edges if e["src_id"] == "mario-calcio"]
    await link_as_facet(graph, uri, "mario-calcio")

    rels = [
        e
        for e in graph.edges
        if e["src_id"] == "mario-calcio" and e["rel_type"] == "Relation"
    ]
    assert len(rels) == 1
    assert rels[0]["dst_id"] == "club-x"
    assert any(
        e["src_id"] == "mario-calcio" and e["rel_type"] == "HAS_CONCEPT" for e in graph.edges
    )
    assert any(
        e["src_id"] == "mario-calcio" and e["rel_type"] == "DERIVED_FROM" for e in graph.edges
    )
    identity_rels = [
        e for e in graph.edges if e["src_kind"] == "IdentityNode" and e["rel_type"] == "Relation"
    ]
    assert identity_rels == []
    assert graph.nodes["mario-calcio"].get("merged_into") is None
    after_non_identity = [
        e for e in graph.edges if e["rel_type"] not in {SAME_AS, POSSIBLY_SAME_AS}
    ]
    assert len(after_non_identity) == len(before_facet_edges)
    assert any(
        e["src_id"] == "mario-calcio"
        and e["rel_type"] == SAME_AS
        and e["dst_id"] == uri
        for e in graph.edges
    )


@pytest.mark.asyncio
async def test_link_then_unlink_restores_graph_snapshot():
    graph = IdentityGraph()
    graph.add_node("facet-a", name="Mario", kernel_category="Agente", type="entity")
    graph.add_node("other", name="Club", kernel_category="CostruttoSociale", type="entity")
    graph.add_edge(
        "Node",
        "facet-a",
        "Relation",
        "Node",
        "other",
        relation="plays_for",
        created_at="2026-01-01T00:00:00Z",
    )
    graph.add_edge("Node", "facet-a", "HAS_CONCEPT", "Concept", "c1")
    uri = await ensure_identity_node(
        graph, uri="identity:mario:Agente", canonical_summary="Mario"
    )
    before = graph.snapshot()
    await link_as_facet(graph, uri, "facet-a")
    assert before != graph.snapshot()
    await unlink_facet(graph, uri, "facet-a")
    assert graph.snapshot() == before


@pytest.mark.asyncio
async def test_flag_on_resolve_node_writes_possibly_same_as_not_merge(monkeypatch):
    monkeypatch.setattr("app.pipeline.node_resolution.settings.ENABLE_FACET_IDENTITY", True)
    session = FakeSession()
    session.enqueue([{"id": "alice-canon", "name": "Alice"}])
    merges = _spy_merge(monkeypatch)

    result = await resolve_node(
        session,
        node_id="alice-new",
        node_type="entity",
        name="Alice",
        embedding=EMBEDDING,
        job_id=JOB_ID,
    )

    assert result == "alice-new"
    assert merges == []
    assert any(cypher == LINK_POSSIBLY_SAME_AS_CYPHER for cypher, _ in session.calls)
    assert not _calls_write_same_as(session.calls)
    assert not any(cypher == SET_MERGED_INTO_CYPHER for cypher, _ in session.calls)
    assert not any(cypher == DELETE_DUP_RELATIONS_CYPHER for cypher, _ in session.calls)
    assert not any("merged_into" in cypher and "SET" in cypher for cypher, _ in session.calls)
    assert not any(":Relation" in cypher and "DELETE" in cypher for cypher, _ in session.calls)


@pytest.mark.asyncio
async def test_flag_off_resolve_node_still_merge_nodes(monkeypatch):
    assert Settings.model_fields["ENABLE_FACET_IDENTITY"].default is False
    monkeypatch.setattr("app.pipeline.node_resolution.settings.ENABLE_FACET_IDENTITY", False)
    session = FakeSession()
    session.enqueue([{"id": "alice-canon", "name": "Alice"}])
    merges = _spy_merge(monkeypatch)

    result = await resolve_node(
        session,
        node_id="alice-new",
        node_type="entity",
        name="Alice",
        embedding=EMBEDDING,
        job_id=JOB_ID,
    )

    assert result == "alice-canon"
    assert merges == [("alice-new", "alice-canon")]
    assert not any(cypher == LINK_POSSIBLY_SAME_AS_CYPHER for cypher, _ in session.calls)


@pytest.mark.asyncio
async def test_merge_nodes_still_available_and_sets_merged_into():
    session = FakeSession()
    session.enqueue(
        [
            {
                "r": FakeRel(relation="works at", normalized_relation=None, is_latest=True),
                "other_id": "node-x",
            }
        ]
    )
    await merge_nodes(session, "dup", "canon")
    assert any(
        call[0] == SET_MERGED_INTO_CYPHER
        and call[1]["dup_id"] == "dup"
        and call[1]["canon_id"] == "canon"
        for call in session.calls
    )


def test_blocking_kernel_category_and_cosine():
    tau = 0.82
    alice = {
        "id": "a1",
        "name": "Alice",
        "kernel_category": "Agente",
        "summary_embedding": _unit((1.0, 0.0)),
        "aliases": (),
    }
    same_cat_high = {
        "id": "a2",
        "name": "Alicia Example",
        "kernel_category": "Agente",
        "summary_embedding": _vec_at_cosine(0.82),
        "aliases": (),
    }
    same_cat_low = {
        "id": "a3",
        "name": "Someone Else",
        "kernel_category": "Agente",
        "summary_embedding": _vec_at_cosine(0.81),
        "aliases": (),
    }
    different_cat = {
        "id": "p1",
        "name": "Alice",
        "kernel_category": "Luogo",
        "summary_embedding": _unit((1.0, 0.0)),
        "aliases": (),
    }
    paris_place = {
        "id": "paris-city",
        "name": "Paris",
        "kernel_category": "Luogo",
        "summary_embedding": _unit((0.0, 1.0)),
        "aliases": (),
    }
    paris_person = {
        "id": "paris-hilton",
        "name": "Paris",
        "kernel_category": "Agente",
        "summary_embedding": _unit((1.0, 0.0)),
        "aliases": (),
    }

    assert generate_identity_candidates(alice, [same_cat_high], tau=tau) == ["a2"]
    assert generate_identity_candidates(alice, [same_cat_low], tau=tau) == []
    assert generate_identity_candidates(alice, [different_cat], tau=tau) == []
    assert generate_identity_candidates(paris_person, [paris_place], tau=tau) == []
    assert generate_identity_candidates(
        alice, [same_cat_high, same_cat_low, different_cat], tau=tau
    ) == ["a2"]


def test_blocking_alias_hit_same_category():
    node = {
        "id": "obama-new",
        "name": "Obama",
        "kernel_category": "Agente",
        "summary_embedding": _vec_at_cosine(0.1),
        "aliases": (),
    }
    other = {
        "id": "obama-1",
        "name": "Barack Obama",
        "kernel_category": "Agente",
        "summary_embedding": _vec_at_cosine(0.1),
        "aliases": ["Obama", "Barack H. Obama"],
    }
    assert generate_identity_candidates(node, [other], tau=0.82) == ["obama-1"]


@pytest.mark.asyncio
async def test_mark_not_same_as_skips_pair():
    graph = IdentityGraph()
    graph.add_node(
        "a1",
        name="Alice",
        kernel_category="Agente",
        type="entity",
        summary_embedding=_unit((1.0, 0.0)),
    )
    graph.add_node(
        "a2",
        name="Alice",
        kernel_category="Agente",
        type="entity",
        summary_embedding=_unit((1.0, 0.0)),
    )
    before = generate_identity_candidates(
        graph._node_row("a1"),
        [graph._node_row("a2")],
        tau=0.82,
    )
    assert before == ["a2"]

    await mark_not_same_as(graph, "a1", "a2")
    pairs = await generate_identity_candidates_from_session(graph, "a1", tau=0.82)
    assert pairs == []
    skipped = generate_identity_candidates(
        graph._node_row("a1"),
        [graph._node_row("a2")],
        tau=0.82,
        not_same_as={frozenset({"a1", "a2"})},
    )
    assert skipped == []


@pytest.mark.asyncio
async def test_omonimia_same_name_never_auto_same_as_when_flag_on(monkeypatch):
    monkeypatch.setattr("app.pipeline.node_resolution.settings.ENABLE_FACET_IDENTITY", True)
    session = FakeSession()
    session.enqueue([{"id": "paris-city", "name": "Paris"}])
    merges = _spy_merge(monkeypatch)

    result = await resolve_node(
        session,
        node_id="paris-hilton",
        node_type="entity",
        name="Paris",
        embedding=EMBEDDING,
        job_id=JOB_ID,
    )

    assert result == "paris-hilton"
    assert merges == []
    assert not _calls_write_same_as(session.calls)
    assert any(cypher == LINK_POSSIBLY_SAME_AS_CYPHER for cypher, _ in session.calls)

    city = {
        "id": "paris-city",
        "name": "Paris",
        "kernel_category": "Luogo",
        "summary_embedding": _unit((1.0, 0.0)),
        "aliases": (),
    }
    person = {
        "id": "paris-hilton",
        "name": "Paris",
        "kernel_category": "Agente",
        "summary_embedding": _unit((0.0, 1.0)),
        "aliases": (),
    }
    assert generate_identity_candidates(person, [city], tau=0.82) == []


def test_dreaming_does_not_call_link_as_facet():
    text = Path(__file__).resolve().parents[1].joinpath("app/pipeline/dreaming.py").read_text(
        encoding="utf-8"
    )
    assert "link_as_facet" not in text
    assert "resolve_node" in text


@pytest.mark.asyncio
async def test_ensure_identity_node_does_not_destroy_facets():
    graph = IdentityGraph()
    graph.add_node("facet-a", name="Mario", kernel_category="Agente")
    graph.add_edge("Node", "facet-a", "Relation", "Node", "club", relation="plays_for")
    before = graph.snapshot()
    await ensure_identity_node(graph, uri="identity:mario:Agente", canonical_summary="Mario")
    nodes, _identities, edges = graph.snapshot()
    assert nodes == before[0]
    assert ("Node", "facet-a", "Relation", "Node", "club", (("relation", "plays_for"),)) in edges
    await ensure_identity_node(graph, uri="identity:mario:Agente", canonical_summary="ignored")
    assert graph.identity_nodes["identity:mario:Agente"]["canonical_summary"] == "ignored"
    after = graph.snapshot()
    assert after[0] == before[0]
    assert ("Node", "facet-a", "Relation", "Node", "club", (("relation", "plays_for"),)) in after[2]


@pytest.mark.asyncio
async def test_link_possibly_same_as_never_writes_same_as():
    graph = IdentityGraph()
    graph.add_node("n1", name="A")
    graph.add_node("n2", name="B")
    await link_possibly_same_as(graph, "n2", "n1")
    assert any(e["rel_type"] == POSSIBLY_SAME_AS for e in graph.edges)
    assert not any(e["rel_type"] == SAME_AS for e in graph.edges)
    src_ids = {(e["src_id"], e["dst_id"]) for e in graph.edges if e["rel_type"] == POSSIBLY_SAME_AS}
    assert src_ids == {("n1", "n2")}
