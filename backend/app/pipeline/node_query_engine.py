"""NL query on the Node/Concept layer: hybrid seed → PPR → rerank → context.

Does not read Chunk nodes. The GDS in-memory graph is Node/Concept/Relation only.
The GDS in-memory graph is ensured lazily (Macrotask 2); projection rebuild
lives only in node_ppr_projection.py, never in the synchronous question path.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from neo4j import AsyncSession
from neo4j.exceptions import ClientError
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.llm_client import call_structured
from app.models.kernel import IS_A, MEMBER_OF
from app.models.query import (
    ConceptUsed,
    NodeQueryResponse,
    NodeSubgraph,
    NodeSubgraphNode,
    NodeSubgraphRelationship,
    NodeUsed,
)
from app.pipeline import embeddings
from app.pipeline.connectivity_rules import kernel_catch_all_ids
from app.pipeline.node_ppr_projection import PPR_GRAPH_NAME, ensure_ppr_projection

logger = logging.getLogger(__name__)

SEED_CAP = 20
NODE_VECTOR_K = 15
CONCEPT_VECTOR_K = 10
RELATION_VECTOR_K = 15
FULLTEXT_K = 15
RRF_K = 60
# Neo4j cosine vector scores sit near 0.5 for orthogonal vectors.
# 0.6 drops those while keeping near-duplicate embeddings (~1.0).
SIMILARITY_THRESHOLD = 0.6
PPR_DAMPING = 0.99
PPR_MAX_ITERATIONS = 100
CANDIDATE_POOL_SIZE = 40
RERANK_TOP_N = 10
MAX_CONTEXT_CHARS = 12000

ENABLE_NODE_VECTOR = True
ENABLE_CONCEPT_VECTOR = True
ENABLE_RELATION_VECTOR = True
ENABLE_NODE_CONCEPT_FULLTEXT = True
ENABLE_RELATION_FULLTEXT = True

EMPTY_NODE_ANSWER = (
    "Nessuna informazione trovata nel grafo di entità, eventi e concetti."
)

NODE_ANSWER_SYSTEM_PROMPT = (
    "Sei un assistente che risponde a domande basandoti SOLO su entità, eventi, "
    "relazioni e concetti forniti. Non scrivere mai ID o UUID dentro il testo di "
    "`answer` — scrivi prosa naturale. Elenca in `cited_node_ids` gli ID (tra "
    "quelli forniti sotto) dei nodi usati per costruire la risposta. Se le "
    "informazioni non bastano, dillo esplicitamente senza inventare."
)

_WORD_RE = re.compile(r"\w+", re.UNICODE)

_reranker = None

NODE_VECTOR_CYPHER = """
CALL db.index.vector.queryNodes('node_embedding', $k, $embedding)
YIELD node, score
WHERE node.merged_into IS NULL AND score >= $threshold
RETURN node.id AS id, score
"""

CONCEPT_VECTOR_CYPHER = """
CALL db.index.vector.queryNodes('concept_embedding', $k, $embedding)
YIELD node, score
WHERE score >= $threshold
RETURN node.id AS id, score
"""

RELATION_VECTOR_CYPHER = """
CALL db.index.vector.queryRelationships('relation_embedding', $k, $embedding)
YIELD relationship, score
WHERE score >= $threshold
WITH relationship, score
MATCH (a)-[relationship]->(b)
WHERE (a:Concept OR (a:Node AND a.merged_into IS NULL))
  AND (b:Concept OR (b:Node AND b.merged_into IS NULL))
RETURN a.id AS start_id, b.id AS end_id, score
"""

NODE_CONCEPT_FULLTEXT_CYPHER = """
CALL db.index.fulltext.queryNodes('node_concept_fulltext', $text)
YIELD node, score
WHERE node:Concept OR (node:Node AND node.merged_into IS NULL)
RETURN node.id AS id, score
ORDER BY score DESC
LIMIT $k
"""

RELATION_FULLTEXT_CYPHER = """
CALL db.index.fulltext.queryRelationships('relation_fulltext', $text)
YIELD relationship, score
WITH relationship, score
MATCH (a)-[relationship]->(b)
WHERE (a:Concept OR (a:Node AND a.merged_into IS NULL))
  AND (b:Concept OR (b:Node AND b.merged_into IS NULL))
RETURN a.id AS start_id, b.id AS end_id, score
ORDER BY score DESC
LIMIT $k
"""

PPR_STREAM_CYPHER = """
MATCH (seed) WHERE seed.id IN $seedIds
WITH collect(seed) AS seeds
CALL gds.pageRank.stream($graphName, {
  sourceNodes: seeds,
  dampingFactor: $dampingFactor,
  maxIterations: $maxIterations
})
YIELD nodeId, score
WITH gds.util.asNode(nodeId) AS n, score
WHERE score > 0
RETURN n.id AS id, labels(n) AS labels, score
ORDER BY score DESC
LIMIT $candidatePoolSize
"""

SEED_FALLBACK_CYPHER = """
MATCH (n)
WHERE n.id IN $ids
  AND ((n:Node AND n.merged_into IS NULL) OR n:Concept)
RETURN n.id AS id, labels(n) AS labels
"""

DESCRIBE_CYPHER = """
MATCH (n)
WHERE n.id IN $ids
  AND ((n:Node AND n.merged_into IS NULL) OR n:Concept)
OPTIONAL MATCH (n)-[r_out:Relation]->(out)
WHERE out:Concept OR (out:Node AND out.merged_into IS NULL)
OPTIONAL MATCH (inn)-[r_in:Relation]->(n)
WHERE inn:Concept OR (inn:Node AND inn.merged_into IS NULL)
OPTIONAL MATCH (n)-[:HAS_CONCEPT]->(c:Concept)
OPTIONAL MATCH (n)-[:DERIVED_FROM]->(src)
WHERE src.doc_id IS NOT NULL
OPTIONAL MATCH (holder)-[:HAS_CONCEPT]->(n)
WHERE n:Concept AND holder:Node AND holder.merged_into IS NULL
RETURN n.id AS id,
       n.name AS name,
       n.type AS type,
       labels(n) AS labels,
       collect(DISTINCT {rel: r_out.relation, name: out.name, id: out.id}) AS out_rels,
       collect(DISTINCT {rel: r_in.relation, name: inn.name, id: inn.id}) AS in_rels,
       collect(DISTINCT {id: c.id, name: c.name}) AS concepts,
       collect(DISTINCT src.doc_id) AS source_doc_ids,
       collect(DISTINCT {id: holder.id, name: holder.name}) AS concept_holders
"""

SUBGRAPH_RELS_CYPHER = """
MATCH (a)-[r:Relation|HAS_CONCEPT]->(b)
WHERE a.id IN $ids AND b.id IN $ids
  AND ((a:Node AND a.merged_into IS NULL) OR a:Concept)
  AND ((b:Node AND b.merged_into IS NULL) OR b:Concept)
RETURN a.id AS source,
       b.id AS target,
       CASE type(r)
         WHEN 'HAS_CONCEPT' THEN 'HAS_CONCEPT'
         ELSE coalesce(r.normalized_relation, r.relation, type(r))
       END AS rel_type
"""


class NodeQueryAnswer(BaseModel):
    """Structured LLM answer for POST /graph/query."""

    answer: str = Field(
        description="Risposta in linguaggio naturale, senza ID o UUID nel testo."
    )
    cited_node_ids: list[str] = Field(
        default_factory=list,
        description="ID (tra quelli forniti nel prompt) dei nodi usati per answer.",
    )


@dataclass
class Candidate:
    id: str
    labels: list[str]
    ppr_score: float
    name: str = ""
    node_type: str | None = None
    description: str = ""
    concepts: list[tuple[str, str]] = field(default_factory=list)
    source_doc_ids: list[str] = field(default_factory=list)
    rerank_score: float = 0.0


def rrf_fuse(ranked_lists: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    """Reciprocal Rank Fusion — rank-only, no score-scale mixing."""
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, item_id in enumerate(ranked):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)
    return scores


def _lucene_query(text: str) -> str | None:
    tokens = _WORD_RE.findall(text)
    if not tokens:
        return None
    return " OR ".join(tokens)


def _is_concept(labels: list[str] | None) -> bool:
    return bool(labels) and "Concept" in labels


def _is_node(labels: list[str] | None) -> bool:
    return bool(labels) and "Node" in labels


def _filter_vector_hits(
    rows: list[Any], *, threshold: float | None = None
) -> list[Any]:
    if threshold is None:
        threshold = SIMILARITY_THRESHOLD
    kept = []
    for row in rows:
        score = float(row["score"])
        if score >= threshold:
            kept.append(row)
    return kept


def _ids_from_hits(rows: list[Any]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for row in rows:
        item_id = row["id"]
        if item_id and item_id not in seen:
            seen.add(item_id)
            ids.append(item_id)
    return ids


def _endpoint_ids_from_relation_hits(rows: list[Any]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in ("start_id", "end_id"):
            item_id = row[key]
            if item_id and item_id not in seen:
                seen.add(item_id)
                ids.append(item_id)
    return ids


def _relation_rrf_scores(rows: list[Any], k: int = RRF_K) -> dict[str, float]:
    """Both endpoints inherit the relation's rank (same RRF contribution)."""
    scores: dict[str, float] = {}
    for rank, row in enumerate(rows):
        contrib = 1.0 / (k + rank + 1)
        for key in ("start_id", "end_id"):
            item_id = row[key]
            if item_id:
                scores[item_id] = scores.get(item_id, 0.0) + contrib
    return scores


def _describe_node(
    *,
    node_id: str,
    name: str,
    kind: str,
    out_rels: list[tuple[str, str]],
    in_rels: list[tuple[str, str]],
    concepts: list[str],
) -> str:
    rel_parts: list[str] = []
    for rel, other in out_rels:
        rel_parts.append(f'"{rel}" → {other}')
    for rel, other in in_rels:
        rel_parts.append(f'"{rel}" ← {other}')
    rel_text = "; ".join(rel_parts) if rel_parts else "(nessuna)"
    concept_text = ", ".join(concepts) if concepts else "(nessuno)"
    return (
        f"[{node_id}] {name} ({kind}) — relazioni: {rel_text}\n"
        f"    concetti: {concept_text}"
    )


def _clean_rel_maps(items: list[Any] | None) -> list[tuple[str, str]]:
    cleaned: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in items or []:
        if not item:
            continue
        rel = item.get("rel") if hasattr(item, "get") else None
        name = item.get("name") if hasattr(item, "get") else None
        if not rel or not name:
            continue
        pair = (str(rel), str(name))
        if pair not in seen:
            seen.add(pair)
            cleaned.append(pair)
    return cleaned


def _clean_named(items: list[Any] | None) -> list[tuple[str, str]]:
    cleaned: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in items or []:
        if not item:
            continue
        item_id = item.get("id") if hasattr(item, "get") else None
        name = item.get("name") if hasattr(item, "get") else None
        if not item_id or not name or item_id in seen:
            continue
        seen.add(str(item_id))
        cleaned.append((str(item_id), str(name)))
    return cleaned


def build_node_query_answer_prompt(question: str, descriptions: list[str]) -> tuple[str, str]:
    if not descriptions:
        body = "(nessuna entità, evento o concetto rilevante trovato)"
    else:
        body = "\n".join(descriptions)
    user = (
        f"DOMANDA: {question}\n\n"
        f"CONTESTO:\n{body}\n\n"
        "Rispondi basandoti solo sul contesto sopra. Le relazioni sono parte del contesto."
    )
    return NODE_ANSWER_SYSTEM_PROMPT, user


def _get_reranker():
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder

        _reranker = CrossEncoder(settings.RERANK_MODEL)
    return _reranker


def reset_reranker() -> None:
    global _reranker
    _reranker = None


def _predict_rerank(question: str, descriptions: list[str]) -> list[float]:
    model = _get_reranker()
    pairs = [(question, desc) for desc in descriptions]
    scores = model.predict(pairs)
    return [float(score) for score in scores]


def _empty_response(answer: str = EMPTY_NODE_ANSWER) -> NodeQueryResponse:
    return NodeQueryResponse(
        answer=answer,
        nodes_used=[],
        concepts_used=[],
        cited_node_ids=[],
        subgraph=NodeSubgraph(nodes=[], relationships=[]),
    )


async def _collect(result) -> list[Any]:
    rows: list[Any] = []
    async for record in result:
        rows.append(record)
    return rows


async def _run_cypher(session: AsyncSession, cypher: str, **kwargs: Any) -> list[Any]:
    result = await session.run(cypher, **kwargs)
    return await _collect(result)


def _driver_or_none():
    try:
        from app.core.neo4j_client import get_driver

        return get_driver()
    except RuntimeError:
        return None


async def _run_search(
    session: AsyncSession, driver, cypher: str, **kwargs: Any
) -> list[Any]:
    try:
        if driver is None:
            return await _run_cypher(session, cypher, **kwargs)
        async with driver.session() as own:
            return await _run_cypher(own, cypher, **kwargs)
    except ClientError:
        logger.warning("Node-query search failed", exc_info=True)
        return []


async def hybrid_seed(
    session: AsyncSession,
    *,
    text: str,
    embedding: list[float],
    threshold: float | None = None,
) -> list[str]:
    """Vector + fulltext over Node/Concept/Relation, fused with RRF, capped."""
    if threshold is None:
        threshold = SIMILARITY_THRESHOLD
    driver = _driver_or_none()
    lucene = _lucene_query(text)

    async def node_vec() -> list[Any]:
        if not ENABLE_NODE_VECTOR:
            return []
        rows = await _run_search(
            session,
            driver,
            NODE_VECTOR_CYPHER,
            k=NODE_VECTOR_K,
            embedding=embedding,
            threshold=threshold,
        )
        return _filter_vector_hits(rows, threshold=threshold)

    async def concept_vec() -> list[Any]:
        if not ENABLE_CONCEPT_VECTOR:
            return []
        rows = await _run_search(
            session,
            driver,
            CONCEPT_VECTOR_CYPHER,
            k=CONCEPT_VECTOR_K,
            embedding=embedding,
            threshold=threshold,
        )
        return _filter_vector_hits(rows, threshold=threshold)

    async def relation_vec() -> list[Any]:
        if not ENABLE_RELATION_VECTOR:
            return []
        rows = await _run_search(
            session,
            driver,
            RELATION_VECTOR_CYPHER,
            k=RELATION_VECTOR_K,
            embedding=embedding,
            threshold=threshold,
        )
        return _filter_vector_hits(rows, threshold=threshold)

    async def node_ft() -> list[Any]:
        if not ENABLE_NODE_CONCEPT_FULLTEXT or lucene is None:
            return []
        return await _run_search(
            session,
            driver,
            NODE_CONCEPT_FULLTEXT_CYPHER,
            text=lucene,
            k=FULLTEXT_K,
        )

    async def relation_ft() -> list[Any]:
        if not ENABLE_RELATION_FULLTEXT or lucene is None:
            return []
        return await _run_search(
            session,
            driver,
            RELATION_FULLTEXT_CYPHER,
            text=lucene,
            k=FULLTEXT_K,
        )

    gathered = await asyncio.gather(
        node_vec(),
        concept_vec(),
        relation_vec(),
        node_ft(),
        relation_ft(),
        return_exceptions=True,
    )
    cleaned: list[list[Any]] = []
    for item in gathered:
        if isinstance(item, Exception):
            logger.warning("Hybrid seed channel failed: %s", item)
            cleaned.append([])
        else:
            cleaned.append(item)
    node_rows, concept_rows, rel_rows, ft_node_rows, ft_rel_rows = cleaned

    id_lists: list[list[str]] = [
        _ids_from_hits(node_rows),
        _ids_from_hits(concept_rows),
        _ids_from_hits(ft_node_rows),
    ]
    scores = rrf_fuse(id_lists)
    for extra in (
        _relation_rrf_scores(rel_rows),
        _relation_rrf_scores(ft_rel_rows),
    ):
        for item_id, value in extra.items():
            scores[item_id] = scores.get(item_id, 0.0) + value

    ranked = sorted(scores, key=lambda item_id: scores[item_id], reverse=True)
    return ranked[:SEED_CAP]


async def expand_ppr(session: AsyncSession, seed_ids: list[str]) -> list[Candidate]:
    """Personalized PageRank over the in-memory projection (never projected here)."""
    if not seed_ids:
        return []
    await ensure_ppr_projection(session)
    try:
        rows = await _run_cypher(
            session,
            PPR_STREAM_CYPHER,
            seedIds=seed_ids,
            graphName=PPR_GRAPH_NAME,
            dampingFactor=PPR_DAMPING,
            maxIterations=PPR_MAX_ITERATIONS,
            candidatePoolSize=CANDIDATE_POOL_SIZE,
        )
    except (ClientError, Exception):
        logger.warning("PPR stream failed; falling back to seeds", exc_info=True)
        rows = []

    candidates: list[Candidate] = []
    seen: set[str] = set()
    for row in rows:
        cid = row["id"]
        if not cid or cid in seen:
            continue
        seen.add(cid)
        labels = list(row["labels"] or [])
        candidates.append(
            Candidate(id=cid, labels=labels, ppr_score=float(row["score"]))
        )

    if candidates:
        return candidates[:CANDIDATE_POOL_SIZE]

    fallback = await _run_cypher(session, SEED_FALLBACK_CYPHER, ids=seed_ids)
    for row in fallback:
        cid = row["id"]
        if not cid or cid in seen:
            continue
        seen.add(cid)
        candidates.append(
            Candidate(id=cid, labels=list(row["labels"] or []), ppr_score=0.0)
        )
    return candidates[:CANDIDATE_POOL_SIZE]


async def _hydrate_candidates(
    session: AsyncSession, candidates: list[Candidate]
) -> list[Candidate]:
    if not candidates:
        return []
    by_id = {c.id: c for c in candidates}
    rows = await _run_cypher(session, DESCRIBE_CYPHER, ids=list(by_id))
    for row in rows:
        cand = by_id.get(row["id"])
        if cand is None:
            continue
        labels = list(row["labels"] or cand.labels)
        cand.labels = labels
        cand.name = row["name"] or cand.id
        raw_type = row["type"]
        cand.node_type = str(raw_type) if raw_type else None
        out_rels = _clean_rel_maps(row["out_rels"])
        in_rels = _clean_rel_maps(row["in_rels"])
        if _is_concept(labels):
            holders = _clean_named(row["concept_holders"])
            in_rels.extend(("HAS_CONCEPT", name) for _hid, name in holders)
            kind = "concept"
        else:
            kind = cand.node_type or "entity"
        concepts = _clean_named(row["concepts"])
        cand.concepts = concepts
        cand.source_doc_ids = [d for d in (row["source_doc_ids"] or []) if d]
        cand.description = _describe_node(
            node_id=cand.id,
            name=cand.name,
            kind=kind,
            out_rels=out_rels,
            in_rels=in_rels,
            concepts=[name for _cid, name in concepts],
        )
    return [c for c in candidates if c.id in by_id and c.description]


def rerank_candidates(
    question: str, candidates: list[Candidate], *, top_n: int = RERANK_TOP_N
) -> list[Candidate]:
    if not candidates:
        return []
    descriptions = [c.description or c.name or c.id for c in candidates]
    try:
        scores = _predict_rerank(question, descriptions)
    except Exception:
        logger.warning("Cross-encoder rerank failed; keeping PPR order", exc_info=True)
        scores = [c.ppr_score for c in candidates]
    for cand, score in zip(candidates, scores, strict=False):
        cand.rerank_score = float(score)
    ordered = sorted(candidates, key=lambda c: c.rerank_score, reverse=True)
    return ordered[:top_n]


def assemble_context(
    candidates: list[Candidate], *, max_chars: int = MAX_CONTEXT_CHARS
) -> list[Candidate]:
    """Drop whole nodes to fit the budget; never strip relation text (D6)."""
    kept: list[Candidate] = []
    used = 0
    for cand in candidates:
        block = cand.description or ""
        extra = len(block) + (1 if kept else 0)
        if kept and used + extra > max_chars:
            break
        kept.append(cand)
        used += extra
    return kept


async def _load_subgraph(
    session: AsyncSession, item_ids: list[str], by_id: dict[str, Candidate]
) -> NodeSubgraph:
    nodes: list[NodeSubgraphNode] = []
    for item_id in item_ids:
        cand = by_id.get(item_id)
        if cand is None:
            continue
        label: str = "Concept" if _is_concept(cand.labels) else "Node"
        props: dict[str, Any] = {"name": cand.name}
        if cand.node_type:
            props["type"] = cand.node_type
        nodes.append(NodeSubgraphNode(id=item_id, label=label, properties=props))

    rels: list[NodeSubgraphRelationship] = []
    if item_ids:
        rows = await _run_cypher(session, SUBGRAPH_RELS_CYPHER, ids=item_ids)
        seen: set[tuple[str, str, str]] = set()
        for row in rows:
            key = (row["source"], row["target"], row["rel_type"])
            if key in seen:
                continue
            seen.add(key)
            rels.append(
                NodeSubgraphRelationship(
                    source=row["source"],
                    target=row["target"],
                    type=row["rel_type"],
                )
            )
    return NodeSubgraph(nodes=nodes, relationships=rels)


def _split_used(
    candidates: list[Candidate],
) -> tuple[list[NodeUsed], list[ConceptUsed]]:
    nodes_used: list[NodeUsed] = []
    concepts_used: list[ConceptUsed] = []
    for cand in candidates:
        if _is_concept(cand.labels):
            concepts_used.append(ConceptUsed(id=cand.id, name=cand.name or cand.id))
            continue
        node_type = cand.node_type if cand.node_type in {"entity", "event"} else "entity"
        nodes_used.append(
            NodeUsed(
                id=cand.id,
                name=cand.name or cand.id,
                type=node_type,  # type: ignore[arg-type]
                source_doc_ids=list(cand.source_doc_ids),
            )
        )
    return nodes_used, concepts_used


async def _persist_node_query_log(
    session: AsyncSession, *, text: str, response: NodeQueryResponse
) -> None:
    """Best-effort NodeQueryLog write — filled in by Macrotask 4."""
    try:
        from app.pipeline import node_query_log
    except ImportError:
        return
    try:
        await node_query_log.write_node_query_log(
            session,
            query_id=str(uuid.uuid4()),
            text=text,
            answer=response.answer,
            cited_node_ids=response.cited_node_ids,
            node_ids=[n.id for n in response.nodes_used],
            concept_ids=[c.id for c in response.concepts_used],
        )
    except Exception:
        logger.warning(
            "Failed to persist NodeQueryLog for text=%r", text[:80], exc_info=True
        )


async def run_node_query(
    session: AsyncSession,
    text: str,
    *,
    job_id: str | None = None,
) -> NodeQueryResponse:
    """Four-stage Node/Concept NL query. Never scans the whole Node set."""
    if not text.strip():
        response = _empty_response()
        await _persist_node_query_log(session, text=text, response=response)
        return response

    embedding = await asyncio.to_thread(embeddings.embed, text)
    seed_ids = await hybrid_seed(
        session, text=text, embedding=embedding, threshold=SIMILARITY_THRESHOLD
    )
    if not seed_ids:
        response = _empty_response()
        await _persist_node_query_log(session, text=text, response=response)
        return response

    raw_candidates = await expand_ppr(session, seed_ids)
    hydrated = await _hydrate_candidates(session, raw_candidates)
    reranked = rerank_candidates(text, hydrated)
    context = assemble_context(reranked)
    by_id = {c.id: c for c in context}
    item_ids = [c.id for c in context]
    nodes_used, concepts_used = _split_used(context)
    subgraph = await _load_subgraph(session, item_ids, by_id)

    descriptions = [c.description for c in context if c.description]
    system, user = build_node_query_answer_prompt(text, descriptions)
    cited: list[str] = []
    try:
        answer_model = await call_structured(
            system, user, NodeQueryAnswer, temperature=0, job_id=job_id
        )
        answer = answer_model.answer
        valid_ids = set(item_ids)
        cited = [nid for nid in answer_model.cited_node_ids if nid in valid_ids]
        if not cited and item_ids:
            cited = list(item_ids)
    except Exception:
        cited = list(item_ids)
        if descriptions:
            answer = (
                "Ecco le entità, eventi e concetti rilevanti "
                "(generazione risposta non disponibile)."
            )
        else:
            answer = EMPTY_NODE_ANSWER

    response = NodeQueryResponse(
        answer=answer,
        nodes_used=nodes_used,
        concepts_used=concepts_used,
        cited_node_ids=cited,
        subgraph=subgraph,
    )
    await _persist_node_query_log(session, text=text, response=response)
    return response


# ---------------------------------------------------------------------------
# Fase 7 — Strato 2 derivation (query-time only; never CREATE/MERGE :Relation)
# ---------------------------------------------------------------------------

_ISA_REL = IS_A.upper()
_MEMBER_OF_REL = MEMBER_OF.upper()
MAX_DESCENT_HOPS = 3

LOAD_CONNECTIVITY_RULES_CYPHER = """
MATCH (r:ConnectivityRule)
RETURN r.source_category AS source_category,
       r.relation_type AS relation_type,
       r.target_category AS target_category,
       r.generalization_level AS generalization_level,
       r.origin_fact_ids AS origin_fact_ids
"""

LOAD_LEAF_S0_RELATIONS_CYPHER = """
MATCH (a:Node)-[r:Relation]->(b:Node)
WHERE r.lifted_from IS NULL
  AND a.merged_into IS NULL
  AND b.merged_into IS NULL
RETURN a.id AS src_id, b.id AS tgt_id,
       r.relation AS relation,
       r.kernel_parent AS kernel_parent,
       r.normalized_relation AS normalized_relation
"""

LOAD_NODE_TYPE_TOKENS_CYPHER = f"""
MATCH (n:Node)
WHERE n.merged_into IS NULL
OPTIONAL MATCH (n)-[:{_MEMBER_OF_REL}]->(c:Concept)
RETURN n.id AS id,
       n.kernel_category AS kernel_category,
       c.id AS concept_id,
       c.name AS concept_name
"""

LOAD_ISA_EDGES_CYPHER = f"""
MATCH (child:Concept)-[:{_ISA_REL}]->(parent:Concept)
RETURN child.id AS child_id, parent.id AS parent_id
"""


@dataclass
class DerivationStep:
    kind: Literal["s0", "s1"]
    detail: str


@dataclass
class DerivedLink:
    source_id: str
    target_id: str
    relation_type: str
    confidence: float
    derivation_chain: list[DerivationStep]


@dataclass(frozen=True)
class _S0Edge:
    src_id: str
    tgt_id: str
    relation: str
    kernel_parent: str | None


@dataclass(frozen=True)
class _S1Rule:
    source_category: str
    relation_type: str
    target_category: str
    generalization_level: int


def _as_str(value: object) -> str:
    if value is None:
        return ""
    return str(value)


async def _iter_rows(result: Any) -> list[dict[str, Any]]:
    if result is None:
        return []
    rows: list[dict[str, Any]] = []
    aiter = getattr(result, "__aiter__", None)
    if aiter is None:
        single = getattr(result, "single", None)
        if callable(single):
            row = await single()
            if row is None:
                return []
            return [dict(row) if not isinstance(row, dict) else row]
        return []
    async for record in result:
        rows.append(dict(record) if not isinstance(record, dict) else record)
    return rows


def _edge_matches_rule(edge: _S0Edge, rule: _S1Rule) -> bool:
    want = rule.relation_type
    rel = edge.relation
    parent = edge.kernel_parent or ""
    return want in {rel, parent} or rel.casefold() == want.casefold()


def _direct_s0_pair(edges: list[_S0Edge], src: str, tgt: str) -> bool:
    return any(edge.src_id == src and edge.tgt_id == tgt for edge in edges)


def _node_type_tokens(
    row: dict[str, Any],
    isa_parent: dict[str, str],
    catch_alls: frozenset[str],
) -> list[str]:
    """Specific → general, excluding kernel catch-all ids."""
    tokens: list[str] = []
    seen: set[str] = set()

    def _add(token: str | None) -> None:
        if not token or token in seen or token in catch_alls:
            return
        seen.add(token)
        tokens.append(token)

    concept_id = row.get("concept_id")
    if concept_id:
        _add(str(concept_id))
        current = str(concept_id)
        walked = 0
        while current in isa_parent and walked < 8:
            current = isa_parent[current]
            if current in catch_alls:
                break
            _add(current)
            walked += 1
    _add(_as_str(row.get("concept_name")) or None)
    _add(_as_str(row.get("kernel_category")) or None)
    return tokens


def _directed_paths(
    edges: list[_S0Edge],
    src: str,
    tgt: str,
    *,
    min_len: int = 2,
    max_len: int = MAX_DESCENT_HOPS,
) -> list[list[_S0Edge]]:
    adj: dict[str, list[_S0Edge]] = {}
    for edge in edges:
        adj.setdefault(edge.src_id, []).append(edge)
    found: list[list[_S0Edge]] = []
    stack: list[tuple[str, list[_S0Edge]]] = [(src, [])]
    while stack:
        node, path = stack.pop()
        if len(path) >= max_len:
            continue
        seen_nodes = {src, *(step.tgt_id for step in path)}
        for edge in adj.get(node, []):
            if edge.tgt_id in seen_nodes:
                continue
            new_path = path + [edge]
            if edge.tgt_id == tgt and min_len <= len(new_path) <= max_len:
                found.append(new_path)
            elif len(new_path) < max_len:
                stack.append((edge.tgt_id, new_path))
    found.sort(key=len)
    return found


def _shared_neighbor_path(
    edges: list[_S0Edge], src: str, tgt: str, rule: _S1Rule
) -> list[_S0Edge] | None:
    """Undirected 2-hop via a shared neighbor that instantiates the rule pattern."""
    src_inc: dict[str, _S0Edge] = {}
    tgt_inc: dict[str, _S0Edge] = {}
    for edge in edges:
        if edge.src_id == src:
            src_inc[edge.tgt_id] = edge
        elif edge.tgt_id == src:
            src_inc[edge.src_id] = edge
        if edge.src_id == tgt:
            tgt_inc[edge.tgt_id] = edge
        elif edge.tgt_id == tgt:
            tgt_inc[edge.src_id] = edge
    for neighbor, first in src_inc.items():
        if neighbor in {src, tgt} or neighbor not in tgt_inc:
            continue
        second = tgt_inc[neighbor]
        if _edge_matches_rule(first, rule) or _edge_matches_rule(second, rule):
            return [first, second]
    return None


def _descent_path(
    edges: list[_S0Edge], src: str, tgt: str, rule: _S1Rule
) -> list[_S0Edge] | None:
    for path in _directed_paths(edges, src, tgt):
        if any(_edge_matches_rule(step, rule) for step in path):
            return path
    return _shared_neighbor_path(edges, src, tgt, rule)


def _s0_step_detail(edge: _S0Edge) -> str:
    rel = edge.relation or edge.kernel_parent or "?"
    return f"{edge.src_id}-[{rel}]->{edge.tgt_id}"


def _s1_step_detail(rule: _S1Rule) -> str:
    return f"{rule.source_category} -{rule.relation_type}-> {rule.target_category}"


def _confidence(rule: _S1Rule, src_tokens: list[str], tgt_tokens: list[str]) -> float:
    exact = (
        bool(src_tokens)
        and bool(tgt_tokens)
        and src_tokens[0] == rule.source_category
        and tgt_tokens[0] == rule.target_category
    )
    if exact:
        return 1.0
    level = max(0, int(rule.generalization_level))
    return 1.0 / (1.0 + level)


async def derive_candidate_links(
    session: AsyncSession,
    *,
    seed_node_ids: list[str] | None = None,
    source_id: str | None = None,
    target_id: str | None = None,
) -> list[DerivedLink]:
    """Instantiate S2 hypotheses from S1 rules + S0 descent. Zero writes."""
    rules_result = await session.run(LOAD_CONNECTIVITY_RULES_CYPHER)
    rule_rows = await _iter_rows(rules_result)
    rules: list[_S1Rule] = []
    for row in rule_rows:
        src_cat = _as_str(row.get("source_category"))
        rel_type = _as_str(row.get("relation_type"))
        tgt_cat = _as_str(row.get("target_category"))
        if not src_cat or not rel_type or not tgt_cat:
            continue
        level_raw = row.get("generalization_level")
        level = int(level_raw) if level_raw is not None else 0
        rules.append(
            _S1Rule(
                source_category=src_cat,
                relation_type=rel_type,
                target_category=tgt_cat,
                generalization_level=level,
            )
        )
    if not rules:
        return []

    s0_result = await session.run(LOAD_LEAF_S0_RELATIONS_CYPHER)
    s0_rows = await _iter_rows(s0_result)
    edges: list[_S0Edge] = []
    for row in s0_rows:
        src = _as_str(row.get("src_id"))
        tgt = _as_str(row.get("tgt_id"))
        if not src or not tgt:
            continue
        edges.append(
            _S0Edge(
                src_id=src,
                tgt_id=tgt,
                relation=_as_str(row.get("relation")),
                kernel_parent=_as_str(row.get("kernel_parent")) or None,
            )
        )

    tokens_result = await session.run(LOAD_NODE_TYPE_TOKENS_CYPHER)
    token_rows = await _iter_rows(tokens_result)
    nodes: dict[str, dict[str, Any]] = {}
    for row in token_rows:
        nid = _as_str(row.get("id"))
        if nid:
            nodes[nid] = row

    isa_result = await session.run(LOAD_ISA_EDGES_CYPHER)
    isa_rows = await _iter_rows(isa_result)
    isa_parent = {
        _as_str(row.get("child_id")): _as_str(row.get("parent_id"))
        for row in isa_rows
        if row.get("child_id") and row.get("parent_id")
    }
    catch_alls = kernel_catch_all_ids()
    token_index: dict[str, list[str]] = {
        nid: _node_type_tokens(row, isa_parent, catch_alls) for nid, row in nodes.items()
    }

    if source_id and target_id:
        pairs = [(source_id, target_id)]
    else:
        universe = list(nodes)
        if seed_node_ids:
            allowed = set(seed_node_ids)
            universe = [nid for nid in universe if nid in allowed]
        pairs = [
            (a, b) for a in universe for b in universe if a != b
        ]

    derived: list[DerivedLink] = []
    seen: set[tuple[str, str, str]] = set()
    for src, tgt in pairs:
        if src not in nodes or tgt not in nodes:
            continue
        if _direct_s0_pair(edges, src, tgt):
            continue
        src_tokens = token_index.get(src) or []
        tgt_tokens = token_index.get(tgt) or []
        if not src_tokens or not tgt_tokens:
            continue
        matching = [
            rule
            for rule in rules
            if rule.source_category in src_tokens and rule.target_category in tgt_tokens
        ]
        matching.sort(key=lambda r: (r.generalization_level, r.relation_type))
        for rule in matching:
            key = (src, tgt, rule.relation_type)
            if key in seen:
                continue
            path = _descent_path(edges, src, tgt, rule)
            if not path:
                continue
            seen.add(key)
            chain = [DerivationStep(kind="s0", detail=_s0_step_detail(step)) for step in path]
            chain.append(DerivationStep(kind="s1", detail=_s1_step_detail(rule)))
            derived.append(
                DerivedLink(
                    source_id=src,
                    target_id=tgt,
                    relation_type=rule.relation_type,
                    confidence=_confidence(rule, src_tokens, tgt_tokens),
                    derivation_chain=chain,
                )
            )
            break
    return derived
