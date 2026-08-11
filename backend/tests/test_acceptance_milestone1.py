"""Milestone 1 acceptance suite — tech-spec §14 / milestone1.md §8 (E10.1).

Each test name maps 1:1 to an acceptance criterion.
"""

from __future__ import annotations

import math
from unittest.mock import AsyncMock

import pytest
from testcontainers.community.neo4j import Neo4jContainer

from app.core import event_bus, neo4j_client
from app.db.schema import apply_schema_with_driver
from app.models.consolidation import ConsolidationOutcome, ConsolidationResult
from app.models.extraction import ExtractedFact, FactExtractionResult, FactType
from app.models.relations import RelationClassification, RelationLabel
from app.pipeline import query_engine
from app.pipeline.dreaming import run_dreaming_pipeline
from app.pipeline.ingestion import run_ingestion_pipeline
from app.pipeline.query_engine import QueryAnswer
from app.pipeline.reconcile import reconcile

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
    doc_id: str = "doc-acc",
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


async def _create_updates(src: str, tgt: str) -> None:
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        await session.run(
            """
            MATCH (n:Fact {id: $src}), (v:Fact {id: $tgt})
            CREATE (n)-[:UPDATES {created_at: datetime()}]->(v)
            SET v.is_latest = false, n.is_latest = true
            """,
            src=src,
            tgt=tgt,
        )


@pytest.mark.asyncio
async def test_criterion_ingestion_creates_chunks_facts_provenance_noise_discarded(
    neo4j_ready, monkeypatch
):
    """milestone1.md §8: ingest → chunks+facts+provenance; rumore scartato."""

    async def mock_extract(chunk_text: str, job_id: str | None = None) -> FactExtractionResult:
        _ = job_id
        if "noise" in chunk_text.lower():
            return FactExtractionResult(facts=[])
        return FactExtractionResult(
            facts=[ExtractedFact(text=f"Fact: {chunk_text[:40]}", type=FactType.fact)]
        )

    monkeypatch.setattr("app.pipeline.ingestion.extraction.extract_facts", mock_extract)

    await run_ingestion_pipeline(
        "doc-acc-1",
        "Alice works at Acme.\n\nok capito noise filler only.",
        "job-acc-ingest",
    )

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        chunks = await (await session.run("MATCH (c:Chunk) RETURN count(c) AS n")).single()
        facts = await (await session.run("MATCH (f:Fact) RETURN count(f) AS n")).single()
        linked = await (
            await session.run(
                "MATCH (f:Fact)-[:DERIVED_FROM]->(c:Chunk) RETURN count(f) AS n"
            )
        ).single()
        noise_facts = await (
            await session.run(
                """
                MATCH (c:Chunk)
                WHERE toLower(c.text) CONTAINS 'noise'
                OPTIONAL MATCH (f:Fact)-[:DERIVED_FROM]->(c)
                RETURN count(f) AS n
                """
            )
        ).single()

    assert chunks is not None and chunks["n"] >= 1
    assert facts is not None and facts["n"] >= 1
    assert linked is not None and linked["n"] == facts["n"]
    assert noise_facts is not None and noise_facts["n"] == 0


@pytest.mark.asyncio
async def test_criterion_replaces_updates_old_not_latest_query_returns_only_new(
    neo4j_ready, monkeypatch
):
    """milestone1.md §8: sostituzione → UPDATES; query corrente solo il nuovo."""
    base = _unit_vector(1)
    await _create_fact(fact_id="old", text="Alice at Beta", embedding=base, dreamed=True)
    await _create_fact(
        fact_id="new",
        text="Alice at Acme",
        embedding=_similar_vector(base),
        dreamed=True,
    )
    await _create_updates("new", "old")
    await _await_vector_index()

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        edge = await (
            await session.run(
                "MATCH (n:Fact {id:'new'})-[:UPDATES]->(v:Fact {id:'old'}) "
                "RETURN v.is_latest AS old_latest, n.is_latest AS new_latest"
            )
        ).single()
    assert edge is not None
    assert edge["old_latest"] is False
    assert edge["new_latest"] is True

    monkeypatch.setattr(
        "app.pipeline.query_engine.embeddings.embed",
        lambda text: _similar_vector(base, 0.005),
    )
    monkeypatch.setattr(
        "app.pipeline.query_engine.call_structured",
        AsyncMock(return_value=QueryAnswer(answer="Alice at Acme")),
    )

    async with driver.session() as session:
        response = await query_engine.run_query(session, "Where does Alice work?")
    used = {f.id for f in response.facts_used}
    assert "new" in used
    assert "old" not in used


@pytest.mark.asyncio
async def test_criterion_chain_a_b_c_only_c_latest_history_walkable(neo4j_ready):
    """milestone1.md §8: catena A←B←C → solo C latest; storico percorribile."""
    base = _unit_vector(2)
    await _create_fact(fact_id="A", text="A", embedding=base, is_latest=False, dreamed=True)
    await _create_fact(
        fact_id="B",
        text="B",
        embedding=_similar_vector(base),
        is_latest=False,
        dreamed=True,
    )
    await _create_fact(
        fact_id="C",
        text="C",
        embedding=_similar_vector(base, 0.02),
        is_latest=True,
        dreamed=True,
    )
    await _create_updates("B", "A")
    await _create_updates("C", "B")

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        latest = await (
            await session.run(
                "MATCH (f:Fact) WHERE f.is_latest = true RETURN collect(f.id) AS ids"
            )
        ).single()
        path = await (
            await session.run(
                "MATCH path=(c:Fact {id:'C'})-[:UPDATES*]->(a:Fact {id:'A'}) "
                "RETURN length(path) AS len"
            )
        ).single()
        history = await query_engine.get_fact_history(session, "C")

    assert latest is not None and latest["ids"] == ["C"]
    assert path is not None and path["len"] == 2
    assert history is not None
    assert [e.id for e in history.facts] == ["C", "B", "A"]


@pytest.mark.asyncio
async def test_criterion_extends_both_latest_query_returns_together(
    neo4j_ready, monkeypatch
):
    """milestone1.md §8: EXTENDS → entrambi correnti; query li restituisce insieme."""
    base = _unit_vector(3)
    await _create_fact(fact_id="main", text="Alice at Acme", embedding=base, dreamed=True)
    await _create_fact(
        fact_id="extra",
        text="Alice prefers remote",
        embedding=_unit_vector(4),
        dreamed=True,
    )
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        await session.run(
            """
            MATCH (n:Fact {id:'extra'}), (v:Fact {id:'main'})
            CREATE (n)-[:EXTENDS {created_at: datetime()}]->(v)
            """
        )
    await _await_vector_index()

    async with driver.session() as session:
        flags = await (
            await session.run(
                "MATCH (f:Fact) WHERE f.id IN ['main','extra'] "
                "RETURN collect(f.is_latest) AS flags"
            )
        ).single()
    assert flags is not None and all(flags["flags"])

    monkeypatch.setattr(
        "app.pipeline.query_engine.embeddings.embed",
        lambda text: base,
    )
    monkeypatch.setattr(
        "app.pipeline.query_engine.call_structured",
        AsyncMock(return_value=QueryAnswer(answer="Alice works remotely at Acme")),
    )
    async with driver.session() as session:
        response = await query_engine.run_query(session, "Tell me about Alice")
    used = {f.id for f in response.facts_used}
    assert "main" in used and "extra" in used


@pytest.mark.asyncio
async def test_criterion_consolidation_abstraction_derives_sources_remain(
    neo4j_ready, monkeypatch
):
    """milestone1.md §8: consolidamento → DERIVES; sorgenti restano."""
    # ENABLE_DERIVES defaults to False (kill-switch); this criterion explicitly
    # re-enables it to keep validating the mechanism works when turned on.
    monkeypatch.setattr("app.core.config.settings.ENABLE_DERIVES", True)

    base = _unit_vector(5)
    await _create_fact(fact_id="s1", text="Alice likes tea", embedding=base)
    await _create_fact(
        fact_id="s2",
        text="Alice prefers tea",
        embedding=_similar_vector(base),
    )

    async def mock_consolidate(facts, job_id=None):
        _ = facts, job_id
        return ConsolidationResult(
            outcome=ConsolidationOutcome.abstraction,
            text="Alice prefers drinking tea",
            type=FactType.preference,
            source_fact_ids=["s1", "s2"],
        )

    async def mock_classify(n_text, v_text, job_id=None):
        _ = n_text, v_text, job_id
        return RelationClassification(relation=RelationLabel.none)

    monkeypatch.setattr(
        "app.pipeline.dreaming.consolidation.consolidate_group", mock_consolidate
    )
    monkeypatch.setattr(
        "app.pipeline.dreaming.relations.classify_relation", mock_classify
    )

    await run_dreaming_pipeline("job-acc-abs")

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        derived = await (
            await session.run(
                """
                MATCH (d:Fact)-[:DERIVES]->(s:Fact)
                WHERE s.id IN ['s1','s2']
                RETURN d.id AS did, collect(s.id) AS sources,
                       collect(s.is_latest) AS source_latest
                """
            )
        ).single()
    assert derived is not None
    assert set(derived["sources"]) == {"s1", "s2"}
    assert all(derived["source_latest"])


@pytest.mark.asyncio
async def test_criterion_update_on_historical_targets_chain_head_not_node(
    neo4j_ready, monkeypatch
):
    """milestone1.md §8: update su fatto storico aggancia la testa, non lo storico."""
    emb_a = _unit_vector(6)
    emb_b = _similar_vector(emb_a, epsilon=0.05)
    emb_n = _similar_vector(emb_a, epsilon=0.001)

    await _create_fact(fact_id="A", text="Alice works at Acme.", embedding=emb_a, dreamed=True)
    await _create_fact(
        fact_id="B",
        text="Alice works at Acme Corp.",
        embedding=emb_b,
        dreamed=True,
    )
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

    monkeypatch.setattr(
        "app.pipeline.dreaming.consolidation.consolidate_group", mock_consolidate
    )
    monkeypatch.setattr(
        "app.pipeline.dreaming.relations.classify_relation", mock_classify
    )

    await run_dreaming_pipeline("job-acc-head")

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        to_b = await (
            await session.run(
                "MATCH (:Fact {id:'N'})-[:UPDATES]->(:Fact {id:'B'}) RETURN count(*) AS n"
            )
        ).single()
        to_a = await (
            await session.run(
                "MATCH (:Fact {id:'N'})-[:UPDATES]->(:Fact {id:'A'}) RETURN count(*) AS n"
            )
        ).single()
        latest_rows = [
            row
            async for row in await session.run(
                "MATCH (f:Fact) WHERE f.id IN ['A','B','N'] "
                "RETURN f.id AS id, f.is_latest AS is_latest"
            )
        ]

    assert to_b is not None and to_b["n"] == 1
    assert to_a is not None and to_a["n"] == 0
    latest_map = {row["id"]: row["is_latest"] for row in latest_rows}
    assert latest_map["N"] is True
    assert latest_map["A"] is False
    assert latest_map["B"] is False


@pytest.mark.asyncio
async def test_criterion_reconcile_zero_drift_after_dreaming_cycle(
    neo4j_ready, monkeypatch
):
    """milestone1.md §8: riconciliazione non cambia righe dopo dreaming (drift=0)."""
    base = _unit_vector(7)
    await _create_fact(fact_id="f1", text="Solo fact", embedding=base)

    async def mock_consolidate(facts, job_id=None):
        _ = job_id
        return ConsolidationResult(
            outcome=ConsolidationOutcome.cleaned_fact,
            text=facts[0][1],
            type=FactType.fact,
            source_fact_ids=[],
        )

    async def mock_classify(n_text, v_text, job_id=None):
        _ = n_text, v_text, job_id
        return RelationClassification(relation=RelationLabel.none)

    monkeypatch.setattr(
        "app.pipeline.dreaming.consolidation.consolidate_group", mock_consolidate
    )
    monkeypatch.setattr(
        "app.pipeline.dreaming.relations.classify_relation", mock_classify
    )

    await run_dreaming_pipeline("job-acc-drift")
    drift = await reconcile()
    assert drift == 0


@pytest.mark.asyncio
async def test_criterion_historical_query_reconstructs_updates_evolution(neo4j_ready):
    """milestone1.md §8: query storica ricostruisce l'evoluzione via UPDATES."""
    base = _unit_vector(8)
    await _create_fact(fact_id="A", text="v1", embedding=base, is_latest=False, dreamed=True)
    await _create_fact(
        fact_id="B",
        text="v2",
        embedding=_similar_vector(base),
        is_latest=False,
        dreamed=True,
    )
    await _create_fact(
        fact_id="C",
        text="v3",
        embedding=_similar_vector(base, 0.02),
        is_latest=True,
        dreamed=True,
    )
    await _create_updates("B", "A")
    await _create_updates("C", "B")

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        history = await query_engine.get_fact_history(session, "C")

    assert history is not None
    assert [e.id for e in history.facts] == ["C", "B", "A"]
    assert [e.path_length for e in history.facts] == [0, 1, 2]
