"""Post-batch judge: epistemic hygiene after dreaming (Fase 10).

Runs once at the end of each dreaming batch (after ``reconcile``). Writes only
through existing primitives: INGEST-style ``:Relation`` witness splits (no new
S2 facts), ``PROMOTE``/``MEMBER_OF`` moves, Famiglia B
(``EQUIVALENT_TO``, ``SAME_AS``, ``NOT_SAME_AS``, ``CONTRADICTS``,
``SUPERSEDES``, ``UPDATED_BY``), and ``link_as_facet`` / ``mark_not_same_as``.
No new kernel types.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal

from neo4j import AsyncSession
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.llm_client import call_structured
from app.models.kernel import IS_A, MEMBER_OF, EntityKernelType
from app.models.relations import RelationLabel
from app.pipeline.concepts import compute_hash_id, genre_concept_id
from app.pipeline.entity_relation_resolution import map_temporal_transition
from app.pipeline.generic_instances import (
    SET_GENERIC_OBSERVATION_COUNT_CYPHER,
    ensure_generic_instance,
    redirect_to_generic,
)
from app.pipeline.identity_resolution import (
    cosine,
    ensure_identity_node,
    identity_uri_from_facet_ids,
    identity_uri_from_name,
    link_as_facet,
    mark_not_same_as,
)
from app.pipeline.ingestion import write_contradicts
from app.pipeline.node_resolution import prefer_recent_summary

logger = logging.getLogger(__name__)

STAGE = "judge"
_ISA_REL = IS_A.upper()
_MEMBER_OF_REL = MEMBER_OF.upper()

RequeuePair = Callable[[str, str], Awaitable[None]]
TemporalDecision = Literal["supersedes", "updated_by", "leave"]


@dataclass
class JudgeStats:
    anti_blur: int = 0
    equivalent_to: int = 0
    reraffine: int = 0
    identity: int = 0
    missed_contradictions: int = 0
    temporal: int = 0
    generic_instances: int = 0


class IdentityVerdict(BaseModel):
    """LLM verdict for a residual POSSIBLY_SAME_AS pair (F10.5)."""

    decision: Literal["same_as", "not_same_as", "defer"] = Field(
        description="same_as, not_same_as, or defer if evidence is insufficient"
    )


class SpecificityVerdict(BaseModel):
    """LLM verdict for a catch-all member with no matching promoted child (F23.4)."""

    decision: Literal["specifico", "generico"] = Field(
        description="specifico = leave as an individual; generico = background instance"
    )


FIND_BLURRED_RELATIONS_CYPHER = """
MATCH ()-[r:Relation]->()
WHERE size(coalesce(r.witnesses_a, [])) > 1
  AND size(coalesce(r.witnesses_b, [])) > 1
RETURN coalesce(r.id, elementId(r)) AS rel_id,
       r.witnesses_a AS witnesses_a,
       r.witnesses_b AS witnesses_b
"""

MARK_BLURRED_RELATION_CYPHER = """
MATCH ()-[r:Relation]->()
WHERE coalesce(r.id, elementId(r)) = $rel_id
SET r.needs_reverify = true,
    r.witnesses_a = [],
    r.witnesses_b = []
"""

FIND_EQUIVALENT_CONCEPT_PAIRS_CYPHER = """
MATCH (a:Concept {promoted: true}), (b:Concept {promoted: true})
WHERE a.id < b.id
  AND a.kernel_category = b.kernel_category
  AND a.parent_uri = b.parent_uri
  AND a.parent_uri IS NOT NULL
  AND NOT (a)-[:EQUIVALENT_TO]-(b)
RETURN a.id AS id_a, a.embedding AS embedding_a,
       b.id AS id_b, b.embedding AS embedding_b
"""

MERGE_EQUIVALENT_TO_CYPHER = """
MATCH (a:Concept {id: $src_id}), (b:Concept {id: $dst_id})
MERGE (a)-[:EQUIVALENT_TO]->(b)
"""

MOVE_ABSORBED_MEMBER_OF_CYPHER = f"""
MATCH (n:Node)-[old:{_MEMBER_OF_REL}]->(absorbed:Concept {{id: $absorbed_id}})
DELETE old
WITH n, absorbed
MATCH (survivor:Concept {{id: $survivor_id}})
CREATE (n)-[:{_MEMBER_OF_REL} {{absorbed_from: $absorbed_id}}]->(survivor)
SET absorbed.absorbed_from = $survivor_id
"""

MARK_ABSORBED_CONCEPT_CYPHER = """
MATCH (absorbed:Concept {id: $absorbed_id})
SET absorbed.absorbed_from = $survivor_id
"""

FIND_PROMOTED_CHILDREN_CYPHER = f"""
MATCH (child:Concept {{promoted: true}})-[:{_ISA_REL}]->(parent:Concept {{id: $parent_id}})
RETURN child.id AS child_id, child.name AS name,
       child.definition AS definition, child.summary AS summary,
       child.kernel_category AS kernel_category
"""

FIND_PARENT_MEMBERS_CYPHER = f"""
MATCH (n:Node)-[:{_MEMBER_OF_REL}]->(parent:Concept {{id: $parent_id}})
RETURN n.id AS id, n.name AS name, n.summary AS summary,
       n.kernel_category AS kernel_category,
       n.is_generic AS is_generic,
       n.merged_into AS merged_into,
       n.generic_observation_count AS generic_observation_count
"""

MOVE_MEMBER_OF_TO_CHILD_CYPHER = f"""
MATCH (n:Node {{id: $node_id}})-[old:{_MEMBER_OF_REL}]->(parent:Concept {{id: $parent_id}})
DELETE old
WITH n
MATCH (child:Concept {{id: $child_id}})
CREATE (n)-[:{_MEMBER_OF_REL}]->(child)
"""

FIND_POSSIBLY_SAME_AS_CYPHER = """
MATCH (a:Node)-[r:POSSIBLY_SAME_AS]->(b:Node)
RETURN a.id AS id_a, a.name AS name_a, a.summary AS summary_a,
       a.kernel_category AS kernel_a, a.created_at AS created_a,
       b.id AS id_b, b.name AS name_b, b.summary AS summary_b,
       b.kernel_category AS kernel_b, b.created_at AS created_b
"""

DELETE_POSSIBLY_SAME_AS_CYPHER = """
MATCH (a:Node {id: $src_id})-[r:POSSIBLY_SAME_AS]-(b:Node {id: $dst_id})
DELETE r
"""

FIND_MISSED_CONTRADICTIONS_CYPHER = """
MATCH (h:Node)-[r1:Relation]->(t1:Node)
MATCH (h)-[r2:Relation]->(t2:Node)
WHERE r1.is_latest = true AND r2.is_latest = true
  AND t1.id < t2.id
  AND coalesce(r1.kernel_parent, '') = coalesce(r2.kernel_parent, '')
  AND NOT (t1)-[:CONTRADICTS]-(t2)
  AND NOT (t1)-[:SUPERSEDES]-(t2)
  AND NOT (t1)-[:UPDATED_BY]-(t2)
  AND (
    size($touched_ids) = 0
    OR h.id IN $touched_ids
    OR t1.id IN $touched_ids
    OR t2.id IN $touched_ids
  )
RETURN h.id AS head_id, t1.id AS tail_a, t2.id AS tail_b,
       coalesce(r1.relation, '') AS relation,
       coalesce(r1.kernel_parent, '') AS kernel_parent
"""

FIND_CONTRADICTS_PAIRS_CYPHER = """
MATCH (a:Node)-[c:CONTRADICTS]->(b:Node)
OPTIONAL MATCH (h1:Node)-[r1:Relation]->(a)
WHERE r1.is_latest = true
OPTIONAL MATCH (h2:Node)-[r2:Relation]->(b)
WHERE r2.is_latest = true
RETURN a.id AS left_id, b.id AS right_id,
       coalesce(c.subject_id, h1.id, h2.id, '') AS subject_id,
       coalesce(r1.relation, a.summary, a.name, c.relation, '') AS text_a,
       coalesce(r2.relation, b.summary, b.name, '') AS text_b
"""

CREATE_SUPERSEDES_BETWEEN_CYPHER = """
MATCH (a:Node {id: $left_id}), (b:Node {id: $right_id})
CREATE (a)-[:SUPERSEDES {
  subject_id: $subject_id,
  created_at: datetime()
}]->(b)
"""

CREATE_UPDATED_BY_BETWEEN_CYPHER = """
MATCH (a:Node {id: $left_id}), (b:Node {id: $right_id})
CREATE (a)-[:UPDATED_BY {
  subject_id: $subject_id,
  created_at: datetime()
}]->(b)
"""

DELETE_CONTRADICTS_BETWEEN_CYPHER = """
MATCH (a:Node {id: $left_id})-[r:CONTRADICTS]-(b:Node {id: $right_id})
DELETE r
"""

MERGE_JUDGE_RUN_CYPHER = """
MERGE (j:JudgeRun {id: $id})
SET j.batch_id = $batch_id,
    j.timestamp = datetime(),
    j.anti_blur = $anti_blur,
    j.equivalent_to = $equivalent_to,
    j.reraffine = $reraffine,
    j.identity = $identity,
    j.missed_contradictions = $missed_contradictions,
    j.temporal = $temporal,
    j.generic_instances = $generic_instances
"""

_IDENTITY_SYSTEM_PROMPT = (
    "Confronta due faccette collegate da POSSIBLY_SAME_AS e decidi se sono la "
    "stessa identità (same_as), omonimi distinti (not_same_as), o se l'evidenza "
    "non basta (defer). Non fondere i nodi: same_as significa solo confermare "
    "le faccette. Rispondi solo secondo lo schema."
)

_SPECIFICITY_SYSTEM_PROMPT = (
    "Classifica un nodo membro di un catch-all che non corrisponde a nessun "
    "genere figlio già promosso. Decidi specifico se è un individuo plausibile "
    "(personaggio nominato, luogo proprio, oggetto distinto) che merita un'identità "
    "propria in futuro. Decidi generico se è un elemento di sfondo senza identità "
    "individuale (una strada, un passante, un oggetto innominato). Una sola "
    "decisione; non inventare un genere. Rispondi solo secondo lo schema."
)


async def requeue_pair(witness_a: str, witness_b: str) -> None:
    """Default no-op requeue. Tests replace this. Does not persist S2 facts."""
    _ = (witness_a, witness_b)
    return None


def split_blurred_relation(rel: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Cartesian pairs of multi-witness lists. Does not invent nodes."""
    witnesses_a = [str(w) for w in (rel.get("witnesses_a") or []) if str(w).strip()]
    witnesses_b = [str(w) for w in (rel.get("witnesses_b") or []) if str(w).strip()]
    if len(witnesses_a) <= 1 or len(witnesses_b) <= 1:
        return []
    return [(wa, wb) for wa in witnesses_a for wb in witnesses_b]


def _embedding_list(raw: object) -> list[float] | None:
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        values = [float(x) for x in raw]
        return values or None
    return None


def _instance_matches_child(node: Mapping[str, Any], child: Mapping[str, Any]) -> bool:
    """Name / summary / genre-hash match between a parent member and child S."""
    name = str(node.get("name") or "").strip()
    child_name = str(child.get("name") or "").strip()
    if name and child_name and name.casefold() == child_name.casefold():
        return True
    summary = str(node.get("summary") or "").strip()
    child_def = str(child.get("definition") or child.get("summary") or "").strip()
    if summary and child_def and summary.casefold() == child_def.casefold():
        return True
    category_raw = str(node.get("kernel_category") or child.get("kernel_category") or "")
    try:
        category = EntityKernelType(category_raw) if category_raw else None
    except ValueError:
        category = None
    child_id = str(child.get("child_id") or child.get("id") or "")
    if category and name and child_id and genre_concept_id(category, name) == child_id:
        return True
    sig_node = compute_hash_id(
        f"{name.casefold()}|{summary.casefold()}|{category_raw}"
    )
    sig_child = compute_hash_id(
        f"{child_name.casefold()}|{child_def.casefold()}|{category_raw}"
    )
    return bool(name or summary) and sig_node == sig_child


def classify_temporal_pair(text_a: str, text_b: str) -> TemporalDecision:
    """Map CONTRADICTS pair to supersedes / updated_by / leave (F10.7)."""
    label = map_temporal_transition(text_a or "", text_b or "")
    if label == RelationLabel.updated_by:
        return "updated_by"
    if label == RelationLabel.supersedes:
        return "supersedes"
    return "leave"


async def _log_judge_run(session: AsyncSession, job_id: str, stats: JudgeStats) -> None:
    await session.run(
        MERGE_JUDGE_RUN_CYPHER,
        id=job_id,
        batch_id=job_id,
        anti_blur=stats.anti_blur,
        equivalent_to=stats.equivalent_to,
        reraffine=stats.reraffine,
        identity=stats.identity,
        missed_contradictions=stats.missed_contradictions,
        temporal=stats.temporal,
        generic_instances=stats.generic_instances,
    )


async def _task_anti_blur(
    session: AsyncSession,
    *,
    requeue: RequeuePair,
) -> int:
    result = await session.run(FIND_BLURRED_RELATIONS_CYPHER)
    blurred: list[dict[str, Any]] = [dict(record) async for record in result]
    count = 0
    for rel in blurred:
        pairs = split_blurred_relation(rel)
        if not pairs:
            continue
        await session.run(MARK_BLURRED_RELATION_CYPHER, rel_id=rel["rel_id"])
        for witness_a, witness_b in pairs:
            await requeue(witness_a, witness_b)
        count += 1
    return count


async def _task_equivalent_to(session: AsyncSession, threshold: float) -> int:
    result = await session.run(FIND_EQUIVALENT_CONCEPT_PAIRS_CYPHER)
    pairs: list[dict[str, Any]] = [dict(record) async for record in result]
    count = 0
    for row in pairs:
        emb_a = _embedding_list(row.get("embedding_a"))
        emb_b = _embedding_list(row.get("embedding_b"))
        if emb_a is None or emb_b is None:
            continue
        if cosine(emb_a, emb_b) < threshold:
            continue
        src_id = str(row["id_a"])
        dst_id = str(row["id_b"])
        await session.run(MERGE_EQUIVALENT_TO_CYPHER, src_id=src_id, dst_id=dst_id)
        await session.run(
            MOVE_ABSORBED_MEMBER_OF_CYPHER,
            absorbed_id=dst_id,
            survivor_id=src_id,
        )
        await session.run(
            MARK_ABSORBED_CONCEPT_CYPHER,
            absorbed_id=dst_id,
            survivor_id=src_id,
        )
        count += 1
    return count


async def _task_reraffine(
    session: AsyncSession, promoted_parent_ids: Sequence[str]
) -> int:
    if not promoted_parent_ids:
        return 0
    count = 0
    seen_parents = list(dict.fromkeys(promoted_parent_ids))
    for parent_id in seen_parents:
        children_result = await session.run(
            FIND_PROMOTED_CHILDREN_CYPHER, parent_id=parent_id
        )
        children = [dict(record) async for record in children_result]
        children.sort(key=lambda row: str(row.get("child_id") or ""))
        if not children:
            continue
        members_result = await session.run(FIND_PARENT_MEMBERS_CYPHER, parent_id=parent_id)
        members = [dict(record) async for record in members_result]
        for node in members:
            target = next(
                (child for child in children if _instance_matches_child(node, child)),
                None,
            )
            if target is None:
                continue
            await session.run(
                MOVE_MEMBER_OF_TO_CHILD_CYPHER,
                node_id=node["id"],
                parent_id=parent_id,
                child_id=target["child_id"],
            )
            count += 1
    return count


async def classify_node_specificity(
    job_id: str,
    name: str,
    summary: str,
    kernel_category: str,
    children: Sequence[Mapping[str, Any]],
) -> SpecificityVerdict:
    """One LLM call per candidate: specifico (leave) vs generico (maybe redirect)."""
    child_lines = []
    for child in children:
        child_name = str(child.get("name") or child.get("child_id") or "")
        child_def = str(child.get("definition") or child.get("summary") or "")
        child_lines.append(f'- nome="{child_name}" definition="{child_def}"')
    listed = "\n".join(child_lines) if child_lines else "(nessun figlio promosso)"
    user_prompt = (
        f'NODO: nome="{name}" summary="{summary}" kernel_category="{kernel_category}"\n'
        f"FIGLI PROMOSSI GIÀ ESISTENTI (nessuno corrisponde a questo nodo):\n{listed}\n"
        "Decidi specifico o generico."
    )
    return await call_structured(
        _SPECIFICITY_SYSTEM_PROMPT,
        user_prompt,
        SpecificityVerdict,
        temperature=0,
        job_id=job_id,
    )


def _as_observation_count(raw: object) -> int:
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


async def _task_generic_instances(
    session: AsyncSession,
    promoted_parent_ids: Sequence[str],
    job_id: str,
) -> int:
    """Seventh judge task: catch-all members with no matching child → generic.

    Runs after ``_task_reraffine`` so members already moved to a child are
    not reconsidered. Gate is the dedicated sub-flag: the other six tasks
    still run when ``ENABLE_JUDGE`` is on.
    """
    if not settings.ENABLE_GENERIC_INSTANCES:
        return 0
    if not promoted_parent_ids:
        return 0
    min_obs = int(settings.GENERIC_INSTANCE_MIN_OBSERVATIONS)
    count = 0
    seen_parents = list(dict.fromkeys(promoted_parent_ids))
    for parent_id in seen_parents:
        children_result = await session.run(
            FIND_PROMOTED_CHILDREN_CYPHER, parent_id=parent_id
        )
        children = [dict(record) async for record in children_result]
        children.sort(key=lambda row: str(row.get("child_id") or ""))
        if not children:
            continue
        members_result = await session.run(FIND_PARENT_MEMBERS_CYPHER, parent_id=parent_id)
        members = [dict(record) async for record in members_result]
        for node in members:
            if node.get("is_generic"):
                continue
            if node.get("merged_into"):
                continue
            if any(_instance_matches_child(node, child) for child in children):
                continue
            node_id = str(node["id"])
            category_raw = str(node.get("kernel_category") or "")
            try:
                category = EntityKernelType(category_raw) if category_raw else None
            except ValueError:
                category = None
            if category is None:
                continue
            try:
                verdict = await classify_node_specificity(
                    job_id,
                    str(node.get("name") or ""),
                    str(node.get("summary") or ""),
                    category_raw,
                    children,
                )
            except Exception:
                logger.exception("judge_generic_instances_llm_failed %s", node_id)
                continue
            if verdict.decision == "specifico":
                await session.run(
                    SET_GENERIC_OBSERVATION_COUNT_CYPHER,
                    node_id=node_id,
                    count=0,
                )
                continue
            if verdict.decision != "generico":
                continue
            new_count = _as_observation_count(node.get("generic_observation_count")) + 1
            await session.run(
                SET_GENERIC_OBSERVATION_COUNT_CYPHER,
                node_id=node_id,
                count=new_count,
            )
            if new_count < min_obs:
                continue
            generic_id = await ensure_generic_instance(session, parent_id, category)
            if generic_id == node_id:
                continue
            await redirect_to_generic(session, node_id, generic_id)
            count += 1
    return count


async def _identity_verdict(
    job_id: str,
    name_a: str,
    summary_a: str,
    name_b: str,
    summary_b: str,
) -> IdentityVerdict:
    user_prompt = (
        f'FACCETTA A: nome="{name_a}" summary="{summary_a}"\n'
        f'FACCETTA B: nome="{name_b}" summary="{summary_b}"\n'
        "Decidi same_as, not_same_as, o defer."
    )
    return await call_structured(
        _IDENTITY_SYSTEM_PROMPT,
        user_prompt,
        IdentityVerdict,
        temperature=0,
        job_id=job_id,
    )


async def _task_identity(session: AsyncSession, job_id: str) -> int:
    result = await session.run(FIND_POSSIBLY_SAME_AS_CYPHER)
    pairs: list[dict[str, Any]] = [dict(record) async for record in result]
    count = 0
    for row in pairs:
        id_a = str(row["id_a"])
        id_b = str(row["id_b"])
        try:
            verdict = await _identity_verdict(
                job_id,
                str(row.get("name_a") or ""),
                str(row.get("summary_a") or ""),
                str(row.get("name_b") or ""),
                str(row.get("summary_b") or ""),
            )
        except Exception:
            logger.exception("judge_identity_llm_failed %s %s", id_a, id_b)
            continue
        decision = verdict.decision
        if decision == "defer":
            continue
        if decision == "same_as":
            name_a = str(row.get("name_a") or "")
            kernel_a = str(row.get("kernel_a") or "")
            if name_a and kernel_a:
                uri = identity_uri_from_name(name_a, kernel_a)
            else:
                uri = identity_uri_from_facet_ids([id_a, id_b])
            summary = prefer_recent_summary(
                str(row.get("summary_a") or ""),
                row.get("created_a"),
                str(row.get("summary_b") or ""),
                row.get("created_b"),
            )
            await ensure_identity_node(session, uri=uri, canonical_summary=summary)
            await link_as_facet(session, uri, id_a)
            await link_as_facet(session, uri, id_b)
            await session.run(DELETE_POSSIBLY_SAME_AS_CYPHER, src_id=id_a, dst_id=id_b)
            count += 1
            continue
        if decision == "not_same_as":
            await mark_not_same_as(session, id_a, id_b)
            await session.run(DELETE_POSSIBLY_SAME_AS_CYPHER, src_id=id_a, dst_id=id_b)
            count += 1
    return count


async def _task_missed_contradictions(
    session: AsyncSession,
    touched_ids: Sequence[str] | None = None,
) -> int:
    ids = [str(nid) for nid in (touched_ids or []) if str(nid)]
    result = await session.run(FIND_MISSED_CONTRADICTIONS_CYPHER, touched_ids=ids)
    pairs: list[dict[str, Any]] = [dict(record) async for record in result]
    count = 0
    for row in pairs:
        await write_contradicts(
            session,
            left_id=str(row["tail_a"]),
            right_id=str(row["tail_b"]),
            subject_id=str(row["head_id"]),
            relation=str(row.get("relation") or "contradicts"),
            kernel_parent=str(row.get("kernel_parent") or "contradicts"),
        )
        count += 1
    return count


async def _task_temporal(session: AsyncSession) -> int:
    result = await session.run(FIND_CONTRADICTS_PAIRS_CYPHER)
    pairs: list[dict[str, Any]] = [dict(record) async for record in result]
    count = 0
    seen: set[frozenset[str]] = set()
    for row in pairs:
        left_id = str(row["left_id"])
        right_id = str(row["right_id"])
        key = frozenset({left_id, right_id})
        if key in seen:
            continue
        seen.add(key)
        decision = classify_temporal_pair(
            str(row.get("text_a") or ""),
            str(row.get("text_b") or ""),
        )
        if decision == "leave":
            continue
        subject_id = str(row.get("subject_id") or "")
        cypher = (
            CREATE_UPDATED_BY_BETWEEN_CYPHER
            if decision == "updated_by"
            else CREATE_SUPERSEDES_BETWEEN_CYPHER
        )
        await session.run(cypher, left_id=left_id, right_id=right_id, subject_id=subject_id)
        await session.run(
            DELETE_CONTRADICTS_BETWEEN_CYPHER,
            left_id=left_id,
            right_id=right_id,
        )
        count += 1
    return count


async def run_judge(
    session: AsyncSession,
    job_id: str,
    *,
    promoted_parent_ids: list[str] | None = None,
    on_requeue: RequeuePair | None = None,
    touched_ids: Sequence[str] | None = None,
) -> JudgeStats:
    """Seven post-batch tasks + ``:JudgeRun`` log. Always writes the log node.

    ``touched_ids`` scopes missed-contradiction pairing to the current batch
    when non-empty; omitted or empty keeps the full-scan (debug/manual).
    The seventh task (generic instances) is a no-op unless
    ``ENABLE_GENERIC_INSTANCES`` is on.
    """
    stats = JudgeStats()
    if not settings.ENABLE_JUDGE:
        await _log_judge_run(session, job_id, stats)
        return stats

    requeue = on_requeue if on_requeue is not None else requeue_pair
    parent_ids = list(promoted_parent_ids or [])
    threshold = float(settings.BACKBONE_COLLAPSE_THRESHOLD)

    stats.anti_blur = await _task_anti_blur(session, requeue=requeue)
    stats.equivalent_to = await _task_equivalent_to(session, threshold)
    stats.reraffine = await _task_reraffine(session, parent_ids)
    stats.generic_instances = await _task_generic_instances(
        session, parent_ids, job_id
    )
    stats.identity = await _task_identity(session, job_id)
    stats.missed_contradictions = await _task_missed_contradictions(
        session, touched_ids=touched_ids
    )
    stats.temporal = await _task_temporal(session)
    await _log_judge_run(session, job_id, stats)
    logger.info("judge_complete job_id=%s stats=%s", job_id, asdict(stats))
    return stats
