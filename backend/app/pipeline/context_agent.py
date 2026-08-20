"""ReAct verification loop for promoted :PendingHypothesis (Fase 22).

Python loop around ``call_structured`` (no native OpenAI tool-calling).
The agent proposes candidate ids + evidence_span; writes go only through
existing primitives. Never CREATE/MERGE/DELETE ``:Node`` / ``:Relation``
here. Timeout / LLM error / exhausted turns → silent fallback, hypothesis
stays ``open``, ``:AgentSearchRun`` is still written. Never raises out of
the pipeline.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any

from neo4j import AsyncSession

from app.core.config import settings
from app.core.llm_client import call_structured
from app.models.agent_step import AgentStep
from app.pipeline.chunking import Chunk
from app.pipeline.context_retrieval import (
    facts_from_source,
    get_domain_dictionary,
    get_metadata,
    get_relations,
    search_fulltext,
    search_vector,
)
from app.pipeline.pending_hypothesis import (
    drain_promoted_queue,
    read_hypothesis,
    resolve_hypothesis,
    update_evidence_gap,
)

logger = logging.getLogger(__name__)

MERGE_AGENT_SEARCH_RUN_CYPHER = """
MERGE (r:AgentSearchRun {id: $id})
SET r.hypothesis_id = $hypothesis_id,
    r.steps = $steps,
    r.verdict = $verdict,
    r.turns_used = $turns_used,
    r.timestamp = datetime()
"""

_EXPLICIT_QUESTION = "quali cani? in quale stanza? rispetto a quale fonte?"

SYSTEM_PROMPT = (
    "Sei l'agente di verifica di contesto del grafo. Ricevi un'ipotesi "
    "promossa e decidi se il grafo è incompleto, in conflitto, o da aggiornare. "
    "A ogni turno scegli UN'azione: search_fulltext, search_vector, get_metadata, "
    "get_relations, get_domain_dictionary, facts_from_source, oppure conclude. "
    "Non estrarre triple nuove. Non inventare testimoni. "
    "conclude solo se hai foglie identificate e testimoni non vuoti, oppure se "
    "l'ipotesi è un quantificatore/ritrattazione e lo scope è chiudibile. "
    "Se manca evidenza (quali membri? in quale stanza? rispetto a quale fonte?) "
    "conclude senza testimoni: l'ipotesi resta aperta. "
    "reasoning breve e obbligatorio."
)


@dataclass(frozen=True)
class AgentSearchOutcome:
    hypothesis_id: str
    verdict: str
    turns_used: int
    status: str


def _to_observation(value: Any) -> str:
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


def _explicit_question(hyp: dict[str, Any]) -> str:
    marker = str(hyp.get("marker_category") or "").casefold()
    existing = str(hyp.get("evidence_gap") or "").strip()
    if marker == "quantifier":
        target = str(hyp.get("claim_target") or "membri").strip() or "membri"
        question = f"quali {target}? in quale stanza? rispetto a quale fonte?"
    elif marker == "retraction":
        question = "rispetto a quale fonte? quale affermazione?"
    else:
        question = _EXPLICIT_QUESTION
    if existing and question not in existing:
        return f"{existing}; {question}"
    return question or existing


def _user_prompt(hyp: dict[str, Any], observations: list[str], remaining: int) -> str:
    spans = hyp.get("evidence_span") or []
    if isinstance(spans, (list, tuple)):
        span_text = " | ".join(str(s) for s in spans)
    else:
        span_text = str(spans)
    obs_block = "\n".join(
        f"[obs {i + 1}] {item}" for i, item in enumerate(observations)
    ) or "(nessuna osservazione ancora)"
    return (
        f"Ipotesi id={hyp.get('id')}\n"
        f"claim_target={hyp.get('claim_target')}\n"
        f"confidence={hyp.get('confidence')} status={hyp.get('status')}\n"
        f"marker_category={hyp.get('marker_category')} kind={hyp.get('kind')}\n"
        f"origin_doc_id={hyp.get('origin_doc_id')}\n"
        f"evidence_span={span_text}\n"
        f"witness_fragments={hyp.get('witness_fragments')}\n"
        f"evidence_gap={hyp.get('evidence_gap')}\n"
        f"turni_rimasti={remaining}\n"
        f"Osservazioni:\n{obs_block}\n"
        "Scegli la prossima azione."
    )


def _has_leaf_witnesses(step: AgentStep) -> bool:
    return bool(step.witness_source.strip() and step.witness_target.strip())


def _endpoint_ids(step: AgentStep) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for raw in [step.node_id, *step.candidate_ids]:
        nid = str(raw or "").strip()
        if not nid or nid in seen:
            continue
        seen.add(nid)
        ids.append(nid)
    return ids


def _chunk_from_hyp(hyp: dict[str, Any], step: AgentStep) -> Chunk:
    spans = hyp.get("evidence_span") or []
    if isinstance(spans, (list, tuple)) and spans:
        default_span = str(spans[0])
    else:
        default_span = str(spans or "")
    text = (step.evidence_span or default_span or "").strip()
    doc_id = (step.doc_id or str(hyp.get("origin_doc_id") or "")).strip()
    return Chunk(id=f"agent-{hyp.get('id', '')}", doc_id=doc_id, text=text)


async def _dispatch(
    session: AsyncSession,
    step: AgentStep,
    hyp: dict[str, Any],
) -> str:
    action = step.action
    if action == "search_fulltext":
        hits = await search_fulltext(session, step.query or "")
        return _to_observation(hits)
    if action == "search_vector":
        hits = await search_vector(session, step.query or "")
        return _to_observation(hits)
    if action == "get_metadata":
        node_id = step.node_id.strip()
        if not node_id:
            return "missing node_id"
        meta = await get_metadata(session, node_id)
        return _to_observation(meta)
    if action == "get_relations":
        node_id = step.node_id.strip()
        if not node_id:
            return "missing node_id"
        rels = await get_relations(session, node_id)
        return _to_observation(rels)
    if action == "get_domain_dictionary":
        concept_id = step.concept_id.strip()
        if not concept_id:
            return "missing concept_id"
        dictionary = await get_domain_dictionary(session, concept_id)
        return _to_observation(dictionary)
    if action == "facts_from_source":
        doc_id = (step.doc_id or str(hyp.get("origin_doc_id") or "")).strip()
        if not doc_id:
            return "missing doc_id"
        facts = await facts_from_source(session, doc_id)
        return _to_observation(facts)
    return f"unknown action {action}"


async def _apply_conclude(
    session: AsyncSession,
    hyp: dict[str, Any],
    step: AgentStep,
) -> str:
    """Write only via existing primitives. No S0 without leaf witnesses."""
    marker = str(hyp.get("marker_category") or "").casefold()
    chunk = _chunk_from_hyp(hyp, step)

    if marker == "quantifier":
        from app.pipeline.quantifier_events import resolve_quantifier_scope

        result = await resolve_quantifier_scope(
            session, chunk, concept_hint=str(hyp.get("claim_target") or "") or None
        )
        if result.closed:
            return "confirmed"
        return "insufficient_evidence"

    if marker == "retraction":
        from app.pipeline.retraction import resolve_retraction_scope

        result = await resolve_retraction_scope(session, chunk)
        if result.identifiable:
            await resolve_hypothesis(session, str(hyp["id"]), "confirmed")
            return "confirmed"
        return "insufficient_evidence"

    if not _has_leaf_witnesses(step):
        return "insufficient_evidence"
    endpoints = _endpoint_ids(step)
    if len(endpoints) < 2:
        return "insufficient_evidence"
    relation = (step.relation or "").strip() or "related"
    kernel_parent = (step.kernel_parent or "").strip()
    if not kernel_parent:
        return "insufficient_evidence"

    from app.pipeline.ingestion import write_node_relation

    await write_node_relation(
        session,
        head_id=endpoints[0],
        tail_id=endpoints[1],
        relation=relation,
        normalized_relation=relation,
        witness_source=step.witness_source,
        witness_target=step.witness_target,
        kernel_parent=kernel_parent,
        provenance=str(hyp.get("origin_doc_id") or step.doc_id or ""),
    )
    await resolve_hypothesis(session, str(hyp["id"]), "confirmed")
    return "confirmed"


async def _log_agent_run(
    session: AsyncSession,
    *,
    hypothesis_id: str,
    job_id: str,
    steps: list[dict[str, str]],
    verdict: str,
    turns_used: int,
) -> None:
    run_id = f"agentrun:{job_id}:{hypothesis_id}" if job_id else f"agentrun:{hypothesis_id}"
    await session.run(
        MERGE_AGENT_SEARCH_RUN_CYPHER,
        id=run_id,
        hypothesis_id=hypothesis_id,
        steps=json.dumps(steps, ensure_ascii=False),
        verdict=verdict,
        turns_used=turns_used,
    )


async def _keep_open_with_question(
    session: AsyncSession, hyp: dict[str, Any]
) -> None:
    gap = _explicit_question(hyp)
    await update_evidence_gap(session, str(hyp["id"]), gap)


async def run_context_agent(
    session: AsyncSession,
    hypothesis_id: str,
    *,
    job_id: str = "",
) -> AgentSearchOutcome:
    """One ReAct loop for a promoted hypothesis. Never raises to the caller."""
    steps_audit: list[dict[str, str]] = []
    turns_used = 0
    verdict = "error"
    status = "open"
    hyp: dict[str, Any] | None = None
    try:
        if not settings.ENABLE_CONTEXT_LAYER:
            return AgentSearchOutcome(
                hypothesis_id=hypothesis_id,
                verdict="disabled",
                turns_used=0,
                status="open",
            )
        hyp = await read_hypothesis(session, hypothesis_id)
        if hyp is None:
            verdict = "missing"
            await _log_agent_run(
                session,
                hypothesis_id=hypothesis_id,
                job_id=job_id,
                steps=steps_audit,
                verdict=verdict,
                turns_used=0,
            )
            return AgentSearchOutcome(
                hypothesis_id=hypothesis_id,
                verdict=verdict,
                turns_used=0,
                status="open",
            )
        status = str(hyp.get("status") or "open")
        if status in {"confirmed", "dismissed"}:
            verdict = "already_resolved"
            await _log_agent_run(
                session,
                hypothesis_id=hypothesis_id,
                job_id=job_id,
                steps=steps_audit,
                verdict=verdict,
                turns_used=0,
            )
            return AgentSearchOutcome(
                hypothesis_id=hypothesis_id,
                verdict=verdict,
                turns_used=0,
                status=status,
            )

        max_turns = max(1, int(settings.CONTEXT_AGENT_MAX_TURNS))
        observations: list[str] = []
        concluded = False
        for turn in range(max_turns):
            turns_used = turn + 1
            remaining = max_turns - turn
            step = await call_structured(
                SYSTEM_PROMPT,
                _user_prompt(hyp, observations, remaining),
                AgentStep,
                job_id=job_id or None,
            )
            steps_audit.append(
                {"action": str(step.action), "reasoning": step.reasoning}
            )
            if step.action == "conclude":
                verdict = await _apply_conclude(session, hyp, step)
                concluded = True
                break
            try:
                observations.append(await _dispatch(session, step, hyp))
            except Exception as exc:
                logger.debug("context_agent tool failed", exc_info=True)
                observations.append(f"tool_error: {exc}")

        if not concluded:
            verdict = "turns_exhausted"

        if verdict in {"insufficient_evidence", "turns_exhausted", "error"}:
            await _keep_open_with_question(session, hyp)
            status = "open"
        elif verdict == "confirmed":
            status = "confirmed"
        else:
            refreshed = await read_hypothesis(session, hypothesis_id)
            status = str((refreshed or hyp).get("status") or "open")
    except Exception:
        logger.exception("context_agent_fallback hypothesis_id=%s", hypothesis_id)
        verdict = "error"
        status = "open"
        if hyp is not None:
            try:
                await _keep_open_with_question(session, hyp)
            except Exception:
                logger.debug("context_agent gap update skipped", exc_info=True)

    try:
        await _log_agent_run(
            session,
            hypothesis_id=hypothesis_id,
            job_id=job_id,
            steps=steps_audit,
            verdict=verdict,
            turns_used=turns_used,
        )
    except Exception:
        logger.exception("context_agent_audit_failed hypothesis_id=%s", hypothesis_id)

    return AgentSearchOutcome(
        hypothesis_id=hypothesis_id,
        verdict=verdict,
        turns_used=turns_used,
        status=status,
    )


async def run_context_agent_for_job(
    session: AsyncSession,
    job_id: str,
) -> list[AgentSearchOutcome]:
    """Drain this job's promotion queue and run the agent once per id.

    Flag off → zero ``call_structured``, no audit runs, queue left untouched.
    Empty queue → zero agent calls.
    """
    if not settings.ENABLE_CONTEXT_LAYER:
        return []
    hypothesis_ids = drain_promoted_queue(job_id)
    outcomes: list[AgentSearchOutcome] = []
    for hid in hypothesis_ids:
        try:
            outcomes.append(await run_context_agent(session, hid, job_id=job_id))
        except Exception:
            logger.exception("context_agent_job_item_failed hypothesis_id=%s", hid)
            try:
                await _log_agent_run(
                    session,
                    hypothesis_id=hid,
                    job_id=job_id,
                    steps=[],
                    verdict="error",
                    turns_used=0,
                )
            except Exception:
                logger.debug("context_agent audit on job item failed", exc_info=True)
            outcomes.append(
                AgentSearchOutcome(
                    hypothesis_id=hid, verdict="error", turns_used=0, status="open"
                )
            )
    return outcomes
