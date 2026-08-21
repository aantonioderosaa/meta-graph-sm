"""Global retractions scoped to a source (Fase 21.2).

A T2 retraction is never a ``DELETE`` and never a fan-out across the KB.
``facts_from_source`` (F19.3) resolves the source. Identifiable source →
Famiglia B (``CONTRADICTS`` / ``UPDATED_BY`` / ``SUPERSEDES``) on that
source's relation-facts only. Unidentifiable source → ``:PendingHypothesis``
with ``evidence_gap="fonte non identificata"``, zero S0.

The corpus-level running summary is **not** a truth overlay.
This module never matches that singleton and never reads its macro text
as the set of claims to retract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from neo4j import AsyncSession

from app.core.config import settings
from app.models.relations import RelationLabel
from app.pipeline.chunking import Chunk
from app.pipeline.context_retrieval import facts_from_source
from app.pipeline.entity_relation_resolution import map_temporal_transition
from app.pipeline.ingestion import write_contradicts
from app.pipeline.pending_hypothesis import (
    compute_hypothesis_id,
    create_or_reinforce_hypothesis,
)
from app.pipeline.relevance_gate import RETRACTION_MARKERS, match_t2_marker

UNIDENTIFIED_SOURCE_GAP = "fonte non identificata"

_THIS_SOURCE_MARKERS = (
    "questo documento",
    "this document",
    "in this document",
    "ti ho detto",
    "che ho detto",
    "che ti ho detto",
    "i told you",
    "i said",
    "i have told you",
    "everything i told you",
    "everything i said",
    "finora",
    "so far",
)
_NAMED_DOC_RE = re.compile(
    r"\b(?:doc(?:ument)?[_-]?id[:\s=]+|documento\s+)([a-zA-Z0-9._-]+)\b",
    re.IGNORECASE,
)
_BARE_DOC_RE = re.compile(r"\b(doc-[a-zA-Z0-9._-]+)\b", re.IGNORECASE)


@dataclass(frozen=True)
class RetractionScopeResult:
    identifiable: bool
    source_doc_id: str | None
    touched_relation_ids: tuple[str, ...]
    hypothesis_id: str | None


def _chunk_text(chunk: Chunk | Any) -> str:
    if isinstance(chunk, dict):
        return str(chunk.get("text") or "")
    return str(getattr(chunk, "text", "") or "")


def _chunk_doc_id(chunk: Chunk | Any) -> str:
    if isinstance(chunk, dict):
        return str(chunk.get("doc_id") or "")
    return str(getattr(chunk, "doc_id", "") or "")


def identify_source_doc_id(chunk: Chunk | Any) -> str | None:
    """Return a source ``doc_id`` only when the text identifies one.

    The chunk's own ``doc_id`` is "this document" when the retraction deictic
    points at prior speech of this source (``ti ho detto``, ``this document``,
    ``finora``, …). A named prior ``doc_id`` in the text wins. No identifier
    → ``None`` (unidentifiable; never fan-out).
    """
    text = _chunk_text(chunk)
    folded = f" {text.casefold()} "
    named = _NAMED_DOC_RE.search(text) or _BARE_DOC_RE.search(text)
    if named:
        return named.group(1)
    chunk_doc = _chunk_doc_id(chunk).strip()
    if chunk_doc and any(marker in folded for marker in _THIS_SOURCE_MARKERS):
        return chunk_doc
    return None


def _retraction_label(chunk_text: str, fact_text: str) -> RelationLabel:
    """Retraction is disagreement, not succession.

    ``map_temporal_transition`` is reused only for the error/correction →
    ``updated_by`` branch. Succession markers are ignored here: a global
    retraction is never a state succession on the source's claims (F24.3
    kept this policy; the old ``ora è``/``finora è`` substring bug is
    fixed in ``contains_lexical_marker`` and is no longer the reason).
    """
    label = map_temporal_transition(chunk_text, fact_text)
    if label == RelationLabel.updated_by:
        return label
    return RelationLabel.contradicts


async def _apply_famiglia_b_on_fact(
    session: AsyncSession,
    fact: dict[str, Any],
    chunk_text: str,
) -> None:
    from app.pipeline.entity_relation_resolution import APPLY_DIFFERENT_TAIL_UPDATED_BY_CYPHER

    from_id = str(fact.get("from_id") or "")
    to_id = str(fact.get("to_id") or "")
    if not from_id or not to_id:
        return
    fact_text = str(fact.get("text") or fact.get("relation") or "")
    label = _retraction_label(chunk_text, fact_text)
    kernel_parent = str(fact.get("kernel_parent") or "contradicts")
    relation = str(fact.get("relation") or fact_text or "contradicts")
    if label == RelationLabel.updated_by:
        await session.run(
            APPLY_DIFFERENT_TAIL_UPDATED_BY_CYPHER,
            head_id=from_id,
            old_tail_id=to_id,
            new_tail_id=to_id,
            old_relation=relation,
            new_relation=relation,
            kernel_parent=kernel_parent,
        )
        return
    await write_contradicts(
        session,
        left_id=from_id,
        right_id=to_id,
        subject_id=from_id,
        relation=relation,
        kernel_parent=kernel_parent,
    )


async def _open_unidentified_hypothesis(
    session: AsyncSession,
    *,
    text: str,
    doc_id: str,
) -> str | None:
    claim_target = doc_id or "retraction:unknown"
    rec = await create_or_reinforce_hypothesis(
        session,
        claim_target=claim_target,
        evidence_span=text,
        doc_id=doc_id,
        evidence_gap=UNIDENTIFIED_SOURCE_GAP,
        marker_category="retraction",
        kind="t2",
    )
    if rec is None:
        return None
    return str(rec.get("id") or compute_hypothesis_id(claim_target))


async def resolve_retraction_scope(
    session: AsyncSession,
    chunk: Chunk | Any,
) -> RetractionScopeResult:
    """On a T2 retraction: Famiglia B on that source, or an open hypothesis.

    Directly callable from Fase 22. Source identity comes from the chunk
    text plus ``facts_from_source``; the running corpus summary is ignored.
    """
    text = _chunk_text(chunk)
    hit = match_t2_marker(text)
    empty = RetractionScopeResult(
        identifiable=False,
        source_doc_id=None,
        touched_relation_ids=(),
        hypothesis_id=None,
    )
    if hit is None or hit[0] != "retraction":
        return empty

    source_doc_id = identify_source_doc_id(chunk)
    if not source_doc_id:
        hyp_id = await _open_unidentified_hypothesis(
            session, text=text, doc_id=_chunk_doc_id(chunk)
        )
        return RetractionScopeResult(
            identifiable=False,
            source_doc_id=None,
            touched_relation_ids=(),
            hypothesis_id=hyp_id,
        )

    facts = await facts_from_source(session, source_doc_id)
    touched: list[str] = []
    for fact in facts:
        if fact.get("kind") != "relation":
            continue
        if str(fact.get("doc_id") or source_doc_id) != source_doc_id:
            continue
        await _apply_famiglia_b_on_fact(session, fact, text)
        rid = str(fact.get("id") or "")
        if rid:
            touched.append(rid)
    return RetractionScopeResult(
        identifiable=True,
        source_doc_id=source_doc_id,
        touched_relation_ids=tuple(touched),
        hypothesis_id=None,
    )


async def maybe_resolve_retraction_scope(
    session: AsyncSession,
    chunk: Chunk | Any,
) -> RetractionScopeResult | None:
    """Ingest hook. Flag off → no queries, no writes."""
    if not settings.ENABLE_CONTEXT_LAYER:
        return None
    text = _chunk_text(chunk)
    hit = match_t2_marker(text)
    if hit is None or hit[0] != "retraction":
        return None
    return await resolve_retraction_scope(session, chunk)


# Touch RETRACTION_MARKERS so this module reuses the gate tuple (no copy).
assert RETRACTION_MARKERS, "RETRACTION_MARKERS must stay the single source of truth"
