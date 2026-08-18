"""Fase 4 acceptance: backbone TBox classification (MEMBER_OF / IS_A). No Docker."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.kernel import EntityKernelType
from app.models.node_extraction import ExtractedEntity
from app.pipeline.backbone import classify_and_grow_backbone
from app.pipeline.concepts import (
    CREATE_ANCHORED_GENRE_CYPHER,
    ENSURE_KERNEL_CATCH_ALL_CYPHER,
    FIND_CONCEPT_BY_ID_CYPHER,
    FIND_CONCEPT_EXACT_NAME_CYPHER,
    FIND_CONCEPT_VECTOR_CYPHER,
    FIND_EXISTING_MEMBER_OF_CYPHER,
    FIND_UNCLASSIFIED_NODES_CYPHER,
    MERGE_CONCEPT_LINK_CYPHER,
    MERGE_MEMBER_OF_CYPHER,
    READ_CORPUS_CONTEXT_SUMMARY_CYPHER,
    WRITE_UNANCHORED_CANDIDATE_CYPHER,
    assign_entity_home,
    kernel_catch_all_concept_id,
    merge_concept_and_link,
)

JOB_ID = "job-f4-backbone"


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


class BackboneFakeSession:
    """Dispatch FakeSession keyed on Cypher constants (not a brittle queue)."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.unclassified: list[dict] = []
        self.member_of: dict[str, str] = {}
        self.concepts: dict[str, dict] = {}
        self.isa: dict[str, str] = {}
        self.vector_hits: list[dict] = []
        self.unanchored: list[dict] = []
        self.has_concept: list[tuple[str, str]] = []
        self.corpus_summary = ""

    async def run(self, cypher, **kwargs):
        self.calls.append((cypher, kwargs))
        if cypher == FIND_UNCLASSIFIED_NODES_CYPHER:
            pending = [
                row
                for row in self.unclassified
                if row["id"] not in self.member_of
            ]
            return FakeResult(pending)
        if cypher == READ_CORPUS_CONTEXT_SUMMARY_CYPHER:
            if not self.corpus_summary:
                return FakeResult([])
            return FakeResult([{"summary_text": self.corpus_summary}])
        if cypher == FIND_EXISTING_MEMBER_OF_CYPHER:
            cid = self.member_of.get(kwargs["node_id"])
            return FakeResult([{"concept_id": cid}] if cid else [])
        if cypher == ENSURE_KERNEL_CATCH_ALL_CYPHER:
            cid = kwargs["concept_id"]
            self.concepts[cid] = {
                "id": cid,
                "name": kwargs["name"],
                "kernel_category": kwargs["kernel_category"],
                "promoted": True,
            }
            return FakeResult([{"id": cid}])
        if cypher == FIND_CONCEPT_BY_ID_CYPHER:
            found = self.concepts.get(kwargs["concept_id"])
            if found is None:
                return FakeResult([])
            return FakeResult([{"id": found["id"], "name": found["name"]}])
        if cypher == FIND_CONCEPT_EXACT_NAME_CYPHER:
            for concept in self.concepts.values():
                if (
                    concept.get("name") == kwargs["name"]
                    and concept.get("kernel_category") == kwargs["kernel_category"]
                ):
                    return FakeResult(
                        [{"id": concept["id"], "name": concept["name"]}]
                    )
            return FakeResult([])
        if cypher == FIND_CONCEPT_VECTOR_CYPHER:
            return FakeResult(list(self.vector_hits))
        if cypher == CREATE_ANCHORED_GENRE_CYPHER:
            cid = kwargs["concept_id"]
            self.concepts[cid] = {
                "id": cid,
                "name": kwargs["name"],
                "kernel_category": kwargs["kernel_category"],
                "promoted": True,
                "definition": kwargs["definition"],
            }
            self.isa[cid] = kwargs["parent_id"]
            return FakeResult([{"id": cid}])
        if cypher == MERGE_MEMBER_OF_CYPHER:
            self.member_of[kwargs["node_id"]] = kwargs["concept_id"]
            return FakeResult([])
        if cypher == WRITE_UNANCHORED_CANDIDATE_CYPHER:
            self.unanchored.append(dict(kwargs))
            return FakeResult([])
        if cypher == MERGE_CONCEPT_LINK_CYPHER:
            self.has_concept.append((kwargs["node_id"], kwargs["concept_id"]))
            return FakeResult([])
        return FakeResult([])


def _entity_row(
    node_id: str,
    name: str,
    *,
    summary: str = "",
    category: EntityKernelType = EntityKernelType.Agente,
) -> dict:
    return {
        "id": node_id,
        "name": name,
        "summary": summary,
        "kernel_category": category.value,
        "type": "entity",
    }


@pytest.mark.asyncio
async def test_two_entities_vector_reuse_same_concept(monkeypatch):
    monkeypatch.setattr("app.pipeline.concepts.embeddings.embed", lambda _t: [0.1] * 8)
    session = BackboneFakeSession()
    session.unclassified = [
        _entity_row("n-alice", "Alice", summary="A football player named Alice."),
        _entity_row(
            "n-alice-rossi", "Alice Rossi", summary="A football player named Alice Rossi."
        ),
    ]
    session.vector_hits = [
        {"id": "genre-calciatore", "name": "Calciatore", "score": 0.86}
    ]

    written = await classify_and_grow_backbone(session, JOB_ID)

    assert written == 2
    assert session.member_of["n-alice"] == "genre-calciatore"
    assert session.member_of["n-alice-rossi"] == "genre-calciatore"
    assert session.member_of["n-alice"] == session.member_of["n-alice-rossi"]
    assert not session.isa


@pytest.mark.asyncio
async def test_no_match_creates_genre_is_a_kernel_catch_all(monkeypatch):
    monkeypatch.setattr("app.pipeline.concepts.embeddings.embed", lambda _t: [0.1] * 8)
    session = BackboneFakeSession()
    session.unclassified = [
        _entity_row("n-bob", "Bob", summary="A software engineer named Bob."),
    ]
    session.vector_hits = []

    written = await classify_and_grow_backbone(session, JOB_ID)

    catch_all = kernel_catch_all_concept_id(EntityKernelType.Agente)
    assert written == 1
    home = session.member_of["n-bob"]
    assert home != catch_all
    assert home in session.isa
    assert session.isa[home] == catch_all
    assert catch_all in session.concepts
    assert session.concepts[catch_all]["promoted"] is True
    assert "IS_A" in CREATE_ANCHORED_GENRE_CYPHER
    parent_calls = [c for c in session.calls if c[0] == CREATE_ANCHORED_GENRE_CYPHER]
    assert parent_calls
    assert parent_calls[0][1]["parent_id"] == catch_all


@pytest.mark.asyncio
async def test_value_filter_writes_unanchored_and_member_of_catch_all(monkeypatch):
    monkeypatch.setattr("app.pipeline.concepts.embeddings.embed", lambda _t: [0.1] * 8)
    session = BackboneFakeSession()
    catch_all = kernel_catch_all_concept_id(EntityKernelType.Agente)
    await assign_entity_home(
        session,
        node_id="n-filter",
        name="giocatori età>50",
        summary="σ_età>50(giocatori)",
        kernel_category=EntityKernelType.Agente,
        definition_kind="value_filter",
    )

    assert session.member_of["n-filter"] == catch_all
    assert session.isa == {}
    assert len(session.unanchored) == 1
    assert session.unanchored[0]["reason"] == "genre_vs_filter_gate"
    assert session.unanchored[0]["node_id"] == "n-filter"
    assert session.unanchored[0]["kernel_category"] == "Agente"
    assert "priority" in session.unanchored[0]


@pytest.mark.asyncio
async def test_every_classified_node_has_exactly_one_member_of(monkeypatch):
    monkeypatch.setattr("app.pipeline.concepts.embeddings.embed", lambda _t: [0.1] * 8)
    session = BackboneFakeSession()
    session.unclassified = [
        _entity_row("n1", "Alice", summary="Person Alice."),
        _entity_row(
            "n2",
            "Acme",
            summary="The company Acme.",
            category=EntityKernelType.CostruttoSociale,
        ),
        {
            "id": "ev1",
            "name": "Kickoff",
            "summary": "A kickoff meeting.",
            "kernel_category": EntityKernelType.Evento.value,
            "type": "event",
        },
    ]
    session.vector_hits = []

    await classify_and_grow_backbone(session, JOB_ID)

    classified_ids = {row["id"] for row in session.unclassified}
    assert classified_ids == set(session.member_of)
    member_writes = [c for c in session.calls if c[0] == MERGE_MEMBER_OF_CYPHER]
    assert len(member_writes) == 3
    for node_id in classified_ids:
        homes = [c[1]["concept_id"] for c in member_writes if c[1]["node_id"] == node_id]
        assert len(homes) == 1


@pytest.mark.asyncio
async def test_second_classify_is_noop_same_concept(monkeypatch):
    monkeypatch.setattr("app.pipeline.concepts.embeddings.embed", lambda _t: [0.1] * 8)
    session = BackboneFakeSession()
    session.unclassified = [_entity_row("n-alice", "Alice", summary="Person Alice.")]
    session.vector_hits = [
        {"id": "genre-persona", "name": "Persona", "score": 0.90}
    ]

    first = await classify_and_grow_backbone(session, JOB_ID)
    home = session.member_of["n-alice"]
    writes_after_first = len([c for c in session.calls if c[0] == MERGE_MEMBER_OF_CYPHER])

    second = await classify_and_grow_backbone(session, JOB_ID)

    assert first == 1
    assert second == 0
    assert session.member_of["n-alice"] == home
    writes_after_second = len([c for c in session.calls if c[0] == MERGE_MEMBER_OF_CYPHER])
    assert writes_after_second == writes_after_first


@pytest.mark.asyncio
async def test_missing_kernel_category_skips_without_crash(monkeypatch):
    monkeypatch.setattr("app.pipeline.concepts.embeddings.embed", lambda _t: [0.1] * 8)
    session = BackboneFakeSession()
    session.unclassified = [
        {
            "id": "n-old",
            "name": "Legacy",
            "summary": "An old node without a category.",
            "kernel_category": None,
            "type": "entity",
        }
    ]

    written = await classify_and_grow_backbone(session, JOB_ID)

    assert written == 0
    assert "n-old" not in session.member_of
    assert session.unanchored
    assert session.unanchored[0]["reason"] == "missing_kernel_category"


@pytest.mark.asyncio
async def test_near_band_assigns_catch_all(monkeypatch):
    monkeypatch.setattr("app.pipeline.concepts.embeddings.embed", lambda _t: [0.1] * 8)
    session = BackboneFakeSession()
    session.unclassified = [_entity_row("n-near", "Pat", summary="Someone named Pat.")]
    session.vector_hits = [{"id": "genre-other", "name": "Other", "score": 0.61}]

    await classify_and_grow_backbone(session, JOB_ID)

    catch_all = kernel_catch_all_concept_id(EntityKernelType.Agente)
    assert session.member_of["n-near"] == catch_all
    assert "n-near" not in session.isa
    assert not session.isa


@pytest.mark.asyncio
async def test_has_concept_still_created_by_existing_merge_path(monkeypatch):
    monkeypatch.setattr("app.pipeline.concepts.embeddings.embed", lambda _t: [0.25] * 8)
    session = BackboneFakeSession()
    await merge_concept_and_link(session, "n-alice", "technology")
    assert session.has_concept
    assert session.calls[0][0] == MERGE_CONCEPT_LINK_CYPHER
    assert "HAS_CONCEPT" in MERGE_CONCEPT_LINK_CYPHER
    assert "MEMBER_OF" not in MERGE_CONCEPT_LINK_CYPHER


@pytest.mark.asyncio
async def test_kill_switch_is_noop(monkeypatch):
    monkeypatch.setattr(
        "app.pipeline.backbone.settings.ENABLE_KERNEL_CLASSIFICATION", False
    )
    session = BackboneFakeSession()
    session.unclassified = [_entity_row("n-alice", "Alice")]
    written = await classify_and_grow_backbone(session, JOB_ID)
    assert written == 0
    assert session.calls == []
    assert session.member_of == {}


def test_extracted_entity_requires_kernel_category():
    with pytest.raises(ValidationError):
        ExtractedEntity(name="Alice")
    with pytest.raises(ValidationError):
        ExtractedEntity(name="Alice", summary="A person named Alice.")
    entity = ExtractedEntity(
        name="Alice",
        summary="A person named Alice.",
        kernel_category=EntityKernelType.Agente,
    )
    assert entity.kernel_category is EntityKernelType.Agente
    field = ExtractedEntity.model_fields["kernel_category"]
    assert field.is_required()
    assert field.annotation is EntityKernelType


@pytest.mark.asyncio
async def test_classify_publishes_member_assigned(monkeypatch):
    monkeypatch.setattr("app.pipeline.concepts.embeddings.embed", lambda _t: [0.1] * 8)
    published: list[dict] = []

    async def spy_publish(job_id, stage, event, payload):
        published.append(
            {"job_id": job_id, "stage": stage, "event": event, "payload": payload}
        )

    monkeypatch.setattr("app.pipeline.backbone.event_bus.publish", spy_publish)
    session = BackboneFakeSession()
    session.unclassified = [_entity_row("n-alice", "Alice", summary="Person Alice.")]
    session.vector_hits = [{"id": "genre-persona", "name": "Persona", "score": 0.90}]

    written = await classify_and_grow_backbone(session, JOB_ID)

    assert written == 1
    assigned = [m for m in published if m["event"] == "backbone_member_assigned"]
    assert len(assigned) == 1
    assert assigned[0]["stage"] == "backbone_classification"
    assert assigned[0]["payload"]["node_id"] == "n-alice"
    assert assigned[0]["payload"]["concept_id"] == "genre-persona"
