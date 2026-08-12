"""R1.2–R1.5 — relation candidates and pair dedup (no GDS required)."""

from __future__ import annotations

import math
from unittest.mock import AsyncMock

import pytest
from testcontainers.community.neo4j import Neo4jContainer

from app.core import neo4j_client
from app.db.schema import apply_schema_with_driver
from app.pipeline.relations import (
    find_candidates,
    find_chunk_local_candidates,
    find_doc_local_candidates,
)

NEO4J_IMAGE = "neo4j:5.24-community"
EMBEDDING_DIM = 768


@pytest.fixture(scope="module")
def neo4j_container():
    # Vector index only — no GDS plugin (avoids slow/fragile plugin download).
    container = Neo4jContainer(NEO4J_IMAGE)
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
              dreamed: false,
              source_doc_id: $doc_id,
              embedding: $embedding,
              created_at: datetime()
            })
            """,
            id=fact_id,
            text=text,
            is_latest=is_latest,
            doc_id=doc_id,
            embedding=embedding,
        )


async def _link_fact_to_chunk(
    *,
    fact_id: str,
    chunk_id: str,
    doc_id: str = "doc-chunk",
    chunk_text: str = "shared chunk",
) -> None:
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        await session.run(
            """
            MERGE (c:Chunk {id: $chunk_id})
            ON CREATE SET
              c.doc_id = $doc_id,
              c.text = $chunk_text,
              c.embedding = $embedding,
              c.created_at = datetime()
            WITH c
            MATCH (f:Fact {id: $fact_id})
            MERGE (f)-[:DERIVED_FROM]->(c)
            """,
            chunk_id=chunk_id,
            doc_id=doc_id,
            chunk_text=chunk_text,
            embedding=_unit_vector(0),
            fact_id=fact_id,
        )


@pytest.mark.asyncio
async def test_same_chunk_orthogonal_facts_are_candidates(neo4j_ready):
    """Same-chunk facts appear as candidates even when outside embedding top-k."""
    probe_emb = _unit_vector(0)
    shared_ids = [f"chunk-f{i}" for i in range(5)]
    embeddings = [probe_emb] + [_unit_vector(100 + i) for i in range(1, 5)]

    for fact_id, emb in zip(shared_ids, embeddings, strict=True):
        await _create_fact(
            fact_id=fact_id,
            text=f"Orthogonal fact {fact_id}.",
            embedding=emb,
        )
        await _link_fact_to_chunk(fact_id=fact_id, chunk_id="chunk-shared")

    for i in range(15):
        await _create_fact(
            fact_id=f"distractor-{i}",
            text=f"Distractor {i}.",
            embedding=_similar_vector(probe_emb, epsilon=0.01 + i * 0.001),
        )

    await _await_vector_index()

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        candidates = await find_candidates(session, "chunk-f0", probe_emb, k=10)

    candidate_ids = {c.id for c in candidates}
    siblings = {f"chunk-f{i}" for i in range(1, 5)}
    assert siblings.issubset(candidate_ids)


@pytest.mark.asyncio
async def test_chunk_local_empty_without_derived_from(neo4j_ready):
    """Fact without DERIVED_FROM → empty chunk-local source, no exception."""
    await _create_fact(
        fact_id="orphan",
        text="No provenance.",
        embedding=_unit_vector(7),
    )
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        chunk_local = await find_chunk_local_candidates(session, "orphan")

    assert chunk_local == []


@pytest.mark.asyncio
async def test_same_doc_different_chunks_are_candidates(neo4j_ready):
    """R1.3: same source_doc_id, different chunks, dissimilar embeddings → still candidates."""
    probe_emb = _unit_vector(0)
    await _create_fact(
        fact_id="doc-a",
        text="Fact A in chunk 1.",
        embedding=probe_emb,
        doc_id="shared-doc",
    )
    await _link_fact_to_chunk(fact_id="doc-a", chunk_id="chunk-a", doc_id="shared-doc")

    await _create_fact(
        fact_id="doc-b",
        text="Fact B in chunk 2.",
        embedding=_unit_vector(50),
        doc_id="shared-doc",
    )
    await _link_fact_to_chunk(fact_id="doc-b", chunk_id="chunk-b", doc_id="shared-doc")

    for i in range(15):
        await _create_fact(
            fact_id=f"distractor-doc-{i}",
            text=f"Distractor {i}.",
            embedding=_similar_vector(probe_emb, epsilon=0.01 + i * 0.001),
            doc_id=f"other-doc-{i}",
        )

    await _await_vector_index()

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        candidates = await find_candidates(
            session,
            "doc-a",
            probe_emb,
            k=10,
            source_doc_id="shared-doc",
        )

    assert "doc-b" in {c.id for c in candidates}


@pytest.mark.asyncio
async def test_empty_doc_id_skips_doc_local_source(neo4j_ready):
    """R1.3: empty doc_id → doc-local source yields nothing (no false positives)."""
    await _create_fact(
        fact_id="blank-a",
        text="Blank doc A.",
        embedding=_unit_vector(3),
        doc_id="",
    )
    await _create_fact(
        fact_id="blank-b",
        text="Blank doc B.",
        embedding=_unit_vector(4),
        doc_id="",
    )

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        doc_local = await find_doc_local_candidates(session, "blank-a", "")

    assert doc_local == []


@pytest.mark.asyncio
async def test_reciprocal_pair_classified_once_per_cycle(neo4j_ready, monkeypatch):
    """R1.5: A↔B same-chunk reciprocal candidates → classify_relation called once."""
    from app.models.relations import RelationClassification, RelationLabel
    from app.pipeline.dreaming import run_dreaming_pipeline

    await _create_fact(
        fact_id="pair-a",
        text="The wind blew hard.",
        embedding=_unit_vector(10),
        doc_id="tale",
    )
    await _create_fact(
        fact_id="pair-b",
        text="The sun shone warmly.",
        embedding=_unit_vector(11),
        doc_id="tale",
    )
    await _link_fact_to_chunk(fact_id="pair-a", chunk_id="tale-chunk", doc_id="tale")
    await _link_fact_to_chunk(fact_id="pair-b", chunk_id="tale-chunk", doc_id="tale")
    await _await_vector_index()

    # Avoid GDS: treat both as singleton undreamed facts.
    async def mock_groups(doc_id=None):
        _ = doc_id
        return [["pair-a"], ["pair-b"]]

    calls: list[tuple[str, str]] = []

    async def mock_classify(n_text, v_text, job_id=None, **kwargs):
        _ = job_id, kwargs
        calls.append((n_text, v_text))
        return RelationClassification(relation=RelationLabel.none)

    monkeypatch.setattr(
        "app.pipeline.dreaming.grouping.group_fresh_facts", mock_groups
    )
    monkeypatch.setattr(
        "app.pipeline.dreaming.relations.classify_relation", mock_classify
    )
    monkeypatch.setattr(
        "app.pipeline.dreaming.reconcile.reconcile_scoped",
        AsyncMock(return_value=0),
    )

    await run_dreaming_pipeline("job-r15-dedup")

    pair_calls = [
        c
        for c in calls
        if {c[0], c[1]}
        == {"The wind blew hard.", "The sun shone warmly."}
    ]
    assert len(pair_calls) == 1


@pytest.mark.asyncio
async def test_classify_receives_locality_flags_from_via(neo4j_ready, monkeypatch):
    """R2.4: same_chunk/same_doc flags follow Candidate.via (chunk vs doc vs embedding)."""
    from app.models.relations import RelationClassification, RelationLabel
    from app.pipeline.dreaming import run_dreaming_pipeline

    probe_emb = _unit_vector(0)
    await _create_fact(
        fact_id="n-local",
        text="Fact N local.",
        embedding=probe_emb,
        doc_id="doc-local",
    )
    # Chunk sibling (orthogonal emb) → via=chunk
    await _create_fact(
        fact_id="v-chunk",
        text="Fact V chunk.",
        embedding=_unit_vector(40),
        doc_id="doc-local",
    )
    await _link_fact_to_chunk(fact_id="n-local", chunk_id="c-shared", doc_id="doc-local")
    await _link_fact_to_chunk(fact_id="v-chunk", chunk_id="c-shared", doc_id="doc-local")

    # Doc sibling on different chunk (orthogonal) → via=doc
    await _create_fact(
        fact_id="v-doc",
        text="Fact V doc.",
        embedding=_unit_vector(41),
        doc_id="doc-local",
    )
    await _link_fact_to_chunk(fact_id="v-doc", chunk_id="c-other", doc_id="doc-local")

    # Embedding neighbor (similar, different doc/chunk) → via=embedding, no locality flags
    await _create_fact(
        fact_id="v-emb",
        text="Fact V emb.",
        embedding=_similar_vector(probe_emb, epsilon=0.01),
        doc_id="other-doc",
    )

    # Saturate embedding top-k so chunk/doc siblings are not labeled via=embedding.
    for i in range(15):
        await _create_fact(
            fact_id=f"distractor-r24-{i}",
            text=f"Distractor R24 {i}.",
            embedding=_similar_vector(probe_emb, epsilon=0.02 + i * 0.001),
            doc_id=f"dist-doc-{i}",
        )

    await _await_vector_index()

    async def mock_groups(doc_id=None):
        _ = doc_id
        return [["n-local"]]

    flag_by_v: dict[str, tuple[bool, bool]] = {}

    async def mock_classify(
        n_text,
        v_text,
        job_id=None,
        *,
        same_chunk: bool = False,
        same_doc: bool = False,
    ):
        _ = n_text, job_id
        flag_by_v[v_text] = (same_chunk, same_doc)
        return RelationClassification(relation=RelationLabel.none)

    monkeypatch.setattr(
        "app.pipeline.dreaming.grouping.group_fresh_facts", mock_groups
    )
    monkeypatch.setattr(
        "app.pipeline.dreaming.relations.classify_relation", mock_classify
    )
    monkeypatch.setattr(
        "app.pipeline.dreaming.reconcile.reconcile_scoped",
        AsyncMock(return_value=0),
    )

    await run_dreaming_pipeline("job-r24-flags")

    assert flag_by_v.get("Fact V chunk.") == (True, False)
    assert flag_by_v.get("Fact V doc.") == (False, True)
    assert flag_by_v.get("Fact V emb.") == (False, False)
