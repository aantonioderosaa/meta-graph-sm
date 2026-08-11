"""Natural-language query endpoint (tech-spec §8.1, E5.3)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.schemas import QueryRequest
from app.core.neo4j_client import Neo4jSessionDep
from app.models.query import QueryResponse
from app.pipeline import query_engine

router = APIRouter(prefix="/query", tags=["query"])


@router.post("", response_model=QueryResponse)
async def post_query(body: QueryRequest, session: Neo4jSessionDep) -> QueryResponse:
    """Current-state NL query — never returns historical facts in facts_used."""
    return await query_engine.run_query(
        session,
        body.text,
        type_filter=body.type_filter,
    )
