"""Per-event judge triage: three fixed phases, then validated writes.

``run_event_triage`` is the body of ``judge._task_event_triage``. Slot mutations
go only through ``validate_slot_proposal`` / ``apply_validated_slot``. Audit
nodes use parameterized module-constant Cypher (same pattern as
``MERGE_AGENT_SEARCH_RUN_CYPHER``). Timeout or LLM error on one event logs and
continues; this function never raises to ``run_judge``.

The per-event loop is not an open-ended ReAct turn budget — an earlier
version let the model pick freely among search/inspect/propose across up to
``EVENT_TRIAGE_MAX_TURNS`` turns, and in practice it would loop re-issuing the
same read (observed live: alternating ``get_relations`` calls on the same two
nodes for six turns straight) and never reach ``propose``, falling through to
a "turns exhausted" waiting verdict that was never a real judgment. Replaced
with three fixed phases, each exactly one structured decision:

1. Search — the model names what to look up (comprehensive: every query runs
   both fulltext and vector); can return an empty list if turn 0 already
   suffices.
2. Inspect — the model picks which specific observed node ids are worth a
   full relations+metadata read.
3. Decide — terminal, always reached: propose slots, or explicitly confirm
   the graph already represents the event correctly.

Phase 3 cannot be skipped or looped past, so "no verdict reached" is no
longer a possible outcome of this function.

Waiting events reuse ``PENDING_HYPOTHESIS_LISTEN_WINDOW`` as the hard cap on
``checks_without_progress`` (no parallel threshold). Zero slots past that
window become ``incomplete``; a later pass that applies ≥1 slot becomes
``confirmed`` on that same pass.

The judge never creates new ``:Node`` — assert/retract only on nodes that
already exist (plan §7). Writes go only through ``validate_slot_proposal`` /
``apply_validated_slot``. Identity and Famiglia B stay closed.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from dataclasses import asdict, is_dataclass
from typing import Any, Literal

from neo4j import AsyncSession
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.llm_client import call_structured
from app.models.kernel import AttributeKernelType, RelationKernelType
from app.pipeline.context_retrieval import (
    get_metadata,
    get_relations,
    search_fulltext,
    search_vector,
)
from app.pipeline.event_slots import apply_validated_slot, validate_slot_proposal

logger = logging.getLogger(__name__)

# Proper-noun witness extraction for the deterministic pre-fetch below. Moved
# in from the removed relevance_gate.py (Fase 20 agentic context layer): this
# one helper is general-purpose (finds capitalized names in free text) and
# event_triage is its only remaining caller.
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

_PROPER_RE = re.compile(r"\b[A-ZÀÈÉÌÒÙ][a-zàèéìòù]{2,}\b")


def _extract_named_witnesses(text: str) -> tuple[str, ...]:
    """Proper-name mentions in free text. Generic collectives don't count."""
    seen: set[str] = set()
    names: list[str] = []
    for match in _PROPER_RE.findall(text or ""):
        token = match.strip()
        key = token.casefold()
        if len(token) < 3 or key in _GENERIC_TOKENS or key in seen:
            continue
        seen.add(key)
        names.append(token)
    return tuple(names)

# Hard cap on LLM-proposed slots per event (same spirit as
# CONNECTIVITY_MAX_GENERALIZATION_HOPS). Module constant, not a Settings flag.
EVENT_TRIAGE_MAX_SLOT_FANOUT = 8

# Phase 1/2 fan-out caps. Module constants, not Settings flags — same spirit
# as EVENT_TRIAGE_PREFETCH_MAX_CANDIDATES below: bound cost per event, not
# something a deployment should need to retune.
EVENT_TRIAGE_MAX_SEARCH_QUERIES = 5
EVENT_TRIAGE_MAX_INSPECT_NODES = 5

# Concatenated :Chunk.text from DERIVED_FROM, injected into the free turn-0
# observation. A few thousand chars is enough; a long document must not blow
# the prompt. Not a phase call.
EVENT_TRIAGE_SOURCE_TEXT_CAP = 4000

# Deterministic pre-fetch, run before phase 1 for every event. Closes a real
# failure mode observed in production: a phase can simply omit search even
# though the system prompt asks for it — an event naming two already-existing
# entities ("Sole", "Vento") was judged "not representable in the graph"
# because the model never searched, even though a plain fulltext query for
# either name ranked the existing :Node first. Every proper-noun-looking
# mention in the event's own text (name + summary + source chunk) is searched
# here, unconditionally, so discovery does not depend on the model choosing
# to look. Phase 1 keeps search for anything this misses (implicit
# references, disambiguation among near-duplicates).
EVENT_TRIAGE_PREFETCH_MAX_CANDIDATES = 5
EVENT_TRIAGE_PREFETCH_HITS_PER_CANDIDATE = 3

# UI fallback when the LLM returns zero slots and empty reasoning.
_DEFAULT_MISSING_CONTEXT = "no resolvable slots"

# Keys whose values are graph node ids when walking tool results.
_OBSERVED_ID_KEYS = frozenset(
    {
        "id",
        "other_id",
        "from_id",
        "to_id",
        "head_id",
        "tail_id",
        "member_of",
        "node_id",
        "event_id",
        "concept_id",
    }
)

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

# Turn-0 read of the original phrasing. get_metadata / NodeMetadataResponse
# expose name/summary/attributes/identity_uris only — they do not traverse
# DERIVED_FROM to :Chunk.text. Sola lettura: MATCH/RETURN, no writes.
FIND_EVENT_SOURCE_CHUNK_TEXT_CYPHER = """
MATCH (e:Node {id: $id})-[:DERIVED_FROM]->(c:Chunk)
RETURN c.text AS text
"""

_KERNEL_VALUES = tuple(
    sorted({m.value for m in AttributeKernelType} | {m.value for m in RelationKernelType})
)

_CLOSED_VOCAB_LINE = (
    f"kernel_parent deve essere un membro esatto del vocabolario chiuso: "
    f"{', '.join(_KERNEL_VALUES)}. Non inventare kernel_parent vicini."
)

SEARCH_SYSTEM_PROMPT = (
    "Sei il giudice degli eventi del grafo, fase 1 di 3: RICERCA. "
    "Leggi l'evento e il contesto già noto (turno 0). Decidi quali nomi, temi o "
    "frasi chiave cercare nel grafo per capire se l'evento è già ben "
    "rappresentato — una ricerca completa, non un singolo tentativo: elenca "
    "tutto ciò che ti serve, non solo il primo termine che ti viene in mente. "
    "Ogni query viene cercata sia per testo sia per significato. "
    "Se il contesto del turno 0 basta già, restituisci una lista vuota. "
    "reasoning breve e obbligatorio. Non proponi nulla in questa fase."
)

INSPECT_SYSTEM_PROMPT = (
    "Sei il giudice degli eventi del grafo, fase 2 di 3: ISPEZIONE. "
    "Hai il contesto del turno 0 e i risultati della ricerca di fase 1. "
    "Decidi quali nodi specifici, tra quelli osservati finora, meritano di "
    "essere esaminati a fondo (le loro relazioni e metadati) per capire se "
    "l'evento è già ben rappresentato nel grafo. Cita solo id di nodi che "
    "compaiono già nelle osservazioni — mai un id inventato. "
    "Se il contesto raccolto finora basta già, restituisci una lista vuota. "
    "reasoning breve e obbligatorio. Non proponi nulla in questa fase."
)

DECIDE_SYSTEM_PROMPT = (
    "Sei il giudice degli eventi del grafo, fase 3 di 3: DECISIONE — "
    "terminale, non puoi più cercare né ispezionare, decidi con quello che hai. "
    "Distingui sempre «il grafo rappresenta già correttamente questo evento» "
    "(slots vuota e verified_no_change=True) da «propongo una modifica» "
    "(slot concreti). Non collassare i due esiti nella stessa risposta vuota. "
    "Se non sai o manca contesto, slots vuota e verified_no_change=False — "
    "meglio dichiarare di non sapere che indovinare. "
    "Non inventare un id: uno slot il cui head o tail non è stato osservato "
    "nelle fasi precedenti non va proposto. "
    "Il giudice non crea mai nuovi :Node — assert/retract solo su nodi già "
    "esistenti. Se un riferimento non risolve, lascia l'evento in attesa. "
    f"{_CLOSED_VOCAB_LINE} Non emettere Cypher. "
    "verified_no_change=True solo con lista slot vuota. "
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
    """Structured phase-3 (terminal) decision. List length is hard-capped."""

    slots: list[EventSlotItem] = Field(
        default_factory=list,
        max_length=EVENT_TRIAGE_MAX_SLOT_FANOUT,
        description="Slot concreti toccati dall'evento, massimo fan-out",
    )
    verified_no_change: bool = Field(
        default=False,
        description=(
            "True se il grafo rappresenta già correttamente l'evento: nessuno "
            "slot da scrivere, ma l'evento è stato valutato con esito positivo."
        ),
    )
    reasoning: str = Field(default="", description="Breve ragionamento")


class SearchPhaseDecision(BaseModel):
    """Phase 1 (terminal for this phase): what to look up, comprehensively."""

    queries: list[str] = Field(
        default_factory=list,
        max_length=EVENT_TRIAGE_MAX_SEARCH_QUERIES,
        description="Nomi/temi/frasi da cercare; vuota se il turno 0 basta già",
    )
    reasoning: str = Field(min_length=1, description="Breve motivazione della ricerca")


class InspectPhaseDecision(BaseModel):
    """Phase 2 (terminal for this phase): which observed nodes to read in full."""

    node_ids: list[str] = Field(
        default_factory=list,
        max_length=EVENT_TRIAGE_MAX_INSPECT_NODES,
        description="Id di nodi già osservati da ispezionare; vuota se il contesto basta già",
    )
    reasoning: str = Field(min_length=1, description="Breve motivazione dell'ispezione")


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


def _add_observed_id(into: set[str], raw: Any) -> None:
    if raw is None or isinstance(raw, bool):
        return
    text = str(raw).strip()
    if text:
        into.add(text)


def _collect_observed_ids(value: Any, into: set[str]) -> None:
    """Walk tool results and keep only values of known id keys."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return
    if hasattr(value, "model_dump"):
        _collect_observed_ids(value.model_dump(), into)
        return
    if is_dataclass(value) and not isinstance(value, type):
        _collect_observed_ids(asdict(value), into)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _OBSERVED_ID_KEYS:
                if isinstance(item, (list, tuple, set)):
                    for part in item:
                        _add_observed_id(into, part)
                else:
                    _add_observed_id(into, item)
            else:
                _collect_observed_ids(item, into)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            _collect_observed_ids(item, into)


def _filter_unobserved_slots(
    proposal: EventSlotProposal, observed: set[str]
) -> EventSlotProposal:
    """Drop slots whose head/tail was never seen in turn 0 or a phase result.

    Combined with Phase 1 MATCH (in ``event_slots``), an unobserved/
    hallucinated id never becomes ``confirmed``. Dropping any slot clears
    ``verified_no_change`` so a mixed guess cannot confirm as «already
    correct».
    """
    kept: list[EventSlotItem] = []
    dropped = False
    for item in proposal.slots:
        head = (item.head or "").strip()
        tail = (item.tail or "").strip() if item.tail else ""
        if head and head not in observed:
            dropped = True
            continue
        if tail and tail not in observed:
            dropped = True
            continue
        kept.append(item)
    if not dropped:
        return proposal
    return proposal.model_copy(update={"slots": kept, "verified_no_change": False})


async def _load_source_chunk_text(session: AsyncSession, event_id: str) -> str:
    """Read-only original phrasing from ``(event)-[:DERIVED_FROM]->(:Chunk)``.

    Missing edges or empty ``c.text`` omit source text; never raises.
    Concatenation is capped so a long document cannot blow the prompt.
    """
    try:
        result = await session.run(FIND_EVENT_SOURCE_CHUNK_TEXT_CYPHER, id=event_id)
        rows = await _fetch_all(result)
    except Exception:
        logger.debug(
            "event_triage source chunk text failed event_id=%s",
            event_id,
            exc_info=True,
        )
        return ""
    parts: list[str] = []
    for row in rows:
        raw = _row_get(row, "text")
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            parts.append(text)
    if not parts:
        return ""
    return "\n\n".join(parts)[:EVENT_TRIAGE_SOURCE_TEXT_CAP]


async def _prefetch_named_candidates(
    session: AsyncSession,
    *,
    event: dict[str, Any],
    source_text: str,
    already_known_text: str,
    observed: set[str],
) -> list[str]:
    """Search proper-noun mentions in the event's text that turn 0 didn't already cover.

    Runs before phase 1 — entity discovery for the common case (named
    entities in the event's own phrasing) never depends on the model deciding
    to search. ``already_known_text`` is the relations+metadata dump already
    fetched for this event: a candidate name literally present there is
    already linked in the graph, so searching it again would be a wasted call
    for no new information — skip it. A candidate absent from
    ``already_known_text`` is exactly the failure mode this closes (an event
    with no pre-linked participants, e.g. "Sole"/"Vento" never reached via
    ``get_relations`` on the event itself). Bounded: at most
    ``EVENT_TRIAGE_PREFETCH_MAX_CANDIDATES`` names, each fulltext-searched for
    at most ``EVENT_TRIAGE_PREFETCH_HITS_PER_CANDIDATE`` hits — cost scales
    with this one event's text, not with the KB.
    """
    blob = " ".join(
        part
        for part in (event.get("name"), event.get("summary"), source_text)
        if part
    )
    candidates = _extract_named_witnesses(blob)[:EVENT_TRIAGE_PREFETCH_MAX_CANDIDATES]
    known_folded = already_known_text.casefold()
    observations: list[str] = []
    for name in candidates:
        if name.casefold() in known_folded:
            continue
        try:
            hits = await search_fulltext(
                session, name, k=EVENT_TRIAGE_PREFETCH_HITS_PER_CANDIDATE
            )
        except Exception:
            logger.debug(
                "event_triage prefetch search failed name=%s", name, exc_info=True
            )
            continue
        _collect_observed_ids(hits, observed)
        observations.append(f"prefetch_search[{name}]={_brief(hits)}")
    return observations


async def _seed_turn0(
    session: AsyncSession, event: dict[str, Any]
) -> tuple[list[str], set[str]]:
    """Free turn 0: relations + metadata + source text + prefetch. Not an LLM call."""
    event_id = str(event["event_id"])
    observed: set[str] = {event_id}
    observations: list[str] = []
    # Separate from `observations`: only what's already linked in the graph
    # (relations + metadata), never the raw event text itself — the event's
    # own sentence always contains its own entity names, which would make the
    # "already known" check in _prefetch_named_candidates always true and
    # silently skip the exact search it exists to run.
    graph_known: list[str] = []
    try:
        relations = await get_relations(session, event_id)
        _collect_observed_ids(relations, observed)
        text = f"relations={_brief(relations)}"
        observations.append(text)
        graph_known.append(text)
    except Exception as exc:
        logger.debug("event_triage get_relations failed event_id=%s", event_id, exc_info=True)
        observations.append(f"relations=tool_error: {exc}")
    try:
        metadata = await get_metadata(session, event_id)
        _collect_observed_ids(metadata, observed)
        text = f"metadata={_brief(metadata)}"
        observations.append(text)
        graph_known.append(text)
    except Exception as exc:
        logger.debug("event_triage get_metadata failed event_id=%s", event_id, exc_info=True)
        observations.append(f"metadata=tool_error: {exc}")
    source_text = await _load_source_chunk_text(session, event_id)
    if source_text:
        observations.append(f"source_chunk_text={source_text}")
    observations.extend(
        await _prefetch_named_candidates(
            session,
            event=event,
            source_text=source_text,
            already_known_text=" ".join(graph_known),
            observed=observed,
        )
    )
    return observations, observed


def _event_block(event: dict[str, Any]) -> str:
    return (
        f"event_id={event.get('event_id')}\n"
        f"name={event.get('name') or ''}\n"
        f"summary={event.get('summary') or ''}\n"
        f"kernel_category={event.get('kernel_category') or ''}\n"
        f"type={event.get('type') or ''}\n"
    )


def _phase_prompt(
    event: dict[str, Any], observations: list[str], instruction: str
) -> str:
    obs_block = "\n".join(observations) or "(nessuna osservazione ancora)"
    return (
        f"{_event_block(event)}"
        f"Contesto in sola lettura:\n{obs_block}\n"
        f"{instruction}"
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


def _honest_verified_no_change(proposal: EventSlotProposal, applied: int) -> bool:
    """True only for an explicit positive evaluation with an empty slot list.

    ``verified_no_change=True`` plus non-empty slots that all failed to apply
    is not "already correct" — that falls through to waiting.
    """
    if applied >= 1 or proposal.slots:
        return False
    return bool(proposal.verified_no_change)


async def _record_verdict(
    session: AsyncSession,
    *,
    event_id: str,
    applied: int,
    run_id: str,
    missing_context: str,
    verified_no_change: bool = False,
) -> str:
    """Write audit + pending.

    ``confirmed`` if ``applied >= 1``, or if ``applied == 0`` and
    ``verified_no_change`` (explicit positive evaluation; does not MERGE
    ``:PendingEventContext`` or consume a listen-window check). Otherwise
    ``waiting``/``incomplete`` as Macrotask 6.
    """
    if applied >= 1:
        verdict = "confirmed"
    elif verified_no_change:
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


async def _run_search_phase(
    session: AsyncSession,
    event: dict[str, Any],
    observations: list[str],
    observed: set[str],
    run_id: str,
) -> None:
    """Phase 1: one comprehensive search decision, executed in full, appended in place."""
    decision = await call_structured(
        SEARCH_SYSTEM_PROMPT,
        _phase_prompt(
            event, observations, "Quali query cerchiamo? Lista vuota se non serve."
        ),
        SearchPhaseDecision,
        temperature=0,
        job_id=run_id,
    )
    for query in decision.queries[:EVENT_TRIAGE_MAX_SEARCH_QUERIES]:
        query = (query or "").strip()
        if not query:
            continue
        for fn, label in ((search_fulltext, "search_fulltext"), (search_vector, "search_vector")):
            try:
                hits = await fn(session, query)
                _collect_observed_ids(hits, observed)
                observations.append(f"{label}[{query}]={_brief(hits)}")
            except Exception as exc:
                logger.debug("event_triage search phase tool failed", exc_info=True)
                observations.append(f"{label}[{query}]=tool_error: {exc}")


async def _run_inspect_phase(
    session: AsyncSession,
    event: dict[str, Any],
    observations: list[str],
    observed: set[str],
    run_id: str,
) -> None:
    """Phase 2: one inspection decision, restricted to already-observed ids."""
    decision = await call_structured(
        INSPECT_SYSTEM_PROMPT,
        _phase_prompt(
            event, observations, "Quali nodi ispezioniamo? Lista vuota se non serve."
        ),
        InspectPhaseDecision,
        temperature=0,
        job_id=run_id,
    )
    seen: set[str] = set()
    for node_id in decision.node_ids[:EVENT_TRIAGE_MAX_INSPECT_NODES]:
        node_id = (node_id or "").strip()
        if not node_id or node_id in seen:
            continue
        seen.add(node_id)
        if node_id not in observed:
            # Never dispatched on an id the model invented — same discipline
            # as _filter_unobserved_slots, applied one step earlier.
            observations.append(f"get_relations[{node_id}]=skipped: id not observed")
            continue
        for fn, label in ((get_relations, "get_relations"), (get_metadata, "get_metadata")):
            try:
                data = await fn(session, node_id)
                _collect_observed_ids(data, observed)
                observations.append(f"{label}[{node_id}]={_brief(data)}")
            except Exception as exc:
                logger.debug("event_triage inspect phase tool failed", exc_info=True)
                observations.append(f"{label}[{node_id}]=tool_error: {exc}")


async def _run_decide_phase(
    event: dict[str, Any],
    observations: list[str],
    observed: set[str],
    run_id: str,
) -> EventSlotProposal:
    """Phase 3: terminal, always reached — no loop, no "turns exhausted" fallback."""
    proposal = await call_structured(
        DECIDE_SYSTEM_PROMPT,
        _phase_prompt(event, observations, "Decidi: proponi, oppure conferma se già corretto."),
        EventSlotProposal,
        temperature=0,
        job_id=run_id,
    )
    return _filter_unobserved_slots(proposal, observed)


async def _triage_one_event(
    session: AsyncSession,
    event: dict[str, Any],
    run_id: str,
) -> tuple[int, str, bool]:
    """Turn 0 (free) → phase 1 (search) → phase 2 (inspect) → phase 3 (decide).

    Exactly three structured calls after the free turn 0, always in this
    order, phase 3 always reached — no repeatable action loop, so this can
    never fall through to a "turns exhausted" non-verdict.
    """
    event_id = str(event["event_id"])
    observations, observed = await _seed_turn0(session, event)
    await _run_search_phase(session, event, observations, observed, run_id)
    await _run_inspect_phase(session, event, observations, observed, run_id)
    proposal = await _run_decide_phase(event, observations, observed, run_id)

    applied = await _apply_proposal(
        session, proposal, event_id=event_id, run_id=run_id
    )
    return (
        applied,
        _missing_context_text(proposal),
        _honest_verified_no_change(proposal, applied),
    )


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
            verified_no_change = False
            try:
                applied, missing_context, verified_no_change = await _triage_one_event(
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
                verified_no_change = False
            try:
                if event_id:
                    await _record_verdict(
                        session,
                        event_id=event_id,
                        applied=applied,
                        run_id=run_id,
                        missing_context=missing_context,
                        verified_no_change=verified_no_change,
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
