"""Unit tests for process_chunk_node_extraction (Macrotask 2; Fase 3 two-pass)."""

from __future__ import annotations

import pytest

from app.core.llm_client import LLMValidationError
from app.models.kernel import EntityKernelType, RelationKernelType
from app.models.node_extraction import (
    ConceptResult,
    EntityExtractionResult,
    EntityRelationExtractionResult,
    EntityRelationTriple,
    EventEntityExtractionResult,
    EventEntityParticipation,
    EventRelationExtractionResult,
    EventRelationTriple,
    ExtractedEntity,
    PairRelationDecision,
)
from app.pipeline.chunking import Chunk
from app.pipeline.ingestion import CREATE_NODE_RELATION_CYPHER, process_chunk_node_extraction

CHUNK = Chunk(id="chunk-1", doc_id="doc-1", text="Alice works at Acme.")
JOB_ID = "job-node-1"


class FakeSession:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def run(self, cypher, **kwargs):
        self.calls.append((cypher, kwargs))


def _entity_rel() -> EntityRelationExtractionResult:
    return EntityRelationExtractionResult(
        triples=[
            EntityRelationTriple(
                head="Alice",
                tail="Acme",
                relation="works at",
                kernel_parent=RelationKernelType.SocialeIntenzionale,
                witness_source="Alice",
                witness_target="Acme",
            )
        ]
    )


def _entities() -> EntityExtractionResult:
    return EntityExtractionResult(
        entities=[
            ExtractedEntity(
                name="Alice",
                summary="A person named Alice.",
                kernel_category=EntityKernelType.Agente,
            ),
            ExtractedEntity(
                name="Acme",
                summary="The company Acme.",
                kernel_category=EntityKernelType.CostruttoSociale,
            ),
        ]
    )


def _pair_related() -> PairRelationDecision:
    return PairRelationDecision(
        related=True,
        relation="works at",
        kernel_parent=RelationKernelType.SocialeIntenzionale,
        witness_source="Alice",
        witness_target="Acme",
    )


def _event_entity() -> EventEntityExtractionResult:
    return EventEntityExtractionResult(
        participations=[
            EventEntityParticipation(event="Alice joined Acme.", entities=["Alice", "Acme"])
        ]
    )


def _event_rel() -> EventRelationExtractionResult:
    return EventRelationExtractionResult(
        triples=[
            EventRelationTriple(
                head="Alice joined Acme.",
                tail="Alice works at Acme.",
                relation="before",
                kernel_parent=RelationKernelType.Temporale,
                witness_source="Alice joined Acme.",
                witness_target="Alice works at Acme.",
            )
        ]
    )


def _empty_concepts() -> ConceptResult:
    return ConceptResult(concepts=[])


def _patch_extractors(
    monkeypatch,
    *,
    entities,
    pair,
    event_entity,
    event_rel,
    concepts=None,
):
    concept_result = concepts if concepts is not None else _empty_concepts()

    async def mock_entities(chunk_text: str, job_id: str | None = None, corpus_summary: str = ""):
        _ = chunk_text, job_id, corpus_summary
        if isinstance(entities, Exception):
            raise entities
        return entities

    async def mock_pair(*_args, **_kwargs):
        if isinstance(pair, Exception):
            raise pair
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

    async def mock_entity_concepts(entity_name: str, context: str, job_id: str | None = None):
        _ = entity_name, context, job_id
        return concept_result

    async def mock_event_concepts(event_text: str, job_id: str | None = None):
        _ = event_text, job_id
        return concept_result

    monkeypatch.setattr("app.pipeline.node_extraction.extract_entities", mock_entities)
    monkeypatch.setattr("app.pipeline.node_extraction.extract_pair_relation", mock_pair)
    monkeypatch.setattr(
        "app.pipeline.node_extraction.extract_event_entities", mock_event_entity
    )
    monkeypatch.setattr(
        "app.pipeline.node_extraction.extract_event_relations", mock_event_rel
    )
    monkeypatch.setattr(
        "app.pipeline.node_extraction.extract_entity_concepts", mock_entity_concepts
    )
    monkeypatch.setattr("app.pipeline.node_extraction.extract_event_concepts", mock_event_concepts)
    monkeypatch.setattr("app.pipeline.embeddings.embed", lambda name: [0.0] * 768)


def test_entity_rel_helper_includes_witnesses_and_kernel_parent():
    triple = _entity_rel().triples[0]
    assert triple.witness_source == "Alice"
    assert triple.witness_target == "Acme"
    assert triple.kernel_parent is RelationKernelType.SocialeIntenzionale


@pytest.mark.enable_node_extraction
@pytest.mark.asyncio
async def test_all_three_extractors_write_entity_event_and_participates(monkeypatch):
    _patch_extractors(
        monkeypatch,
        entities=_entities(),
        pair=_pair_related(),
        event_entity=_event_entity(),
        event_rel=_event_rel(),
    )
    session = FakeSession()

    count = await process_chunk_node_extraction(session, CHUNK, "doc-1", JOB_ID)

    assert count > 0
    kwargs_list = [kw for _, kw in session.calls]
    assert any(kw.get("type") == "entity" for kw in kwargs_list)
    assert any(kw.get("type") == "event" for kw in kwargs_list)
    assert any(kw.get("normalized_relation") == "participates" for kw in kwargs_list)
    assert any(kw.get("relation") == "is participated by" for kw in kwargs_list)
    assert any(kw.get("relation") == "works at" for kw in kwargs_list)
    assert any(kw.get("summary") == "A person named Alice." for kw in kwargs_list)
    assert any(
        kw.get("kernel_parent") == RelationKernelType.SocialeIntenzionale.value
        for kw in kwargs_list
    )


@pytest.mark.enable_node_extraction
@pytest.mark.asyncio
async def test_one_extractor_failure_skips_that_branch_and_publishes(monkeypatch):
    published: list[tuple[str, str, str, dict]] = []

    async def spy_publish(job_id, stage, event, payload):
        published.append((job_id, stage, event, payload))

    monkeypatch.setattr("app.core.event_bus.publish", spy_publish)
    _patch_extractors(
        monkeypatch,
        entities=LLMValidationError("bad entity triples"),
        pair=_pair_related(),
        event_entity=_event_entity(),
        event_rel=_event_rel(),
    )
    session = FakeSession()

    count = await process_chunk_node_extraction(session, CHUNK, "doc-1", JOB_ID)

    assert count > 0
    kwargs_list = [kw for _, kw in session.calls]
    assert any(kw.get("type") == "event" for kw in kwargs_list)
    assert any(kw.get("normalized_relation") == "participates" for kw in kwargs_list)
    assert not any(kw.get("relation") == "works at" for kw in kwargs_list)

    failed = [item for item in published if item[2] == "llm_call_failed"]
    assert failed
    job_id, stage, event, payload = failed[0]
    assert job_id == JOB_ID
    assert stage == "node_extraction"
    assert event == "llm_call_failed"
    assert payload["item_id"] == CHUNK.id
    assert payload["error"] == "validation_error"


@pytest.mark.enable_node_extraction
@pytest.mark.asyncio
async def test_pair_llm_failure_writes_no_entity_relation(monkeypatch):
    _patch_extractors(
        monkeypatch,
        entities=_entities(),
        pair=LLMValidationError("bad pair"),
        event_entity=EventEntityExtractionResult(participations=[]),
        event_rel=EventRelationExtractionResult(triples=[]),
    )
    session = FakeSession()

    await process_chunk_node_extraction(session, CHUNK, "doc-1", JOB_ID)

    assert not any(
        cypher == CREATE_NODE_RELATION_CYPHER and kw.get("relation") == "works at"
        for cypher, kw in session.calls
    )
    assert not any(cypher == CREATE_NODE_RELATION_CYPHER for cypher, _kw in session.calls)


@pytest.mark.enable_node_extraction
@pytest.mark.asyncio
async def test_every_written_node_has_derived_from_to_chunk(monkeypatch):
    _patch_extractors(
        monkeypatch,
        entities=_entities(),
        pair=_pair_related(),
        event_entity=_event_entity(),
        event_rel=_event_rel(),
    )
    session = FakeSession()

    await process_chunk_node_extraction(session, CHUNK, "doc-1", JOB_ID)

    node_writes = [
        (cypher, kw)
        for cypher, kw in session.calls
        if kw.get("type") in {"entity", "event"}
    ]
    assert node_writes
    for cypher, kw in node_writes:
        assert "DERIVED_FROM" in cypher
        assert kw.get("chunk_id") == CHUNK.id
