"""Macrotask 8 integration acceptance (scenarios 1, 2, 4). Requires Docker / testcontainers."""

from __future__ import annotations

import hashlib

import pytest

from app.core import event_bus, neo4j_client
from app.db.schema import apply_schema_with_driver
from app.models.node_extraction import (
    ConceptResult,
    EntityExtractionResult,
    EventEntityExtractionResult,
    EventEntityParticipation,
    EventRelationClassification,
    EventRelationExtractionResult,
    EventRelationLabel,
    NodeDedupResult,
    PairRelationDecision,
)
from app.models.relations import RelationClassification, RelationLabel
from app.pipeline.concepts import compute_hash_id
from app.pipeline.dreaming import _run_node_phases
from app.pipeline.ingestion import run_ingestion_pipeline
from app.pipeline.node_graph_engine import (
    get_concept_neighbors,
    get_entity_graph,
    get_event_graph,
    get_participation_graph,
)
from tests.neo4j_gds import neo4j_gds_container

EMBEDDING_DIM = 768
DOC_TEXT = "Alice and Acme attended the product launch."
EVENT_NAME = "product launch"
ENTITY_A = "Alice"
ENTITY_B = "Acme"


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
        await session.run("CALL db.awaitIndex('node_embedding', 300)")

    yield neo4j_container

    async with driver.session() as session:
        await session.run("MATCH (n) DETACH DELETE n")
    await neo4j_client.close_neo4j_driver()


@pytest.fixture(autouse=True)
def clean_event_bus():
    event_bus.reset_event_bus()
    yield
    event_bus.reset_event_bus()


def _unit_vector(name: str) -> list[float]:
    known = {ENTITY_A: 0, ENTITY_B: 1, EVENT_NAME: 2}
    vec = [0.0] * EMBEDDING_DIM
    if name in known:
        vec[known[name]] = 1.0
        return vec
    idx = int(hashlib.md5(name.encode("utf-8")).hexdigest(), 16) % EMBEDDING_DIM
    vec[idx] = 1.0
    return vec


def _patch_embeddings(monkeypatch) -> None:
    monkeypatch.setattr("app.pipeline.embeddings.embed", _unit_vector)
    monkeypatch.setattr(
        "app.pipeline.embeddings.embed_batch",
        lambda texts: [_unit_vector(text) for text in texts],
    )


def _patch_no_openai(monkeypatch) -> list:
    llm_calls: list[object] = []

    async def fake_call_structured(system, user, model, temperature=0, job_id=None):
        _ = system, user, temperature, job_id
        llm_calls.append(model)
        if model is NodeDedupResult:
            return NodeDedupResult(duplicate_of=None)
        if model is EventRelationClassification:
            return EventRelationClassification(label=EventRelationLabel.none)
        raise AssertionError(f"unexpected structured model {model}")

    async def fake_classify_relation(*_args, **_kwargs):
        llm_calls.append("classify_relation")
        return RelationClassification(relation=RelationLabel.none)

    monkeypatch.setattr("app.pipeline.node_resolution.call_structured", fake_call_structured)
    monkeypatch.setattr(
        "app.pipeline.event_relation_resolution.call_structured", fake_call_structured
    )
    monkeypatch.setattr(
        "app.pipeline.entity_relation_resolution.classify_relation", fake_classify_relation
    )
    return llm_calls


def _patch_extractors(monkeypatch, *, concepts: list[str] | None = None) -> None:
    concept_names = concepts if concepts is not None else []

    async def mock_entities(
        chunk_text: str, job_id: str | None = None, corpus_summary: str = ""
    ):
        _ = chunk_text, job_id, corpus_summary
        return EntityExtractionResult(entities=[])

    async def mock_pair(*_args, **_kwargs):
        return PairRelationDecision(related=False)

    async def mock_event_entity(chunk_text: str, job_id: str | None = None):
        _ = chunk_text, job_id
        return EventEntityExtractionResult(
            participations=[
                EventEntityParticipation(event=EVENT_NAME, entities=[ENTITY_A, ENTITY_B])
            ]
        )

    async def mock_event_rel(chunk_text: str, job_id: str | None = None):
        _ = chunk_text, job_id
        return EventRelationExtractionResult(triples=[])

    async def mock_concepts(*_args, **_kwargs):
        return ConceptResult(concepts=list(concept_names))

    async def mock_corpus(session, document_text, job_id) -> str:
        _ = session, document_text, job_id
        return "Macrotask 8 scenario corpus."

    monkeypatch.setattr("app.pipeline.node_extraction.extract_entities", mock_entities)
    monkeypatch.setattr("app.pipeline.node_extraction.extract_pair_relation", mock_pair)
    monkeypatch.setattr("app.pipeline.node_extraction.extract_event_entities", mock_event_entity)
    monkeypatch.setattr("app.pipeline.node_extraction.extract_event_relations", mock_event_rel)
    monkeypatch.setattr("app.pipeline.node_extraction.extract_entity_concepts", mock_concepts)
    monkeypatch.setattr("app.pipeline.node_extraction.extract_event_concepts", mock_concepts)
    monkeypatch.setattr("app.pipeline.ingestion.update_corpus_context", mock_corpus)


async def _dream(job_id: str) -> None:
    await _run_node_phases(neo4j_client.get_driver(), job_id)


async def _count(cypher: str, **kwargs) -> int:
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        result = await session.run(cypher, **kwargs)
        record = await result.single()
        assert record is not None
        return int(record["n"])


@pytest.mark.enable_node_extraction
@pytest.mark.asyncio
async def test_scenario1_clean_ingest_two_entities_one_event_two_participates(
    neo4j_ready, monkeypatch
):
    _patch_embeddings(monkeypatch)
    _patch_no_openai(monkeypatch)
    _patch_extractors(monkeypatch)

    await run_ingestion_pipeline("doc-m8-s1", DOC_TEXT, "job-m8-s1")
    await _dream("job-m8-s1")

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        entities = await get_entity_graph(session, is_latest=True, limit=200)
        events = await get_event_graph(session, is_latest=True, limit=200)
        participation = await get_participation_graph(session, limit=200)

    assert len(entities.nodes) == 2
    assert {node.caption for node in entities.nodes} == {ENTITY_A, ENTITY_B}
    assert all(node.properties.get("type") == "entity" for node in entities.nodes)
    assert entities.relationships == []

    assert len(events.nodes) == 1
    assert events.nodes[0].caption == EVENT_NAME
    assert events.nodes[0].properties.get("type") == "event"

    assert len(participation.relationships) == 2
    assert all(rel.type == "participates" for rel in participation.relationships)
    part_types = {node.properties.get("type") for node in participation.nodes}
    assert part_types == {"entity", "event"}

    merged = await _count(
        "MATCH (n:Node) WHERE n.merged_into IS NULL AND n.type = 'entity' RETURN count(n) AS n"
    )
    assert merged == 2


@pytest.mark.enable_node_extraction
@pytest.mark.asyncio
async def test_scenario2_second_ingest_dedups_entities_and_events(neo4j_ready, monkeypatch):
    _patch_embeddings(monkeypatch)
    _patch_no_openai(monkeypatch)
    _patch_extractors(monkeypatch)

    await run_ingestion_pipeline("doc-m8-s2a", DOC_TEXT, "job-m8-s2a")
    await _dream("job-m8-s2a")

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        first_entities = await get_entity_graph(session, is_latest=True, limit=200)
        first_events = await get_event_graph(session, is_latest=True, limit=200)
    assert len(first_entities.nodes) == 2
    assert len(first_events.nodes) == 1

    await run_ingestion_pipeline("doc-m8-s2b", DOC_TEXT, "job-m8-s2b")
    await _dream("job-m8-s2b")

    async with driver.session() as session:
        entities = await get_entity_graph(session, is_latest=True, limit=200)
        events = await get_event_graph(session, is_latest=True, limit=200)
        participation = await get_participation_graph(session, limit=200)

    assert len(entities.nodes) == 2
    assert len(events.nodes) == 1
    assert len(participation.relationships) == 2

    dup_entities = await _count(
        "MATCH (n:Node {type:'entity'}) WHERE n.merged_into IS NOT NULL RETURN count(n) AS n"
    )
    dup_events = await _count(
        "MATCH (n:Node {type:'event'}) WHERE n.merged_into IS NOT NULL RETURN count(n) AS n"
    )
    assert dup_entities == 2
    assert dup_events == 1


@pytest.mark.enable_node_extraction
@pytest.mark.asyncio
async def test_scenario4_concept_neighbors_include_entity_and_event(neo4j_ready, monkeypatch):
    _patch_embeddings(monkeypatch)
    _patch_no_openai(monkeypatch)
    _patch_extractors(monkeypatch, concepts=["technology"])

    await run_ingestion_pipeline("doc-m8-s4", DOC_TEXT, "job-m8-s4")

    concept_id = compute_hash_id("technology")
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        graph = await get_concept_neighbors(session, concept_id)

    types = {node.properties.get("type") for node in graph.nodes}
    assert "entity" in types
    assert "event" in types
    assert "concept" in types
    names = {node.caption for node in graph.nodes}
    assert ENTITY_A in names or ENTITY_B in names
    assert EVENT_NAME in names
