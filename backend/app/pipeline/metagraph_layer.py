"""Read-only Metagraph layer views for Fase 12 UI panels (S1, judge, incompleteness)."""

from __future__ import annotations

from typing import Any

from neo4j import AsyncSession

from app.api.schemas import (
    ConnectivityRuleItem,
    ConnectivityRuleListResponse,
    ContradictionItem,
    ContradictionListResponse,
    EventIncompletenessItem,
    EventIncompletenessListResponse,
    JudgeRunItem,
    JudgeRunListResponse,
)

LIST_CONTRADICTIONS_CYPHER = """
MATCH (a:Node)-[c:CONTRADICTS]->(b:Node)
RETURN elementId(c) AS id,
       a.id AS left_id,
       coalesce(a.name, a.id) AS left_name,
       b.id AS right_id,
       coalesce(b.name, b.id) AS right_name,
       c.subject_id AS subject_id
"""

LIST_CONNECTIVITY_RULES_CYPHER = """
MATCH (r:ConnectivityRule)
RETURN r.source_category AS source_category,
       r.relation_type AS relation_type,
       r.target_category AS target_category,
       coalesce(r.generalization_level, 0) AS generalization_level,
       size(coalesce(r.origin_fact_ids, [])) AS origin_count
ORDER BY r.generalization_level, r.source_category, r.relation_type
"""

LIST_JUDGE_RUNS_CYPHER = """
MATCH (j:JudgeRun)
RETURN j.id AS id,
       j.batch_id AS batch_id,
       toString(j.timestamp) AS timestamp,
       coalesce(j.anti_blur, 0) AS anti_blur,
       coalesce(j.equivalent_to, 0) AS equivalent_to,
       coalesce(j.reraffine, 0) AS reraffine,
       coalesce(j.temporal, 0) AS temporal
ORDER BY j.timestamp DESC
"""

LIST_EVENT_INCOMPLETENESS_CYPHER = """
MATCH (r:EventTriageRun)
WHERE r.verdict = 'incomplete'
OPTIONAL MATCH (p:PendingEventContext)
WHERE p.event_id = coalesce(r.event_id, r.id)
OPTIONAL MATCH (e:Node)
WHERE e.id = coalesce(r.event_id, r.id)
RETURN coalesce(r.event_id, r.id) AS event_id,
       coalesce(e.name, e.summary, p.missing_context, r.event_id, r.id) AS text,
       p.missing_context AS missing_context,
       p.first_seen_run_id AS first_seen_run_id,
       coalesce(p.checks_without_progress, 0) AS checks_without_progress,
       toString(r.timestamp) AS incomplete_at,
       r.verdict AS verdict
ORDER BY r.timestamp DESC
"""


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    getter = getattr(obj, "get", None)
    if callable(getter):
        return getter(key, default)
    try:
        return obj[key]
    except (KeyError, TypeError, IndexError):
        return default


def _as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _as_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


async def _collect_rows(result: Any) -> list[Any]:
    rows: list[Any] = []
    if result is None:
        return rows
    aiter = getattr(result, "__aiter__", None)
    if aiter is not None:
        async for record in result:
            rows.append(record)
        return rows
    records = getattr(result, "records", None)
    if records is not None:
        return list(records)
    return rows


async def list_contradictions(session: AsyncSession) -> ContradictionListResponse:
    """Open CONTRADICTS — never filtered. Node–Node and relation-tail pairs."""
    result = await session.run(LIST_CONTRADICTIONS_CYPHER)
    items: list[ContradictionItem] = []
    async for row in result:
        left_id = _as_str(_get(row, "left_id"))
        right_id = _as_str(_get(row, "right_id"))
        raw_id = _get(row, "id")
        subject = _get(row, "subject_id")
        items.append(
            ContradictionItem(
                id=_as_str(raw_id, f"{left_id}->{right_id}"),
                left_id=left_id,
                left_name=_as_str(_get(row, "left_name"), left_id),
                right_id=right_id,
                right_name=_as_str(_get(row, "right_name"), right_id),
                subject_id=None if subject in (None, "") else str(subject),
            )
        )
    return ContradictionListResponse(items=items)


async def list_connectivity_rules(session: AsyncSession) -> ConnectivityRuleListResponse:
    result = await session.run(LIST_CONNECTIVITY_RULES_CYPHER)
    items: list[ConnectivityRuleItem] = []
    async for row in result:
        items.append(
            ConnectivityRuleItem(
                source_category=_as_str(_get(row, "source_category")),
                relation_type=_as_str(_get(row, "relation_type")),
                target_category=_as_str(_get(row, "target_category")),
                generalization_level=_as_int(_get(row, "generalization_level")),
                origin_count=_as_int(_get(row, "origin_count")),
            )
        )
    return ConnectivityRuleListResponse(items=items)


async def list_judge_runs(session: AsyncSession) -> JudgeRunListResponse:
    result = await session.run(LIST_JUDGE_RUNS_CYPHER)
    items: list[JudgeRunItem] = []
    async for row in result:
        batch = _get(row, "batch_id")
        ts = _get(row, "timestamp")
        items.append(
            JudgeRunItem(
                id=_as_str(_get(row, "id")),
                batch_id=None if batch in (None, "") else str(batch),
                timestamp=None if ts in (None, "") else str(ts),
                anti_blur=_as_int(_get(row, "anti_blur")),
                equivalent_to=_as_int(_get(row, "equivalent_to")),
                reraffine=_as_int(_get(row, "reraffine")),
                temporal=_as_int(_get(row, "temporal")),
            )
        )
    return JudgeRunListResponse(items=items)


async def list_event_incompleteness(
    session: AsyncSession,
) -> EventIncompletenessListResponse:
    """Incomplete EventTriageRun rows only. Read-only: MATCH / OPTIONAL MATCH."""
    result = await session.run(LIST_EVENT_INCOMPLETENESS_CYPHER)
    rows = await _collect_rows(result)
    items: list[EventIncompletenessItem] = []
    for row in rows:
        verdict = _as_str(_get(row, "verdict"), "incomplete")
        if verdict != "incomplete":
            continue
        event_id = _as_str(_get(row, "event_id"))
        ts = _get(row, "incomplete_at")
        ts_s = None if ts in (None, "") else str(ts)
        missing = _get(row, "missing_context")
        first_seen = _get(row, "first_seen_run_id")
        text = _as_str(_get(row, "text"), event_id)
        items.append(
            EventIncompletenessItem(
                event_id=event_id,
                text=text,
                missing_context=None if missing in (None, "") else str(missing),
                first_seen_run_id=None if first_seen in (None, "") else str(first_seen),
                checks_without_progress=_as_int(_get(row, "checks_without_progress")),
                incomplete_at=ts_s,
                timestamp=ts_s,
            )
        )
    return EventIncompletenessListResponse(items=items)
