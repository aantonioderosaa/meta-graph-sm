"""Per-event judge triage: LLM proposes slots, primitives write (Macrotasks 5–6).

``run_event_triage`` is the body of ``judge._task_event_triage``. Slot mutations
go only through ``validate_slot_proposal`` / ``apply_validated_slot``. Audit
nodes use parameterized module-constant Cypher (same pattern as
``MERGE_AGENT_SEARCH_RUN_CYPHER``). Timeout or LLM error on one event logs and
continues; this function never raises to ``run_judge``.

Waiting events reuse ``PENDING_HYPOTHESIS_LISTEN_WINDOW`` as the hard cap on
``checks_without_progress`` (no parallel threshold). Zero slots past that
window become ``incomplete``; a later pass that applies ≥1 slot becomes
``confirmed`` on that same pass.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import asdict, is_dataclass
from typing import Any, Literal

from neo4j import AsyncSession
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.llm_client import call_structured
from app.models.kernel import AttributeKernelType, RelationKernelType
from app.pipeline.context_retrieval import get_metadata, get_relations
from app.pipeline.event_slots import apply_validated_slot, validate_slot_proposal

logger = logging.getLogger(__name__)

# Hard cap on LLM-proposed slots per event (same spirit as
# CONNECTIVITY_MAX_GENERALIZATION_HOPS). Module constant, not a Settings flag.
EVENT_TRIAGE_MAX_SLOT_FANOUT = 8

# UI fallback when the LLM returns zero slots and empty reasoning.
_DEFAULT_MISSING_CONTEXT = "no resolvable slots"

FIND_BATCH_EVENTS_CYPHER = """
MATCH (e:Node)
WHERE (e.kernel_category = 'Evento' OR e.type = 'event')
  AND (
    size($touched_ids) = 0
    OR e.id IN $touched_ids
  )
OPTIONAL MATCH (run:EventTriageRun {id: e.id})
WITH e, run
WHERE run IS NULL OR NOT run.verdict IN ['confirmed', 'incomplete']
RETURN e.id AS event_id,
       e.name AS name,
       e.summary AS summary,
       e.kernel_category AS kernel_category,
       e.type AS type
"""

FIND_WAITING_EVENTS_CYPHER = """
MATCH (p:PendingEventContext)
OPTIONAL MATCH (run:EventTriageRun {id: p.event_id})
WITH p, run
WHERE run IS NULL OR NOT run.verdict IN ['confirmed', 'incomplete']
OPTIONAL MATCH (e:Node {id: p.event_id})
RETURN p.event_id AS event_id,
       e.name AS name,
       e.summary AS summary,
       e.kernel_category AS kernel_category,
       e.type AS type
"""

MERGE_EVENT_TRIAGE_RUN_CYPHER = """
MERGE (r:EventTriageRun {id: $event_id})
SET r.event_id = $event_id,
    r.verdict = $verdict,
    r.run_id = $run_id,
    r.timestamp = datetime()
"""

MERGE_PENDING_EVENT_CONTEXT_CYPHER = """
MERGE (p:PendingEventContext {event_id: $event_id})
SET p.event_id = $event_id,
    p.missing_context = $missing_context,
    p.first_seen_run_id = coalesce(p.first_seen_run_id, $run_id),
    p.last_checked_run_id = $run_id,
    p.checks_without_progress = coalesce(p.checks_without_progress, 0) + 1
RETURN p.checks_without_progress AS checks_without_progress
"""

_KERNEL_VALUES = tuple(
    sorted({m.value for m in AttributeKernelType} | {m.value for m in RelationKernelType})
)

SYSTEM_PROMPT = (
    "Sei il giudice degli eventi del grafo. Per l'evento dato proponi zero o più "
    "slot da assertire o ritrattare. kernel_parent deve essere un membro esatto "
    f"del vocabolario chiuso: {', '.join(_KERNEL_VALUES)}. "
    "Non inventare kernel_parent vicini. Non emettere Cypher. "
    "Se non riesci a enumerare slot concreti, restituisci la lista vuota. "
    "reasoning breve e obbligatorio."
)


class EventSlotItem(BaseModel):
    """One proposed assert/retract. Validated before any graph write."""

    head: str = Field(description="Id del nodo testa dello slot")
    kernel_parent: str = Field(
        description="Membro esatto di RelationKernelType o AttributeKernelType"
    )
    tail: str | None = Field(
        default=None, description="Id del nodo coda, se risolto"
    )
    verbo: Literal["assert", "retract"] = Field(description="assert oppure retract")
    fonte: str = Field(description="Id della fonte / testimone")


class EventSlotProposal(BaseModel):
    """Structured LLM output for one event. List length is hard-capped."""

    slots: list[EventSlotItem] = Field(
        default_factory=list,
        max_length=EVENT_TRIAGE_MAX_SLOT_FANOUT,
        description="Slot concreti toccati dall'evento, massimo fan-out",
    )
    reasoning: str = Field(default="", description="Breve ragionamento")


def _brief(value: Any) -> str:
    if value is None:
        return "null"
    if hasattr(value, "model_dump"):
        payload: Any = value.model_dump()
    elif is_dataclass(value) and not isinstance(value, type):
        payload = asdict(value)
    elif isinstance(value, list):
        payload = [
            (
                item.model_dump()
                if hasattr(item, "model_dump")
                else asdict(item)
                if is_dataclass(item) and not isinstance(item, type)
                else item
            )
            for item in value
        ]
    else:
        payload = value
    text = json.dumps(payload, ensure_ascii=False, default=str)
    return text[:4000]


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, TypeError):
        return default


async def _fetch_all(result: Any) -> list[Any]:
    rows: list[Any] = []
    async for record in result:
        rows.append(record)
    return rows


async def _load_readonly_context(session: AsyncSession, event_id: str) -> str:
    relations: Any = []
    metadata: Any = None
    try:
        relations = await get_relations(session, event_id)
    except Exception:
        logger.debug("event_triage get_relations failed event_id=%s", event_id, exc_info=True)
    try:
        metadata = await get_metadata(session, event_id)
    except Exception:
        logger.debug("event_triage get_metadata failed event_id=%s", event_id, exc_info=True)
    return (
        f"metadata={_brief(metadata)}\n"
        f"relations={_brief(relations)}"
    )


def _user_prompt(event: dict[str, Any], context_block: str) -> str:
    return (
        f"event_id={event.get('event_id')}\n"
        f"name={event.get('name') or ''}\n"
        f"summary={event.get('summary') or ''}\n"
        f"kernel_category={event.get('kernel_category') or ''}\n"
        f"type={event.get('type') or ''}\n"
        f"Contesto in sola lettura:\n{context_block}\n"
        "Proponi gli slot (lista vuota se nessuno è risolvibile)."
    )


async def _load_events(
    session: AsyncSession,
    touched_ids: Sequence[str] | None,
) -> list[dict[str, Any]]:
    ids = [str(nid) for nid in (touched_ids or []) if str(nid)]
    waiting_result = await session.run(FIND_WAITING_EVENTS_CYPHER)
    batch_result = await session.run(FIND_BATCH_EVENTS_CYPHER, touched_ids=ids)
    waiting_rows = await _fetch_all(waiting_result)
    batch_rows = await _fetch_all(batch_result)
    seen: set[str] = set()
    events: list[dict[str, Any]] = []
    for row in [*waiting_rows, *batch_rows]:
        event_id = str(_row_get(row, "event_id") or "").strip()
        if not event_id or event_id in seen:
            continue
        seen.add(event_id)
        events.append(
            {
                "event_id": event_id,
                "name": _row_get(row, "name") or "",
                "summary": _row_get(row, "summary") or "",
                "kernel_category": _row_get(row, "kernel_category") or "",
                "type": _row_get(row, "type") or "",
            }
        )
    return events


def _missing_context_text(proposal: EventSlotProposal | None) -> str:
    if proposal is None:
        return _DEFAULT_MISSING_CONTEXT
    text = (proposal.reasoning or "").strip()
    return text or _DEFAULT_MISSING_CONTEXT


async def _record_verdict(
    session: AsyncSession,
    *,
    event_id: str,
    applied: int,
    run_id: str,
    missing_context: str,
) -> str:
    """Write audit + pending. ``incomplete`` iff empty checks hit the listen window."""
    if applied >= 1:
        verdict = "confirmed"
    else:
        result = await session.run(
            MERGE_PENDING_EVENT_CONTEXT_CYPHER,
            event_id=event_id,
            run_id=run_id,
            missing_context=missing_context or _DEFAULT_MISSING_CONTEXT,
        )
        rows = await _fetch_all(result)
        checks = 0
        if rows:
            raw = _row_get(rows[0], "checks_without_progress", 0)
            try:
                checks = int(raw or 0)
            except (TypeError, ValueError):
                checks = 0
        # Same comparison as pending_hypothesis.listen_count >= window.
        window = int(settings.PENDING_HYPOTHESIS_LISTEN_WINDOW)
        verdict = "incomplete" if checks >= window else "waiting"
    await session.run(
        MERGE_EVENT_TRIAGE_RUN_CYPHER,
        event_id=event_id,
        verdict=verdict,
        run_id=run_id,
    )
    return verdict


async def _apply_proposal(
    session: AsyncSession,
    proposal: EventSlotProposal,
    *,
    event_id: str,
    run_id: str,
) -> int:
    applied = 0
    for item in proposal.slots[:EVENT_TRIAGE_MAX_SLOT_FANOUT]:
        validated = validate_slot_proposal(item)
        if validated is None:
            continue
        wrote = await apply_validated_slot(
            session,
            validated,
            caused_by_event_id=event_id,
            run_id=run_id,
        )
        if wrote:
            applied += 1
    return applied


async def _triage_one_event(
    session: AsyncSession,
    event: dict[str, Any],
    run_id: str,
) -> tuple[int, str]:
    event_id = str(event["event_id"])
    context_block = await _load_readonly_context(session, event_id)
    proposal = await call_structured(
        SYSTEM_PROMPT,
        _user_prompt(event, context_block),
        EventSlotProposal,
        temperature=0,
        job_id=run_id,
    )
    if not isinstance(proposal, EventSlotProposal):
        proposal = EventSlotProposal()
    applied = await _apply_proposal(
        session, proposal, event_id=event_id, run_id=run_id
    )
    return applied, _missing_context_text(proposal)


async def run_event_triage(
    session: AsyncSession,
    run_id: str,
    *,
    touched_ids: Sequence[str] | None = None,
) -> int:
    """Triage waiting events first, then the batch. Never raises to the caller."""
    triaged = 0
    try:
        events = await _load_events(session, touched_ids)
        for event in events:
            event_id = str(event.get("event_id") or "")
            applied = 0
            missing_context = _DEFAULT_MISSING_CONTEXT
            try:
                applied, missing_context = await _triage_one_event(
                    session, event, run_id
                )
                triaged += 1
            except Exception:
                logger.exception(
                    "event_triage_event_failed event_id=%s run_id=%s",
                    event_id,
                    run_id,
                )
                applied = 0
                missing_context = _DEFAULT_MISSING_CONTEXT
            try:
                if event_id:
                    await _record_verdict(
                        session,
                        event_id=event_id,
                        applied=applied,
                        run_id=run_id,
                        missing_context=missing_context,
                    )
            except Exception:
                logger.exception(
                    "event_triage_audit_failed event_id=%s run_id=%s",
                    event_id,
                    run_id,
                )
    except Exception:
        logger.exception("event_triage_failed run_id=%s", run_id)
    return triaged
