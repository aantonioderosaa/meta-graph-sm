"""Unit tests for two-level Concept match Cypher and MEMBER_OF helpers (Fase 4)."""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.models.kernel import KERNEL_VERSION, EntityKernelType
from app.pipeline.concepts import (
    CONCEPT_VECTOR_INDEX,
    CREATE_ANCHORED_GENRE_CYPHER,
    ENSURE_KERNEL_CATCH_ALL_CYPHER,
    FIND_CONCEPT_BY_ID_CYPHER,
    FIND_CONCEPT_EXACT_NAME_CYPHER,
    FIND_CONCEPT_VECTOR_CYPHER,
    FIND_EXISTING_MEMBER_OF_CYPHER,
    FIND_UNCLASSIFIED_NODES_CYPHER,
    MERGE_CONCEPT_LINK_CYPHER,
    MERGE_MEMBER_OF_CYPHER,
    WRITE_UNANCHORED_CANDIDATE_CYPHER,
    ConceptMatch,
    assign_entity_home,
    compute_hash_id,
    find_concept_match,
    genre_concept_id,
    infer_definition_kind,
    kernel_catch_all_concept_id,
    merge_concept_and_link,
)
from app.pipeline.domain_book import CATEGORY_CARDS


class FakeResult:
    def __init__(self, records: list[dict]):
        self._records = records

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for record in self._records:
            yield record

    async def single(self):
        return self._records[0] if self._records else None


class FakeSession:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.queue: list[list[dict]] = []

    def enqueue(self, records: list[dict]) -> None:
        self.queue.append(records)

    async def run(self, cypher, **kwargs):
        self.calls.append((cypher, kwargs))
        records = self.queue.pop(0) if self.queue else []
        return FakeResult(records)


def _compact(cypher: str) -> str:
    return " ".join(cypher.split())


def test_exact_concept_cypher_is_id_or_name_plus_kernel_category():
    compact_id = _compact(FIND_CONCEPT_BY_ID_CYPHER)
    assert "MATCH (c:Concept {id: $concept_id})" in compact_id
    compact_name = _compact(FIND_CONCEPT_EXACT_NAME_CYPHER)
    assert "MATCH (c:Concept {name: $name})" in compact_name
    assert "c.kernel_category = $kernel_category" in FIND_CONCEPT_EXACT_NAME_CYPHER


def test_vector_concept_cypher_filters_kernel_category():
    compact = _compact(FIND_CONCEPT_VECTOR_CYPHER)
    assert "db.index.vector.queryNodes('concept_embedding'" in compact
    assert "$k" in FIND_CONCEPT_VECTOR_CYPHER
    assert "$embedding" in FIND_CONCEPT_VECTOR_CYPHER
    assert "node.kernel_category = $kernel_category" in FIND_CONCEPT_VECTOR_CYPHER
    assert CONCEPT_VECTOR_INDEX == "concept_embedding"


def test_vector_concept_cypher_does_not_scan_all_concepts():
    compact = _compact(FIND_CONCEPT_VECTOR_CYPHER)
    assert "MATCH (c:Concept) RETURN c" not in compact
    assert "MATCH (n:Node) RETURN n" not in compact


def test_member_of_cypher_is_merge_not_create():
    compact = _compact(MERGE_MEMBER_OF_CYPHER)
    assert "MERGE (n)-[:MEMBER_OF]->(c)" in compact
    assert "HAS_CONCEPT" not in MERGE_MEMBER_OF_CYPHER
    assert "MEMBER_OF" in FIND_EXISTING_MEMBER_OF_CYPHER
    assert "LIMIT 1" in FIND_EXISTING_MEMBER_OF_CYPHER


def test_catch_all_has_no_is_a_and_uses_kernel_prefix():
    assert "IS_A" not in ENSURE_KERNEL_CATCH_ALL_CYPHER
    assert "promoted = true" in ENSURE_KERNEL_CATCH_ALL_CYPHER
    assert "c.definition = $definition" in ENSURE_KERNEL_CATCH_ALL_CYPHER
    agente_id = kernel_catch_all_concept_id(EntityKernelType.Agente)
    assert agente_id == compute_hash_id("kernel:Agente")
    free_tag_id = compute_hash_id(CATEGORY_CARDS[EntityKernelType.Agente].catch_all)
    assert agente_id != free_tag_id


def test_new_genre_is_a_targets_existing_parent():
    compact = _compact(CREATE_ANCHORED_GENRE_CYPHER)
    assert "MATCH (parent:Concept {id: $parent_id})" in compact
    assert "MERGE (c)-[:IS_A]->(parent)" in compact
    assert "c.promoted = true" in CREATE_ANCHORED_GENRE_CYPHER
    assert "c.embedding = $embedding" in CREATE_ANCHORED_GENRE_CYPHER
    assert "c.definition = $definition" in CREATE_ANCHORED_GENRE_CYPHER


def test_unclassified_query_covers_entities_and_events_without_member_of():
    compact = _compact(FIND_UNCLASSIFIED_NODES_CYPHER)
    assert "n.type = 'entity' OR n.type = 'event'" in compact
    assert "NOT (n)-[:MEMBER_OF]->(:Concept)" in compact
    assert "merged_into IS NULL" in FIND_UNCLASSIFIED_NODES_CYPHER


def test_unanchored_candidate_label_and_priority():
    assert ":UnanchoredCandidate" in WRITE_UNANCHORED_CANDIDATE_CYPHER
    assert "u.priority = $priority" in WRITE_UNANCHORED_CANDIDATE_CYPHER
    assert "u.reason = $reason" in WRITE_UNANCHORED_CANDIDATE_CYPHER


def test_has_concept_merge_path_unchanged():
    assert "HAS_CONCEPT" in MERGE_CONCEPT_LINK_CYPHER
    assert "MEMBER_OF" not in MERGE_CONCEPT_LINK_CYPHER
    assert "IS_A" not in MERGE_CONCEPT_LINK_CYPHER


def test_infer_definition_kind_detects_value_filter():
    assert infer_definition_kind("giocatori età>50") == "value_filter"
    assert infer_definition_kind("Alice", "A person named Alice.") == "primitive_concept"


def test_settings_backbone_thresholds_and_flag():
    assert Settings.model_fields["BACKBONE_REUSE_THRESHOLD"].default == 0.80
    assert Settings.model_fields["BACKBONE_NEAR_THRESHOLD"].default == 0.50
    assert Settings.model_fields["ENABLE_KERNEL_CLASSIFICATION"].default is True


def test_kernel_version_written_on_promoted_concepts():
    assert "$kernel_version" in ENSURE_KERNEL_CATCH_ALL_CYPHER
    assert "$kernel_version" in CREATE_ANCHORED_GENRE_CYPHER
    assert KERNEL_VERSION == "1.0.0"


@pytest.mark.asyncio
async def test_find_concept_match_exact_then_vector(monkeypatch):
    session = FakeSession()
    session.enqueue([])  # exact id miss
    session.enqueue([])  # exact name miss
    session.enqueue(
        [{"id": "genre-calciatore", "name": "Calciatore", "score": 0.91}]
    )
    monkeypatch.setattr("app.pipeline.concepts.embeddings.embed", lambda _t: [0.1] * 8)

    match = await find_concept_match(
        session,
        name="Alice",
        kernel_category=EntityKernelType.Agente,
        embedding=[0.1] * 8,
    )

    assert match == ConceptMatch(
        concept_id="genre-calciatore",
        name="Calciatore",
        score=0.91,
        via="embedding",
    )
    assert session.calls[0][0] == FIND_CONCEPT_BY_ID_CYPHER
    assert session.calls[0][1]["concept_id"] == genre_concept_id(
        EntityKernelType.Agente, "Alice"
    )
    assert session.calls[1][0] == FIND_CONCEPT_EXACT_NAME_CYPHER
    assert session.calls[1][1]["kernel_category"] == "Agente"
    assert session.calls[2][0] == FIND_CONCEPT_VECTOR_CYPHER
    assert session.calls[2][1]["kernel_category"] == "Agente"


@pytest.mark.asyncio
async def test_find_concept_match_skips_vector_on_exact_id():
    session = FakeSession()
    session.enqueue([{"id": "genre-alice", "name": "Alice"}])

    match = await find_concept_match(
        session,
        name="Alice",
        kernel_category=EntityKernelType.Agente,
        embedding=[0.1] * 8,
    )

    assert match is not None
    assert match.via == "exact_id"
    assert match.score == 1.0
    assert len(session.calls) == 1
    assert not any("vector.queryNodes" in call[0] for call in session.calls)


@pytest.mark.asyncio
async def test_vector_reuse_two_names_same_concept(monkeypatch):
    monkeypatch.setattr("app.pipeline.concepts.embeddings.embed", lambda _t: [0.2] * 8)
    catch_all = kernel_catch_all_concept_id(EntityKernelType.Agente)
    shared = "genre-persona"

    async def _assign(name: str) -> str | None:
        session = FakeSession()
        session.enqueue([])  # no existing MEMBER_OF
        session.enqueue([{"id": catch_all}])  # ensure catch-all
        session.enqueue([])  # exact id
        session.enqueue([])  # exact name
        session.enqueue([{"id": shared, "name": "Persona", "score": 0.84}])
        return await assign_entity_home(
            session,
            node_id=f"n-{name}",
            name=name,
            summary=f"A person called {name}.",
            kernel_category=EntityKernelType.Agente,
        )

    first = await _assign("Alice")
    second = await _assign("Alice Rossi")
    assert first == second == shared


@pytest.mark.asyncio
async def test_second_assign_is_noop_same_concept_id(monkeypatch):
    monkeypatch.setattr("app.pipeline.concepts.embeddings.embed", lambda _t: [0.2] * 8)
    session = FakeSession()
    session.enqueue([{"concept_id": "already-home"}])

    result = await assign_entity_home(
        session,
        node_id="n-alice",
        name="Alice",
        summary="A person named Alice.",
        kernel_category=EntityKernelType.Agente,
    )

    assert result == "already-home"
    assert len(session.calls) == 1
    assert session.calls[0][0] == FIND_EXISTING_MEMBER_OF_CYPHER
    assert not any(call[0] == MERGE_MEMBER_OF_CYPHER for call in session.calls)


@pytest.mark.asyncio
async def test_merge_concept_and_link_still_writes_has_concept(monkeypatch):
    monkeypatch.setattr("app.pipeline.concepts.embeddings.embed", lambda _t: [0.25] * 8)
    session = FakeSession()
    await merge_concept_and_link(session, "node-1", "technology")
    cypher, kwargs = session.calls[0]
    assert cypher == MERGE_CONCEPT_LINK_CYPHER
    assert "HAS_CONCEPT" in cypher
    assert kwargs["name"] == "technology"
