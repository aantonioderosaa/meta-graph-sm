"""Fase 21.2–F21.4: retractions scoped to source. FakeSession, no Docker."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core import event_bus
from app.models.kernel import RelationKernelType
from app.models.node_extraction import (
    EntityExtractionResult,
    EventEntityExtractionResult,
    EventRelationExtractionResult,
    PairRelationDecision,
)
from app.pipeline.chunking import Chunk
from app.pipeline.context_retrieval import (
    FACTS_FROM_SOURCE_NODES_CYPHER,
    FACTS_FROM_SOURCE_RELS_CYPHER,
)
from app.pipeline.ingestion import CREATE_CONTRADICTS_CYPHER, process_chunk_node_extraction
from app.pipeline.pending_hypothesis import (
    MERGE_HYPOTHESIS_CYPHER,
    READ_HYPOTHESIS_CYPHER,
)
from app.pipeline.retraction import (
    UNIDENTIFIED_SOURCE_GAP,
    identify_source_doc_id,
    maybe_resolve_retraction_scope,
    resolve_retraction_scope,
)

JOB_ID = "job-f21-r"
DOC_A = "doc-source-a"
DOC_B = "doc-source-b"
ALICE, ACME, BOB, ROME = "alice", "acme", "bob", "rome"
REL_A = "rel-alice-acme"
REL_B = "rel-bob-rome"


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


class RetractionGraph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.chunks: dict[str, dict] = {}
        self.derived_from: list[tuple[str, str]] = []
        self.relations: list[dict] = []
        self.famiglia: list[dict] = []
        self.hypotheses: dict[str, dict] = {}

    def add_chunk(self, chunk_id: str, doc_id: str) -> None:
        self.chunks[chunk_id] = {"id": chunk_id, "doc_id": doc_id}

    def add_node(self, node_id: str, name: str, *, doc_id: str) -> None:
        self.nodes[node_id] = {"id": node_id, "name": name, "merged_into": None, "type": "entity"}
        cid = f"chunk-{doc_id}"
        if cid not in self.chunks:
            self.add_chunk(cid, doc_id)
        self.derived_from.append((node_id, cid))

    def add_relation(
        self,
        rel_id: str,
        from_id: str,
        to_id: str,
        relation: str,
        *,
        kernel_parent: str,
    ) -> None:
        self.relations.append(
            {
                "id": rel_id,
                "from_id": from_id,
                "to_id": to_id,
                "from_name": self.nodes.get(from_id, {}).get("name"),
                "to_name": self.nodes.get(to_id, {}).get("name"),
                "relation": relation,
                "normalized_relation": relation,
                "kernel_parent": kernel_parent,
                "is_latest": True,
            }
        )

    def facts_for(self, doc_id: str) -> tuple[list[dict], list[dict]]:
        chunk_ids = {cid for cid, ch in self.chunks.items() if ch["doc_id"] == doc_id}
        node_ids = {nid for nid, cid in self.derived_from if cid in chunk_ids}
        nodes = [
            {
                "id": nid,
                "name": self.nodes[nid]["name"],
                "summary": None,
                "type": "entity",
                "kernel_category": None,
                "created_at": None,
                "member_of": None,
                "member_of_name": None,
                "chunk_ids": [cid for n, cid in self.derived_from if n == nid],
            }
            for nid in node_ids
            if nid in self.nodes
        ]
        rels = [
            {
                "id": rel["id"],
                "from_id": rel["from_id"],
                "from_name": rel.get("from_name"),
                "to_id": rel["to_id"],
                "to_name": rel.get("to_name"),
                "relation": rel["relation"],
                "normalized_relation": rel.get("normalized_relation"),
                "kernel_parent": rel.get("kernel_parent"),
                "is_latest": rel.get("is_latest"),
                "valid_time": None,
                "system_time": None,
                "provenance": None,
                "witnesses_a": [],
                "witnesses_b": [],
                "witness_text": None,
            }
            for rel in self.relations
            if rel["from_id"] in node_ids or rel["to_id"] in node_ids
        ]
        return nodes, rels


class RetractionSession:
    def __init__(self, graph: RetractionGraph | None = None) -> None:
        self.graph = graph or RetractionGraph()
        self.calls: list[tuple[str, dict]] = []

    async def run(self, cypher, **kwargs):
        self.calls.append((cypher, kwargs))
        g = self.graph
        if cypher == FACTS_FROM_SOURCE_NODES_CYPHER:
            nodes, _rels = g.facts_for(kwargs["doc_id"])
            return FakeResult(nodes)
        if cypher == FACTS_FROM_SOURCE_RELS_CYPHER:
            _nodes, rels = g.facts_for(kwargs["doc_id"])
            return FakeResult(rels)
        if cypher == CREATE_CONTRADICTS_CYPHER:
            g.famiglia.append(
                {
                    "src": kwargs["left_id"],
                    "dst": kwargs["right_id"],
                    "rel_type": "CONTRADICTS",
                    "subject_id": kwargs.get("subject_id"),
                    "relation": kwargs.get("relation"),
                    "kernel_parent": kwargs.get("kernel_parent"),
                }
            )
            return FakeResult([])
        if cypher == READ_HYPOTHESIS_CYPHER:
            hyp = g.hypotheses.get(kwargs["id"])
            return FakeResult([dict(hyp)] if hyp else [])
        if cypher == MERGE_HYPOTHESIS_CYPHER:
            hid = kwargs["id"]
            existing = g.hypotheses.get(hid, {})
            g.hypotheses[hid] = {**existing, **kwargs}
            return FakeResult([])
        return FakeResult([])


@pytest.fixture(autouse=True)
def _stub_embed(monkeypatch):
    monkeypatch.setattr("app.pipeline.embeddings.embed", lambda _text: [0.0] * 768)
    event_bus.reset_event_bus()
    yield
    event_bus.reset_event_bus()


def _two_source_graph() -> RetractionGraph:
    graph = RetractionGraph()
    graph.add_node(ALICE, "Alice", doc_id=DOC_A)
    graph.add_node(ACME, "Acme", doc_id=DOC_A)
    graph.add_node(BOB, "Bob", doc_id=DOC_B)
    graph.add_node(ROME, "Rome", doc_id=DOC_B)
    graph.add_relation(
        REL_A,
        ALICE,
        ACME,
        "works_at",
        kernel_parent=RelationKernelType.SocialeIntenzionale.value,
    )
    graph.add_relation(
        REL_B,
        BOB,
        ROME,
        "lives_in",
        kernel_parent=RelationKernelType.Spaziale.value,
    )
    return graph


def _identifiable_chunk() -> Chunk:
    return Chunk(
        id="chunk-retract-a",
        doc_id=DOC_A,
        text="Tutto quello che ti ho detto finora è falso.",
    )


def _unidentifiable_chunk() -> Chunk:
    return Chunk(
        id="chunk-retract-bare",
        doc_id=DOC_A,
        text="Tutto è falso.",
    )


@pytest.mark.asyncio
async def test_identifiable_source_famiglia_b_only_on_that_source():
    graph = _two_source_graph()
    session = RetractionSession(graph)
    result = await resolve_retraction_scope(session, _identifiable_chunk())

    assert result.identifiable is True
    assert result.source_doc_id == DOC_A
    assert REL_A in result.touched_relation_ids
    assert REL_B not in result.touched_relation_ids
    assert result.hypothesis_id is None

    contradicts = [e for e in graph.famiglia if e["rel_type"] == "CONTRADICTS"]
    assert contradicts
    endpoints = {(e["src"], e["dst"]) for e in contradicts}
    assert (ALICE, ACME) in endpoints
    assert (BOB, ROME) not in endpoints
    assert not any(e["src"] == BOB or e["dst"] == ROME for e in contradicts)
    assert not any("DELETE" in cy for cy, _ in session.calls)
    assert not any("CorpusContext" in cy for cy, _ in session.calls)


@pytest.mark.asyncio
async def test_unidentifiable_source_opens_hypothesis_zero_fanout():
    graph = _two_source_graph()
    session = RetractionSession(graph)
    result = await resolve_retraction_scope(session, _unidentifiable_chunk())

    assert result.identifiable is False
    assert result.source_doc_id is None
    assert result.touched_relation_ids == ()
    assert result.hypothesis_id
    hyp = graph.hypotheses[result.hypothesis_id]
    assert hyp["status"] == "open"
    assert hyp["evidence_gap"] == UNIDENTIFIED_SOURCE_GAP
    assert hyp["evidence_gap"] == "fonte non identificata"
    assert graph.famiglia == []
    assert not any(cy == FACTS_FROM_SOURCE_RELS_CYPHER for cy, _ in session.calls)
    assert not any(cy == FACTS_FROM_SOURCE_NODES_CYPHER for cy, _ in session.calls)
    assert not any(cy == CREATE_CONTRADICTS_CYPHER for cy, _ in session.calls)
    assert any(cy == MERGE_HYPOTHESIS_CYPHER for cy, _ in session.calls)


def test_retraction_never_matches_corpus_context():
    source = Path(resolve_retraction_scope.__code__.co_filename).read_text(encoding="utf-8")
    assert "CorpusContext" not in source
    assert "update_corpus_context" not in source
    assert "facts_from_source" in source
    assert "DETACH DELETE" not in source


@pytest.mark.asyncio
async def test_retraction_cypher_calls_never_touch_corpus_context():
    graph = _two_source_graph()
    session = RetractionSession(graph)
    await resolve_retraction_scope(session, _identifiable_chunk())
    await resolve_retraction_scope(session, _unidentifiable_chunk())
    for cypher, _kwargs in session.calls:
        assert "CorpusContext" not in cypher
        assert "update_corpus_context" not in cypher
        compact = " ".join(cypher.split()).casefold()
        assert ":corpuscontext" not in compact.replace(" ", "")


@pytest.mark.asyncio
async def test_maybe_retraction_flag_off_is_noop(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.ENABLE_CONTEXT_LAYER", False)
    monkeypatch.setattr("app.pipeline.retraction.settings.ENABLE_CONTEXT_LAYER", False)
    session = RetractionSession(_two_source_graph())
    out = await maybe_resolve_retraction_scope(session, _identifiable_chunk())
    assert out is None
    assert session.calls == []


def test_this_document_deictic_identifies_chunk_doc():
    chunk = _identifiable_chunk()
    assert identify_source_doc_id(chunk) == DOC_A
    assert identify_source_doc_id(_unidentifiable_chunk()) is None


@pytest.mark.enable_node_extraction
@pytest.mark.asyncio
async def test_ingest_flag_off_does_not_run_retraction_hook(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.ENABLE_CONTEXT_LAYER", False)
    monkeypatch.setattr("app.pipeline.ingestion.settings.ENABLE_CONTEXT_LAYER", False)
    monkeypatch.setattr("app.pipeline.retraction.settings.ENABLE_CONTEXT_LAYER", False)

    async def mock_entities(_text, **_kwargs):
        return EntityExtractionResult(entities=[])

    async def mock_pair(*_args, **_kwargs):
        return PairRelationDecision(related=False)

    async def mock_empty(*_args, **_kwargs):
        return EventEntityExtractionResult(participations=[])

    async def mock_rels(*_args, **_kwargs):
        return EventRelationExtractionResult(triples=[])

    monkeypatch.setattr("app.pipeline.node_extraction.extract_entities", mock_entities)
    monkeypatch.setattr("app.pipeline.node_extraction.extract_pair_relation", mock_pair)
    monkeypatch.setattr("app.pipeline.node_extraction.extract_event_entities", mock_empty)
    monkeypatch.setattr("app.pipeline.node_extraction.extract_event_relations", mock_rels)

    session = RetractionSession()
    await process_chunk_node_extraction(session, _identifiable_chunk(), DOC_A, JOB_ID)
    assert not any(cy == FACTS_FROM_SOURCE_RELS_CYPHER for cy, _ in session.calls)
    assert not any(cy == CREATE_CONTRADICTS_CYPHER for cy, _ in session.calls)
    assert not any(cy == MERGE_HYPOTHESIS_CYPHER for cy, _ in session.calls)
    assert not any("CorpusContext" in cy for cy, _ in session.calls)
