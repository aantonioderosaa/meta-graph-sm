"""Fase 3 acceptance: anti-blur ingest (doc1 §2.4–§2.5, doc4 §6)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.llm_client import LLMValidationError
from app.models.kernel import EntityKernelType, RelationKernelType
from app.models.node_extraction import (
    ConceptResult,
    EntityExtractionResult,
    EntityRelationTriple,
    EventEntityExtractionResult,
    EventRelationExtractionResult,
    EventRelationTriple,
    ExtractedEntity,
    MacroDomainSummary,
    PairRelationDecision,
)
from app.pipeline.chunking import Chunk
from app.pipeline.domain_book import GENRE_NOT_TOPIC_PROMPT
from app.pipeline.ingestion import (
    CREATE_CONTRADICTS_CYPHER,
    CREATE_NODE_RELATION_CYPHER,
    MERGE_CORPUS_CONTEXT_CYPHER,
    has_required_witnesses,
    process_chunk_node_extraction,
    update_corpus_context,
)
from app.pipeline.node_extraction_prompts import build_pair_relation_prompt

CHUNK = Chunk(id="chunk-ab", doc_id="doc-ab", text="Alice works at Acme.")
JOB_ID = "job-antiblur"


class FakeSession:
    def __init__(self, existing_corpus=None):
        self.calls: list[tuple[str, dict]] = []
        self._existing_corpus = existing_corpus

    async def run(self, cypher, **kwargs):
        self.calls.append((cypher, kwargs))
        session = self

        class _Result:
            async def single(self):
                return session._existing_corpus

            def __aiter__(self):
                return self._iterate()

            async def _iterate(self):
                if False:
                    yield {}

        return _Result()


def _empty_events() -> EventEntityExtractionResult:
    return EventEntityExtractionResult(participations=[])


def _empty_event_rels() -> EventRelationExtractionResult:
    return EventRelationExtractionResult(triples=[])


def _empty_concepts() -> ConceptResult:
    return ConceptResult(concepts=[])


def _alice_acme() -> EntityExtractionResult:
    return EntityExtractionResult(
        entities=[
            ExtractedEntity(
                name="Alice",
                summary="A person named Alice who is employed.",
                kernel_category=EntityKernelType.Agente,
            ),
            ExtractedEntity(
                name="Acme",
                summary="A company called Acme.",
                kernel_category=EntityKernelType.CostruttoSociale,
            ),
        ]
    )


def _patch_two_pass(
    monkeypatch,
    *,
    entities,
    pair,
    event_entity=None,
    event_rel=None,
) -> None:
    event_entity = event_entity if event_entity is not None else _empty_events()
    event_rel = event_rel if event_rel is not None else _empty_event_rels()

    async def mock_entities(chunk_text: str, job_id: str | None = None, corpus_summary: str = ""):
        _ = chunk_text, job_id, corpus_summary
        if isinstance(entities, Exception):
            raise entities
        return entities

    async def mock_pair(*_args, **_kwargs):
        if isinstance(pair, Exception):
            raise pair
        if callable(pair):
            return pair(*_args, **_kwargs)
        return pair

    async def mock_event_entity(chunk_text: str, job_id: str | None = None):
        _ = chunk_text, job_id
        if isinstance(event_entity, Exception):
            raise event_entity
        return event_entity

    async def mock_event_rel(chunk_text: str, job_id: str | None = None):
        _ = chunk_text, job_id
        if isinstance(event_rel, Exception):
            raise event_rel
        return event_rel

    async def mock_concepts(*_args, **_kwargs):
        return _empty_concepts()

    monkeypatch.setattr("app.pipeline.node_extraction.extract_entities", mock_entities)
    monkeypatch.setattr("app.pipeline.node_extraction.extract_pair_relation", mock_pair)
    monkeypatch.setattr(
        "app.pipeline.node_extraction.extract_event_entities", mock_event_entity
    )
    monkeypatch.setattr(
        "app.pipeline.node_extraction.extract_event_relations", mock_event_rel
    )
    monkeypatch.setattr("app.pipeline.node_extraction.extract_entity_concepts", mock_concepts)
    monkeypatch.setattr("app.pipeline.node_extraction.extract_event_concepts", mock_concepts)
    monkeypatch.setattr("app.pipeline.embeddings.embed", lambda name: [0.0] * 768)


def test_pydantic_rejects_list_valued_head_and_tail():
    payload = dict(
        relation="works_at",
        kernel_parent=RelationKernelType.SocialeIntenzionale,
        witness_source="Alice",
        witness_target="Acme",
    )
    with pytest.raises(ValidationError):
        EntityRelationTriple(head=["Alice", "Bob"], tail="Acme", **payload)
    with pytest.raises(ValidationError):
        EntityRelationTriple(head="Alice", tail=["Acme", "Globex"], **payload)
    with pytest.raises(ValidationError):
        EventRelationTriple(
            head=["e1", "e2"],
            tail="e3",
            relation="before",
            kernel_parent=RelationKernelType.Temporale,
            witness_source="e1",
            witness_target="e3",
        )


def test_pydantic_rejects_missing_or_empty_witnesses():
    base = dict(
        head="Alice",
        tail="Acme",
        relation="works_at",
        kernel_parent=RelationKernelType.SocialeIntenzionale,
    )
    with pytest.raises(ValidationError):
        EntityRelationTriple(**base, witness_source="", witness_target="Acme")
    with pytest.raises(ValidationError):
        EntityRelationTriple(**base, witness_source="Alice", witness_target="")
    with pytest.raises(ValidationError):
        EntityRelationTriple(
            head="Alice",
            tail="Acme",
            relation="works_at",
            kernel_parent=RelationKernelType.SocialeIntenzionale,
        )
    with pytest.raises(ValidationError):
        PairRelationDecision(
            related=True,
            relation="works_at",
            kernel_parent=RelationKernelType.SocialeIntenzionale,
            witness_source="",
            witness_target="Acme",
        )


def test_has_required_witnesses_filter():
    assert has_required_witnesses("Alice", "Acme")
    assert not has_required_witnesses("", "Acme")
    assert not has_required_witnesses("Alice", "  ")
    assert not has_required_witnesses(None, "Acme")


@pytest.mark.enable_node_extraction
@pytest.mark.asyncio
async def test_fact_without_both_witnesses_is_not_written(monkeypatch):
    decision = PairRelationDecision.model_construct(
        related=True,
        relation="works_at",
        kernel_parent=RelationKernelType.SocialeIntenzionale,
        witness_source="",
        witness_target="Acme",
    )
    _patch_two_pass(monkeypatch, entities=_alice_acme(), pair=decision)
    session = FakeSession()

    await process_chunk_node_extraction(session, CHUNK, "doc-ab", JOB_ID)

    relation_writes = [
        cypher for cypher, _kw in session.calls if cypher == CREATE_NODE_RELATION_CYPHER
    ]
    assert relation_writes == []


@pytest.mark.enable_node_extraction
@pytest.mark.asyncio
async def test_copresence_related_false_writes_no_entity_relation(monkeypatch):
    _patch_two_pass(
        monkeypatch,
        entities=_alice_acme(),
        pair=PairRelationDecision(related=False),
    )
    session = FakeSession()

    count = await process_chunk_node_extraction(session, CHUNK, "doc-ab", JOB_ID)

    assert count == 2
    assert not any(cypher == CREATE_NODE_RELATION_CYPHER for cypher, _kw in session.calls)
    names = {kw["name"] for _cypher, kw in session.calls if kw.get("type") == "entity"}
    assert names == {"Alice", "Acme"}


def test_pair_prompt_includes_both_summaries_and_genre_rule():
    summary_a = "Alice is a tennis player."
    summary_b = "X is Alice's coach."
    _system, user = build_pair_relation_prompt(
        "Alice is coached by X.",
        "Alice",
        summary_a,
        "X",
        summary_b,
        corpus_summary="A corpus about tennis.",
    )
    assert summary_a in user
    assert summary_b in user
    assert "Alice" in user
    assert GENRE_NOT_TOPIC_PROMPT in user
    assert "A corpus about tennis." in user
    for primitive in RelationKernelType:
        assert primitive.value in user


@pytest.mark.enable_node_extraction
@pytest.mark.asyncio
async def test_conflicting_pairs_write_both_relations_and_contradicts(monkeypatch):
    entities = EntityExtractionResult(
        entities=[
            ExtractedEntity(
                name="Alice",
                summary="A person.",
                kernel_category=EntityKernelType.Agente,
            ),
            ExtractedEntity(
                name="Acme",
                summary="One employer.",
                kernel_category=EntityKernelType.CostruttoSociale,
            ),
            ExtractedEntity(
                name="Globex",
                summary="Another employer.",
                kernel_category=EntityKernelType.CostruttoSociale,
            ),
        ]
    )

    def decide(_chunk, name_a, _sa, name_b, _sb, **_kwargs):
        names = {name_a, name_b}
        if names == {"Acme", "Globex"}:
            return PairRelationDecision(related=False)
        return PairRelationDecision(
            related=True,
            relation="works_at",
            kernel_parent=RelationKernelType.SocialeIntenzionale,
            witness_source=name_a,
            witness_target=name_b,
        )

    _patch_two_pass(monkeypatch, entities=entities, pair=decide)
    session = FakeSession()

    await process_chunk_node_extraction(session, CHUNK, "doc-ab", JOB_ID)

    relation_writes = [
        kw for cypher, kw in session.calls if cypher == CREATE_NODE_RELATION_CYPHER
    ]
    assert len(relation_writes) == 2
    contradicts = [cypher for cypher, _kw in session.calls if cypher == CREATE_CONTRADICTS_CYPHER]
    assert len(contradicts) == 1
    contra_kw = [kw for cypher, kw in session.calls if cypher == CREATE_CONTRADICTS_CYPHER][0]
    assert contra_kw["relation"] == "works_at"
    assert contra_kw["kernel_parent"] == RelationKernelType.SocialeIntenzionale.value


@pytest.mark.asyncio
async def test_corpus_context_merge_is_o1_and_writes_summary(monkeypatch):
    async def fake_llm(*_args, **_kwargs):
        return MacroDomainSummary(summary_text="The corpus is about tennis players.")

    monkeypatch.setattr("app.pipeline.ingestion.call_structured", fake_llm)
    monkeypatch.setattr("app.pipeline.ingestion.embeddings.embed", lambda text: [0.1] * 8)
    session = FakeSession(existing_corpus={"summary_text": "Old summary.", "document_count": 1})

    summary = await update_corpus_context(session, "New document about Sinner.", "job-cc")

    assert summary == "The corpus is about tennis players."
    assert any(cypher == MERGE_CORPUS_CONTEXT_CYPHER for cypher, _kw in session.calls)
    merge_kw = [kw for cypher, kw in session.calls if cypher == MERGE_CORPUS_CONTEXT_CYPHER][0]
    assert merge_kw["summary_text"] == summary
    assert merge_kw["id"] == "default"
    assert "MATCH (d:Document)" not in "".join(cypher for cypher, _ in session.calls)


@pytest.mark.asyncio
async def test_corpus_context_llm_failure_keeps_previous_summary(monkeypatch):
    published: list[tuple[str, str, str, dict]] = []

    async def spy_publish(job_id, stage, event, payload):
        published.append((job_id, stage, event, payload))

    async def boom(*_args, **_kwargs):
        raise LLMValidationError("bad summary")

    monkeypatch.setattr("app.pipeline.ingestion.call_structured", boom)
    monkeypatch.setattr("app.pipeline.ingestion.embeddings.embed", lambda text: [0.1] * 8)
    monkeypatch.setattr("app.core.event_bus.publish", spy_publish)
    session = FakeSession(existing_corpus={"summary_text": "Keep me.", "document_count": 3})

    summary = await update_corpus_context(session, "Another doc.", "job-cc-fail")

    assert summary == "Keep me."
    failed = [item for item in published if item[2] == "llm_call_failed"]
    assert failed
    assert failed[0][1] == "ingestion"
    merge_kw = [kw for cypher, kw in session.calls if cypher == MERGE_CORPUS_CONTEXT_CYPHER][0]
    assert merge_kw["summary_text"] == "Keep me."


def test_pipeline_complete_remains_at_end_of_run_ingestion_pipeline():
    source = (
        Path(__file__).resolve().parents[1] / "app" / "pipeline" / "ingestion.py"
    ).read_text(encoding="utf-8")
    body = source.split("async def run_ingestion_pipeline", 1)[1]
    assert "pipeline_complete" in body
    assert body.rfind("pipeline_complete") > body.rfind("process_chunk_node_extraction")
    assert body.rfind("update_corpus_context") < body.rfind("chunking.chunk_text")
