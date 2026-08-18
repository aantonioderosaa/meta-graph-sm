"""Fase 14.3: six Doc1 §13 kernel stress cases. FakeSession, no Docker."""

from __future__ import annotations

import inspect
import math
import re

import pytest

from app.models.kernel import EntityKernelType, RelationKernelType, SpecialRelationType
from app.models.query import NodeSubgraph, NodeSubgraphRelationship
from app.pipeline.identity_resolution import (
    SAME_AS,
    UNLINK_FACET_CYPHER,
    ensure_identity_node,
    identity_uri_from_facet_ids,
    link_as_facet,
    unlink_facet,
)
from app.pipeline.ingestion import write_contradicts, write_node_relation
from app.pipeline.judge import run_judge
from app.pipeline.node_query_engine import (
    derive_candidate_links,
    infer_kernel_categories,
    label_query_citations,
)
from app.pipeline.promote import is_skipped_relation
from tests.test_acceptance_identity_facets import IdentityGraph
from tests.test_acceptance_judge import JudgeGraph
from tests.test_acceptance_s0_s1_s2 import GraphSession, _seed_player_coach

JOB_ID = "job-f14-stress"
_RELATION_WRITE_RE = re.compile(
    r"\b(?:CREATE|MERGE)\b[\s\S]{0,240}:Relation\b",
    re.IGNORECASE,
)
_ENTITY_VALUES = {m.value for m in EntityKernelType}
_RELATION_VALUES = {m.value for m in RelationKernelType}
_SPECIAL_VALUES = {m.value for m in SpecialRelationType}


def _vec_at_cosine(target: float) -> list[float]:
    y = math.sqrt(max(0.0, 1.0 - target * target))
    return [target, y]


def _assert_categories_closed(values: list[str | None]) -> None:
    for raw in values:
        if not raw:
            continue
        assert raw in _ENTITY_VALUES, raw


@pytest.fixture
def embed_stub(monkeypatch):
    monkeypatch.setattr("app.pipeline.ingestion.embeddings.embed", lambda _t: [0.1] * 8)


@pytest.mark.asyncio
async def test_cross_category_entity_uses_two_facets_not_one_node(monkeypatch):
    """§13.1 Ditta individuale: Agente + CostruttoSociale as two facets, not one node."""
    monkeypatch.setattr(
        "app.pipeline.identity_resolution.settings.ENABLE_FACET_IDENTITY",
        True,
    )
    graph = IdentityGraph()
    graph.add_node(
        "mario-persona",
        name="Mario Rossi",
        kernel_category=EntityKernelType.Agente.value,
        type="entity",
    )
    graph.add_node(
        "rossi-snc",
        name="Rossi Snc",
        kernel_category=EntityKernelType.CostruttoSociale.value,
        type="entity",
    )
    uri = identity_uri_from_facet_ids(["mario-persona", "rossi-snc"])
    await ensure_identity_node(graph, uri=uri, canonical_summary="Ditta individuale")
    await link_as_facet(graph, uri, "mario-persona")
    await link_as_facet(graph, uri, "rossi-snc")

    cats = {
        graph.nodes["mario-persona"]["kernel_category"],
        graph.nodes["rossi-snc"]["kernel_category"],
    }
    assert cats == {
        EntityKernelType.Agente.value,
        EntityKernelType.CostruttoSociale.value,
    }
    assert not any(
        len({graph.nodes[nid].get("kernel_category")}) > 1
        for nid in ("mario-persona", "rossi-snc")
    )
    combined = [
        n
        for n in graph.nodes.values()
        if n.get("kernel_category")
        not in {EntityKernelType.Agente.value, EntityKernelType.CostruttoSociale.value}
        and n.get("kernel_category") not in _ENTITY_VALUES
    ]
    assert combined == []
    assert len(EntityKernelType) == 8
    _assert_categories_closed([n.get("kernel_category") for n in graph.nodes.values()])

    persona_hits = [
        n
        for n in graph.nodes.values()
        if n.get("kernel_category") in infer_kernel_categories("quale Persona è titolare?")
    ]
    org_hits = [
        n
        for n in graph.nodes.values()
        if n.get("kernel_category")
        in infer_kernel_categories("quale Organizzazione è la ditta?")
    ]
    assert {n["id"] for n in persona_hits} == {"mario-persona"}
    assert {n["id"] for n in org_hits} == {"rossi-snc"}

    node_ids_before = set(graph.nodes)
    await unlink_facet(graph, uri, "mario-persona")
    assert set(graph.nodes) == node_ids_before
    assert "mario-persona" in graph.nodes
    assert "rossi-snc" in graph.nodes
    compact = " ".join(UNLINK_FACET_CYPHER.split())
    assert "DELETE r" in compact
    assert "DELETE facet" not in compact
    assert "DETACH DELETE" not in UNLINK_FACET_CYPHER


@pytest.mark.asyncio
async def test_horizontal_sibling_refinement_stays_under_r1_r6(embed_stub):
    """§13.2 coached_by is a vertical refinement, never a 7th RelationKernelType."""
    assert len(RelationKernelType) == 6
    with pytest.raises(ValueError):
        RelationKernelType("coached_by")
    with pytest.raises(ValueError):
        RelationKernelType("HorizontalSibling")

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
    assert session.graph.relations[0]["kernel_parent"] in _RELATION_VALUES
    assert session.graph.relations[0]["kernel_parent"] == "Partecipativa"
    assert session.graph.relations[0]["relation"] == "coached_by"
    assert len(RelationKernelType) == 6
    _assert_categories_closed(
        [n.get("kernel_category") for n in session.graph.nodes.values()]
    )


@pytest.mark.asyncio
async def test_equivalent_refinements_from_sister_domains_collapse():
    """§13.3 sister-domain refinements collapse via EQUIVALENT_TO + absorbed_from."""
    graph = JudgeGraph()
    emb_a = [1.0, 0.0]
    emb_b = _vec_at_cosine(0.95)
    graph.add_concept(
        "calciatore",
        promoted=True,
        kernel_category=EntityKernelType.Agente.value,
        parent_uri="kernel-agente",
        embedding=emb_a,
        name="calciatore",
    )
    graph.add_concept(
        "giocatore",
        promoted=True,
        kernel_category=EntityKernelType.Agente.value,
        parent_uri="kernel-agente",
        embedding=emb_b,
        name="giocatore",
    )
    graph.add_node("n-mario", name="Mario", kernel_category=EntityKernelType.Agente.value)
    graph.set_member_of("n-mario", "giocatore")

    stats = await run_judge(graph, JOB_ID)

    assert stats.equivalent_to >= 1
    assert graph._has_famiglia("calciatore", "giocatore", "EQUIVALENT_TO")
    assert "giocatore" in graph.concepts
    home = graph.member_of["n-mario"]
    assert home["concept_id"] == "calciatore"
    assert home["absorbed_from"] == "giocatore"
    assert graph.concepts["giocatore"]["absorbed_from"] == "calciatore"
    assert JOB_ID in graph.judge_runs
    assert len(RelationKernelType) == 6
    _assert_categories_closed(
        [c.get("kernel_category") for c in graph.concepts.values()]
        + [n.get("kernel_category") for n in graph.nodes.values()]
    )


@pytest.mark.asyncio
async def test_cross_domain_referent_keeps_contradictory_relations(monkeypatch):
    """§13.4 identity facets keep both apparently conflicting facts; SAME_AS moves no edges."""
    monkeypatch.setattr(
        "app.pipeline.identity_resolution.settings.ENABLE_FACET_IDENTITY",
        True,
    )
    graph = IdentityGraph()
    graph.add_node(
        "weah-calcio",
        name="Weah",
        kernel_category=EntityKernelType.Agente.value,
        type="entity",
    )
    graph.add_node(
        "weah-presidente",
        name="Weah",
        kernel_category=EntityKernelType.CostruttoSociale.value,
        type="entity",
    )
    graph.add_node(
        "club-x",
        name="Club X",
        kernel_category=EntityKernelType.CostruttoSociale.value,
        type="entity",
    )
    graph.add_node(
        "liberia",
        name="Liberia",
        kernel_category=EntityKernelType.CostruttoSociale.value,
        type="entity",
    )
    graph.add_edge(
        "Node",
        "weah-calcio",
        "Relation",
        "Node",
        "club-x",
        relation="plays_for",
        kernel_parent="SocialeIntenzionale",
    )
    graph.add_edge(
        "Node",
        "weah-presidente",
        "Relation",
        "Node",
        "liberia",
        relation="president_of",
        kernel_parent="SocialeIntenzionale",
    )
    before = [
        e
        for e in graph.edges
        if e["rel_type"] in {"Relation", "HAS_CONCEPT", "DERIVED_FROM"}
    ]
    uri = identity_uri_from_facet_ids(["weah-calcio", "weah-presidente"])
    await ensure_identity_node(graph, uri=uri, canonical_summary="George Weah")
    await link_as_facet(graph, uri, "weah-calcio")
    await link_as_facet(graph, uri, "weah-presidente")

    after_facts = [
        e
        for e in graph.edges
        if e["rel_type"] in {"Relation", "HAS_CONCEPT", "DERIVED_FROM"}
    ]
    assert after_facts == before
    assert graph.nodes["weah-calcio"].get("merged_into") is None
    assert graph.nodes["weah-presidente"].get("merged_into") is None
    rels = [e for e in graph.edges if e["rel_type"] == "Relation"]
    assert len(rels) == 2
    assert {e["src_id"] for e in rels} == {"weah-calcio", "weah-presidente"}
    assert any(
        e["src_id"] == "weah-calcio" and e["rel_type"] == SAME_AS for e in graph.edges
    )
    _assert_categories_closed([n.get("kernel_category") for n in graph.nodes.values()])


@pytest.mark.asyncio
async def test_ingest_contradiction_keeps_both_facts(embed_stub):
    """§13.5 both latest facts kept + CONTRADICTS; PROMOTE never retypes Famiglia B."""
    graph = JudgeGraph()
    graph.add_node("mario", name="Mario", kernel_category=EntityKernelType.Agente.value)
    graph.add_node(
        "y2010", name="2010", kernel_category=EntityKernelType.EntitaTemporale.value
    )
    graph.add_node(
        "y2011", name="2011", kernel_category=EntityKernelType.EntitaTemporale.value
    )
    graph.add_relation(
        "mario",
        "y2010",
        relation="Fonte A: ha vinto il torneo nel 2010",
        kernel_parent=RelationKernelType.Partecipativa.value,
        is_latest=True,
    )
    graph.add_relation(
        "mario",
        "y2011",
        relation="Fonte B: ha vinto il torneo nel 2011",
        kernel_parent=RelationKernelType.Partecipativa.value,
        is_latest=True,
    )

    stats = await run_judge(graph, JOB_ID)

    assert stats.missed_contradictions >= 1
    assert graph._has_famiglia("y2010", "y2011", "CONTRADICTS")
    latest = [rel for rel in graph.relations if rel.get("is_latest", True)]
    assert len(latest) == 2
    assert {graph.relations[0]["dst"], graph.relations[1]["dst"]} == {"y2010", "y2011"}
    assert is_skipped_relation("contradicts", SpecialRelationType.contradicts.value)
    assert is_skipped_relation("CONTRADICTS", "CONTRADICTS")
    assert not is_skipped_relation("coached_by", RelationKernelType.Partecipativa.value)
    _assert_categories_closed([n.get("kernel_category") for n in graph.nodes.values()])
    for rel in graph.relations:
        assert rel.get("kernel_parent") in _RELATION_VALUES

    session = GraphSession()
    session.graph.nodes["h"] = {"id": "h", "kernel_category": "Agente"}
    session.graph.nodes["t1"] = {"id": "t1", "kernel_category": "EntitaTemporale"}
    session.graph.nodes["t2"] = {"id": "t2", "kernel_category": "EntitaTemporale"}
    await write_node_relation(
        session,
        head_id="h",
        tail_id="t1",
        relation="won_2010",
        normalized_relation="won",
        kernel_parent=RelationKernelType.Partecipativa,
        witness_source="A",
        witness_target="2010",
    )
    await write_node_relation(
        session,
        head_id="h",
        tail_id="t2",
        relation="won_2011",
        normalized_relation="won",
        kernel_parent=RelationKernelType.Partecipativa,
        witness_source="B",
        witness_target="2011",
    )
    await write_contradicts(
        session,
        left_id="t1",
        right_id="t2",
        subject_id="h",
        relation="won",
        kernel_parent=RelationKernelType.Partecipativa.value,
    )
    assert len(session.graph.relations) == 2
    assert all(rel.get("lifted_from") is None for rel in session.graph.relations)


@pytest.mark.asyncio
async def test_unattested_cross_domain_link_stays_derived_in_memory(embed_stub):
    """§13.6 unattested hop is in-memory DerivedLink; Cypher never CREATE/MERGE :Relation."""
    session = GraphSession()
    _seed_player_coach(session.graph)
    writes_before = session.relation_writes
    links = await derive_candidate_links(session, source_id="alice", target_id="x")
    assert links == []
    assert session.relation_writes == writes_before
    for cypher, _kw in session.calls:
        assert _RELATION_WRITE_RE.search(cypher) is None

    session.graph.nodes["mid"] = {"id": "mid", "kernel_category": "Agente"}
    session.graph.member_of["mid"] = "giocatore"
    await write_node_relation(
        session,
        head_id="alice",
        tail_id="mid",
        relation="teammate",
        normalized_relation=None,
        kernel_parent=RelationKernelType.SocialeIntenzionale,
        witness_source="Alice",
        witness_target="Mid",
    )
    await write_node_relation(
        session,
        head_id="mid",
        tail_id="x",
        relation="coached_by",
        normalized_relation=None,
        kernel_parent=RelationKernelType.Partecipativa,
        witness_source="Mid",
        witness_target="X",
    )
    rel_count = len(session.graph.relations)
    writes_after_facts = session.relation_writes
    derive_from = len(session.calls)

    links = await derive_candidate_links(session, source_id="alice", target_id="x")
    assert len(links) == 1
    assert links[0].derivation_chain
    assert links[0].relation_type == "coached_by"
    assert len(session.graph.relations) == rel_count
    assert session.relation_writes == writes_after_facts
    for cypher, _kw in session.calls[derive_from:]:
        assert _RELATION_WRITE_RE.search(cypher) is None
        assert "CREATE" not in cypher
        assert "MERGE" not in cypher

    source = inspect.getsource(derive_candidate_links)
    assert _RELATION_WRITE_RE.search(source) is None

    subgraph = NodeSubgraph(
        nodes=[],
        relationships=[
            NodeSubgraphRelationship(source="alice", target="mid", type="teammate"),
            NodeSubgraphRelationship(source="mid", target="x", type="coached_by"),
        ],
    )
    citations = label_query_citations(
        cited_node_ids=["alice", "x"],
        subgraph=subgraph,
        derived_links=links,
        context_ids=["alice", "x"],
    )
    derived = [c for c in citations if c.epistemic_status == "derived"]
    assert derived
    assert derived[0].derivation_chain
    _assert_categories_closed(
        [n.get("kernel_category") for n in session.graph.nodes.values()]
    )
    for rel in session.graph.relations:
        assert rel.get("kernel_parent") in _RELATION_VALUES
    assert _SPECIAL_VALUES == {m.value for m in SpecialRelationType}
