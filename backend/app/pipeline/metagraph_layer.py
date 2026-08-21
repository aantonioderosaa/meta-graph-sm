"""Read-only Metagraph layer views for Fase 12 UI panels (identities, S1, judge)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from neo4j import AsyncSession

from app.api.schemas import (
    ConnectivityRuleItem,
    ConnectivityRuleListResponse,
    ContradictionItem,
    ContradictionListResponse,
    IdentityFacet,
    IdentityItem,
    IdentityListResponse,
    JudgeRunItem,
    JudgeRunListResponse,
    UnlinkFacetResponse,
)
from app.pipeline.identity_resolution import unlink_facet

LIST_IDENTITIES_CYPHER = """
MATCH (i:IdentityNode)
OPTIONAL MATCH (f:Node)-[:SAME_AS]->(i)
RETURN i.uri AS uri,
       f.id AS facet_id,
       f.name AS facet_name,
       f.kernel_category AS kernel_category
ORDER BY i.uri
"""

GET_IDENTITY_CYPHER = """
MATCH (i:IdentityNode {uri: $uri})
OPTIONAL MATCH (f:Node)-[:SAME_AS]->(i)
RETURN i.uri AS uri,
       f.id AS facet_id,
       f.name AS facet_name,
       f.kernel_category AS kernel_category
"""

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
       coalesce(j.identity, 0) AS identity,
       coalesce(j.missed_contradictions, 0) AS missed_contradictions,
       coalesce(j.temporal, 0) AS temporal
ORDER BY j.timestamp DESC
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


def _facet_from_row(row: Any) -> IdentityFacet | None:
    facet_id = _get(row, "facet_id")
    if facet_id is None:
        return None
    name = _get(row, "facet_name")
    kernel = _get(row, "kernel_category")
    return IdentityFacet(
        id=str(facet_id),
        name=_as_str(name, str(facet_id)),
        kernel_category=None if kernel is None else str(kernel),
    )


def _identities_from_rows(rows: list[Any]) -> list[IdentityItem]:
    grouped: dict[str, list[IdentityFacet]] = defaultdict(list)
    seen_facets: dict[str, set[str]] = defaultdict(set)
    order: list[str] = []
    for row in rows:
        uri = _get(row, "uri")
        if uri is None:
            continue
        uri_s = str(uri)
        if uri_s not in grouped:
            order.append(uri_s)
        facet = _facet_from_row(row)
        if facet is None or facet.id in seen_facets[uri_s]:
            continue
        seen_facets[uri_s].add(facet.id)
        grouped[uri_s].append(facet)
    return [IdentityItem(uri=uri, facets=grouped[uri]) for uri in order]


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


async def list_identities(session: AsyncSession) -> IdentityListResponse:
    result = await session.run(LIST_IDENTITIES_CYPHER)
    rows = await _collect_rows(result)
    return IdentityListResponse(items=_identities_from_rows(rows))


async def get_identity(session: AsyncSession, uri: str) -> IdentityItem:
    result = await session.run(GET_IDENTITY_CYPHER, uri=uri)
    rows = await _collect_rows(result)
    items = _identities_from_rows(rows)
    if items:
        return items[0]
    return IdentityItem(uri=uri, facets=[])


async def unlink_identity_facet(
    session: AsyncSession, uri: str, facet_node_id: str
) -> UnlinkFacetResponse:
    """Detach a facet: DELETE SAME_AS/POSSIBLY_SAME_AS only. Never deletes ``:Node``."""
    await unlink_facet(session, identity_id=uri, facet_node_id=facet_node_id)
    return UnlinkFacetResponse(
        unlinked=True,
        identity_uri=uri,
        facet_node_id=facet_node_id,
    )


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
                identity=_as_int(_get(row, "identity")),
                missed_contradictions=_as_int(_get(row, "missed_contradictions")),
                temporal=_as_int(_get(row, "temporal")),
            )
        )
    return JudgeRunListResponse(items=items)
