"""Fase 20: :PendingHypothesis create/reinforce/listen. FakeSession, no Docker."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core import event_bus
from app.models.kernel import EntityKernelType, RelationKernelType
from app.models.node_extraction import (
    EntityExtractionResult,
    EventEntityExtractionResult,
    EventRelationExtractionResult,
    ExtractedEntity,
    PairRelationDecision,
)
from app.pipeline.chunking import Chunk
from app.pipeline.entity_relation_resolution import FIND_DIFFERENT_TAIL_PAIRS_CYPHER
from app.pipeline.ingestion import CREATE_NODE_RELATION_CYPHER, process_chunk_node_extraction
from app.pipeline.pending_hypothesis import (
    INCREMENT_LISTEN_CYPHER,
    LIST_OPEN_HYPOTHESES_CYPHER,
    MERGE_HYPOTHESIS_CYPHER,
    READ_HYPOTHESIS_CYPHER,
    RESOLVE_HYPOTHESIS_CYPHER,
    SET_EVIDENCE_GAP_CYPHER,
    create_or_reinforce_hypothesis,
    list_open_hypotheses,
    listen_open_hypotheses,
    resolve_hypothesis,
    route_chunk_signal,
)
from app.pipeline.relevance_gate import FragmentSignal, classify_fragment_relevance

JOB_ID = "job-f20"
DOC_A = "doc-f20-a"
DOC_B = "doc-f20-b"


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


class HypothesisSession:
    """In-memory :PendingHypothesis store. No :Relation / :Node fact writes."""

    def __init__(self, *, comparables: list[dict] | None = None):
        self.hypotheses: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        self.comparables = comparables or []

    def seed(self, **props) -> dict:
        row = {
            "status": "open",
            "confidence": "low",
            "evidence_span": [],
            "witness_fragments": [],
            "listen_count": 0,
            "origin_doc_count": 0,
            "promoted": False,
            **props,
        }
        self.hypotheses[row["id"]] = row
        return row

    async def run(self, cypher, **kwargs):
        self.calls.append((cypher, kwargs))
        if cypher == READ_HYPOTHESIS_CYPHER:
            hyp = self.hypotheses.get(kwargs["id"])
            return FakeResult([dict(hyp)] if hyp else [])
        if cypher == MERGE_HYPOTHESIS_CYPHER:
            hid = kwargs["id"]
            existing = self.hypotheses.get(hid, {})
            row = {**existing, **kwargs}
            if existing:
                row["origin_doc_id"] = existing.get("origin_doc_id") or kwargs.get(
                    "origin_doc_id"
                )
                row["origin_doc_count"] = existing.get("origin_doc_count", 0)
            self.hypotheses[hid] = row
            return FakeResult([])
        if cypher == LIST_OPEN_HYPOTHESES_CYPHER:
            return FakeResult(
                [dict(h) for h in self.hypotheses.values() if h.get("status") == "open"]
            )
        if cypher == RESOLVE_HYPOTHESIS_CYPHER:
            hyp = self.hypotheses.get(kwargs["id"])
            if hyp is None:
                return FakeResult([])
            hyp["status"] = kwargs["status"]
            return FakeResult([dict(hyp)])
        if cypher == INCREMENT_LISTEN_CYPHER:
            hyp = self.hypotheses.get(kwargs["id"])
            if hyp is None:
                return FakeResult([])
            hyp["listen_count"] = int(hyp.get("listen_count") or 0) + 1
            return FakeResult([dict(hyp)])
        if cypher == SET_EVIDENCE_GAP_CYPHER:
            hyp = self.hypotheses.get(kwargs["id"])
            if hyp is None:
                return FakeResult([])
            hyp["evidence_gap"] = kwargs["evidence_gap"]
            return FakeResult([dict(hyp)])
        if cypher == FIND_DIFFERENT_TAIL_PAIRS_CYPHER:
            return FakeResult(list(self.comparables))
        return FakeResult([])


@pytest.fixture(autouse=True)
def _stub_embed(monkeypatch):
    monkeypatch.setattr("app.pipeline.embeddings.embed", lambda _text: [0.0] * 768)
    event_bus.reset_event_bus()
    from app.pipeline.pending_hypothesis import reset_promoted_queue

    reset_promoted_queue()
    yield
    event_bus.reset_event_bus()
    reset_promoted_queue()


def _signal(text: str, **kwargs) -> FragmentSignal:
    signal = classify_fragment_relevance(text, kwargs.pop("pair_entities", []), None)
    assert signal is not None
    if kwargs:
        data = {**signal.__dict__, **kwargs}
        signal = FragmentSignal(**data)
    return signal


def test_module_never_writes_s0():
    source = Path(create_or_reinforce_hypothesis.__code__.co_filename).read_text(
        encoding="utf-8"
    )
    assert "CREATE (n:Node" not in source
    assert "-[:Relation" not in source
    assert "write_node_relation" not in source
    gate = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "relevance_gate.py"
    gate_src = gate.read_text(encoding="utf-8")
    assert "CREATE" not in gate_src
    assert "MERGE" not in gate_src


@pytest.mark.asyncio
async def test_rejected_fragment_never_creates_hypothesis(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.ENABLE_CONTEXT_LAYER", True)
    session = HypothesisSession()
    assert await create_or_reinforce_hypothesis(session, job_id=JOB_ID) is None
    rec = await route_chunk_signal(
        session,
        chunk_text="Nota a margine.",
        doc_id=DOC_A,
        job_id=JOB_ID,
    )
    assert rec is None
    assert MERGE_HYPOTHESIS_CYPHER not in {cy for cy, _ in session.calls}
    assert session.hypotheses == {}


@pytest.mark.asyncio
async def test_named_witness_promotes_high_and_publishes(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.ENABLE_CONTEXT_LAYER", True)
    queue = await event_bus.subscribe(JOB_ID)
    session = HypothesisSession()
    rec = await create_or_reinforce_hypothesis(
        session,
        signal=_signal("Fido è uscito dal cancello."),
        doc_id=DOC_A,
        job_id=JOB_ID,
    )
    assert rec is not None
    assert rec["confidence"] == "high"
    assert rec["status"] == "open"
    assert rec["promoted"] is True
    assert rec["witness_fragments"]
    msg = queue.get_nowait()
    assert msg["stage"] == "context_layer"
    assert msg["event"] == "hypothesis_promoted"
    assert msg["payload"]["hypothesis_id"] == rec["id"]
    assert msg["payload"]["confidence"] == "high"
    assert not any("Relation" in cy for cy, _ in session.calls)


@pytest.mark.asyncio
async def test_double_reinforcement_promotes_medium():
    session = HypothesisSession()
    first = await create_or_reinforce_hypothesis(
        session,
        signal=_signal("I cani sono usciti."),
        doc_id=DOC_A,
        job_id=JOB_ID,
    )
    assert first is not None
    assert first["confidence"] == "low"
    assert first["promoted"] is False
    second = await create_or_reinforce_hypothesis(
        session,
        claim_target=first["claim_target"],
        evidence_span="Anche i cani della cucina sono usciti.",
        reinforcement=True,
        doc_id=DOC_B,
        job_id=JOB_ID,
    )
    assert second is not None
    assert second["id"] == first["id"]
    assert second["confidence"] == "medium"
    assert second["promoted"] is True
    assert second["status"] == "open"
    assert len(session.hypotheses) == 1


@pytest.mark.asyncio
async def test_no_reinforcement_stays_open_past_window(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.ENABLE_CONTEXT_LAYER", True)
    monkeypatch.setattr("app.core.config.settings.PENDING_HYPOTHESIS_LISTEN_WINDOW", 5)
    queue = await event_bus.subscribe(JOB_ID)
    session = HypothesisSession()
    created = await create_or_reinforce_hypothesis(
        session,
        signal=_signal("I cani sono usciti."),
        doc_id=DOC_A,
        job_id=JOB_ID,
    )
    assert created is not None
    while not queue.empty():
        queue.get_nowait()
    for index in range(6):
        await listen_open_hypotheses(
            session,
            doc_id=f"doc-later-{index}",
            new_text="Alice works at Acme.",
            job_id=JOB_ID,
        )
    open_hyps = await list_open_hypotheses(session)
    assert len(open_hyps) == 1
    assert open_hyps[0]["status"] == "open"
    assert open_hyps[0]["confidence"] == "low"
    assert open_hyps[0]["promoted"] is False
    assert open_hyps[0]["listen_count"] >= 5
    assert queue.empty()


@pytest.mark.asyncio
async def test_denial_dismisses_and_leaves_untouched(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.ENABLE_CONTEXT_LAYER", True)
    session = HypothesisSession()
    created = await create_or_reinforce_hypothesis(
        session,
        signal=_signal("I cani sono usciti."),
        doc_id=DOC_A,
        job_id=JOB_ID,
    )
    assert created is not None
    updated = await listen_open_hypotheses(
        session,
        doc_id=DOC_B,
        new_text="Non è vero niente: i cani non sono usciti.",
        job_id=JOB_ID,
    )
    assert updated
    assert updated[0]["status"] == "dismissed"
    assert session.hypotheses[created["id"]]["status"] == "dismissed"
    assert not any(cy == CREATE_NODE_RELATION_CYPHER for cy, _ in session.calls)
    assert not any("CREATE (n:Node" in cy for cy, _ in session.calls)


@pytest.mark.asyncio
async def test_resolve_hypothesis_dismissed():
    session = HypothesisSession()
    created = await create_or_reinforce_hypothesis(
        session,
        signal=_signal("I cani sono usciti."),
        doc_id=DOC_A,
        job_id=JOB_ID,
    )
    assert created is not None
    resolved = await resolve_hypothesis(session, created["id"], "dismissed")
    assert resolved is not None
    assert resolved["status"] == "dismissed"
    assert await list_open_hypotheses(session) == []


@pytest.mark.asyncio
async def test_flag_off_zero_hypothesis_writes_and_queries(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.ENABLE_CONTEXT_LAYER", False)
    session = HypothesisSession()
    rec = await route_chunk_signal(
        session,
        chunk_text="Tutti i cani sono usciti.",
        s0_written=True,
        node_ids=["n1", "n2"],
        doc_id=DOC_A,
        job_id=JOB_ID,
    )
    assert rec is None
    listened = await listen_open_hypotheses(
        session,
        doc_id=DOC_B,
        new_text="Tutti i cani sono usciti.",
        job_id=JOB_ID,
    )
    assert listened == []
    assert session.calls == []
    assert session.hypotheses == {}


@pytest.mark.asyncio
async def test_flag_on_t2_creates_low_hypothesis_without_named_witness(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.ENABLE_CONTEXT_LAYER", True)
    session = HypothesisSession()
    rec = await route_chunk_signal(
        session,
        chunk_text="Tutti i cani sono usciti.",
        doc_id=DOC_A,
        job_id=JOB_ID,
    )
    assert rec is not None
    assert rec["confidence"] == "low"
    assert rec["promoted"] is False
    assert rec["status"] == "open"
    assert rec["kind"] in {"t1", "t2"}
    assert any(cy == MERGE_HYPOTHESIS_CYPHER for cy, _ in session.calls)


@pytest.mark.asyncio
async def test_listen_skips_hypotheses_from_the_same_document(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.ENABLE_CONTEXT_LAYER", True)
    session = HypothesisSession()
    created = await create_or_reinforce_hypothesis(
        session,
        signal=_signal("I cani sono usciti."),
        doc_id=DOC_A,
        job_id=JOB_ID,
    )
    assert created is not None
    before = session.hypotheses[created["id"]]["listen_count"]
    await listen_open_hypotheses(
        session,
        doc_id=DOC_A,
        new_text="I cani sono usciti.",
        job_id=JOB_ID,
    )
    assert session.hypotheses[created["id"]]["listen_count"] == before
    assert session.hypotheses[created["id"]]["promoted"] is False


@pytest.mark.enable_node_extraction
@pytest.mark.asyncio
async def test_ingest_flag_off_does_not_emit_hypothesis_cypher(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.ENABLE_CONTEXT_LAYER", False)
    monkeypatch.setattr("app.pipeline.ingestion.settings.ENABLE_CONTEXT_LAYER", False)

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
    session = HypothesisSession()
    chunk = Chunk(id="c-off", doc_id=DOC_A, text="Tutti i cani sono usciti.")
    await process_chunk_node_extraction(session, chunk, DOC_A, JOB_ID)
    hypothesis_cypher = {
        READ_HYPOTHESIS_CYPHER,
        MERGE_HYPOTHESIS_CYPHER,
        LIST_OPEN_HYPOTHESES_CYPHER,
        FIND_DIFFERENT_TAIL_PAIRS_CYPHER,
    }
    assert not any(cy in hypothesis_cypher for cy, _ in session.calls)


@pytest.mark.enable_node_extraction
@pytest.mark.asyncio
async def test_ingest_rejected_fragment_does_not_merge_hypothesis(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.ENABLE_CONTEXT_LAYER", True)
    monkeypatch.setattr("app.pipeline.ingestion.settings.ENABLE_CONTEXT_LAYER", True)

    async def mock_entities(_text, **_kwargs):
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

    async def mock_pair(*_args, **_kwargs):
        return PairRelationDecision(
            related=True,
            relation="works_at",
            kernel_parent=RelationKernelType.SocialeIntenzionale,
            witness_source="Alice",
            witness_target="Acme",
        )

    async def mock_empty(*_args, **_kwargs):
        return EventEntityExtractionResult(participations=[])

    async def mock_rels(*_args, **_kwargs):
        return EventRelationExtractionResult(triples=[])

    monkeypatch.setattr("app.pipeline.node_extraction.extract_entities", mock_entities)
    monkeypatch.setattr("app.pipeline.node_extraction.extract_pair_relation", mock_pair)
    monkeypatch.setattr("app.pipeline.node_extraction.extract_event_entities", mock_empty)
    monkeypatch.setattr("app.pipeline.node_extraction.extract_event_relations", mock_rels)
    session = HypothesisSession()
    chunk = Chunk(id="c-ok", doc_id=DOC_A, text="Alice works at Acme.")
    await process_chunk_node_extraction(session, chunk, DOC_A, JOB_ID)
    assert not any(cy == MERGE_HYPOTHESIS_CYPHER for cy, _ in session.calls)
    assert any(cy == CREATE_NODE_RELATION_CYPHER for cy, _ in session.calls)
