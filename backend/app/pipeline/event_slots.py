"""Slot assert/retract with per-fonte witness OR-Set (event triage Macrotask 1–4).

Writes reuse ``write_node_relation`` / ``write_contradicts`` plus parameterized
queries defined as module constants. Supporting set is ``witness_source_ids``
(unique ``fonte_id`` strings); ``witnesses_a`` / ``witnesses_b`` stay UI-only.
Unsupported slots flip ``is_latest=false`` and keep the edge.

Every edge this module stamps or updates carries ``caused_by_event_id`` and
``run_id``. Scalar node overwrites go through ``append_node_revision``.

LLM proposals must pass ``validate_slot_proposal`` before any write. Invented or
nearby ``kernel_parent`` values yield ``None`` (waiting/incomplete), never a
coerced member of the closed R1–R6 / A1–A7 enums.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from neo4j import AsyncSession

from app.models.kernel import AttributeKernelType, RelationKernelType
from app.pipeline.ingestion import write_contradicts, write_node_relation
from app.pipeline.reconcile import reconcile_scoped_attribute_slots

_ATTRIBUTE_VALUES = frozenset(m.value for m in AttributeKernelType)
_RELATION_VALUES = frozenset(m.value for m in RelationKernelType)
_KERNEL_PARENT_BY_VALUE: dict[str, AttributeKernelType | RelationKernelType] = {
    **{m.value: m for m in AttributeKernelType},
    **{m.value: m for m in RelationKernelType},
}
_VALID_VERBS = frozenset({"assert", "retract"})
_IDENTITY_PROPERTIES = frozenset({"id", "merged_into"})
_MISSING = object()

FIND_SLOT_RELATIONS_CYPHER = """
MATCH (h:Node {id: $head_id})-[r:Relation]->(t:Node)
WHERE r.slot_id = $slot_id
RETURN t.id AS tail_id,
       r.is_latest AS is_latest,
       r.witness_source_ids AS witness_source_ids,
       r.witness_target_ids AS witness_target_ids,
       r.witness_add_tags AS witness_add_tags,
       r.witnesses_a AS witnesses_a,
       r.witnesses_b AS witnesses_b,
       r.relation AS relation,
       r.kernel_parent AS kernel_parent,
       r.normalized_relation AS normalized_relation
"""

STAMP_SLOT_ON_LATEST_CYPHER = """
MATCH (h:Node {id: $head_id})-[r:Relation]->(t:Node {id: $tail_id})
WHERE r.kernel_parent = $kernel_parent
  AND coalesce(r.normalized_relation, '') = $normalized_relation
  AND r.is_latest = true
  AND r.slot_id IS NULL
WITH r
ORDER BY r.created_at DESC
LIMIT 1
SET r.slot_id = $slot_id,
    r.witness_source_ids = $witness_source_ids,
    r.witness_target_ids = $witness_target_ids,
    r.witness_add_tags = $witness_add_tags,
    r.witnesses_a = $witnesses_a,
    r.witnesses_b = $witnesses_b,
    r.caused_by_event_id = $caused_by_event_id,
    r.run_id = $run_id
"""

UPDATE_SLOT_EDGE_CYPHER = """
MATCH (h:Node {id: $head_id})-[r:Relation]->(t:Node {id: $tail_id})
WHERE r.slot_id = $slot_id
SET r.witness_source_ids = $witness_source_ids,
    r.witness_target_ids = $witness_target_ids,
    r.witness_add_tags = $witness_add_tags,
    r.witnesses_a = $witnesses_a,
    r.witnesses_b = $witnesses_b,
    r.is_latest = $is_latest,
    r.caused_by_event_id = $caused_by_event_id,
    r.run_id = $run_id
"""

FIND_CONFLICTING_LATEST_CYPHER = """
MATCH (h:Node {id: $head_id})-[r:Relation]->(t:Node)
WHERE r.kernel_parent = $kernel_parent
  AND r.is_latest = true
  AND t.id <> $tail_id
RETURN t.id AS tail_id,
       r.relation AS relation,
       r.kernel_parent AS kernel_parent
LIMIT 1
"""

READ_NODE_SCALAR_CYPHER = """
MATCH (n:Node {id: $node_id})
RETURN n[$property_name] AS current_value,
       n.revisions AS revisions
"""

APPEND_NODE_REVISION_CYPHER = """
MATCH (n:Node {id: $node_id})
SET n.revisions = CASE
    WHEN n.revisions IS NULL THEN [$revision]
    ELSE n.revisions + [$revision]
  END,
    n[$property_name] = $new_value
"""


@dataclass(frozen=True)
class Slot:
    """Identity of one attribute-or-relation slot on a head node."""

    head_id: str
    kernel_parent: str
    normalized_relation: str
    tail_id: str | None = None


@dataclass(frozen=True)
class ValidatedSlot:
    """A proposal accepted against the closed R1–R6 / A1–A7 kernel.

    ``kernel_parent`` is the enum member (not a free string). Macrotask 5 can
    pass ``slot`` to ``assert_slot`` / ``retract_slot`` without re-interpreting it.
    """

    verb: str
    fonte_id: str
    kernel_parent: AttributeKernelType | RelationKernelType
    slot: Slot
    tail_id: str | None = None


def _kernel_value(kernel_parent: AttributeKernelType | RelationKernelType | str) -> str:
    if isinstance(kernel_parent, Enum):
        return str(kernel_parent.value)
    return str(kernel_parent)


def is_attribute_kernel(kernel_parent: AttributeKernelType | RelationKernelType | str) -> bool:
    return _kernel_value(kernel_parent) in _ATTRIBUTE_VALUES


def is_relation_kernel(kernel_parent: AttributeKernelType | RelationKernelType | str) -> bool:
    return _kernel_value(kernel_parent) in _RELATION_VALUES


def slot_discriminant(
    kernel_parent: AttributeKernelType | RelationKernelType | str,
    normalized_relation: str,
    tail_id: str | None = None,
) -> str | tuple[str, str]:
    """Attribute slots ignore tail; relation slots are a specific triple."""
    rel = normalized_relation or ""
    if is_attribute_kernel(kernel_parent):
        return rel
    if not tail_id:
        raise ValueError("relation slot discriminant requires tail_id")
    return (rel, tail_id)


def slot_id(
    head_id: str,
    kernel_parent: AttributeKernelType | RelationKernelType | str,
    discriminant: str | tuple[str, ...],
) -> str:
    """Deterministic sha256 of ``(head_id, kernel_parent, discriminant)``."""
    kernel = _kernel_value(kernel_parent)
    if isinstance(discriminant, tuple):
        disc = [str(part) for part in discriminant]
    else:
        disc = [str(discriminant)]
    payload = "slot:" + json.dumps(
        {"head": str(head_id), "kernel": kernel, "disc": disc},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def slot_id_for(slot: Slot, tail_id: str | None = None) -> str:
    disc = slot_discriminant(
        slot.kernel_parent,
        slot.normalized_relation,
        tail_id if tail_id is not None else slot.tail_id,
    )
    return slot_id(slot.head_id, slot.kernel_parent, disc)


def _proposal_get(proposal: Any, *names: str) -> Any:
    if isinstance(proposal, Mapping):
        for name in names:
            if name in proposal:
                return proposal[name]
        return _MISSING
    for name in names:
        if hasattr(proposal, name):
            return getattr(proposal, name)
    return _MISSING


def _nonempty_str(value: Any) -> str | None:
    if type(value) is not str:
        return None
    stripped = value.strip()
    return stripped or None


def _exact_kernel_parent(raw: Any) -> AttributeKernelType | RelationKernelType | None:
    """Exact closed-enum member only. No strip, no case fold, no nearby match."""
    if isinstance(raw, AttributeKernelType):
        return raw
    if isinstance(raw, RelationKernelType):
        return raw
    if isinstance(raw, Enum):
        return None
    if type(raw) is not str:
        return None
    return _KERNEL_PARENT_BY_VALUE.get(raw)


def validate_slot_proposal(proposal: Any) -> ValidatedSlot | None:
    """Return a ``ValidatedSlot`` or ``None``. Never raises. Never coerces kernel.

    ``kernel_parent`` must be an exact ``RelationKernelType`` or
    ``AttributeKernelType`` member (enum or value string). Invented names,
    nearby spellings, Famiglia B, ``EntityKernelType``, backbone relations, and
    empty values are ``None``. Missing write fields (head, fonte, verb, or
    relation tail) are also ``None`` — waiting/incomplete, not a write.
    """
    try:
        if isinstance(proposal, ValidatedSlot):
            return proposal
        if proposal is None:
            return None

        kernel = _exact_kernel_parent(_proposal_get(proposal, "kernel_parent"))
        if kernel is None:
            return None

        head_id = _nonempty_str(_proposal_get(proposal, "head_id", "head"))
        if head_id is None:
            return None

        fonte_id = _nonempty_str(_proposal_get(proposal, "fonte_id", "fonte"))
        if fonte_id is None:
            return None

        verb_raw = _proposal_get(proposal, "verb", "verbo")
        if type(verb_raw) is not str:
            return None
        verb = verb_raw.strip()
        if verb not in _VALID_VERBS:
            return None

        rel_raw = _proposal_get(proposal, "normalized_relation", "relation")
        if rel_raw is _MISSING or rel_raw is None:
            normalized = kernel.value
        elif type(rel_raw) is not str:
            return None
        else:
            normalized = rel_raw.strip() or kernel.value

        tail_raw = _proposal_get(proposal, "tail_id", "tail")
        if tail_raw is _MISSING or tail_raw is None:
            tail_id = None
        else:
            tail_id = _nonempty_str(tail_raw)
            if tail_id is None:
                return None

        is_relation = kernel.value in _RELATION_VALUES
        if is_relation and tail_id is None:
            return None
        if verb == "assert" and tail_id is None:
            return None

        slot = Slot(
            head_id=head_id,
            kernel_parent=kernel.value,
            normalized_relation=normalized,
            tail_id=tail_id,
        )
        return ValidatedSlot(
            verb=verb,
            fonte_id=fonte_id,
            kernel_parent=kernel,
            slot=slot,
            tail_id=tail_id,
        )
    except Exception:
        return None


async def apply_validated_slot(
    session: AsyncSession,
    validated: ValidatedSlot | None,
    *,
    caused_by_event_id: str,
    run_id: str,
    relation: str | None = None,
    head_name: str = "",
    tail_name: str = "",
    witness_source: str = "",
    witness_target: str = "",
    provenance: dict | str | None = None,
) -> bool:
    """Apply one ``ValidatedSlot``. ``None`` / invalid input is a no-op (no write).

    Raw proposals are validated first; only an accepted slot reaches
    ``assert_slot`` / ``retract_slot``. Macrotask 5 should call this helper
    rather than the writers directly.
    """
    if not isinstance(validated, ValidatedSlot):
        if validated is None:
            return False
        validated = validate_slot_proposal(validated)
        if validated is None:
            return False
    if validated.verb == "assert":
        tail_id = validated.tail_id
        if not tail_id:
            return False
        await assert_slot(
            session,
            slot=validated.slot,
            tail_id_or_value=tail_id,
            fonte_id=validated.fonte_id,
            caused_by_event_id=caused_by_event_id,
            run_id=run_id,
            relation=relation,
            head_name=head_name,
            tail_name=tail_name,
            witness_source=witness_source,
            witness_target=witness_target,
            provenance=provenance,
        )
        return True
    await retract_slot(
        session,
        slot=validated.slot,
        fonte_id=validated.fonte_id,
        caused_by_event_id=caused_by_event_id,
        run_id=run_id,
    )
    return True


def _require_nonempty(name: str, value: str | None, *, context: str) -> str:
    if value is None or not str(value).strip():
        raise ValueError(f"{name} is required for {context}")
    return str(value).strip()


def _require_fonte_id(fonte_id: str | None) -> str:
    return _require_nonempty("fonte_id", fonte_id, context="slot assert/retract")


def _require_provenance(
    caused_by_event_id: str | None,
    run_id: str | None,
    *,
    context: str,
) -> tuple[str, str]:
    event = _require_nonempty("caused_by_event_id", caused_by_event_id, context=context)
    rid = _require_nonempty("run_id", run_id, context=context)
    return event, rid


def _unique_ids(values: list[Any] | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in values or []:
        item = str(raw).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _orset_add(
    source_ids: list[Any] | None,
    tags: list[Any] | None,
    fonte_id: str,
) -> tuple[list[str], list[str], bool]:
    ids = _unique_ids(source_ids)
    tag_list = [str(tag) for tag in (tags or []) if str(tag).strip()]
    if fonte_id in ids:
        return ids, tag_list, True
    tag_list.append(f"{fonte_id}:{uuid.uuid4()}")
    ids.append(fonte_id)
    return ids, tag_list, False


def _orset_remove(
    source_ids: list[Any] | None,
    tags: list[Any] | None,
    fonte_id: str,
) -> tuple[list[str], list[str], bool]:
    ids = _unique_ids(source_ids)
    tag_list = [str(tag) for tag in (tags or []) if str(tag).strip()]
    if fonte_id not in ids:
        return ids, tag_list, False
    prefix = f"{fonte_id}:"
    kept_tags = [tag for tag in tag_list if not tag.startswith(prefix)]
    kept_ids = [item for item in ids if item != fonte_id]
    return kept_ids, kept_tags, True


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return bool(value)


async def _fetch_all(result: Any) -> list[Any]:
    rows: list[Any] = []
    async for record in result:
        rows.append(record)
    return rows


async def _find_slot_rows(session: AsyncSession, *, head_id: str, sid: str) -> list[Any]:
    result = await session.run(FIND_SLOT_RELATIONS_CYPHER, head_id=head_id, slot_id=sid)
    return await _fetch_all(result)


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, TypeError):
        return default


async def _update_slot_edge(
    session: AsyncSession,
    *,
    head_id: str,
    tail_id: str,
    sid: str,
    witness_source_ids: list[str],
    witness_target_ids: list[str],
    witness_add_tags: list[str],
    witnesses_a: list[str],
    witnesses_b: list[str],
    is_latest: bool,
    caused_by_event_id: str,
    run_id: str,
) -> None:
    await session.run(
        UPDATE_SLOT_EDGE_CYPHER,
        head_id=head_id,
        tail_id=tail_id,
        slot_id=sid,
        witness_source_ids=_unique_ids(witness_source_ids),
        witness_target_ids=_unique_ids(witness_target_ids),
        witness_add_tags=list(witness_add_tags),
        witnesses_a=_unique_ids(witnesses_a),
        witnesses_b=_unique_ids(witnesses_b),
        is_latest=is_latest,
        caused_by_event_id=caused_by_event_id,
        run_id=run_id,
    )


async def _stamp_new_edge(
    session: AsyncSession,
    *,
    slot: Slot,
    tail_id: str,
    sid: str,
    fonte_id: str,
    relation: str,
    head_name: str,
    tail_name: str,
    witness_source: str,
    witness_target: str,
    provenance: dict | str | None,
    caused_by_event_id: str,
    run_id: str,
) -> None:
    kernel = _kernel_value(slot.kernel_parent)
    ui_source = (witness_source or fonte_id).strip()
    ui_target = (witness_target or "").strip()
    await write_node_relation(
        session,
        head_id=slot.head_id,
        tail_id=tail_id,
        relation=relation,
        normalized_relation=slot.normalized_relation or None,
        head_name=head_name,
        tail_name=tail_name,
        kernel_parent=kernel,
        witness_source=ui_source,
        witness_target=ui_target,
        provenance=provenance,
    )
    ids, tags, _already = _orset_add([], [], fonte_id)
    await session.run(
        STAMP_SLOT_ON_LATEST_CYPHER,
        head_id=slot.head_id,
        tail_id=tail_id,
        kernel_parent=kernel,
        normalized_relation=slot.normalized_relation or "",
        slot_id=sid,
        witness_source_ids=ids,
        witness_target_ids=[],
        witness_add_tags=tags,
        witnesses_a=_unique_ids([ui_source]),
        witnesses_b=_unique_ids([ui_target] if ui_target else []),
        caused_by_event_id=caused_by_event_id,
        run_id=run_id,
    )


async def assert_slot(
    session: AsyncSession,
    *,
    slot: Slot,
    tail_id_or_value: str,
    fonte_id: str,
    caused_by_event_id: str,
    run_id: str,
    relation: str | None = None,
    head_name: str = "",
    tail_name: str = "",
    witness_source: str = "",
    witness_target: str = "",
    provenance: dict | str | None = None,
) -> None:
    """Assert ``tail_id_or_value`` on ``slot`` with provenance ``fonte_id``.

    Empty ``fonte_id`` / ``caused_by_event_id`` / ``run_id`` is a programming
    error. Duplicate ``(slot, fonte_id)`` is idempotent (one id in the
    supporting set). A retracted slot is brought back to supported when the
    same fonte re-asserts (resurrection).
    """
    fonte = _require_fonte_id(fonte_id)
    event_id, rid = _require_provenance(
        caused_by_event_id, run_id, context="slot assert/retract"
    )
    tail_id = str(tail_id_or_value).strip()
    if not tail_id:
        raise ValueError("tail_id_or_value is required for slot assert")
    sid = slot_id_for(slot, tail_id)
    relation_name = (
        relation or slot.normalized_relation or _kernel_value(slot.kernel_parent)
    ).strip()
    ui_source = (witness_source or fonte).strip()
    ui_target = (witness_target or "").strip()

    rows = await _find_slot_rows(session, head_id=slot.head_id, sid=sid)
    same_tail = [row for row in rows if str(_row_get(row, "tail_id") or "") == tail_id]
    if same_tail:
        latest = [row for row in same_tail if _as_bool(_row_get(row, "is_latest"))]
        row = latest[0] if latest else same_tail[0]
        ids, tags, already = _orset_add(
            _row_get(row, "witness_source_ids") or [],
            _row_get(row, "witness_add_tags") or [],
            fonte,
        )
        if already and _as_bool(_row_get(row, "is_latest")):
            return
        witnesses_b = list(_row_get(row, "witnesses_b") or [])
        if ui_target:
            witnesses_b.append(ui_target)
        await _update_slot_edge(
            session,
            head_id=slot.head_id,
            tail_id=tail_id,
            sid=sid,
            witness_source_ids=ids,
            witness_target_ids=_unique_ids(_row_get(row, "witness_target_ids") or []),
            witness_add_tags=tags,
            witnesses_a=_unique_ids([*(_row_get(row, "witnesses_a") or []), ui_source]),
            witnesses_b=_unique_ids(witnesses_b),
            is_latest=True,
            caused_by_event_id=event_id,
            run_id=rid,
        )
    else:
        await _stamp_new_edge(
            session,
            slot=slot,
            tail_id=tail_id,
            sid=sid,
            fonte_id=fonte,
            relation=relation_name,
            head_name=head_name,
            tail_name=tail_name,
            witness_source=ui_source,
            witness_target=ui_target,
            provenance=provenance,
            caused_by_event_id=event_id,
            run_id=rid,
        )

    if is_attribute_kernel(slot.kernel_parent):
        await reconcile_scoped_attribute_slots(
            session,
            head_ids=[slot.head_id],
            slot_id=sid,
        )


async def retract_slot(
    session: AsyncSession,
    *,
    slot: Slot,
    fonte_id: str,
    caused_by_event_id: str,
    run_id: str,
) -> None:
    """Remove ``fonte_id`` from the slot's supporting set.

    Unknown ``(slot, fonte_id)`` is a no-op. Empty supporting set sets
    ``is_latest=false`` and leaves the edge in place. A true no-op does not
    write the edge; every edge that is updated still gets provenance.
    """
    fonte = _require_fonte_id(fonte_id)
    event_id, rid = _require_provenance(
        caused_by_event_id, run_id, context="slot assert/retract"
    )
    sid = slot_id_for(slot)
    rows = await _find_slot_rows(session, head_id=slot.head_id, sid=sid)
    for row in rows:
        tail_id = str(_row_get(row, "tail_id") or "")
        if not tail_id:
            continue
        ids, tags, was_present = _orset_remove(
            _row_get(row, "witness_source_ids") or [],
            _row_get(row, "witness_add_tags") or [],
            fonte,
        )
        if not was_present:
            continue
        empty = not ids
        still_latest = False if empty else _as_bool(_row_get(row, "is_latest"), True)
        await _update_slot_edge(
            session,
            head_id=slot.head_id,
            tail_id=tail_id,
            sid=sid,
            witness_source_ids=ids,
            witness_target_ids=_unique_ids(_row_get(row, "witness_target_ids") or []),
            witness_add_tags=tags,
            witnesses_a=_unique_ids(_row_get(row, "witnesses_a") or []),
            witnesses_b=_unique_ids(_row_get(row, "witnesses_b") or []),
            is_latest=still_latest,
            caused_by_event_id=event_id,
            run_id=rid,
        )
        if empty and is_relation_kernel(slot.kernel_parent):
            conflict = await session.run(
                FIND_CONFLICTING_LATEST_CYPHER,
                head_id=slot.head_id,
                kernel_parent=_kernel_value(slot.kernel_parent),
                tail_id=tail_id,
            )
            record = await conflict.single()
            if record is not None:
                other_tail = str(_row_get(record, "tail_id") or "")
                if other_tail:
                    await write_contradicts(
                        session,
                        left_id=tail_id,
                        right_id=other_tail,
                        subject_id=slot.head_id,
                        relation=str(
                            _row_get(row, "relation")
                            or slot.normalized_relation
                            or _kernel_value(slot.kernel_parent)
                        ),
                        kernel_parent=_kernel_value(slot.kernel_parent),
                    )


async def append_node_revision(
    session: AsyncSession,
    node_id: str,
    *,
    property: str,
    new_value: Any,
    event_id: str,
    run_id: str,
) -> None:
    """Read the current scalar, append a revision, then SET the new value.

    ``revisions[-1].old_value`` is the value the property had before this
    write. Previous entries are never dropped. Identity properties are
    refused. Callers (tests now; later assert/summary) pass ``new_value``;
    the pre-write scalar is always captured from the node.
    """
    nid = str(node_id or "").strip()
    if not nid:
        raise ValueError("node_id is required for append_node_revision")
    prop = _require_nonempty("property", property, context="append_node_revision")
    if prop in _IDENTITY_PROPERTIES:
        raise ValueError(f"cannot revise identity property {prop!r}")
    event = _require_nonempty("event_id", event_id, context="append_node_revision")
    rid = _require_nonempty("run_id", run_id, context="append_node_revision")

    result = await session.run(
        READ_NODE_SCALAR_CYPHER,
        node_id=nid,
        property_name=prop,
    )
    rows = await _fetch_all(result)
    if not rows:
        raise ValueError(f"node_id not found: {nid}")
    old_value = _row_get(rows[0], "current_value")
    revision = {
        "property": prop,
        "old_value": old_value,
        "event_id": event,
        "run_id": rid,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    await session.run(
        APPEND_NODE_REVISION_CYPHER,
        node_id=nid,
        property_name=prop,
        new_value=new_value,
        revision=revision,
    )
