"""Quantifier → event (Fase 21.1).

A T2 quantifier is a **scene**, never a collective entity and never the full
``MEMBER_OF`` extension of the named genre.

Closed-scope test (documented, concrete)
----------------------------------------
Scope is **closed** when at least ``CLOSED_SCOPE_MIN_MEMBERS`` (2) identified
leaf ``:Node`` members share the scene. Same ``doc_id`` via
``DERIVED_FROM`` → ``:Chunk`` is the *minimum* closed-scope signal (the plan's
example is three dogs in the same document). Named witnesses already in the
chunk, and members whose latest ``Spaziale`` tail is a place of this document,
may add to the scene. Zero or one member, or only the genre name with no
scene, is **not** closed: ``:PendingHypothesis`` stays/opens, **zero S0**.

Writes reuse Fase 6 ``reify_shared_situation`` (Evento + R5 ``participates``)
and Fase 9 ``map_temporal_transition`` / different-tail SUPERSEDES /
UPDATED_BY. Never ``DELETE``. Never a ``:Node`` whose name is the genre
plural.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from neo4j import AsyncSession

from app.core.config import settings
from app.models.kernel import IS_A, MEMBER_OF, RelationKernelType
from app.models.relations import RelationLabel
from app.pipeline.chunking import Chunk
from app.pipeline.entity_relation_resolution import (
    _apply_different_tail_temporal,
    map_temporal_transition,
)
from app.pipeline.event_relation_resolution import reify_shared_situation
from app.pipeline.pending_hypothesis import (
    compute_hypothesis_id,
    create_or_reinforce_hypothesis,
    infer_claim_target,
    resolve_hypothesis,
)
from app.pipeline.relevance_gate import (
    QUANTIFIER_MARKERS,
    extract_named_witnesses,
    match_t2_marker,
)

CLOSED_SCOPE_MIN_MEMBERS = 2
"""Minimum identified leaf members sharing the scene for a closed quantifier."""

_MEMBER_OF_REL = MEMBER_OF.upper()
_ISA_REL = IS_A.upper()
_SPATIAL_PARENT = RelationKernelType.Spaziale.value
_COLLECTIVE_BLOCKLIST = frozenset(
    {
        "cani",
        "i cani",
        "cane",
        "dogs",
        "the dogs",
        "dog",
        "gatti",
        "i gatti",
        "cats",
        "the cats",
    }
)
_TOKEN_RE = re.compile(r"[a-zàèéìòù']+", re.IGNORECASE)
_AFTER_QUANTIFIER_RE = re.compile(
    r"(?:tutti i|tutti gli|tutte le|tutti|tutte|ogni|ciascun[oa]?|"
    r"all the|all of the|all of them|every|each of the)\s+([a-zàèéìòù']+)",
    re.IGNORECASE,
)

FIND_SCOPED_DOC_MEMBERS_CYPHER = f"""
MATCH (genre:Concept)
WHERE toLower(coalesce(genre.name, '')) IN $genre_names
MATCH (c:Chunk {{doc_id: $doc_id}})
MATCH (leaf:Node)-[:DERIVED_FROM]->(c)
WHERE leaf.merged_into IS NULL
  AND coalesce(leaf.type, 'entity') <> 'event'
MATCH (leaf)-[:{_MEMBER_OF_REL}]->(home:Concept)
WHERE home = genre OR (home)-[:{_ISA_REL}*0..8]->(genre)
RETURN DISTINCT leaf.id AS id, leaf.name AS name
"""

FIND_NAMED_GENRE_MEMBERS_CYPHER = f"""
MATCH (genre:Concept)
WHERE toLower(coalesce(genre.name, '')) IN $genre_names
MATCH (leaf:Node)-[:{_MEMBER_OF_REL}]->(home:Concept)
WHERE leaf.merged_into IS NULL
  AND coalesce(leaf.type, 'entity') <> 'event'
  AND toLower(leaf.name) IN $witness_names
  AND (home = genre OR (home)-[:{_ISA_REL}*0..8]->(genre))
RETURN DISTINCT leaf.id AS id, leaf.name AS name
"""

FIND_PLACE_SCOPED_MEMBERS_CYPHER = f"""
MATCH (c:Chunk {{doc_id: $doc_id}})
MATCH (place:Node)-[:DERIVED_FROM]->(c)
WHERE place.merged_into IS NULL
  AND (place.kernel_category = 'Luogo' OR coalesce(place.type, '') = 'place')
MATCH (leaf:Node)-[r:Relation]->(place)
WHERE r.is_latest = true
  AND coalesce(r.kernel_parent, '') = $spatial_parent
  AND leaf.merged_into IS NULL
  AND coalesce(leaf.type, 'entity') <> 'event'
MATCH (leaf)-[:{_MEMBER_OF_REL}]->(home:Concept)
MATCH (genre:Concept)
WHERE toLower(coalesce(genre.name, '')) IN $genre_names
  AND (home = genre OR (home)-[:{_ISA_REL}*0..8]->(genre))
RETURN DISTINCT leaf.id AS id, leaf.name AS name
"""

FIND_LATEST_STATE_REL_CYPHER = """
MATCH (n:Node {id: $member_id})-[r:Relation]->(other:Node)
WHERE r.is_latest = true
  AND n.merged_into IS NULL
  AND other.merged_into IS NULL
  AND coalesce(r.normalized_relation, '') <> 'participates'
  AND (
    coalesce(r.kernel_parent, '') = $spatial_parent
    OR coalesce(r.kernel_parent, '') = 'Stato'
  )
RETURN other.id AS tail_id,
       other.name AS tail_name,
       r.relation AS relation,
       r.normalized_relation AS normalized_relation,
       r.kernel_parent AS kernel_parent,
       r.created_at AS created_at
ORDER BY r.created_at DESC
LIMIT 1
"""


@dataclass(frozen=True)
class QuantifierScopeResult:
    closed: bool
    event_id: str | None
    member_ids: tuple[str, ...]
    hypothesis_id: str | None
    genre_hint: str


def _chunk_text(chunk: Chunk | Any) -> str:
    if isinstance(chunk, dict):
        return str(chunk.get("text") or "")
    return str(getattr(chunk, "text", "") or "")


def _chunk_id(chunk: Chunk | Any) -> str:
    if isinstance(chunk, dict):
        return str(chunk.get("id") or "")
    return str(getattr(chunk, "id", "") or "")


def _chunk_doc_id(chunk: Chunk | Any) -> str:
    if isinstance(chunk, dict):
        return str(chunk.get("doc_id") or "")
    return str(getattr(chunk, "doc_id", "") or "")


def genre_name_variants(name: str) -> tuple[str, ...]:
    """Lexical singular/plural folds so 'cani' matches Concept 'cane'."""
    raw = (name or "").strip().casefold()
    if not raw:
        return ()
    variants = {raw}
    if raw.endswith("i") and len(raw) > 2:
        variants.add(raw[:-1] + "e")
        variants.add(raw[:-1] + "o")
        variants.add(raw[:-1])
    if raw.endswith("e") and len(raw) > 2:
        variants.add(raw[:-1] + "i")
        variants.add(raw[:-1] + "o")
    if raw.endswith("o") and len(raw) > 2:
        variants.add(raw[:-1] + "i")
        variants.add(raw + "s")
    if raw.endswith("s") and len(raw) > 2:
        variants.add(raw[:-1])
    if raw.endswith("ies") and len(raw) > 3:
        variants.add(raw[:-3] + "y")
    if raw.endswith("y") and len(raw) > 2:
        variants.add(raw[:-1] + "ies")
    return tuple(sorted(variants))


def infer_genre_hint(text: str, concept_hint: str | None = None) -> str:
    if (concept_hint or "").strip():
        return concept_hint.strip()
    match = _AFTER_QUANTIFIER_RE.search(text or "")
    if match:
        return match.group(1)
    folded = f" {(text or '').casefold()} "
    for marker in QUANTIFIER_MARKERS:
        idx = folded.find(marker)
        if idx < 0:
            continue
        rest = folded[idx + len(marker) :]
        tokens = _TOKEN_RE.findall(rest)
        if tokens:
            return tokens[0]
    return ""


def _situation_name(text: str) -> str:
    span = " ".join((text or "").split())
    return span[:160] if span else "quantifier event"


def _exit_state_relation(text: str) -> str:
    folded = (text or "").casefold()
    if any(tok in folded for tok in ("usciti", "uscito", "uscite", " left ", "left.")):
        return "è uscito"
    if "fuori" in folded or "outside" in folded:
        return "è fuori"
    return "è in un nuovo stato"


def _is_collective_name(name: str) -> bool:
    return (name or "").strip().casefold() in _COLLECTIVE_BLOCKLIST


def _scope_is_closed(member_ids: list[str]) -> bool:
    return len(member_ids) >= CLOSED_SCOPE_MIN_MEMBERS


async def _collect_rows(result: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    async for record in result:
        rows.append(dict(record))
    return rows


async def _add_members(
    session: AsyncSession,
    collected: dict[str, str],
    cypher: str,
    **params: Any,
) -> None:
    result = await session.run(cypher, **params)
    for row in await _collect_rows(result):
        nid = str(row.get("id") or "")
        name = str(row.get("name") or "")
        if not nid or _is_collective_name(name):
            continue
        collected.setdefault(nid, name)


async def collect_in_scope_members(
    session: AsyncSession,
    *,
    doc_id: str,
    genre_hint: str,
    chunk_text: str,
) -> dict[str, str]:
    """Members of ``genre_hint`` in this scene — never the full genre extension.

    Starts from this document's chunks (and optionally named witnesses / places
    of this document). Does not ``MATCH`` every ``MEMBER_OF`` the genre.
    """
    names = list(genre_name_variants(genre_hint))
    if not names:
        return {}
    collected: dict[str, str] = {}
    if doc_id:
        await _add_members(
            session,
            collected,
            FIND_SCOPED_DOC_MEMBERS_CYPHER,
            genre_names=names,
            doc_id=doc_id,
        )
    witnesses = [
        w.casefold()
        for w in extract_named_witnesses(chunk_text)
        if w.strip() and not _is_collective_name(w)
    ]
    if witnesses:
        await _add_members(
            session,
            collected,
            FIND_NAMED_GENRE_MEMBERS_CYPHER,
            genre_names=names,
            witness_names=witnesses,
        )
    if doc_id:
        await _add_members(
            session,
            collected,
            FIND_PLACE_SCOPED_MEMBERS_CYPHER,
            genre_names=names,
            doc_id=doc_id,
            spatial_parent=_SPATIAL_PARENT,
        )
    return collected


def _temporal_label_for_state(new_text: str, old_text: str) -> RelationLabel:
    """Quantifier state change is a succession unless the texts say otherwise."""
    label = map_temporal_transition(new_text, old_text)
    if label in (RelationLabel.none, RelationLabel.extends):
        return RelationLabel.supersedes
    return label


async def _apply_member_state_updates(
    session: AsyncSession,
    *,
    member_ids: dict[str, str],
    event_id: str,
    chunk_text: str,
) -> None:
    from app.pipeline.ingestion import write_node_relation

    new_relation = _exit_state_relation(chunk_text)
    for member_id, member_name in member_ids.items():
        result = await session.run(
            FIND_LATEST_STATE_REL_CYPHER,
            member_id=member_id,
            spatial_parent=_SPATIAL_PARENT,
        )
        old = None
        async for row in result:
            old = dict(row)
            break
        if old is None:
            continue
        old_tail = str(old.get("tail_id") or "")
        if not old_tail or old_tail == event_id:
            continue
        kernel_parent = str(old.get("kernel_parent") or _SPATIAL_PARENT)
        old_relation = str(old.get("relation") or "")
        await write_node_relation(
            session,
            head_id=member_id,
            tail_id=event_id,
            relation=new_relation,
            normalized_relation=new_relation,
            head_name=member_name,
            tail_name=_situation_name(chunk_text),
            kernel_parent=kernel_parent,
            witness_source=member_name or chunk_text,
            witness_target=chunk_text or member_name,
        )
        label = _temporal_label_for_state(chunk_text, old_relation)
        await _apply_different_tail_temporal(
            session,
            label,
            head_id=member_id,
            old_tail_id=old_tail,
            new_tail_id=event_id,
            old_relation=old_relation,
            new_relation=new_relation,
            kernel_parent=kernel_parent,
        )


async def _open_unclosed_hypothesis(
    session: AsyncSession,
    *,
    text: str,
    doc_id: str,
    genre_hint: str,
) -> str | None:
    target = infer_claim_target(
        text,
        marker_category="quantifier",
        doc_id=doc_id,
        explicit=genre_hint or None,
    )
    rec = await create_or_reinforce_hypothesis(
        session,
        claim_target=target,
        evidence_span=text,
        doc_id=doc_id,
        evidence_gap="quantifier scope not closed",
        marker_category="quantifier",
        kind="t2",
    )
    if rec is None:
        return None
    return str(rec.get("id") or compute_hypothesis_id(target))


async def _confirm_closed_hypothesis(
    session: AsyncSession,
    *,
    text: str,
    doc_id: str,
    genre_hint: str,
) -> None:
    target = infer_claim_target(
        text,
        marker_category="quantifier",
        doc_id=doc_id,
        explicit=genre_hint or None,
    )
    hid = compute_hypothesis_id(target)
    await resolve_hypothesis(session, hid, "confirmed")


async def resolve_quantifier_scope(
    session: AsyncSession,
    chunk: Chunk | Any,
    concept_hint: str | None = None,
) -> QuantifierScopeResult:
    """On a T2 quantifier: Evento + R5 if the scene is closed, else hypothesis.

    Never creates a collective entity. Never fans out to every MEMBER_OF the
    genre. Directly callable from Fase 22; the ingest hook is
    ``maybe_resolve_quantifier_scope``.
    """
    text = _chunk_text(chunk)
    hit = match_t2_marker(text)
    empty = QuantifierScopeResult(
        closed=False, event_id=None, member_ids=(), hypothesis_id=None, genre_hint=""
    )
    if hit is None or hit[0] != "quantifier":
        return empty

    genre_hint = infer_genre_hint(text, concept_hint)
    doc_id = _chunk_doc_id(chunk)
    members = await collect_in_scope_members(
        session, doc_id=doc_id, genre_hint=genre_hint, chunk_text=text
    )
    member_ids = tuple(members.keys())

    if not _scope_is_closed(list(member_ids)):
        hyp_id = await _open_unclosed_hypothesis(
            session, text=text, doc_id=doc_id, genre_hint=genre_hint
        )
        return QuantifierScopeResult(
            closed=False,
            event_id=None,
            member_ids=member_ids,
            hypothesis_id=hyp_id,
            genre_hint=genre_hint,
        )

    situation = _situation_name(text)
    event_id = await reify_shared_situation(
        session,
        participant_node_ids=list(member_ids),
        situation_name=situation,
        chunk_id=_chunk_id(chunk) or None,
    )
    await _apply_member_state_updates(
        session,
        member_ids=members,
        event_id=event_id,
        chunk_text=text,
    )
    await _confirm_closed_hypothesis(
        session, text=text, doc_id=doc_id, genre_hint=genre_hint
    )
    return QuantifierScopeResult(
        closed=True,
        event_id=event_id,
        member_ids=member_ids,
        hypothesis_id=None,
        genre_hint=genre_hint,
    )


async def maybe_resolve_quantifier_scope(
    session: AsyncSession,
    chunk: Chunk | Any,
    concept_hint: str | None = None,
) -> QuantifierScopeResult | None:
    """Ingest hook. Flag off → no queries, no writes."""
    if not settings.ENABLE_CONTEXT_LAYER:
        return None
    text = _chunk_text(chunk)
    hit = match_t2_marker(text)
    if hit is None or hit[0] != "quantifier":
        return None
    return await resolve_quantifier_scope(session, chunk, concept_hint)
