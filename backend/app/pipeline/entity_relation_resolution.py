"""Incremental entity↔entity :Relation resolution (Macrotask 4.1).

Candidates are same-endpoint edges identified by elementId(r). Never a
full-graph Relation scan.

Fase 9 extends T1: when temporal transitions are enabled, the classifier
returns supersedes / updated_by / contradicts (plus extends / none). The
legacy replaces/extends/none path remains when both flags are off.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from neo4j import AsyncSession

from app.core.config import settings
from app.core.llm_client import call_structured
from app.models.relations import RelationClassification, RelationLabel
from app.pipeline.ingestion import write_contradicts

logger = logging.getLogger(__name__)

_REPLACES_SECTION = (
    '- `"replaces"` se il fatto nuovo contraddice o sostituisce il fatto esistente sullo stesso '
    "soggetto/attributo: un'informazione più recente annulla o rimpiazza la precedente. "
    "Per stabilire quale dei due descrive lo stato più recente, cerca marcatori temporali nel "
    'testo di entrambi i fatti (date assolute, espressioni relative come "ora", "da allora", '
    '"fino al", "ho appena iniziato", "il mese scorso") — questi sono la base primaria della '
    "decisione, non l'ordine di presentazione. Le etichette FATTO NUOVO/FATTO ESISTENTE "
    "indicano solo quale dei due stai valutando ora — non implicano da sole che uno sia "
    "temporalmente precedente all'altro.\n"
)

_TEMPORAL_TRANSITIONS_SECTION = (
    '- `"supersedes"` se la versione precedente era vera quando è stata asserita e il fatto '
    "nuovo è una successione legittima nel tempo (cambio di ruolo, mandato, stato, datore di "
    "lavoro) — non un errore e non un disaccordo tra fonti. Per stabilire quale dei due "
    "descrive lo stato più recente, cerca marcatori temporali nel testo di entrambi i fatti "
    '(date assolute, espressioni relative come "ora", "da allora", "fino al", "dal 2018", '
    '"ho appena iniziato", "il mese scorso") — questi sono la base primaria della decisione, '
    "non l'ordine di presentazione. Le etichette FATTO NUOVO/FATTO ESISTENTE indicano solo "
    "quale dei due stai valutando ora — non implicano da sole che uno sia temporalmente "
    "precedente all'altro.\n"
    '- `"updated_by"` se il fatto precedente era un errore e il testo contiene una rettifica '
    'esplicita (es. "in realtà mi sono sbagliato", "non nel 2010 ma nel 2011", "correzione"). '
    "Senza quel marcatore di errore nel testo, non scegliere `updated_by`.\n"
    '- `"contradicts"` se un\'altra fonte, o un disaccordo autorevole, afferma qualcosa di '
    "incompatibile sullo stesso soggetto/attributo SENZA marcatore di errore — mai "
    "`updated_by`. Entrambe le asserzioni restano pretese vere; il conflitto è di prima "
    "classe.\n"
)

_EXTENDS_NONE_SECTION = (
    '- `"extends"` se il fatto nuovo aggiunge dettagli complementari sullo stesso soggetto '
    "o sulla stessa situazione/episodio complessivo (non necessariamente lo stesso attributo "
    "specifico), senza contraddire né rendere superfluo il fatto esistente: entrambi possono "
    "restare veri contemporaneamente. Vale anche quando i due fatti descrivono attributi "
    "diversi dello stesso oggetto concreto (es. luogo e attrezzatura dello stesso ufficio). "
    "extends non è una transizione di versione: i due fatti sono complementari, non in "
    "sequenza.\n"
    '  Esempio extends: "Il vento soffiava forte sulla strada" e "Il sole uscì e scaldò il '
    'viandante" — momenti diversi dello stesso episodio narrativo.\n'
    '  Esempio extends: "L\'ufficio è a Milano" e "L\'ufficio ha una palestra sul tetto" — '
    "dettagli complementari sullo stesso soggetto.\n"
    '  Esempio none: "Il vento soffiava forte" e "Alice lavora ad Acme" — argomenti '
    "scorrelati, anche se nello stesso documento.\n"
    '- `"none"` se non c\'è relazione significativa tra i due.\n\n'
)

_PRUDENCE_LEGACY = (
    "Se nessuno dei due fatti contiene un marcatore temporale esplicito che stabilisca quale "
    "dei due descrive lo stato più recente, non scegliere `replaces` sulla sola base "
    "dell'ordine di presentazione — valuta invece se i due fatti possono coesistere "
    "(`extends`) o se non c'è relazione significativa (`none`). Dichiarare erroneamente "
    "`replaces` nasconde un fatto vero: è un errore peggiore di non dichiarare nulla.\n\n"
)

_PRUDENCE_TEMPORAL = (
    "Se nessuno dei due fatti contiene un marcatore temporale esplicito che stabilisca quale "
    "dei due descrive lo stato più recente, non scegliere `supersedes` (né `replaces`) sulla "
    "sola base dell'ordine di presentazione — valuta invece se i due fatti possono "
    "coesistere (`extends`), se sono un disaccordo senza rettifica (`contradicts`), o se "
    "non c'è relazione significativa (`none`). Dichiarare erroneamente una transizione che "
    "nasconde un fatto vero è un errore peggiore di non dichiarare nulla.\n\n"
    "`updated_by` solo in presenza di una rettifica esplicita nel testo; due fonti "
    "autorevoli in conflitto senza marcatore di errore → `contradicts`, mai `updated_by`.\n\n"
)

SYSTEM_PROMPT = (
    "Confronta il FATTO NUOVO con il FATTO ESISTENTE e classifica la relazione tra i due:\n"
    f"{_TEMPORAL_TRANSITIONS_SECTION}"
    f"{_EXTENDS_NONE_SECTION}"
    f"{_PRUDENCE_TEMPORAL}"
    "Rispondi solo secondo lo schema fornito, senza aggiungere testo libero."
)

LEGACY_SYSTEM_PROMPT = (
    "Confronta il FATTO NUOVO con il FATTO ESISTENTE e classifica la relazione tra i due:\n"
    f"{_REPLACES_SECTION}"
    f"{_EXTENDS_NONE_SECTION}"
    f"{_PRUDENCE_LEGACY}"
    "Rispondi solo secondo lo schema fornito, senza aggiungere testo libero."
)

_ERROR_MARKERS = (
    "mi sono sbagliato",
    "mi ero sbagliato",
    "ho sbagliato",
    "era un errore",
    "rettifica",
    "correzione",
    "non nel ",
    "ma nel ",
)

_SUCCESSION_MARKERS = (
    "dal 20",
    "dal 19",
    "fino al",
    "da gennaio",
    "da allora",
    "ora è",
    "ora l'",
    "ora l’",
    "ho appena iniziato",
    "il mese scorso",
    "presidente",
)

_STOPWORDS = frozenset(
    {
        "il",
        "la",
        "lo",
        "i",
        "gli",
        "le",
        "di",
        "a",
        "da",
        "in",
        "su",
        "e",
        "o",
        "un",
        "una",
        "the",
        "at",
        "to",
        "of",
        "nel",
        "del",
        "che",
        "per",
        "con",
        "non",
        "ma",
    }
)


def temporal_transitions_enabled() -> bool:
    """Fase 9 flag, also on when facet identity is on (OR)."""
    return bool(settings.ENABLE_TEMPORAL_TRANSITIONS or settings.ENABLE_FACET_IDENTITY)


def _has_error_marker(text: str) -> bool:
    folded = text.casefold()
    return any(marker in folded for marker in _ERROR_MARKERS)


def map_temporal_transition(new_text: str, old_text: str) -> RelationLabel:
    """Deterministic three-way mapping (no LLM). Never drops a disagreement.

    ``updated_by`` only if explicit error/correction wording is present.
    Conflicting years without that wording are ``contradicts``, never
    ``updated_by``. Succession markers without an error marker are
    ``supersedes``. Complementary non-sequential details are ``extends``.
    """
    blob = f"{new_text} {old_text}"
    folded = blob.casefold()
    if _has_error_marker(folded):
        return RelationLabel.updated_by
    if any(marker in folded for marker in _SUCCESSION_MARKERS):
        return RelationLabel.supersedes
    years = set(re.findall(r"\b(?:19|20)\d{2}\b", folded))
    if len(years) >= 2:
        return RelationLabel.contradicts
    new_tokens = {
        tok
        for tok in re.findall(r"[a-zàèéìòù0-9]+", new_text.casefold())
        if tok not in _STOPWORDS and len(tok) > 2
    }
    old_tokens = {
        tok
        for tok in re.findall(r"[a-zàèéìòù0-9]+", old_text.casefold())
        if tok not in _STOPWORDS and len(tok) > 2
    }
    if new_tokens & old_tokens:
        return RelationLabel.extends
    return RelationLabel.none


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
    system_prompt = SYSTEM_PROMPT if temporal_transitions_enabled() else LEGACY_SYSTEM_PROMPT
    return system_prompt, user_prompt


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


OnClassified = Callable[[str, str, str], Awaitable[None]]
OnClassifyError = Callable[[str, str, str], Awaitable[None]]

VECTOR_RELATION_CANDIDATE_K = 10

FIND_FRESH_ENTITY_RELS_CYPHER = """
MATCH (a:Node {type:'entity'})-[r:Relation]->(b:Node {type:'entity'})
WHERE r.normalized_relation IS NULL
  AND a.merged_into IS NULL AND b.merged_into IS NULL
RETURN a.id AS head_id, b.id AS tail_id, r.relation AS relation, elementId(r) AS rel_id
"""

FIND_FRESH_ENTITY_RELS_TOUCHED_CYPHER = """
MATCH (a:Node {type:'entity'})-[r:Relation]->(b:Node {type:'entity'})
WHERE r.normalized_relation IS NULL
  AND a.merged_into IS NULL AND b.merged_into IS NULL
  AND (a.id IN $touched_ids OR b.id IN $touched_ids)
RETURN a.id AS head_id, b.id AS tail_id, r.relation AS relation, elementId(r) AS rel_id
"""

FIND_SAME_ENDPOINT_RELS_CYPHER = """
MATCH (a:Node {id:$head_id})-[r:Relation]->(b:Node {id:$tail_id})
WHERE elementId(r) <> $rel_id AND r.is_latest = true
RETURN elementId(r) AS rel_id, r.relation AS relation,
       r.normalized_relation AS normalized_relation
"""

FETCH_HEAD_EMBEDDING_CYPHER = """
MATCH (a:Node {id: $head_id})
RETURN a.embedding AS embedding
"""

VECTOR_ASSISTED_SAME_ENDPOINT_RELS_CYPHER = """
CALL db.index.vector.queryNodes('node_embedding', $k, $embedding)
YIELD node AS neighbor, score
WHERE neighbor.type = 'entity'
  AND neighbor.merged_into IS NULL
WITH collect(neighbor.id) AS neighbor_ids
MATCH (a:Node {id: $head_id})-[r:Relation]->(b:Node {id: $tail_id})
WHERE elementId(r) <> $rel_id
  AND r.is_latest = true
  AND ($head_id IN neighbor_ids OR $tail_id IN neighbor_ids)
RETURN elementId(r) AS rel_id, r.relation AS relation,
       r.normalized_relation AS normalized_relation
"""

APPLY_UPDATES_CYPHER = """
MATCH (a:Node {id:$head_id})-[neu:Relation]->(b:Node {id:$tail_id})
WHERE elementId(neu) = $new_rel_id
MATCH (a)-[old:Relation]->(b)
WHERE elementId(old) = $old_rel_id
SET neu.normalized_relation = 'updates',
    old.is_latest = false
"""

APPLY_EXTENDS_CYPHER = """
MATCH (a:Node {id:$head_id})-[neu:Relation]->(b:Node {id:$tail_id})
WHERE elementId(neu) = $new_rel_id
SET neu.normalized_relation = 'extends'
"""

MARK_PROCESSED_NONE_CYPHER = """
MATCH (a:Node {id:$head_id})-[neu:Relation]->(b:Node {id:$tail_id})
WHERE elementId(neu) = $new_rel_id
SET neu.normalized_relation = neu.relation
"""

MARK_CONTRADICTS_CYPHER = """
MATCH (a:Node {id:$head_id})-[neu:Relation]->(b:Node {id:$tail_id})
WHERE elementId(neu) = $new_rel_id
SET neu.normalized_relation = 'contradicts'
"""

APPLY_SUPERSEDES_CYPHER = """
MATCH (a:Node {id:$head_id})-[neu:Relation]->(b:Node {id:$tail_id})
WHERE elementId(neu) = $new_rel_id
MATCH (a)-[old:Relation]->(b)
WHERE elementId(old) = $old_rel_id
SET neu.normalized_relation = 'updates',
    old.is_latest = false
WITH a, b
CREATE (b)-[:SUPERSEDES {
  subject_id: $head_id,
  new_rel_id: $new_rel_id,
  old_rel_id: $old_rel_id,
  created_at: datetime()
}]->(b)
"""

APPLY_UPDATED_BY_CYPHER = """
MATCH (a:Node {id:$head_id})-[neu:Relation]->(b:Node {id:$tail_id})
WHERE elementId(neu) = $new_rel_id
MATCH (a)-[old:Relation]->(b)
WHERE elementId(old) = $old_rel_id
SET neu.normalized_relation = 'updates',
    old.is_latest = false
WITH a, b
CREATE (b)-[:UPDATED_BY {
  subject_id: $head_id,
  new_rel_id: $new_rel_id,
  old_rel_id: $old_rel_id,
  created_at: datetime()
}]->(b)
"""


@dataclass(frozen=True)
class EntityRelationCandidate:
    rel_id: str
    relation: str
    normalized_relation: str | None = None


def _candidate_from_record(record: dict) -> EntityRelationCandidate:
    return EntityRelationCandidate(
        rel_id=str(record["rel_id"]),
        relation=record["relation"],
        normalized_relation=record.get("normalized_relation"),
    )


async def _collect_candidates(result) -> list[EntityRelationCandidate]:
    candidates: list[EntityRelationCandidate] = []
    async for record in result:
        candidates.append(_candidate_from_record(record))
    return candidates


async def find_entity_relation_candidates(
    session: AsyncSession,
    head_id: str,
    tail_id: str,
    rel_id: str,
) -> list[EntityRelationCandidate]:
    """Same-endpoint is_latest Relations, plus a scoped vector assist on the endpoints."""
    result = await session.run(
        FIND_SAME_ENDPOINT_RELS_CYPHER,
        head_id=head_id,
        tail_id=tail_id,
        rel_id=rel_id,
    )
    by_id: dict[str, EntityRelationCandidate] = {}
    for candidate in await _collect_candidates(result):
        by_id[candidate.rel_id] = candidate

    embedding_result = await session.run(FETCH_HEAD_EMBEDDING_CYPHER, head_id=head_id)
    embedding = None
    async for record in embedding_result:
        embedding = record.get("embedding")
        break
    if embedding:
        vector_result = await session.run(
            VECTOR_ASSISTED_SAME_ENDPOINT_RELS_CYPHER,
            k=VECTOR_RELATION_CANDIDATE_K,
            embedding=list(embedding),
            head_id=head_id,
            tail_id=tail_id,
            rel_id=rel_id,
        )
        for candidate in await _collect_candidates(vector_result):
            by_id.setdefault(candidate.rel_id, candidate)

    return list(by_id.values())


def _apply_kwargs(
    head_id: str, tail_id: str, new_rel_id: str, old_rel_id: str
) -> dict[str, str]:
    return {
        "head_id": head_id,
        "tail_id": tail_id,
        "new_rel_id": new_rel_id,
        "old_rel_id": old_rel_id,
    }


async def _apply_temporal_label(
    session: AsyncSession,
    label: RelationLabel,
    head_id: str,
    tail_id: str,
    new_rel_id: str,
    old_rel_id: str,
) -> str | None:
    """Apply a three-way temporal verdict. Returns outcome or None to try the next candidate."""
    params = _apply_kwargs(head_id, tail_id, new_rel_id, old_rel_id)
    if label == RelationLabel.contradicts:
        await session.run(
            MARK_CONTRADICTS_CYPHER,
            head_id=head_id,
            tail_id=tail_id,
            new_rel_id=new_rel_id,
        )
        await write_contradicts(
            session,
            left_id=tail_id,
            right_id=tail_id,
            subject_id=head_id,
            relation="contradicts",
            kernel_parent="contradicts",
        )
        return "contradicts"
    if label in (RelationLabel.supersedes, RelationLabel.replaces):
        await session.run(APPLY_SUPERSEDES_CYPHER, **params)
        return "supersedes"
    if label == RelationLabel.updated_by:
        await session.run(APPLY_UPDATED_BY_CYPHER, **params)
        return "updated_by"
    if label == RelationLabel.extends:
        await session.run(
            APPLY_EXTENDS_CYPHER,
            head_id=head_id,
            tail_id=tail_id,
            new_rel_id=new_rel_id,
        )
        return "extends"
    return None


async def classify_and_apply_entity_relation(
    session: AsyncSession,
    head_id: str,
    tail_id: str,
    rel_id: str,
    new_relation_text: str,
    job_id: str,
) -> str | None:
    """Classify a fresh entity-entity Relation against same-endpoint candidates.

    Returns ``supersedes``, ``updated_by``, ``contradicts``, ``extends``, or
    ``none`` when temporal transitions are on; otherwise ``updates`` /
    ``extends`` / ``none``.
    """
    candidates = await find_entity_relation_candidates(session, head_id, tail_id, rel_id)
    temporal = temporal_transitions_enabled()
    for candidate in candidates:
        verdict = await classify_relation(
            new_relation_text,
            candidate.relation,
            job_id=job_id,
        )
        if temporal:
            outcome = await _apply_temporal_label(
                session,
                verdict.relation,
                head_id,
                tail_id,
                rel_id,
                candidate.rel_id,
            )
            if outcome is not None:
                return outcome
        elif verdict.relation == RelationLabel.replaces:
            await session.run(
                APPLY_UPDATES_CYPHER,
                head_id=head_id,
                tail_id=tail_id,
                new_rel_id=rel_id,
                old_rel_id=candidate.rel_id,
            )
            return "updates"
        elif verdict.relation == RelationLabel.extends:
            await session.run(
                APPLY_EXTENDS_CYPHER,
                head_id=head_id,
                tail_id=tail_id,
                new_rel_id=rel_id,
            )
            return "extends"

    await session.run(
        MARK_PROCESSED_NONE_CYPHER,
        head_id=head_id,
        tail_id=tail_id,
        new_rel_id=rel_id,
    )
    return "none"


async def resolve_fresh_entity_relations(
    session: AsyncSession,
    job_id: str,
    touched_entity_ids: set[str] | None = None,
    *,
    on_classified: OnClassified | None = None,
    on_error: OnClassifyError | None = None,
) -> int:
    """Classify fresh entity-entity Relations. Scoped to touched ids when provided."""
    if touched_entity_ids is None:
        result = await session.run(FIND_FRESH_ENTITY_RELS_CYPHER)
    else:
        result = await session.run(
            FIND_FRESH_ENTITY_RELS_TOUCHED_CYPHER,
            touched_ids=list(touched_entity_ids),
        )

    fresh: list[tuple[str, str, str, str]] = []
    async for record in result:
        fresh.append(
            (
                record["head_id"],
                record["tail_id"],
                str(record["rel_id"]),
                record["relation"],
            )
        )

    processed = 0
    for head_id, tail_id, rel_id, relation in fresh:
        try:
            outcome = await classify_and_apply_entity_relation(
                session,
                head_id,
                tail_id,
                rel_id,
                relation,
                job_id,
            )
        except Exception as exc:
            logger.exception(
                "entity_relation_classification_failed head=%s tail=%s",
                head_id,
                tail_id,
            )
            if on_error is not None:
                await on_error(head_id, tail_id, str(exc))
            continue
        if outcome is not None:
            processed += 1
            if on_classified is not None:
                await on_classified(outcome, head_id, tail_id)
    return processed
