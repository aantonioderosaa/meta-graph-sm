"""Unit tests for ConnectivityRule deposit (F7.3–F7.5). No Docker."""

from __future__ import annotations

import pytest

from app.models.kernel import EntityKernelType, RelationKernelType, SpecialRelationType
from app.pipeline.concepts import kernel_catch_all_concept_id
from app.pipeline.connectivity_rules import (
    MERGE_CONNECTIVITY_RULE_CYPHER,
    READ_CONCEPT_ANCESTORS_CYPHER,
    READ_NODE_TYPE_TOKEN_CYPHER,
    deposit_from_asserted_fact,
    is_structural_relation_type,
    type_token_from_row,
)
from app.pipeline.ingestion import CREATE_NODE_RELATION_CYPHER, write_node_relation
from tests.test_acceptance_s0_s1_s2 import GraphSession, _seed_player_coach


@pytest.fixture
def embed_stub(monkeypatch):
    monkeypatch.setattr("app.pipeline.ingestion.embeddings.embed", lambda _t: [0.1] * 8)


def test_structural_relation_types_skipped():
    assert is_structural_relation_type("contradicts")
    assert is_structural_relation_type(SpecialRelationType.same_as.value)
    assert is_structural_relation_type("IS_A")
    assert is_structural_relation_type("member_of")
    assert is_structural_relation_type("HAS_CONCEPT")
    assert not is_structural_relation_type("coached_by")
    assert not is_structural_relation_type("plays_for")


def test_type_token_prefers_member_of_id():
    assert (
        type_token_from_row(
            {"concept_id": "giocatore", "concept_name": "Giocatore", "kernel_category": "Agente"}
        )
        == "giocatore"
    )
    assert type_token_from_row({"kernel_category": "Agente"}) == "Agente"
    assert type_token_from_row({}) is None


@pytest.mark.asyncio
async def test_famiglia_b_does_not_deposit(embed_stub):
    session = GraphSession()
    _seed_player_coach(session.graph)

    await deposit_from_asserted_fact(
        session,
        head_id="alice",
        tail_id="x",
        relation_type="contradicts",
        origin_id="o1",
    )
    assert session.graph.rules == {}
    assert not any(cypher == MERGE_CONNECTIVITY_RULE_CYPHER for cypher, _ in session.calls)


@pytest.mark.asyncio
async def test_kernel_category_only_skips_generalization(embed_stub, monkeypatch):
    monkeypatch.setattr(
        "app.pipeline.connectivity_rules.settings.CONNECTIVITY_MAX_GENERALIZATION_HOPS",
        1,
    )
    session = GraphSession()
    session.graph.nodes["a"] = {"id": "a", "kernel_category": "Agente"}
    session.graph.nodes["b"] = {"id": "b", "kernel_category": "Agente"}

    await write_node_relation(
        session,
        head_id="a",
        tail_id="b",
        relation="knows",
        normalized_relation=None,
        kernel_parent=RelationKernelType.SocialeIntenzionale,
    )

    assert ("Agente", "knows", "Agente") in session.graph.rules
    assert session.graph.rules[("Agente", "knows", "Agente")]["generalization_level"] == 0
    kernel = kernel_catch_all_concept_id(EntityKernelType.Agente)
    assert (kernel, "knows", kernel) not in session.graph.rules
    assert not any(cypher == READ_CONCEPT_ANCESTORS_CYPHER for cypher, _ in session.calls)


@pytest.mark.asyncio
async def test_deposit_reads_member_of_then_merges(embed_stub):
    session = GraphSession()
    _seed_player_coach(session.graph)

    await deposit_from_asserted_fact(
        session,
        head_id="alice",
        tail_id="x",
        relation_type="coached_by",
        origin_id="fact-1",
    )

    assert any(cypher == READ_NODE_TYPE_TOKEN_CYPHER for cypher, _ in session.calls)
    assert ("giocatore", "coached_by", "coach") in session.graph.rules
    rule = session.graph.rules[("giocatore", "coached_by", "coach")]
    assert rule["origin_fact_ids"] == ["fact-1"]
    assert rule["generalization_level"] == 0


@pytest.mark.asyncio
async def test_write_still_uses_create_relation_cypher(embed_stub):
    session = GraphSession()
    _seed_player_coach(session.graph)
    await write_node_relation(
        session,
        head_id="alice",
        tail_id="x",
        relation="coached_by",
        normalized_relation=None,
        kernel_parent=RelationKernelType.Partecipativa,
    )
    assert session.calls[0][0] == CREATE_NODE_RELATION_CYPHER
    assert "CREATE (h)-[:Relation" in CREATE_NODE_RELATION_CYPHER
