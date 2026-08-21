"""Macrotask 4: kernel-safe slot proposal validation. FakeSession, no Neo4j/OpenAI."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.kernel import (
    AttributeKernelType,
    EntityKernelType,
    RelationKernelType,
    SpecialRelationType,
)
from app.pipeline.event_slots import (
    STAMP_SLOT_ON_LATEST_CYPHER,
    UPDATE_SLOT_EDGE_CYPHER,
    ValidatedSlot,
    apply_validated_slot,
    validate_slot_proposal,
)
from app.pipeline.ingestion import CREATE_NODE_RELATION_CYPHER
from tests.test_event_slots import (
    FAILED,
    FONTE,
    HEAD,
    PLACE,
    PROV,
    FakeSession,
    _seed,
)

WRITE_CYPHER = {
    CREATE_NODE_RELATION_CYPHER,
    STAMP_SLOT_ON_LATEST_CYPHER,
    UPDATE_SLOT_EDGE_CYPHER,
}


def _assert_proposal(**overrides: object) -> dict:
    proposal: dict = {
        "head_id": HEAD,
        "kernel_parent": AttributeKernelType.Stato.value,
        "tail_id": FAILED,
        "verb": "assert",
        "fonte_id": FONTE,
        "normalized_relation": "has_state",
    }
    proposal.update(overrides)
    return proposal


def _causale_proposal(**overrides: object) -> dict:
    proposal: dict = {
        "head_id": HEAD,
        "kernel_parent": RelationKernelType.Causale.value,
        "tail_id": PLACE,
        "verb": "assert",
        "fonte_id": FONTE,
        "normalized_relation": "caused_by",
    }
    proposal.update(overrides)
    return proposal


def _write_calls(session: FakeSession) -> list[tuple[str, dict]]:
    return [call for call in session.calls if call[0] in WRITE_CYPHER]


@pytest.fixture
def stub_ingestion_side_effects(monkeypatch):
    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.pipeline.ingestion.deposit_from_asserted_fact", _noop)
    monkeypatch.setattr("app.pipeline.ingestion.embeddings.embed", lambda _t: [0.1] * 8)


@pytest.mark.parametrize(
    "kernel_parent",
    ["EXPLAINED_BY", "Statoo", "same_as", "Evento"],
)
@pytest.mark.asyncio
async def test_invented_kernel_parent_none_and_zero_writes(
    stub_ingestion_side_effects, kernel_parent: str
):
    session = FakeSession(_seed(FAILED, PLACE))
    proposal = _assert_proposal(kernel_parent=kernel_parent)
    validated = validate_slot_proposal(proposal)
    assert validated is None

    wrote = await apply_validated_slot(session, validated, **PROV)
    assert wrote is False
    assert session.graph.relations == []
    assert _write_calls(session) == []
    assert session.calls == []

    wrote_raw = await apply_validated_slot(session, proposal, **PROV)
    assert wrote_raw is False
    assert session.graph.relations == []
    assert _write_calls(session) == []
    assert session.calls == []


@pytest.mark.parametrize(
    "kernel_parent",
    [
        "stato",
        "STATO",
        "Stato ",
        "Causale ",
        "is_a",
        "member_of",
        EntityKernelType.Evento,
        SpecialRelationType.same_as,
        SpecialRelationType.contradicts,
        "",
    ],
)
def test_nearby_and_wrong_family_kernel_not_coerced(kernel_parent: object):
    assert validate_slot_proposal(_assert_proposal(kernel_parent=kernel_parent)) is None
    assert validate_slot_proposal(_causale_proposal(kernel_parent=kernel_parent)) is None


@pytest.mark.asyncio
async def test_exact_stato_validates_and_applies(stub_ingestion_side_effects):
    graph = _seed(FAILED)
    session = FakeSession(graph)
    proposal = _assert_proposal(kernel_parent="Stato")
    validated = validate_slot_proposal(proposal)
    assert validated is not None
    assert isinstance(validated, ValidatedSlot)
    assert validated.kernel_parent is AttributeKernelType.Stato
    assert validated.slot.kernel_parent == AttributeKernelType.Stato.value
    assert validated.verb == "assert"
    assert validated.fonte_id == FONTE
    assert validated.tail_id == FAILED

    wrote = await apply_validated_slot(session, validated, **PROV)
    assert wrote is True
    assert len(graph.relations) == 1
    assert graph.relations[0]["kernel_parent"] == "Stato"
    assert graph.relations[0]["tail_id"] == FAILED
    assert graph.relations[0]["witness_source_ids"] == [FONTE]


@pytest.mark.asyncio
async def test_exact_causale_validates_and_applies(stub_ingestion_side_effects):
    graph = _seed(PLACE)
    session = FakeSession(graph)
    proposal = _causale_proposal(kernel_parent="Causale")
    validated = validate_slot_proposal(proposal)
    assert validated is not None
    assert validated.kernel_parent is RelationKernelType.Causale
    assert validated.slot.kernel_parent == RelationKernelType.Causale.value
    assert validated.tail_id == PLACE

    wrote = await apply_validated_slot(session, validated, **PROV)
    assert wrote is True
    assert len(graph.relations) == 1
    assert graph.relations[0]["kernel_parent"] == "Causale"
    assert graph.relations[0]["tail_id"] == PLACE
    assert graph.relations[0]["normalized_relation"] == "caused_by"


@pytest.mark.asyncio
async def test_enum_kernel_parent_accepted(stub_ingestion_side_effects):
    graph = _seed(FAILED)
    session = FakeSession(graph)
    validated = validate_slot_proposal(
        _assert_proposal(kernel_parent=AttributeKernelType.Stato)
    )
    assert validated is not None
    assert validated.kernel_parent is AttributeKernelType.Stato
    assert await apply_validated_slot(session, validated, **PROV) is True
    assert graph.relations[0]["kernel_parent"] == "Stato"


@pytest.mark.parametrize(
    "proposal",
    [
        None,
        {},
        [],
        1,
        "Stato",
        {"kernel_parent": 1, "head_id": HEAD, "verb": "assert", "fonte_id": FONTE},
        {"kernel_parent": ["Stato"], "head_id": HEAD, "verb": "assert", "fonte_id": FONTE},
        SimpleNamespace(),
        object(),
    ],
)
def test_malformed_proposal_returns_none_without_raising(proposal: object):
    assert validate_slot_proposal(proposal) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"head_id": ""},
        {"head_id": None},
        {"head_id": 12},
        {"fonte_id": ""},
        {"fonte_id": None},
        {"fonte_id": "   "},
        {"verb": "update"},
        {"verb": "Assert"},
        {"verb": None},
        {"verb": 0},
        {"tail_id": None, "kernel_parent": "Causale"},
        {"tail_id": "", "kernel_parent": "Causale"},
        {"tail_id": None, "kernel_parent": "Stato", "verb": "assert"},
    ],
)
def test_missing_required_write_fields_returns_none(overrides: dict):
    assert validate_slot_proposal(_assert_proposal(**overrides)) is None


def test_relation_slot_without_tail_rejected_even_for_retract():
    assert (
        validate_slot_proposal(
            _causale_proposal(verb="retract", tail_id=None, normalized_relation="caused_by")
        )
        is None
    )


def test_attribute_retract_without_tail_is_valid():
    validated = validate_slot_proposal(
        _assert_proposal(verb="retract", tail_id=None, kernel_parent="Stato")
    )
    assert validated is not None
    assert validated.verb == "retract"
    assert validated.kernel_parent is AttributeKernelType.Stato
    assert validated.tail_id is None


@pytest.mark.asyncio
async def test_apply_none_performs_zero_graph_writes(stub_ingestion_side_effects):
    session = FakeSession(_seed(FAILED))
    assert await apply_validated_slot(session, None, **PROV) is False
    assert session.graph.relations == []
    assert session.calls == []
    sets = [cy for cy, _ in session.calls if "SET " in cy]
    assert sets == []
