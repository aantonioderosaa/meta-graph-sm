"""Node-query engine integration (Macrotask 3). Requires Docker / GDS."""

from __future__ import annotations

import math

import pytest

from app.core import neo4j_client
from app.db.schema import apply_schema_with_driver
from app.pipeline import node_ppr_projection as npp
from app.pipeline import node_query_engine as nqe
from app.pipeline.node_ppr_projection import PPR_GRAPH_NAME, ensure_ppr_projection
from app.pipeline.node_query_engine import NodeQueryAnswer, run_node_query
from tests.neo4j_gds import neo4j_gds_container, wait_for_gds

EMBEDDING_DIM = 768
ALICE = "alice"
BOB = "bob"
MEETING = "meeting"
MERGED = "alice-dup"
MARIO = "mario"
ACME = "acme"
FACT_ID = "fact-should-never-appear"
TECH = "tech"


@pytest.fixture(scope="module")
def neo4j_container():
    container = neo4j_gds_container()
    container.start()
    try:
        yield container
    finally:
        container.stop()


@pytest.fixture
async def neo4j_ready(neo4j_container, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.NEO4J_URI", neo4j_container.get_connection_url())
    monkeypatch.setattr("app.core.config.settings.NEO4J_USER", neo4j_container.username)
    monkeypatch.setattr("app.core.config.settings.NEO4J_PASSWORD", neo4j_container.password)
    monkeypatch.setattr("app.core.config.settings.AUTO_MIGRATE", False)

    driver = neo4j_container.get_driver()
    wait_for_gds(driver)
    apply_schema_with_driver(driver)

    await neo4j_client.close_neo4j_driver()
    await neo4j_client.init_neo4j_driver()

    async_driver = neo4j_client.get_driver()
    async with async_driver.session() as session:
        await session.run("MATCH (n) DETACH DELETE n")
        exists = await session.run(
            "CALL gds.graph.exists($name) YIELD exists RETURN exists",
            name=PPR_GRAPH_NAME,
        )
        record = await exists.single()
        if record is not None and record["exists"]:
            await session.run("CALL gds.graph.drop($name, false)", name=PPR_GRAPH_NAME)

    yield neo4j_container

    async with async_driver.session() as session:
        exists = await session.run(
            "CALL gds.graph.exists($name) YIELD exists RETURN exists",
            name=PPR_GRAPH_NAME,
        )
        record = await exists.single()
        if record is not None and record["exists"]:
            await session.run("CALL gds.graph.drop($name, false)", name=PPR_GRAPH_NAME)
        await session.run("MATCH (n) DETACH DELETE n")
    await neo4j_client.close_neo4j_driver()


def _unit_vector(index: int, dim: int = EMBEDDING_DIM) -> list[float]:
    vector = [0.0] * dim
    vector[index % dim] = 1.0
    return vector


def _similar_vector(base: list[float], epsilon: float = 0.01) -> list[float]:
    vec = list(base)
    vec[0] = max(0.0, vec[0] - epsilon)
    norm = math.sqrt(sum(value * value for value in vec))
    return [value / norm for value in vec]


ALICE_VEC = _unit_vector(0)
BOB_VEC = _unit_vector(1)
MEETING_VEC = _unit_vector(2)
FOUND_VEC = _unit_vector(5)
TECH_VEC = _unit_vector(7)
FAR_VEC = _unit_vector(20)


async def _await_indexes() -> None:
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        for name in (
            "node_embedding",
            "concept_embedding",
            "relation_embedding",
            "node_concept_fulltext",
            "relation_fulltext",
        ):
            await session.run("CALL db.awaitIndex($name, 300)", name=name)


async def _seed_graph(session) -> None:
    await session.run(
        """
        CREATE (alice:Node {
          id: $alice, name: 'Alice', type: 'entity',
          merged_into: null, embedding: $aliceVec
        })
        CREATE (bob:Node {
          id: $bob, name: 'Bob', type: 'entity',
          merged_into: null, embedding: $bobVec
        })
        CREATE (meeting:Node {
          id: $meeting, name: 'Riunione Q3', type: 'event',
          merged_into: null, embedding: $meetingVec
        })
        CREATE (dup:Node {
          id: $dup, name: 'Alice', type: 'entity',
          merged_into: $alice, embedding: $aliceVec
        })
        CREATE (mario:Node {
          id: $mario, name: 'Mario', type: 'entity',
          merged_into: null, embedding: $bobVec
        })
        CREATE (acme:Node {
          id: $acme, name: 'Acme', type: 'entity',
          merged_into: null, embedding: $meetingVec
        })
        CREATE (meeting)-[:Relation {
          relation: 'is participated by',
          normalized_relation: 'participates',
          is_latest: true
        }]->(alice)
        CREATE (meeting)-[:Relation {
          relation: 'is participated by',
          normalized_relation: 'participates',
          is_latest: true
        }]->(bob)
        CREATE (mario)-[:Relation {
          relation: 'ha fondato',
          normalized_relation: 'founded',
          embedding: $foundVec,
          is_latest: true
        }]->(acme)
        CREATE (tech:Concept {
          id: $tech, name: 'technology', embedding: $techVec
        })
        CREATE (alice)-[:HAS_CONCEPT]->(tech)
        CREATE (meeting)-[:HAS_CONCEPT]->(tech)
        CREATE (fact:Fact {
          id: $fact, text: 'A fact that must not leak',
          type: 'fact', is_latest: true, embedding: $aliceVec
        })
        """,
        alice=ALICE,
        bob=BOB,
        meeting=MEETING,
        dup=MERGED,
        mario=MARIO,
        acme=ACME,
        tech=TECH,
        fact=FACT_ID,
        aliceVec=ALICE_VEC,
        bobVec=BOB_VEC,
        meetingVec=MEETING_VEC,
        foundVec=FOUND_VEC,
        techVec=TECH_VEC,
    )


@pytest.fixture
async def seeded(neo4j_ready, monkeypatch):
    monkeypatch.setattr(
        nqe,
        "_predict_rerank",
        lambda question, descriptions: [0.5] * len(descriptions),
    )

    async def fake_llm(system, user, model, temperature=0, job_id=None):
        cited = []
        if "Alice" in user:
            cited.append(ALICE)
        if "Bob" in user:
            cited.append(BOB)
        if "Mario" in user:
            cited.append(MARIO)
        if "technology" in user.lower() or "Technology" in user:
            cited.append(TECH)
        return NodeQueryAnswer(
            answer="Risposta di test sul grafo Node.",
            cited_node_ids=cited,
        )

    monkeypatch.setattr(nqe, "call_structured", fake_llm)

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        await _seed_graph(session)
    await _await_indexes()
    yield neo4j_ready


@pytest.mark.asyncio
async def test_entity_question_cites_alice(seeded, monkeypatch):
    monkeypatch.setattr(nqe.embeddings, "embed", lambda text: _similar_vector(ALICE_VEC))
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        response = await run_node_query(session, "chi è Alice?")
    assert ALICE in {n.id for n in response.nodes_used}
    assert MERGED not in {n.id for n in response.nodes_used}
    assert FACT_ID not in {n.id for n in response.nodes_used}
    assert FACT_ID not in response.cited_node_ids


@pytest.mark.asyncio
async def test_ppr_reaches_bob_via_shared_event(seeded, monkeypatch):
    monkeypatch.setattr(nqe.embeddings, "embed", lambda text: _similar_vector(ALICE_VEC))
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        await ensure_ppr_projection(session)
        response = await run_node_query(session, "chi è Alice?")
    used = {n.id for n in response.nodes_used}
    assert ALICE in used
    assert BOB in used
    assert MERGED not in used


@pytest.mark.asyncio
async def test_relation_channel_recall_without_entity_names(seeded, monkeypatch):
    monkeypatch.setattr(nqe, "ENABLE_NODE_VECTOR", False)
    monkeypatch.setattr(nqe, "ENABLE_CONCEPT_VECTOR", False)
    monkeypatch.setattr(nqe, "ENABLE_NODE_CONCEPT_FULLTEXT", False)
    monkeypatch.setattr(nqe.embeddings, "embed", lambda text: _similar_vector(FOUND_VEC))
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        response = await run_node_query(session, "chi ha fondato")
    used = {n.id for n in response.nodes_used}
    assert used
    assert used & {MARIO, ACME}


@pytest.mark.asyncio
async def test_below_threshold_does_not_seed(seeded, monkeypatch):
    monkeypatch.setattr(nqe.embeddings, "embed", lambda text: FAR_VEC)
    monkeypatch.setattr(nqe, "ENABLE_NODE_CONCEPT_FULLTEXT", False)
    monkeypatch.setattr(nqe, "ENABLE_RELATION_FULLTEXT", False)
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        response = await run_node_query(session, "zzzz-unrelated-xyz")
    assert response.nodes_used == []
    assert "nessuna informazione trovata" in response.answer.lower()


@pytest.mark.asyncio
async def test_scenario2_subgraph_has_participates(seeded, monkeypatch):
    monkeypatch.setattr(nqe.embeddings, "embed", lambda text: _similar_vector(ALICE_VEC))
    monkeypatch.setattr(
        nqe,
        "_predict_rerank",
        lambda question, descriptions: [0.9] * len(descriptions),
    )
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        await ensure_ppr_projection(session)
        response = await run_node_query(session, "chi è Alice?")
    rel_types = {rel.type for rel in response.subgraph.relationships}
    assert "participates" in rel_types
    used = {n.id for n in response.nodes_used}
    assert ALICE in used
    assert BOB in used
    types = {n.type for n in response.nodes_used}
    assert "entity" in types


@pytest.mark.asyncio
async def test_scenario3_concept_question(seeded, monkeypatch):
    monkeypatch.setattr(nqe.embeddings, "embed", lambda text: _similar_vector(TECH_VEC))
    monkeypatch.setattr(
        nqe,
        "_predict_rerank",
        lambda question, descriptions: [0.9] * len(descriptions),
    )
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        await ensure_ppr_projection(session)
        response = await run_node_query(session, "parlami di technology")
    assert response.concepts_used
    types = {n.type for n in response.nodes_used}
    assert "entity" in types
    assert "event" in types
    used = {n.id for n in response.nodes_used}
    assert ALICE in used
    assert MEETING in used


@pytest.mark.asyncio
async def test_scenario5_fact_only_graph(neo4j_ready, monkeypatch):
    monkeypatch.setattr(nqe.embeddings, "embed", lambda text: _similar_vector(ALICE_VEC))
    monkeypatch.setattr(nqe, "ENABLE_NODE_CONCEPT_FULLTEXT", False)
    monkeypatch.setattr(nqe, "ENABLE_RELATION_FULLTEXT", False)
    monkeypatch.setattr(
        nqe,
        "_predict_rerank",
        lambda question, descriptions: [0.5] * len(descriptions),
    )

    async def fake_llm(system, user, model, temperature=0, job_id=None):
        return NodeQueryAnswer(answer="should not be used", cited_node_ids=[])

    monkeypatch.setattr(nqe, "call_structured", fake_llm)

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        await session.run(
            """
            CREATE (fact:Fact {
              id: $fact, text: 'Alice works at Acme',
              type: 'fact', is_latest: true, embedding: $aliceVec
            })
            """,
            fact=FACT_ID,
            aliceVec=ALICE_VEC,
        )
    await _await_indexes()
    async with driver.session() as session:
        response = await run_node_query(session, "chi è Alice?")
    assert response.nodes_used == []
    assert response.concepts_used == []
    assert "nessuna informazione trovata" in response.answer.lower()


@pytest.mark.asyncio
async def test_scenario8_consecutive_queries_do_not_reproject(seeded, monkeypatch):
    monkeypatch.setattr(nqe.embeddings, "embed", lambda text: _similar_vector(ALICE_VEC))
    refresh_calls: list[str] = []
    original_refresh = npp.refresh_ppr_projection

    async def spy_refresh(session):
        refresh_calls.append("refresh")
        await original_refresh(session)

    monkeypatch.setattr(npp, "refresh_ppr_projection", spy_refresh)

    driver = neo4j_client.get_driver()
    project_cyphers: list[str] = []
    async with driver.session() as session:
        await ensure_ppr_projection(session)
        refresh_after_ensure = list(refresh_calls)
        original_run = session.run

        async def spy_run(cypher, *args, **kwargs):
            if "project.cypher" in cypher or "gds.graph.drop" in cypher:
                project_cyphers.append(cypher)
            return await original_run(cypher, *args, **kwargs)

        monkeypatch.setattr(session, "run", spy_run)
        await run_node_query(session, "chi è Alice?")
        await run_node_query(session, "chi è Alice?")

    assert refresh_calls == refresh_after_ensure
    assert project_cyphers == []
