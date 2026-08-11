"""Relation detection: candidates, classification, atomic writes (tech-spec §6.3, E4.4–E4.5)."""

from __future__ import annotations

from dataclasses import dataclass

from neo4j import AsyncSession

from app.core import event_bus
from app.core.llm_client import call_structured
from app.models.relations import RelationClassification, RelationLabel

FIND_CANDIDATES_CYPHER = """
CALL db.index.vector.queryNodes('fact_embedding', $k, $embedding)
YIELD node AS candidate, score
WHERE candidate.is_latest = true AND candidate.id <> $n_id
RETURN candidate.id AS id, candidate.text AS text, score
ORDER BY score DESC
"""

APPLY_REPLACES_CYPHER = """
MATCH (n:Fact {id: $n_id}), (v:Fact {id: $v_id})
CREATE (n)-[:UPDATES {created_at: datetime()}]->(v)
SET v.is_latest = false, n.is_latest = true
"""

APPLY_EXTENDS_CYPHER = """
MATCH (n:Fact {id: $n_id}), (v:Fact {id: $v_id})
CREATE (n)-[:EXTENDS {created_at: datetime()}]->(v)
"""

SYSTEM_PROMPT = (
    "Confronta il FATTO NUOVO con il FATTO ESISTENTE e classifica la relazione tra i due:\n"
    '- `"replaces"` se il fatto nuovo contraddice o sostituisce il fatto esistente (es. cambia un '
    "valore, un'informazione più recente annulla o rimpiazza la precedente sullo stesso "
    "soggetto/attributo).\n"
    '- `"extends"` se il fatto nuovo aggiunge dettagli complementari, senza contraddire il fatto '
    "esistente: entrambi possono restare veri contemporaneamente.\n"
    '- `"none"` se non c\'è relazione significativa tra i due.\n\n'
    "Rispondi solo secondo lo schema fornito, senza aggiungere testo libero."
)


@dataclass(frozen=True)
class Candidate:
    id: str
    text: str
    score: float


def build_relation_prompt(n_text: str, v_text: str) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for relation classification."""
    user_prompt = (
        f'FATTO NUOVO: "{n_text}"\n'
        f'FATTO ESISTENTE: "{v_text}"\n\n'
        "Classifica la relazione."
    )
    return SYSTEM_PROMPT, user_prompt


async def find_candidates(
    session: AsyncSession,
    fact_id: str,
    embedding: list[float],
    k: int = 10,
) -> list[Candidate]:
    """Find is_latest candidate facts via the fact_embedding vector index."""
    result = await session.run(
        FIND_CANDIDATES_CYPHER,
        k=k,
        embedding=embedding,
        n_id=fact_id,
    )
    candidates: list[Candidate] = []
    async for record in result:
        candidates.append(
            Candidate(
                id=record["id"],
                text=record["text"],
                score=float(record["score"]),
            )
        )
    return candidates


async def classify_relation(
    n_text: str,
    v_text: str,
    job_id: str | None = None,
) -> RelationClassification:
    """Classify the relationship between a new fact N and candidate V."""
    system_prompt, user_prompt = build_relation_prompt(n_text, v_text)
    return await call_structured(
        system_prompt,
        user_prompt,
        RelationClassification,
        temperature=0,
        job_id=job_id,
    )


def relation_edge_event_type(relation: RelationLabel) -> str | None:
    """Map classification to SSE edge type payload value."""
    if relation == RelationLabel.replaces:
        return "updates"
    if relation == RelationLabel.extends:
        return "extends"
    return None


async def apply_relation(
    session: AsyncSession,
    *,
    n_id: str,
    v_id: str,
    relation: RelationLabel,
    job_id: str,
) -> bool:
    """Apply relation atomically; return True if an edge was written."""
    if relation == RelationLabel.none:
        return False

    if relation == RelationLabel.replaces:
        await session.run(APPLY_REPLACES_CYPHER, n_id=n_id, v_id=v_id)
        edge_type = "updates"
        await event_bus.publish(
            job_id,
            "relation_detection",
            "edge_created",
            {"type": edge_type, "src": n_id, "tgt": v_id},
        )
        await event_bus.publish(
            job_id,
            "relation_detection",
            "is_latest_changed",
            {"fact_id": v_id, "value": False},
        )
        return True

    await session.run(APPLY_EXTENDS_CYPHER, n_id=n_id, v_id=v_id)
    await event_bus.publish(
        job_id,
        "relation_detection",
        "edge_created",
        {"type": "extends", "src": n_id, "tgt": v_id},
    )
    return True
