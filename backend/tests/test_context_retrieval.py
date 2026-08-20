"""Fase 19: metadata-rich retrieval substrate. Docker required for indexes."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.core import event_bus, neo4j_client
from app.db.schema import apply_schema_with_driver
from app.models.kernel import EntityKernelType, RelationKernelType
from app.pipeline.chunking import Chunk
from app.pipeline.context_retrieval import (
    FACTS_FROM_SOURCE_NODES_CYPHER,
    FACTS_FROM_SOURCE_RELS_CYPHER,
    NODE_CONCEPT_FULLTEXT_CYPHER,
    NODE_RELATIONS_CYPHER,
    NODE_SUMMARY_FULLTEXT_CYPHER,
    NODE_SUMMARY_VECTOR_CYPHER,
    RELATION_FULLTEXT_CYPHER,
    RELATION_WITNESS_FULLTEXT_CYPHER,
    RelationSnapshot,
    RetrievalHit,
    facts_from_source,
    get_domain_dictionary,
    get_metadata,
    get_node_relations,
    get_relations,
    search_fulltext,
    search_vector,
)
from app.pipeline.ingestion import (
    CREATE_NODE_CYPHER,
    CREATE_NODE_RELATION_CYPHER,
    write_chunk,
    write_node,
    write_node_relation,
)
from tests.neo4j_gds import neo4j_gds_container

EMBEDDING_DIM = 768
DOC_ID = "doc-f19"
CHUNK_ID = "chunk-f19"
ALICE_ID = "alice-f19"
BOB_ID = "bob-f19"
CONCEPT_ID = "concept-coach-f19"
ALICE_NAME = "Alice"
BOB_NAME = "Bob"
ALICE_SUMMARY = "veteran coach based in Milan"
BOB_SUMMARY = "forward who plays for Acme"
WITNESS_A = "Alice confirmed"
WITNESS_B = "Bob agreed"
RELATION_TEXT = "coaches"
JOB_ID = "job-f19"

RETRIEVAL_INDEXES = (
    "node_summary_embedding",
    "node_summary_fulltext",
    "relation_witness_fulltext",
)


class FakeResult:
    def __init__(self, records: list[dict] | None = None):
        self._records = records or []

    async def single(self):
        return self._records[0] if self._records else None

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for record in self._records:
            yield record


class FakeSession:
    def __init__(self, queue: list[list[dict]] | None = None):
        self.calls: list[tuple[str, dict]] = []
        self._queue = list(queue or [])

    async def run(self, cypher, **kwargs):
        self.calls.append((cypher, kwargs))
        records = self._queue.pop(0) if self._queue else []
        return FakeResult(records)


@dataclass(frozen=True)
class SeededGraph:
    alice_id: str
    bob_id: str
    concept_id: str
    doc_id: str


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
    _patch_embeddings(monkeypatch)

    apply_schema_with_driver(neo4j_container.get_driver())
    await neo4j_client.close_neo4j_driver()
    await neo4j_client.init_neo4j_driver()

    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        await session.run("MATCH (n) DETACH DELETE n")
        for name in RETRIEVAL_INDEXES:
            await session.run(f"CALL db.awaitIndex('{name}', 300)")

    yield neo4j_container

    async with driver.session() as session:
        await session.run("MATCH (n) DETACH DELETE n")
    await neo4j_client.close_neo4j_driver()


@pytest.fixture(autouse=True)
def clean_event_bus():
    event_bus.reset_event_bus()
    yield
    event_bus.reset_event_bus()


def _unit_vector(text: str) -> list[float]:
    known = {
        ALICE_SUMMARY: 0,
        BOB_SUMMARY: 1,
        ALICE_NAME: 2,
        BOB_NAME: 3,
        "": 4,
    }
    vec = [0.0] * EMBEDDING_DIM
    if text in known:
        vec[known[text]] = 1.0
        return vec
    idx = int(hashlib.md5(text.encode("utf-8")).hexdigest(), 16) % EMBEDDING_DIM
    vec[idx] = 1.0
    return vec


def _patch_embeddings(monkeypatch) -> None:
    monkeypatch.setattr("app.pipeline.embeddings.embed", _unit_vector)
    monkeypatch.setattr(
        "app.pipeline.embeddings.embed_batch",
        lambda texts: [_unit_vector(text) for text in texts],
    )


async def _async_noop(*_args, **_kwargs) -> None:
    return None


async def _graph_counts(session) -> tuple[int, int]:
    node_row = await (await session.run("MATCH (n) RETURN count(n) AS n")).single()
    rel_row = await (await session.run("MATCH ()-[r]->() RETURN count(r) AS n")).single()
    assert node_row is not None and rel_row is not None
    return int(node_row["n"]), int(rel_row["n"])


async def _seed_graph(session) -> SeededGraph:
    await write_chunk(
        session,
        Chunk(id=CHUNK_ID, doc_id=DOC_ID, text="Alice coaches Bob in Milan."),
        _unit_vector("chunk"),
        JOB_ID,
    )
    await write_node(
        session,
        node_id=ALICE_ID,
        name=ALICE_NAME,
        node_type="entity",
        chunk_id=CHUNK_ID,
        embedding=_unit_vector(ALICE_NAME),
        job_id=JOB_ID,
        summary=ALICE_SUMMARY,
        kernel_category=EntityKernelType.Agente.value,
    )
    await write_node(
        session,
        node_id=BOB_ID,
        name=BOB_NAME,
        node_type="entity",
        chunk_id=CHUNK_ID,
        embedding=_unit_vector(BOB_NAME),
        job_id=JOB_ID,
        summary=BOB_SUMMARY,
        kernel_category=EntityKernelType.Agente.value,
    )
    await write_node_relation(
        session,
        head_id=ALICE_ID,
        tail_id=BOB_ID,
        relation=RELATION_TEXT,
        normalized_relation=RELATION_TEXT,
        head_name=ALICE_NAME,
        tail_name=BOB_NAME,
        kernel_parent=RelationKernelType.SocialeIntenzionale,
        witness_source=WITNESS_A,
        witness_target=WITNESS_B,
        valid_time="2024",
        provenance={"doc_id": DOC_ID},
    )
    await session.run(
        """
        CREATE (c:Concept {id: $cid, name: $name, kernel_category: $cat})
        WITH c
        MATCH (a:Node {id: $alice}), (b:Node {id: $bob})
        CREATE (a)-[:MEMBER_OF]->(c)
        CREATE (b)-[:MEMBER_OF]->(c)
        """,
        cid=CONCEPT_ID,
        name="Coach",
        cat=EntityKernelType.Agente.value,
        alice=ALICE_ID,
        bob=BOB_ID,
    )
    for name in RETRIEVAL_INDEXES:
        await session.run(f"CALL db.awaitIndex('{name}', 300)")
    return SeededGraph(
        alice_id=ALICE_ID,
        bob_id=BOB_ID,
        concept_id=CONCEPT_ID,
        doc_id=DOC_ID,
    )


@pytest.fixture
async def seeded_graph(neo4j_ready) -> SeededGraph:
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        return await _seed_graph(session)


def test_module_is_read_only_and_has_no_llm():
    source = Path(__file__).resolve().parents[1].joinpath(
        "app", "pipeline", "context_retrieval.py"
    ).read_text(encoding="utf-8")
    assert "call_structured" not in source
    assert "openai" not in source.lower()
    cypher = "\n".join(
        (
            NODE_SUMMARY_FULLTEXT_CYPHER,
            NODE_CONCEPT_FULLTEXT_CYPHER,
            RELATION_WITNESS_FULLTEXT_CYPHER,
            RELATION_FULLTEXT_CYPHER,
            NODE_SUMMARY_VECTOR_CYPHER,
            NODE_RELATIONS_CYPHER,
            FACTS_FROM_SOURCE_NODES_CYPHER,
            FACTS_FROM_SOURCE_RELS_CYPHER,
        )
    )
    assert "MERGE " not in cypher
    assert "DELETE " not in cypher
    assert "DETACH " not in cypher
    assert "CREATE (" not in cypher
    compact = " ".join(NODE_RELATIONS_CYPHER.split())
    assert "$dup_id" not in compact
    assert "$canon_id" not in compact


@pytest.mark.asyncio
async def test_write_node_always_writes_summary_embedding(monkeypatch):
    seen: list[str] = []

    def fake_embed(text: str) -> list[float]:
        seen.append(text)
        return [0.25] * EMBEDDING_DIM

    monkeypatch.setattr("app.pipeline.embeddings.embed", fake_embed)
    session = FakeSession()
    name_emb = [0.9] * EMBEDDING_DIM
    await write_node(
        session,
        node_id="n1",
        name="Alice",
        node_type="entity",
        chunk_id="c1",
        embedding=name_emb,
        job_id=JOB_ID,
        summary="hello summary",
        kernel_category="Agente",
    )
    cypher, kwargs = session.calls[0]
    assert cypher == CREATE_NODE_CYPHER
    assert "summary_embedding: $summary_emb" in cypher
    assert kwargs["emb"] == name_emb
    assert kwargs["summary_emb"] == [0.25] * EMBEDDING_DIM
    assert kwargs["summary"] == "hello summary"
    assert seen == ["hello summary"]


@pytest.mark.asyncio
async def test_write_node_embeds_empty_summary(monkeypatch):
    seen: list[str] = []

    def fake_embed(text: str) -> list[float]:
        seen.append(text)
        return [0.0] * EMBEDDING_DIM

    monkeypatch.setattr("app.pipeline.embeddings.embed", fake_embed)
    session = FakeSession()
    await write_node(
        session,
        node_id="n-empty",
        name="Bare",
        node_type="entity",
        chunk_id="c1",
        embedding=[0.1] * EMBEDDING_DIM,
        job_id=JOB_ID,
    )
    _cypher, kwargs = session.calls[0]
    assert kwargs["summary"] == ""
    assert kwargs["summary_emb"] == [0.0] * EMBEDDING_DIM
    assert seen == [""]


@pytest.mark.asyncio
async def test_write_node_relation_writes_witness_text(monkeypatch):
    monkeypatch.setattr("app.pipeline.embeddings.embed", lambda _t: [0.5] * EMBEDDING_DIM)
    monkeypatch.setattr(
        "app.pipeline.ingestion.deposit_from_asserted_fact",
        _async_noop,
    )
    session = FakeSession()
    await write_node_relation(
        session,
        head_id="h1",
        tail_id="t1",
        relation="coaches",
        normalized_relation="coaches",
        kernel_parent=RelationKernelType.SocialeIntenzionale,
        witness_source=WITNESS_A,
        witness_target=WITNESS_B,
    )
    cypher, kwargs = session.calls[0]
    assert cypher == CREATE_NODE_RELATION_CYPHER
    assert "witness_text: $witness_text" in cypher
    assert kwargs["witnesses_a"] == [WITNESS_A]
    assert kwargs["witnesses_b"] == [WITNESS_B]
    assert kwargs["witness_text"] == f"{WITNESS_A} {WITNESS_B}"


@pytest.mark.asyncio
async def test_write_node_persists_queryable_summary_embedding(neo4j_ready, seeded_graph):
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        row = await (
            await session.run(
                """
                MATCH (n:Node {id: $id})
                RETURN n.summary_embedding AS emb, n.embedding AS name_emb
                """,
                id=seeded_graph.alice_id,
            )
        ).single()
        assert row is not None
        assert len(row["emb"]) == EMBEDDING_DIM
        assert row["emb"] == _unit_vector(ALICE_SUMMARY)
        assert row["name_emb"] == _unit_vector(ALICE_NAME)
        hit = await (
            await session.run(
                """
                CALL db.index.vector.queryNodes('node_summary_embedding', 1, $embedding)
                YIELD node, score
                RETURN node.id AS id, score
                """,
                embedding=_unit_vector(ALICE_SUMMARY),
            )
        ).single()
        assert hit is not None
        assert hit["id"] == seeded_graph.alice_id
        assert float(hit["score"]) > 0.99


@pytest.mark.asyncio
async def test_write_node_relation_persists_witness_text(neo4j_ready, seeded_graph):
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        row = await (
            await session.run(
                """
                MATCH (:Node {id: $alice})-[r:Relation]->(:Node {id: $bob})
                RETURN r.witness_text AS witness_text,
                       r.witnesses_a AS witnesses_a,
                       r.witnesses_b AS witnesses_b
                """,
                alice=seeded_graph.alice_id,
                bob=seeded_graph.bob_id,
            )
        ).single()
        assert row is not None
        assert row["witness_text"] == f"{WITNESS_A} {WITNESS_B}"
        assert list(row["witnesses_a"]) == [WITNESS_A]
        assert list(row["witnesses_b"]) == [WITNESS_B]


@pytest.mark.asyncio
async def test_search_fulltext_returns_summary_and_witness_metadata(
    neo4j_ready, seeded_graph
):
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        node_hits = await search_fulltext(session, "Milan")
        assert node_hits
        alice = next(hit for hit in node_hits if hit.id == seeded_graph.alice_id)
        assert isinstance(alice, RetrievalHit)
        assert alice.kind == "node"
        assert alice.summary == ALICE_SUMMARY
        assert alice.name == ALICE_NAME
        assert alice.kernel_category == EntityKernelType.Agente.value
        assert alice.member_of == seeded_graph.concept_id
        assert alice.index == "node_summary_fulltext"
        assert alice.score > 0

        rel_hits = await search_fulltext(session, "confirmed")
        assert rel_hits
        rel = next(hit for hit in rel_hits if hit.kind == "relation")
        assert rel.witness_text == f"{WITNESS_A} {WITNESS_B}"
        assert rel.relation == RELATION_TEXT
        assert rel.kernel_parent == RelationKernelType.SocialeIntenzionale.value
        assert rel.is_latest is True
        assert rel.from_id == seeded_graph.alice_id
        assert rel.to_id == seeded_graph.bob_id
        assert rel.index == "relation_witness_fulltext"
        assert WITNESS_A in rel.witnesses_a
        assert rel.provenance is not None


@pytest.mark.asyncio
async def test_search_vector_embeds_arbitrary_query(neo4j_ready, seeded_graph):
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        hits = await search_vector(session, ALICE_SUMMARY)
        assert hits
        top = hits[0]
        assert top.kind == "node"
        assert top.id == seeded_graph.alice_id
        assert top.summary == ALICE_SUMMARY
        assert top.name == ALICE_NAME
        assert top.kernel_category == EntityKernelType.Agente.value
        assert top.member_of == seeded_graph.concept_id
        assert top.index == "node_summary_embedding"
        assert top.score > 0.99
        assert seeded_graph.doc_id in top.doc_ids


@pytest.mark.asyncio
async def test_get_metadata_reuses_node_graph_engine(neo4j_ready, seeded_graph):
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        meta = await get_metadata(session, seeded_graph.alice_id)
        assert meta is not None
        assert meta.id == seeded_graph.alice_id
        assert meta.kind == "node"
        assert meta.name == ALICE_NAME
        assert meta.summary == ALICE_SUMMARY
        assert meta.kernel_category == EntityKernelType.Agente.value
        assert meta.node_type == "entity"


@pytest.mark.asyncio
async def test_get_relations_returns_relation_snapshots(neo4j_ready, seeded_graph):
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        via_wrapper = await get_relations(session, seeded_graph.alice_id)
        via_named = await get_node_relations(session, seeded_graph.alice_id)
        assert via_wrapper == via_named
        assert len(via_wrapper) == 1
        snap = via_wrapper[0]
        assert isinstance(snap, RelationSnapshot)
        assert snap.text == RELATION_TEXT
        assert snap.relation == RELATION_TEXT
        assert snap.kernel_parent == RelationKernelType.SocialeIntenzionale.value
        assert snap.is_latest is True
        assert snap.valid_time == "2024"
        assert snap.system_time is not None
        assert snap.witnesses_a == (WITNESS_A,)
        assert snap.witnesses_b == (WITNESS_B,)
        assert snap.direction == "outgoing"
        assert snap.other_id == seeded_graph.bob_id
        assert snap.other_name == BOB_NAME
        assert snap.provenance is not None
        assert snap.witness_text == f"{WITNESS_A} {WITNESS_B}"


@pytest.mark.asyncio
async def test_get_domain_dictionary_reuses_fase17(neo4j_ready, seeded_graph):
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        dictionary = await get_domain_dictionary(session, seeded_graph.concept_id)
        assert dictionary is not None
        names = {item.name for item in dictionary.items}
        assert RELATION_TEXT in names
        rel_item = next(item for item in dictionary.items if item.name == RELATION_TEXT)
        assert rel_item.kind == "relation"
        assert rel_item.kernel_parent == RelationKernelType.SocialeIntenzionale.value
        assert rel_item.count >= 1


@pytest.mark.asyncio
async def test_facts_from_source_includes_nodes_and_relations(neo4j_ready, seeded_graph):
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        facts = await facts_from_source(session, seeded_graph.doc_id)
        nodes = [fact for fact in facts if fact["kind"] == "node"]
        rels = [fact for fact in facts if fact["kind"] == "relation"]
        node_ids = {fact["id"] for fact in nodes}
        assert seeded_graph.alice_id in node_ids
        assert seeded_graph.bob_id in node_ids
        alice = next(fact for fact in nodes if fact["id"] == seeded_graph.alice_id)
        assert alice["name"] == ALICE_NAME
        assert alice["summary"] == ALICE_SUMMARY
        assert alice["kernel_category"] == EntityKernelType.Agente.value
        assert alice["member_of"] == seeded_graph.concept_id
        assert alice["doc_id"] == seeded_graph.doc_id
        assert CHUNK_ID in alice["chunk_ids"]
        assert len(rels) == 1
        rel = rels[0]
        assert rel["relation"] == RELATION_TEXT
        assert rel["kernel_parent"] == RelationKernelType.SocialeIntenzionale.value
        assert rel["is_latest"] is True
        assert rel["valid_time"] == "2024"
        assert rel["system_time"] is not None
        assert rel["witness_text"] == f"{WITNESS_A} {WITNESS_B}"
        assert rel["from_id"] == seeded_graph.alice_id
        assert rel["to_id"] == seeded_graph.bob_id
        assert rel["provenance"] is not None


@pytest.mark.asyncio
async def test_retrieval_functions_write_nothing(neo4j_ready, seeded_graph):
    driver = neo4j_client.get_driver()
    async with driver.session() as session:
        before = await _graph_counts(session)
        await search_fulltext(session, "Milan")
        await search_vector(session, ALICE_SUMMARY)
        await get_metadata(session, seeded_graph.alice_id)
        await get_relations(session, seeded_graph.alice_id)
        await get_domain_dictionary(session, seeded_graph.concept_id)
        await facts_from_source(session, seeded_graph.doc_id)
        after = await _graph_counts(session)
        assert after == before
