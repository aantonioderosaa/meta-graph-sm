"""F25.1: persist and list context-layer counters.

Design choice (smallest additive surface, documented for the runbook):

Gate counters per batch are written on a ``:ContextLayerRun`` log node
(MERGE on ``id = job_id``), the same pattern as ``:JudgeRun``. They are
**not** a sibling ``GET /graph/context-layer/stats`` and not an in-memory
only tally — they have to survive the request so an operator can inspect
a batch without Neo4j Browser.

``GET /graph/context-layer/runs`` returns three lists on one payload:

* ``gate_runs`` — those log nodes (T1/T2/T3/model-fallback, promotions,
  agent_runs, agent_turns_used)
* ``agent_runs`` — ``:AgentSearchRun`` (turns used, verdict)
* ``open_hypotheses`` — open ``:PendingHypothesis``

No extra uniqueness constraint (comment-only in ``schema.cypher``, like
``:JudgeRun`` / ``:AgentSearchRun``). Failures here never raise into the
pipeline.
"""

from __future__ import annotations

import logging
from typing import Any

from neo4j import AsyncSession

from app.pipeline.relevance_gate import GatePassResult

logger = logging.getLogger(__name__)

MERGE_CONTEXT_LAYER_RUN_CYPHER = """
MERGE (r:ContextLayerRun {id: $id})
ON CREATE SET
  r.job_id = $job_id,
  r.timestamp = datetime(),
  r.t1 = 0,
  r.t2 = 0,
  r.t3 = 0,
  r.model_fallback = 0,
  r.promotions = 0,
  r.agent_runs = 0,
  r.agent_turns_used = 0
SET r.job_id = $job_id,
    r.timestamp = datetime(),
    r.t1 = coalesce(r.t1, 0) + $t1,
    r.t2 = coalesce(r.t2, 0) + $t2,
    r.t3 = coalesce(r.t3, 0) + $t3,
    r.model_fallback = coalesce(r.model_fallback, 0) + $model_fallback,
    r.promotions = coalesce(r.promotions, 0) + $promotions,
    r.agent_runs = coalesce(r.agent_runs, 0) + $agent_runs,
    r.agent_turns_used = coalesce(r.agent_turns_used, 0) + $agent_turns_used
"""

LIST_CONTEXT_LAYER_RUNS_CYPHER = """
MATCH (r:ContextLayerRun)
RETURN r.id AS id,
       r.job_id AS job_id,
       toString(r.timestamp) AS timestamp,
       coalesce(r.t1, 0) AS t1,
       coalesce(r.t2, 0) AS t2,
       coalesce(r.t3, 0) AS t3,
       coalesce(r.model_fallback, 0) AS model_fallback,
       coalesce(r.promotions, 0) AS promotions,
       coalesce(r.agent_runs, 0) AS agent_runs,
       coalesce(r.agent_turns_used, 0) AS agent_turns_used
ORDER BY r.timestamp DESC
"""

LIST_AGENT_SEARCH_RUNS_CYPHER = """
MATCH (r:AgentSearchRun)
RETURN r.id AS id,
       r.hypothesis_id AS hypothesis_id,
       r.verdict AS verdict,
       coalesce(r.turns_used, 0) AS turns_used,
       toString(r.timestamp) AS timestamp,
       r.steps AS steps
ORDER BY r.timestamp DESC
"""


def _empty_deltas() -> dict[str, int]:
    return {
        "t1": 0,
        "t2": 0,
        "t3": 0,
        "model_fallback": 0,
        "promotions": 0,
        "agent_runs": 0,
        "agent_turns_used": 0,
    }


async def _bump(session: AsyncSession, job_id: str, **deltas: int) -> None:
    hid = (job_id or "").strip()
    if not hid:
        return
    payload = _empty_deltas()
    payload.update({k: int(v) for k, v in deltas.items() if k in payload})
    if not any(payload.values()):
        return
    try:
        await session.run(
            MERGE_CONTEXT_LAYER_RUN_CYPHER,
            id=hid,
            job_id=hid,
            **payload,
        )
    except Exception:
        logger.debug("context_layer_run_bump_skipped job_id=%s", hid, exc_info=True)


async def record_gate_pass(
    session: AsyncSession,
    job_id: str,
    result: GatePassResult,
) -> None:
    """Increment T1/T2/T3/model-fallback for this job's gate pass."""
    deltas = _empty_deltas()
    if result.deterministic_kind == "t1":
        deltas["t1"] = 1
    elif result.deterministic_kind == "t2":
        deltas["t2"] = 1
    elif result.deterministic_kind == "t3":
        deltas["t3"] = 1
    if result.model_fallback_used:
        deltas["model_fallback"] = 1
    await _bump(session, job_id, **deltas)


async def record_promotion(session: AsyncSession, job_id: str) -> None:
    await _bump(session, job_id, promotions=1)


async def record_agent_run(
    session: AsyncSession,
    job_id: str,
    *,
    turns_used: int = 0,
) -> None:
    await _bump(session, job_id, agent_runs=1, agent_turns_used=int(turns_used or 0))


def row_get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    getter = getattr(obj, "get", None)
    if callable(getter):
        return getter(key, default)
    try:
        return obj[key]
    except (KeyError, TypeError, IndexError):
        return default
