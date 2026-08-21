"""``:PendingHypothesis`` accumulate/route layer (Fase 20).

Zero S0 writes: this module never CREATE/MERGE ``:Relation`` or ``:Node``
facts. Confidence and ``hypothesis_promoted`` events are the only outputs
Fase 22 will later consume.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from neo4j import AsyncSession

from app.core import event_bus
from app.core.config import settings
from app.pipeline.chunking import Chunk
from app.pipeline.relevance_gate import (
    RETRACTION_MARKERS,
    FragmentSignal,
    S0Outcome,
    classify_fragment_relevance,
    extract_named_witnesses,
    has_different_tail_comparables,
    has_t2_marker,
)

logger = logging.getLogger(__name__)

HypothesisStatus = Literal["open", "confirmed", "dismissed"]
HypothesisConfidence = Literal["low", "medium", "high"]

# In-memory promotion queue keyed by job_id. Fase 22 drains this after the
# per-batch judge — a true subscribe, not a graph poll. Test-resettable.
_promoted_by_job: dict[str, list[str]] = {}

_TOKEN_RE = re.compile(r"[a-zàèéìòù0-9']+", re.IGNORECASE)
_STOPWORDS = frozenset(
    {
        "il",
        "la",
        "lo",
        "i",
        "gli",
        "le",
        "di",
        "a",
        "da",
        "in",
        "su",
        "e",
        "o",
        "un",
        "una",
        "the",
        "at",
        "to",
        "of",
        "nel",
        "del",
        "che",
        "per",
        "con",
        "non",
        "ma",
        "sono",
        "è",
        "ha",
        "are",
        "is",
        "was",
        "tutti",
        "tutte",
        "ogni",
        "all",
        "every",
        "usciti",
        "uscito",
        "left",
    }
)
_DENIAL_MARKERS = RETRACTION_MARKERS + (
    "non è vero",
    "non e' vero",
    "non e vero",
    "is not true",
    "never happened",
    "non sono usciti",
    "did not",
    "didn't",
    "non è così",
    "that is false",
)

READ_HYPOTHESIS_CYPHER = """
MATCH (h:PendingHypothesis {id: $id})
RETURN h.id AS id,
       h.claim_target AS claim_target,
       h.evidence_span AS evidence_span,
       h.witness_fragments AS witness_fragments,
       h.evidence_gap AS evidence_gap,
       h.confidence AS confidence,
       h.status AS status,
       h.marker_category AS marker_category,
       h.kind AS kind,
       h.origin_doc_id AS origin_doc_id,
       h.origin_doc_count AS origin_doc_count,
       h.listen_count AS listen_count,
       h.promoted AS promoted
"""

MERGE_HYPOTHESIS_CYPHER = """
MERGE (h:PendingHypothesis {id: $id})
ON CREATE SET
  h.claim_target = $claim_target,
  h.evidence_span = $evidence_span,
  h.witness_fragments = $witness_fragments,
  h.evidence_gap = $evidence_gap,
  h.confidence = $confidence,
  h.status = $status,
  h.marker_category = $marker_category,
  h.kind = $kind,
  h.origin_doc_id = $origin_doc_id,
  h.origin_doc_count = $origin_doc_count,
  h.listen_count = $listen_count,
  h.promoted = $promoted,
  h.created_at = datetime(),
  h.updated_at = datetime()
ON MATCH SET
  h.claim_target = $claim_target,
  h.evidence_span = $evidence_span,
  h.witness_fragments = $witness_fragments,
  h.evidence_gap = $evidence_gap,
  h.confidence = $confidence,
  h.status = $status,
  h.marker_category = $marker_category,
  h.kind = $kind,
  h.listen_count = $listen_count,
  h.promoted = $promoted,
  h.updated_at = datetime()
"""

LIST_OPEN_HYPOTHESES_CYPHER = """
MATCH (h:PendingHypothesis)
WHERE h.status = 'open'
RETURN h.id AS id,
       h.claim_target AS claim_target,
       h.evidence_span AS evidence_span,
       h.witness_fragments AS witness_fragments,
       h.evidence_gap AS evidence_gap,
       h.confidence AS confidence,
       h.status AS status,
       h.marker_category AS marker_category,
       h.kind AS kind,
       h.origin_doc_id AS origin_doc_id,
       h.origin_doc_count AS origin_doc_count,
       h.listen_count AS listen_count,
       h.promoted AS promoted
ORDER BY h.created_at
"""

RESOLVE_HYPOTHESIS_CYPHER = """
MATCH (h:PendingHypothesis {id: $id})
SET h.status = $status,
    h.updated_at = datetime()
RETURN h.id AS id,
       h.claim_target AS claim_target,
       h.evidence_span AS evidence_span,
       h.witness_fragments AS witness_fragments,
       h.evidence_gap AS evidence_gap,
       h.confidence AS confidence,
       h.status AS status,
       h.marker_category AS marker_category,
       h.kind AS kind,
       h.origin_doc_id AS origin_doc_id,
       h.origin_doc_count AS origin_doc_count,
       h.listen_count AS listen_count,
       h.promoted AS promoted
"""

INCREMENT_LISTEN_CYPHER = """
MATCH (h:PendingHypothesis {id: $id})
SET h.listen_count = coalesce(h.listen_count, 0) + 1,
    h.updated_at = datetime()
RETURN h.id AS id,
       h.claim_target AS claim_target,
       h.evidence_span AS evidence_span,
       h.witness_fragments AS witness_fragments,
       h.evidence_gap AS evidence_gap,
       h.confidence AS confidence,
       h.status AS status,
       h.marker_category AS marker_category,
       h.kind AS kind,
       h.origin_doc_id AS origin_doc_id,
       h.origin_doc_count AS origin_doc_count,
       h.listen_count AS listen_count,
       h.promoted AS promoted
"""

SET_EVIDENCE_GAP_CYPHER = """
MATCH (h:PendingHypothesis {id: $id})
SET h.evidence_gap = $evidence_gap,
    h.updated_at = datetime()
RETURN h.id AS id,
       h.claim_target AS claim_target,
       h.evidence_span AS evidence_span,
       h.witness_fragments AS witness_fragments,
       h.evidence_gap AS evidence_gap,
       h.confidence AS confidence,
       h.status AS status,
       h.marker_category AS marker_category,
       h.kind AS kind,
       h.origin_doc_id AS origin_doc_id,
       h.origin_doc_count AS origin_doc_count,
       h.listen_count AS listen_count,
       h.promoted AS promoted
"""


@dataclass(frozen=True)
class PendingHypothesisRecord:
    id: str
    claim_target: str
    evidence_span: tuple[str, ...]
    witness_fragments: tuple[str, ...]
    evidence_gap: str
    confidence: str
    status: str
    marker_category: str | None
    kind: str | None
    origin_doc_id: str
    origin_doc_count: int
    listen_count: int
    promoted: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "claim_target": self.claim_target,
            "evidence_span": list(self.evidence_span),
            "witness_fragments": list(self.witness_fragments),
            "evidence_gap": self.evidence_gap,
            "confidence": self.confidence,
            "status": self.status,
            "marker_category": self.marker_category,
            "kind": self.kind,
            "origin_doc_id": self.origin_doc_id,
            "origin_doc_count": self.origin_doc_count,
            "listen_count": self.listen_count,
            "promoted": self.promoted,
        }


def compute_hypothesis_id(claim_target: str) -> str:
    """Deterministic id: same claim_target → same node (MERGE/reinforce)."""
    normalized = " ".join((claim_target or "").casefold().split())
    return hashlib.sha256(f"pending:{normalized}".encode("utf-8")).hexdigest()


def _content_tokens(text: str) -> set[str]:
    return {
        tok
        for tok in _TOKEN_RE.findall((text or "").casefold())
        if tok not in _STOPWORDS and len(tok) > 2
    }


def infer_claim_target(
    text: str,
    *,
    marker_category: str | None,
    doc_id: str = "",
    pair_entities: Sequence[Any] = (),
    explicit: str | None = None,
) -> str:
    if (explicit or "").strip():
        return explicit.strip()
    if marker_category == "retraction":
        return f"retraction:{doc_id or 'unknown'}"
    names: list[str] = []
    for item in pair_entities or ():
        ent = item[1] if isinstance(item, tuple) and len(item) >= 2 else item
        name = str(getattr(ent, "name", "") or "").strip()
        if name:
            names.append(name.casefold())
    if names:
        return "|".join(sorted(set(names)))
    tokens = [
        tok
        for tok in _TOKEN_RE.findall((text or "").casefold())
        if tok not in _STOPWORDS and len(tok) > 2
    ]
    if tokens:
        return tokens[0]
    return " ".join((text or "").casefold().split())[:80] or "unknown"


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _append_unique(
    existing: Sequence[str], new_items: Sequence[str], *, cap: int = 20
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in [*existing, *new_items]:
        text = str(item).strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= cap:
            break
    return out


def _record_from_row(row: MappingLike | None) -> PendingHypothesisRecord | None:
    if row is None:
        return None
    data = dict(row)
    hid = data.get("id")
    if not hid:
        return None
    return PendingHypothesisRecord(
        id=str(hid),
        claim_target=str(data.get("claim_target") or ""),
        evidence_span=tuple(_as_list(data.get("evidence_span"))),
        witness_fragments=tuple(_as_list(data.get("witness_fragments"))),
        evidence_gap=str(data.get("evidence_gap") or ""),
        confidence=str(data.get("confidence") or "low"),
        status=str(data.get("status") or "open"),
        marker_category=(str(data["marker_category"]) if data.get("marker_category") else None),
        kind=(str(data["kind"]) if data.get("kind") else None),
        origin_doc_id=str(data.get("origin_doc_id") or ""),
        origin_doc_count=int(data.get("origin_doc_count") or 0),
        listen_count=int(data.get("listen_count") or 0),
        promoted=bool(data.get("promoted")),
    )


MappingLike = Any


async def _single_record(result: Any) -> PendingHypothesisRecord | None:
    getter = getattr(result, "single", None)
    if getter is not None:
        row = await getter()
        return _record_from_row(row)
    async for row in result:
        return _record_from_row(row)
    return None


async def _all_records(result: Any) -> list[PendingHypothesisRecord]:
    rows: list[PendingHypothesisRecord] = []
    async for row in result:
        rec = _record_from_row(row)
        if rec is not None:
            rows.append(rec)
    return rows


def _confidence_and_promote(
    *,
    existing: PendingHypothesisRecord | None,
    named_witnesses: Sequence[str],
    reinforcement: bool,
    listen_count: int,
) -> tuple[HypothesisConfidence, bool]:
    """§5 table. No promotion without named witnesses or reinforcement."""
    named = any(str(w).strip() for w in named_witnesses)
    window = int(settings.PENDING_HYPOTHESIS_LISTEN_WINDOW)
    past_window = listen_count >= window
    if named:
        return "high", True
    if reinforcement:
        return "medium", True
    if past_window:
        # Aging never promotes; stay at the current (or low) level.
        current = (existing.confidence if existing else "low") or "low"
        if current not in {"low", "medium", "high"}:
            current = "low"
        return current, False
    return "low", False


def enqueue_promoted(job_id: str, hypothesis_id: str) -> None:
    """Queue a promoted hypothesis id for the Fase 22 agent (per job)."""
    hid = (hypothesis_id or "").strip()
    if not hid:
        return
    key = job_id or ""
    bucket = _promoted_by_job.setdefault(key, [])
    if hid not in bucket:
        bucket.append(hid)


def drain_promoted_queue(job_id: str) -> list[str]:
    """Pop and return unique hypothesis ids queued for ``job_id``."""
    ids = _promoted_by_job.pop(job_id or "", [])
    return list(dict.fromkeys(ids))


def reset_promoted_queue(job_id: str | None = None) -> None:
    """Test helper: clear one job's queue, or all queues."""
    if job_id is None:
        _promoted_by_job.clear()
        return
    _promoted_by_job.pop(job_id or "", None)


async def _publish_promoted(
    job_id: str,
    record: PendingHypothesisRecord,
) -> None:
    enqueue_promoted(job_id, record.id)
    await event_bus.publish(
        job_id,
        "context_layer",
        "hypothesis_promoted",
        {
            "hypothesis_id": record.id,
            "confidence": record.confidence,
            "claim_target": record.claim_target,
            "status": record.status,
            "kind": record.kind,
            "marker_category": record.marker_category,
        },
    )


async def create_or_reinforce_hypothesis(
    session: AsyncSession,
    *,
    signal: FragmentSignal | None = None,
    claim_target: str | None = None,
    evidence_span: str | Sequence[str] = (),
    named_witnesses: Sequence[str] = (),
    doc_id: str = "",
    job_id: str = "",
    origin_doc_count: int = 0,
    contradiction: bool = False,
    reinforcement: bool = False,
    pair_entities: Sequence[Any] = (),
    evidence_gap: str | None = None,
    marker_category: str | None = None,
    kind: str | None = None,
) -> dict[str, Any] | None:
    """MERGE/reinforce a ``:PendingHypothesis``. Never writes S0.

    A ``None`` signal with no ``claim_target`` is a no-op (rejected fragment).
    """
    if (
        signal is None
        and not reinforcement
        and not contradiction
        and not (claim_target or "").strip()
    ):
        return None

    span_text = ""
    if signal is not None:
        span_text = signal.span or signal.text
        marker_category = marker_category or signal.marker_category
        kind = kind or signal.kind
        evidence_gap = evidence_gap or signal.evidence_gap
        if not named_witnesses:
            named_witnesses = signal.named_witnesses
        if not pair_entities and signal.pair_entity_ids:
            pair_entities = tuple((pid, None) for pid in signal.pair_entity_ids)
    elif evidence_span:
        if isinstance(evidence_span, str):
            span_text = evidence_span
        elif evidence_span:
            span_text = str(evidence_span[0])

    target = infer_claim_target(
        span_text or (signal.text if signal else ""),
        marker_category=marker_category,
        doc_id=doc_id,
        pair_entities=pair_entities,
        explicit=claim_target,
    )
    if not target:
        return None

    hyp_id = compute_hypothesis_id(target)
    existing = await _single_record(await session.run(READ_HYPOTHESIS_CYPHER, id=hyp_id))

    if existing is not None and existing.status in {"dismissed", "confirmed"}:
        return existing.as_dict()

    if contradiction:
        return await resolve_hypothesis(session, hyp_id, "dismissed")

    spans = _append_unique(
        existing.evidence_span if existing else (),
        _as_list(evidence_span) or ([span_text] if span_text else []),
    )
    witnesses = _append_unique(
        existing.witness_fragments if existing else (),
        [str(w) for w in named_witnesses if str(w).strip()],
    )
    known_spans = {s.casefold() for s in (existing.evidence_span if existing else ())}
    new_span_items = _as_list(evidence_span) or ([span_text] if span_text else [])
    is_new_span = any(s.casefold() not in known_spans for s in new_span_items if s)
    is_reinforcement = bool(reinforcement or (existing is not None and is_new_span))
    listen_count = existing.listen_count if existing else 0
    confidence, should_promote = _confidence_and_promote(
        existing=existing,
        named_witnesses=witnesses or named_witnesses,
        reinforcement=is_reinforcement,
        listen_count=listen_count,
    )
    already_promoted = bool(existing.promoted) if existing else False
    promoted = already_promoted or should_promote
    gap = evidence_gap or (existing.evidence_gap if existing else "") or "predicate without pair"

    params = {
        "id": hyp_id,
        "claim_target": target,
        "evidence_span": spans,
        "witness_fragments": witnesses,
        "evidence_gap": gap,
        "confidence": confidence,
        "status": "open",
        "marker_category": marker_category or (existing.marker_category if existing else None),
        "kind": kind or (existing.kind if existing else None),
        "origin_doc_id": (existing.origin_doc_id if existing else doc_id) or doc_id,
        "origin_doc_count": existing.origin_doc_count if existing else origin_doc_count,
        "listen_count": listen_count,
        "promoted": promoted,
    }
    await session.run(MERGE_HYPOTHESIS_CYPHER, **params)
    record = PendingHypothesisRecord(
        id=hyp_id,
        claim_target=target,
        evidence_span=tuple(spans),
        witness_fragments=tuple(witnesses),
        evidence_gap=gap,
        confidence=confidence,
        status="open",
        marker_category=params["marker_category"],
        kind=params["kind"],
        origin_doc_id=str(params["origin_doc_id"] or ""),
        origin_doc_count=int(params["origin_doc_count"] or 0),
        listen_count=listen_count,
        promoted=promoted,
    )
    if should_promote and not already_promoted:
        await _publish_promoted(job_id, record)
    return record.as_dict()


async def read_hypothesis(
    session: AsyncSession, hypothesis_id: str
) -> dict[str, Any] | None:
    record = await _single_record(
        await session.run(READ_HYPOTHESIS_CYPHER, id=hypothesis_id)
    )
    return record.as_dict() if record is not None else None


async def update_evidence_gap(
    session: AsyncSession,
    hypothesis_id: str,
    evidence_gap: str,
) -> dict[str, Any] | None:
    """F22.5: enrich the evidence-gap question. Never writes S0."""
    record = await _single_record(
        await session.run(
            SET_EVIDENCE_GAP_CYPHER,
            id=hypothesis_id,
            evidence_gap=evidence_gap,
        )
    )
    return record.as_dict() if record is not None else None


async def list_open_hypotheses(session: AsyncSession) -> list[dict[str, Any]]:
    records = await _all_records(await session.run(LIST_OPEN_HYPOTHESES_CYPHER))
    return [rec.as_dict() for rec in records]


async def resolve_hypothesis(
    session: AsyncSession,
    hypothesis_id: str,
    status: HypothesisStatus,
) -> dict[str, Any] | None:
    if status not in {"confirmed", "dismissed"}:
        raise ValueError(f"status must be confirmed|dismissed, got {status!r}")
    record = await _single_record(
        await session.run(RESOLVE_HYPOTHESIS_CYPHER, id=hypothesis_id, status=status)
    )
    return record.as_dict() if record is not None else None


async def route_chunk_signal(
    session: AsyncSession,
    *,
    chunk_text: str,
    pair_entities: Sequence[Any] = (),
    s0_written: bool = False,
    node_ids: Sequence[str] = (),
    doc_id: str = "",
    job_id: str = "",
    origin_doc_count: int = 0,
) -> dict[str, Any] | None:
    """F20.2: classify then maybe MERGE a hypothesis. Never writes S0.

    Flag off → immediate no-op (zero queries). ``None`` from the gate → no
    ``:PendingHypothesis``.
    """
    if not settings.ENABLE_CONTEXT_LAYER:
        return None
    has_comp = False
    if s0_written and has_t2_marker(chunk_text) and node_ids:
        has_comp = await has_different_tail_comparables(session, node_ids)
    signal = classify_fragment_relevance(
        chunk_text,
        pair_entities,
        S0Outcome(relation_written=s0_written, has_comparables=has_comp),
    )
    if signal is None:
        return None
    return await create_or_reinforce_hypothesis(
        session,
        signal=signal,
        doc_id=doc_id,
        job_id=job_id,
        origin_doc_count=origin_doc_count,
        pair_entities=pair_entities,
    )


def _lexical_overlap(new_text: str, hyp: dict[str, Any]) -> bool:
    target = str(hyp.get("claim_target") or "").casefold()
    folded = (new_text or "").casefold()
    if target and target not in {"unknown"} and target in folded:
        return True
    new_tokens = _content_tokens(new_text)
    hyp_tokens = _content_tokens(
        " ".join([str(hyp.get("claim_target") or ""), *_as_list(hyp.get("evidence_span"))])
    )
    if not new_tokens or not hyp_tokens:
        return False
    overlap = new_tokens & hyp_tokens
    return len(overlap) >= 1 and (len(overlap) / max(len(hyp_tokens), 1)) >= 0.2


def _is_contradiction(new_text: str, hyp: dict[str, Any]) -> bool:
    folded = f" {(new_text or '').casefold()} "
    if not any(marker in folded for marker in _DENIAL_MARKERS):
        return False
    return _lexical_overlap(new_text, hyp) or str(hyp.get("marker_category") or "") == "retraction"


def _hit_mentions_witnesses(
    hits: Sequence[Any],
    facts: Sequence[dict[str, Any]],
    hyp: dict[str, Any],
) -> bool:
    witnesses = {
        w.casefold()
        for w in [
            *_as_list(hyp.get("witness_fragments")),
            str(hyp.get("claim_target") or ""),
        ]
        if w and w.casefold() not in _STOPWORDS
    }
    if not witnesses:
        return False
    blobs: list[str] = []
    for hit in hits:
        blobs.append(str(getattr(hit, "name", "") or ""))
        blobs.append(str(getattr(hit, "summary", "") or ""))
        if isinstance(hit, dict):
            blobs.extend(str(hit.get(k) or "") for k in ("name", "summary", "text", "witness_text"))
    for fact in facts:
        blobs.extend(
            str(fact.get(k) or "")
            for k in ("name", "summary", "text", "witness_text", "from_name", "to_name")
        )
    joined = " ".join(blobs).casefold()
    return any(w in joined for w in witnesses if len(w) > 2)


async def listen_open_hypotheses(
    session: AsyncSession,
    *,
    doc_id: str,
    chunks: Sequence[Any] = (),
    job_id: str = "",
    new_text: str | None = None,
) -> list[dict[str, Any]]:
    """F20.5: re-read open hypotheses against a subsequent document.

    Flag off → zero queries. Hypotheses minted from ``doc_id`` itself are
    skipped (listen is for later documents). Contradiction → ``dismissed``
    (leaves untouched). Overlap / retrieval-hit witnesses → reinforce.
    Past ``PENDING_HYPOTHESIS_LISTEN_WINDOW`` without reinforcement → stay
    ``open``, no auto-promotion.
    """
    if not settings.ENABLE_CONTEXT_LAYER:
        return []
    from app.pipeline.context_retrieval import facts_from_source, search_fulltext, search_vector

    open_hyps = await list_open_hypotheses(session)
    subsequent = [h for h in open_hyps if str(h.get("origin_doc_id") or "") != doc_id]
    if not subsequent:
        return []

    if new_text is None:
        parts: list[str] = []
        for chunk in chunks:
            if isinstance(chunk, Chunk):
                parts.append(chunk.text)
            elif isinstance(chunk, dict):
                parts.append(str(chunk.get("text") or ""))
            else:
                parts.append(str(getattr(chunk, "text", "") or ""))
        new_text = " ".join(parts)

    query_parts = [str(h.get("claim_target") or "") for h in subsequent]
    for hyp in subsequent:
        query_parts.extend(_as_list(hyp.get("evidence_span"))[:2])
        query_parts.extend(_as_list(hyp.get("witness_fragments"))[:2])
    query = " ".join(p for p in query_parts if p).strip()[:500]

    hits: list[Any] = []
    if query:
        try:
            hits.extend(await search_fulltext(session, query, k=5))
        except Exception:
            logger.debug("listen search_fulltext skipped", exc_info=True)
        try:
            hits.extend(await search_vector(session, query, k=5))
        except Exception:
            logger.debug("listen search_vector skipped", exc_info=True)
    try:
        facts = await facts_from_source(session, doc_id)
    except Exception:
        logger.debug("listen facts_from_source skipped", exc_info=True)
        facts = []

    updated: list[dict[str, Any]] = []
    for hyp in subsequent:
        bumped = await _single_record(
            await session.run(INCREMENT_LISTEN_CYPHER, id=hyp["id"])
        )
        current = (
            bumped.as_dict()
            if bumped is not None
            else {**hyp, "listen_count": hyp.get("listen_count", 0) + 1}
        )
        if _is_contradiction(new_text or "", current):
            resolved = await resolve_hypothesis(session, hyp["id"], "dismissed")
            if resolved is not None:
                updated.append(resolved)
            continue
        retrieval_hit = _hit_mentions_witnesses(hits, facts, current)
        if _lexical_overlap(new_text or "", current) or retrieval_hit:
            extra_names = extract_named_witnesses(new_text or "")
            rec = await create_or_reinforce_hypothesis(
                session,
                claim_target=str(current.get("claim_target") or ""),
                evidence_span=new_text or "",
                named_witnesses=extra_names,
                doc_id=doc_id,
                job_id=job_id,
                reinforcement=True,
                marker_category=current.get("marker_category"),
                kind=current.get("kind"),
                evidence_gap=current.get("evidence_gap"),
            )
            if rec is not None:
                updated.append(rec)
            continue
        updated.append(current)
    return updated
