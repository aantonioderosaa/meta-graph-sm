"""Query history list + detail endpoints (Epic F4.3)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from app.api.schemas import QueryHistoryResponse
from app.core.neo4j_client import Neo4jSessionDep
from app.models.query import QueryResponse
from app.pipeline import query_log

router = APIRouter(prefix="/queries", tags=["queries"])


@router.get("", response_model=QueryHistoryResponse)
async def list_queries(
    session: Neo4jSessionDep,
    limit: int = Query(default=20, ge=1, le=100),
) -> QueryHistoryResponse:
    items = await query_log.list_query_logs(session, limit=limit)
    return QueryHistoryResponse(items=items)


@router.get("/{query_id}", response_model=QueryResponse)
async def get_query(
    query_id: str,
    session: Neo4jSessionDep,
) -> QueryResponse:
    detail = await query_log.get_query_log_detail(session, query_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"QueryLog '{query_id}' not found",
        )
    return detail
