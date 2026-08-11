"""Dreaming pipeline integration tests (Epic 4)."""

from __future__ import annotations

import asyncio
import math
from unittest.mock import AsyncMock

import pytest
from testcontainers.community.neo4j import Neo4jContainer

from app.core import event_bus, neo4j_client
from app.core.llm_client import LLMValidationError
from app.db.schema import apply_schema_with_driver
from app.models.consolidation import ConsolidationOutcome, ConsolidationResult
from app.models.extraction import FactType
from app.models.relations import RelationClassification, RelationLabel
from app.pipeline import grouping
from app.pipeline.dreaming import run_dreaming_pipeline
from app.pipeline.reconcile import reconcile
from app.pipeline.relations import find_candidates

NEO4J_IMAGE = "neo4j:5.24-community"
EMBEDDING_DIM = 768


@pytest.fixture(scope="module")
def neo4j_container():
    container = (
        Neo4jContainer(NEO4J_IMAGE)
        .with_env("NEO4J_PLUGINS", '["graph-data-science"]')
        .with_env("NEO4J_dbms_security_procedures_unrestricted", "gds.*")
    )
    container.start()
    try:
        yield container
    finally:
        container.stop()


@pytest.fixture
async def neo4j_ready(neo4j_container, monkeypatch):
    uri = neo4j_container.get_connection_url()
    user = neo4j_container.username
    password = neo4j_container.password

    monkeypatch.setattr("app.core.config.settings.NEO4J_URI", uri)
    monkeypatch.setattr("app.core.config.settings.NEO4J_USER", user)
    monkeypatch.setattr("app.core.config.settings.NEO4J_PASSWORD", password)
    monkeypatch.setattr("app.core.config.settings.AUTO_MIGRATE", False)

    apply_schema_with_driver(neo4j_container.get_driver())

    await neo4j_client.close_neo4j_driver()
    await neo4j_client.init_neo4j_driver()

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        await session.run("MATCH (n) DETACH DELETE n")

    yield neo4j_container

    async with driver.session() as session:
        await session.run("MATCH (n) DETACH DELETE n")
    await neo4j_client.close_neo4j_driver()


@pytest.fixture(autouse=True)
def clean_event_bus():
    event_bus.reset_event_bus()
    yield
    event_bus.reset_event_bus()


def _unit_vector(index: int, dim: int = EMBEDDING_DIM) -> list[float]:
    vector = [0.0] * dim
    vector[index % dim] = 1.0
    return vector


def _similar_vector(base: list[float], epsilon: float = 0.01) -> list[float]:
    vec = list(base)
    vec[0] = max(0.0, vec[0] - epsilon)
    norm = math.sqrt(sum(value * value for value in vec))
    return [value / norm for value in vec]


async def _await_vector_index() -> None:
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        await session.run("CALL db.awaitIndex('fact_embedding', 300)")


async def _create_fact(
    *,
    fact_id: str,
    text: str,
    embedding: list[float],
    doc_id: str = "doc-test",
    is_latest: bool = True,
    dreamed: bool = False,
) -> None:
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        await session.run(
            """
            CREATE (f:Fact {
              id: $id,
              text: $text,
              type: 'fact',
              is_latest: $is_latest,
              confidence: 1.0,
              dreamed: $dreamed,
              source_doc_id: $doc_id,
              embedding: $embedding,
              created_at: datetime()
            })
            """,
            id=fact_id,
            text=text,
            is_latest=is_latest,
            dreamed=dreamed,
            doc_id=doc_id,
            embedding=embedding,
        )


async def _create_updates(src_id: str, tgt_id: str) -> None:
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        await session.run(
            """
            MATCH (n:Fact {id: $src_id}), (v:Fact {id: $tgt_id})
            CREATE (n)-[:UPDATES {created_at: datetime()}]->(v)
            SET v.is_latest = false, n.is_latest = true
            """,
            src_id=src_id,
            tgt_id=tgt_id,
        )


async def _graph_exists(name: str) -> bool:
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        result = await session.run(
            "CALL gds.graph.exists($name) YIELD exists RETURN exists",
            name=name,
        )
        record = await result.single()
        return bool(record and record["exists"])


@pytest.mark.asyncio
async def test_grouping_three_similar_one_group_two_isolates(neo4j_ready):
    base = _unit_vector(0)
    similar_a = _similar_vector(base)
    similar_b = _similar_vector(base, epsilon=0.02)
    isolated_x = _unit_vector(1)
    isolated_y = _unit_vector(2)

    await _create_fact(fact_id="f1", text="group one", embedding=base)
    await _create_fact(fact_id="f2", text="group two", embedding=similar_a)
    await _create_fact(fact_id="f3", text="group three", embedding=similar_b)
    await _create_fact(fact_id="f4", text="isolated x", embedding=isolated_x)
    await _create_fact(fact_id="f5", text="isolated y", embedding=isolated_y)

    groups = await grouping.group_fresh_facts()
    group_sets = {frozenset(group) for group in groups}

    assert frozenset({"f1", "f2", "f3"}) in group_sets
    assert frozenset({"f4"}) in group_sets
    assert frozenset({"f5"}) in group_sets
    assert not await _graph_exists("freshFacts")


@pytest.mark.asyncio
async def test_grouping_drops_fresh_facts_after_exception(neo4j_ready, monkeypatch):
    await _create_fact(fact_id="solo", text="alone", embedding=_unit_vector(3))

    monkeypatch.setattr(
        grouping,
        "KNN_MUTATE_CYPHER",
        "CALL gds.this_procedure_does_not_exist($graph_name)",
    )

    with pytest.raises(Exception):
        await grouping.group_fresh_facts()

    assert not await _graph_exists("freshFacts")


@pytest.mark.asyncio
async def test_abstraction_writes_derives_sources_is_latest_unchanged(neo4j_ready, monkeypatch):
    # ENABLE_DERIVES defaults to False (kill-switch); this test explicitly re-enables
    # it to keep validating the abstraction mechanism itself works when turned on.
    monkeypatch.setattr("app.core.config.settings.ENABLE_DERIVES", True)

    base = _unit_vector(10)
    await _create_fact(fact_id="s1", text="Alice likes tea.", embedding=base)
    await _create_fact(
        fact_id="s2",
        text="Alice prefers tea.",
        embedding=_similar_vector(base),
    )

    async def mock_consolidate(facts, job_id=None):
        _ = facts, job_id
        return ConsolidationResult(
            outcome=ConsolidationOutcome.abstraction,
            text="Alice prefers drinking tea.",
            type=FactType.preference,
            source_fact_ids=["s1", "s2"],
        )

    async def mock_classify(n_text, v_text, job_id=None):
        _ = n_text, v_text, job_id
        return RelationClassification(relation=RelationLabel.none)

    monkeypatch.setattr("app.pipeline.dreaming.consolidation.consolidate_group", mock_consolidate)
    monkeypatch.setattr("app.pipeline.dreaming.relations.classify_relation", mock_classify)

    job_id = "job-abstraction"
    queue = await event_bus.subscribe(job_id)
    await run_dreaming_pipeline(job_id)

    events = []
    while True:
        event = await asyncio.wait_for(queue.get(), timeout=5)
        events.append(event)
        if event.get("stage") == "done":
            break

    derived_events = [event for event in events if event["event"] == "fact_derived"]
    assert len(derived_events) == 1
    derived_id = derived_events[0]["payload"]["fact_id"]

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        derives = await session.run(
            """
            MATCH (d:Fact {id: $did})-[:DERIVES]->(s:Fact)
            RETURN collect(s.id) AS sources, d.dreamed AS dreamed
            """,
            did=derived_id,
        )
        record = await derives.single()
        assert record is not None
        assert set(record["sources"]) == {"s1", "s2"}
        assert record["dreamed"] is True

        latest = await session.run(
            "MATCH (f:Fact) WHERE f.id IN ['s1', 's2'] RETURN collect(f.is_latest) AS vals"
        )
        latest_record = await latest.single()
        assert latest_record is not None
        assert latest_record["vals"] == [True, True]


@pytest.mark.asyncio
async def test_derives_disabled_by_default_ignores_abstraction_outcome(
    neo4j_ready, monkeypatch
):
    """ENABLE_DERIVES defaults to False: consolidate_group still runs (grouping and
    cleaned_fact dedup are independent of the flag) and group_formed still fires,
    but an "abstraction" outcome must be ignored — no DERIVES edge, no fact_derived
    event, no new hub fact; the original facts are evaluated individually instead."""
    base = _unit_vector(11)
    await _create_fact(fact_id="d1", text="Alice likes coffee.", embedding=base)
    await _create_fact(
        fact_id="d2",
        text="Alice prefers coffee.",
        embedding=_similar_vector(base),
    )

    async def mock_consolidate(facts, job_id=None):
        _ = facts, job_id
        return ConsolidationResult(
            outcome=ConsolidationOutcome.abstraction,
            text="Alice likes coffee (abstraction attempt).",
            type=FactType.preference,
            source_fact_ids=["d1", "d2"],
        )

    async def mock_classify(n_text, v_text, job_id=None):
        _ = n_text, v_text, job_id
        return RelationClassification(relation=RelationLabel.none)

    monkeypatch.setattr("app.pipeline.dreaming.consolidation.consolidate_group", mock_consolidate)
    monkeypatch.setattr("app.pipeline.dreaming.relations.classify_relation", mock_classify)

    job_id = "job-derives-disabled"
    queue = await event_bus.subscribe(job_id)
    await run_dreaming_pipeline(job_id)

    events = []
    while True:
        event = await asyncio.wait_for(queue.get(), timeout=5)
        events.append(event)
        if event.get("stage") == "done":
            break

    assert not [e for e in events if e["event"] == "fact_derived"]
    group_events = [e for e in events if e["event"] == "group_formed"]
    assert len(group_events) == 1
    assert set(group_events[0]["payload"]["fact_ids"]) == {"d1", "d2"}

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        derives = await session.run("MATCH ()-[r:DERIVES]->() RETURN count(r) AS n")
        record = await derives.single()
        assert record is not None
        assert record["n"] == 0

        # No new abstraction fact was created: still exactly d1 and d2.
        total = await session.run(
            "MATCH (f:Fact) WHERE f.id IN ['d1', 'd2'] RETURN count(f) AS n"
        )
        total_record = await total.single()
        assert total_record is not None
        assert total_record["n"] == 2

        facts = await session.run(
            "MATCH (f:Fact) WHERE f.id IN ['d1', 'd2'] RETURN collect(f.dreamed) AS dreamed"
        )
        facts_record = await facts.single()
        assert facts_record is not None
        assert facts_record["dreamed"] == [True, True]


@pytest.mark.asyncio
async def test_historical_fact_not_in_candidates(neo4j_ready):
    await _create_fact(fact_id="current", text="Current fact.", embedding=_unit_vector(20))
    await _create_fact(
        fact_id="historical",
        text="Old fact.",
        embedding=_unit_vector(21),
        is_latest=False,
    )
    await _await_vector_index()

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        candidates = await find_candidates(
            session,
            "probe",
            _unit_vector(20),
        )

    candidate_ids = {candidate.id for candidate in candidates}
    assert "current" in candidate_ids
    assert "historical" not in candidate_ids


@pytest.mark.asyncio
async def test_update_targets_chain_head_not_historical_node(neo4j_ready, monkeypatch):
    """E4.7: N semantically near A must UPDATES→B (chain head), never A."""
    emb_a = _unit_vector(30)
    emb_b = _similar_vector(emb_a, epsilon=0.05)
    emb_n = _similar_vector(emb_a, epsilon=0.001)

    await _create_fact(fact_id="A", text="Alice works at Acme.", embedding=emb_a, dreamed=True)
    await _create_fact(fact_id="B", text="Alice works at Acme Corp.", embedding=emb_b, dreamed=True)
    await _create_updates("B", "A")
    await _create_fact(
        fact_id="N",
        text="Alice is employed by Acme Corporation.",
        embedding=emb_n,
        dreamed=False,
    )
    await _await_vector_index()

    async def mock_consolidate(facts, job_id=None):
        _ = facts, job_id
        raise AssertionError("singleton should skip consolidation")

    async def mock_classify(n_text, v_text, job_id=None):
        _ = n_text, job_id
        if v_text.startswith("Alice works at Acme Corp"):
            return RelationClassification(relation=RelationLabel.replaces)
        return RelationClassification(relation=RelationLabel.none)

    monkeypatch.setattr("app.pipeline.dreaming.consolidation.consolidate_group", mock_consolidate)
    monkeypatch.setattr("app.pipeline.dreaming.relations.classify_relation", mock_classify)

    await run_dreaming_pipeline("job-chain-head")

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        n_to_b = await session.run(
            "MATCH (:Fact {id: 'N'})-[:UPDATES]->(:Fact {id: 'B'}) RETURN count(*) AS n"
        )
        n_to_a = await session.run(
            "MATCH (:Fact {id: 'N'})-[:UPDATES]->(:Fact {id: 'A'}) RETURN count(*) AS n"
        )
        record_b = await n_to_b.single()
        record_a = await n_to_a.single()
        assert record_b is not None and record_b["n"] == 1
        assert record_a is not None and record_a["n"] == 0

        latest = await session.run(
            "MATCH (f:Fact) WHERE f.id IN ['A', 'B', 'N'] "
            "RETURN f.id AS id, f.is_latest AS is_latest ORDER BY f.id"
        )
        rows = [row async for row in latest]
        latest_map = {row["id"]: row["is_latest"] for row in rows}
        assert latest_map["N"] is True
        assert latest_map["A"] is False
        assert latest_map["B"] is False


@pytest.mark.asyncio
async def test_reconcile_zero_drift_after_clean_cycle(neo4j_ready, monkeypatch):
    await _create_fact(fact_id="x1", text="Fact one.", embedding=_unit_vector(40))

    monkeypatch.setattr(
        "app.pipeline.dreaming.relations.classify_relation",
        AsyncMock(return_value=RelationClassification(relation=RelationLabel.none)),
    )

    await run_dreaming_pipeline("job-drift-clean")
    assert await reconcile() == 0


@pytest.mark.asyncio
async def test_reconcile_corrects_injected_drift(neo4j_ready):
    await _create_fact(fact_id="drift-me", text="Drift.", embedding=_unit_vector(50))

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        await session.run(
            "MATCH (f:Fact {id: 'drift-me'}) SET f.is_latest = false"
        )

    drift = await reconcile()
    assert drift == 1

    async with driver.session() as session:
        result = await session.run(
            "MATCH (f:Fact {id: 'drift-me'}) RETURN f.is_latest AS is_latest"
        )
        record = await result.single()
        assert record is not None
        assert record["is_latest"] is True


@pytest.mark.asyncio
async def test_llm_failures_do_not_stop_cycle(neo4j_ready, monkeypatch):
    base = _unit_vector(60)
    await _create_fact(fact_id="g1a", text="Team alpha one.", embedding=base)
    await _create_fact(fact_id="g1b", text="Team alpha two.", embedding=_similar_vector(base))
    await _create_fact(fact_id="g2a", text="Team beta one.", embedding=_unit_vector(61))
    await _create_fact(
        fact_id="g2b",
        text="Team beta two.",
        embedding=_similar_vector(_unit_vector(61)),
    )
    await _create_fact(fact_id="g3a", text="Team gamma one.", embedding=_unit_vector(62))
    await _create_fact(
        fact_id="g3b",
        text="Team gamma two.",
        embedding=_similar_vector(_unit_vector(62)),
    )

    call_count = {"n": 0}

    async def mock_consolidate(facts, job_id=None):
        _ = job_id
        call_count["n"] += 1
        fact_ids = {fact_id for fact_id, _text in facts}
        if "g1a" in fact_ids:
            raise LLMValidationError("forced consolidation failure")
        return ConsolidationResult(
            outcome=ConsolidationOutcome.cleaned_fact,
            text="Cleaned " + next(iter(facts))[1],
            type=FactType.fact,
            source_fact_ids=[],
        )

    async def mock_classify(n_text, v_text, job_id=None):
        _ = n_text, job_id
        if "fail-pair-target" in v_text:
            raise LLMValidationError("forced classification failure")
        return RelationClassification(relation=RelationLabel.none)

    await _create_fact(
        fact_id="solo-fail",
        text="Standalone fact for pair failure.",
        embedding=_unit_vector(63),
    )
    await _create_fact(
        fact_id="fail-pair-target",
        text="fail-pair-target candidate.",
        embedding=_similar_vector(_unit_vector(63)),
        is_latest=True,
        dreamed=True,
    )
    await _await_vector_index()

    monkeypatch.setattr("app.pipeline.dreaming.consolidation.consolidate_group", mock_consolidate)
    monkeypatch.setattr("app.pipeline.dreaming.relations.classify_relation", mock_classify)

    job_id = "job-failures"
    queue = await event_bus.subscribe(job_id)
    await run_dreaming_pipeline(job_id)

    events = []
    while True:
        event = await asyncio.wait_for(queue.get(), timeout=5)
        events.append(event)
        if event.get("stage") == "done":
            break

    failed_consolidation = [
        event
        for event in events
        if event["event"] == "llm_call_failed" and event["stage"] == "consolidation"
    ]
    assert len(failed_consolidation) >= 1
    assert call_count["n"] == 3

    failed_relation = [
        event
        for event in events
        if event["event"] == "llm_call_failed" and event["stage"] == "relation_detection"
    ]
    assert len(failed_relation) >= 1

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        failed_group = await session.run(
            "MATCH (f:Fact) WHERE f.id IN ['g1a', 'g1b'] AND f.dreamed = false RETURN count(f) AS n"
        )
        failed_record = await failed_group.single()
        assert failed_record is not None
        assert failed_record["n"] == 2

        processed = await session.run(
            "MATCH (f:Fact) WHERE f.id IN ['g2a', 'g2b', 'g3a', 'g3b'] AND f.dreamed = true "
            "RETURN count(f) AS n"
        )
        processed_record = await processed.single()
        assert processed_record is not None
        assert processed_record["n"] >= 2


@pytest.mark.asyncio
async def test_replaces_extends_and_update_chain(neo4j_ready, monkeypatch):
    await _create_fact(
        fact_id="A", text="Office in Rome.", embedding=_unit_vector(70), dreamed=True
    )
    await _create_fact(
        fact_id="B", text="Office in Milan.", embedding=_unit_vector(71), dreamed=True
    )
    await _create_fact(
        fact_id="C",
        text="Office in Milan city.",
        embedding=_unit_vector(72),
        dreamed=True,
    )
    await _create_fact(
        fact_id="D", text="Office has a gym.", embedding=_unit_vector(73), dreamed=True
    )

    await _create_updates("B", "A")
    await _create_updates("C", "B")

    await _create_fact(
        fact_id="N_replace",
        text="Office is in Milan downtown.",
        embedding=_similar_vector(_unit_vector(72), epsilon=0.001),
        dreamed=False,
    )
    await _create_fact(
        fact_id="N_extend",
        text="Office building has a rooftop gym.",
        embedding=_similar_vector(_unit_vector(73), epsilon=0.001),
        dreamed=False,
    )
    await _await_vector_index()

    async def mock_consolidate(facts, job_id=None):
        _ = facts, job_id
        raise AssertionError("singletons only")

    async def mock_classify(n_text, v_text, job_id=None):
        _ = job_id
        if n_text.startswith("Office is in Milan") and "Milan city" in v_text:
            return RelationClassification(relation=RelationLabel.replaces)
        if n_text.startswith("Office building") and "gym" in v_text:
            return RelationClassification(relation=RelationLabel.extends)
        return RelationClassification(relation=RelationLabel.none)

    monkeypatch.setattr("app.pipeline.dreaming.consolidation.consolidate_group", mock_consolidate)
    monkeypatch.setattr("app.pipeline.dreaming.relations.classify_relation", mock_classify)

    await run_dreaming_pipeline("job-relations")

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        chain = await session.run(
            "MATCH path = (c:Fact {id: 'C'})-[:UPDATES*0..]->(a:Fact {id: 'A'}) "
            "RETURN length(path) AS len ORDER BY len DESC LIMIT 1"
        )
        chain_record = await chain.single()
        assert chain_record is not None
        assert chain_record["len"] == 2

        latest = await session.run(
            "MATCH (f:Fact) WHERE f.id IN ['A', 'B', 'C', 'N_replace'] "
            "RETURN f.id AS id, f.is_latest AS is_latest"
        )
        latest_map = {row["id"]: row["is_latest"] async for row in latest}
        assert latest_map["N_replace"] is True
        assert latest_map["A"] is False
        assert latest_map["B"] is False
        assert latest_map["C"] is False

        extends = await session.run(
            "MATCH (:Fact {id: 'N_extend'})-[:EXTENDS]->(:Fact {id: 'D'}) RETURN count(*) AS n"
        )
        extends_record = await extends.single()
        assert extends_record is not None and extends_record["n"] == 1

        d_latest = await session.run(
            "MATCH (f:Fact {id: 'D'}) RETURN f.is_latest AS is_latest"
        )
        d_record = await d_latest.single()
        assert d_record is not None and d_record["is_latest"] is True


@pytest.mark.asyncio
async def test_group_formed_only_for_multi_member_groups(neo4j_ready, monkeypatch):
    base = _unit_vector(80)
    await _create_fact(fact_id="pair-a", text="Pair A", embedding=base)
    await _create_fact(fact_id="pair-b", text="Pair B", embedding=_similar_vector(base))
    await _create_fact(fact_id="single", text="Single", embedding=_unit_vector(81))

    monkeypatch.setattr(
        "app.pipeline.dreaming.consolidation.consolidate_group",
        AsyncMock(
            return_value=ConsolidationResult(
                outcome=ConsolidationOutcome.cleaned_fact,
                text="Cleaned pair",
                type=FactType.fact,
            )
        ),
    )
    monkeypatch.setattr(
        "app.pipeline.dreaming.relations.classify_relation",
        AsyncMock(return_value=RelationClassification(relation=RelationLabel.none)),
    )

    job_id = "job-group-formed"
    queue = await event_bus.subscribe(job_id)
    await run_dreaming_pipeline(job_id)

    events = []
    while True:
        event = await asyncio.wait_for(queue.get(), timeout=5)
        events.append(event)
        if event.get("stage") == "done":
            break

    group_events = [event for event in events if event["event"] == "group_formed"]
    assert len(group_events) == 1
    assert set(group_events[0]["payload"]["fact_ids"]) == {"pair-a", "pair-b"}
