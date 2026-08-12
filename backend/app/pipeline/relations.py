"""Relation detection: candidates, classification, atomic writes (tech-spec §6.3, E4.4–E4.5)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from neo4j import AsyncSession

from app.core import event_bus
from app.core.llm_client import call_structured
from app.models.relations import RelationClassification, RelationLabel

MAX_CHUNK_LOCAL_CANDIDATES = 8
MAX_DOC_LOCAL_CANDIDATES = 8
MAX_TOTAL_CANDIDATES = 20

CandidateVia = Literal["embedding", "chunk", "doc"]
_VIA_PRIORITY: dict[CandidateVia, int] = {"embedding": 0, "chunk": 1, "doc": 2}

FIND_CANDIDATES_CYPHER = """
CALL db.index.vector.queryNodes('fact_embedding', $k, $embedding)
YIELD node AS candidate, score
WHERE candidate.is_latest = true AND candidate.id <> $n_id
RETURN candidate.id AS id, candidate.text AS text, score
ORDER BY score DESC
"""

FIND_CHUNK_LOCAL_CANDIDATES_CYPHER = """
MATCH (n:Fact {id: $n_id})-[:DERIVED_FROM]->(:Chunk)<-[:DERIVED_FROM]-(candidate:Fact)
WHERE candidate.is_latest = true AND candidate.id <> $n_id
RETURN DISTINCT candidate.id AS id, candidate.text AS text
LIMIT $chunk_limit
"""

FIND_DOC_LOCAL_CANDIDATES_CYPHER = """
MATCH (candidate:Fact)
WHERE candidate.is_latest = true AND candidate.id <> $n_id
  AND candidate.source_doc_id = $doc_id
RETURN candidate.id AS id, candidate.text AS text
LIMIT $doc_limit
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

# Byte-stable replaces definition (R2.2 must not alter this section).
_REPLACES_SECTION = (
    '- `"replaces"` se il fatto nuovo contraddice o sostituisce il fatto esistente (es. cambia un '
    "valore, un'informazione più recente annulla o rimpiazza la precedente sullo stesso "
    "soggetto/attributo).\n"
)

SYSTEM_PROMPT = (
    "Confronta il FATTO NUOVO con il FATTO ESISTENTE e classifica la relazione tra i due:\n"
    f"{_REPLACES_SECTION}"
    '- `"extends"` se il fatto nuovo aggiunge dettagli complementari sullo stesso soggetto '
    "o sulla stessa situazione/episodio complessivo (non necessariamente lo stesso attributo "
    "specifico), senza contraddire né rendere superfluo il fatto esistente: entrambi possono "
    "restare veri contemporaneamente. Vale anche quando i due fatti descrivono attributi "
    "diversi dello stesso oggetto concreto (es. luogo e attrezzatura dello stesso ufficio).\n"
    "  Esempio extends: \"Il vento soffiava forte sulla strada\" e \"Il sole uscì e scaldò il "
    "viandante\" — momenti diversi dello stesso episodio narrativo.\n"
    "  Esempio extends: \"L'ufficio è a Milano\" e \"L'ufficio ha una palestra sul tetto\" — "
    "dettagli complementari sullo stesso soggetto.\n"
    "  Esempio none: \"Il vento soffiava forte\" e \"Alice lavora ad Acme\" — argomenti "
    "scorrelati, anche se nello stesso documento.\n"
    '- `"none"` se non c\'è relazione significativa tra i due.\n\n'
    "Rispondi solo secondo lo schema fornito, senza aggiungere testo libero."
)


@dataclass(frozen=True)
class Candidate:
    id: str
    text: str
    score: float | None
    via: CandidateVia


def merge_and_cap_candidates(
    embedding: list[Candidate],
    chunk: list[Candidate],
    doc: list[Candidate],
    *,
    max_total: int = MAX_TOTAL_CANDIDATES,
) -> list[Candidate]:
    """Deduplicate by id (stronger via wins) and cap at max_total (embedding→chunk→doc)."""
    by_id: dict[str, Candidate] = {}
    for group in (embedding, chunk, doc):
        for candidate in group:
            existing = by_id.get(candidate.id)
            if existing is None:
                by_id[candidate.id] = candidate
                continue
            if _VIA_PRIORITY[candidate.via] < _VIA_PRIORITY[existing.via]:
                score = (
                    candidate.score if candidate.score is not None else existing.score
                )
                by_id[candidate.id] = Candidate(
                    id=candidate.id,
                    text=candidate.text,
                    score=score,
                    via=candidate.via,
                )
            elif existing.score is None and candidate.score is not None:
                by_id[candidate.id] = Candidate(
                    id=existing.id,
                    text=existing.text,
                    score=candidate.score,
                    via=existing.via,
                )

    ordered = (
        [c for c in by_id.values() if c.via == "embedding"]
        + [c for c in by_id.values() if c.via == "chunk"]
        + [c for c in by_id.values() if c.via == "doc"]
    )
    return ordered[:max_total]


def build_relation_prompt(
    n_text: str,
    v_text: str,
    *,
    same_chunk: bool = False,
    same_doc: bool = False,
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for relation classification."""
    locality_note = ""
    if same_chunk:
        locality_note = (
            "Nota: i due fatti provengono dallo stesso passaggio di testo.\n"
        )
    elif same_doc:
        locality_note = "Nota: i due fatti provengono dallo stesso documento.\n"

    user_prompt = (
        f'FATTO NUOVO: "{n_text}"\n'
        f'FATTO ESISTENTE: "{v_text}"\n'
        f"{locality_note}"
        f"\nClassifica la relazione."
    )
    return SYSTEM_PROMPT, user_prompt


async def find_chunk_local_candidates(
    session: AsyncSession,
    fact_id: str,
    *,
    chunk_limit: int = MAX_CHUNK_LOCAL_CANDIDATES,
) -> list[Candidate]:
    """Find is_latest facts that share at least one source chunk with fact_id.

    Facts without DERIVED_FROM yield an empty list (no error).
    """
    result = await session.run(
        FIND_CHUNK_LOCAL_CANDIDATES_CYPHER,
        n_id=fact_id,
        chunk_limit=chunk_limit,
    )
    candidates: list[Candidate] = []
    async for record in result:
        candidates.append(
            Candidate(
                id=record["id"],
                text=record["text"],
                score=None,
                via="chunk",
            )
        )
    return candidates


async def find_doc_local_candidates(
    session: AsyncSession,
    fact_id: str,
    source_doc_id: str,
    *,
    doc_limit: int = MAX_DOC_LOCAL_CANDIDATES,
) -> list[Candidate]:
    """Find is_latest facts that share source_doc_id with the new fact.

    Empty/absent doc_id skips the query entirely (avoids matching blank docs).
    """
    if not source_doc_id:
        return []

    result = await session.run(
        FIND_DOC_LOCAL_CANDIDATES_CYPHER,
        n_id=fact_id,
        doc_id=source_doc_id,
        doc_limit=doc_limit,
    )
    candidates: list[Candidate] = []
    async for record in result:
        candidates.append(
            Candidate(
                id=record["id"],
                text=record["text"],
                score=None,
                via="doc",
            )
        )
    return candidates


async def find_candidates(
    session: AsyncSession,
    fact_id: str,
    embedding: list[float],
    k: int = 10,
    *,
    source_doc_id: str = "",
) -> list[Candidate]:
    """Find is_latest candidates via embedding, same-chunk, and same-document."""
    result = await session.run(
        FIND_CANDIDATES_CYPHER,
        k=k,
        embedding=embedding,
        n_id=fact_id,
    )
    embedding_candidates: list[Candidate] = []
    async for record in result:
        embedding_candidates.append(
            Candidate(
                id=record["id"],
                text=record["text"],
                score=float(record["score"]),
                via="embedding",
            )
        )

    chunk_candidates = await find_chunk_local_candidates(session, fact_id)
    doc_candidates = await find_doc_local_candidates(session, fact_id, source_doc_id)
    return merge_and_cap_candidates(
        embedding_candidates, chunk_candidates, doc_candidates
    )


async def classify_relation(
    n_text: str,
    v_text: str,
    job_id: str | None = None,
    *,
    same_chunk: bool = False,
    same_doc: bool = False,
) -> RelationClassification:
    """Classify the relationship between a new fact N and candidate V."""
    system_prompt, user_prompt = build_relation_prompt(
        n_text,
        v_text,
        same_chunk=same_chunk,
        same_doc=same_doc,
    )
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
