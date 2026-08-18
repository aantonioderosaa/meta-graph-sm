"""Fase 6 acceptance: leaf facts at LCA via projection, not copy. No Docker."""

from __future__ import annotations

import inspect

import pytest

from app.models.kernel import EntityKernelType, RelationKernelType
from app.pipeline import lca as lca_mod
from app.pipeline.event_relation_resolution import (
    CREATE_SITUATION_EVENT_CYPHER,
    FIND_EVENT_BY_NAME_CYPHER,
    LINK_SITUATION_CHUNK_CYPHER,
    MERGE_SITUATION_PARTICIPATES_CYPHER,
    SITUATION_NORMALIZED_RELATION,
    SITUATION_PARTICIPATES_RELATION,
    reify_shared_situation,
)
from app.pipeline.lca import (
    COUNT_PHYSICAL_LEAF_FACTS_CYPHER,
    FACTS_VISIBLE_IN_SUBDOMAIN_CYPHER,
    FIND_HOME_AND_ANCESTORS_CYPHER,
    WITNESSED_NEIGHBORS_CYPHER,
    compute_lca,
    count_physical_leaf_facts,
    facts_visible_in_subdomain,
    lca_from_ancestor_lists,
    witnessed_neighbors,
)
from app.pipeline.node_graph_engine import (
    FACTS_VISIBLE_IN_SUBDOMAIN_CYPHER as ENGINE_FACTS_CYPHER,
)
from app.pipeline.node_graph_engine import (
    count_physical_leaf_facts as engine_count_physical_leaf_facts,
)
from app.pipeline.node_graph_engine import (
    facts_visible_in_subdomain as engine_facts_visible_in_subdomain,
)
from app.pipeline.node_graph_engine import (
    witnessed_neighbors as engine_witnessed_neighbors,
)

P = "concept-P"
A = "concept-A"
B = "concept-B"
SINNER = "sinner"
TEAMMATE = "teammate"
LONELY = "lonely"
COACH = "coach-x"
FACT_TEAM = "rel-teammate"
FACT_COACH = "rel-coached-by"
LIFT_SHADOW = "rel-lift-shadow"


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


class FactGraph:
    def __init__(self) -> None:
        self.concepts: dict[str, dict] = {}
        self.nodes: dict[str, dict] = {}
        self.member_of: dict[str, str] = {}
        self.isa: dict[str, str] = {}
        self.relations: list[dict] = []
        self.rel_seq = 0
        self.chunks: set[str] = set()
        self.derived_from: list[tuple[str, str]] = []

    def add_concept(self, concept_id: str, name: str) -> None:
        self.concepts[concept_id] = {"id": concept_id, "name": name}

    def add_node(self, node_id: str, name: str, *, node_type: str = "entity") -> None:
        self.nodes[node_id] = {
            "id": node_id,
            "name": name,
            "type": node_type,
            "merged_into": None,
            "kernel_category": None,
        }

    def add_leaf_fact(
        self,
        rel_id: str,
        from_id: str,
        to_id: str,
        relation: str,
        *,
        lifted_from: str | None = None,
        kernel_parent: str | None = None,
        normalized_relation: str | None = None,
    ) -> None:
        self.relations.append(
            {
                "id": rel_id,
                "from_id": from_id,
                "to_id": to_id,
                "relation": relation,
                "normalized_relation": normalized_relation or relation,
                "kernel_parent": kernel_parent,
                "lifted_from": lifted_from,
            }
        )

    def descendant_concepts(self, concept_id: str) -> set[str]:
        found = {concept_id}
        changed = True
        while changed:
            changed = False
            for child, parent in self.isa.items():
                if parent in found and child not in found:
                    found.add(child)
                    changed = True
        return found

    def instance_ids(self, concept_id: str) -> set[str]:
        concepts = self.descendant_concepts(concept_id)
        return {
            nid
            for nid, home in self.member_of.items()
            if home in concepts and self.nodes.get(nid, {}).get("merged_into") is None
        }

    def ancestor_rows(self, node_id: str) -> list[dict]:
        home = self.member_of.get(node_id)
        if home is None or home not in self.concepts:
            return []
        rows = [{"home_id": home, "ancestor_id": home, "dist": 0}]
        current = home
        dist = 0
        seen = {home}
        while current in self.isa:
            parent = self.isa[current]
            if parent in seen:
                break
            dist += 1
            rows.append({"home_id": home, "ancestor_id": parent, "dist": dist})
            seen.add(parent)
            current = parent
        return rows


class GraphSession:
    def __init__(self, graph: FactGraph) -> None:
        self.graph = graph
        self.calls: list[tuple[str, dict]] = []

    async def run(self, cypher, **kwargs):
        self.calls.append((cypher, kwargs))
        return FakeResult(_dispatch(self.graph, cypher, kwargs))


def _dispatch(graph: FactGraph, cypher: str, kwargs: dict) -> list[dict]:
    if cypher == FIND_HOME_AND_ANCESTORS_CYPHER:
        return graph.ancestor_rows(kwargs["node_id"])
    if cypher == FACTS_VISIBLE_IN_SUBDOMAIN_CYPHER:
        instances = graph.instance_ids(kwargs["concept_id"])
        rows = []
        for rel in graph.relations:
            if rel.get("lifted_from"):
                continue
            if rel["from_id"] not in graph.nodes or rel["to_id"] not in graph.nodes:
                continue
            if rel["from_id"] in instances and rel["to_id"] in instances:
                rows.append(
                    {
                        "id": rel["id"],
                        "from_id": rel["from_id"],
                        "to_id": rel["to_id"],
                        "relation": rel["relation"],
                        "normalized_relation": rel["normalized_relation"],
                        "kernel_parent": rel.get("kernel_parent"),
                    }
                )
        return rows
    if cypher == WITNESSED_NEIGHBORS_CYPHER:
        nid = kwargs["node_id"]
        node = graph.nodes.get(nid)
        if node is None or node.get("merged_into") is not None:
            return []
        seen: set[str] = set()
        rows = []
        for rel in graph.relations:
            if rel.get("lifted_from"):
                continue
            other = None
            if rel["from_id"] == nid:
                other = rel["to_id"]
            elif rel["to_id"] == nid:
                other = rel["from_id"]
            if other is None or other == nid or other in seen:
                continue
            other_node = graph.nodes.get(other)
            if other_node is None or other_node.get("merged_into") is not None:
                continue
            seen.add(other)
            rows.append({"id": other})
        return rows
    if cypher == COUNT_PHYSICAL_LEAF_FACTS_CYPHER:
        n = sum(
            1
            for rel in graph.relations
            if not rel.get("lifted_from")
            and rel["from_id"] in graph.nodes
            and rel["to_id"] in graph.nodes
        )
        return [{"n": n}]
    if cypher == FIND_EVENT_BY_NAME_CYPHER:
        name = kwargs["name"]
        for node in graph.nodes.values():
            if (
                node.get("type") == "event"
                and node.get("name") == name
                and node.get("merged_into") is None
            ):
                return [{"id": node["id"]}]
        return []
    if cypher == CREATE_SITUATION_EVENT_CYPHER:
        nid = kwargs["id"]
        graph.nodes[nid] = {
            "id": nid,
            "name": kwargs["name"],
            "type": "event",
            "merged_into": None,
            "kernel_category": kwargs["kernel_category"],
        }
        return [{"id": nid}]
    if cypher == LINK_SITUATION_CHUNK_CYPHER:
        if kwargs["chunk_id"] in graph.chunks and kwargs["node_id"] in graph.nodes:
            graph.derived_from.append((kwargs["node_id"], kwargs["chunk_id"]))
        return []
    if cypher == MERGE_SITUATION_PARTICIPATES_CYPHER:
        event_id = kwargs["event_id"]
        participant_id = kwargs["participant_id"]
        if event_id not in graph.nodes or participant_id not in graph.nodes:
            return []
        for rel in graph.relations:
            if (
                rel["from_id"] == event_id
                and rel["to_id"] == participant_id
                and rel.get("normalized_relation") == kwargs["normalized_relation"]
            ):
                return []
        graph.rel_seq += 1
        graph.add_leaf_fact(
            f"part-{graph.rel_seq}",
            event_id,
            participant_id,
            kwargs["relation"],
            kernel_parent=kwargs["kernel_parent"],
            normalized_relation=kwargs["normalized_relation"],
        )
        return []
    raise AssertionError(f"unexpected cypher: {cypher[:80]}")


def _placement_graph() -> FactGraph:
    graph = FactGraph()
    graph.add_concept(P, "tennis")
    graph.add_concept(A, "players")
    graph.add_concept(B, "coaches")
    graph.isa[A] = P
    graph.isa[B] = P
    graph.add_node(SINNER, "Sinner")
    graph.add_node(TEAMMATE, "Teammate")
    graph.add_node(LONELY, "Lonely")
    graph.add_node(COACH, "Coach X")
    graph.member_of[SINNER] = A
    graph.member_of[TEAMMATE] = A
    graph.member_of[LONELY] = A
    graph.member_of[COACH] = B
    graph.add_leaf_fact(
        FACT_TEAM,
        SINNER,
        TEAMMATE,
        "teammate_of",
        kernel_parent=RelationKernelType.SocialeIntenzionale.value,
    )
    graph.add_leaf_fact(
        FACT_COACH,
        SINNER,
        COACH,
        "coached_by",
        kernel_parent=RelationKernelType.SocialeIntenzionale.value,
    )
    # PROMOTE Concept-endpoint shadow — not a second leaf copy.
    graph.add_leaf_fact(
        LIFT_SHADOW,
        A,
        COACH,
        "coached_by",
        lifted_from=SINNER,
        kernel_parent=RelationKernelType.SocialeIntenzionale.value,
    )
    return graph


def _compact(cypher: str) -> str:
    return " ".join(cypher.split())


def test_lca_from_ancestor_lists_same_home():
    assert lca_from_ancestor_lists(["A"], ["A"]) == "A"


def test_lca_from_ancestor_lists_siblings_share_parent():
    assert lca_from_ancestor_lists(["A", "P", "K"], ["B", "P", "K"]) == "P"


def test_lca_from_ancestor_lists_deeper_child_vs_parent():
    assert lca_from_ancestor_lists(["A", "P", "K"], ["P", "K"]) == "P"


def test_lca_from_ancestor_lists_only_kernel():
    assert lca_from_ancestor_lists(["A", "K"], ["B", "K"]) == "K"


def test_lca_from_ancestor_lists_empty_or_disjoint():
    assert lca_from_ancestor_lists([], ["A"]) is None
    assert lca_from_ancestor_lists(["A"], []) is None
    assert lca_from_ancestor_lists(["A"], ["B"]) is None


def test_facts_visible_cypher_is_traversal_not_copy():
    compact = _compact(FACTS_VISIBLE_IN_SUBDOMAIN_CYPHER)
    assert "CREATE" not in FACTS_VISIBLE_IN_SUBDOMAIN_CYPHER
    assert "MERGE" not in FACTS_VISIBLE_IN_SUBDOMAIN_CYPHER
    assert "SET " not in FACTS_VISIBLE_IN_SUBDOMAIN_CYPHER
    assert "DELETE" not in FACTS_VISIBLE_IN_SUBDOMAIN_CYPHER
    assert ":IS_A*0.." in compact or ":IS_A*0.." in FACTS_VISIBLE_IN_SUBDOMAIN_CYPHER
    assert ":MEMBER_OF" in FACTS_VISIBLE_IN_SUBDOMAIN_CYPHER
    assert "(a:Node)-[r:Relation]->(b:Node)" in compact
    assert "r.lifted_from IS NULL" in FACTS_VISIBLE_IN_SUBDOMAIN_CYPHER
    assert ENGINE_FACTS_CYPHER is FACTS_VISIBLE_IN_SUBDOMAIN_CYPHER


def test_witnessed_neighbors_cypher_ignores_backbone():
    compact = _compact(WITNESSED_NEIGHBORS_CYPHER)
    assert "[r:Relation]" in compact
    assert "MEMBER_OF" not in WITNESSED_NEIGHBORS_CYPHER
    assert "HAS_CONCEPT" not in WITNESSED_NEIGHBORS_CYPHER
    assert "IS_A" not in WITNESSED_NEIGHBORS_CYPHER
    assert "lifted_from IS NULL" in WITNESSED_NEIGHBORS_CYPHER


def test_lca_cypher_uses_member_of_and_is_a():
    assert ":MEMBER_OF" in FIND_HOME_AND_ANCESTORS_CYPHER
    assert ":IS_A" in FIND_HOME_AND_ANCESTORS_CYPHER
    assert "CREATE" not in FIND_HOME_AND_ANCESTORS_CYPHER


def test_no_write_in_lca_read_cypher():
    for name, value in inspect.getmembers(lca_mod):
        if not name.endswith("_CYPHER") or not isinstance(value, str):
            continue
        assert "CREATE" not in value
        assert "MERGE" not in value


@pytest.mark.asyncio
async def test_compute_lca_cross_child_concepts_is_parent():
    session = GraphSession(_placement_graph())
    assert await compute_lca(session, SINNER, COACH) == P
    assert await compute_lca(session, TEAMMATE, COACH) == P
    assert await compute_lca(session, SINNER, TEAMMATE) == A


@pytest.mark.asyncio
async def test_compute_lca_missing_member_of_is_none():
    graph = _placement_graph()
    graph.add_node("orphan", "Orphan")
    session = GraphSession(graph)
    assert await compute_lca(session, "orphan", SINNER) is None
    assert await compute_lca(session, SINNER, "missing") is None


@pytest.mark.asyncio
async def test_facts_visible_from_parent_returns_both_leaf_facts():
    session = GraphSession(_placement_graph())
    facts = await facts_visible_in_subdomain(session, P)
    ids = {row["id"] for row in facts}
    assert ids == {FACT_TEAM, FACT_COACH}
    assert LIFT_SHADOW not in ids
    engine_facts = await engine_facts_visible_in_subdomain(session, P)
    assert {row["id"] for row in engine_facts} == ids


@pytest.mark.asyncio
async def test_facts_visible_from_child_a_only_internal_ends():
    session = GraphSession(_placement_graph())
    facts = await facts_visible_in_subdomain(session, A)
    ids = {row["id"] for row in facts}
    assert ids == {FACT_TEAM}
    pairs = {(row["from_id"], row["to_id"]) for row in facts}
    assert pairs == {(SINNER, TEAMMATE)}
    facts_b = await facts_visible_in_subdomain(session, B)
    assert facts_b == []


@pytest.mark.asyncio
async def test_physical_leaf_facts_are_not_duplicated_at_parent():
    session = GraphSession(_placement_graph())
    assert await count_physical_leaf_facts(session) == 2
    assert await engine_count_physical_leaf_facts(session) == 2
    visible = await facts_visible_in_subdomain(session, P)
    assert len(visible) == 2
    writes = [cypher for cypher, _kw in session.calls if "CREATE" in cypher or "MERGE" in cypher]
    assert writes == []


@pytest.mark.asyncio
async def test_co_presence_siblings_are_not_witnessed_neighbors():
    session = GraphSession(_placement_graph())
    neighbors = await witnessed_neighbors(session, LONELY)
    assert neighbors == []
    sinner_neighbors = await engine_witnessed_neighbors(session, SINNER)
    assert LONELY not in sinner_neighbors
    assert set(sinner_neighbors) == {TEAMMATE, COACH}


@pytest.mark.asyncio
async def test_reify_shared_situation_creates_evento_and_r5_not_context_edge():
    graph = _placement_graph()
    session = GraphSession(graph)
    before = await count_physical_leaf_facts(session)
    event_id = await reify_shared_situation(
        session,
        participant_node_ids=[SINNER, LONELY],
        situation_name="Press conference",
    )
    event = graph.nodes[event_id]
    assert event["type"] == "event"
    assert event["kernel_category"] == EntityKernelType.Evento.value
    assert event["kernel_category"] != "E4"

    participates = [
        rel
        for rel in graph.relations
        if rel.get("normalized_relation") == SITUATION_NORMALIZED_RELATION
    ]
    assert len(participates) == 2
    assert {rel["to_id"] for rel in participates} == {SINNER, LONELY}
    assert all(rel["from_id"] == event_id for rel in participates)
    assert all(rel["relation"] == SITUATION_PARTICIPATES_RELATION for rel in participates)
    assert all(
        rel["kernel_parent"] == RelationKernelType.Partecipativa.value for rel in participates
    )

    context_edges = [
        rel
        for rel in graph.relations
        if {rel["from_id"], rel["to_id"]} == {SINNER, LONELY}
        and rel["from_id"] in graph.nodes
        and rel["to_id"] in graph.nodes
        and graph.nodes[rel["from_id"]]["type"] != "event"
        and graph.nodes[rel["to_id"]]["type"] != "event"
    ]
    assert context_edges == []
    assert await witnessed_neighbors(session, LONELY) == [event_id]
    assert LONELY not in await witnessed_neighbors(session, SINNER)
    assert await count_physical_leaf_facts(session) == before + 2

    reused = await reify_shared_situation(
        session,
        participant_node_ids=[SINNER, LONELY],
        situation_name="Press conference",
    )
    assert reused == event_id
    events = [n for n in graph.nodes.values() if n.get("type") == "event"]
    assert len(events) == 1
    assert sum(
        1
        for rel in graph.relations
        if rel.get("normalized_relation") == SITUATION_NORMALIZED_RELATION
    ) == 2

    create_kwargs = [kw for cypher, kw in session.calls if cypher == CREATE_SITUATION_EVENT_CYPHER]
    assert len(create_kwargs) == 1
    assert create_kwargs[0]["kernel_category"] == "Evento"
    assert not any(
        "context" in (kw.get("relation") or "").casefold()
        or "co-occurrence" in (kw.get("relation") or "").casefold()
        or "cooccurrence" in (kw.get("relation") or "").casefold()
        for _cypher, kw in session.calls
    )
    assert LINK_SITUATION_CHUNK_CYPHER not in {cypher for cypher, _ in session.calls}
