"""Concept / Relation embeddings written at create time (Macrotask 1)."""

from __future__ import annotations

import pytest

from app.models.kernel import RelationKernelType
from app.pipeline.concepts import MERGE_CONCEPT_LINK_CYPHER, merge_concept_and_link
from app.pipeline.ingestion import CREATE_NODE_RELATION_CYPHER, write_node_relation


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
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def run(self, cypher, **kwargs):
        self.calls.append((cypher, kwargs))
        return FakeResult()


@pytest.mark.asyncio
async def test_merge_concept_writes_embedding_on_create(monkeypatch):
    monkeypatch.setattr("app.pipeline.embeddings.embed", lambda name: [0.25] * 768)
    session = FakeSession()

    await merge_concept_and_link(session, "node-1", "technology")

    assert len(session.calls) == 1
    cypher, kwargs = session.calls[0]
    assert cypher == MERGE_CONCEPT_LINK_CYPHER
    assert "ON CREATE SET" in cypher
    assert "c.embedding = $embedding" in cypher
    assert kwargs["name"] == "technology"
    assert kwargs["embedding"] == [0.25] * 768
    assert kwargs["node_id"] == "node-1"


@pytest.mark.asyncio
async def test_write_open_vocab_relation_embeds_synthetic_text(monkeypatch):
    seen: list[str] = []

    def fake_embed(text: str) -> list[float]:
        seen.append(text)
        return [0.5] * 768

    monkeypatch.setattr("app.pipeline.embeddings.embed", fake_embed)
    session = FakeSession()

    await write_node_relation(
        session,
        head_id="h1",
        tail_id="t1",
        relation="ha fondato",
        normalized_relation=None,
        head_name="Mario",
        tail_name="Acme",
        kernel_parent=RelationKernelType.SocialeIntenzionale,
    )

    assert seen == ["Mario ha fondato Acme"]
    cypher, kwargs = session.calls[0]
    assert cypher == CREATE_NODE_RELATION_CYPHER
    assert kwargs["embedding"] == [0.5] * 768
    assert kwargs["relation"] == "ha fondato"


@pytest.mark.asyncio
async def test_participates_relation_never_embeds(monkeypatch):
    def boom(_text: str) -> list[float]:
        raise AssertionError("participates must not be embedded")

    monkeypatch.setattr("app.pipeline.embeddings.embed", boom)
    session = FakeSession()

    await write_node_relation(
        session,
        head_id="event-1",
        tail_id="entity-1",
        relation="is participated by",
        normalized_relation="participates",
        head_name="product launch",
        tail_name="Alice",
        kernel_parent=RelationKernelType.Partecipativa,
    )

    cypher, kwargs = session.calls[0]
    assert cypher == CREATE_NODE_RELATION_CYPHER
    assert kwargs["embedding"] is None
    assert kwargs["normalized_relation"] == "participates"
