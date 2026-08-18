"""Fase 7 acceptance: S0 kernel_parent, S1 ConnectivityRule, S2 derive wall."""

from __future__ import annotations

import re

import pytest

from app.core.config import Settings
from app.models.kernel import EntityKernelType, RelationKernelType
from app.pipeline.concepts import kernel_catch_all_concept_id
from app.pipeline.connectivity_rules import (
    MERGE_CONNECTIVITY_RULE_CYPHER,
    READ_CONCEPT_ANCESTORS_CYPHER,
    READ_NODE_TYPE_TOKEN_CYPHER,
    kernel_catch_all_ids,
)
from app.pipeline.ingestion import CREATE_NODE_RELATION_CYPHER, write_node_relation
from app.pipeline.node_query_engine import (
    LOAD_CONNECTIVITY_RULES_CYPHER,
    LOAD_ISA_EDGES_CYPHER,
    LOAD_LEAF_S0_RELATIONS_CYPHER,
    LOAD_NODE_TYPE_TOKENS_CYPHER,
    DerivedLink,
    derive_candidate_links,
)

_RELATION_WRITE_RE = re.compile(
    r"\b(?:CREATE|MERGE)\b[\s\S]{0,240}:Relation\b",
    re.IGNORECASE,
)


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


class LayerGraph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.concepts: dict[str, dict] = {}
        self.member_of: dict[str, str] = {}
        self.isa: dict[str, str] = {}
        self.relations: list[dict] = []
        self.rules: dict[tuple[str, str, str], dict] = {}


def _ancestors(graph: LayerGraph, concept_id: str) -> list[dict]:
    rows: list[dict] = []
    current = concept_id
    seen: set[str] = set()
    hops = 0
    while current in graph.isa and current not in seen:
        seen.add(current)
        current = graph.isa[current]
        hops += 1
        concept = graph.concepts.get(current, {})
        rows.append({"id": current, "name": concept.get("name"), "hops": hops})
    return rows


def _apply(graph: LayerGraph, cypher: str, kwargs: dict) -> list[dict]:
    if cypher == CREATE_NODE_RELATION_CYPHER:
        graph.relations.append(
            {
                "src_id": kwargs["head_id"],
                "tgt_id": kwargs["tail_id"],
                "relation": kwargs.get("relation"),
                "kernel_parent": kwargs.get("kernel_parent"),
                "normalized_relation": kwargs.get("normalized_relation"),
                "lifted_from": None,
            }
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
        return _ancestors(graph, kwargs["concept_id"])
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
            if rel["src_id"] not in graph.nodes or rel["tgt_id"] not in graph.nodes:
                continue
            rows.append(
                {
                    "src_id": rel["src_id"],
                    "tgt_id": rel["tgt_id"],
                    "relation": rel.get("relation"),
                    "kernel_parent": rel.get("kernel_parent"),
                    "normalized_relation": rel.get("normalized_relation"),
                }
            )
        return rows
    if cypher == LOAD_NODE_TYPE_TOKENS_CYPHER:
        rows = []
        for nid, node in graph.nodes.items():
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
            {"child_id": child, "parent_id": parent}
            for child, parent in graph.isa.items()
        ]
    return []


class GraphSession:
    def __init__(self, graph: LayerGraph | None = None) -> None:
        self.graph = graph or LayerGraph()
        self.calls: list[tuple[str, dict]] = []
        self.relation_writes = 0

    async def run(self, cypher, **kwargs):
        self.calls.append((cypher, kwargs))
        if _RELATION_WRITE_RE.search(cypher or ""):
            self.relation_writes += 1
        return FakeResult(_apply(self.graph, cypher, kwargs))


def _seed_player_coach(graph: LayerGraph) -> str:
    kernel = kernel_catch_all_concept_id(EntityKernelType.Agente)
    graph.concepts["giocatore"] = {"id": "giocatore", "name": "Giocatore"}
    graph.concepts["coach"] = {"id": "coach", "name": "Coach"}
    graph.concepts["persona"] = {"id": "persona", "name": "Persona"}
    graph.concepts[kernel] = {"id": kernel, "name": "Agente"}
    graph.isa["giocatore"] = "persona"
    graph.isa["coach"] = "persona"
    graph.isa["persona"] = kernel
    graph.nodes["alice"] = {"id": "alice", "kernel_category": "Agente"}
    graph.nodes["x"] = {"id": "x", "kernel_category": "Agente"}
    graph.member_of["alice"] = "giocatore"
    graph.member_of["x"] = "coach"
    return kernel


@pytest.fixture
def embed_stub(monkeypatch):
    monkeypatch.setattr("app.pipeline.ingestion.embeddings.embed", lambda _t: [0.1] * 8)


@pytest.mark.asyncio
async def test_f71_asserted_write_without_kernel_parent_is_not_created(embed_stub):
    session = GraphSession()
    session.graph.nodes["h"] = {"id": "h", "kernel_category": "Agente"}
    session.graph.nodes["t"] = {"id": "t", "kernel_category": "Agente"}

    await write_node_relation(
        session,
        head_id="h",
        tail_id="t",
        relation="coached_by",
        normalized_relation=None,
        head_name="H",
        tail_name="T",
    )

    assert session.graph.relations == []
    assert not any(cypher == CREATE_NODE_RELATION_CYPHER for cypher, _ in session.calls)
    assert session.graph.rules == {}


@pytest.mark.asyncio
async def test_f71_asserted_write_with_kernel_parent_creates(embed_stub):
    session = GraphSession()
    _seed_player_coach(session.graph)

    await write_node_relation(
        session,
        head_id="alice",
        tail_id="x",
        relation="coached_by",
        normalized_relation=None,
        head_name="Alice",
        tail_name="X",
        kernel_parent=RelationKernelType.Partecipativa,
        witness_source="Alice",
        witness_target="X",
    )

    assert len(session.graph.relations) == 1
    assert session.graph.relations[0]["kernel_parent"] == "Partecipativa"
    assert any(cypher == CREATE_NODE_RELATION_CYPHER for cypher, _ in session.calls)


@pytest.mark.asyncio
async def test_f75_second_fact_merges_rule_and_grows_origins(embed_stub):
    session = GraphSession()
    _seed_player_coach(session.graph)
    session.graph.nodes["bob"] = {"id": "bob", "kernel_category": "Agente"}
    session.graph.nodes["y"] = {"id": "y", "kernel_category": "Agente"}
    session.graph.member_of["bob"] = "giocatore"
    session.graph.member_of["y"] = "coach"

    await write_node_relation(
        session,
        head_id="alice",
        tail_id="x",
        relation="coached_by",
        normalized_relation=None,
        kernel_parent=RelationKernelType.Partecipativa,
    )
    exact_after_first = [
        key for key in session.graph.rules if key == ("giocatore", "coached_by", "coach")
    ]
    assert len(exact_after_first) == 1
    first_origins = list(session.graph.rules[exact_after_first[0]]["origin_fact_ids"])

    await write_node_relation(
        session,
        head_id="bob",
        tail_id="y",
        relation="coached_by",
        normalized_relation=None,
        kernel_parent=RelationKernelType.Partecipativa,
    )

    exact = ("giocatore", "coached_by", "coach")
    assert exact in session.graph.rules
    assert len([k for k in session.graph.rules if k == exact]) == 1
    origins = session.graph.rules[exact]["origin_fact_ids"]
    assert len(origins) == 2
    assert origins[0] == first_origins[0]
    assert "bob|coached_by|y" in origins


@pytest.mark.asyncio
async def test_f74_hop1_parent_rule_and_kernel_catchall_skipped(embed_stub, monkeypatch):
    monkeypatch.setattr(
        "app.pipeline.connectivity_rules.settings.CONNECTIVITY_MAX_GENERALIZATION_HOPS",
        1,
    )
    session = GraphSession()
    kernel = _seed_player_coach(session.graph)

    await write_node_relation(
        session,
        head_id="alice",
        tail_id="x",
        relation="coached_by",
        normalized_relation=None,
        kernel_parent=RelationKernelType.Partecipativa,
    )

    assert ("giocatore", "coached_by", "coach") in session.graph.rules
    assert session.graph.rules[("giocatore", "coached_by", "coach")][
        "generalization_level"
    ] == 0
    assert ("persona", "coached_by", "persona") in session.graph.rules
    assert session.graph.rules[("persona", "coached_by", "persona")][
        "generalization_level"
    ] == 1
    assert (kernel, "coached_by", kernel) not in session.graph.rules
    for src, _rel, tgt in session.graph.rules:
        assert src not in kernel_catch_all_ids()
        assert tgt not in kernel_catch_all_ids()

    session2 = GraphSession()
    kernel = kernel_catch_all_concept_id(EntityKernelType.Agente)
    session2.graph.concepts["persona"] = {"id": "persona", "name": "Persona"}
    session2.graph.concepts[kernel] = {"id": kernel, "name": "Agente"}
    session2.graph.isa["persona"] = kernel
    session2.graph.nodes["p1"] = {"id": "p1", "kernel_category": "Agente"}
    session2.graph.nodes["p2"] = {"id": "p2", "kernel_category": "Agente"}
    session2.graph.member_of["p1"] = "persona"
    session2.graph.member_of["p2"] = "persona"

    await write_node_relation(
        session2,
        head_id="p1",
        tail_id="p2",
        relation="knows",
        normalized_relation=None,
        kernel_parent=RelationKernelType.SocialeIntenzionale,
    )
    assert ("persona", "knows", "persona") in session2.graph.rules
    assert (kernel, "knows", kernel) not in session2.graph.rules


@pytest.mark.asyncio
async def test_f76_derived_link_with_descent_is_not_persisted(embed_stub):
    session = GraphSession()
    _seed_player_coach(session.graph)
    session.graph.nodes["mid"] = {"id": "mid", "kernel_category": "Agente"}
    session.graph.member_of["mid"] = "giocatore"

    await write_node_relation(
        session,
        head_id="alice",
        tail_id="mid",
        relation="teammate",
        normalized_relation=None,
        kernel_parent=RelationKernelType.SocialeIntenzionale,
    )
    await write_node_relation(
        session,
        head_id="mid",
        tail_id="x",
        relation="coached_by",
        normalized_relation=None,
        kernel_parent=RelationKernelType.Partecipativa,
    )
    rel_count = len(session.graph.relations)
    assert not any(
        rel["src_id"] == "alice" and rel["tgt_id"] == "x" for rel in session.graph.relations
    )

    links = await derive_candidate_links(session, source_id="alice", target_id="x")

    assert len(links) == 1
    link = links[0]
    assert isinstance(link, DerivedLink)
    assert link.source_id == "alice"
    assert link.target_id == "x"
    assert link.relation_type == "coached_by"
    assert link.confidence == 1.0
    kinds = [step.kind for step in link.derivation_chain]
    assert kinds[0] == "s0"
    assert kinds[-1] == "s1"
    assert "coached_by" in link.derivation_chain[-1].detail
    assert len(session.graph.relations) == rel_count


@pytest.mark.asyncio
async def test_f77_overly_general_rule_without_s0_path_is_rejected():
    session = GraphSession()
    session.graph.nodes["bob"] = {"id": "bob", "kernel_category": "Agente"}
    session.graph.nodes["wally"] = {"id": "wally", "kernel_category": "Agente"}
    session.graph.rules[("Agente", "knows", "Agente")] = {
        "source_category": "Agente",
        "relation_type": "knows",
        "target_category": "Agente",
        "origin_fact_ids": ["seed"],
        "generalization_level": 2,
    }
    rel_count = len(session.graph.relations)

    links = await derive_candidate_links(session, source_id="bob", target_id="wally")

    assert links == []
    assert len(session.graph.relations) == rel_count


@pytest.mark.asyncio
async def test_f78_n_derive_calls_do_not_increase_relation_count(embed_stub):
    session = GraphSession()
    _seed_player_coach(session.graph)
    session.graph.nodes["mid"] = {"id": "mid", "kernel_category": "Agente"}
    session.graph.member_of["mid"] = "giocatore"
    await write_node_relation(
        session,
        head_id="alice",
        tail_id="mid",
        relation="teammate",
        normalized_relation=None,
        kernel_parent=RelationKernelType.SocialeIntenzionale,
    )
    await write_node_relation(
        session,
        head_id="mid",
        tail_id="x",
        relation="coached_by",
        normalized_relation=None,
        kernel_parent=RelationKernelType.Partecipativa,
    )
    before = len(session.graph.relations)
    writes_before = session.relation_writes

    for _ in range(3):
        links = await derive_candidate_links(session, source_id="alice", target_id="x")
        assert links
        assert links[0].derivation_chain

    assert len(session.graph.relations) == before
    assert session.relation_writes == writes_before


def test_connectivity_hops_setting_default():
    assert Settings.model_fields["CONNECTIVITY_MAX_GENERALIZATION_HOPS"].default == 1


def test_derive_cypher_never_writes_relation():
    from app.pipeline import node_query_engine as nqe

    for cypher in (
        nqe.LOAD_CONNECTIVITY_RULES_CYPHER,
        nqe.LOAD_LEAF_S0_RELATIONS_CYPHER,
        nqe.LOAD_NODE_TYPE_TOKENS_CYPHER,
        nqe.LOAD_ISA_EDGES_CYPHER,
    ):
        assert _RELATION_WRITE_RE.search(cypher) is None
        assert "CREATE" not in cypher
        assert "MERGE" not in cypher
