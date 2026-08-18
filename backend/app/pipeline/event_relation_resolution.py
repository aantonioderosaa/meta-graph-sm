"""Event node dedup and event↔event classification in one stage (Macrotask 4.2).

Participation (event→entity) is not a separate stage: merge_nodes already
collapses duplicate participates arcs.

Fase 6.3: shared situations are reified as an Evento node plus R5
``participates`` edges — never as a direct entity–entity context arc.
"""

from __future__ import annotations

import uuid

from neo4j import AsyncSession

from app.core.llm_client import call_structured
from app.models.kernel import EntityKernelType, RelationKernelType
from app.models.node_extraction import (
    EventRelationClassification,
    EventRelationLabel,
    SequenceType,
)
from app.pipeline.node_resolution import (
    FIND_NODE_CANDIDATES_CYPHER,
    HIGH_CONFIDENCE_SCORE,
    find_node_candidates,
    merge_nodes,
)

SITUATION_PARTICIPATES_RELATION = "is participated by"
SITUATION_NORMALIZED_RELATION = "participates"

FIND_EVENT_CANDIDATES_CYPHER = FIND_NODE_CANDIDATES_CYPHER

FIND_FRESH_EVENTS_CYPHER = """
MATCH (n:Node {type:'event', dreamed:false})
WHERE n.merged_into IS NULL
RETURN n.id AS id, n.name AS name, n.embedding AS embedding
"""

SHARED_ENTITIES_CYPHER = """
MATCH (a:Node {id: $event_a})-[:Relation {normalized_relation:'participates'}]->(ea:Node)
MATCH (b:Node {id: $event_b})-[:Relation {normalized_relation:'participates'}]->(eb:Node)
WHERE ea.id = eb.id
RETURN count(DISTINCT ea) AS shared
"""

FIND_EXISTING_RAW_EVENT_REL_CYPHER = """
MATCH (a:Node {id: $head_id})-[r:Relation]->(b:Node {id: $tail_id})
WHERE r.normalized_relation IS NULL
RETURN elementId(r) AS rel_id, r.relation AS relation
LIMIT 1
"""

SET_EVENT_SEQUENCE_CYPHER = """
MATCH (a:Node {id: $head_id})-[r:Relation]->(b:Node {id: $tail_id})
WHERE elementId(r) = $rel_id
SET r.normalized_relation = $normalized_relation,
    r.is_latest = true
"""

CREATE_EVENT_SEQUENCE_CYPHER = """
MATCH (a:Node {id: $head_id}), (b:Node {id: $tail_id})
CREATE (a)-[r:Relation {
    relation: $relation,
    normalized_relation: $normalized_relation,
    is_latest: true,
    created_at: datetime()
}]->(b)
"""

FIND_EVENT_BY_NAME_CYPHER = """
MATCH (n:Node {type: 'event', name: $name})
WHERE n.merged_into IS NULL
RETURN n.id AS id
LIMIT 1
"""

CREATE_SITUATION_EVENT_CYPHER = """
CREATE (n:Node {
  id: $id,
  name: $name,
  type: 'event',
  dreamed: false,
  merged_into: null,
  kernel_category: $kernel_category,
  summary: $name,
  created_at: datetime()
})
RETURN n.id AS id
"""

LINK_SITUATION_CHUNK_CYPHER = """
MATCH (n:Node {id: $node_id}), (c:Chunk {id: $chunk_id})
CREATE (n)-[:DERIVED_FROM]->(c)
"""

MERGE_SITUATION_PARTICIPATES_CYPHER = """
MATCH (ev:Node {id: $event_id}), (p:Node {id: $participant_id})
MERGE (ev)-[r:Relation {normalized_relation: $normalized_relation}]->(p)
ON CREATE SET
  r.relation = $relation,
  r.kernel_parent = $kernel_parent,
  r.is_latest = true,
  r.created_at = datetime()
"""

EVENT_RELATION_SYSTEM_PROMPT = (
    "Confronta l'EVENTO NUOVO con l'EVENTO CANDIDATO e classifica la relazione tra i due.\n"
    "- `same_event`: i due testi descrivono lo stesso evento nel mondo reale "
    "(la stessa occorrenza). In questo caso non serve sequence_type.\n"
    "- `sequenced`: sono eventi distinti ma collegati nel tempo o per causa. "
    "sequence_type è obbligatorio e deve essere uno di: `precedes` (l'evento nuovo "
    "precede il candidato), `causes` (l'evento nuovo causa il candidato), "
    "`cooccurs` (avvengono insieme).\n"
    "- `none`: non c'è una relazione significativa.\n"
    "Rispondi solo secondo lo schema fornito, senza aggiungere testo libero."
)


def build_event_relation_prompt(
    new_name: str,
    candidate_name: str,
    shared_count: int,
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for three-way event classification."""
    gate = ""
    if shared_count == 0:
        gate = (
            "Le due descrizioni non condividono alcuna entità partecipante "
            "(shared_count=0): `same_event` è vietato. Scegli `sequenced` o `none`.\n"
        )
    user_prompt = (
        f"{gate}"
        f'EVENTO NUOVO: "{new_name}"\n'
        f'EVENTO CANDIDATO: "{candidate_name}"\n'
        f"Entità partecipanti in comune: {shared_count}\n\n"
        "Classifica la relazione."
    )
    return EVENT_RELATION_SYSTEM_PROMPT, user_prompt


def _coerce_classification(
    verdict: EventRelationClassification,
    shared_count: int,
) -> EventRelationClassification:
    if verdict.label == EventRelationLabel.same_event and shared_count == 0:
        return EventRelationClassification(label=EventRelationLabel.none, sequence_type=None)
    if verdict.label == EventRelationLabel.sequenced and verdict.sequence_type is None:
        return EventRelationClassification(label=EventRelationLabel.none, sequence_type=None)
    return verdict


async def count_shared_entities(session: AsyncSession, event_a: str, event_b: str) -> int:
    result = await session.run(
        SHARED_ENTITIES_CYPHER,
        event_a=event_a,
        event_b=event_b,
    )
    async for record in result:
        return int(record["shared"] or 0)
    return 0


async def classify_event_relation(
    new_name: str,
    candidate_name: str,
    shared_count: int,
    job_id: str | None = None,
) -> EventRelationClassification:
    """Three-way LLM classification; same_event is forbidden when shared_count == 0."""
    system_prompt, user_prompt = build_event_relation_prompt(
        new_name, candidate_name, shared_count
    )
    verdict = await call_structured(
        system_prompt,
        user_prompt,
        EventRelationClassification,
        temperature=0,
        job_id=job_id,
    )
    return _coerce_classification(verdict, shared_count)


def _sequence_endpoints(
    fresh_id: str,
    candidate_id: str,
    sequence_type: SequenceType,
) -> tuple[str, str]:
    if sequence_type == SequenceType.cooccurs:
        if fresh_id <= candidate_id:
            return fresh_id, candidate_id
        return candidate_id, fresh_id
    return fresh_id, candidate_id


async def _write_sequence_relation(
    session: AsyncSession,
    fresh_id: str,
    candidate_id: str,
    sequence_type: SequenceType,
) -> None:
    head_id, tail_id = _sequence_endpoints(fresh_id, candidate_id, sequence_type)
    normalized = sequence_type.value

    existing = await session.run(
        FIND_EXISTING_RAW_EVENT_REL_CYPHER,
        head_id=head_id,
        tail_id=tail_id,
    )
    existing_id = None
    async for record in existing:
        existing_id = str(record["rel_id"])
        break

    if existing_id is None:
        reverse = await session.run(
            FIND_EXISTING_RAW_EVENT_REL_CYPHER,
            head_id=tail_id,
            tail_id=head_id,
        )
        async for record in reverse:
            existing_id = str(record["rel_id"])
            head_id, tail_id = tail_id, head_id
            break

    if existing_id is not None:
        await session.run(
            SET_EVENT_SEQUENCE_CYPHER,
            head_id=head_id,
            tail_id=tail_id,
            rel_id=existing_id,
            normalized_relation=normalized,
        )
        return

    await session.run(
        CREATE_EVENT_SEQUENCE_CYPHER,
        head_id=head_id,
        tail_id=tail_id,
        relation=normalized,
        normalized_relation=normalized,
    )


async def resolve_event(
    session: AsyncSession,
    event_id: str,
    name: str,
    embedding: list[float],
    job_id: str,
) -> str:
    """Dedup and classify one fresh event. Returns the canonical node id."""
    candidates = await find_node_candidates(
        session,
        event_id,
        "event",
        embedding,
        name,
    )
    if not candidates:
        return event_id

    shared_counts: dict[str, int] = {}
    for candidate in candidates:
        shared_counts[candidate.id] = await count_shared_entities(
            session, event_id, candidate.id
        )

    exact_with_shared = [
        c
        for c in candidates
        if c.via == "exact_name" and shared_counts[c.id] >= 1
    ]
    if exact_with_shared:
        canon_id = exact_with_shared[0].id
        await merge_nodes(session, event_id, canon_id)
        return canon_id

    high_with_shared = [
        c
        for c in candidates
        if c.score is not None
        and c.score >= HIGH_CONFIDENCE_SCORE
        and shared_counts[c.id] >= 1
    ]
    if len(high_with_shared) == 1:
        canon_id = high_with_shared[0].id
        await merge_nodes(session, event_id, canon_id)
        return canon_id

    for candidate in candidates:
        shared = shared_counts[candidate.id]
        verdict = await classify_event_relation(name, candidate.name, shared, job_id)
        if verdict.label == EventRelationLabel.same_event:
            if shared >= 1:
                await merge_nodes(session, event_id, candidate.id)
                return candidate.id
            continue
        if verdict.label == EventRelationLabel.sequenced and verdict.sequence_type is not None:
            await _write_sequence_relation(
                session, event_id, candidate.id, verdict.sequence_type
            )

    return event_id


async def resolve_fresh_events(session: AsyncSession, job_id: str) -> set[str]:
    """Resolve every fresh (dreamed:false, unmerged) event. Returns canonical ids."""
    result = await session.run(FIND_FRESH_EVENTS_CYPHER)
    rows: list[tuple[str, str, list[float]]] = []
    async for record in result:
        embedding = record["embedding"]
        rows.append(
            (
                record["id"],
                record["name"],
                list(embedding) if embedding is not None else [],
            )
        )

    touched: set[str] = set()
    for event_id, name, embedding in rows:
        canon = await resolve_event(session, event_id, name, embedding, job_id)
        touched.add(canon)
    return touched


async def reify_shared_situation(
    session: AsyncSession,
    *,
    participant_node_ids: list[str],
    situation_name: str,
    chunk_id: str | None = None,
) -> str:
    """Reify a shared situation as an Evento node plus R5 participates edges.

    Reuses an existing ``:Node {type:'event'}`` with the same name. Never
    creates a direct entity–entity context / co-occurrence edge.
    """
    existing = await session.run(FIND_EVENT_BY_NAME_CYPHER, name=situation_name)
    event_id: str | None = None
    async for record in existing:
        event_id = str(record["id"])
        break

    if event_id is None:
        event_id = str(uuid.uuid4())
        await session.run(
            CREATE_SITUATION_EVENT_CYPHER,
            id=event_id,
            name=situation_name,
            kernel_category=EntityKernelType.Evento.value,
        )
        if chunk_id:
            await session.run(
                LINK_SITUATION_CHUNK_CYPHER,
                node_id=event_id,
                chunk_id=chunk_id,
            )

    kernel_parent = RelationKernelType.Partecipativa.value
    seen: set[str] = set()
    for participant_id in participant_node_ids:
        if not participant_id or participant_id in seen or participant_id == event_id:
            continue
        seen.add(participant_id)
        await session.run(
            MERGE_SITUATION_PARTICIPATES_CYPHER,
            event_id=event_id,
            participant_id=participant_id,
            normalized_relation=SITUATION_NORMALIZED_RELATION,
            relation=SITUATION_PARTICIPATES_RELATION,
            kernel_parent=kernel_parent,
        )
    return event_id

