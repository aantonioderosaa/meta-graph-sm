"""Deterministic structural-relevance gate (Fase 20).

Zero LLM calls. Same lexical style as ``map_temporal_transition``.
A fragment enters the hypothesis funnel only if T1, T2, or T3 matches;
otherwise the function returns ``None`` and ingestion is unchanged.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from neo4j import AsyncSession

from app.pipeline.entity_relation_resolution import ERROR_MARKERS, SUCCESSION_MARKERS

MarkerCategory = Literal["quantifier", "retraction", "error", "succession"]
SignalKind = Literal["t1", "t2", "t3"]

# Quantifier / collective-scope wording (IT+EN). Keep in this module: the
# temporal markers live in entity_relation_resolution (one source of truth);
# these two tuples are the Fase 20 additions.
QUANTIFIER_MARKERS = (
    "tutti i ",
    "tutti gli ",
    "tutte le ",
    "tutti ",
    "tutte ",
    "ogni ",
    "ciascun",
    "all the ",
    "all of the ",
    "all of them",
    "every ",
    "each of the ",
)

RETRACTION_MARKERS = (
    "tutto è falso",
    "tutto e' falso",
    "tutto e falso",
    "tutto quello che ti ho detto",
    "everything i told you is false",
    "everything i said is false",
    "everything i told you",
    "non è vero niente",
    "non e' vero niente",
    "non e vero niente",
    "niente di tutto questo è vero",
    "it was all a lie",
    "forget everything i said",
    "tutto ciò che ho detto è falso",
    "tutto cio' che ho detto è falso",
)

# T1 lexical predicate test. Conservative IT+EN copula / motion / assertion
# markers. A fragment without one of these (or a verb-like suffix) is not T1
# even when pair extraction produced fewer than two usable entities.
_PREDICATE_MARKERS = (
    " sono ",
    " è ",
    " e' ",
    " ha ",
    " hanno ",
    "usciti",
    "uscito",
    "uscite",
    "andati",
    "andato",
    "andate",
    "stati",
    "stato",
    "diventa",
    "risulta",
    " restano ",
    " rimane ",
    " are ",
    " is ",
    " was ",
    " were ",
    " have ",
    " has ",
    " left ",
    " went ",
    " became",
    " remain",
    "falso",
    "false",
)

_IT_VERB_SUFFIXES = (
    "are",
    "ere",
    "ire",
    "ato",
    "uto",
    "ito",
    "ati",
    "uti",
    "iti",
    "ano",
    "ono",
)

_GENERIC_TOKENS = frozenset(
    {
        "cani",
        "cane",
        "dogs",
        "dog",
        "gatti",
        "gatto",
        "cats",
        "cat",
        "tutti",
        "tutte",
        "ogni",
        "all",
        "every",
        "everything",
        "tutto",
        "persone",
        "people",
        "person",
        "the",
        "and",
        "dei",
        "delle",
        "degli",
    }
)

_TOKEN_RE = re.compile(r"[a-zàèéìòù0-9']+", re.IGNORECASE)
_PROPER_RE = re.compile(r"\b[A-ZÀÈÉÌÒÙ][a-zàèéìòù]{2,}\b")


@dataclass(frozen=True)
class S0Outcome:
    """Ingest-time stand-in for F18.3 / entity–entity :Relation writes.

    ``relation_written``: this chunk created at least one entity–entity
    ``:Relation``. ``has_comparables``: a same-head different-tail latest
    pair already exists for the chunk's node ids (FIND_DIFFERENT_TAIL_PAIRS
    shape). T3 fires only when a T2 marker is also present.
    """

    relation_written: bool = False
    has_comparables: bool = False


@dataclass(frozen=True)
class FragmentSignal:
    """Payload for F20.3. Never produced for a rejected fragment."""

    kind: SignalKind
    text: str
    span: str
    pair_entity_ids: tuple[str, ...]
    marker_category: MarkerCategory | None
    evidence_gap: str
    named_witnesses: tuple[str, ...] = ()


def _padded(text: str) -> str:
    return f" {(text or '').casefold()} "


def _iter_pair_entities(
    pair_entities: Sequence[Any] | None,
) -> list[tuple[str, str, str]]:
    """Normalize pair_entities to ``(id, summary, name)`` rows."""
    rows: list[tuple[str, str, str]] = []
    for item in pair_entities or ():
        if isinstance(item, tuple) and len(item) >= 2:
            nid, ent = item[0], item[1]
            rows.append((str(nid or ""), _summary_of(ent), _name_of(ent)))
        elif isinstance(item, Mapping):
            rows.append(
                (
                    str(item.get("id") or ""),
                    str(item.get("summary") or ""),
                    str(item.get("name") or ""),
                )
            )
        else:
            rows.append(("", _summary_of(item), _name_of(item)))
    return rows


def _summary_of(ent: Any) -> str:
    if ent is None:
        return ""
    if isinstance(ent, str):
        return ""
    return str(getattr(ent, "summary", "") or "")


def _name_of(ent: Any) -> str:
    if ent is None:
        return ""
    if isinstance(ent, str):
        return ent
    return str(getattr(ent, "name", "") or "")


def _pair_insufficient(pair_entities: Sequence[Any] | None) -> bool:
    rows = _iter_pair_entities(pair_entities)
    if len(rows) < 2:
        return True
    usable = sum(1 for _nid, summary, _name in rows if summary.strip())
    return usable < 2


def _has_lexical_predicate(text: str) -> bool:
    folded = _padded(text)
    if any(marker in folded for marker in _PREDICATE_MARKERS):
        return True
    for token in _TOKEN_RE.findall(text or ""):
        low = token.casefold()
        if len(low) >= 4 and low.endswith(_IT_VERB_SUFFIXES):
            return True
    return False


def match_t2_marker(text: str) -> tuple[MarkerCategory, str] | None:
    """Return ``(category, matched_marker)`` for the first T2 hit, else None.

    Retraction is checked before quantifier (more specific); then error
    and succession from the shared temporal-marker tuples.
    """
    folded = _padded(text)
    for marker in RETRACTION_MARKERS:
        if marker in folded:
            return ("retraction", marker)
    for marker in QUANTIFIER_MARKERS:
        if marker in folded:
            return ("quantifier", marker)
    for marker in ERROR_MARKERS:
        if marker in folded:
            return ("error", marker)
    for marker in SUCCESSION_MARKERS:
        if marker in folded:
            return ("succession", marker)
    return None


def has_t2_marker(text: str) -> bool:
    return match_t2_marker(text) is not None


def extract_named_witnesses(
    text: str,
    pair_entities: Sequence[Any] | None = None,
) -> tuple[str, ...]:
    """Proper-name witnesses that can close a quantifier/assertion scope.

    Generic collectives (``cani``, ``dogs``, …) are not named witnesses.
    A single proper name is enough for high confidence (plan F20.6).
    """
    seen: set[str] = set()
    names: list[str] = []

    def _add(raw: str) -> None:
        token = (raw or "").strip()
        key = token.casefold()
        if len(token) < 3 or key in _GENERIC_TOKENS or key in seen:
            return
        if not token[0].isalpha():
            return
        seen.add(key)
        names.append(token)

    for _nid, _summary, name in _iter_pair_entities(pair_entities):
        if name and name[0].isupper() and name.casefold() not in _GENERIC_TOKENS:
            _add(name)
    for match in _PROPER_RE.findall(text or ""):
        _add(match)
    return tuple(names)


def _pair_entity_ids(pair_entities: Sequence[Any] | None) -> tuple[str, ...]:
    return tuple(nid for nid, _summary, _name in _iter_pair_entities(pair_entities) if nid)


def _coerce_s0(s0_outcome: S0Outcome | Mapping[str, Any] | None) -> S0Outcome:
    if s0_outcome is None:
        return S0Outcome()
    if isinstance(s0_outcome, S0Outcome):
        return s0_outcome
    return S0Outcome(
        relation_written=bool(s0_outcome.get("relation_written")),
        has_comparables=bool(s0_outcome.get("has_comparables")),
    )


def _evidence_gap(kind: SignalKind, category: MarkerCategory | None) -> str:
    if kind == "t3":
        return "recall gap — S0 written but no F18.3 comparables"
    if category == "quantifier":
        return "quantifier scope not closed"
    if category == "retraction":
        return "fonte non identificata"
    if category in {"error", "succession"}:
        return "no temporal counterpart"
    return "predicate without pair"


def classify_fragment_relevance(
    chunk_text: str,
    pair_entities: Sequence[Any] | None = None,
    s0_outcome: S0Outcome | Mapping[str, Any] | None = None,
) -> FragmentSignal | None:
    """T1 / T2 / T3 gate. Any match → signal; else ``None`` (reject).

    Priority when several match: T3 (recall gap) > T2 (explicit marker) > T1
    (predicate without a usable pair).
    """
    text = chunk_text or ""
    s0 = _coerce_s0(s0_outcome)
    t2 = match_t2_marker(text)
    t1 = _pair_insufficient(pair_entities) and _has_lexical_predicate(text)
    t3 = bool(s0.relation_written and (not s0.has_comparables) and t2 is not None)

    if not (t1 or t2 is not None or t3):
        return None

    if t3:
        kind: SignalKind = "t3"
        category = t2[0] if t2 is not None else None
    elif t2 is not None:
        kind = "t2"
        category = t2[0]
    else:
        kind = "t1"
        category = None

    span = " ".join(text.split())
    return FragmentSignal(
        kind=kind,
        text=text,
        span=span,
        pair_entity_ids=_pair_entity_ids(pair_entities),
        marker_category=category,
        evidence_gap=_evidence_gap(kind, category),
        named_witnesses=extract_named_witnesses(text, pair_entities),
    )


async def has_different_tail_comparables(
    session: AsyncSession,
    node_ids: Sequence[str],
) -> bool:
    """True if F18.3's pairing shape already has a same-head different-tail row.

    Uses ``FIND_DIFFERENT_TAIL_PAIRS_CYPHER`` scoped to ``node_ids`` so ingest
    T3 stays deterministic and does not call the LLM classifier.
    """
    ids = [str(nid) for nid in node_ids if nid]
    if not ids:
        return False
    from app.pipeline.entity_relation_resolution import FIND_DIFFERENT_TAIL_PAIRS_CYPHER

    result = await session.run(FIND_DIFFERENT_TAIL_PAIRS_CYPHER, touched_ids=ids)
    async for _row in result:
        return True
    return False
